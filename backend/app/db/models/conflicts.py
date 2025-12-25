from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import ConflictSeverity, ConflictStatus


class ConflictSeverityType(TypeDecorator):
    """TypeDecorator для правильной конвертации ConflictSeverity enum в значение строки."""

    impl = PG_ENUM
    cache_ok = True

    def __init__(self):
        super().__init__(
            ConflictSeverity,
            name="conflict_severity",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )

    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, ConflictSeverity):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return ConflictSeverity(value)


class ConflictStatusType(TypeDecorator):
    """TypeDecorator для правильной конвертации ConflictStatus enum в значение строки."""

    impl = PG_ENUM
    cache_ok = True

    def __init__(self):
        super().__init__(
            ConflictStatus,
            name="conflict_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )

    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, ConflictStatus):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return ConflictStatus(value)


class Conflict(Base):
    __tablename__ = "conflicts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    study_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=False,
    )
    conflict_type: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[ConflictSeverity] = mapped_column(
        ConflictSeverityType(),
        nullable=False,
    )
    status: Mapped[ConflictStatus] = mapped_column(
        ConflictStatusType(),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ConflictItem(Base):
    __tablename__ = "conflict_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conflict_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conflicts.id", ondelete="CASCADE"),
        nullable=False,
    )
    left_anchor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    right_anchor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    left_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("facts.id", ondelete="SET NULL"),
        nullable=True,
    )
    right_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("facts.id", ondelete="SET NULL"),
        nullable=True,
    )
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


