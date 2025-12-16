from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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

    model_config = ConfigDict(extra="ignore")

    # Legacy (v1): плоские списки (оставляем для migration-safe совместимости).
    claims: list[str] = Field(default_factory=list)
    numbers: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)  # anchor_id

    # MVP (v2): структурированные утверждения и ссылки (per-claim).
    claim_items: list["ClaimArtifact"] = Field(default_factory=list)
    citation_items: list["CitationArtifact"] = Field(default_factory=list)


class GenerateSectionResult(BaseModel):
    """Результат генерации секции."""

    content_text: str
    artifacts_json: ArtifactsSchema
    qc_status: QCStatus
    qc_report_json: QCReportSchema
    generation_run_id: UUID


class CitationArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    anchor_id: str
    role: str | None = None  # primary|supporting
    note: str | None = None


class NumberArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: float | int
    unit: str | None = None
    fact_key: str | None = None


class ClaimArtifact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    anchor_ids: list[str] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)  # fact_key
    numbers: list[NumberArtifact] = Field(default_factory=list)


# Pydantic v2: разрешаем forward refs в ArtifactsSchema
ArtifactsSchema.model_rebuild()

