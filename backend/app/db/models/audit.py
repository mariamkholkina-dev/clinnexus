from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base, TimestampMixin, UUIDMixin
from app.db.enums import AuditCategory, AuditSeverity, AuditStatus


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditSeverityType(TypeDecorator):
    """TypeDecorator для правильной конвертации AuditSeverity enum в значение строки."""

    impl = PG_ENUM
    cache_ok = True

    def __init__(self):
        super().__init__(
            AuditSeverity,
            name="audit_severity",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )

    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, AuditSeverity):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return AuditSeverity(value)


class AuditCategoryType(TypeDecorator):
    """TypeDecorator для правильной конвертации AuditCategory enum в значение строки."""

    impl = PG_ENUM
    cache_ok = True

    def __init__(self):
        super().__init__(
            AuditCategory,
            name="audit_category",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )

    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, AuditCategory):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return AuditCategory(value)


class AuditStatusType(TypeDecorator):
    """TypeDecorator для правильной конвертации AuditStatus enum в значение строки."""

    impl = PG_ENUM
    cache_ok = True

    def __init__(self):
        super().__init__(
            AuditStatus,
            name="audit_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )

    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, AuditStatus):
            return value.value
        return value

    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return AuditStatus(value)


class AuditIssue(Base, UUIDMixin, TimestampMixin):
    """Модель для хранения аудиторских находок (issues)."""

    __tablename__ = "audit_issues"
    __table_args__ = (
        Index("ix_audit_issues_study_id", "study_id"),
        Index("ix_audit_issues_doc_version_id", "doc_version_id"),
        Index("ix_audit_issues_severity", "severity"),
        Index("ix_audit_issues_category", "category"),
        Index("ix_audit_issues_status", "status"),
        Index("ix_audit_issues_study_status", "study_id", "status"),
    )

    study_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    severity: Mapped[AuditSeverity] = mapped_column(
        AuditSeverityType(),
        nullable=False,
    )
    category: Mapped[AuditCategory] = mapped_column(
        AuditCategoryType(),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location_anchors: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[AuditStatus] = mapped_column(
        AuditStatusType(),
        nullable=False,
    )
    suppression_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


