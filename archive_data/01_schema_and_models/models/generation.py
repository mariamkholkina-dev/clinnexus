from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import (
    DocumentType,
    GenerationStatus,
    QCStatus,
)
from app.db.models.studies import DocumentTypeType


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    template_body: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ModelConfig(Base):
    __tablename__ = "model_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    study_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    target_section: Mapped[str] = mapped_column(Text, nullable=False)
    view_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("section_contracts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_config_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("model_configs.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[GenerationStatus] = mapped_column(
        Enum(GenerationStatus, name="generation_status", native_enum=True),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    
    @property
    def section_key(self) -> str:
        """Обратная совместимость: section_key → target_section (deprecated)."""
        import warnings
        warnings.warn(
            "Использование section_key устарело. Используйте target_section.",
            DeprecationWarning,
            stacklevel=2
        )
        return self.target_section
    
    @section_key.setter
    def section_key(self, value: str) -> None:
        """Обратная совместимость: section_key → target_section (deprecated)."""
        import warnings
        warnings.warn(
            "Использование section_key устарело. Используйте target_section.",
            DeprecationWarning,
            stacklevel=2
        )
        self.target_section = value


class GeneratedSection(Base):
    __tablename__ = "generated_sections"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    generation_run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    # artifacts_json хранит утверждения/числа/цитации (anchor_id)
    artifacts_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    qc_status: Mapped[QCStatus] = mapped_column(
        Enum(QCStatus, name="qc_status", native_enum=True),
        nullable=False,
    )
    qc_report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    published_to_document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


