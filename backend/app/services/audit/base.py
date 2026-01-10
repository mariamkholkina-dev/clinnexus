"""Базовый класс для всех аудиторов."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.audit import AuditIssue


class BaseAuditor(ABC):
    """Абстрактный базовый класс для всех аудиторов.

    Все аудиторы должны наследоваться от этого класса и реализовать метод run().
    Внутридокументные аудиторы используют run(doc_version_id).
    Кросс-документные аудиторы переопределяют run() с другой сигнатурой.
    """

    def __init__(self, db: AsyncSession, study_id: UUID) -> None:
        """Инициализация аудитора.

        Args:
            db: Асинхронная сессия БД
            study_id: ID исследования
        """
        self.db = db
        self.study_id = study_id

    @abstractmethod
    async def run(self, doc_version_id: UUID) -> list[AuditIssue]:
        """Запускает проверку и возвращает список найденных проблем.

        Для внутридокументных аудиторов:
            Args:
                doc_version_id: ID версии документа для проверки

        Для кросс-документных аудиторов метод может быть переопределен
        с другой сигнатурой (например, run(primary_doc_version_id, secondary_doc_version_id)).

        Returns:
            Список найденных проблем (AuditIssue)
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """Имя аудитора для логирования."""
        raise NotImplementedError

