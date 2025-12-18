from __future__ import annotations

from pathlib import Path
import re
import urllib.parse
from uuid import UUID
from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

from app.api.deps import get_db
from app.core.audit import log_audit
from app.core.logging import logger
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.storage import save_upload
from app.worker.job_runner import run_ingestion_now
from app.db.models.studies import Document, DocumentVersion, Study
from app.db.models.anchors import Anchor
from app.db.enums import AnchorContentType, IngestionStatus, DocumentLanguage
from app.schemas.common import SoAResult
from app.schemas.documents import (
    DocumentCreate,
    DocumentOut,
    DocumentVersionCreate,
    DocumentVersionOut,
    UploadResult,
)
from app.schemas.anchors import AnchorOut
from app.db.models.facts import Fact

router = APIRouter()

# Разрешенные расширения файлов
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx"}

_CYR_RE = re.compile(r"[А-Яа-яЁё]")
_LAT_RE = re.compile(r"[A-Za-z]")


def _uri_to_path(uri: str) -> Path:
    if uri.startswith("file://"):
        parsed = urllib.parse.urlparse(uri)
        path = urllib.parse.unquote(parsed.path)
        # Windows: file:///C:/path -> /C:/path
        if path.startswith("/") and len(path) > 3 and path[2] == ":":
            path = path[1:]
        return Path(path)
    return Path(uri)


def _detect_language_from_docx(file_path: Path, max_chars: int = 50_000) -> tuple[DocumentLanguage, dict[str, Any]]:
    """
    Очень простой детект языка:
    - считаем количество кириллических и латинских букв
    - ru/en/mixed на основе долей
    """
    try:
        from docx import Document as DocxDocument  # локальный импорт, чтобы не тянуть зависимость везде
    except Exception as e:  # noqa: BLE001
        return DocumentLanguage.UNKNOWN, {"error": f"python-docx import failed: {e}"}

    try:
        doc = DocxDocument(str(file_path))
    except Exception as e:  # noqa: BLE001
        return DocumentLanguage.UNKNOWN, {"error": f"docx open failed: {e}"}

    buf: list[str] = []
    total = 0

    # paragraphs
    for p in getattr(doc, "paragraphs", []) or []:
        t = (getattr(p, "text", "") or "").strip()
        if not t:
            continue
        buf.append(t)
        total += len(t)
        if total >= max_chars:
            break

    # tables (если текста в параграфах мало)
    if total < max_chars:
        for tbl in getattr(doc, "tables", []) or []:
            for row in getattr(tbl, "rows", []) or []:
                for cell in getattr(row, "cells", []) or []:
                    t = (getattr(cell, "text", "") or "").strip()
                    if not t:
                        continue
                    buf.append(t)
                    total += len(t)
                    if total >= max_chars:
                        break
                if total >= max_chars:
                    break
            if total >= max_chars:
                break

    text = "\n".join(buf)[:max_chars]
    cyr = len(_CYR_RE.findall(text))
    lat = len(_LAT_RE.findall(text))
    letters = cyr + lat
    if letters == 0:
        return DocumentLanguage.UNKNOWN, {"cyr": cyr, "lat": lat, "letters": letters, "max_chars": max_chars}

    cyr_ratio = cyr / letters
    lat_ratio = lat / letters

    # thresholds: минимально практичные
    if cyr_ratio >= 0.7:
        lang = DocumentLanguage.RU
    elif lat_ratio >= 0.7:
        lang = DocumentLanguage.EN
    elif cyr >= 20 and lat >= 20:
        lang = DocumentLanguage.MIXED
    else:
        lang = DocumentLanguage.UNKNOWN

    meta = {
        "cyr": cyr,
        "lat": lat,
        "letters": letters,
        "cyr_ratio": round(cyr_ratio, 4),
        "lat_ratio": round(lat_ratio, 4),
        "max_chars": max_chars,
    }
    return lang, meta


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


async def _build_ingestion_summary(
    *,
    db: AsyncSession,
    version: DocumentVersion,
    ingestion_result: Any | None,
    final_status: IngestionStatus,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """
    Формирует ingestion_summary_json со стабильной схемой (MVP шаги 1–6).

    Требуемые ключи (всегда присутствуют):
    - anchors_created
    - soa_found
    - soa_facts_written
    - chunks_created
    - mapping_status
    - warnings
    - errors
    """
    base_summary: dict[str, Any] = version.ingestion_summary_json or {}

    anchors_created = int(getattr(ingestion_result, "anchors_created", 0) or 0) if ingestion_result else 0
    chunks_created = int(getattr(ingestion_result, "chunks_created", 0) or 0) if ingestion_result else 0
    soa_found = bool(getattr(ingestion_result, "soa_detected", False)) if ingestion_result else False

    # SoA facts actually written: проверяем по БД (регрессионно устойчиво к изменениям сервиса)
    soa_facts_counts: dict[str, int] = {"visits": 0, "procedures": 0, "matrix": 0}
    if ingestion_result is not None:
        facts_res = await db.execute(
            select(Fact.fact_key, func.count(Fact.id).label("cnt"))
            .where(
                Fact.created_from_doc_version_id == version.id,
                Fact.fact_type == "soa",
                Fact.fact_key.in_(("visits", "procedures", "matrix")),
            )
            .group_by(Fact.fact_key)
        )
        for row in facts_res.all():
            soa_facts_counts[str(row.fact_key)] = int(row.cnt)

    soa_facts_written = {
        "visits": soa_facts_counts["visits"] > 0,
        "procedures": soa_facts_counts["procedures"] > 0,
        "matrix": soa_facts_counts["matrix"] > 0,
        "counts": soa_facts_counts,
    }

    # Маппинг секций: минимальная стабильная схема
    sections_mapped_count = None
    sections_needs_review_count = None
    if ingestion_result is not None and getattr(ingestion_result, "docx_summary", None):
        ds = getattr(ingestion_result, "docx_summary") or {}
        if "sections_mapped_count" in ds:
            sections_mapped_count = ds.get("sections_mapped_count")
        if "sections_needs_review_count" in ds:
            sections_needs_review_count = ds.get("sections_needs_review_count")

    mapping_status = {
        "sections_mapped_count": sections_mapped_count,
        "sections_needs_review_count": sections_needs_review_count,
        "status": "needs_review" if final_status == IngestionStatus.NEEDS_REVIEW else ("failed" if final_status == IngestionStatus.FAILED else "ready"),
    }

    stable_summary: dict[str, Any] = {
        "anchors_created": anchors_created,
        "soa_found": soa_found,
        "soa_facts_written": soa_facts_written,
        "chunks_created": chunks_created,
        "mapping_status": mapping_status,
        "warnings": _ensure_list(warnings),
        "errors": _ensure_list(errors),
        # Дублируем для обратной совместимости с существующими тестами/клиентами:
        "soa_detected": bool(getattr(ingestion_result, "soa_detected", False)) if ingestion_result else False,
        "needs_review": bool(getattr(ingestion_result, "needs_review", False)) if ingestion_result else (final_status == IngestionStatus.NEEDS_REVIEW),
    }

    # Сохраняем метаданные загруженного файла, если уже были записаны при upload
    for k in ("filename", "size_bytes"):
        if k in base_summary and k not in stable_summary:
            stable_summary[k] = base_summary[k]

    # Прокидываем sha256 из модели в summary для трассировки
    if version.source_sha256:
        stable_summary["source_sha256"] = version.source_sha256

    # При желании сохраняем дополнительные поля, но не полагаемся на них как на часть стабильной схемы
    if ingestion_result is not None:
        stable_summary["facts_extraction"] = {
            "facts_count": getattr(ingestion_result, "facts_count", 0),
            "needs_review": getattr(ingestion_result, "facts_needs_review", []),
        }
        if getattr(ingestion_result, "soa_detected", False):
            stable_summary["soa"] = {
                "table_index": getattr(ingestion_result, "soa_table_index", None),
                "section_path": getattr(ingestion_result, "soa_section_path", None),
                "confidence": getattr(ingestion_result, "soa_confidence", None),
                "cell_anchors_created": getattr(ingestion_result, "cell_anchors_created", 0),
            }
        if getattr(ingestion_result, "docx_summary", None):
            stable_summary["docx_summary"] = getattr(ingestion_result, "docx_summary")

    return stable_summary


def validate_file_extension(filename: str) -> None:
    """Проверяет расширение файла."""
    if not filename:
        raise ValidationError("Имя файла не может быть пустым")
    
    file_ext = Path(filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Неподдерживаемый тип файла. Разрешенные типы: {', '.join(ALLOWED_EXTENSIONS)}"
        )


@router.post(
    "/studies/{study_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_document(
    study_id: UUID,
    payload: DocumentCreate,
    db: AsyncSession = Depends(get_db),
) -> DocumentOut:
    """Создание нового документа."""
    # Проверяем существование study
    study = await db.get(Study, study_id)
    if not study:
        raise NotFoundError("Study", str(study_id))

    document = Document(
        workspace_id=study.workspace_id,
        study_id=study_id,
        doc_type=payload.doc_type,
        title=payload.title,
        lifecycle_status=payload.lifecycle_status,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    
    # Audit logging
    await log_audit(
        db=db,
        workspace_id=study.workspace_id,
        action="create",
        entity_type="document",
        entity_id=str(document.id),
        after_json={
            "doc_type": payload.doc_type.value,
            "title": payload.title,
            "lifecycle_status": payload.lifecycle_status.value,
        },
    )
    await db.commit()
    
    return DocumentOut.model_validate(document)


@router.post(
    "/documents/{document_id}/versions",
    response_model=DocumentVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_version(
    document_id: UUID,
    payload: DocumentVersionCreate,
    db: AsyncSession = Depends(get_db),
) -> DocumentVersionOut:
    """Создание новой версии документа."""
    # Проверяем существование document
    document = await db.get(Document, document_id)
    if not document:
        raise NotFoundError("Document", str(document_id))

    # Логируем, как определяется язык документа:
    # - document_language имеет дефолт UNKNOWN в схеме, поэтому дополнительно
    #   смотрим, был ли field передан явно в запросе.
    try:
        provided_fields = getattr(payload, "model_fields_set", set())  # pydantic v2
    except Exception:  # noqa: BLE001
        provided_fields = set()
    language_source = "client_payload" if "document_language" in provided_fields else "default_unknown"
    logger.info(
        "DocumentVersion: document_language определён "
        f"(document_id={document_id}, version_label={payload.version_label!r}, "
        f"document_language={payload.document_language.value}, source={language_source})"
    )

    version = DocumentVersion(
        document_id=document_id,
        version_label=payload.version_label,
        source_file_uri=None,  # Будет обновлено при upload
        source_sha256=None,  # Будет обновлено при upload
        effective_date=payload.effective_date,
        ingestion_status=IngestionStatus.UPLOADED,  # Устанавливаем uploaded сразу (допускается по требованиям)
        document_language=payload.document_language,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    
    # Audit logging
    await log_audit(
        db=db,
        workspace_id=document.workspace_id,
        action="create",
        entity_type="document_version",
        entity_id=str(version.id),
        after_json={
            "version_label": payload.version_label,
            "effective_date": payload.effective_date.isoformat() if payload.effective_date else None,
            "ingestion_status": IngestionStatus.UPLOADED.value,
        },
    )
    await db.commit()
    
    return DocumentVersionOut.model_validate(version)


@router.post(
    "/document-versions/{version_id}/upload",
    response_model=UploadResult,
    status_code=status.HTTP_200_OK,
)
async def upload_document_version(
    version_id: UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> UploadResult:
    """Загрузка файла для версии документа."""
    # Проверяем существование version и загружаем document для получения workspace_id
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))
    
    # Загружаем document для получения workspace_id (до коммита, чтобы избежать lazy loading)
    document = await db.get(Document, version.document_id)
    if not document:
        raise NotFoundError("Document", str(version.document_id))
    
    workspace_id = document.workspace_id

    # Валидация файла
    if not file.filename:
        raise ValidationError("Имя файла не может быть пустым")
    
    validate_file_extension(file.filename)
    
    # Проверяем, что файл не пустой
    # Читаем первый chunk для проверки
    first_chunk = await file.read(1)
    if not first_chunk:
        raise ValidationError("Файл не может быть пустым")
    
    # Возвращаем позицию файла в начало
    # FastAPI UploadFile использует SpooledTemporaryFile или BytesIO, которые поддерживают seek
    file.file.seek(0)

    # Сохраняем состояние до изменений для audit
    before_state = {
        "source_file_uri": version.source_file_uri,
        "source_sha256": version.source_sha256,
        "ingestion_status": version.ingestion_status.value,
    }

    # Сохраняем файл (с стримингом и вычислением SHA256)
    stored_file = await save_upload(file, version_id)

    # Обновляем ingestion_summary_json
    summary = version.ingestion_summary_json or {}
    summary.update({
        "filename": stored_file.original_filename,
        "size_bytes": stored_file.size_bytes,
    })

    # Автодетект языка на upload: только если язык не был задан явно (UNKNOWN) и это DOCX.
    # Если язык уже ru/en/mixed — не трогаем.
    detected_lang: DocumentLanguage | None = None
    detected_meta: dict[str, Any] | None = None
    try:
        file_ext = Path(stored_file.original_filename).suffix.lower()
    except Exception:  # noqa: BLE001
        file_ext = ""

    if version.document_language == DocumentLanguage.UNKNOWN and file_ext == ".docx":
        path = _uri_to_path(stored_file.uri)
        detected_lang, detected_meta = _detect_language_from_docx(path)
        if detected_lang != DocumentLanguage.UNKNOWN:
            version.document_language = detected_lang
            summary["document_language"] = detected_lang.value
            summary["document_language_source"] = "auto_detect_upload"
            summary["document_language_detect"] = detected_meta
            logger.info(
                "DocumentVersion: document_language auto-detected on upload "
                f"(version_id={version_id}, detected={detected_lang.value}, meta={detected_meta})"
            )
        else:
            summary["document_language_detect"] = detected_meta
            logger.info(
                "DocumentVersion: document_language auto-detect inconclusive "
                f"(version_id={version_id}, meta={detected_meta})"
            )

    # Обновляем version
    version.source_file_uri = stored_file.uri
    version.source_sha256 = stored_file.sha256
    version.ingestion_status = IngestionStatus.UPLOADED
    version.ingestion_summary_json = summary
    await db.commit()
    
    # Audit logging
    await log_audit(
        db=db,
        workspace_id=workspace_id,
        action="upload",
        entity_type="document_version",
        entity_id=str(version_id),
        before_json=before_state,
        after_json={
            "source_file_uri": stored_file.uri,
            "source_sha256": stored_file.sha256,
            "ingestion_status": IngestionStatus.UPLOADED.value,
            "filename": stored_file.original_filename,
            "size_bytes": stored_file.size_bytes,
            "document_language": version.document_language.value,
            "document_language_source": summary.get("document_language_source"),
        },
    )
    await db.commit()

    return UploadResult(
        version_id=version_id,
        uri=stored_file.uri,
        sha256=stored_file.sha256,
        size=stored_file.size_bytes,
        status=IngestionStatus.UPLOADED.value,
    )


@router.post(
    "/document-versions/{version_id}/ingest",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_ingestion(
    version_id: UUID,
    force: bool = Query(False, description="Принудительный перезапуск для failed/needs_review статусов"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Запуск ингестии документа.
    
    State machine:
    - uploaded -> processing -> ready/needs_review/failed
    - Можно перезапустить с force=true для failed/needs_review
    """
    # Проверяем существование version и загружаем document для получения workspace_id
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))
    
    # Загружаем document для получения workspace_id (до коммитов, чтобы избежать lazy loading)
    document = await db.get(Document, version.document_id)
    if not document:
        raise NotFoundError("Document", str(version.document_id))
    
    workspace_id = document.workspace_id

    # Guard rails: проверка наличия файла
    if not version.source_file_uri or not version.source_sha256:
        raise ValidationError(
            "Нельзя запустить ингестию без загруженного файла. Сначала загрузите файл через /upload"
        )

    # Guard rails: проверка статуса
    current_status = version.ingestion_status
    
    # Запрещаем ingest, если статус уже processing
    if current_status == IngestionStatus.PROCESSING:
        raise ConflictError(
            f"Ингестия уже выполняется. Текущий статус: {current_status.value}",
            details={"current_status": current_status.value}
        )
    
    # Разрешаем re-ingest при failed и needs_review только с force=true
    if current_status in (IngestionStatus.FAILED, IngestionStatus.NEEDS_REVIEW):
        if not force:
            raise ConflictError(
                f"Ингестия уже завершена со статусом {current_status.value}. "
                "Используйте force=true для перезапуска",
                details={"current_status": current_status.value}
            )
    
    # Сохраняем состояние до изменений для audit
    before_state = {
        "ingestion_status": current_status.value,
        "ingestion_summary_json": version.ingestion_summary_json,
    }

    # Переводим статус: uploaded -> processing
    version.ingestion_status = IngestionStatus.PROCESSING
    await db.commit()
    
    # Audit: логируем переход в processing
    await log_audit(
        db=db,
        workspace_id=workspace_id,
        action="ingest_start",
        entity_type="document_version",
        entity_id=str(version_id),
        before_json=before_state,
        after_json={"ingestion_status": IngestionStatus.PROCESSING.value},
    )
    await db.commit()

    try:
        # Выполняем ингестию через JobRunner
        ingestion_result = await run_ingestion_now(db, version_id)
        
        # Определяем финальный статус на основе результата
        if ingestion_result.needs_review or ingestion_result.warnings:
            final_status = IngestionStatus.NEEDS_REVIEW
        else:
            final_status = IngestionStatus.READY
        
        # Формируем стабильный summary
        all_warnings: list[str] = []
        if getattr(ingestion_result, "warnings", None):
            all_warnings.extend(list(getattr(ingestion_result, "warnings") or []))
        if getattr(ingestion_result, "docx_summary", None) and isinstance(ingestion_result.docx_summary, dict):
            docx_warnings = ingestion_result.docx_summary.get("warnings") or []
            all_warnings.extend(list(docx_warnings))

        summary = await _build_ingestion_summary(
            db=db,
            version=version,
            ingestion_result=ingestion_result,
            final_status=final_status,
            warnings=all_warnings,
            errors=[],
        )

        # Собираем counts_by_type и num_sections из созданных anchors (доп. поля; не часть стабильной схемы)
        if ingestion_result.anchors_created > 0:
            counts_result = await db.execute(
                select(
                    Anchor.content_type,
                    func.count(Anchor.id).label("count"),
                )
                .where(Anchor.doc_version_id == version_id)
                .group_by(Anchor.content_type)
            )
            summary["counts_by_type"] = {
                row.content_type.value: row.count
                for row in counts_result.all()
            }
            sections_result = await db.execute(
                select(Anchor.section_path)
                .where(Anchor.doc_version_id == version_id)
                .distinct()
            )
            unique_sections = sorted([row.section_path for row in sections_result.all()])
            summary["num_sections"] = len(unique_sections)
            summary["sections"] = unique_sections

        version.ingestion_summary_json = summary
        
        # Обновляем статус
        version.ingestion_status = final_status
        await db.commit()
        
        # Audit: логируем завершение ингестии
        await log_audit(
            db=db,
            workspace_id=workspace_id,
            action="ingest_complete",
            entity_type="document_version",
            entity_id=str(version_id),
            before_json={"ingestion_status": IngestionStatus.PROCESSING.value},
            after_json={
                "ingestion_status": final_status.value,
                "ingestion_summary": version.ingestion_summary_json,
            },
        )
        await db.commit()
        
        return {
            "status": final_status.value,
            "version_id": str(version_id),
            "anchors_created": ingestion_result.anchors_created,
            "chunks_created": ingestion_result.chunks_created,
            "soa_detected": ingestion_result.soa_detected,
            "warnings": ingestion_result.warnings,
            "needs_review": ingestion_result.needs_review,
        }
        
    except Exception as e:
        # Обработка ошибок: processing -> failed
        error_message = str(e)
        version.ingestion_status = IngestionStatus.FAILED
        version.ingestion_summary_json = await _build_ingestion_summary(
            db=db,
            version=version,
            ingestion_result=None,
            final_status=IngestionStatus.FAILED,
            warnings=[],
            errors=[error_message],
        )
        await db.commit()
        
        # Audit: логируем ошибку
        await log_audit(
            db=db,
            workspace_id=workspace_id,
            action="ingest_failed",
            entity_type="document_version",
            entity_id=str(version_id),
            before_json={"ingestion_status": IngestionStatus.PROCESSING.value},
            after_json={
                "ingestion_status": IngestionStatus.FAILED.value,
                "error": error_message,
            },
        )
        await db.commit()
        
        # Пробрасываем ошибку дальше
        raise


@router.get(
    "/document-versions/{version_id}",
    response_model=DocumentVersionOut,
)
async def get_document_version(
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> DocumentVersionOut:
    """Получение версии документа по ID."""
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))
    return DocumentVersionOut.model_validate(version)


@router.get(
    "/document-versions/{version_id}/anchors",
    response_model=list[AnchorOut],
)
async def list_anchors(
    version_id: UUID,
    section_path: str | None = Query(None),
    content_type: AnchorContentType | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> list[AnchorOut]:
    """Список якорей версии документа."""
    # Проверяем существование version
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))

    # Получаем anchors
    stmt = select(Anchor).where(Anchor.doc_version_id == version_id)
    if section_path:
        stmt = stmt.where(Anchor.section_path == section_path)
    if content_type:
        stmt = stmt.where(Anchor.content_type == content_type)

    result = await db.execute(stmt)
    anchors = result.scalars().all()
    return [AnchorOut.model_validate(a) for a in anchors]


@router.get(
    "/document-versions/{version_id}/soa",
    response_model=SoAResult,
    status_code=status.HTTP_200_OK,
)
async def get_soa(
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> SoAResult:
    """Получение Schedule of Activities из версии документа."""
    # Проверяем существование version
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))
    
    # Получаем document для получения study_id
    document = await db.get(Document, version.document_id)
    if not document:
        raise NotFoundError("Document", str(version.document_id))
    
    study_id = document.study_id
    
    # Получаем факты SoA
    visits_fact = await db.execute(
        select(Fact)
        .where(
            Fact.study_id == study_id,
            Fact.fact_type == "soa",
            Fact.fact_key == "visits",
            Fact.created_from_doc_version_id == version_id,
        )
    )
    visits_fact_obj = visits_fact.scalar_one_or_none()
    
    procedures_fact = await db.execute(
        select(Fact)
        .where(
            Fact.study_id == study_id,
            Fact.fact_type == "soa",
            Fact.fact_key == "procedures",
            Fact.created_from_doc_version_id == version_id,
        )
    )
    procedures_fact_obj = procedures_fact.scalar_one_or_none()
    
    matrix_fact = await db.execute(
        select(Fact)
        .where(
            Fact.study_id == study_id,
            Fact.fact_type == "soa",
            Fact.fact_key == "matrix",
            Fact.created_from_doc_version_id == version_id,
        )
    )
    matrix_fact_obj = matrix_fact.scalar_one_or_none()
    
    # Если нет ни одного факта, SoA не найден
    if not visits_fact_obj and not procedures_fact_obj and not matrix_fact_obj:
        raise NotFoundError(
            "SoA",
            f"SoA не найден для версии документа {version_id}",
        )
    
    # Собираем данные из фактов
    visits_data = visits_fact_obj.value_json.get("visits", []) if visits_fact_obj else []
    procedures_data = procedures_fact_obj.value_json.get("procedures", []) if procedures_fact_obj else []
    matrix_data = matrix_fact_obj.value_json.get("matrix", []) if matrix_fact_obj else []
    
    # Определяем table_index и section_path из ingestion_summary_json
    table_index = 0
    section_path = "ROOT"
    confidence = 0.7
    soa_warnings: list[str] = []
    
    if version.ingestion_summary_json:
        soa_info = version.ingestion_summary_json.get("soa", {})
        table_index = soa_info.get("table_index", 0)
        section_path = soa_info.get("section_path", "ROOT")
        confidence = soa_info.get("confidence", 0.7)
        
        # Получаем warnings из summary
        summary_warnings = version.ingestion_summary_json.get("warnings", [])
        soa_warnings = [w for w in summary_warnings if "soa" in w.lower() or "schedule" in w.lower()]
    
    # Если section_path не найден, пытаемся получить из первого anchor
    if section_path == "ROOT" and visits_data:
        first_visit_anchor_id = visits_data[0].get("anchor_id") if visits_data else None
        if first_visit_anchor_id:
            anchor_stmt = select(Anchor).where(Anchor.anchor_id == first_visit_anchor_id)
            anchor_result = await db.execute(anchor_stmt)
            anchor_obj = anchor_result.scalar_one_or_none()
            if anchor_obj:
                section_path = anchor_obj.section_path
    
    # Преобразуем dict в Pydantic модели
    from app.schemas.common import SoAVisit, SoAProcedure, SoAMatrixEntry
    
    visits = [SoAVisit(**v) if isinstance(v, dict) else v for v in visits_data]
    procedures = [SoAProcedure(**p) if isinstance(p, dict) else p for p in procedures_data]
    matrix = [SoAMatrixEntry(**m) if isinstance(m, dict) else m for m in matrix_data]
    
    return SoAResult(
        table_index=table_index,
        section_path=section_path,
        visits=visits,
        procedures=procedures,
        matrix=matrix,
        notes=[],  # TODO: извлечь notes если есть
        confidence=confidence,
        warnings=soa_warnings,
    )
