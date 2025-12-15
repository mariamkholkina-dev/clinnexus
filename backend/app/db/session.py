from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

"""
Инициализация async-движка и фабрики сессий SQLAlchemy 2.0.
Используется во всём приложении (FastAPI-зависимость get_db).
"""

engine: AsyncEngine = create_async_engine(
    settings.async_database_url,
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Зависимость FastAPI для получения async-сессии БД."""

    async with async_session_factory() as session:
        yield session


