"""ORM модели для zone_sets и zone_crosswalk."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Numeric, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import DocumentType
from app.db.models.studies import DocumentTypeType


class ZoneSet(Base):
    """Набор зон для типа документа."""

    __tablename__ = "zone_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    zone_key: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ZoneCrosswalk(Base):
    """Маппинг между зонами разных типов документов с весами."""

    __tablename__ = "zone_crosswalk"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    from_doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    from_zone_key: Mapped[str] = mapped_column(Text, nullable=False)
    to_doc_type: Mapped[DocumentType] = mapped_column(
        DocumentTypeType(),
        nullable=False,
    )
    to_zone_key: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

