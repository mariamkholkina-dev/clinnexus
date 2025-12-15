from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.enums import EvidenceRole, FactStatus


class Fact(Base):
    __tablename__ = "facts"

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
    status: Mapped[FactStatus] = mapped_column(
        Enum(FactStatus, name="fact_status", native_enum=True),
        nullable=False,
    )
    created_from_doc_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
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
        Enum(EvidenceRole, name="evidence_role", native_enum=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


