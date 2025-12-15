from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import (
    DocumentLifecycleStatus,
    DocumentType,
    IngestionStatus,
    StudyStatus,
)


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
        Enum(StudyStatus, name="study_status", native_enum=True),
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
        Enum(DocumentType, name="document_type", native_enum=True),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[DocumentLifecycleStatus] = mapped_column(
        Enum(DocumentLifecycleStatus, name="document_lifecycle_status", native_enum=True),
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
    source_file_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    ingestion_status: Mapped[IngestionStatus] = mapped_column(
        Enum(IngestionStatus, name="ingestion_status", native_enum=True),
        nullable=False,
    )
    ingestion_summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="versions")


