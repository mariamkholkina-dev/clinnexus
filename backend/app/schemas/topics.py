from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.db.enums import DocumentLanguage, DocumentType


class TopicOut(BaseModel):
    """Схема для вывода топика."""

    id: UUID
    workspace_id: UUID
    topic_key: str
    title_ru: str | None
    title_en: str | None
    description: str | None
    topic_profile_json: dict[str, Any]
    applicable_to_json: list[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TopicListItem(BaseModel):
    """Упрощенная схема для списка топиков."""

    topic_key: str
    title_ru: str | None
    title_en: str | None
    is_active: bool

    class Config:
        from_attributes = True


class HeadingClusterOut(BaseModel):
    """Схема для вывода кластера заголовков."""

    id: UUID
    doc_version_id: UUID
    cluster_id: int
    language: DocumentLanguage
    top_titles_json: list[Any]
    examples_json: list[Any]
    stats_json: dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class TopicMappingRunOut(BaseModel):
    """Схема для вывода запуска маппинга топиков."""

    id: UUID
    doc_version_id: UUID
    mode: str
    pipeline_version: str
    pipeline_config_hash: str
    params_json: dict[str, Any]
    metrics_json: dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class ClusterAssignmentOut(BaseModel):
    """Схема для вывода привязки кластера к топику."""

    id: UUID
    doc_version_id: UUID
    cluster_id: int
    topic_key: str
    mapped_by: str
    confidence: float | None
    notes: str | None
    mapping_debug_json: dict[str, Any] | None
    created_at: datetime

    class Config:
        from_attributes = True


class TopicZonePriorOut(BaseModel):
    """Схема для вывода приоритета зоны для топика."""

    id: UUID
    topic_key: str
    doc_type: DocumentType
    zone_key: str
    weight: float
    notes: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class TopicZonePriorCreate(BaseModel):
    """Схема для создания приоритета зоны для топика."""

    topic_key: str
    doc_type: DocumentType
    zone_key: str
    weight: float
    notes: str | None = None


class TopicZonePriorUpdate(BaseModel):
    """Схема для обновления приоритета зоны для топика."""

    weight: float | None = None
    notes: str | None = None

