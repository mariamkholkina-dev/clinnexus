from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import DocumentLanguage
from app.db.models.anchors import Vector1536
from app.db.models.studies import DocumentLanguageType


class Topic(Base):
    """Топик - семантическая тема для группировки контента."""

    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_key: Mapped[str] = mapped_column(Text, nullable=False)
    title_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_en: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_profile_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    topic_embedding: Mapped[list[float] | None] = mapped_column(
        Vector1536, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ClusterAssignment(Base):
    """Привязка кластера к топику для конкретной версии документа."""

    __tablename__ = "cluster_assignments"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_key: Mapped[str] = mapped_column(Text, nullable=False)
    mapped_by: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapping_debug_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HeadingCluster(Base):
    """Кластер заголовков для версии документа."""

    __tablename__ = "heading_clusters"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[DocumentLanguage] = mapped_column(
        DocumentLanguageType(),
        nullable=False,
    )
    top_titles_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    examples_json: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    stats_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    cluster_embedding: Mapped[list[float] | None] = mapped_column(
        Vector1536, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TopicMappingRun(Base):
    """Запись о запуске маппинга топиков для версии документа."""

    __tablename__ = "topic_mapping_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline_version: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline_config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TopicEvidence(Base):
    """Агрегированное доказательство для топика из anchors и chunks."""

    __tablename__ = "topic_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_zone: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[DocumentLanguage] = mapped_column(
        DocumentLanguageType(),
        nullable=False,
    )
    anchor_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    chunk_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=False
    )
    score: Mapped[float | None] = mapped_column(nullable=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

