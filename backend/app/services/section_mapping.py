"""Сервис для автоматического маппинга семантических секций на anchors документа."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import (
    AnchorContentType,
    DocumentLanguage,
    DocumentType,
    SectionMapMappedBy,
    SectionMapStatus,
)
from app.db.models.anchors import Anchor
from app.db.models.sections import SectionContract, SectionMap
from app.db.models.studies import Document, DocumentVersion
from app.services.text_normalization import normalize_for_match, normalize_for_regex


@dataclass
class MappingSummary:
    """Сводка результатов маппинга секций."""

    sections_mapped_count: int = 0
    sections_needs_review_count: int = 0
    mapping_warnings: list[str] = None

    def __post_init__(self):
        if self.mapping_warnings is None:
            self.mapping_warnings = []


@dataclass
class HeadingCandidate:
    """Кандидат заголовка для секции."""

    anchor_id: str
    anchor: Anchor
    score: float
    reason: str


@dataclass
class DocumentOutline:
    """Структура документа (заголовки в порядке появления)."""

    headings: list[tuple[Anchor, int]]  # (anchor, level)


@dataclass
class LanguageAwareSignals:
    """Сигналы для матчинга с учетом языка."""
    
    must_keywords: list[str]
    should_keywords: list[str]
    not_keywords: list[str]
    regex_patterns: list[str]
    threshold: float = 3.0  # Минимальный score для кандидата
    confidence_cap: float | None = None  # Максимальный confidence (для mixed/unknown)


class SectionMappingService:
    """Сервис для автоматического маппинга семантических секций на anchors документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
    
    def _get_signals(
        self, recipe_json: dict[str, Any], document_language: DocumentLanguage
    ) -> LanguageAwareSignals:
        """
        Извлекает language-aware signals из retrieval_recipe_json.
        
        Поддерживает:
        - v1 (legacy): heading_match.must/should/not как arrays без языков
        - v2 (new): lang.ru.must, lang.en.must, etc.
        
        Args:
            recipe_json: retrieval_recipe_json из контракта
            document_language: Язык документа
            
        Returns:
            LanguageAwareSignals с keywords и regex patterns
        """
        version = recipe_json.get("version", 1)
        
        if version == 2:
            # Новая версия с языковой поддержкой
            lang_section = recipe_json.get("lang", {})
            regex_section = recipe_json.get("regex", {})
            
            must_keywords: list[str] = []
            should_keywords: list[str] = []
            not_keywords: list[str] = []
            regex_patterns: list[str] = []
            
            if document_language == DocumentLanguage.RU:
                lang_data = lang_section.get("ru", {})
                must_keywords = lang_data.get("must", [])
                should_keywords = lang_data.get("should", [])
                not_keywords = lang_data.get("not", [])
                regex_patterns = regex_section.get("heading", {}).get("ru", [])
            elif document_language == DocumentLanguage.EN:
                lang_data = lang_section.get("en", {})
                must_keywords = lang_data.get("must", [])
                should_keywords = lang_data.get("should", [])
                not_keywords = lang_data.get("not", [])
                regex_patterns = regex_section.get("heading", {}).get("en", [])
            else:  # MIXED или UNKNOWN
                # Объединяем RU и EN
                ru_data = lang_section.get("ru", {})
                en_data = lang_section.get("en", {})
                
                must_keywords = list(set(ru_data.get("must", []) + en_data.get("must", [])))
                should_keywords = list(set(ru_data.get("should", []) + en_data.get("should", [])))
                not_keywords = list(set(ru_data.get("not", []) + en_data.get("not", [])))
                regex_patterns = regex_section.get("heading", {}).get("ru", []) + regex_section.get("heading", {}).get("en", [])
                
                # Повышаем threshold и добавляем confidence cap для mixed/unknown
                return LanguageAwareSignals(
                    must_keywords=must_keywords,
                    should_keywords=should_keywords,
                    not_keywords=not_keywords,
                    regex_patterns=regex_patterns,
                    threshold=4.0,  # +1 к threshold
                    confidence_cap=0.8,  # Cap для mixed/unknown
                )
            
            return LanguageAwareSignals(
                must_keywords=must_keywords,
                should_keywords=should_keywords,
                not_keywords=not_keywords,
                regex_patterns=regex_patterns,
            )
        else:
            # Legacy v1 формат (без языков)
            heading_match = recipe_json.get("heading_match", {})
            regex_section = recipe_json.get("regex", {})
            
            return LanguageAwareSignals(
                must_keywords=heading_match.get("must", []),
                should_keywords=heading_match.get("should", []),
                not_keywords=heading_match.get("not", []),
                regex_patterns=regex_section.get("heading", []),
            )

    async def map_sections(
        self, doc_version_id: UUID, force: bool = False
    ) -> MappingSummary:
        """
        Автоматический маппинг секций для версии документа.

        Args:
            doc_version_id: ID версии документа
            force: Если True, пересоздать все system mappings (кроме overridden)

        Returns:
            MappingSummary с результатами маппинга
        """
        logger.info(f"Начало маппинга секций для doc_version_id={doc_version_id}, force={force}")

        # Получаем версию документа и document
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        document = await self.db.get(Document, doc_version.document_id)
        if not document:
            raise ValueError(f"Document {doc_version.document_id} не найден")

        doc_type = document.doc_type

        # Получаем активные SectionContracts для doc_type
        contracts_stmt = select(SectionContract).where(
            SectionContract.doc_type == doc_type,
            SectionContract.is_active == True,
        )
        contracts_result = await self.db.execute(contracts_stmt)
        contracts = contracts_result.scalars().all()

        if not contracts:
            logger.warning(f"Нет активных SectionContracts для doc_type={doc_type.value}")
            return MappingSummary(
                sections_mapped_count=0,
                sections_needs_review_count=0,
                mapping_warnings=["Нет активных SectionContracts для данного типа документа"],
            )

        # Получаем все anchors версии
        anchors_stmt = select(Anchor).where(Anchor.doc_version_id == doc_version_id)
        anchors_result = await self.db.execute(anchors_stmt)
        all_anchors = anchors_result.scalars().all()
        
        # Сортируем anchors по para_index
        all_anchors = sorted(
            all_anchors,
            key=lambda a: a.location_json.get("para_index", 999999) if isinstance(a.location_json, dict) else 999999
        )

        if not all_anchors:
            logger.warning(f"Нет anchors для doc_version_id={doc_version_id}")
            return MappingSummary(
                sections_mapped_count=0,
                sections_needs_review_count=0,
                mapping_warnings=["Нет anchors для маппинга"],
            )

        # Строим document outline (заголовки)
        outline = self._build_document_outline(all_anchors)

        # Получаем существующие маппинги
        existing_maps_stmt = select(SectionMap).where(
            SectionMap.doc_version_id == doc_version_id
        )
        existing_maps_result = await self.db.execute(existing_maps_stmt)
        existing_maps = {m.section_key: m for m in existing_maps_result.scalars().all()}

        # Маппинг для каждого контракта
        summary = MappingSummary()
        new_maps: list[SectionMap] = []

        for contract in contracts:
            # Пропускаем overridden маппинги, если не force
            if not force and contract.section_key in existing_maps:
                existing_map = existing_maps[contract.section_key]
                if existing_map.status == SectionMapStatus.OVERRIDDEN:
                    logger.info(
                        f"Пропуск overridden маппинга для section_key={contract.section_key}"
                    )
                    continue

            # Ищем кандидатов заголовков
            heading_candidate = await self._find_heading_candidate(
                contract, outline, all_anchors, doc_version.document_language
            )

            # Создаём или обновляем маппинг
            section_map = await self._create_or_update_section_map(
                doc_version_id=doc_version_id,
                contract=contract,
                heading_candidate=heading_candidate,
                all_anchors=all_anchors,
                outline=outline,
                existing_map=existing_maps.get(contract.section_key) if not force else None,
                document_language=doc_version.document_language,
            )

            if section_map:
                new_maps.append(section_map)

                # Обновляем счётчики
                if section_map.status == SectionMapStatus.MAPPED:
                    summary.sections_mapped_count += 1
                elif section_map.status == SectionMapStatus.NEEDS_REVIEW:
                    summary.sections_needs_review_count += 1

                # Добавляем предупреждения
                if section_map.notes and "No heading match" in section_map.notes:
                    summary.mapping_warnings.append(
                        f"No heading match for {contract.section_key}"
                    )

        # Сохраняем маппинги (новые добавляем, существующие уже в сессии)
        for section_map in new_maps:
            if not section_map.id:  # Новый маппинг
                self.db.add(section_map)

        await self.db.flush()

        # Разрешаем конфликты (если один anchor попал в несколько секций)
        await self._resolve_conflicts(doc_version_id, new_maps)

        await self.db.commit()

        logger.info(
            f"Маппинг завершён для doc_version_id={doc_version_id}: "
            f"mapped={summary.sections_mapped_count}, "
            f"needs_review={summary.sections_needs_review_count}, "
            f"warnings={len(summary.mapping_warnings)}"
        )

        return summary

    def _build_document_outline(self, anchors: list[Anchor]) -> DocumentOutline:
        """
        Строит структуру документа (заголовки в порядке появления).

        Args:
            anchors: Все anchors документа

        Returns:
            DocumentOutline со списком заголовков
        """
        headings: list[tuple[Anchor, int]] = []

        for anchor in anchors:
            if anchor.content_type == AnchorContentType.HDR:
                # Определяем уровень из section_path глубины или из location_json
                level = self._extract_heading_level(anchor)
                headings.append((anchor, level))

        return DocumentOutline(headings=headings)

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

    async def _find_heading_candidate(
        self,
        contract: SectionContract,
        outline: DocumentOutline,
        all_anchors: list[Anchor],
        document_language: DocumentLanguage,
    ) -> HeadingCandidate | None:
        """
        Ищет кандидата заголовка для секции с учетом языка документа.

        Args:
            contract: SectionContract
            outline: Структура документа
            all_anchors: Все anchors документа
            document_language: Язык документа

        Returns:
            HeadingCandidate или None
        """
        recipe = contract.retrieval_recipe_json
        if not recipe:
            return None

        # Получаем language-aware signals
        signals = self._get_signals(recipe, document_language)

        candidates: list[HeadingCandidate] = []

        # Проходим по заголовкам
        for heading_anchor, level in outline.headings:
            score = 0.0
            reasons: list[str] = []

            # Нормализуем текст заголовка для матчинга keywords
            text_normalized = normalize_for_match(heading_anchor.text_norm)
            text_for_regex = normalize_for_regex(heading_anchor.text_norm)

            # Проверка keywords must
            for keyword in signals.must_keywords:
                keyword_normalized = normalize_for_match(keyword)
                if keyword_normalized in text_normalized:
                    score += 2.0
                    reasons.append(f"must:'{keyword}'")

            # Проверка keywords should
            for keyword in signals.should_keywords:
                keyword_normalized = normalize_for_match(keyword)
                if keyword_normalized in text_normalized:
                    score += 1.0
                    reasons.append(f"should:'{keyword}'")

            # Проверка negative keywords
            for keyword in signals.not_keywords:
                keyword_normalized = normalize_for_match(keyword)
                if keyword_normalized in text_normalized:
                    score -= 3.0
                    reasons.append(f"not:'{keyword}'")

            # Проверка regex (на нормализованном тексте для regex)
            for pattern in signals.regex_patterns:
                try:
                    if re.search(pattern, text_for_regex, re.IGNORECASE):
                        score += 3.0
                        reasons.append(f"regex:'{pattern}'")
                        break  # Первый матч достаточен
                except re.error:
                    logger.warning(f"Некорректный regex pattern: {pattern}")

            # Если score >= threshold, добавляем кандидата
            if score >= signals.threshold:
                candidates.append(
                    HeadingCandidate(
                        anchor_id=heading_anchor.anchor_id,
                        anchor=heading_anchor,
                        score=score,
                        reason=", ".join(reasons),
                    )
                )

        # Выбираем top-1 кандидата (с максимальным score)
        if candidates:
            best = max(candidates, key=lambda c: c.score)
            return best

        # Fallback для protocol.soa: ищем по фактам или cell anchors
        if contract.section_key == "protocol.soa":
            return await self._find_soa_fallback(all_anchors)

        return None

    async def _find_soa_fallback(
        self, all_anchors: list[Anchor]
    ) -> HeadingCandidate | None:
        """
        Fallback для поиска SoA секции (по cell anchors или фактам).

        Args:
            all_anchors: Все anchors документа

        Returns:
            HeadingCandidate или None
        """
        # Ищем cell anchors (признак SoA таблицы)
        cell_anchors = [
            a for a in all_anchors if a.content_type == AnchorContentType.CELL
        ]

        if cell_anchors:
            # Берём первый hdr anchor перед cell anchors
            first_cell_para_index = min(
                a.location_json.get("para_index", 999999) for a in cell_anchors
            )

            for anchor in all_anchors:
                if (
                    anchor.content_type == AnchorContentType.HDR
                    and anchor.location_json.get("para_index", 0) < first_cell_para_index
                ):
                    # Ищем ближайший заголовок перед таблицей
                    text_lower = anchor.text_norm.lower()
                    if any(
                        kw in text_lower
                        for kw in ["schedule", "activities", "soa", "visits", "таблица"]
                    ):
                        return HeadingCandidate(
                            anchor_id=anchor.anchor_id,
                            anchor=anchor,
                            score=2.0,
                            reason="soa_fallback:cell_anchors",
                        )

        return None

    async def _create_or_update_section_map(
        self,
        doc_version_id: UUID,
        contract: SectionContract,
        heading_candidate: HeadingCandidate | None,
        all_anchors: list[Anchor],
        outline: DocumentOutline,
        existing_map: SectionMap | None,
        document_language: DocumentLanguage,
    ) -> SectionMap | None:
        """
        Создаёт или обновляет SectionMap.

        Args:
            doc_version_id: ID версии документа
            contract: SectionContract
            heading_candidate: Кандидат заголовка или None
            all_anchors: Все anchors документа
            outline: Структура документа
            existing_map: Существующий маппинг (если есть)
            document_language: Язык документа

        Returns:
            SectionMap или None
        """
        if not heading_candidate:
            # Нет кандидата → needs_review
            if existing_map and existing_map.status == SectionMapStatus.OVERRIDDEN:
                # Не трогаем overridden
                return None

            if existing_map:
                # Обновляем существующий
                existing_map.anchor_ids = []
                existing_map.confidence = 0.0
                existing_map.status = SectionMapStatus.NEEDS_REVIEW
                existing_map.notes = "No heading match"
                existing_map.mapped_by = SectionMapMappedBy.SYSTEM
                return existing_map
            else:
                # Создаём новый
                section_map = SectionMap(
                    doc_version_id=doc_version_id,
                    section_key=contract.section_key,
                    anchor_ids=[],
                    chunk_ids=None,
                    confidence=0.0,
                    status=SectionMapStatus.NEEDS_REVIEW,
                    mapped_by=SectionMapMappedBy.SYSTEM,
                    notes="No heading match",
                )
                return section_map

        # Есть кандидат → захватываем блок
        anchor_ids, confidence, notes = self._capture_heading_block(
            heading_candidate, all_anchors, outline, contract, document_language
        )

        # Определяем status
        if confidence >= 0.7:
            status = SectionMapStatus.MAPPED
        else:
            status = SectionMapStatus.NEEDS_REVIEW

        if existing_map and existing_map.status == SectionMapStatus.OVERRIDDEN:
            # Не трогаем overridden
            return None

        if existing_map:
            # Обновляем существующий
            existing_map.anchor_ids = anchor_ids
            existing_map.confidence = confidence
            existing_map.status = status
            existing_map.notes = notes
            existing_map.mapped_by = SectionMapMappedBy.SYSTEM
            return existing_map
        else:
            # Создаём новый
            section_map = SectionMap(
                doc_version_id=doc_version_id,
                section_key=contract.section_key,
                anchor_ids=anchor_ids,
                chunk_ids=None,
                confidence=confidence,
                status=status,
                mapped_by=SectionMapMappedBy.SYSTEM,
                notes=notes,
            )
            return section_map

    def _capture_heading_block(
        self,
        heading_candidate: HeadingCandidate,
        all_anchors: list[Anchor],
        outline: DocumentOutline,
        contract: SectionContract,
        document_language: DocumentLanguage,
    ) -> tuple[list[str], float, str]:
        """
        Захватывает блок секции от заголовка до следующего заголовка того же/выше уровня.

        Args:
            heading_candidate: Кандидат заголовка
            all_anchors: Все anchors документа
            outline: Структура документа
            contract: SectionContract
            document_language: Язык документа

        Returns:
            (anchor_ids, confidence, notes)
        """
        heading_anchor = heading_candidate.anchor
        heading_level = self._extract_heading_level(heading_anchor)

        # Находим позицию заголовка в списке anchors
        heading_para_index = heading_anchor.location_json.get("para_index", 0)

        # Находим следующий заголовок с level <= heading_level
        end_para_index = None
        for anchor, level in outline.headings:
            anchor_para_index = anchor.location_json.get("para_index", 0)
            if anchor_para_index > heading_para_index and level <= heading_level:
                end_para_index = anchor_para_index
                break

        # Собираем все anchors между start и end
        anchor_ids: list[str] = []
        for anchor in all_anchors:
            para_index = anchor.location_json.get("para_index", 0)
            if para_index >= heading_para_index:
                if end_para_index is None or para_index < end_para_index:
                    anchor_ids.append(anchor.anchor_id)
                else:
                    break

        # Вычисляем confidence с учетом языка
        recipe = contract.retrieval_recipe_json
        signals = self._get_signals(recipe, document_language)

        confidence = 0.5  # Базовый confidence

        # Проверяем regex match
        has_regex_match = False
        text_for_regex = normalize_for_regex(heading_anchor.text_norm)
        for pattern in signals.regex_patterns:
            try:
                if re.search(pattern, text_for_regex, re.IGNORECASE):
                    has_regex_match = True
                    break
            except re.error:
                pass

        # Проверяем must match (на нормализованном тексте)
        text_normalized = normalize_for_match(heading_anchor.text_norm)
        has_must_match = any(
            normalize_for_match(kw) in text_normalized for kw in signals.must_keywords
        )

        if has_regex_match and has_must_match:
            confidence = 0.9
        elif has_regex_match or has_must_match:
            confidence = 0.7
        else:
            confidence = 0.5

        # Применяем confidence cap для mixed/unknown
        if signals.confidence_cap is not None:
            confidence = min(confidence, signals.confidence_cap)

        notes = f"Matched heading: {heading_anchor.text_norm[:100]} (score={heading_candidate.score:.1f}, {heading_candidate.reason})"

        return anchor_ids, confidence, notes

    async def _resolve_conflicts(
        self, doc_version_id: UUID, section_maps: list[SectionMap]
    ) -> None:
        """
        Разрешает конфликты маппинга (если один anchor попал в несколько секций).

        Args:
            doc_version_id: ID версии документа
            section_maps: Список маппингов
        """
        # Строим индекс: anchor_id -> список section_maps
        anchor_to_maps: dict[str, list[SectionMap]] = {}
        for section_map in section_maps:
            if section_map.anchor_ids:
                for anchor_id in section_map.anchor_ids:
                    if anchor_id not in anchor_to_maps:
                        anchor_to_maps[anchor_id] = []
                    anchor_to_maps[anchor_id].append(section_map)

        # Находим конфликты
        conflicts: list[tuple[str, list[SectionMap]]] = [
            (anchor_id, maps) for anchor_id, maps in anchor_to_maps.items() if len(maps) > 1
        ]

        if not conflicts:
            return

        logger.info(f"Найдено {len(conflicts)} конфликтов маппинга")

        # Разрешаем конфликты: предпочитаем секцию с более высоким confidence
        for anchor_id, maps in conflicts:
            # Сортируем по confidence (убывание)
            maps_sorted = sorted(maps, key=lambda m: m.confidence, reverse=True)

            # Оставляем anchor_id только в секции с максимальным confidence
            winner = maps_sorted[0]
            losers = maps_sorted[1:]

            for loser in losers:
                if loser.anchor_ids and anchor_id in loser.anchor_ids:
                    loser.anchor_ids.remove(anchor_id)
                    # Обновляем notes
                    if loser.notes:
                        loser.notes += f"; Conflict resolved: {anchor_id} -> {winner.section_key}"
                    else:
                        loser.notes = f"Conflict resolved: {anchor_id} -> {winner.section_key}"

                    # Если конфликт сильный, ставим needs_review
                    if loser.confidence >= 0.7:
                        loser.status = SectionMapStatus.NEEDS_REVIEW
