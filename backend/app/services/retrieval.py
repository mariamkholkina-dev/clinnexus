from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import logger
from app.db.enums import DocumentType
from app.db.models.anchors import Chunk
from app.schemas.anchors import ChunkOut
from app.services.zone_config import get_zone_config_service


class RetrievalService:
    """Сервис для поиска релевантных chunks по запросу."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def retrieve(
        self,
        query: str,
        filters: dict[str, Any] | None = None,
        k: int = 10,
    ) -> list[ChunkOut]:
        """
        Поиск релевантных chunks по запросу.

        TODO: Реальная реализация должна:
        - Векторизовать query
        - Использовать pgvector для поиска похожих chunks
        - Применять filters (study_id, doc_type, section_path и т.д.)
        - Возвращать топ-k результатов
        
        TODO (в будущем): Добавить фильтр по language:
        - Если target doc на RU — брать anchors/chunks language=ru
        - Если mixed — применять prefer_language
        """
        logger.info(f"Поиск chunks для запроса: {query[:50]}...")

        # TODO: Реальная логика поиска
        # Здесь должна быть логика:
        # 1. Векторизовать query (использовать ту же модель, что и для chunks)
        # 2. Выполнить векторный поиск с фильтрами
        # 3. Вернуть топ-k результатов
        
        # TODO: Фильтр по language (когда будет реализовано):
        # - Если target_doc.document_language == DocumentLanguage.RU:
        #     stmt = stmt.where(Chunk.language == DocumentLanguage.RU)
        # - Если target_doc.document_language == DocumentLanguage.MIXED:
        #     prefer_language = filters.get("prefer_language")
        #     if prefer_language:
        #         # Предпочитаем prefer_language, но не исключаем другие
        #         stmt = stmt.order_by(case((Chunk.language == prefer_language, 0), else_=1))

        # Заглушка: возвращаем пустой список
        # В реальной реализации здесь будет:
        # - query_vector = embed(query)
        # - stmt = select(Chunk).where(...).order_by(Chunk.embedding.cosine_distance(query_vector)).limit(k)
        # - results = await self.db.execute(stmt)

        logger.info(f"Найдено 0 chunks (заглушка)")
        return []

    async def retrieve_with_zone_crosswalk(
        self,
        query: str,
        source_doc_type: DocumentType | str,
        target_doc_type: DocumentType | str,
        source_zones: list[str] | None = None,
        filters: dict[str, Any] | None = None,
        k: int = 10,
    ) -> list[ChunkOut]:
        """
        Поиск chunks с использованием zone_crosswalk для cross-doc retrieval.

        Args:
            query: Поисковый запрос
            source_doc_type: Тип исходного документа
            target_doc_type: Тип целевого документа
            source_zones: Исходные зоны для перевода
            filters: Дополнительные фильтры
            k: Количество результатов

        Returns:
            Список chunks с учётом перевода зон через crosswalk
        """
        zone_config = get_zone_config_service()
        
        # Если source_zones не заданы, используем все зоны из zone_set
        if not source_zones:
            source_zones = zone_config.get_zone_set(source_doc_type)
        
        # Собираем целевые зоны с весами через crosswalk
        target_zones_with_weights: list[tuple[str, float]] = []
        for source_zone in source_zones:
            crosswalk_result = zone_config.get_crosswalk_zones(
                source_doc_type=source_doc_type,
                source_zone=source_zone,
                target_doc_type=target_doc_type,
            )
            target_zones_with_weights.extend(crosswalk_result)
        
        # Сортируем по весу и берём топ зоны
        target_zones_with_weights.sort(key=lambda x: x[1], reverse=True)
        top_target_zones = [zone for zone, _ in target_zones_with_weights[:5]]  # Топ-5 зон
        
        logger.info(
            f"Cross-doc retrieval: {source_doc_type} -> {target_doc_type}, "
            f"source_zones={source_zones}, target_zones={top_target_zones}"
        )
        
        # TODO: Реальная реализация должна использовать target_zones для фильтрации chunks
        # Здесь будет логика:
        # 1. Векторизовать query
        # 2. Фильтровать chunks по target_zones (chunk.source_zone in top_target_zones)
        # 3. Применять веса из crosswalk для ранжирования
        # 4. Возвращать топ-k результатов
        
        return []
