from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import UserDefinedType

from app.db.base import Base
from app.db.enums import AnchorContentType


class Vector1536(UserDefinedType):
    """Тип столбца pgvector(1536).

    В миграции создаётся расширение `vector` и используется тот же col spec.
    """

    def get_col_spec(self, **kw: Any) -> str:  # type: ignore[override]
        return "vector(1536)"


class Anchor(Base):
    __tablename__ = "anchors"

    # ВАЖНО: anchor_id — глобальный строковый идентификатор якоря.
    # Формат:
    # {doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash(text_norm)}
    #
    # - doc_version_id: UUID документа-версии
    # - section_path: структурный путь внутри конкретного документа (например "1.2.3").
    #   ВАЖНО: section_path отражает ТЕКУЩУЮ структуру документа и может меняться
    #   при обновлении разметки.
    # - content_type: см. AnchorContentType
    # - ordinal: порядковый номер якоря в данной секции/типе
    # - hash(text_norm): стабильный хеш нормализованного текста
    #
    # Семантическая идентичность секции НЕ зависит от section_path; для этого
    # используются section_key и паспорта в таблицах section_contracts/section_maps.

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    anchor_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    section_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[AnchorContentType] = mapped_column(
        Enum(AnchorContentType, name="anchor_content_type", native_enum=True),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    text_raw: Mapped[str] = mapped_column(Text, nullable=False)
    text_norm: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    location_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_version_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    section_path: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector1536, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


