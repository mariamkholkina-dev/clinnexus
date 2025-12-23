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
from app.db.enums import DocumentType
from app.db.models.topics import (
    ClusterAssignment,
    HeadingCluster,
    Topic,
    TopicMappingRun,
    TopicZonePrior,
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
        applicable_to_json: list[str] | None = None,
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
            applicable_to_json=applicable_to_json or [],
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
                applicable_to_json=stmt.excluded.applicable_to_json,
                is_active=stmt.excluded.is_active,
            ),
        ).returning(Topic)

        result = await self.db.execute(stmt)
        await self.db.commit()
        topic = result.scalar_one()
        await self.db.refresh(topic)
        logger.info(f"Upserted topic: {topic_key} in workspace {workspace_id}")
        return topic

    async def get_profiles_by_doc_type(
        self,
        topic_key: str,
        workspace_id: UUID | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Получает профили топика по типам документов из topic_profile_json.

        Возвращает словарь {doc_type: profile_dict}, где profile_dict может содержать
        ключи типа aliases_ru, aliases_en, keywords_ru, keywords_en, source_zones и т.д.
        """
        topic = await self.get_topic(topic_key, workspace_id)
        if not topic:
            return {}

        topic_profile = topic.topic_profile_json or {}
        profiles_by_doc_type = topic_profile.get("profiles_by_doc_type", {})
        return profiles_by_doc_type

    async def set_profiles_by_doc_type(
        self,
        topic_key: str,
        profiles_by_doc_type: dict[str, dict[str, Any]],
        workspace_id: UUID | None = None,
    ) -> Topic:
        """
        Устанавливает профили топика по типам документов в topic_profile_json.

        profiles_by_doc_type должен быть словарем {doc_type: profile_dict}.
        """
        topic = await self.get_topic(topic_key, workspace_id)
        if not topic:
            raise ValueError(f"Topic {topic_key} not found")

        topic_profile = topic.topic_profile_json or {}
        topic_profile["profiles_by_doc_type"] = profiles_by_doc_type

        topic.topic_profile_json = topic_profile
        await self.db.commit()
        await self.db.refresh(topic)
        logger.info(f"Updated profiles_by_doc_type for topic: {topic_key}")
        return topic

    async def get_applicable_to(
        self,
        topic_key: str,
        workspace_id: UUID | None = None,
    ) -> list[str]:
        """
        Получает список типов документов, к которым применим топик.

        Пустой список означает, что топик применим ко всем типам документов.
        """
        topic = await self.get_topic(topic_key, workspace_id)
        if not topic:
            return []
        return topic.applicable_to_json or []

    async def set_applicable_to(
        self,
        topic_key: str,
        applicable_to: list[str],
        workspace_id: UUID | None = None,
    ) -> Topic:
        """
        Устанавливает список типов документов, к которым применим топик.

        Пустой список означает, что топик применим ко всем типам документов.
        """
        topic = await self.get_topic(topic_key, workspace_id)
        if not topic:
            raise ValueError(f"Topic {topic_key} not found")

        topic.applicable_to_json = applicable_to
        await self.db.commit()
        await self.db.refresh(topic)
        logger.info(f"Updated applicable_to for topic: {topic_key}")
        return topic

    async def get_zone_priors(
        self,
        topic_key: str,
        doc_type: DocumentType | None = None,
    ) -> list[TopicZonePrior]:
        """
        Получает приоритеты зон для топика.

        Если doc_type указан, возвращает только приоритеты для этого типа документа.
        """
        stmt = select(TopicZonePrior).where(TopicZonePrior.topic_key == topic_key)
        if doc_type:
            stmt = stmt.where(TopicZonePrior.doc_type == doc_type)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def upsert_zone_prior(
        self,
        topic_key: str,
        doc_type: DocumentType,
        zone_key: str,
        weight: float,
        notes: str | None = None,
    ) -> TopicZonePrior:
        """
        Создает или обновляет приоритет зоны для топика по типу документа.
        """
        stmt = pg_insert(TopicZonePrior).values(
            topic_key=topic_key,
            doc_type=doc_type,
            zone_key=zone_key,
            weight=weight,
            notes=notes,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_topic_zone_priors_topic_doc_zone",
            set_=dict(
                weight=stmt.excluded.weight,
                notes=stmt.excluded.notes,
            ),
        ).returning(TopicZonePrior)

        result = await self.db.execute(stmt)
        await self.db.commit()
        prior = result.scalar_one()
        await self.db.refresh(prior)
        logger.info(
            f"Upserted zone prior: topic={topic_key}, doc_type={doc_type.value}, zone={zone_key}, weight={weight}"
        )
        return prior

    async def delete_zone_prior(
        self,
        topic_key: str,
        doc_type: DocumentType,
        zone_key: str,
    ) -> bool:
        """
        Удаляет приоритет зоны для топика.

        Возвращает True, если приоритет был удален, False если не найден.
        """
        stmt = select(TopicZonePrior).where(
            TopicZonePrior.topic_key == topic_key,
            TopicZonePrior.doc_type == doc_type,
            TopicZonePrior.zone_key == zone_key,
        )
        result = await self.db.execute(stmt)
        prior = result.scalar_one_or_none()
        if prior:
            await self.db.delete(prior)
            await self.db.commit()
            logger.info(
                f"Deleted zone prior: topic={topic_key}, doc_type={doc_type.value}, zone={zone_key}"
            )
            return True
        return False


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

