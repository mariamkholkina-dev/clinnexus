from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency для получения async-сессии БД в FastAPI endpoints.
    """
    async with async_session_factory() as session:
        yield session

