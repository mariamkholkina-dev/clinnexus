from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact, FactEvidence
from app.schemas.fact_conflicts import (
    CanonicalFactValue,
    ConflictDetectionResult,
    FactConflict,
    FactEvidence as FactEvidenceSchema,
)

# Порог уверенности для определения высокоуверенных значений
CONFIDENCE_THRESHOLD = 0.7

# Маркеры неопределённости в тексте (RU/EN)
UNCERTAINTY_MARKERS_RU = {
    "примерно",
    "около",
    "приблизительно",
    "~",
    "порядка",
    "до",
    "не более",
    "не менее",
}
UNCERTAINTY_MARKERS_EN = {
    "approximately",
    "about",
    "around",
    "~",
    "up to",
    "up to",
    "less than",
    "more than",
    "nearly",
}


class FactConflictDetector:
    """Детектор конфликтов фактов между различными зонами документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def detect(
        self,
        study_id: UUID,
        *,
        doc_version_ids: list[UUID] | None = None,
        prefer_source_zones: list[str] | None = None,
    ) -> ConflictDetectionResult:
        """
        Обнаруживает конфликты фактов для указанного исследования.

        Args:
            study_id: ID исследования для проверки
            doc_version_ids: Опциональный список версий документов для ограничения проверки
            prefer_source_zones: Приоритетные зоны для авторазрешения конфликтов

        Returns:
            ConflictDetectionResult с обнаруженными конфликтами
        """
        logger.info(f"Обнаружение конфликтов фактов для study_id={study_id}")

        # Получаем все факты для исследования
        stmt = select(Fact).where(Fact.study_id == study_id)
        if doc_version_ids:
            stmt = stmt.where(Fact.created_from_doc_version_id.in_(doc_version_ids))
        res = await self.db.execute(stmt)
        facts = res.scalars().all()

        if not facts:
            return ConflictDetectionResult()

        # Группируем факты по fact_key
        facts_by_key: dict[str, list[Fact]] = {}
        for fact in facts:
            if fact.fact_key not in facts_by_key:
                facts_by_key[fact.fact_key] = []
            facts_by_key[fact.fact_key].append(fact)

        conflicts: list[FactConflict] = []

        # Проверяем каждый fact_key на конфликты
        for fact_key, fact_list in facts_by_key.items():
            if len(fact_list) < 2:
                continue  # Нужно минимум 2 факта для конфликта

            # Собираем доказательства для каждого факта
            evidence_list: list[FactEvidenceSchema] = []
            for fact in fact_list:
                evidence = await self._collect_evidence_for_fact(fact)
                evidence_list.extend(evidence)

            # Группируем доказательства по нормализованным значениям
            value_groups = self._group_evidence_by_value(evidence_list)

            # Если есть разные значения - это потенциальный конфликт
            if len(value_groups) > 1:
                conflict = self._analyze_conflict(
                    fact_key=fact_key,
                    value_groups=value_groups,
                    prefer_source_zones=prefer_source_zones or [],
                )
                if conflict:
                    conflicts.append(conflict)

        blocking = sum(1 for c in conflicts if c.severity == "block")
        warnings = sum(1 for c in conflicts if c.severity == "warn")

        return ConflictDetectionResult(
            conflicts=conflicts,
            total_conflicts=len(conflicts),
            blocking_conflicts=blocking,
            warning_conflicts=warnings,
        )

    async def _collect_evidence_for_fact(self, fact: Fact) -> list[FactEvidenceSchema]:
        """Собирает доказательства для факта из связанных anchors."""
        # Получаем все evidence для факта
        stmt = select(FactEvidence).where(FactEvidence.fact_id == fact.id)
        res = await self.db.execute(stmt)
        fact_evidences = res.scalars().all()

        if not fact_evidences:
            return []

        anchor_ids = [fe.anchor_id for fe in fact_evidences]

        # Получаем anchors для определения source_zone
        stmt_anchors = select(Anchor.anchor_id, Anchor.source_zone, Anchor.confidence).where(
            Anchor.anchor_id.in_(anchor_ids)
        )
        res_anchors = await self.db.execute(stmt_anchors)
        anchors_data = {row[0]: (row[1], row[2]) for row in res_anchors.all()}

        evidence_list: list[FactEvidenceSchema] = []
        for fe in fact_evidences:
            source_zone, anchor_confidence = anchors_data.get(fe.anchor_id, ("unknown", None))
            evidence_list.append(
                FactEvidenceSchema(
                    value=fact.value_json,
                    source_zone=source_zone,
                    anchor_ids=[fe.anchor_id],
                    confidence=anchor_confidence or fact.confidence if hasattr(fact, "confidence") else None,
                    fact_id=fact.id,
                )
            )

        return evidence_list

    def _group_evidence_by_value(
        self, evidence_list: list[FactEvidenceSchema]
    ) -> dict[str, list[FactEvidenceSchema]]:
        """Группирует доказательства по нормализованным значениям."""
        groups: dict[str, list[FactEvidenceSchema]] = {}

        for evidence in evidence_list:
            # Нормализуем значение для группировки
            normalized_key = self._normalize_value_key(evidence.value)
            if normalized_key not in groups:
                groups[normalized_key] = []
            groups[normalized_key].append(evidence)

        return groups

    def _normalize_value_key(self, value: Any) -> str:
        """Создаёт нормализованный ключ для группировки значений."""
        if isinstance(value, dict):
            # Для словарей берём значение по ключу "value" если есть
            val = value.get("value", value)
        else:
            val = value

        if isinstance(val, (int, float)):
            return str(val)
        elif isinstance(val, str):
            # Для строк нормализуем: убираем пробелы, приводим к нижнему регистру
            return re.sub(r"\s+", " ", val.lower().strip())
        elif isinstance(val, list):
            # Для списков сортируем и объединяем
            sorted_items = sorted(str(item) for item in val)
            return "|".join(sorted_items)
        else:
            return str(val)

    def _analyze_conflict(
        self,
        fact_key: str,
        value_groups: dict[str, list[FactEvidenceSchema]],
        prefer_source_zones: list[str],
    ) -> FactConflict | None:
        """
        Анализирует конфликт между группами значений.

        Правила:
        - Если значения различаются и оба имеют confidence >= threshold => BLOCK
        - Если одно значение низкоуверенное => WARN
        - Для sample_size: парсим числовые значения; "примерно/около" = низкая уверенность
        """
        if len(value_groups) < 2:
            return None

        # Собираем все доказательства
        all_evidence: list[FactEvidenceSchema] = []
        canonical_values: list[CanonicalFactValue] = []

        for value_key, evidence_group in value_groups.items():
            # Объединяем anchor_ids из всех доказательств в группе
            all_anchor_ids: list[str] = []
            all_zones: set[str] = set()
            confidences: list[float] = []

            for ev in evidence_group:
                all_anchor_ids.extend(ev.anchor_ids)
                all_zones.add(ev.source_zone)
                if ev.confidence is not None:
                    confidences.append(ev.confidence)

            # Определяем уверенность группы
            avg_confidence = sum(confidences) / len(confidences) if confidences else None
            is_low_confidence = self._is_low_confidence_value(value_key, fact_key)

            # Создаём каноническое значение
            canonical_value = CanonicalFactValue(
                value=self._parse_value(value_key, fact_key),
                confidence=avg_confidence,
                is_low_confidence=is_low_confidence,
            )
            canonical_values.append(canonical_value)

            # Создаём объединённое доказательство для группы
            combined_evidence = FactEvidenceSchema(
                value=self._parse_value(value_key, fact_key),
                source_zone=", ".join(sorted(all_zones)),
                anchor_ids=list(set(all_anchor_ids)),
                confidence=avg_confidence,
            )
            all_evidence.append(combined_evidence)

        # Определяем severity
        high_confidence_count = sum(
            1
            for cv in canonical_values
            if cv.confidence is not None
            and cv.confidence >= CONFIDENCE_THRESHOLD
            and not cv.is_low_confidence
        )

        if high_confidence_count >= 2:
            severity = "block"
        elif high_confidence_count == 1 and any(
            cv.is_low_confidence or (cv.confidence is not None and cv.confidence < CONFIDENCE_THRESHOLD)
            for cv in canonical_values
        ):
            severity = "warn"
        else:
            severity = "warn"

        # Проверяем возможность авторазрешения
        can_auto_resolve = False
        if prefer_source_zones and severity == "warn":
            # Авторазрешение возможно только если:
            # - prefer zone имеет значение с высокой уверенностью
            # - другие зоны имеют низкую уверенность или отсутствуют
            prefer_evidence = [
                ev for ev in all_evidence if any(zone in ev.source_zone for zone in prefer_source_zones)
            ]
            other_evidence = [
                ev for ev in all_evidence if not any(zone in ev.source_zone for zone in prefer_source_zones)
            ]

            if prefer_evidence:
                prefer_ev = prefer_evidence[0]
                if (
                    prefer_ev.confidence is not None
                    and prefer_ev.confidence >= CONFIDENCE_THRESHOLD
                    and not self._is_low_confidence_value(str(prefer_ev.value), fact_key)
                ):
                    # Проверяем, что другие зоны имеют низкую уверенность
                    if all(
                        ev.confidence is None
                        or ev.confidence < CONFIDENCE_THRESHOLD
                        or self._is_low_confidence_value(str(ev.value), fact_key)
                        for ev in other_evidence
                    ):
                        can_auto_resolve = True

        return FactConflict(
            fact_key=fact_key,
            values=canonical_values,
            evidence=all_evidence,
            severity=severity,
            can_auto_resolve=can_auto_resolve,
        )

    def _parse_value(self, value_str: str, fact_key: str) -> Any:
        """Парсит значение в зависимости от типа fact_key."""
        # Для числовых фактов пытаемся извлечь число
        if fact_key in ("sample_size", "planned_n_total", "planned_n_per_arm"):
            return self._parse_numeric_value(value_str)
        elif fact_key in ("allocation_ratio",):
            return self._parse_ratio_value(value_str)
        elif fact_key in ("stratification_factors",):
            # Для списков возвращаем как есть или парсим
            if isinstance(value_str, list):
                return value_str
            # Пытаемся распарсить список из строки
            return [s.strip() for s in str(value_str).split(",") if s.strip()]
        else:
            # Для остальных возвращаем как есть
            return value_str

    def _parse_numeric_value(self, text: str) -> int | float | None:
        """
        Парсит числовое значение из текста (RU/EN).

        Поддерживает форматы:
        - "100"
        - "N=100"
        - "около 100" / "approximately 100"
        - "100 участников" / "100 participants"
        """
        if isinstance(text, (int, float)):
            return text

        text_str = str(text).strip()

        # Убираем маркеры неопределённости для парсинга
        for marker in UNCERTAINTY_MARKERS_RU | UNCERTAINTY_MARKERS_EN:
            text_str = text_str.replace(marker, "")

        # Ищем паттерны типа "N=100", "N = 100", "100"
        patterns = [
            r"N\s*[=:]\s*(\d+)",
            r"(\d+)\s*(?:участников|participants|subjects|patients)",
            r"\b(\d+)\b",
        ]

        for pattern in patterns:
            match = re.search(pattern, text_str, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue

        # Если не нашли число, пытаемся преобразовать всю строку
        try:
            # Убираем все нецифровые символы кроме точки и минуса
            cleaned = re.sub(r"[^\d.\-]", "", text_str)
            if cleaned:
                if "." in cleaned:
                    return float(cleaned)
                return int(cleaned)
        except (ValueError, TypeError):
            pass

        return None

    def _parse_ratio_value(self, text: str) -> str | None:
        """Парсит значение соотношения (например, "1:1", "2:1")."""
        if isinstance(text, str):
            # Ищем паттерн "число:число"
            match = re.search(r"(\d+)\s*:\s*(\d+)", text)
            if match:
                return f"{match.group(1)}:{match.group(2)}"
        return str(text) if text else None

    def _is_low_confidence_value(self, value_str: str, fact_key: str) -> bool:
        """Проверяет, содержит ли значение маркеры неопределённости."""
        value_lower = str(value_str).lower()

        # Проверяем маркеры неопределённости
        for marker in UNCERTAINTY_MARKERS_RU | UNCERTAINTY_MARKERS_EN:
            if marker.lower() in value_lower:
                return True

        return False

