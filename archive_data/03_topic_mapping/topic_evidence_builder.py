from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AnchorContentType, DocumentLanguage
from app.db.models.anchors import Anchor, Chunk
from app.db.models.topics import ClusterAssignment, TopicEvidence


class TopicEvidenceBuilder:
    """Сервис для построения topic_evidence из cluster_assignments и anchors."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_evidence_for_doc_version(
        self, doc_version_id: uuid.UUID
    ) -> int:
        """
        Пересобирает topic_evidence для указанной версии документа.

        Алгоритм:
        1. Получает все cluster_assignments для doc_version_id
        2. Для каждого cluster_id находит все HDR anchors
        3. Для P/LI anchors наследует cluster_id от ближайшего заголовка
        4. Агрегирует anchor_ids и chunk_ids по topic_key + source_zone + language
        5. Сохраняет/обновляет topic_evidence

        Args:
            doc_version_id: UUID версии документа

        Returns:
            Количество созданных/обновленных записей topic_evidence
        """
        # Получаем все cluster_assignments для этого doc_version
        stmt = select(ClusterAssignment).where(
            ClusterAssignment.doc_version_id == doc_version_id
        )
        result = await self.db.execute(stmt)
        assignments = result.scalars().all()

        if not assignments:
            logger.warning(
                f"Нет cluster_assignments для doc_version_id={doc_version_id}"
            )
            return 0

        # Создаем маппинг cluster_id -> topic_key
        cluster_to_topic: dict[int, str] = {
            ca.cluster_id: ca.topic_key for ca in assignments
        }

        # Получаем все anchors для этого doc_version, отсортированные по ordinal
        stmt = (
            select(Anchor)
            .where(Anchor.doc_version_id == doc_version_id)
            .order_by(Anchor.ordinal)
        )
        result = await self.db.execute(stmt)
        anchors = result.scalars().all()

        # Получаем все chunks для этого doc_version
        stmt = select(Chunk).where(Chunk.doc_version_id == doc_version_id)
        result = await self.db.execute(stmt)
        chunks = result.scalars().all()

        # Создаем маппинг chunk_id -> chunk для быстрого доступа
        chunk_by_id: dict[uuid.UUID, Chunk] = {chunk.id: chunk for chunk in chunks}

        # Шаг 1: Находим cluster_id для HDR anchors
        # Предполагаем, что cluster_id хранится в metadata_json или location_json
        # Для MVP: ищем в location_json поле cluster_id
        anchor_to_cluster: dict[str, int | None] = {}
        for anchor in anchors:
            if anchor.content_type == AnchorContentType.HDR:
                # Ищем cluster_id в location_json
                cluster_id = anchor.location_json.get("cluster_id")
                if cluster_id is not None and isinstance(cluster_id, int):
                    anchor_to_cluster[anchor.anchor_id] = cluster_id
                else:
                    anchor_to_cluster[anchor.anchor_id] = None
            else:
                anchor_to_cluster[anchor.anchor_id] = None

        # Шаг 2: Для P/LI anchors наследуем cluster_id от ближайшего заголовка
        # Проходим по anchors и для каждого P/LI ищем ближайший HDR выше по ordinal
        current_hdr_cluster: int | None = None
        for anchor in anchors:
            if anchor.content_type == AnchorContentType.HDR:
                current_hdr_cluster = anchor_to_cluster.get(anchor.anchor_id)
            elif anchor.content_type in (
                AnchorContentType.P,
                AnchorContentType.LI,
            ):
                if current_hdr_cluster is not None:
                    anchor_to_cluster[anchor.anchor_id] = current_hdr_cluster

        # Шаг 3: Агрегируем anchor_ids и chunk_ids по (topic_key, source_zone, language)
        evidence_map: dict[
            tuple[str, str, DocumentLanguage], dict[str, Any]
        ] = defaultdict(
            lambda: {
                "anchor_ids": set(),
                "chunk_ids": set(),
            }
        )

        # Обрабатываем anchors
        for anchor in anchors:
            cluster_id = anchor_to_cluster.get(anchor.anchor_id)
            if cluster_id is None:
                continue

            topic_key = cluster_to_topic.get(cluster_id)
            if topic_key is None:
                continue

            key = (topic_key, anchor.source_zone, anchor.language)
            evidence_map[key]["anchor_ids"].add(anchor.anchor_id)

        # Обрабатываем chunks: находим chunk_ids, которые содержат anchor_ids из evidence
        anchor_ids_in_evidence: set[str] = set()
        for evidence in evidence_map.values():
            anchor_ids_in_evidence.update(evidence["anchor_ids"])

        for chunk in chunks:
            # Проверяем, есть ли пересечение anchor_ids chunk с anchor_ids в evidence
            chunk_anchor_ids = set(chunk.anchor_ids)
            if not chunk_anchor_ids.intersection(anchor_ids_in_evidence):
                continue

            # Находим, к каким topic_key относится этот chunk
            for topic_key, source_zone, language in evidence_map.keys():
                # Проверяем, что chunk соответствует source_zone и language
                if chunk.source_zone != source_zone or chunk.language != language:
                    continue

                # Проверяем, есть ли пересечение anchor_ids
                if chunk_anchor_ids.intersection(evidence_map[(topic_key, source_zone, language)]["anchor_ids"]):
                    evidence_map[(topic_key, source_zone, language)]["chunk_ids"].add(
                        chunk.id
                    )

        # Шаг 4: Сохраняем/обновляем topic_evidence
        # Сначала удаляем старые записи для этого doc_version
        stmt = select(TopicEvidence).where(
            TopicEvidence.doc_version_id == doc_version_id
        )
        result = await self.db.execute(stmt)
        existing_evidence = result.scalars().all()
        for evidence in existing_evidence:
            await self.db.delete(evidence)

        # Создаем новые записи
        created_count = 0
        for (topic_key, source_zone, language), data in evidence_map.items():
            if not data["anchor_ids"]:
                continue

            evidence = TopicEvidence(
                doc_version_id=doc_version_id,
                topic_key=topic_key,
                source_zone=source_zone,
                language=language,
                anchor_ids=sorted(list(data["anchor_ids"])),
                chunk_ids=sorted(list(data["chunk_ids"])),
                score=None,  # Можно вычислить позже
                evidence_json=None,
            )
            self.db.add(evidence)
            created_count += 1

        await self.db.commit()
        logger.info(
            f"Создано {created_count} записей topic_evidence для doc_version_id={doc_version_id}"
        )

        return created_count

