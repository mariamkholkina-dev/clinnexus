"""
Репозитории для работы с zone_sets и zone_crosswalk.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import logger
from app.db.enums import DocumentType
from app.db.models.zones import ZoneCrosswalk, ZoneSet


class ZoneSetRepository:
    """Репозиторий для работы с наборами зон."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_active_zone_keys(self, doc_type: DocumentType) -> list[str]:
        """
        Получает список активных zone_key для данного doc_type.

        Args:
            doc_type: Тип документа

        Returns:
            Список zone_key, отсортированный по created_at
        """
        stmt = (
            select(ZoneSet.zone_key)
            .where(ZoneSet.doc_type == doc_type)
            .where(ZoneSet.is_active == True)  # noqa: E712
            .order_by(ZoneSet.created_at)
        )
        result = await self.db.execute(stmt)
        return [row[0] for row in result.all()]

    async def upsert_zone_set(
        self,
        doc_type: DocumentType,
        zone_key: str,
        is_active: bool = True,
    ) -> ZoneSet:
        """
        Создает или обновляет запись zone_set.

        Использует ON CONFLICT для идемпотентности.
        """
        stmt = pg_insert(ZoneSet).values(
            doc_type=doc_type,
            zone_key=zone_key,
            is_active=is_active,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_zone_sets_doc_type_zone_key",
            set_=dict(
                is_active=stmt.excluded.is_active,
            ),
        ).returning(ZoneSet)

        result = await self.db.execute(stmt)
        await self.db.commit()
        zone_set = result.scalar_one()
        await self.db.refresh(zone_set)
        logger.info(
            f"Upserted zone set: doc_type={doc_type.value}, zone_key={zone_key}, is_active={is_active}"
        )
        return zone_set

    async def bulk_upsert_zone_sets(
        self,
        doc_type: DocumentType,
        zone_keys: list[str],
        is_active: bool = True,
    ) -> list[ZoneSet]:
        """
        Массовое создание или обновление zone_sets.

        Args:
            doc_type: Тип документа
            zone_keys: Список zone_key для добавления
            is_active: Флаг активности

        Returns:
            Список созданных/обновленных ZoneSet
        """
        zone_sets = []
        for zone_key in zone_keys:
            zone_set = await self.upsert_zone_set(doc_type, zone_key, is_active)
            zone_sets.append(zone_set)
        return zone_sets


class ZoneCrosswalkRepository:
    """Репозиторий для работы с маппингом зон между типами документов."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_targets(
        self,
        from_doc_type: DocumentType,
        from_zone_key: str,
        to_doc_type: DocumentType,
    ) -> list[ZoneCrosswalk]:
        """
        Получает список целевых зон для маппинга, отсортированный по weight (desc).

        Args:
            from_doc_type: Исходный тип документа
            from_zone_key: Исходная зона
            to_doc_type: Целевой тип документа

        Returns:
            Список ZoneCrosswalk, отсортированный по weight (desc)
        """
        stmt = (
            select(ZoneCrosswalk)
            .where(ZoneCrosswalk.from_doc_type == from_doc_type)
            .where(ZoneCrosswalk.from_zone_key == from_zone_key)
            .where(ZoneCrosswalk.to_doc_type == to_doc_type)
            .where(ZoneCrosswalk.is_active == True)  # noqa: E712
            .order_by(ZoneCrosswalk.weight.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def upsert_crosswalk(
        self,
        from_doc_type: DocumentType,
        from_zone_key: str,
        to_doc_type: DocumentType,
        to_zone_key: str,
        weight: Decimal | float,
        notes: str | None = None,
        is_active: bool = True,
    ) -> ZoneCrosswalk:
        """
        Создает или обновляет запись zone_crosswalk.

        Использует ON CONFLICT для идемпотентности.
        """
        # Конвертируем float в Decimal, если нужно
        if isinstance(weight, float):
            weight = Decimal(str(weight))

        stmt = pg_insert(ZoneCrosswalk).values(
            from_doc_type=from_doc_type,
            from_zone_key=from_zone_key,
            to_doc_type=to_doc_type,
            to_zone_key=to_zone_key,
            weight=weight,
            notes=notes,
            is_active=is_active,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_zone_crosswalk_from_to",
            set_=dict(
                weight=stmt.excluded.weight,
                notes=stmt.excluded.notes,
                is_active=stmt.excluded.is_active,
            ),
        ).returning(ZoneCrosswalk)

        result = await self.db.execute(stmt)
        await self.db.commit()
        crosswalk = result.scalar_one()
        await self.db.refresh(crosswalk)
        logger.info(
            f"Upserted zone crosswalk: {from_doc_type.value}.{from_zone_key} -> "
            f"{to_doc_type.value}.{to_zone_key}, weight={weight}"
        )
        return crosswalk

    async def bulk_upsert_crosswalks(
        self,
        crosswalks: list[dict[str, Any]],
    ) -> list[ZoneCrosswalk]:
        """
        Массовое создание или обновление zone_crosswalk.

        Args:
            crosswalks: Список словарей с ключами:
                - from_doc_type: DocumentType
                - from_zone_key: str
                - to_doc_type: DocumentType
                - to_zone_key: str
                - weight: Decimal | float
                - notes: str | None (опционально)
                - is_active: bool (опционально, по умолчанию True)

        Returns:
            Список созданных/обновленных ZoneCrosswalk
        """
        result_crosswalks = []
        for crosswalk_data in crosswalks:
            crosswalk = await self.upsert_crosswalk(
                from_doc_type=crosswalk_data["from_doc_type"],
                from_zone_key=crosswalk_data["from_zone_key"],
                to_doc_type=crosswalk_data["to_doc_type"],
                to_zone_key=crosswalk_data["to_zone_key"],
                weight=crosswalk_data["weight"],
                notes=crosswalk_data.get("notes"),
                is_active=crosswalk_data.get("is_active", True),
            )
            result_crosswalks.append(crosswalk)
        return result_crosswalks

