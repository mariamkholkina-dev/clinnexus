from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import CitationPolicy, DocumentType, SectionMapMappedBy, SectionMapStatus


class SectionContractCreate(BaseModel):
    """Схема для создания контракта секции."""

    workspace_id: UUID
    doc_type: DocumentType
    section_key: str
    title: str
    required_facts_json: dict[str, Any]
    allowed_sources_json: dict[str, Any]
    retrieval_recipe_json: dict[str, Any]
    qc_ruleset_json: dict[str, Any]
    citation_policy: CitationPolicy
    version: int = 1
    is_active: bool = True


class SectionContractOut(BaseModel):
    """Схема для вывода контракта секции."""

    id: UUID
    workspace_id: UUID
    doc_type: DocumentType
    section_key: str
    title: str
    required_facts_json: dict[str, Any]
    allowed_sources_json: dict[str, Any]
    retrieval_recipe_json: dict[str, Any]
    qc_ruleset_json: dict[str, Any]
    citation_policy: CitationPolicy
    version: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SectionMapOut(BaseModel):
    """Схема для вывода маппинга секции."""

    id: UUID
    doc_version_id: UUID
    section_key: str
    anchor_ids: list[str] | None
    chunk_ids: list[UUID] | None
    confidence: float
    status: SectionMapStatus
    mapped_by: SectionMapMappedBy
    notes: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class SectionMapOverrideRequest(BaseModel):
    """Схема для переопределения маппинга секции."""

    anchor_ids: list[str] | None = None
    chunk_ids: list[UUID] | None = None
    notes: str | None = None

