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
from app.services.anchor_aligner import AnchorAligner
from app.db.models.studies import Document, DocumentVersion, Study
from app.db.models.anchors import Anchor
from app.db.models.anchor_matches import AnchorMatch
from app.db.enums import AnchorContentType, IngestionStatus, DocumentLanguage
from app.schemas.common import SoAResult
from app.schemas.documents import (
    DocumentCreate,
    DocumentOut,
    DocumentVersionCreate,
    DocumentVersionOut,
    UploadResult,
    DiffResult,
    ChangedAnchor,
)
from app.schemas.anchors import AnchorOut
from app.db.models.facts import Fact

router = APIRouter()

# Разрешенные расширения файлов
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx"}

_CYR_RE = re.compile(r"[А-Яа-яЁё]")
_LAT_RE = re.compile(r"[A-Za-z]")


def _detect_text_language(text: str) -> DocumentLanguage:
    """
    Быстрая локальная детекция языка из текста (regex на кириллицу/латиницу).
    Используется для определения языка конкретного anchor/chunk.
    """
    if not text:
        return DocumentLanguage.UNKNOWN
    
    cyr_count = len(_CYR_RE.findall(text))
    lat_count = len(_LAT_RE.findall(text))
    total_letters = cyr_count + lat_count
    
    if total_letters == 0:
        return DocumentLanguage.UNKNOWN
    
    cyr_ratio = cyr_count / total_letters
    
    if cyr_ratio >= 0.7:
        return DocumentLanguage.RU
    elif cyr_ratio <= 0.3:
        return DocumentLanguage.EN
    else:
        # Смешанный, если достаточно обеих букв
        return DocumentLanguage.MIXED if cyr_count >= 10 and lat_count >= 10 else DocumentLanguage.UNKNOWN


def _detect_bilingual_two_column_tables(doc) -> tuple[bool, dict[str, Any]]:
    """
    Детектирует bilingual two-column таблицы в DOCX:
    - если значимая доля текста приходит из таблиц 2-колоночного вида (в одной ячейке RU, в другой EN)
    - или если в таблицах много пар строк ru/en по соседству
    
    Returns:
        (is_bilingual, metadata) - является ли документ bilingual two-column
    """
    tables = getattr(doc, "tables", []) or []
    if not tables:
        return False, {"tables_count": 0}
    
    two_column_tables = 0
    bilingual_cells_count = 0
    total_table_chars = 0
    bilingual_pairs_count = 0
    
    for tbl in tables:
        rows = getattr(tbl, "rows", []) or []
        if not rows:
            continue
        
        # Проверяем, является ли таблица 2-колоночной (≈2 колонки)
        if len(rows) > 0:
            col_count = len(rows[0].cells) if len(rows[0].cells) > 0 else 0
            if col_count == 2:
                two_column_tables += 1
                table_chars = 0
                row_bilingual_pairs = 0
                
                # Проверяем каждую строку на bilingual пары
                for row in rows:
                    cells = getattr(row, "cells", []) or []
                    if len(cells) >= 2:
                        cell1_text = (getattr(cells[0], "text", "") or "").strip()
                        cell2_text = (getattr(cells[1], "text", "") or "").strip()
                        
                        if cell1_text and cell2_text:
                            table_chars += len(cell1_text) + len(cell2_text)
                            
                            # Проверяем, является ли пара bilingual (один RU, другой EN)
                            lang1 = _detect_text_language(cell1_text)
                            lang2 = _detect_text_language(cell2_text)
                            
                            if (lang1 == DocumentLanguage.RU and lang2 == DocumentLanguage.EN) or \
                               (lang1 == DocumentLanguage.EN and lang2 == DocumentLanguage.RU):
                                bilingual_cells_count += 2
                                row_bilingual_pairs += 1
                
                total_table_chars += table_chars
                if row_bilingual_pairs > 0:
                    bilingual_pairs_count += row_bilingual_pairs
                
                # Также проверяем соседние строки (ru/en по соседству)
                for i in range(len(rows) - 1):
                    row1_cells = getattr(rows[i], "cells", []) or []
                    row2_cells = getattr(rows[i + 1], "cells", []) or []
                    
                    if row1_cells and row2_cells:
                        row1_text = (getattr(row1_cells[0], "text", "") or "").strip()
                        row2_text = (getattr(row2_cells[0], "text", "") or "").strip()
                        
                        if row1_text and row2_text:
                            lang1 = _detect_text_language(row1_text)
                            lang2 = _detect_text_language(row2_text)
                            
                            if (lang1 == DocumentLanguage.RU and lang2 == DocumentLanguage.EN) or \
                               (lang1 == DocumentLanguage.EN and lang2 == DocumentLanguage.RU):
                                bilingual_pairs_count += 1
    
    # Определяем, является ли документ bilingual two-column
    # Порог: если >=30% текста из таблиц приходится на bilingual пары, или много пар строк
    is_bilingual = False
    if two_column_tables > 0 and total_table_chars > 0:
        bilingual_ratio = bilingual_cells_count * 50 / total_table_chars if total_table_chars > 0 else 0
        # Значимая доля = >=30% текста в таблицах bilingual, или >=5 пар
        if bilingual_ratio >= 0.3 or bilingual_pairs_count >= 5:
            is_bilingual = True
    
    meta = {
        "tables_count": len(tables),
        "two_column_tables": two_column_tables,
        "bilingual_cells_count": bilingual_cells_count,
        "bilingual_pairs_count": bilingual_pairs_count,
        "total_table_chars": total_table_chars,
        "bilingual_ratio": round(bilingual_cells_count * 50 / total_table_chars, 4) if total_table_chars > 0 else 0,
    }
    return is_bilingual, meta


def _uri_to_path(uri: str) -> Path:
    if uri.startswith("file://"):
        parsed = urllib.parse.urlparse(uri)
        path = urllib.parse.unquote(parsed.path)
        # Windows: file:///C:/path -> /C:/path
        if path.startswith("/") and len(path) > 3 and path[2] == ":":
            path = path[1:]
        return Path(path)
    return Path(uri)


def _detect_text_language(text: str) -> DocumentLanguage:
    """
    Быстрая локальная детекция языка из текста (regex на кириллицу/латиницу).
    Используется для определения языка конкретного anchor/chunk.
    """
    if not text:
        return DocumentLanguage.UNKNOWN
    
    cyr_count = len(_CYR_RE.findall(text))
    lat_count = len(_LAT_RE.findall(text))
    total_letters = cyr_count + lat_count
    
    if total_letters == 0:
        return DocumentLanguage.UNKNOWN
    
    cyr_ratio = cyr_count / total_letters
    
    if cyr_ratio >= 0.7:
        return DocumentLanguage.RU
    elif cyr_ratio <= 0.3:
        return DocumentLanguage.EN
    else:
        # Смешанный, если достаточно обеих букв
        return DocumentLanguage.MIXED if cyr_count >= 10 and lat_count >= 10 else DocumentLanguage.UNKNOWN


def _detect_bilingual_two_column_tables(doc) -> tuple[bool, dict[str, Any]]:
    """
    Детектирует bilingual two-column таблицы в DOCX:
    - если значимая доля текста приходит из таблиц 2-колоночного вида (в одной ячейке RU, в другой EN)
    - или если в таблицах много пар строк ru/en по соседству
    
    Returns:
        (is_bilingual, metadata) - является ли документ bilingual two-column
    """
    tables = getattr(doc, "tables", []) or []
    if not tables:
        return False, {"tables_count": 0}
    
    two_column_tables = 0
    bilingual_cells_count = 0
    total_table_chars = 0
    bilingual_pairs_count = 0
    
    for tbl in tables:
        rows = getattr(tbl, "rows", []) or []
        if not rows:
            continue
        
        # Проверяем, является ли таблица 2-колоночной (≈2 колонки)
        if len(rows) > 0:
            col_count = len(rows[0].cells) if len(rows[0].cells) > 0 else 0
            if col_count == 2:
                two_column_tables += 1
                table_chars = 0
                row_bilingual_pairs = 0
                
                # Проверяем каждую строку на bilingual пары
                for row in rows:
                    cells = getattr(row, "cells", []) or []
                    if len(cells) >= 2:
                        cell1_text = (getattr(cells[0], "text", "") or "").strip()
                        cell2_text = (getattr(cells[1], "text", "") or "").strip()
                        
                        if cell1_text and cell2_text:
                            table_chars += len(cell1_text) + len(cell2_text)
                            
                            # Проверяем, является ли пара bilingual (один RU, другой EN)
                            lang1 = _detect_text_language(cell1_text)
                            lang2 = _detect_text_language(cell2_text)
                            
                            if (lang1 == DocumentLanguage.RU and lang2 == DocumentLanguage.EN) or \
                               (lang1 == DocumentLanguage.EN and lang2 == DocumentLanguage.RU):
                                bilingual_cells_count += 2
                                row_bilingual_pairs += 1
                
                total_table_chars += table_chars
                if row_bilingual_pairs > 0:
                    bilingual_pairs_count += row_bilingual_pairs
                
                # Также проверяем соседние строки (ru/en по соседству)
                for i in range(len(rows) - 1):
                    row1_cells = getattr(rows[i], "cells", []) or []
                    row2_cells = getattr(rows[i + 1], "cells", []) or []
                    
                    if row1_cells and row2_cells:
                        row1_text = (getattr(row1_cells[0], "text", "") or "").strip()
                        row2_text = (getattr(row2_cells[0], "text", "") or "").strip()
                        
                        if row1_text and row2_text:
                            lang1 = _detect_text_language(row1_text)
                            lang2 = _detect_text_language(row2_text)
                            
                            if (lang1 == DocumentLanguage.RU and lang2 == DocumentLanguage.EN) or \
                               (lang1 == DocumentLanguage.EN and lang2 == DocumentLanguage.RU):
                                bilingual_pairs_count += 1
    
    # Определяем, является ли документ bilingual two-column
    # Порог: если >=30% текста из таблиц приходится на bilingual пары, или много пар строк
    is_bilingual = False
    if two_column_tables > 0 and total_table_chars > 0:
        bilingual_ratio = bilingual_cells_count * 50 / total_table_chars if total_table_chars > 0 else 0
        # Значимая доля = >=30% текста в таблицах bilingual, или >=5 пар
        if bilingual_ratio >= 0.3 or bilingual_pairs_count >= 5:
            is_bilingual = True
    
    meta = {
        "tables_count": len(tables),
        "two_column_tables": two_column_tables,
        "bilingual_cells_count": bilingual_cells_count,
        "bilingual_pairs_count": bilingual_pairs_count,
        "total_table_chars": total_table_chars,
        "bilingual_ratio": round(bilingual_cells_count * 50 / total_table_chars, 4) if total_table_chars > 0 else 0,
    }
    return is_bilingual, meta


def _detect_language_from_docx(file_path: Path, max_chars: int = 50_000) -> tuple[DocumentLanguage, dict[str, Any]]:
    """
    Детект языка документа:
    - считаем количество кириллических и латинских букв
    - ru/en/mixed на основе долей
    - дополнительно проверяем bilingual two-column таблицы для mixed
    """
    try:
        from docx import Document as DocxDocument  # локальный импорт, чтобы не тянуть зависимость везде
    except Exception as e:  # noqa: BLE001
        return DocumentLanguage.UNKNOWN, {"error": f"python-docx import failed: {e}"}

    try:
        doc = DocxDocument(str(file_path))
    except Exception as e:  # noqa: BLE001
        return DocumentLanguage.UNKNOWN, {"error": f"docx open failed: {e}"}

    # Проверяем bilingual two-column таблицы
    is_bilingual, bilingual_meta = _detect_bilingual_two_column_tables(doc)
    if is_bilingual:
        meta = {
            "cyr": 0,
            "lat": 0,
            "letters": 0,
            "cyr_ratio": 0.0,
            "lat_ratio": 0.0,
            "max_chars": max_chars,
            "bilingual_two_column": True,
            **bilingual_meta,
        }
        return DocumentLanguage.MIXED, meta

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
        meta = {
            "cyr": cyr,
            "lat": lat,
            "letters": letters,
            "max_chars": max_chars,
            "bilingual_two_column": False,
            **bilingual_meta,
        }
        return DocumentLanguage.UNKNOWN, meta

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
        "bilingual_two_column": False,
        **bilingual_meta,
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

    # SoA facts actually written: проверяем по БД и извлекаем количество элементов из value_json
    soa_facts_counts: dict[str, int] = {"visits": 0, "procedures": 0, "matrix": 0}
    soa_facts_written_flags: dict[str, bool] = {"visits": False, "procedures": False, "matrix": False}
    
    if ingestion_result is not None:
        # Получаем факты SoA с их value_json
        facts_res = await db.execute(
            select(Fact.fact_key, Fact.value_json)
            .where(
                Fact.created_from_doc_version_id == version.id,
                Fact.fact_type == "soa",
                Fact.fact_key.in_(("visits", "procedures", "matrix")),
            )
        )
        
        for row in facts_res.all():
            fact_key = str(row.fact_key)
            value_json = row.value_json or {}
            
            # Извлекаем количество элементов из value_json
            if fact_key == "visits":
                visits_list = value_json.get("visits", [])
                if isinstance(visits_list, list):
                    soa_facts_counts["visits"] = len(visits_list)
                    soa_facts_written_flags["visits"] = len(visits_list) > 0
            elif fact_key == "procedures":
                procedures_list = value_json.get("procedures", [])
                if isinstance(procedures_list, list):
                    soa_facts_counts["procedures"] = len(procedures_list)
                    soa_facts_written_flags["procedures"] = len(procedures_list) > 0
            elif fact_key == "matrix":
                matrix_list = value_json.get("matrix", [])
                if isinstance(matrix_list, list):
                    soa_facts_counts["matrix"] = len(matrix_list)
                    soa_facts_written_flags["matrix"] = len(matrix_list) > 0

    soa_facts_written = {
        "visits": soa_facts_written_flags["visits"],
        "procedures": soa_facts_written_flags["procedures"],
        "matrix": soa_facts_written_flags["matrix"],
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


@router.post(
    "/documents/{doc_id}/versions/{from_version_id}/diff/{to_version_id}",
    response_model=DiffResult,
    status_code=status.HTTP_200_OK,
)
async def diff_versions(
    doc_id: UUID,
    from_version_id: UUID,
    to_version_id: UUID,
    min_score: float = Query(0.78, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_db),
) -> DiffResult:
    """
    Сравнивает две версии документа и возвращает различия между якорями.
    
    Выполняет выравнивание якорей между версиями и возвращает:
    - matched: количество совпавших якорей
    - changed: список измененных якорей с score
    - added: список добавленных anchor_ids
    - removed: список удаленных anchor_ids
    """
    # Проверяем существование документа
    document = await db.get(Document, doc_id)
    if not document:
        raise NotFoundError("Document", str(doc_id))
    
    # Проверяем существование версий
    from_version = await db.get(DocumentVersion, from_version_id)
    if not from_version:
        raise NotFoundError("DocumentVersion", str(from_version_id))
    
    to_version = await db.get(DocumentVersion, to_version_id)
    if not to_version:
        raise NotFoundError("DocumentVersion", str(to_version_id))
    
    # Проверяем, что версии принадлежат одному документу
    if from_version.document_id != doc_id or to_version.document_id != doc_id:
        raise ValidationError("Версии должны принадлежать одному документу")
    
    # Выполняем выравнивание
    aligner = AnchorAligner(db)
    stats = await aligner.align(
        from_version_id,
        to_version_id,
        scope="body",
        min_score=min_score,
    )
    
    # Получаем детали матчей
    stmt = select(AnchorMatch).where(
        AnchorMatch.from_doc_version_id == from_version_id,
        AnchorMatch.to_doc_version_id == to_version_id,
    )
    result = await db.execute(stmt)
    matches = result.scalars().all()
    
    # Формируем список измененных якорей
    changed: list[ChangedAnchor] = []
    matched_anchor_ids_b: set[str] = set()
    
    for match in matches:
        matched_anchor_ids_b.add(match.to_anchor_id)
        if match.score < 1.0:
            # Генерируем краткое описание изменений
            diff_summary = None
            if match.meta_json:
                text_sim = match.meta_json.get("text_sim")
                if text_sim is not None and text_sim < 1.0:
                    diff_summary = f"Текст изменен (similarity: {text_sim:.2f})"
            
            changed.append(
                ChangedAnchor(
                    from_anchor_id=match.from_anchor_id,
                    to_anchor_id=match.to_anchor_id,
                    score=match.score,
                    diff_summary=diff_summary,
                )
            )
    
    # Получаем все якоря для определения added/removed
    stmt_a = select(Anchor.anchor_id).where(Anchor.doc_version_id == from_version_id)
    result_a = await db.execute(stmt_a)
    all_anchor_ids_a = {row[0] for row in result_a}
    
    stmt_b = select(Anchor.anchor_id).where(Anchor.doc_version_id == to_version_id)
    result_b = await db.execute(stmt_b)
    all_anchor_ids_b = {row[0] for row in result_b}
    
    # Определяем added и removed
    matched_anchor_ids_a = {match.from_anchor_id for match in matches}
    added = list(all_anchor_ids_b - matched_anchor_ids_b)
    removed = list(all_anchor_ids_a - matched_anchor_ids_a)
    
    return DiffResult(
        matched=stats.matched,
        changed=changed,
        added=added,
        removed=removed,
    )
