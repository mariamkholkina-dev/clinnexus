"""CLI скрипт для маппинга топиков на кластеры заголовков документа."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import settings
from app.core.logging import logger
from app.services.heading_clustering import HeadingClusteringService
from app.services.topic_mapping import TopicMappingService


async def map_topics_for_doc_version(
    db: AsyncSession,
    doc_version_id: UUID,
    mode: str = "auto",
    apply: bool = True,
) -> None:
    """
    Выполняет маппинг топиков для версии документа.

    Args:
        db: Сессия базы данных
        doc_version_id: ID версии документа
        mode: Режим маппинга ("auto" или "assist")
        apply: Сохранять ли результаты в БД
    """
    logger.info(f"Начало маппинга топиков для doc_version_id={doc_version_id}")

    # Маппинг блоков на топики (кластеризация опциональна и управляется через настройки)
    logger.info("Шаг 1: Маппинг блоков на топики...")
    mapping_service = TopicMappingService(db)
    assignments, metrics = await mapping_service.map_topics_for_doc_version(
        doc_version_id=doc_version_id,
        mode=mode,
        apply=apply,
        confidence_threshold=0.65,
    )

    # Строим topic_evidence
    if apply:
        logger.info("Шаг 2: Построение topic_evidence...")
        from app.services.topic_evidence_builder import TopicEvidenceBuilder
        evidence_builder = TopicEvidenceBuilder(db)
        evidence_count = await evidence_builder.build_evidence_for_doc_version(doc_version_id)
        logger.info(f"Создано {evidence_count} записей topic_evidence")

    # Выводим результаты
    logger.info("\n" + "=" * 60)
    logger.info("РЕЗУЛЬТАТЫ МАППИНГА")
    logger.info("=" * 60)
    logger.info(f"Создано назначений: {len(assignments)}")
    logger.info(f"\nМетрики качества:")
    logger.info(f"  Blocks total: {metrics.blocks_total}")
    logger.info(f"  Blocks mapped: {metrics.blocks_mapped}")
    logger.info(f"  Mapped rate: {metrics.mapped_rate:.2%} (доля блоков с confidence >= 0.65)")
    logger.info(f"  Low confidence rate: {metrics.low_confidence_rate:.2%} (доля блоков с низким confidence)")
    logger.info(f"  Clustering enabled: {metrics.clustering_enabled}")

    if assignments:
        logger.info(f"\nТоп-10 назначений:")
        for i, assignment in enumerate(assignments[:10], 1):
            debug_info = assignment.debug_json or {}
            top_candidates = debug_info.get("top3_candidates", [])
            best = top_candidates[0] if top_candidates else {}
            
            logger.info(
                f"  {i}. Block {assignment.heading_block_id[:20]}... -> {assignment.topic_key} "
                f"(confidence={assignment.confidence:.3f}, "
                f"heading_match={best.get('heading_match_score', 0):.2f}, "
                f"keywords={best.get('text_keywords_match_score', 0):.2f})"
            )

    if metrics.unmapped_top_headings:
        logger.info(f"\nТоп-5 unmapped заголовков:")
        for heading in metrics.unmapped_top_headings[:5]:
            logger.info(f"  - {heading[:60]}...")

    logger.info("=" * 60)


async def main() -> None:
    """Главная функция CLI."""
    parser = argparse.ArgumentParser(
        description="Маппинг топиков на кластеры заголовков документа"
    )
    parser.add_argument(
        "--doc-version-id",
        type=str,
        required=True,
        help="UUID версии документа",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="auto",
        choices=["auto", "assist"],
        help="Режим маппинга (по умолчанию: auto)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Сохранять результаты в БД (по умолчанию: нет)",
    )

    args = parser.parse_args()

    try:
        doc_version_id = UUID(args.doc_version_id)
    except ValueError:
        logger.error(f"Неверный UUID: {args.doc_version_id}")
        sys.exit(1)

    # Создаем engine и session
    engine = create_async_engine(settings.async_database_url, echo=False)
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session_maker() as db:
        try:
            await map_topics_for_doc_version(
                db=db,
                doc_version_id=doc_version_id,
                mode=args.mode,
                apply=args.apply,
            )
        except Exception as e:
            logger.error(f"Ошибка при выполнении маппинга: {e}", exc_info=True)
            sys.exit(1)
        finally:
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

