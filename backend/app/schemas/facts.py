from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.enums import EvidenceRole, FactStatus


class FactEvidenceOut(BaseModel):
    """Схема для вывода доказательства факта."""

    anchor_id: str
    role: EvidenceRole = Field(alias="evidence_role")

    class Config:
        from_attributes = True
        populate_by_name = True


class FactOut(BaseModel):
    """Схема для вывода факта."""

    id: UUID
    fact_type: str
    fact_key: str
    value_json: dict[str, Any]
    unit: str | None
    status: FactStatus
    created_from_doc_version_id: UUID | None
    created_at: datetime
    updated_at: datetime
    evidence: list[FactEvidenceOut] = []

    class Config:
        from_attributes = True

