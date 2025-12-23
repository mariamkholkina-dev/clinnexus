"""Сервис для извлечения и сохранения фактов из документа (rules-first подход)."""

from __future__ import annotations

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
from app.db.models.studies import DocumentVersion
from app.services.fact_extraction_rules import (
    EXTRACTOR_VERSION,
    ExtractionRule,
    ExtractedFactCandidate,
    get_extraction_rules,
)


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


class FactExtractionService:
    """Сервис для извлечения и сохранения фактов из документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._rules_cache: list[ExtractionRule] | None = None

    @property
    def rules(self) -> list[ExtractionRule]:
        """Кэшированный список правил извлечения."""
        if self._rules_cache is None:
            self._rules_cache = get_extraction_rules()
        return self._rules_cache

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
        allowed_anchor_ids = {a.anchor_id for a in anchors}

        # Извлекаем факты через правила
        all_candidates = self._extract_facts_from_anchors(anchors, allowed_anchor_ids)

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

        # Upsert фактов (поддерживаем множественные кандидаты)
        upserted: list[Fact] = []
        for (fact_type, fact_key), candidates in candidates_by_key.items():
            # Сортируем кандидатов по confidence и priority
            candidates_sorted = sorted(
                candidates,
                key=lambda c: (c.confidence, -self._get_rule_priority(fact_type, fact_key)),
                reverse=True,
            )

            # Берем лучшего кандидата, но сохраняем альтернативы в meta_json
            best_candidate = candidates_sorted[0]
            alternatives = candidates_sorted[1:] if len(candidates_sorted) > 1 else []

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

    def _extract_facts_from_anchors(
        self, anchors: list[Anchor], allowed_anchor_ids: set[str]
    ) -> list[ExtractedFactCandidate]:
        """Извлекает факты из anchors используя правила."""
        candidates: list[ExtractedFactCandidate] = []

        # Группируем anchors по source_zone для приоритизации
        anchors_by_zone: dict[str, list[Anchor]] = defaultdict(list)
        for anchor in anchors:
            zone = anchor.source_zone or "unknown"
            anchors_by_zone[zone].append(anchor)

        # Применяем каждое правило
        for rule in self.rules:
            # Определяем приоритетные зоны для этого правила
            preferred_zones = rule.preferred_source_zones or []
            all_zones = preferred_zones + [z for z in anchors_by_zone.keys() if z not in preferred_zones]

            # Ищем совпадения в приоритетных зонах первыми
            found = False
            for zone in all_zones:
                zone_anchors = anchors_by_zone[zone]
                for anchor in zone_anchors:
                    text = anchor.text_raw or anchor.text_norm
                    if not text:
                        continue

                    # Пробуем RU и EN паттерны
                    for pattern in rule.patterns_ru + rule.patterns_en:
                        match = pattern.search(text)
                        if match:
                            # Парсим значение
                            parsed = rule.parser(text, match)
                            if parsed is None:
                                continue

                            # Вычисляем confidence
                            confidence = rule.confidence_policy(text, match)

                            # Определяем статус
                            status = FactStatus.EXTRACTED if confidence >= 0.7 else FactStatus.NEEDS_REVIEW

                            # Создаем кандидата
                            # match.lastindex может быть None, если в regex нет групп
                            if match.lastindex is None or match.lastindex == 0:
                                raw_value = match.group(0)
                            elif match.lastindex >= 1:
                                raw_value = match.group(1)
                            else:
                                raw_value = None
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

                            candidates.append(candidate)
                            found = True
                            break  # Нашли совпадение, переходим к следующему правилу

                    if found:
                        break  # Нашли совпадение для этого правила

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

        # Определяем статус: если есть конфликты (альтернативы), помечаем как conflicting
        if alternatives:
            status = FactStatus.CONFLICTING
        elif candidate.value_json.get("value") is not None:
            status = FactStatus.EXTRACTED if candidate.confidence >= 0.7 else FactStatus.NEEDS_REVIEW
        else:
            status = FactStatus.NEEDS_REVIEW

        if existing:
            existing.value_json = candidate.value_json
            existing.confidence = candidate.confidence
            existing.extractor_version = candidate.extractor_version
            existing.meta_json = meta_json if meta_json else None
            existing.status = status
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
