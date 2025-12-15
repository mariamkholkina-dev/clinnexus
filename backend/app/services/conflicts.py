from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import logger
from app.db.models.conflicts import Conflict
from app.schemas.conflicts import ConflictOut


class ConflictService:
    """Сервис для обнаружения конфликтов в исследованиях."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def detect_structured(self, study_id: UUID) -> list[ConflictOut]:
        """
        Обнаруживает структурированные конфликты в исследовании.

        TODO: Реальная реализация должна:
        - Сравнить факты из разных документов
        - Найти противоречия (числовые, текстовые)
        - Создать Conflict записи
        """
        logger.info(f"Обнаружение конфликтов для study {study_id}")

        # TODO: Реальная логика обнаружения конфликтов
        # Здесь должна быть логика:
        # 1. Получить все факты для study
        # 2. Сравнить факты с одинаковым fact_key
        # 3. Найти противоречия
        # 4. Создать Conflict записи

        # Заглушка: возвращаем существующие конфликты
        stmt = select(Conflict).where(Conflict.study_id == study_id)
        result = await self.db.execute(stmt)
        conflicts = result.scalars().all()

        logger.info(f"Найдено {len(conflicts)} конфликтов")
        return [ConflictOut.model_validate(c) for c in conflicts]

