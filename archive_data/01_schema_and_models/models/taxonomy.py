"""Модели для Section Taxonomy (иерархия и связи секций)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.models.studies import DocumentTypeType
from app.db.enums import DocumentType


class SectionTaxonomyNode(Base):
    """
    Узел иерархии секций в taxonomy.
    
    Хранит иерархию секций (parent->child) для каждого doc_type.
    """

    __tablename__ = "section_taxonomy_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    target_section: Mapped[str] = mapped_column(Text, nullable=False)
    title_ru: Mapped[str] = mapped_column(Text, nullable=False)
    parent_target_section: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_narrow: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    expected_content: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        onupdate=func.now(),
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
    
    @property
    def parent_section_key(self) -> str | None:
        """Обратная совместимость: parent_section_key → parent_target_section (deprecated)."""
        import warnings
        warnings.warn(
            "Использование parent_section_key устарело. Используйте parent_target_section.",
            DeprecationWarning,
            stacklevel=2
        )
        return self.parent_target_section
    
    @parent_section_key.setter
    def parent_section_key(self, value: str | None) -> None:
        """Обратная совместимость: parent_section_key → parent_target_section (deprecated)."""
        import warnings
        warnings.warn(
            "Использование parent_section_key устарело. Используйте parent_target_section.",
            DeprecationWarning,
            stacklevel=2
        )
        self.parent_target_section = value

    __table_args__ = (
        UniqueConstraint("doc_type", "target_section", name="uq_section_taxonomy_nodes_doc_type_section_key"),
        Index("ix_section_taxonomy_nodes_parent", "doc_type", "parent_target_section"),
    )


class SectionTaxonomyAlias(Base):
    """
    Алиас секции (alias -> canonical).
    
    Например: protocol.soa -> protocol.schedule_of_activities
    """

    __tablename__ = "section_taxonomy_aliases"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    alias_key: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_key: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("doc_type", "alias_key", name="uq_section_taxonomy_aliases_doc_type_alias_key"),
        Index("ix_section_taxonomy_aliases_canonical", "doc_type", "canonical_key"),
    )


class SectionTaxonomyRelated(Base):
    """
    Связанные секции (двунаправленный граф конфликтов).
    
    Хранит пары секций, которые часто смешиваются.
    Например: endpoints <-> efficacy_assessment
    """

    __tablename__ = "section_taxonomy_related"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    a_target_section: Mapped[str] = mapped_column(Text, nullable=False)
    b_target_section: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    
    @property
    def a_section_key(self) -> str:
        """Обратная совместимость: a_section_key → a_target_section (deprecated)."""
        import warnings
        warnings.warn(
            "Использование a_section_key устарело. Используйте a_target_section.",
            DeprecationWarning,
            stacklevel=2
        )
        return self.a_target_section
    
    @a_section_key.setter
    def a_section_key(self, value: str) -> None:
        """Обратная совместимость: a_section_key → a_target_section (deprecated)."""
        import warnings
        warnings.warn(
            "Использование a_section_key устарело. Используйте a_target_section.",
            DeprecationWarning,
            stacklevel=2
        )
        self.a_target_section = value
    
    @property
    def b_section_key(self) -> str:
        """Обратная совместимость: b_section_key → b_target_section (deprecated)."""
        import warnings
        warnings.warn(
            "Использование b_section_key устарело. Используйте b_target_section.",
            DeprecationWarning,
            stacklevel=2
        )
        return self.b_target_section
    
    @b_section_key.setter
    def b_section_key(self, value: str) -> None:
        """Обратная совместимость: b_section_key → b_target_section (deprecated)."""
        import warnings
        warnings.warn(
            "Использование b_section_key устарело. Используйте b_target_section.",
            DeprecationWarning,
            stacklevel=2
        )
        self.b_target_section = value

    __table_args__ = (
        # Гарантируем, что a_target_section < b_target_section (нормализованный порядок)
        UniqueConstraint(
            "doc_type", "a_target_section", "b_target_section",
            name="uq_section_taxonomy_related_doc_type_ab"
        ),
        Index("ix_section_taxonomy_related_a", "doc_type", "a_target_section"),
        Index("ix_section_taxonomy_related_b", "doc_type", "b_target_section"),
    )

