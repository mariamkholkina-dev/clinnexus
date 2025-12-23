"""
Скрипт для заполнения zone_sets и zone_crosswalk начальными данными.

Заполняет:
- zone_sets: 12 зон для protocol
- zone_crosswalk: минимальные маппинги protocol -> csr
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import settings
from app.db.enums import DocumentType
from app.services.zone_repository import ZoneCrosswalkRepository, ZoneSetRepository


async def seed_zone_sets() -> None:
    """Заполнение zone_sets для protocol (12 зон)."""
    engine = create_async_engine(settings.async_database_url, echo=True)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session_factory() as session:
        repo = ZoneSetRepository(session)

        # 12 зон для protocol из zone_sets.yaml
        protocol_zones = [
            "overview",
            "design",
            "ip",
            "statistics",
            "safety",
            "endpoints",
            "population",
            "procedures",
            "data_management",
            "ethics",
            "admin",
            "appendix",
        ]

        print(f"Добавление {len(protocol_zones)} зон для protocol...")
        await repo.bulk_upsert_zone_sets(
            doc_type=DocumentType.PROTOCOL,
            zone_keys=protocol_zones,
            is_active=True,
        )
        await session.commit()
        print("✓ zone_sets для protocol добавлены")


async def seed_zone_crosswalk() -> None:
    """Заполнение zone_crosswalk минимальными маппингами protocol -> csr."""
    engine = create_async_engine(settings.async_database_url, echo=True)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session_factory() as session:
        repo = ZoneCrosswalkRepository(session)

        # Минимальные crosswalk маппинги согласно требованиям
        crosswalks = [
            {
                "from_doc_type": DocumentType.PROTOCOL,
                "from_zone_key": "statistics",
                "to_doc_type": DocumentType.CSR,
                "to_zone_key": "statistics_results",
                "weight": Decimal("1.0"),
                "notes": "Прямой маппинг статистики протокола в результаты CSR",
            },
            {
                "from_doc_type": DocumentType.PROTOCOL,
                "from_zone_key": "statistics",
                "to_doc_type": DocumentType.CSR,
                "to_zone_key": "tfl",
                "weight": Decimal("0.8"),
                "notes": "Статистика протокола также релевантна для TFL",
            },
            {
                "from_doc_type": DocumentType.PROTOCOL,
                "from_zone_key": "safety",
                "to_doc_type": DocumentType.CSR,
                "to_zone_key": "safety_results",
                "weight": Decimal("1.0"),
                "notes": "Прямой маппинг безопасности протокола в результаты CSR",
            },
            {
                "from_doc_type": DocumentType.PROTOCOL,
                "from_zone_key": "safety",
                "to_doc_type": DocumentType.CSR,
                "to_zone_key": "tfl",
                "weight": Decimal("0.8"),
                "notes": "Безопасность протокола также релевантна для TFL",
            },
            {
                "from_doc_type": DocumentType.PROTOCOL,
                "from_zone_key": "population",
                "to_doc_type": DocumentType.CSR,
                "to_zone_key": "population",
                "weight": Decimal("1.0"),
                "notes": "Прямой маппинг популяции протокола в популяцию CSR",
            },
            {
                "from_doc_type": DocumentType.PROTOCOL,
                "from_zone_key": "population",
                "to_doc_type": DocumentType.CSR,
                "to_zone_key": "disposition",
                "weight": Decimal("0.8"),
                "notes": "Популяция протокола также релевантна для disposition в CSR",
            },
        ]

        print(f"Добавление {len(crosswalks)} crosswalk маппингов...")
        await repo.bulk_upsert_crosswalks(crosswalks)
        await session.commit()
        print("✓ zone_crosswalk маппинги добавлены")


async def main() -> None:
    """Главная функция для запуска всех seed операций."""
    print("Начало заполнения zone_sets и zone_crosswalk...")
    await seed_zone_sets()
    await seed_zone_crosswalk()
    print("✓ Заполнение завершено")


if __name__ == "__main__":
    asyncio.run(main())

