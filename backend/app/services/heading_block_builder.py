"""Сервис для построения heading blocks из anchors документа."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AnchorContentType, DocumentLanguage, SourceZone
from app.db.models.anchors import Anchor
from app.services.source_zone_classifier import get_classifier


@dataclass
class HeadingBlock:
    """Блок заголовка: заголовок + контент до следующего заголовка."""

    heading_block_id: str  # Стабильный идентификатор блока
    heading_anchor_id: str  # ID заголовка
    content_anchor_ids: list[str]  # ID контента до следующего заголовка
    section_path: str  # section_path заголовка
    source_zone: SourceZone  # Определяется через SourceZoneClassifier
    text_preview: str  # Небольшой превью для скоринга/отладки
    heading_text: str  # Текст заголовка
    language: DocumentLanguage  # Язык блока


class HeadingBlockBuilder:
    """Сервис для построения heading blocks из anchors."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.source_zone_classifier = get_classifier()

    async def build_blocks_for_doc_version(
        self, doc_version_id: UUID, doc_type: Any
    ) -> list[HeadingBlock]:
        """
        Строит heading blocks для версии документа.

        Алгоритм:
        1. Загружает все anchors, отсортированные по ordinal
        2. Проходит по anchors: при встрече HDR начинает новый блок
        3. Собирает контент (P/LI) до следующего HDR
        4. Определяет source_zone через SourceZoneClassifier
        5. Генерирует стабильный heading_block_id

        Args:
            doc_version_id: ID версии документа
            doc_type: Тип документа (DocumentType enum)

        Returns:
            Список heading blocks
        """
        logger.info(f"Построение heading blocks для doc_version_id={doc_version_id}")

        # Загружаем все anchors, отсортированные по ordinal
        stmt = (
            select(Anchor)
            .where(Anchor.doc_version_id == doc_version_id)
            .order_by(Anchor.ordinal)
        )
        result = await self.db.execute(stmt)
        anchors = list(result.scalars().all())

        if not anchors:
            logger.warning(f"Не найдено anchors для doc_version_id={doc_version_id}")
            return []

        blocks: list[HeadingBlock] = []
        current_heading: Anchor | None = None
        current_content: list[Anchor] = []

        for anchor in anchors:
            if anchor.content_type == AnchorContentType.HDR:
                # Сохраняем предыдущий блок, если есть
                if current_heading is not None:
                    block = self._create_block(
                        current_heading, current_content, doc_version_id, doc_type
                    )
                    if block:
                        blocks.append(block)

                # Начинаем новый блок
                current_heading = anchor
                current_content = []
            elif current_heading is not None:
                # Добавляем контент к текущему блоку
                if anchor.content_type in (
                    AnchorContentType.P,
                    AnchorContentType.LI,
                ):
                    current_content.append(anchor)

        # Сохраняем последний блок
        if current_heading is not None:
            block = self._create_block(
                current_heading, current_content, doc_version_id, doc_type
            )
            if block:
                blocks.append(block)

        logger.info(f"Построено {len(blocks)} heading blocks")
        return blocks

    def _create_block(
        self,
        heading: Anchor,
        content: list[Anchor],
        doc_version_id: UUID,
        doc_type: Any,
    ) -> HeadingBlock | None:
        """Создает HeadingBlock из заголовка и контента."""
        # Определяем source_zone через SourceZoneClassifier
        zone_result = self.source_zone_classifier.classify(
            doc_type=doc_type,
            section_path=heading.section_path,
            heading_text=heading.text_raw,
        )
        source_zone = SourceZone(zone_result.zone)

        # Определяем язык блока (берем язык заголовка, если mixed - берем most_common из контента)
        block_language = heading.language
        if block_language == DocumentLanguage.MIXED and content:
            # Простая эвристика: если есть контент, берем его язык
            content_languages = [a.language for a in content if a.language != DocumentLanguage.UNKNOWN]
            if content_languages:
                # Берем первый не-UNKNOWN язык из контента
                block_language = content_languages[0]

        # Генерируем стабильный heading_block_id
        # Формат: {doc_version_id}:block:{hash(heading_anchor_id)}
        # Используем heading_anchor_id как основу для стабильности
        heading_block_id = f"{doc_version_id}:block:{hashlib.sha256(heading.anchor_id.encode()).hexdigest()[:16]}"

        # Создаем text_preview (первые 200 символов заголовка + контента)
        preview_parts = [heading.text_raw]
        for anchor in content[:3]:  # Берем первые 3 параграфа
            preview_parts.append(anchor.text_raw[:100])
        text_preview = " ".join(preview_parts)[:200]

        return HeadingBlock(
            heading_block_id=heading_block_id,
            heading_anchor_id=heading.anchor_id,
            content_anchor_ids=[a.anchor_id for a in content],
            section_path=heading.section_path,
            source_zone=source_zone,
            text_preview=text_preview,
            heading_text=heading.text_raw,
            language=block_language,
        )

