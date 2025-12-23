from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import (
    DocumentLanguage,
    DocumentLifecycleStatus,
    DocumentType,
    IngestionStatus,
    StudyStatus,
)


class StudyStatusType(TypeDecorator):
    """TypeDecorator для правильной конвертации StudyStatus enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            StudyStatus,
            name="study_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, StudyStatus):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return StudyStatus(value)


class DocumentTypeType(TypeDecorator):
    """TypeDecorator для правильной конвертации DocumentType enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            DocumentType,
            name="document_type",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, DocumentType):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return DocumentType(value)


class DocumentLifecycleStatusType(TypeDecorator):
    """TypeDecorator для правильной конвертации DocumentLifecycleStatus enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            DocumentLifecycleStatus,
            name="document_lifecycle_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, DocumentLifecycleStatus):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return DocumentLifecycleStatus(value)


class IngestionStatusType(TypeDecorator):
    """TypeDecorator для правильной конвертации IngestionStatus enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            IngestionStatus,
            name="ingestion_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, IngestionStatus):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return IngestionStatus(value)


class DocumentLanguageType(TypeDecorator):
    """TypeDecorator для правильной конвертации DocumentLanguage enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            DocumentLanguage,
            name="document_language",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, DocumentLanguage):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return DocumentLanguage(value)


class Study(Base):
    __tablename__ = "studies"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    study_code: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[StudyStatus] = mapped_column(
        StudyStatusType(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    documents: Mapped[list["Document"]] = relationship(
        back_populates="study", cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    study_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[DocumentLifecycleStatus] = mapped_column(
        DocumentLifecycleStatusType(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    study: Mapped["Study"] = relationship(back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    source_file_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ingestion_status: Mapped[IngestionStatus] = mapped_column(
        IngestionStatusType(),
        nullable=False,
    )
    ingestion_summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    document_language: Mapped[DocumentLanguage] = mapped_column(
        DocumentLanguageType(),
        nullable=False,
        server_default="unknown",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"),
        nullable=True,
    )

    document: Mapped["Document"] = relationship(back_populates="versions")
    last_ingestion_run: Mapped["IngestionRun | None"] = relationship(
        foreign_keys=[last_ingestion_run_id],
        post_update=True,
        remote_side="IngestionRun.id",
    )
    ingestion_runs: Mapped[list["IngestionRun"]] = relationship(
        back_populates="doc_version",
        primaryjoin="DocumentVersion.id == IngestionRun.doc_version_id",
        cascade="all, delete-orphan",
        order_by="desc(IngestionRun.started_at)",
    )


