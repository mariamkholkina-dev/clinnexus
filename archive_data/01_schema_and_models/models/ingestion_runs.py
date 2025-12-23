"""Модели для отслеживания запусков ингестии документов."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base


class IngestionRunStatusType(TypeDecorator):
    """TypeDecorator для правильной конвертации статуса ингестии."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Конвертируем значение при сохранении."""
        if value is None:
            return None
        if isinstance(value, str):
            if value not in ("ok", "failed", "partial"):
                raise ValueError(f"Invalid ingestion run status: {value}")
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        """Конвертируем значение при чтении."""
        if value is None:
            return None
        return value


class IngestionRun(Base):
    """Запись о запуске ингестии документа."""

    __tablename__ = "ingestion_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        IngestionRunStatusType(),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pipeline_version: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline_config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    quality_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="'{}'::jsonb"
    )
    warnings_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )
    errors_json: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'::jsonb"
    )

    # Relationships
    doc_version: Mapped["DocumentVersion"] = relationship(
        back_populates="ingestion_runs",
        foreign_keys=[doc_version_id],
    )

