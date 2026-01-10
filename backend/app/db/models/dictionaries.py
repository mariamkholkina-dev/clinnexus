from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base, TimestampMixin, UUIDMixin


class TerminologyDictionary(Base, UUIDMixin, TimestampMixin):
    """Словарь терминологии для исследований."""

    __tablename__ = "terminology_dictionaries"
    __table_args__ = (
        Index("ix_terminology_dictionaries_study_id", "study_id"),
        Index("ix_terminology_dictionaries_term_category", "term_category"),
        UniqueConstraint(
            "study_id",
            "term_category",
            "preferred_term",
            name="uq_terminology_study_category_term",
        ),
    )

    study_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="CASCADE"),
        nullable=False,
    )
    term_category: Mapped[str] = mapped_column(String(128), nullable=False)
    preferred_term: Mapped[str] = mapped_column(Text, nullable=False)
    variations: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

