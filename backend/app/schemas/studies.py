from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import StudyStatus


class StudyCreate(BaseModel):
    """Схема для создания исследования."""

    workspace_id: UUID
    study_code: str
    title: str
    status: StudyStatus = StudyStatus.ACTIVE


class StudyOut(BaseModel):
    """Схема для вывода исследования."""

    id: UUID
    workspace_id: UUID
    study_code: str
    title: str
    status: StudyStatus
    created_at: datetime

    class Config:
        from_attributes = True

