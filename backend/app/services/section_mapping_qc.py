"""QC Gate для валидации кандидатов section mapping."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AnchorContentType, DocumentLanguage
from app.db.models.anchors import Anchor
from app.db.models.sections import SectionContract, SectionMap
from app.db.models.studies import DocumentVersion
from app.services.section_mapping import DocumentOutline, SectionMappingService
from app.services.text_normalization import normalize_for_match, normalize_for_regex


@dataclass
class QCError:
    """Ошибка QC валидации."""

    type: str  # "must_keywords", "not_keywords", "regex", "block_size", "conflict"
    message: str


@dataclass
class QCResult:
    """Результат QC валидации."""

    status: str  # "mapped" | "needs_review" | "rejected"
    selected_heading_anchor_id: str | None
    errors: list[QCError]
    derived_confidence: float


class SectionMappingQCGate:
    """Детерминированный QC Gate для валидации кандидатов section mapping."""

    def __init__(self, db: AsyncSession) -> None:
        """
        Инициализация QC Gate.

        Args:
            db: AsyncSession для доступа к БД
        """
        self.db = db

    async def validate_candidate(
        self,
        doc_version_id: UUID,
        section_key: str,
        heading_anchor_id: str,
        contract: SectionContract,
        all_anchors: list[Anchor],
        outline: DocumentOutline,
        existing_maps: dict[str, SectionMap],
        document_language: DocumentLanguage | None = None,
    ) -> QCResult:
        """
        Валидирует кандидата заголовка через QC Gate.

        Args:
            doc_version_id: ID версии документа
            section_key: Ключ секции
            heading_anchor_id: ID кандидата заголовка
            contract: SectionContract
            all_anchors: Все anchors документа
            outline: Структура документа
            existing_maps: Существующие маппинги (для проверки конфликтов)
            document_language: Язык документа (опционально, если None - берём из doc_version)

        Returns:
            QCResult с результатом валидации
        """
        errors: list[QCError] = []

        # Получаем document_language, если не передан
        if document_language is None:
            from sqlalchemy import select
            from app.db.models.studies import DocumentVersion
            result = await self.db.execute(select(DocumentVersion).where(DocumentVersion.id == doc_version_id))
            doc_version = result.scalar_one_or_none()
            if doc_version:
                document_language = doc_version.document_language
                logger.debug(
                    "SectionMappingQC: document_language взят из doc_version "
                    f"(doc_version_id={doc_version_id}, document_language={document_language.value})"
                )
            else:
                document_language = DocumentLanguage.UNKNOWN
                logger.debug(
                    "SectionMappingQC: document_language UNKNOWN (doc_version не найден) "
                    f"(doc_version_id={doc_version_id})"
                )
        else:
            logger.debug(
                "SectionMappingQC: document_language передан явно "
                f"(doc_version_id={doc_version_id}, document_language={document_language.value})"
            )

        # 1. Проверяем, что heading_anchor_id существует и content_type=hdr
        heading_anchor = None
        for anchor in all_anchors:
            if anchor.anchor_id == heading_anchor_id:
                heading_anchor = anchor
                break

        if not heading_anchor:
            errors.append(
                QCError(
                    type="not_found",
                    message=f"heading_anchor_id {heading_anchor_id} не найден",
                )
            )
            return QCResult(
                status="rejected",
                selected_heading_anchor_id=None,
                errors=errors,
                derived_confidence=0.0,
            )

        if heading_anchor.content_type != AnchorContentType.HDR:
            errors.append(
                QCError(
                    type="invalid_type",
                    message=f"anchor {heading_anchor_id} не является заголовком (content_type={heading_anchor.content_type})",
                )
            )
            return QCResult(
                status="rejected",
                selected_heading_anchor_id=None,
                errors=errors,
                derived_confidence=0.0,
            )

        # 2. Проверяем keywords и regex из контракта (language-aware)
        recipe = contract.retrieval_recipe_json
        if not recipe:
            errors.append(
                QCError(
                    type="invalid_contract",
                    message="retrieval_recipe_json невалиден",
                )
            )
            return QCResult(
                status="rejected",
                selected_heading_anchor_id=None,
                errors=errors,
                derived_confidence=0.0,
            )

        # Используем helper из SectionMappingService для получения signals
        mapping_service = SectionMappingService(self.db)
        signals = mapping_service._get_signals(recipe, document_language)

        # Получаем текст заголовка и сниппет
        heading_text = heading_anchor.text_norm
        snippet = self._get_snippet(heading_anchor, all_anchors)
        combined_text_normalized = normalize_for_match(heading_text + " " + snippet)

        # Проверка must keywords (на нормализованном тексте)
        if signals.must_keywords:
            has_must = any(
                normalize_for_match(kw) in combined_text_normalized
                for kw in signals.must_keywords
            )
            if not has_must:
                errors.append(
                    QCError(
                        type="must_keywords",
                        message=f"Не найдены обязательные keywords: {signals.must_keywords}",
                    )
                )

        # Проверка not keywords (на нормализованном тексте)
        if signals.not_keywords:
            found_not_keywords = [
                kw for kw in signals.not_keywords
                if normalize_for_match(kw) in combined_text_normalized
            ]
            if found_not_keywords:
                errors.append(
                    QCError(
                        type="not_keywords",
                        message=f"Найдены запрещённые keywords: {found_not_keywords}",
                    )
                )

        # Проверка regex (на нормализованном тексте для regex)
        has_regex_match = False
        heading_text_for_regex = normalize_for_regex(heading_text)
        if signals.regex_patterns:
            for pattern in signals.regex_patterns:
                try:
                    if re.search(pattern, heading_text_for_regex, re.IGNORECASE):
                        has_regex_match = True
                        break
                except re.error:
                    logger.warning(f"Некорректный regex pattern: {pattern}")

        # Если есть критические ошибки (must_keywords или not_keywords), отклоняем
        critical_errors = [e for e in errors if e.type in ("must_keywords", "not_keywords")]
        if critical_errors:
            return QCResult(
                status="rejected",
                selected_heading_anchor_id=None,
                errors=errors,
                derived_confidence=0.0,
            )

        # 3. Проверяем capture heading_block
        heading_level = self._extract_heading_level(heading_anchor)
        heading_para_index = heading_anchor.location_json.get("para_index", 0)

        # Находим следующий заголовок с level <= heading_level
        end_para_index = None
        for anchor, level in outline.headings:
            anchor_para_index = anchor.location_json.get("para_index", 0)
            if anchor_para_index > heading_para_index and level <= heading_level:
                end_para_index = anchor_para_index
                break

        # Собираем все anchors между start и end
        block_anchor_ids: list[str] = []
        for anchor in all_anchors:
            para_index = anchor.location_json.get("para_index", 0)
            if para_index >= heading_para_index:
                if end_para_index is None or para_index < end_para_index:
                    block_anchor_ids.append(anchor.anchor_id)
                else:
                    break

        # Проверяем минимальный размер блока
        capture_config = recipe.get("capture", {})
        min_block_size = capture_config.get("min_anchors", 2)
        if len(block_anchor_ids) < min_block_size:
            errors.append(
                QCError(
                    type="block_size",
                    message=f"Блок слишком мал: {len(block_anchor_ids)} < {min_block_size}",
                )
            )

        # 4. Проверяем пересечения с существующими маппингами
        conflict_sections: list[str] = []
        for other_section_key, other_map in existing_maps.items():
            if other_section_key == section_key:
                continue
            if other_map.status.value == "overridden":
                continue  # Не проверяем overridden
            if other_map.anchor_ids and set(block_anchor_ids) & set(other_map.anchor_ids):
                # Есть пересечение
                if other_map.confidence >= 0.7:
                    conflict_sections.append(other_section_key)

        if conflict_sections:
            errors.append(
                QCError(
                    type="conflict",
                    message=f"Пересечение с секциями: {conflict_sections}",
                )
            )

        # 5. Вычисляем derived_confidence
        confidence = 0.5  # Базовый

        if has_regex_match:
            confidence += 0.2
        if signals.must_keywords and any(
            normalize_for_match(kw) in combined_text_normalized
            for kw in signals.must_keywords
        ):
            confidence += 0.2
        if len(block_anchor_ids) >= min_block_size:
            confidence += 0.1

        # Штрафы за ошибки
        if any(e.type == "block_size" for e in errors):
            confidence -= 0.2
        if any(e.type == "conflict" for e in errors):
            confidence -= 0.1

        # Применяем confidence cap для mixed/unknown
        if signals.confidence_cap is not None:
            confidence = min(confidence, signals.confidence_cap)

        confidence = max(0.0, min(1.0, confidence))

        # 6. Определяем статус
        if errors and not any(e.type in ("block_size", "conflict") for e in errors):
            # Есть некритические ошибки, но не блокирующие
            status = "needs_review"
        elif confidence >= 0.75 and not conflict_sections:
            status = "mapped"
        elif confidence >= 0.5:
            status = "needs_review"
        else:
            status = "rejected"

        return QCResult(
            status=status,
            selected_heading_anchor_id=heading_anchor_id if status != "rejected" else None,
            errors=errors,
            derived_confidence=confidence,
        )

    def _get_snippet(self, heading_anchor: Anchor, all_anchors: list[Anchor]) -> str:
        """
        Получает сниппет (1-2 первых paragraph после заголовка, до 300 символов).

        Args:
            heading_anchor: Anchor заголовка
            all_anchors: Все anchors документа

        Returns:
            Сниппет текста
        """
        heading_para_index = heading_anchor.location_json.get("para_index", 0)
        snippet_parts: list[str] = []
        total_length = 0

        for anchor in all_anchors:
            para_index = anchor.location_json.get("para_index", 0)
            if para_index <= heading_para_index:
                continue

            # Берём только первые 2 paragraph после заголовка
            if anchor.content_type == AnchorContentType.P:
                text = anchor.text_norm[:300]
                if total_length + len(text) > 300:
                    text = text[: 300 - total_length]
                snippet_parts.append(text)
                total_length += len(text)
                if len(snippet_parts) >= 2 or total_length >= 300:
                    break

        return " ".join(snippet_parts)

    def _extract_heading_level(self, anchor: Anchor) -> int:
        """
        Извлекает уровень заголовка из anchor.

        Args:
            anchor: Anchor заголовка

        Returns:
            Уровень заголовка (1..9)
        """
        # Пытаемся извлечь из section_path (количество "/" + 1)
        if anchor.section_path and anchor.section_path != "ROOT":
            level = anchor.section_path.count("/") + 1
            if 1 <= level <= 9:
                return level

        # Fallback: пытаемся извлечь из нумерации в тексте
        text = anchor.text_norm
        match = re.match(r"^(\d+(?:\.\d+)*)[)\.]?\s+", text)
        if match:
            numbering_part = match.group(1)
            level = numbering_part.count(".") + 1
            if 1 <= level <= 9:
                return level

        # По умолчанию уровень 1
        return 1

