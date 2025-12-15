from __future__ import annotations

"""
Базовый declarative-слой для SQLAlchemy 2.0.

Все модели в проекте должны наследоваться от `Base`, использовать UUID в качестве
первичного ключа и timezone-aware временные метки.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.sql import func


convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


metadata = MetaData(naming_convention=convention)


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей."""

    metadata = metadata

    @declared_attr.directive
    def __tablename__(cls) -> str:  # type: ignore[override]
        return cls.__name__.lower()


class UUIDMixin:
    """Миксин с UUID первичным ключом."""

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """Миксин со стандартными временными метками."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


JSONType = dict[str, Any]



