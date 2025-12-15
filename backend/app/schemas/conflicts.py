from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import ConflictSeverity, ConflictStatus


class ConflictOut(BaseModel):
    """Схема для вывода конфликта."""

    id: UUID
    study_id: UUID
    conflict_type: str
    severity: ConflictSeverity
    status: ConflictStatus
    title: str
    description: str
    owner_user_id: UUID | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

