"""Схемы Pydantic для модуля аудита."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.enums import AuditCategory, AuditSeverity


class AuditIssue(BaseModel):
    """Схема для аудиторской находки (issue)."""

    severity: AuditSeverity = Field(..., description="Серьезность находки")
    category: AuditCategory = Field(..., description="Категория проверки")
    description: str = Field(..., description="Описание проблемы")
    location_anchors: list[str] = Field(
        default_factory=list,
        description="Список anchor_id для подсветки мест в документе",
    )
    suggested_fix: str | None = Field(
        None,
        description="Предлагаемое исправление (опционально)",
    )

    class Config:
        from_attributes = False  # Это схема для создания, не для ORM
        populate_by_name = True


class AuditIssueOut(BaseModel):
    """Схема для вывода аудиторской находки из БД."""

    id: UUID
    study_id: UUID
    doc_version_id: UUID | None
    severity: AuditSeverity
    category: AuditCategory
    description: str
    location_anchors: list[str] | None
    suggested_fix: str | None = None
    status: str
    suppression_reason: str | None
    created_at: Any  # datetime
    updated_at: Any  # datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class AuditRunResult(BaseModel):
    """Результат запуска набора аудиторов."""

    doc_version_id: UUID
    auditor_name: str
    issues_count: int
    issues: list[AuditIssue]

    class Config:
        from_attributes = False

