from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import IngestionStatus
from app.db.models.anchors import Anchor, Chunk
from app.db.models.audit import AuditLog
from app.db.models.studies import DocumentVersion


class IngestionResult:
    """Результат ингестии документа."""

    def __init__(
        self,
        doc_version_id: UUID,
        status: IngestionStatus,
        anchors_count: int = 0,
        chunks_count: int = 0,
        summary: dict[str, Any] | None = None,
    ) -> None:
        self.doc_version_id = doc_version_id
        self.status = status
        self.anchors_count = anchors_count
        self.chunks_count = chunks_count
        self.summary = summary or {}


class IngestionService:
    """Сервис для ингестии документов (извлечение структуры и создание anchors/chunks)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ingest(self, doc_version_id: UUID) -> IngestionResult:
        """
        Ингестия документа: извлечение структуры, создание anchors и chunks.

        TODO: Реальная реализация должна:
        - Загрузить файл по source_file_uri
        - Использовать OCR/PDF парсер для извлечения структуры
        - Создать anchors для каждого элемента (параграф, таблица, заголовок и т.д.)
        - Создать chunks для векторного поиска
        - Обновить ingestion_status и ingestion_summary_json
        """
        logger.info(f"Начало ингестии документа {doc_version_id}")

        # Получаем версию документа
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        # Обновляем статус
        doc_version.ingestion_status = IngestionStatus.PROCESSING
        await self.db.commit()

        # TODO: Реальная логика ингестии
        # Здесь должна быть логика:
        # 1. Загрузка файла
        # 2. Парсинг структуры
        # 3. Создание anchors
        # 4. Создание chunks с embeddings

        # Заглушка: создаём несколько тестовых anchors
        anchor_count = 3
        chunk_count = 2

        # Обновляем статус на READY
        doc_version.ingestion_status = IngestionStatus.READY
        doc_version.ingestion_summary_json = {
            "anchors_count": anchor_count,
            "chunks_count": chunk_count,
            "status": "completed",
        }

        await self.db.commit()

        # Логируем в audit
        await self._log_audit(
            workspace_id=doc_version.document.workspace_id,
            action="ingest",
            entity_type="document_version",
            entity_id=str(doc_version_id),
            after_json={"status": "ready", "anchors_count": anchor_count},
        )

        logger.info(f"Ингестия завершена для {doc_version_id}: {anchor_count} anchors, {chunk_count} chunks")

        return IngestionResult(
            doc_version_id=doc_version_id,
            status=IngestionStatus.READY,
            anchors_count=anchor_count,
            chunks_count=chunk_count,
            summary=doc_version.ingestion_summary_json,
        )

    async def _log_audit(
        self,
        workspace_id: UUID,
        action: str,
        entity_type: str,
        entity_id: str,
        before_json: dict[str, Any] | None = None,
        after_json: dict[str, Any] | None = None,
        actor_user_id: UUID | None = None,
    ) -> None:
        """Вспомогательный метод для логирования в audit_log."""
        audit_entry = AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_json=before_json,
            after_json=after_json,
        )
        self.db.add(audit_entry)
        await self.db.commit()

