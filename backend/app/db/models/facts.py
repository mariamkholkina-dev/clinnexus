from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import EvidenceRole, FactScope, FactStatus


class FactStatusType(TypeDecorator):
    """TypeDecorator для правильной конвертации FactStatus enum в значение строки."""

    impl = PG_ENUM
    cache_ok = True

    def __init__(self):
        super().__init__(
            FactStatus,
            name="fact_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )

    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, FactStatus):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return FactStatus(value)


class EvidenceRoleType(TypeDecorator):
    """TypeDecorator для правильной конвертации EvidenceRole enum в значение строки."""

    impl = PG_ENUM
    cache_ok = True

    def __init__(self):
        super().__init__(
            EvidenceRole,
            name="evidence_role",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )

    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, EvidenceRole):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return EvidenceRole(value)


class FactScopeType(TypeDecorator):
    """TypeDecorator для правильной конвертации FactScope enum в значение строки."""

    impl = PG_ENUM
    cache_ok = True

    def __init__(self):
        super().__init__(
            FactScope,
            name="fact_scope",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )

    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, FactScope):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return FactScope(value)


class Fact(Base):
    __tablename__ = "facts"
    __table_args__ = (
        Index("ix_facts_scope", "scope"),
        Index("ix_facts_type_category", "type_category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    study_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_type: Mapped[str] = mapped_column(Text, nullable=False)
    fact_key: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[FactScope] = mapped_column(
        FactScopeType(),
        nullable=False,
        default=FactScope.GLOBAL,
    )
    type_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[FactStatus] = mapped_column(
        FactStatusType(),
        nullable=False,
    )
    created_from_doc_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    extractor_version: Mapped[int | None] = mapped_column(nullable=True)
    meta_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FactEvidence(Base):
    __tablename__ = "fact_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("facts.id", ondelete="CASCADE"),
        nullable=False,
    )
    # anchor_id ссылается на anchors.anchor_id (строковый идентификатор), а не на anchors.id.
    anchor_id: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_role: Mapped[EvidenceRole] = mapped_column(
        EvidenceRoleType(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


