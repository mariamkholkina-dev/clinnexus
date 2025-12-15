from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger


class DiffResult:
    """Результат сравнения версий документа."""

    def __init__(
        self,
        from_version_id: UUID,
        to_version_id: UUID,
        summary: dict[str, Any],
    ) -> None:
        self.from_version_id = from_version_id
        self.to_version_id = to_version_id
        self.summary = summary


class DiffService:
    """Сервис для сравнения версий документов."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def diff_versions(
        self, from_version_id: UUID, to_version_id: UUID
    ) -> DiffResult:
        """
        Сравнивает две версии документа и возвращает различия.

        TODO: Реальная реализация должна:
        - Сравнить anchors двух версий
        - Найти добавленные/удалённые/изменённые секции
        - Вернуть структурированный diff
        """
        logger.info(f"Сравнение версий {from_version_id} -> {to_version_id}")

        # TODO: Реальная логика сравнения
        # Здесь должна быть логика:
        # 1. Получить anchors для обеих версий
        # 2. Сравнить section_path и content
        # 3. Найти изменения
        # 4. Вернуть структурированный diff

        # Заглушка
        summary = {
            "added_sections": [],
            "removed_sections": [],
            "modified_sections": [],
            "anchors_added": 0,
            "anchors_removed": 0,
            "anchors_modified": 0,
        }

        logger.info(f"Сравнение завершено")
        return DiffResult(
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            summary=summary,
        )

