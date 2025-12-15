from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.api.deps import get_db
from app.core.errors import NotFoundError, ValidationError
from app.core.storage import save_upload
from app.db.models.studies import Document, DocumentVersion, Study
from app.db.models.anchors import Anchor
from app.db.enums import AnchorContentType, IngestionStatus
from app.schemas.documents import (
    DocumentCreate,
    DocumentOut,
    DocumentVersionCreate,
    DocumentVersionOut,
    UploadResult,
)
from app.schemas.anchors import AnchorOut
from app.services.ingestion import IngestionService
from app.services.section_mapping import SectionMappingService
from app.services.fact_extraction import FactExtractionService

router = APIRouter()


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

    if payload.study_id != study_id:
        raise ValidationError("study_id в payload не совпадает с study_id в пути")

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

    if payload.document_id != document_id:
        raise ValidationError("document_id в payload не совпадает с document_id в пути")

    version = DocumentVersion(
        document_id=document_id,
        version_label=payload.version_label,
        source_file_uri="",  # Будет обновлено при upload
        source_sha256="",  # Будет обновлено при upload
        effective_date=payload.effective_date,
        ingestion_status=IngestionStatus.UPLOADED,
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
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
    # Проверяем существование version
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))

    # Читаем файл
    file_content = await file.read()

    # Сохраняем файл
    uri, sha256 = save_upload(file_content, str(version_id), file.filename or "document.pdf")

    # Обновляем version
    version.source_file_uri = uri
    version.source_sha256 = sha256
    await db.commit()

    return UploadResult(version_id=version_id, uri=uri, sha256=sha256)


@router.post(
    "/document-versions/{version_id}/ingest",
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_ingestion(
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Запуск ингестии документа (ingestion -> section mapping -> fact extraction)."""
    # Проверяем существование version
    version = await db.get(DocumentVersion, version_id)
    if not version:
        raise NotFoundError("DocumentVersion", str(version_id))

    # Запускаем ингестию
    ingestion_service = IngestionService(db)
    ingestion_result = await ingestion_service.ingest(version_id)

    # Маппинг секций (заглушка: используем стандартные секции)
    section_mapping_service = SectionMappingService(db)
    # TODO: получить список section_keys из section_contracts для doc_type
    section_keys = ["protocol.soa", "protocol.endpoints", "csr.methods.schedule"]
    await section_mapping_service.map_sections(version_id, section_keys)

    # Извлечение фактов
    fact_extraction_service = FactExtractionService(db)
    await fact_extraction_service.extract_and_upsert(version_id)

    return {
        "status": "completed",
        "version_id": str(version_id),
        "anchors_count": ingestion_result.anchors_count,
        "chunks_count": ingestion_result.chunks_count,
    }


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
