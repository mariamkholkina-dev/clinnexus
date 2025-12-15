from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import TaskStatus, TaskType


class TaskOut(BaseModel):
    """Схема для вывода задачи."""

    id: UUID
    study_id: UUID
    type: TaskType
    status: TaskStatus
    assigned_to: UUID | None
    payload_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

