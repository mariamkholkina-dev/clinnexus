"""Сервис для извлечения и сохранения фактов из документа (rules-first подход)."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AnchorContentType, EvidenceRole, FactStatus
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact, FactEvidence
from app.db.models.sections import TargetSectionMap
from app.db.models.studies import Document, DocumentVersion
from app.db.models.topics import TopicEvidence
from app.services.fact_extraction_rules import (
    EXTRACTOR_VERSION,
    ExtractionRule,
    ExtractedFactCandidate,
    get_extraction_rules,
)
from app.services.value_normalizer import ValueNormalizer, ValueNormalizationResult


class FactExtractionResult:
    """Результат извлечения фактов."""

    def __init__(
        self,
        doc_version_id: UUID,
        facts_count: int = 0,
        facts: list[Fact] | None = None,
    ) -> None:
        self.doc_version_id = doc_version_id
        self.facts_count = facts_count
        self.facts = facts or []


def _get_type_category_from_fact_type(fact_type: str) -> str | None:
    """Определяет type_category на основе fact_type."""
    category_mapping: dict[str, str] = {
        "protocol_meta": "metadata",
        "study": "design",
        "population": "population",
        "treatment": "intervention",
        "intervention": "intervention",
        "bioequivalence": "bioequivalence",
        "statistics": "design",
        "endpoints": "design",
    }
    return category_mapping.get(fact_type)


class FactExtractionService:
    """Сервис для извлечения и сохранения фактов из документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._rules_cache: list[ExtractionRule] | None = None
        self._value_normalizer: ValueNormalizer | None = None

    @property
    def rules(self) -> list[ExtractionRule]:
        """Кэшированный список правил извлечения."""
        if self._rules_cache is None:
            self._rules_cache = get_extraction_rules()
        return self._rules_cache

    @property
    def value_normalizer(self) -> ValueNormalizer:
        """Кэшированный нормализатор значений."""
        if self._value_normalizer is None:
            self._value_normalizer = ValueNormalizer()
        return self._value_normalizer

    async def extract_and_upsert(self, doc_version_id: UUID, *, commit: bool = True) -> FactExtractionResult:
        """
        Извлекает факты из документа и сохраняет их в БД.

        Реализация rules-first (без LLM):
        - Загружаем anchors версии документа по типам: hdr/p/li/fn
        - Сортируем: hdr первыми, затем p/li/fn, затем ordinal
        - Применяем правила извлечения из реестра
        - Поддерживаем множественные кандидаты для конфликт-детекции
        - Upsert по (study_id, fact_type, fact_key)
        - Evidence: идемпотентно заменяем (delete by fact_id + insert), anchor_id только реальный
        """
        logger.info(f"Rules-first извлечение фактов из документа {doc_version_id}")

        # Получаем версию документа
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        study_id = doc_version.document.study_id

        anchors = await self._load_anchors_for_fact_extraction(doc_version_id)
        logger.info(f"Загружено {len(anchors)} anchors для извлечения фактов")
        allowed_anchor_ids = {a.anchor_id for a in anchors}

        # Загружаем маппинги топиков для использования при извлечении фактов
        topic_mappings = await self._load_topic_mappings(doc_version_id)
        
        # Загружаем маппинг anchor_id -> set[topic_key] для контекстного приоритета
        anchor_topic_mapping = await self._load_anchor_topic_mapping(doc_version_id)

        # Логируем список правил, которые будут применяться
        logger.info(f"Будут применены следующие правила извлечения:")
        for rule in self.rules:
            logger.info(f"  - {rule.fact_type}.{rule.fact_key} (priority={rule.priority}, RU паттернов={len(rule.patterns_ru)}, EN паттернов={len(rule.patterns_en)})")

        # Извлекаем факты через правила
        all_candidates = self._extract_facts_from_anchors(anchors, allowed_anchor_ids, topic_mappings, anchor_topic_mapping)
        logger.info(f"Извлечено {len(all_candidates)} кандидатов фактов из правил")

        # Извлекаем endpoints отдельно (специальная обработка)
        endpoint_candidates = self._extract_endpoints(anchors, allowed_anchor_ids)
        all_candidates.extend(endpoint_candidates)

        # Извлекаем analysis_populations отдельно (специальная обработка)
        analysis_pop_candidates = self._extract_analysis_populations(anchors, allowed_anchor_ids)
        all_candidates.extend(analysis_pop_candidates)

        # Группируем кандидаты по (fact_type, fact_key)
        candidates_by_key: dict[tuple[str, str], list[ExtractedFactCandidate]] = defaultdict(list)
        for cand in all_candidates:
            candidates_by_key[(cand.fact_type, cand.fact_key)].append(cand)

        # Upsert фактов с внутренним арбитражем для одного документа
        upserted: list[Fact] = []
        logger.info(f"Обрабатываем {len(candidates_by_key)} уникальных фактов (fact_type.fact_key)")
        for (fact_type, fact_key), candidates in candidates_by_key.items():
            logger.debug(f"Обрабатываем факт {fact_type}.{fact_key}: {len(candidates)} кандидатов")
            
            # Внутренний арбитраж: дедупликация кандидатов для одного документа
            best_candidate, alternatives = self._arbitrate_candidates_for_single_document(
                candidates=candidates,
                fact_type=fact_type,
                fact_key=fact_key,
                anchors=anchors,
            )
            
            if alternatives:
                logger.info(f"Факт {fact_type}.{fact_key}: выбрано лучшее значение (confidence={best_candidate.confidence:.2f}), {len(alternatives)} альтернатив с отличающимися значениями")

            # Применяем Value Normalizer для двойной проверки (GxP)
            normalized_candidate = await self._normalize_fact_value(
                candidate=best_candidate,
                anchors=anchors,
            )
            
            # Используем нормализованный кандидат вместо исходного
            if normalized_candidate:
                best_candidate = normalized_candidate

            fact = await self._upsert_fact(
                study_id=study_id,
                doc_version_id=doc_version_id,
                fact_type=fact_type,
                fact_key=fact_key,
                candidate=best_candidate,
                alternatives=alternatives,
            )

            await self._replace_evidence_for_fact(
                fact_id=fact.id,
                evidence_anchor_ids=best_candidate.evidence_anchor_ids,
                allowed_anchor_ids=allowed_anchor_ids,
            )

            upserted.append(fact)

        if commit:
            await self.db.commit()
        else:
            await self.db.flush()

        logger.info(f"Извлечено/обновлено {len(upserted)} фактов из {doc_version_id}")
        return FactExtractionResult(doc_version_id=doc_version_id, facts_count=len(upserted), facts=upserted)

    def _arbitrate_candidates_for_single_document(
        self,
        candidates: list[ExtractedFactCandidate],
        fact_type: str,
        fact_key: str,
        anchors: list[Anchor],
    ) -> tuple[ExtractedFactCandidate, list[ExtractedFactCandidate]]:
        """
        Внутренний арбитраж: дедупликация кандидатов для одной DocumentVersion.
        
        Логика выбора победителя:
        1. Группируем кандидатов по нормализованным значениям (64 и 64.0 схлопываются)
        2. Для каждой группы одинаковых значений выбираем лучшего по confidence
        3. Выбираем победителя среди уникальных значений:
           - LLM-валидированные значения (status='validated' или 'extracted' с confidence > 0.8) побеждают
           - Если нет LLM-валидированных, используем Majority Vote (значение, которое встречается чаще)
           - При равенстве вхождений выбираем по максимальному confidence
        
        Returns:
            Кортеж (лучший кандидат, список альтернативных кандидатов с отличающимися значениями)
        """
        if not candidates:
            raise ValueError("Список кандидатов не может быть пустым")
        
        if len(candidates) == 1:
            return candidates[0], []
        
        # Шаг 1: Группируем кандидатов по нормализованным значениям
        # (одинаковые значения типа 64 и 64.0 схлопываются в одну группу)
        candidates_by_value: dict[str, list[ExtractedFactCandidate]] = defaultdict(list)
        for cand in candidates:
            normalized_val = self._normalize_value_for_comparison(cand.value_json)
            value_key = json.dumps(normalized_val, sort_keys=True) if normalized_val is not None else ""
            candidates_by_value[value_key].append(cand)
        
        # Шаг 2: Для каждой группы одинаковых значений выбираем лучшего кандидата
        # (по максимальному confidence)
        best_candidates_by_value: list[ExtractedFactCandidate] = []
        for value_key, value_candidates in candidates_by_value.items():
            if len(value_candidates) == 1:
                best_candidates_by_value.append(value_candidates[0])
            else:
                # Выбираем лучшего по confidence
                best_candidate_for_value = max(value_candidates, key=lambda c: c.confidence)
                best_candidates_by_value.append(best_candidate_for_value)
                logger.debug(
                    f"Факт {fact_type}.{fact_key}: для нормализованного значения {value_key} "
                    f"выбран кандидат с confidence={best_candidate_for_value.confidence:.2f} "
                    f"из {len(value_candidates)} кандидатов с одинаковым значением"
                )
        
        # Шаг 3: Выбираем победителя среди уникальных значений
        # Приоритет: LLM-валидированные > Majority Vote > максимальный confidence
        
        def _is_llm_validated(cand: ExtractedFactCandidate) -> bool:
            """Проверяет, является ли кандидат LLM-валидированным."""
            if not cand.meta_json:
                return False
            norm_data = cand.meta_json.get("value_normalization")
            if not norm_data:
                return False
            status = norm_data.get("status")
            if status == "validated":
                return True
            if status == "extracted":
                # Проверяем confidence LLM
                llm_confidence = norm_data.get("llm_confidence", 0)
                if llm_confidence > 0.8:
                    return True
            return False
        
        # Разделяем кандидатов на LLM-валидированные и остальные
        llm_validated_candidates = [c for c in best_candidates_by_value if _is_llm_validated(c)]
        regex_candidates = [c for c in best_candidates_by_value if not _is_llm_validated(c)]
        
        # Выбираем победителя
        if llm_validated_candidates:
            # Если есть LLM-валидированные, выбираем среди них по максимальному confidence
            best_candidate = max(llm_validated_candidates, key=lambda c: c.confidence)
            logger.info(
                f"Факт {fact_type}.{fact_key}: выбран LLM-валидированный кандидат "
                f"(confidence={best_candidate.confidence:.2f})"
            )
        elif regex_candidates:
            # Если нет LLM-валидированных, используем Majority Vote
            # Подсчитываем частоту появления каждого значения среди исходных кандидатов
            candidates_with_frequency: list[tuple[ExtractedFactCandidate, int]] = []
            for cand in regex_candidates:
                # Находим value_key для этого кандидата
                cand_normalized = self._normalize_value_for_comparison(cand.value_json)
                cand_value_key = json.dumps(cand_normalized, sort_keys=True) if cand_normalized is not None else ""
                # Подсчитываем, сколько раз это значение встречается среди исходных кандидатов
                frequency = len(candidates_by_value.get(cand_value_key, []))
                candidates_with_frequency.append((cand, frequency))
            
            # Находим максимальную частоту
            max_frequency = max(freq for _, freq in candidates_with_frequency)
            most_frequent_candidates = [c for c, freq in candidates_with_frequency if freq == max_frequency]
            
            if len(most_frequent_candidates) == 1:
                best_candidate = most_frequent_candidates[0]
            else:
                # Если несколько кандидатов с одинаковой частотой, выбираем по максимальному confidence
                best_candidate = max(most_frequent_candidates, key=lambda c: c.confidence)
            
            logger.info(
                f"Факт {fact_type}.{fact_key}: выбран кандидат по Majority Vote "
                f"(частота={max_frequency}, confidence={best_candidate.confidence:.2f})"
            )
        else:
            # Fallback: просто выбираем по максимальному confidence
            best_candidate = max(best_candidates_by_value, key=lambda c: c.confidence)
            logger.info(
                f"Факт {fact_type}.{fact_key}: выбран кандидат по максимальному confidence "
                f"(confidence={best_candidate.confidence:.2f})"
            )
        
        # Формируем список альтернатив (кандидаты с отличающимися значениями)
        alternatives = [
            c for c in best_candidates_by_value
            if c != best_candidate and not self._values_are_identical(best_candidate.value_json, c.value_json)
        ]
        
        return best_candidate, alternatives

    def _get_rule_priority(self, fact_type: str, fact_key: str) -> int:
        """Возвращает приоритет правила для данного fact_key."""
        for rule in self.rules:
            if rule.fact_type == fact_type and rule.fact_key == fact_key:
                return rule.priority
        return 100  # Дефолтный приоритет

    async def _load_anchors_for_fact_extraction(self, doc_version_id: UUID) -> list[Anchor]:
        """Загружает anchors для извлечения фактов."""
        allowed_types = [
            AnchorContentType.HDR,
            AnchorContentType.P,
            AnchorContentType.LI,
            AnchorContentType.FN,
        ]
        stmt = (
            select(Anchor)
            .where(Anchor.doc_version_id == doc_version_id)
            .where(Anchor.content_type.in_(allowed_types))
        )
        res = await self.db.execute(stmt)
        anchors = res.scalars().all()

        def _type_bucket(ct: AnchorContentType) -> int:
            # hdr first, then p/li/fn
            if ct == AnchorContentType.HDR:
                return 0
            return 1

        def _type_order(ct: AnchorContentType) -> int:
            # within non-hdr: p first, then li, then fn
            return {
                AnchorContentType.P: 0,
                AnchorContentType.LI: 1,
                AnchorContentType.FN: 2,
                AnchorContentType.HDR: 0,
            }.get(ct, 9)

        anchors.sort(key=lambda a: (_type_bucket(a.content_type), _type_order(a.content_type), a.ordinal))
        return anchors

    async def _load_topic_mappings(self, doc_version_id: UUID) -> dict[str, set[str]]:
        """
        Загружает маппинги топиков (target_section) на anchor_ids.
        
        Возвращает словарь: {target_section: set(anchor_ids)}
        """
        stmt = select(TargetSectionMap).where(
            TargetSectionMap.doc_version_id == doc_version_id
        )
        res = await self.db.execute(stmt)
        maps = res.scalars().all()
        
        topic_to_anchors: dict[str, set[str]] = {}
        for section_map in maps:
            if section_map.anchor_ids:
                topic = section_map.target_section
                if topic not in topic_to_anchors:
                    topic_to_anchors[topic] = set()
                topic_to_anchors[topic].update(section_map.anchor_ids)
        
        return topic_to_anchors

    async def _load_anchor_topic_mapping(self, doc_version_id: UUID) -> dict[str, set[str]]:
        """
        Загружает маппинг anchor_id -> set[topic_key] из topic_evidence.
        
        Возвращает словарь: {anchor_id: {topic_key1, topic_key2, ...}}
        """
        stmt = select(TopicEvidence).where(
            TopicEvidence.doc_version_id == doc_version_id
        )
        res = await self.db.execute(stmt)
        evidences = res.scalars().all()
        
        anchor_to_topics: dict[str, set[str]] = defaultdict(set)
        for evidence in evidences:
            if evidence.anchor_ids:
                for anchor_id in evidence.anchor_ids:
                    anchor_to_topics[anchor_id].add(evidence.topic_key)
        
        return dict(anchor_to_topics)

    def _extract_facts_from_anchors(
        self, 
        anchors: list[Anchor], 
        allowed_anchor_ids: set[str], 
        topic_mappings: dict[str, set[str]] | None = None,
        anchor_topic_mapping: dict[str, set[str]] | None = None
    ) -> list[ExtractedFactCandidate]:
        """
        Извлекает факты из anchors используя правила с логикой контекстного приоритета.
        
        Логика контекстного приоритета:
        1. Для каждого правила разделяем anchors на priority_anchors (с топиками из rule.preferred_topics) 
           и fallback_anchors (остальные)
        2. Сначала применяем regex-паттерны к priority_anchors
        3. Если совпадение найдено в приоритетном якоре - устанавливаем confidence = 0.95
        4. Если в приоритетных ничего не найдено, применяем правила к fallback_anchors с базовым confidence
        5. Если один и тот же факт найден в нескольких местах, побеждает вариант из priority_anchors
        """
        candidates: list[ExtractedFactCandidate] = []
        topic_mappings = topic_mappings or {}
        anchor_topic_mapping = anchor_topic_mapping or {}

        # Применяем каждое правило
        logger.info(f"Применяем {len(self.rules)} правил извлечения фактов")
        for rule in self.rules:
            logger.debug(f"Применяем правило: {rule.fact_type}.{rule.fact_key} (priority={rule.priority})")
            # Мета-факты (protocol_version, protocol_date, sponsor_name) ищем по всему документу,
            # игнорируя source_zone, так как они могут быть в любой части документа
            is_meta_fact = (
                rule.fact_type == "protocol_meta" 
                and rule.fact_key in ("protocol_version", "protocol_date", "sponsor_name")
            )
            
            # Разделяем anchors на priority_anchors и fallback_anchors
            priority_anchors: list[Anchor] = []
            fallback_anchors: list[Anchor] = []
            
            if is_meta_fact:
                # Для мета-фактов ищем по всему документу, все anchors идут в fallback
                # (но также проверяем preferred_topics для приоритета)
                if rule.preferred_topics and anchor_topic_mapping:
                    preferred_topic_set = set(rule.preferred_topics)
                    for anchor in anchors:
                        anchor_topics = anchor_topic_mapping.get(anchor.anchor_id, set())
                        # Если есть пересечение топиков anchor с preferred_topics правила
                        if preferred_topic_set & anchor_topics:
                            priority_anchors.append(anchor)
                        else:
                            # Для мета-фактов включаем все anchors, даже с source_zone == 'unknown'
                            fallback_anchors.append(anchor)
                else:
                    # Если у правила нет preferred_topics, все anchors идут в fallback
                    fallback_anchors = anchors
            elif rule.preferred_topics and anchor_topic_mapping:
                # Определяем, какие anchor_ids относятся к preferred_topics
                preferred_topic_set = set(rule.preferred_topics)
                for anchor in anchors:
                    anchor_topics = anchor_topic_mapping.get(anchor.anchor_id, set())
                    # Если есть пересечение топиков anchor с preferred_topics правила
                    if preferred_topic_set & anchor_topics:
                        priority_anchors.append(anchor)
                    else:
                        fallback_anchors.append(anchor)
            else:
                # Если у правила нет preferred_topics, все anchors идут в fallback
                fallback_anchors = anchors

            # Собираем кандидаты для этого правила
            priority_candidates: list[ExtractedFactCandidate] = []
            fallback_candidates: list[ExtractedFactCandidate] = []
            
            # ШАГ 1: Применяем regex-паттерны к priority_anchors
            logger.debug(f"Правило {rule.fact_type}.{rule.fact_key}: проверяем {len(priority_anchors)} приоритетных anchors")
            for anchor in priority_anchors:
                text = anchor.text_raw or anchor.text_norm
                if not text:
                    continue

                # Пробуем RU и EN паттерны
                for pattern in rule.patterns_ru + rule.patterns_en:
                    match = pattern.search(text)
                    if match:
                        logger.info(f"Правило {rule.fact_type}.{rule.fact_key}: найдено совпадение в приоритетном anchor {anchor.anchor_id[:50]}... | Текст: {text[:100]}...")
                        # Парсим значение
                        parsed = rule.parser(text, match)
                        if parsed is None:
                            continue

                        # Определяем, в каком preferred_topic найден факт
                        matched_topic = None
                        anchor_topics = anchor_topic_mapping.get(anchor.anchor_id, set())
                        preferred_topic_set = set(rule.preferred_topics or [])
                        # Находим первый пересекающийся топик
                        intersection = preferred_topic_set & anchor_topics
                        if intersection:
                            matched_topic = next(iter(intersection))
                        
                        # Устанавливаем confidence = 0.95 для фактов из priority_anchors
                        confidence = 0.95
                        
                        # Логируем boost
                        if matched_topic:
                            logger.info(
                                f"Fact {rule.fact_key} matched in preferred topic {matched_topic}. "
                                f"Confidence boosted to 0.95."
                            )

                        # Создаем кандидата
                        if match.lastindex is None or match.lastindex == 0:
                            raw_value = match.group(0)
                        elif match.lastindex >= 1:
                            raw_value = match.group(1)
                        else:
                            raw_value = None
                        
                        zone = anchor.source_zone or "unknown"
                        meta_json: dict[str, Any] = {
                            "source_zone": zone,
                            "matched_topic": matched_topic,
                            "confidence_boosted": True
                        } if matched_topic else {"source_zone": zone} if zone != "unknown" else {}
                        
                        candidate = ExtractedFactCandidate(
                            fact_type=rule.fact_type,
                            fact_key=rule.fact_key,
                            value_json=parsed,
                            raw_value=raw_value,
                            confidence=confidence,
                            evidence_anchor_ids=[anchor.anchor_id],
                            extractor_version=EXTRACTOR_VERSION,
                            meta_json=meta_json if meta_json else None,
                        )

                        priority_candidates.append(candidate)
                        # Прерываем после первого найденного совпадения в этом anchor
                        break

            # ШАГ 2: Если в приоритетных ничего не найдено, применяем правила к fallback_anchors
            if not priority_candidates:
                logger.debug(f"Правило {rule.fact_type}.{rule.fact_key}: совпадений в приоритетных не найдено, проверяем {len(fallback_anchors)} fallback anchors")
                for anchor in fallback_anchors:
                    text = anchor.text_raw or anchor.text_norm
                    if not text:
                        continue

                    # Пробуем RU и EN паттерны
                    for pattern in rule.patterns_ru + rule.patterns_en:
                        match = pattern.search(text)
                        if match:
                            logger.info(f"Правило {rule.fact_type}.{rule.fact_key}: найдено совпадение в fallback anchor {anchor.anchor_id[:50]}... | Текст: {text[:100]}...")
                            # Парсим значение
                            parsed = rule.parser(text, match)
                            if parsed is None:
                                continue

                            # Вычисляем confidence обычным способом (без boost)
                            confidence = rule.confidence_policy(text, match)

                            # Создаем кандидата
                            if match.lastindex is None or match.lastindex == 0:
                                raw_value = match.group(0)
                            elif match.lastindex >= 1:
                                raw_value = match.group(1)
                            else:
                                raw_value = None
                            
                            zone = anchor.source_zone or "unknown"
                            candidate = ExtractedFactCandidate(
                                fact_type=rule.fact_type,
                                fact_key=rule.fact_key,
                                value_json=parsed,
                                raw_value=raw_value,
                                confidence=confidence,
                                evidence_anchor_ids=[anchor.anchor_id],
                                extractor_version=EXTRACTOR_VERSION,
                                meta_json={"source_zone": zone} if zone != "unknown" else None,
                            )

                            fallback_candidates.append(candidate)
                            # Прерываем после первого найденного совпадения в этом anchor
                            break

            # ШАГ 3: Обрабатываем кандидаты - если один и тот же факт найден в нескольких местах, 
            # побеждает вариант из priority_anchors
            all_rule_candidates = priority_candidates + fallback_candidates
            if all_rule_candidates:
                logger.info(f"Правило {rule.fact_type}.{rule.fact_key}: найдено {len(all_rule_candidates)} кандидатов ({len(priority_candidates)} приоритетных, {len(fallback_candidates)} fallback)")
            else:
                logger.debug(f"Правило {rule.fact_type}.{rule.fact_key}: совпадений не найдено")
            if all_rule_candidates:
                # Группируем кандидаты по нормализованному значению (для обнаружения дубликатов)
                # Используем нормализацию, чтобы '64' и 64 считались одинаковыми
                candidates_by_value: dict[str, list[ExtractedFactCandidate]] = defaultdict(list)
                for cand in all_rule_candidates:
                    # Нормализуем значение для группировки
                    normalized_val = self._normalize_value_for_comparison(cand.value_json)
                    value_key = json.dumps(normalized_val, sort_keys=True) if normalized_val is not None else ""
                    candidates_by_value[value_key].append(cand)
                
                # Для каждого уникального значения выбираем лучшего кандидата
                for value_key, value_candidates in candidates_by_value.items():
                    # Разделяем на приоритетные и fallback
                    priority_cands = [
                        c for c in value_candidates 
                        if c.meta_json and c.meta_json.get("matched_topic")
                    ]
                    fallback_cands = [
                        c for c in value_candidates 
                        if not (c.meta_json and c.meta_json.get("matched_topic"))
                    ]
                    
                    # Если есть приоритетные - всегда выбираем лучшего из них (даже если есть fallback с тем же значением)
                    if priority_cands:
                        # Сортируем по confidence и выбираем лучшего
                        best_candidate = max(priority_cands, key=lambda c: c.confidence)
                        candidates.append(best_candidate)
                    # Иначе выбираем лучшего из fallback
                    elif fallback_cands:
                        best_candidate = max(fallback_cands, key=lambda c: c.confidence)
                        candidates.append(best_candidate)

        return candidates

    def _extract_endpoints(
        self, anchors: list[Anchor], allowed_anchor_ids: set[str]
    ) -> list[ExtractedFactCandidate]:
        """Специальная обработка для извлечения endpoints (массивы)."""
        candidates: list[ExtractedFactCandidate] = []

        # Паттерны для заголовков endpoints
        primary_headers_ru = [
            re.compile(r"\b(?:первичная\s+конечная\s+точка|первичная\s+конечная\s+точка\s+эффективности|primary\s+endpoint)", re.IGNORECASE),
        ]
        primary_headers_en = [
            re.compile(r"\b(?:primary\s+endpoint|primary\s+efficacy\s+endpoint)", re.IGNORECASE),
        ]
        secondary_headers_ru = [
            re.compile(r"\b(?:вторичные\s+конечные\s+точки|вторичные\s+конечные\s+точки\s+эффективности|secondary\s+endpoints)", re.IGNORECASE),
        ]
        secondary_headers_en = [
            re.compile(r"\b(?:secondary\s+endpoints|secondary\s+efficacy\s+endpoints)", re.IGNORECASE),
        ]

        # Ищем заголовки и извлекаем следующие элементы списка
        for i, anchor in enumerate(anchors):
            text = anchor.text_raw or anchor.text_norm
            if not text:
                continue

            # Проверяем, является ли это заголовком primary endpoint
            is_primary = any(p.search(text) for p in primary_headers_ru + primary_headers_en)
            is_secondary = any(p.search(text) for p in secondary_headers_ru + secondary_headers_en)

            if is_primary or is_secondary:
                endpoint_type = "primary" if is_primary else "secondary"
                endpoint_values: list[str] = []
                endpoint_anchor_ids: list[str] = [anchor.anchor_id]

                # Собираем следующие 1-3 элемента списка/параграфа до следующего заголовка
                for j in range(i + 1, min(i + 4, len(anchors))):
                    next_anchor = anchors[j]
                    next_text = next_anchor.text_raw or next_anchor.text_norm

                    # Если следующий anchor - заголовок, останавливаемся
                    if next_anchor.content_type == AnchorContentType.HDR:
                        break

                    # Если это элемент списка или параграф, добавляем
                    if next_anchor.content_type in (AnchorContentType.LI, AnchorContentType.P):
                        cleaned = re.sub(r"^[•\-\d+\.\)]\s*", "", next_text.strip())
                        if cleaned:
                            endpoint_values.append(cleaned)
                            endpoint_anchor_ids.append(next_anchor.anchor_id)

                if endpoint_values:
                    candidate = ExtractedFactCandidate(
                        fact_type="endpoints",
                        fact_key=endpoint_type,
                        value_json={"value": endpoint_values},
                        raw_value=None,
                        confidence=0.8,
                        evidence_anchor_ids=endpoint_anchor_ids,
                        extractor_version=EXTRACTOR_VERSION,
                        meta_json={"count": len(endpoint_values)},
                    )
                    candidates.append(candidate)

        return candidates

    def _extract_analysis_populations(
        self, anchors: list[Anchor], allowed_anchor_ids: set[str]
    ) -> list[ExtractedFactCandidate]:
        """Специальная обработка для извлечения analysis_populations (массив)."""
        candidates: list[ExtractedFactCandidate] = []

        # Паттерны для заголовков analysis populations
        headers_ru = [
            re.compile(r"\b(?:популяции\s+анализа|анализ\s+популяций|analysis\s+populations)", re.IGNORECASE),
        ]
        headers_en = [
            re.compile(r"\b(?:analysis\s+populations|populations\s+for\s+analysis)", re.IGNORECASE),
        ]

        # Паттерны для конкретных популяций
        pop_patterns = [
            (r"\b(?:ITT|intent\s*-\s*to\s*-\s*treat|намерение\s+лечить)", "ITT"),
            (r"\b(?:PP|per\s*-\s*protocol|по\s+протоколу)", "PP"),
            (r"\b(?:safety\s+set|безопасность|набор\s+безопасности)", "Safety set"),
            (r"\b(?:full\s+analysis\s+set|FAS|полный\s+набор\s+анализа)", "FAS"),
        ]

        # Ищем заголовки и извлекаем упоминания популяций
        found_header = False
        populations: list[str] = []
        population_anchor_ids: list[str] = []

        for anchor in anchors:
            text = anchor.text_raw or anchor.text_norm
            if not text:
                continue

            # Проверяем, является ли это заголовком
            is_header = any(p.search(text) for p in headers_ru + headers_en)
            if is_header:
                found_header = True
                population_anchor_ids.append(anchor.anchor_id)
                continue

            # Если нашли заголовок, ищем упоминания популяций в следующих элементах
            if found_header:
                # Если следующий заголовок - останавливаемся
                if anchor.content_type == AnchorContentType.HDR:
                    break

                # Ищем упоминания популяций
                for pattern, pop_name in pop_patterns:
                    if re.search(pattern, text, re.IGNORECASE):
                        if pop_name not in populations:
                            populations.append(pop_name)
                            population_anchor_ids.append(anchor.anchor_id)

        if populations:
            candidate = ExtractedFactCandidate(
                fact_type="statistics",
                fact_key="analysis_populations",
                value_json={"value": populations},
                raw_value=None,
                confidence=0.8,
                evidence_anchor_ids=population_anchor_ids,
                extractor_version=EXTRACTOR_VERSION,
                meta_json={"count": len(populations)},
            )
            candidates.append(candidate)

        return candidates

    async def _normalize_fact_value(
        self,
        candidate: ExtractedFactCandidate,
        anchors: list[Anchor],
    ) -> ExtractedFactCandidate | None:
        """
        Применяет Value Normalizer для двойной проверки значения факта (GxP).
        
        Returns:
            Обновленный кандидат с нормализованным значением и статусом, или None если не требуется нормализация
        """
        # Получаем текст фрагмента из первого evidence anchor
        text_fragment = ""
        if candidate.evidence_anchor_ids:
            anchor_id = candidate.evidence_anchor_ids[0]
            for anchor in anchors:
                if anchor.anchor_id == anchor_id:
                    text_fragment = anchor.text_raw or anchor.text_norm or ""
                    break
        
        if not text_fragment:
            # Если не нашли текст, используем raw_value как fallback
            text_fragment = candidate.raw_value or ""
        
        # Применяем нормализатор
        normalization_result = await self.value_normalizer.normalize_value(
            candidate=candidate,
            text_fragment=text_fragment,
        )
        
        # Если статус изменился (validated или conflicting), обновляем кандидата
        # Также обрабатываем случай, когда LLM вернула пустое значение (статус EXTRACTED)
        if normalization_result.status in (FactStatus.VALIDATED, FactStatus.CONFLICTING, FactStatus.EXTRACTED):
            # Обновляем meta_json с информацией о нормализации
            meta_json = candidate.meta_json.copy() if candidate.meta_json else {}
            
            # Определяем, является ли поле приоритетным для Smart Choice
            # (endpoints, ip_name, sponsor_name, comparator_name, study_title - где регулярки часто ошибаются)
            is_smart_choice_field = (
                candidate.fact_type == "endpoints"
                or (candidate.fact_type == "study" and candidate.fact_key == "study_title")
                or (candidate.fact_type == "treatment" and candidate.fact_key == "ip_name")
                or (candidate.fact_type == "treatment" and candidate.fact_key == "comparator_name")
                or (candidate.fact_type == "protocol_meta" and candidate.fact_key == "sponsor_name")
            )
            
            # Проверяем, что LLM вернула не пустое значение
            def _is_llm_value_empty(llm_value: dict[str, Any] | None) -> bool:
                """Проверяет, является ли значение LLM пустым."""
                if llm_value is None:
                    return True
                # Проверяем, есть ли ключ "value" и он не пустой
                if "value" in llm_value:
                    val = llm_value["value"]
                    if val is None:
                        return True
                    if isinstance(val, str) and not val.strip():
                        return True
                    if isinstance(val, list) and len(val) == 0:
                        return True
                    if isinstance(val, dict) and len(val) == 0:
                        return True
                # Если структура не содержит "value", проверяем сам словарь
                if isinstance(llm_value, dict) and len(llm_value) == 0:
                    return True
                return False
            
            llm_is_empty = _is_llm_value_empty(normalization_result.llm_value)
            regex_has_value = normalization_result.normalized_value is not None
            
            # Инициализируем final_value значением по умолчанию
            final_value = normalization_result.normalized_value or candidate.value_json
            
            # Если LLM вернула пустое значение, а regex нашел значение
            # Для Smart Choice полей (ip_name, sponsor_name) это подозрительно - помечаем как needs_review
            # Для остальных полей используем regex-результат со статусом EXTRACTED
            if llm_is_empty and regex_has_value:
                if is_smart_choice_field:
                    # Для Smart Choice полей, если LLM пустая, а regex нашел - это подозрительно
                    meta_json["value_normalization"] = {
                        "status": FactStatus.NEEDS_REVIEW.value,
                        "match": False,
                        "llm_value": normalization_result.llm_value,
                        "llm_confidence": normalization_result.llm_confidence,
                        "regex_value": normalization_result.normalized_value or candidate.value_json,
                        "warning": "LLM вернула пустое значение, но Regex нашел значение - требуется проверка",
                    }
                    final_value = normalization_result.normalized_value or candidate.value_json
                    logger.warning(
                        f"Для Smart Choice поля {candidate.fact_type}.{candidate.fact_key}: "
                        f"LLM вернула пустое значение, но Regex нашел значение. Статус: needs_review"
                    )
                elif normalization_result.status == FactStatus.CONFLICTING:
                    # Для остальных полей, если статус CONFLICTING, используем regex-результат со статусом EXTRACTED
                    meta_json["value_normalization"] = {
                        "status": FactStatus.EXTRACTED.value,
                        "match": False,
                        "llm_value": normalization_result.llm_value,
                        "llm_confidence": normalization_result.llm_confidence,
                        "regex_value": normalization_result.normalized_value or candidate.value_json,
                        "note": "LLM вернула пустое значение, используется regex-результат",
                    }
                    final_value = normalization_result.normalized_value or candidate.value_json
                    logger.info(
                        f"Для факта {candidate.fact_type}.{candidate.fact_key}: "
                        f"LLM вернула пустое значение, используем regex-результат со статусом EXTRACTED"
                    )
                # Если статус уже EXTRACTED (из value_normalizer), используем стандартную логику ниже
            # Smart Choice: если значения не совпадают и это приоритетное поле (и LLM не пустая)
            elif not normalization_result.match and is_smart_choice_field and not llm_is_empty:
                regex_value = normalization_result.normalized_value or candidate.value_json
                llm_value = normalization_result.llm_value
                
                # Если LLM вернула не пустую строку - принимаем её как основное
                if not llm_is_empty:
                    meta_json["value_normalization"] = {
                        "status": FactStatus.EXTRACTED.value,  # Устанавливаем extracted вместо needs_review
                        "match": False,
                        "llm_value": llm_value,
                        "llm_confidence": normalization_result.llm_confidence,
                        "regex_rejected": regex_value,  # Сохраняем regex-результат для истории
                        "llm_priority": True,  # Флаг для _upsert_fact
                    }
                    
                    logger.info(
                        f"Smart Choice для поля {candidate.fact_type}.{candidate.fact_key}: "
                        f"LLM вернула не пустое значение (confidence={normalization_result.llm_confidence:.2f}), "
                        f"принимаем значение LLM, regex сохранен в regex_rejected"
                    )
                    
                    # Используем значение LLM как основное
                    final_value = llm_value
                # Если LLM вернула пустую строку, а Regex что-то нашел - оставляем needs_review
                elif llm_is_empty and regex_has_value:
                    meta_json["value_normalization"] = {
                        "status": FactStatus.NEEDS_REVIEW.value,  # Подозрительно: LLM пустая, а Regex нашел
                        "match": False,
                        "llm_value": llm_value,
                        "llm_confidence": normalization_result.llm_confidence,
                        "regex_value": regex_value,
                        "warning": "LLM вернула пустое значение, но Regex нашел значение - требуется проверка",
                    }
                    
                    logger.warning(
                        f"Подозрительная ситуация для поля {candidate.fact_type}.{candidate.fact_key}: "
                        f"LLM вернула пустое значение, но Regex нашел значение. Статус: needs_review"
                    )
                    
                    # Используем regex-результат, но со статусом needs_review
                    final_value = regex_value
                else:
                    # Стандартная логика для остальных случаев
                    meta_json["value_normalization"] = {
                        "status": normalization_result.status.value,
                        "match": normalization_result.match,
                        "llm_value": normalization_result.llm_value,
                        "llm_confidence": normalization_result.llm_confidence,
                    }
                    final_value = normalization_result.normalized_value or candidate.value_json
            else:
                # Стандартная логика: используем regex-результат
                # Если статус CONFLICTING, но LLM вернула пустое значение - это не конфликт
                result_status = normalization_result.status
                if result_status == FactStatus.CONFLICTING and llm_is_empty and regex_has_value:
                    result_status = FactStatus.EXTRACTED
                    meta_json["value_normalization"] = {
                        "status": FactStatus.EXTRACTED.value,
                        "match": False,
                        "llm_value": normalization_result.llm_value,
                        "llm_confidence": normalization_result.llm_confidence,
                        "regex_value": normalization_result.normalized_value or candidate.value_json,
                        "note": "LLM вернула пустое значение или ошибку, используется regex-результат",
                    }
                    logger.info(
                        f"Для факта {candidate.fact_type}.{candidate.fact_key}: "
                        f"LLM вернула пустое значение или ошибку, используем regex-результат со статусом EXTRACTED"
                    )
                else:
                    meta_json["value_normalization"] = {
                        "status": result_status.value,
                        "match": normalization_result.match,
                        "llm_value": normalization_result.llm_value,
                        "llm_confidence": normalization_result.llm_confidence,
                    }
                final_value = normalization_result.normalized_value or candidate.value_json
            
            # Создаем обновленный кандидат с новым статусом
            updated_candidate = ExtractedFactCandidate(
                fact_type=candidate.fact_type,
                fact_key=candidate.fact_key,
                value_json=final_value,
                raw_value=candidate.raw_value,
                confidence=candidate.confidence,
                evidence_anchor_ids=candidate.evidence_anchor_ids,
                extractor_version=candidate.extractor_version,
                meta_json=meta_json,
            )
            return updated_candidate
        
        return None

    def _normalize_value_for_comparison(self, value: Any) -> Any:
        """
        Нормализует значение для сравнения (приводит типы: N=64 и N="64" считаются одинаковыми).
        Также нормализует даты к ISO формату.
        
        Returns:
            Нормализованное значение для сравнения
        """
        if value is None:
            return None
        
        # Если это словарь, нормализуем значение по ключу "value"
        if isinstance(value, dict):
            if "value" in value:
                return self._normalize_value_for_comparison(value["value"])
            # Если словарь не содержит "value", нормализуем сам словарь
            return {k: self._normalize_value_for_comparison(v) for k, v in value.items()}
        
        # Если это список, нормализуем каждый элемент
        if isinstance(value, list):
            return [self._normalize_value_for_comparison(item) for item in value]
        
        # Приводим строки с числами к числам
        if isinstance(value, str):
            # Сначала пробуем нормализовать дату к ISO формату
            normalized_date = self._normalize_date_to_iso_for_comparison(value)
            if normalized_date:
                return normalized_date
            
            # Пробуем преобразовать в int
            try:
                # Убираем пробелы и запятые
                cleaned = value.strip().replace("\u00a0", " ").replace(" ", "").replace(",", "")
                if cleaned.isdigit():
                    return int(cleaned)
            except (ValueError, AttributeError):
                pass
            
            # Пробуем преобразовать в float
            try:
                cleaned = value.strip().replace("\u00a0", " ").replace(",", ".")
                float_val = float(cleaned)
                # Проверяем, что это действительно число, а не просто строка с точкой
                if cleaned.replace(".", "").replace("-", "").isdigit():
                    return float_val
            except (ValueError, AttributeError):
                pass
            
            # Возвращаем нормализованную строку (убираем лишние пробелы)
            return value.strip().lower() if value else None
        
        return value
    
    def _normalize_date_to_iso_for_comparison(self, date_str: str) -> str | None:
        """Нормализует дату к ISO формату YYYY-MM-DD для сравнения.
        
        Поддерживает форматы:
        - DD.MM.YYYY -> YYYY-MM-DD
        - DD/MM/YYYY -> YYYY-MM-DD
        - YYYY-MM-DD -> YYYY-MM-DD (уже ISO)
        - "12 апреля 2010" -> YYYY-MM-DD
        """
        if not date_str or not isinstance(date_str, str):
            return None
        
        from datetime import date
        
        s = date_str.strip()
        if not s:
            return None
        
        # ISO формат (уже нормализован)
        m = re.match(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$", s)
        if m:
            try:
                dt = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
                return dt.isoformat()
            except ValueError:
                pass
        
        # DD.MM.YYYY or DD/MM/YYYY
        m = re.match(r"^(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})$", s)
        if m:
            try:
                dt = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
                return dt.isoformat()
            except ValueError:
                pass
        
        # "12 апреля 2010" / "12 April 2010"
        m = re.search(r"(?P<d>\d{1,2})\s+(?P<mon>[A-Za-zА-Яа-яёЁ]+)\s+(?P<y>\d{4})", s)
        if m:
            mon = self._month_to_int_for_comparison(m.group("mon"))
            if mon is not None:
                try:
                    dt = date(int(m.group("y")), mon, int(m.group("d")))
                    return dt.isoformat()
                except ValueError:
                    pass
        
        return None
    
    def _month_to_int_for_comparison(self, mon: str) -> int | None:
        """Преобразует название месяца в число."""
        t = (mon or "").strip().lower().replace(".", "")
        ru = {
            "января": 1, "янв": 1, "февраля": 2, "фев": 2, "марта": 3, "мар": 3,
            "апреля": 4, "апр": 4, "мая": 5, "май": 5, "июня": 6, "июн": 6,
            "июля": 7, "июл": 7, "августа": 8, "авг": 8, "сентября": 9, "сен": 9, "сент": 9,
            "октября": 10, "окт": 10, "ноября": 11, "ноя": 11, "декабря": 12, "дек": 12,
        }
        en = {
            "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
            "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
            "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
            "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
        }
        return ru.get(t) or en.get(t)

    def _values_are_identical(self, val1: Any, val2: Any) -> bool:
        """
        Проверяет, являются ли два значения идентичными после нормализации.
        
        Args:
            val1: Первое значение
            val2: Второе значение
            
        Returns:
            True, если значения идентичны после нормализации
        """
        norm1 = self._normalize_value_for_comparison(val1)
        norm2 = self._normalize_value_for_comparison(val2)
        
        # Сравниваем нормализованные значения
        if norm1 == norm2:
            return True
        
        # Дополнительная проверка для словарей (сравниваем как JSON)
        if isinstance(norm1, dict) and isinstance(norm2, dict):
            # Сравниваем ключи и значения
            if set(norm1.keys()) != set(norm2.keys()):
                return False
            return all(self._values_are_identical(norm1[k], norm2[k]) for k in norm1.keys())
        
        # Дополнительная проверка для списков
        if isinstance(norm1, list) and isinstance(norm2, list):
            if len(norm1) != len(norm2):
                return False
            return all(self._values_are_identical(norm1[i], norm2[i]) for i in range(len(norm1)))
        
        return False

    async def _count_documents_in_study(self, study_id: UUID) -> int:
        """
        Подсчитывает количество документов в исследовании.
        
        Returns:
            Количество документов в исследовании
        """
        stmt = select(Document).where(Document.study_id == study_id)
        res = await self.db.execute(stmt)
        documents = res.scalars().all()
        return len(documents)

    async def _upsert_fact(
        self,
        *,
        study_id: UUID,
        doc_version_id: UUID,
        fact_type: str,
        fact_key: str,
        candidate: ExtractedFactCandidate,
        alternatives: list[ExtractedFactCandidate],
    ) -> Fact:
        """Upsert факта с поддержкой множественных кандидатов."""
        stmt = select(Fact).where(
            Fact.study_id == study_id,
            Fact.fact_type == fact_type,
            Fact.fact_key == fact_key,
        )
        res = await self.db.execute(stmt)
        existing = res.scalar_one_or_none()

        # Проверяем количество документов в исследовании
        documents_count = await self._count_documents_in_study(study_id)
        is_single_document = documents_count <= 1

        # Подготавливаем meta_json с альтернативами
        meta_json: dict[str, Any] = candidate.meta_json.copy() if candidate.meta_json else {}
        if alternatives:
            meta_json["alternatives"] = [
                {
                    "value": alt.value_json,
                    "confidence": alt.confidence,
                    "raw_value": alt.raw_value,
                }
                for alt in alternatives
            ]

        # Определяем статус: проверяем конфликты между кандидатами
        # Если все альтернативы имеют одинаковое значение - это не конфликт
        has_real_conflict_in_alternatives = False
        if alternatives:
            candidate_value = candidate.value_json
            # Проверяем, есть ли альтернативы с отличающимися значениями
            for alt in alternatives:
                if not self._values_are_identical(candidate_value, alt.value_json):
                    has_real_conflict_in_alternatives = True
                    break

        # Проверяем конфликт с существующим фактом
        has_conflict_with_existing = False
        if existing:
            if not self._values_are_identical(existing.value_json, candidate.value_json):
                has_conflict_with_existing = True
                # Логируем конфликт
                logger.info(
                    f"Конфликт {fact_type}.{fact_key}: существующее значение {existing.value_json} "
                    f"vs новое значение {candidate.value_json}"
                )
            else:
                # Значения идентичны - это не конфликт
                logger.info(
                    f"Факт {fact_type}.{fact_key} прошел валидацию успешно: "
                    f"значение {candidate.value_json} совпадает с существующим"
                )

        # Определяем статус
        # Если есть реальный конфликт между альтернативами И это не единственный документ
        if has_real_conflict_in_alternatives and not is_single_document:
            status = FactStatus.CONFLICTING
            logger.info(
                f"Факт {fact_type}.{fact_key} помечен как CONFLICTING: "
                f"обнаружен конфликт между альтернативами"
            )
        # Если есть конфликт с существующим фактом И это не единственный документ
        elif has_conflict_with_existing and not is_single_document:
            status = FactStatus.CONFLICTING
            logger.info(
                f"Факт {fact_type}.{fact_key} помечен как CONFLICTING: "
                f"конфликт с существующим фактом из другого документа"
            )
        elif candidate.meta_json and "value_normalization" in candidate.meta_json:
            # Проверяем, установлен ли приоритет LLM
            norm_data = candidate.meta_json["value_normalization"]
            if norm_data.get("llm_priority", False):
                # Если установлен приоритет LLM, используем статус 'extracted' вместо 'conflicting'
                status = FactStatus.EXTRACTED
                logger.info(
                    f"Установлен статус 'extracted' для факта {fact_type}.{fact_key} "
                    f"с приоритетом LLM (confidence={norm_data.get('llm_confidence', 0):.2f})"
                )
            else:
                # Используем статус из нормализатора
                norm_status = norm_data.get("status")
                if norm_status:
                    try:
                        status = FactStatus(norm_status)
                        # Если статус CONFLICTING, проверяем условия:
                        # 1. Если LLM вернула пустое значение - это не конфликт, используем EXTRACTED
                        # 2. Если это единственный документ - это не конфликт, используем EXTRACTED
                        if status == FactStatus.CONFLICTING:
                            llm_value = norm_data.get("llm_value")
                            # Проверяем, является ли значение LLM пустым
                            llm_is_empty = (
                                llm_value is None
                                or (isinstance(llm_value, dict) and len(llm_value) == 0)
                                or (isinstance(llm_value, dict) and "value" in llm_value and (
                                    llm_value["value"] is None
                                    or (isinstance(llm_value["value"], str) and not llm_value["value"].strip())
                                    or (isinstance(llm_value["value"], list) and len(llm_value["value"]) == 0)
                                    or (isinstance(llm_value["value"], dict) and len(llm_value["value"]) == 0)
                                ))
                            )
                            if llm_is_empty or is_single_document:
                                # Если LLM вернула пустое значение или это единственный документ,
                                # а regex нашел значение - это не конфликт
                                status = FactStatus.EXTRACTED
                                reason = "LLM вернула пустое значение" if llm_is_empty else "единственный документ в исследовании"
                                logger.info(
                                    f"Для факта {fact_type}.{fact_key}: {reason}, "
                                    f"используем статус EXTRACTED вместо CONFLICTING"
                                )
                    except ValueError:
                        # Если статус невалиден, используем стандартную логику
                        status = FactStatus.EXTRACTED if candidate.confidence >= 0.7 else FactStatus.NEEDS_REVIEW
                else:
                    status = FactStatus.EXTRACTED if candidate.confidence >= 0.7 else FactStatus.NEEDS_REVIEW
        elif candidate.value_json.get("value") is not None:
            # Проверяем флаг needs_review в value_json (для ip_name с общими значениями)
            if candidate.value_json.get("needs_review", False):
                status = FactStatus.NEEDS_REVIEW
            else:
                status = FactStatus.EXTRACTED if candidate.confidence >= 0.7 else FactStatus.NEEDS_REVIEW
        else:
            status = FactStatus.NEEDS_REVIEW

        # Логируем успешное создание нового факта
        if not existing and status == FactStatus.EXTRACTED:
            logger.info(
                f"Факт {fact_type}.{fact_key} прошел валидацию успешно: "
                f"новое значение {candidate.value_json} с confidence={candidate.confidence:.2f}"
            )

        # Определяем type_category на основе fact_type
        type_category = _get_type_category_from_fact_type(fact_type)

        if existing:
            existing.value_json = candidate.value_json
            existing.confidence = candidate.confidence
            existing.extractor_version = candidate.extractor_version
            existing.meta_json = meta_json if meta_json else None
            existing.status = status
            existing.type_category = type_category
            existing.created_from_doc_version_id = doc_version_id
            await self.db.flush()
            return existing

        fact = Fact(
            study_id=study_id,
            fact_type=fact_type,
            fact_key=fact_key,
            value_json=candidate.value_json,
            confidence=candidate.confidence,
            extractor_version=candidate.extractor_version,
            meta_json=meta_json if meta_json else None,
            status=status,
            type_category=type_category,
            created_from_doc_version_id=doc_version_id,
        )
        self.db.add(fact)
        try:
            await self.db.flush()
            return fact
        except IntegrityError:
            # Возможная гонка при уникальном индексе (study_id, fact_type, fact_key):
            # параллельная транзакция уже успела вставить факт.
            await self.db.rollback()
            res = await self.db.execute(
                select(Fact).where(
                    Fact.study_id == study_id,
                    Fact.fact_type == fact_type,
                    Fact.fact_key == fact_key,
                )
            )
            existing_after = res.scalar_one_or_none()
            if existing_after is None:
                # Если по каким‑то причинам факт так и не найден, пробрасываем исходную ошибку.
                raise
            return existing_after

    async def _replace_evidence_for_fact(
        self,
        *,
        fact_id: UUID,
        evidence_anchor_ids: list[str],
        allowed_anchor_ids: set[str],
    ) -> None:
        """Идемпотентно заменяет evidence для факта."""
        await self.db.execute(delete(FactEvidence).where(FactEvidence.fact_id == fact_id))

        # Фильтруем только реальные anchor_ids
        valid_anchor_ids = [aid for aid in evidence_anchor_ids if aid in allowed_anchor_ids]
        valid_anchor_ids = _dedupe_keep_order(valid_anchor_ids)

        # Все evidence помечаем как PRIMARY (можно расширить логику)
        for aid in valid_anchor_ids:
            self.db.add(FactEvidence(fact_id=fact_id, anchor_id=aid, evidence_role=EvidenceRole.PRIMARY))

        await self.db.flush()


def _dedupe_keep_order(items: list[str]) -> list[str]:
    """Удаляет дубликаты, сохраняя порядок."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out
