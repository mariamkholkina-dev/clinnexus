from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ingestion import IngestionService, IngestionResult

"""
JobRunner - слой для выполнения фоновых задач.

Подготовка к переходу на Celery/Redis: выделяем интерфейс
для синхронного и асинхронного выполнения задач.
"""


async def run_ingestion_now(
    db: AsyncSession,
    version_id: UUID,
) -> IngestionResult:
    """
    Синхронно выполняет ингестию документа.

    Args:
        db: Сессия базы данных
        version_id: ID версии документа

    Returns:
        IngestionResult с результатами ингестии
    """
    ingestion_service = IngestionService(db)
    return await ingestion_service.ingest(version_id)


async def enqueue_ingestion(version_id: UUID) -> None:
    """
    Ставит задачу ингестии в очередь (TODO: реализовать через Celery/Redis).

    Args:
        version_id: ID версии документа
    """
    # TODO: Реализовать через Celery/Redis
    # Пример:
    # from celery import current_app
    # current_app.send_task('ingest_document', args=[str(version_id)])
    raise NotImplementedError("Асинхронная очередь пока не реализована")

