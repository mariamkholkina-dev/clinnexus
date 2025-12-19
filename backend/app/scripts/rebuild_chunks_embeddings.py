"""
Скрипт для пересоздания embeddings для всех существующих chunks в базе данных.

Этот скрипт находит все версии документов со статусом 'ready' или 'needs_review'
и пересоздает для них chunks с embeddings через ChunkingService.

Использование:
    # Показать статистику без изменений (dry-run)
    python -m app.scripts.rebuild_chunks_embeddings --dry-run

    # Пересоздать embeddings для всех версий
    python -m app.scripts.rebuild_chunks_embeddings

    # Обработать только первые 5 версий (для тестирования)
    python -m app.scripts.rebuild_chunks_embeddings --max-versions 5

Примечания:
    - Скрипт использует ChunkingService.rebuild_chunks_for_doc_version(),
      который идемпотентен: удаляет старые chunks и создает новые с embeddings
    - Embeddings создаются через feature hashing (_hash_embedding_v1)
    - Требуется, чтобы типы pgvector были зарегистрированы (см. app/db/session.py)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Настройка event loop для Windows (psycopg требует SelectorEventLoop)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Добавляем корневую директорию в путь для импортов
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.db.models.anchors import Chunk
from app.db.models.studies import DocumentVersion
from app.db.session import async_session_factory
from app.services.chunking import ChunkingService


async def rebuild_embeddings_for_all_versions(
    dry_run: bool = False,
    max_versions: int | None = None,
) -> dict[str, int]:
    """
    Пересоздает embeddings для всех chunks в базе данных.

    Args:
        dry_run: Если True, только показывает статистику без изменений
        max_versions: Максимальное количество версий для обработки (для тестирования)

    Returns:
        Словарь со статистикой: {
            "total_versions": количество версий документов,
            "versions_processed": обработано версий,
            "chunks_created": создано chunks,
            "errors": количество ошибок
        }
    """
    stats = {
        "total_versions": 0,
        "versions_processed": 0,
        "chunks_created": 0,
        "errors": 0,
    }

    async with async_session_factory() as db:
        # Получаем все версии документов, которые прошли ingest
        # (ingestion_status = 'ready' или 'needs_review')
        from app.db.enums import IngestionStatus

        stmt = (
            select(
                DocumentVersion.id,
                DocumentVersion.version_label,
                func.count(Chunk.id).label("chunk_count"),
            )
            .outerjoin(Chunk, Chunk.doc_version_id == DocumentVersion.id)
            .where(
                DocumentVersion.ingestion_status.in_(
                    [IngestionStatus.READY, IngestionStatus.NEEDS_REVIEW]
                )
            )
            .group_by(DocumentVersion.id, DocumentVersion.version_label)
        )

        if max_versions:
            stmt = stmt.limit(max_versions)

        result = await db.execute(stmt)
        versions = result.all()

        stats["total_versions"] = len(versions)

        logger.info(
            f"Найдено {stats['total_versions']} версий документов "
            f"со статусом ready/needs_review"
        )

        if dry_run:
            logger.info("DRY RUN: изменения не будут сохранены")
            for row in versions:
                logger.info(
                    f"  - doc_version_id={row.id}, "
                    f"version_label={row.version_label}, "
                    f"chunks={row.chunk_count}"
                )
            return stats

        # Обрабатываем каждую версию
        chunking_service = ChunkingService(db)

        for idx, row in enumerate(versions, 1):
            doc_version_id = row.id
            version_label = row.version_label
            old_chunk_count = row.chunk_count

            try:
                logger.info(
                    f"[{idx}/{stats['total_versions']}] "
                    f"Обработка doc_version_id={doc_version_id} "
                    f"(version_label={version_label}, текущих chunks: {old_chunk_count})"
                )

                # Пересоздаем chunks с embeddings
                chunks_created = await chunking_service.rebuild_chunks_for_doc_version(
                    doc_version_id
                )

                await db.commit()

                stats["versions_processed"] += 1
                stats["chunks_created"] += chunks_created

                logger.info(
                    f"  ✓ Создано {chunks_created} chunks с embeddings "
                    f"(было {old_chunk_count})"
                )

            except Exception as e:
                stats["errors"] += 1
                logger.error(
                    f"  ✗ Ошибка при обработке doc_version_id={doc_version_id}: {e}",
                    exc_info=True,
                )
                await db.rollback()

    return stats


async def main() -> None:
    """Главная функция скрипта."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Пересоздание embeddings для всех chunks в базе данных"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать статистику без изменений",
    )
    parser.add_argument(
        "--max-versions",
        type=int,
        default=None,
        help="Максимальное количество версий для обработки (для тестирования)",
    )

    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Пересоздание embeddings для chunks")
    logger.info("=" * 60)
    logger.info(f"Database: {settings.db_host}:{settings.db_port}/{settings.db_name}")
    logger.info(f"Dry run: {args.dry_run}")
    if args.max_versions:
        logger.info(f"Max versions: {args.max_versions}")

    try:
        stats = await rebuild_embeddings_for_all_versions(
            dry_run=args.dry_run,
            max_versions=args.max_versions,
        )

        logger.info("=" * 60)
        logger.info("Результаты:")
        logger.info(f"  Всего версий документов: {stats['total_versions']}")
        logger.info(f"  Обработано: {stats['versions_processed']}")
        logger.info(f"  Создано chunks: {stats['chunks_created']}")
        logger.info(f"  Ошибок: {stats['errors']}")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

