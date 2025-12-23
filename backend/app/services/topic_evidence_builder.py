from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import DocumentLanguage
from app.db.models.anchors import Anchor, Chunk
from app.db.models.topics import HeadingBlockTopicAssignment, TopicEvidence
from app.services.heading_block_builder import HeadingBlockBuilder


class TopicEvidenceBuilder:
    """Сервис для построения topic_evidence из heading_block_topic_assignments и blocks."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build_evidence_for_doc_version(
        self, doc_version_id: uuid.UUID
    ) -> int:
        """
        Пересобирает topic_evidence для указанной версии документа.

        Алгоритм:
        1. Получает все heading_block_topic_assignments для doc_version_id
        2. Строит heading blocks для получения anchor_ids каждого блока
        3. Агрегирует anchor_ids и chunk_ids по topic_key + source_zone + language
        4. Сохраняет/обновляет topic_evidence

        Args:
            doc_version_id: UUID версии документа

        Returns:
            Количество созданных/обновленных записей topic_evidence
        """
        # Получаем все block assignments для этого doc_version
        stmt = select(HeadingBlockTopicAssignment).where(
            HeadingBlockTopicAssignment.doc_version_id == doc_version_id
        )
        result = await self.db.execute(stmt)
        assignments = result.scalars().all()

        if not assignments:
            logger.warning(
                f"Нет heading_block_topic_assignments для doc_version_id={doc_version_id}"
            )
            return 0

        # Создаем маппинг heading_block_id -> topic_key
        block_to_topic: dict[str, str] = {
            ba.heading_block_id: ba.topic_key for ba in assignments
        }

        # Получаем doc_type для построения блоков
        from app.db.models.studies import Document, DocumentVersion

        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        document = await self.db.get(Document, doc_version.document_id)
        if not document:
            raise ValueError(f"Document {doc_version.document_id} не найден")

        # Строим блоки для получения anchor_ids
        block_builder = HeadingBlockBuilder(self.db)
        blocks = await block_builder.build_blocks_for_doc_version(doc_version_id, document.doc_type)

        # Создаем маппинг heading_block_id -> block
        block_by_id: dict[str, Any] = {block.heading_block_id: block for block in blocks}

        # Получаем все chunks для этого doc_version
        stmt = select(Chunk).where(Chunk.doc_version_id == doc_version_id)
        result = await self.db.execute(stmt)
        chunks = result.scalars().all()

        # Получаем все anchors для определения source_zone и language
        stmt = select(Anchor).where(Anchor.doc_version_id == doc_version_id)
        result = await self.db.execute(stmt)
        anchors = {a.anchor_id: a for a in result.scalars().all()}

        # Агрегируем anchor_ids и chunk_ids по (topic_key, source_zone, language)
        evidence_map: dict[
            tuple[str, str, DocumentLanguage], dict[str, Any]
        ] = defaultdict(
            lambda: {
                "anchor_ids": set(),
                "chunk_ids": set(),
                "top_headings": [],
                "block_ids": [],
            }
        )

        # Обрабатываем блоки
        for block_id, topic_key in block_to_topic.items():
            block = block_by_id.get(block_id)
            if not block:
                continue

            # Собираем все anchor_ids блока (заголовок + контент)
            block_anchor_ids = [block.heading_anchor_id] + block.content_anchor_ids

            # Определяем source_zone и language из блока
            source_zone = block.source_zone.value
            language = block.language

            key = (topic_key, source_zone, language)
            evidence_map[key]["anchor_ids"].update(block_anchor_ids)
            evidence_map[key]["top_headings"].append(block.heading_text)
            evidence_map[key]["block_ids"].append(block_id)

        # Обрабатываем chunks: находим chunk_ids, которые содержат anchor_ids из evidence
        anchor_ids_in_evidence: set[str] = set()
        for evidence in evidence_map.values():
            anchor_ids_in_evidence.update(evidence["anchor_ids"])

        for chunk in chunks:
            chunk_anchor_ids = set(chunk.anchor_ids)
            if not chunk_anchor_ids.intersection(anchor_ids_in_evidence):
                continue

            # Находим, к каким topic_key относится этот chunk
            for topic_key, source_zone, language in evidence_map.keys():
                if chunk.source_zone.value != source_zone or chunk.language != language:
                    continue

                if chunk_anchor_ids.intersection(evidence_map[(topic_key, source_zone, language)]["anchor_ids"]):
                    evidence_map[(topic_key, source_zone, language)]["chunk_ids"].add(chunk.id)

        # Сохраняем/обновляем topic_evidence
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

            # Вычисляем score как максимальный confidence из assignments для этого топика
            topic_assignments = [ba for ba in assignments if ba.topic_key == topic_key]
            max_confidence = max([ba.confidence for ba in topic_assignments if ba.confidence], default=None)

            # Формируем evidence_json
            evidence_json = {
                "top_headings": data["top_headings"][:10],  # Топ-10 заголовков
                "block_ids": data["block_ids"],
                "blocks_count": len(data["block_ids"]),
            }

            evidence = TopicEvidence(
                doc_version_id=doc_version_id,
                topic_key=topic_key,
                source_zone=source_zone,
                language=language,
                anchor_ids=sorted(list(data["anchor_ids"])),
                chunk_ids=sorted(list(data["chunk_ids"])),
                score=max_confidence,
                evidence_json=evidence_json,
            )
            self.db.add(evidence)
            created_count += 1

        await self.db.commit()
        logger.info(
            f"Создано {created_count} записей topic_evidence для doc_version_id={doc_version_id}"
        )

        return created_count

