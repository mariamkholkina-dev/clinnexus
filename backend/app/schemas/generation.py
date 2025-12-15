from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import DocumentType, QCStatus


class GenerateSectionRequest(BaseModel):
    """Запрос на генерацию секции."""

    study_id: UUID
    target_doc_type: DocumentType
    section_key: str
    template_id: UUID
    contract_id: UUID
    source_doc_version_ids: list[UUID]
    user_instruction: str | None = None


class QCErrorSchema(BaseModel):
    """Схема ошибки QC."""

    type: str
    message: str
    anchor_ids: list[str] | None = None


class QCReportSchema(BaseModel):
    """Схема отчёта QC."""

    status: QCStatus
    errors: list[QCErrorSchema] = []


class ArtifactsSchema(BaseModel):
    """Схема артефактов генерации."""

    claims: list[str] = []
    numbers: list[dict[str, Any]] = []
    citations: list[str] = []  # anchor_id


class GenerateSectionResult(BaseModel):
    """Результат генерации секции."""

    content_text: str
    artifacts_json: ArtifactsSchema
    qc_status: QCStatus
    qc_report_json: QCReportSchema
    generation_run_id: UUID

