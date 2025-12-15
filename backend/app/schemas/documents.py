from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import DocumentLifecycleStatus, DocumentType, IngestionStatus


class DocumentCreate(BaseModel):
    """Схема для создания документа."""

    study_id: UUID
    doc_type: DocumentType
    title: str
    lifecycle_status: DocumentLifecycleStatus = DocumentLifecycleStatus.DRAFT


class DocumentOut(BaseModel):
    """Схема для вывода документа."""

    id: UUID
    workspace_id: UUID
    study_id: UUID
    doc_type: DocumentType
    title: str
    lifecycle_status: DocumentLifecycleStatus
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentVersionCreate(BaseModel):
    """Схема для создания версии документа."""

    document_id: UUID
    version_label: str
    effective_date: date | None = None


class DocumentVersionOut(BaseModel):
    """Схема для вывода версии документа."""

    id: UUID
    document_id: UUID
    version_label: str
    source_file_uri: str
    source_sha256: str
    effective_date: date | None
    ingestion_status: IngestionStatus
    ingestion_summary_json: dict[str, Any] | None
    created_by: UUID | None
    created_at: datetime

    class Config:
        from_attributes = True


class UploadResult(BaseModel):
    """Результат загрузки файла."""

    version_id: UUID
    uri: str
    sha256: str

