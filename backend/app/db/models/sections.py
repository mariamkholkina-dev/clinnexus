from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ARRAY, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import (
    CitationPolicy,
    DocumentType,
    SectionMapMappedBy,
    SectionMapStatus,
)
from app.db.models.studies import DocumentTypeType


class CitationPolicyType(TypeDecorator):
    """TypeDecorator для правильной конвертации CitationPolicy enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            CitationPolicy,
            name="citation_policy",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, CitationPolicy):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return CitationPolicy(value)


class SectionMapStatusType(TypeDecorator):
    """TypeDecorator для правильной конвертации SectionMapStatus enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            SectionMapStatus,
            name="section_map_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, SectionMapStatus):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return SectionMapStatus(value)


class SectionMapMappedByType(TypeDecorator):
    """TypeDecorator для правильной конвертации SectionMapMappedBy enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            SectionMapMappedBy,
            name="section_map_mapped_by",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, SectionMapMappedBy):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return SectionMapMappedBy(value)


class SectionContract(Base):
    """
    Паспорт / контракт семантической секции (UNIVERSAL).

    - section_key: стабильный семантический ключ (например 'protocol.soa').
      Он НЕ зависит от конкретной структуры документа.
    - doc_type: тип документа, для которого применяется контракт.

    Конкретная привязка к структуре и якорям хранится в `SectionMap` и опирается
    на section_key, а не на section_path.
    """

    __tablename__ = "section_contracts"

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
    section_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    required_facts_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    allowed_sources_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    retrieval_recipe_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    qc_ruleset_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    citation_policy: Mapped[CitationPolicy] = mapped_column(
        CitationPolicyType(),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SectionMap(Base):
    """
    Маппинг семантической секции (section_key) на конкретную версию документа.

    ВАЖНО:
    - section_key — главный идентификатор семантической секции.
    - section_path здесь НЕ используется как ключ и может храниться только
      в notes/metadata (при необходимости), чтобы не связывать семантику
      напрямую с текущей структурой документа.
    """

    __tablename__ = "section_maps"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_key: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_ids: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )  # массив строк anchor_id
    chunk_ids: Mapped[list[uuid.UUID] | None] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)), nullable=True
    )  # массив UUID chunk.id
    confidence: Mapped[float] = mapped_column(nullable=False)
    status: Mapped[SectionMapStatus] = mapped_column(
        SectionMapStatusType(),
        nullable=False,
    )
    mapped_by: Mapped[SectionMapMappedBy] = mapped_column(
        SectionMapMappedByType(),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


