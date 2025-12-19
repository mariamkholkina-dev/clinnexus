from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from pgvector.psycopg import register_vector_async

"""
Инициализация async-движка и фабрики сессий SQLAlchemy 2.0.
Используется во всём приложении (FastAPI-зависимость get_db).
"""

engine: AsyncEngine = create_async_engine(
    settings.async_database_url,
    echo=False,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def register_pgvector_types(dbapi_connection, connection_record):
    """Регистрирует типы pgvector для psycopg 3."""
    # Для async connections используем run_async
    if hasattr(dbapi_connection, "run_async"):
        dbapi_connection.run_async(register_vector_async)
    else:
        # Fallback для sync connections (если понадобится)
        from pgvector.psycopg import register_vector
        register_vector(dbapi_connection)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Зависимость FastAPI для получения async-сессии БД."""

    async with async_session_factory() as session:
        yield session


