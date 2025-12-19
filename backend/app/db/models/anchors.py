from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import ARRAY, ENUM as PG_ENUM, JSONB, UUID as PG_UUID
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

    def bind_processor(self, dialect):  # type: ignore[override]
        """
        Для psycopg 3 с pgvector можно передавать список напрямую.
        pgvector автоматически конвертирует его в правильный формат.

        Важно: `chunks.embedding` = vector(1536) NOT NULL, поэтому здесь также
        валидируем длину.
        """

        def process(value):
            if value is None:
                return None
            if not isinstance(value, (list, tuple)):
                raise TypeError(f"Vector1536 ожидает list[float], получено: {type(value)!r}")
            if len(value) != 1536:
                raise ValueError(f"Vector1536 ожидает длину 1536, получено: {len(value)}")

            # Для psycopg 3 с зарегистрированными типами pgvector
            # можно передавать список напрямую - он будет автоматически конвертирован
            # Проверяем, что все элементы - float
            try:
                return [float(x) for x in value]
            except (ValueError, TypeError) as e:
                raise TypeError(f"Vector1536: элемент не float: {e}") from e

        return process

    def result_processor(self, dialect, coltype):  # type: ignore[override]
        # Для MVP достаточно вернуть строку/сырое значение; чтение embedding не требуется.
        def process(value):
            return value

        return process


class AnchorContentTypeType(TypeDecorator):
    """TypeDecorator для правильной конвертации AnchorContentType enum в значение строки."""
    
    impl = PG_ENUM
    cache_ok = True
    
    def __init__(self):
        super().__init__(
            AnchorContentType,
            name="anchor_content_type",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        )
    
    def process_bind_param(self, value, dialect):
        """Конвертируем enum в значение строки при сохранении."""
        if value is None:
            return None
        if isinstance(value, AnchorContentType):
            return value.value
        return value
    
    def process_result_value(self, value, dialect):
        """Конвертируем значение строки обратно в enum при чтении."""
        if value is None:
            return None
        return AnchorContentType(value)


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
        AnchorContentTypeType(),
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


