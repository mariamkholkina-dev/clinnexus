from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import DocumentType, ImpactStatus, RecommendedAction


class ImpactItemOut(BaseModel):
    """Схема для вывода элемента воздействия."""

    id: UUID
    change_event_id: UUID
    affected_doc_type: DocumentType
    affected_section_key: str
    reason_json: dict[str, Any]
    recommended_action: RecommendedAction
    status: ImpactStatus
    created_at: datetime

    class Config:
        from_attributes = True

