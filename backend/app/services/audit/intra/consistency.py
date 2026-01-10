"""Аудитор для проверки согласованности числовых фактов внутри документа."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AuditCategory, AuditSeverity, SourceZone
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact, FactEvidence
from app.schemas.audit import AuditIssue
from app.services.audit.base import BaseAuditor


class ConsistencyAuditor(BaseAuditor):
    """Проверяет согласованность числовых фактов между разными секциями документа.

    Примеры проверок:
    - Сравнение sample_size из population и statistics
    - Проверка суммы длительностей периодов лечения
    """

    @property
    def name(self) -> str:
        return "ConsistencyAuditor"

    async def run(self, doc_version_id: UUID) -> list[AuditIssue]:
        """Запускает проверку согласованности фактов."""
        logger.info(f"[{self.name}] Запуск проверки для doc_version_id={doc_version_id}")

        issues: list[AuditIssue] = []

        # 1. Проверка sample_size: population.planned_sample_size vs statistics.sample_size
        issues.extend(await self._check_sample_size_consistency(doc_version_id))

        # 2. Проверка длительности исследования
        issues.extend(await self._check_study_duration_consistency(doc_version_id))

        logger.info(f"[{self.name}] Найдено проблем: {len(issues)}")
        return issues

    async def _check_sample_size_consistency(self, doc_version_id: UUID) -> list[AuditIssue]:
        """Сравнивает planned_sample_size из population и sample_size из statistics."""
        issues: list[AuditIssue] = []

        # Получаем факты
        pop_fact_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "population",
            Fact.fact_key == "planned_sample_size",
        )
        pop_result = await self.db.execute(pop_fact_stmt)
        pop_fact = pop_result.scalar_one_or_none()

        stats_fact_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "statistics",
            Fact.fact_key == "sample_size",
        )
        stats_result = await self.db.execute(stats_fact_stmt)
        stats_fact = stats_result.scalar_one_or_none()

        if not pop_fact or not stats_fact:
            # Если одного из фактов нет - не можем сравнить
            return issues

        # Извлекаем числовые значения
        pop_value = self._extract_numeric_value(pop_fact.value_json)
        stats_value = self._extract_numeric_value(stats_fact.value_json)

        if pop_value is None or stats_value is None:
            return issues

        if abs(pop_value - stats_value) > 0.01:  # Допустимая погрешность для float
            # Получаем anchor_id из evidence
            pop_anchors = await self._get_fact_anchors(pop_fact.id)
            stats_anchors = await self._get_fact_anchors(stats_fact.id)

            issues.append(
                AuditIssue(
                    severity=AuditSeverity.MAJOR,
                    category=AuditCategory.CONSISTENCY,
                    description=(
                        f"Несоответствие размера выборки: "
                        f"{pop_value} (Синопсис, population.planned_sample_size) vs "
                        f"{stats_value} (Статистика, statistics.sample_size)"
                    ),
                    location_anchors=pop_anchors + stats_anchors,
                    suggested_fix=(
                        f"Проверить и привести к единому значению. "
                        f"Рекомендуется использовать значение из статистики: {stats_value}"
                    ),
                )
            )

        return issues

    async def _check_study_duration_consistency(self, doc_version_id: UUID) -> list[AuditIssue]:
        """Проверяет, что сумма treatment_duration + follow_up равна total_study_duration."""
        issues: list[AuditIssue] = []

        # Получаем факты из design
        treatment_duration_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "design",
            Fact.fact_key == "treatment_duration",
        )
        treatment_result = await self.db.execute(treatment_duration_stmt)
        treatment_fact = treatment_result.scalar_one_or_none()

        follow_up_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "design",
            Fact.fact_key == "follow_up",
        )
        follow_up_result = await self.db.execute(follow_up_stmt)
        follow_up_fact = follow_up_result.scalar_one_or_none()

        total_duration_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "design",
            Fact.fact_key == "total_study_duration",
        )
        total_result = await self.db.execute(total_duration_stmt)
        total_fact = total_result.scalar_one_or_none()

        if not treatment_fact or not follow_up_fact or not total_fact:
            return issues

        treatment_value = self._extract_numeric_value(treatment_fact.value_json)
        follow_up_value = self._extract_numeric_value(follow_up_fact.value_json)
        total_value = self._extract_numeric_value(total_fact.value_json)

        if treatment_value is None or follow_up_value is None or total_value is None:
            return issues

        calculated_total = treatment_value + follow_up_value

        if abs(calculated_total - total_value) > 0.01:
            # Получаем anchor_id из evidence
            treatment_anchors = await self._get_fact_anchors(treatment_fact.id)
            follow_up_anchors = await self._get_fact_anchors(follow_up_fact.id)
            total_anchors = await self._get_fact_anchors(total_fact.id)

            issues.append(
                AuditIssue(
                    severity=AuditSeverity.MAJOR,
                    category=AuditCategory.CONSISTENCY,
                    description=(
                        f"Несоответствие общей длительности исследования: "
                        f"treatment_duration ({treatment_value}) + follow_up ({follow_up_value}) = {calculated_total}, "
                        f"но указано total_study_duration = {total_value}"
                    ),
                    location_anchors=treatment_anchors + follow_up_anchors + total_anchors,
                    suggested_fix=(
                        f"Исправить total_study_duration на {calculated_total} "
                        f"или проверить правильность treatment_duration и follow_up"
                    ),
                )
            )

        return issues

    def _extract_numeric_value(self, value_json: dict) -> float | None:
        """Извлекает числовое значение из value_json."""
        if isinstance(value_json, (int, float)):
            return float(value_json)
        if isinstance(value_json, dict):
            # Может быть структура типа {"value": 100, "unit": "days"}
            if "value" in value_json:
                val = value_json["value"]
                if isinstance(val, (int, float)):
                    return float(val)
            # Или просто число в корне
            for key in ["min", "max", "mean", "median"]:
                if key in value_json and isinstance(value_json[key], (int, float)):
                    return float(value_json[key])
        if isinstance(value_json, list) and len(value_json) > 0:
            # Может быть список чисел - берем первое
            if isinstance(value_json[0], (int, float)):
                return float(value_json[0])
        return None

    async def _get_fact_anchors(self, fact_id: UUID) -> list[str]:
        """Получает список anchor_id для факта из его evidence."""
        stmt = select(FactEvidence.anchor_id).where(FactEvidence.fact_id == fact_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

