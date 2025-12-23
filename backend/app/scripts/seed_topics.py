"""
Сидер Topics из canonical dictionary JSON.

Читает backend/app/data/topics_canonical_dictionary.json и импортирует в БД.
Формат JSON:
{
  "topics": [
    {
      "topic_key": "study_design",
      "title_ru": "Дизайн исследования",
      "title_en": "Study Design",
      "description": "Описание дизайна исследования",
      "profile": {
        "category": "design",
        "keywords": ["design", "methodology"],
        ...
      }
    },
    ...
  ]
}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import logger
from app.db.models.auth import Workspace
from app.services.topic_repository import TopicRepository


async def seed_topics(
    *,
    topics_file: Path,
    workspace_id: UUID | None = None,
    clear_existing: bool = False,
) -> None:
    """Импортирует topics из JSON файла."""
    if not topics_file.exists():
        raise SystemExit(f"Файл не найден: {topics_file}")

    with open(topics_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    topics_data = data.get("topics", [])
    if not topics_data:
        raise SystemExit("В JSON отсутствует поле 'topics' или оно пустое")

    engine = create_async_engine(settings.async_database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Определяем workspace_id
        if workspace_id is None:
            # Если не указан, берем первый доступный workspace
            result = await session.execute(select(Workspace))
            workspace = result.scalar_one_or_none()
            if not workspace:
                raise SystemExit(
                    "Workspace не найден. Создайте workspace или укажите workspace_id через --workspace-id"
                )
            workspace_id = workspace.id
            logger.info(f"Используется workspace: {workspace_id}")
        else:
            # Проверяем существование workspace
            workspace = await session.get(Workspace, workspace_id)
            if not workspace:
                raise SystemExit(f"Workspace с id {workspace_id} не найден")

        # Очистка существующих topics (опционально)
        if clear_existing:
            logger.info(f"Очистка существующих topics для workspace {workspace_id}...")
            from app.db.models.topics import Topic

            result = await session.execute(
                select(Topic).where(Topic.workspace_id == workspace_id)
            )
            topics_to_delete = result.scalars().all()
            for topic in topics_to_delete:
                await session.delete(topic)
            await session.commit()
            logger.info("Очистка завершена")

        # Импорт topics
        logger.info(f"Импорт {len(topics_data)} topics...")
        repo = TopicRepository(session)

        for topic_data in topics_data:
            topic_key = topic_data.get("topic_key")
            if not topic_key:
                logger.warning("Пропущен topic без topic_key")
                continue

            title_ru = topic_data.get("title_ru")
            title_en = topic_data.get("title_en")
            description = topic_data.get("description")
            profile = topic_data.get("profile", {})

            # Используем upsert для идемпотентности
            try:
                await repo.upsert_topic(
                    workspace_id=workspace_id,
                    topic_key=topic_key,
                    title_ru=title_ru,
                    title_en=title_en,
                    description=description,
                    topic_profile_json=profile,
                    is_active=True,
                )
                logger.info(f"Upserted topic: {topic_key}")
            except Exception as e:
                logger.error(f"Ошибка при импорте topic {topic_key}: {e}")
                await session.rollback()
                raise

        logger.info(f"Импорт topics завершен успешно для workspace {workspace_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт Topics из canonical dictionary JSON")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(__file__).parent.parent / "data" / "topics_canonical_dictionary.json",
        help="Путь к JSON файлу с topics",
    )
    parser.add_argument(
        "--workspace-id",
        type=UUID,
        help="UUID workspace для импорта topics (если не указан, используется первый доступный)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Очистить существующие topics перед импортом",
    )
    args = parser.parse_args()

    # Исправление для Windows: используем SelectorEventLoop вместо ProactorEventLoop
    # для совместимости с psycopg (асинхронный драйвер PostgreSQL)
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                seed_topics(
                    topics_file=args.file,
                    workspace_id=args.workspace_id,
                    clear_existing=args.clear,
                )
            )
        finally:
            loop.close()
    else:
        asyncio.run(
            seed_topics(
                topics_file=args.file,
                workspace_id=args.workspace_id,
                clear_existing=args.clear,
            )
        )


if __name__ == "__main__":
    main()

