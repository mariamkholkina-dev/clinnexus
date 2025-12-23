"""
Репозитории для работы с topics и heading_clusters.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import logger
from app.db.models.topics import (
    ClusterAssignment,
    HeadingCluster,
    Topic,
    TopicMappingRun,
)


class TopicRepository:
    """Репозиторий для работы с топиками."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_topics(
        self,
        workspace_id: UUID | None = None,
        is_active: bool | None = True,
    ) -> list[Topic]:
        """Получает список топиков с опциональной фильтрацией."""
        stmt = select(Topic)
        if workspace_id:
            stmt = stmt.where(Topic.workspace_id == workspace_id)
        if is_active is not None:
            stmt = stmt.where(Topic.is_active == is_active)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_topic(
        self,
        topic_key: str,
        workspace_id: UUID | None = None,
    ) -> Topic | None:
        """Получает топик по topic_key."""
        stmt = select(Topic).where(Topic.topic_key == topic_key)
        if workspace_id:
            stmt = stmt.where(Topic.workspace_id == workspace_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_topic(
        self,
        workspace_id: UUID,
        topic_key: str,
        title_ru: str | None = None,
        title_en: str | None = None,
        description: str | None = None,
        topic_profile_json: dict[str, Any] | None = None,
        is_active: bool = True,
    ) -> Topic:
        """
        Создает или обновляет топик.

        Использует ON CONFLICT для идемпотентности.
        """
        # Используем PostgreSQL INSERT ... ON CONFLICT
        stmt = pg_insert(Topic).values(
            workspace_id=workspace_id,
            topic_key=topic_key,
            title_ru=title_ru,
            title_en=title_en,
            description=description,
            topic_profile_json=topic_profile_json or {},
            is_active=is_active,
        )
        # Используем constraint name из миграции 0008
        stmt = stmt.on_conflict_do_update(
            constraint="uq_topics_workspace_topic_key",
            set_=dict(
                title_ru=stmt.excluded.title_ru,
                title_en=stmt.excluded.title_en,
                description=stmt.excluded.description,
                topic_profile_json=stmt.excluded.topic_profile_json,
                is_active=stmt.excluded.is_active,
            ),
        ).returning(Topic)

        result = await self.db.execute(stmt)
        await self.db.commit()
        topic = result.scalar_one()
        await self.db.refresh(topic)
        logger.info(f"Upserted topic: {topic_key} in workspace {workspace_id}")
        return topic


class HeadingClusterRepository:
    """Репозиторий для работы с кластерами заголовков."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert_cluster(
        self,
        doc_version_id: UUID,
        cluster_id: int,
        language: str,
        top_titles_json: list[Any] | None = None,
        examples_json: list[Any] | None = None,
        stats_json: dict[str, Any] | None = None,
        cluster_embedding: list[float] | None = None,
    ) -> HeadingCluster:
        """
        Создает или обновляет кластер заголовков.

        Использует ON CONFLICT для идемпотентности.
        """
        from app.db.enums import DocumentLanguage

        # Конвертируем строку в enum, если нужно
        if isinstance(language, str):
            language_enum = DocumentLanguage(language)
        else:
            language_enum = language

        values_dict = {
            "doc_version_id": doc_version_id,
            "cluster_id": cluster_id,
            "language": language_enum,
            "top_titles_json": top_titles_json or [],
            "examples_json": examples_json or [],
            "stats_json": stats_json or {},
        }
        
        # Для vector типа передаем список напрямую
        if cluster_embedding is not None:
            values_dict["cluster_embedding"] = cluster_embedding
        
        stmt = pg_insert(HeadingCluster).values(**values_dict)
        
        set_dict = {
            "top_titles_json": stmt.excluded.top_titles_json,
            "examples_json": stmt.excluded.examples_json,
            "stats_json": stmt.excluded.stats_json,
        }
        
        if cluster_embedding is not None:
            set_dict["cluster_embedding"] = stmt.excluded.cluster_embedding
        
        stmt = stmt.on_conflict_do_update(
            index_elements=["doc_version_id", "cluster_id", "language"],
            set_=set_dict,
        ).returning(HeadingCluster)

        result = await self.db.execute(stmt)
        await self.db.commit()
        cluster = result.scalar_one()
        await self.db.refresh(cluster)
        logger.info(
            f"Upserted heading cluster: {cluster_id} for doc_version {doc_version_id}, language {language_enum.value}"
        )
        return cluster

    async def get_clusters_by_doc_version(
        self,
        doc_version_id: UUID,
    ) -> list[HeadingCluster]:
        """Получает все кластеры для версии документа."""
        stmt = select(HeadingCluster).where(
            HeadingCluster.doc_version_id == doc_version_id
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())


class ClusterAssignmentRepository:
    """Репозиторий для работы с привязками кластеров к топикам."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert_assignment(
        self,
        doc_version_id: UUID,
        cluster_id: int,
        topic_key: str,
        mapped_by: str,
        confidence: float | None = None,
        notes: str | None = None,
        mapping_debug_json: dict[str, Any] | None = None,
    ) -> ClusterAssignment:
        """
        Создает или обновляет привязку кластера к топику.

        Использует ON CONFLICT для идемпотентности.
        """
        # Валидация mapped_by
        valid_mapped_by = {"auto", "assist", "manual", "seed", "import"}
        if mapped_by not in valid_mapped_by:
            raise ValueError(
                f"mapped_by must be one of {valid_mapped_by}, got: {mapped_by}"
            )

        stmt = pg_insert(ClusterAssignment).values(
            doc_version_id=doc_version_id,
            cluster_id=cluster_id,
            topic_key=topic_key,
            mapped_by=mapped_by,
            confidence=confidence,
            notes=notes,
            mapping_debug_json=mapping_debug_json,
        )
        # Используем constraint name из миграции 0008
        stmt = stmt.on_conflict_do_update(
            constraint="uq_cluster_assignments_doc_version_cluster",
            set_=dict(
                topic_key=stmt.excluded.topic_key,
                mapped_by=stmt.excluded.mapped_by,
                confidence=stmt.excluded.confidence,
                notes=stmt.excluded.notes,
                mapping_debug_json=stmt.excluded.mapping_debug_json,
            ),
        ).returning(ClusterAssignment)

        result = await self.db.execute(stmt)
        await self.db.commit()
        assignment = result.scalar_one()
        await self.db.refresh(assignment)
        logger.info(
            f"Upserted cluster assignment: cluster {cluster_id} -> topic {topic_key} "
            f"for doc_version {doc_version_id}"
        )
        return assignment

    async def get_assignments_by_doc_version(
        self,
        doc_version_id: UUID,
    ) -> list[ClusterAssignment]:
        """Получает все привязки кластеров для версии документа."""
        stmt = select(ClusterAssignment).where(
            ClusterAssignment.doc_version_id == doc_version_id
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_assignment(
        self,
        doc_version_id: UUID,
        cluster_id: int,
    ) -> ClusterAssignment | None:
        """Получает привязку кластера по doc_version_id и cluster_id."""
        stmt = select(ClusterAssignment).where(
            ClusterAssignment.doc_version_id == doc_version_id,
            ClusterAssignment.cluster_id == cluster_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

