"""
Скрипт для обновления существующих SectionContracts на формат v2 с поддержкой RU/EN.

Обновляет retrieval_recipe_json для протокольных секций:
- Конвертирует v1 формат в v2 с lang.ru/lang.en
- Добавляет RU keywords/regex для основных секций
- Не обновляет контракты, которые уже имеют version>=2
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Настройка event loop для Windows (psycopg требует SelectorEventLoop)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import settings
from app.db.models.sections import TargetSectionContract
from app.db.enums import DocumentType


# Определение RU+EN keywords для основных протокольных секций
PROTOCOL_CONTRACTS_MULTILANG = {
    "protocol.synopsis": {
        "lang": {
            "ru": {
                "must": ["синопсис", "краткое содержание", "резюме"],
                "should": ["обзор", "краткий обзор"],
                "not": ["содержание", "оглавление"],
            },
            "en": {
                "must": ["synopsis", "summary"],
                "should": ["overview", "brief"],
                "not": ["table of contents", "contents"],
            },
        },
        "regex": {
            "heading": {
                "ru": ["^(\\d+\\.)?\\s*(Синопсис|Краткое содержание|Резюме)\\b"],
                "en": ["^(\\d+\\.)?\\s*(Synopsis|Summary)\\b"],
            },
        },
    },
    "protocol.objectives": {
        "lang": {
            "ru": {
                "must": ["цели", "цели исследования", "задачи исследования"],
                "should": ["конечные точки", "endpoint", "эндпоинт"],
                "not": ["содержание", "оглавление"],
            },
            "en": {
                "must": ["objectives", "study objectives"],
                "should": ["endpoint", "rationale"],
                "not": ["table of contents", "contents"],
            },
        },
        "regex": {
            "heading": {
                "ru": ["^(\\d+\\.)?\\s*(Цели|Цели исследования|Задачи исследования)\\b"],
                "en": ["^(\\d+\\.)?\\s*(Objectives|Study Objectives)\\b"],
            },
        },
    },
    "protocol.study_design": {
        "lang": {
            "ru": {
                "must": ["дизайн", "дизайн исследования", "схема исследования"],
                "should": ["методология", "методы"],
                "not": [],
            },
            "en": {
                "must": ["study design", "design"],
                "should": ["methodology", "methods"],
                "not": [],
            },
        },
        "regex": {
            "heading": {
                "ru": ["^(\\d+\\.)?\\s*(Дизайн|Дизайн исследования|Схема исследования)\\b"],
                "en": ["^(\\d+\\.)?\\s*(Study Design|Design)\\b"],
            },
        },
    },
    "protocol.soa": {
        "lang": {
            "ru": {
                "must": ["график", "график визитов", "расписание", "таблица визитов"],
                "should": ["визиты", "процедуры", "таблица"],
                "not": [],
            },
            "en": {
                "must": ["schedule", "activities", "soa"],
                "should": ["visits", "procedures"],
                "not": [],
            },
        },
        "regex": {
            "heading": {
                "ru": ["^(\\d+\\.)?\\s*(График|График визитов|Расписание|Таблица визитов)\\b"],
                "en": ["^(\\d+\\.)?\\s*(Schedule of Activities|SoA|Visits)\\b"],
            },
        },
    },
    "protocol.eligibility.inclusion": {
        "lang": {
            "ru": {
                "must": ["критерии включения", "включение"],
                "should": ["отбор", "критерии"],
                "not": ["исключение", "исключения"],
            },
            "en": {
                "must": ["inclusion", "inclusion criteria"],
                "should": ["eligibility", "criteria"],
                "not": ["exclusion"],
            },
        },
        "regex": {
            "heading": {
                "ru": ["^(\\d+\\.)?\\s*(Критерии включения|Включение)\\b"],
                "en": ["^(\\d+\\.)?\\s*(Inclusion Criteria|Inclusion)\\b"],
            },
        },
    },
    "protocol.eligibility.exclusion": {
        "lang": {
            "ru": {
                "must": ["критерии исключения", "исключение"],
                "should": ["отбор", "критерии"],
                "not": ["включение", "включения"],
            },
            "en": {
                "must": ["exclusion", "exclusion criteria"],
                "should": ["eligibility", "criteria"],
                "not": ["inclusion"],
            },
        },
        "regex": {
            "heading": {
                "ru": ["^(\\d+\\.)?\\s*(Критерии исключения|Исключение)\\b"],
                "en": ["^(\\d+\\.)?\\s*(Exclusion Criteria|Exclusion)\\b"],
            },
        },
    },
    "protocol.treatments.dosing": {
        "lang": {
            "ru": {
                "must": ["лечение", "дозировка", "доза"],
                "should": ["препарат", "лекарство", "терапия"],
                "not": [],
            },
            "en": {
                "must": ["treatment", "dosing", "dose"],
                "should": ["drug", "medication", "therapy"],
                "not": [],
            },
        },
        "regex": {
            "heading": {
                "ru": ["^(\\d+\\.)?\\s*(Лечение|Дозировка|Доза)\\b"],
                "en": ["^(\\d+\\.)?\\s*(Treatment|Dosing|Dose)\\b"],
            },
        },
    },
    "protocol.safety.ae_reporting": {
        "lang": {
            "ru": {
                "must": ["нежелательные явления", "безопасность", "сообщение о нежелательных явлениях"],
                "should": ["сообщение", "события", "безопасность"],
                "not": [],
            },
            "en": {
                "must": ["adverse event", "ae reporting", "safety"],
                "should": ["reporting", "events", "safety"],
                "not": [],
            },
        },
        "regex": {
            "heading": {
                "ru": ["^(\\d+\\.)?\\s*(Нежелательные явления|Безопасность|Сообщение о НЯ)\\b"],
                "en": ["^(\\d+\\.)?\\s*(Adverse Event|AE Reporting|Safety)\\b"],
            },
        },
    },
}


async def update_contracts_multilang() -> None:
    """Обновляет существующие SectionContracts на формат v2 с RU+EN."""
    engine = create_async_engine(settings.async_database_url, echo=True)
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session_factory() as session:
        # Находим активные контракты для протоколов
        stmt = select(TargetSectionContract).where(
            TargetSectionContract.doc_type == DocumentType.PROTOCOL,
            TargetSectionContract.is_active == True,
        )
        result = await session.execute(stmt)
        contracts = result.scalars().all()

        updated_count = 0
        skipped_count = 0

        for contract in contracts:
            # Пропускаем контракты, которые уже имеют version>=2
            recipe = contract.retrieval_recipe_json
            if recipe and recipe.get("version", 1) >= 2:
                print(f"Пропуск {contract.section_key}: уже версия {recipe.get('version')}")
                skipped_count += 1
                continue

            # Проверяем, есть ли мультиязычные данные для этой секции
            if contract.section_key not in PROTOCOL_CONTRACTS_MULTILANG:
                print(f"Пропуск {contract.section_key}: нет мультиязычных данных")
                skipped_count += 1
                continue

            multilang_data = PROTOCOL_CONTRACTS_MULTILANG[contract.section_key]

            # Конвертируем v1 в v2 формат
            # Сохраняем остальные поля из recipe (scope, capture, etc.)
            new_recipe = {
                "version": 2,
                "lang": multilang_data["lang"],
                "regex": multilang_data["regex"],
            }

            # Сохраняем scope, если был
            if "scope" in recipe:
                new_recipe["scope"] = recipe["scope"]

            # Сохраняем capture, если был
            if "capture" in recipe:
                new_recipe["capture"] = recipe["capture"]
            else:
                # Стандартный capture по умолчанию
                new_recipe["capture"] = {
                    "strategy": "heading_block",
                    "max_depth": 3,
                    "stop_at_same_or_higher_level": True,
                }

            # Обновляем retrieval_recipe_json
            contract.retrieval_recipe_json = new_recipe
            contract.version = 2  # Обновляем version контракта

            updated_count += 1
            print(f"Обновлён {contract.section_key}: добавлены RU+EN keywords/regex")

        await session.commit()

        print(f"\n✅ Обновление завершено!")
        print(f"   Обновлено: {updated_count}")
        print(f"   Пропущено: {skipped_count}")


if __name__ == "__main__":
    asyncio.run(update_contracts_multilang())

