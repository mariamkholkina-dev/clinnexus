"""Аудитор для проверки логики визитов в Schedule of Activities."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AuditCategory, AuditSeverity
from app.db.models.facts import Fact, FactEvidence
from app.schemas.audit import AuditIssue
from app.services.audit.base import BaseAuditor


class VisitLogicAuditor(BaseAuditor):
    """Проверяет логику визитов в Schedule of Activities.

    Проверки:
    - Последовательность visit_day (Visit N должен быть < Visit N+1)
    - Пересечение окон визитов (Visit Windows)
    """

    @property
    def name(self) -> str:
        return "VisitLogicAuditor"

    async def run(self, doc_version_id: UUID) -> list[AuditIssue]:
        """Запускает проверку логики визитов."""
        logger.info(f"[{self.name}] Запуск проверки для doc_version_id={doc_version_id}")

        issues: list[AuditIssue] = []

        # Получаем факт с визитами
        visits_fact_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "soa",
            Fact.fact_key == "visits",
        )
        visits_result = await self.db.execute(visits_fact_stmt)
        visits_fact = visits_result.scalar_one_or_none()

        if not visits_fact:
            return issues

        # Извлекаем визиты из value_json
        value_json = visits_fact.value_json
        if not isinstance(value_json, dict) or "visits" not in value_json:
            return issues

        visits_data = value_json["visits"]
        if not isinstance(visits_data, list):
            return issues

        # Проверка последовательности визитов
        issues.extend(await self._check_visit_sequence(visits_data, visits_fact.id))

        # Проверка пересечения окон визитов
        issues.extend(await self._check_visit_windows_overlap(visits_data, visits_fact.id))

        logger.info(f"[{self.name}] Найдено проблем: {len(issues)}")
        return issues

    async def _check_visit_sequence(
        self, visits_data: list[dict], fact_id: UUID
    ) -> list[AuditIssue]:
        """Проверяет, что visit_day увеличивается с номером визита."""
        issues: list[AuditIssue] = []

        # Извлекаем визиты с номерами и днями
        visits_with_days: list[tuple[int, float, str, str | None]] = []

        for visit in visits_data:
            if not isinstance(visit, dict):
                continue

            visit_id = visit.get("visit_id", "")
            label = visit.get("label", "")
            day_str = visit.get("day")

            if not day_str:
                continue

            # Пытаемся извлечь номер визита из visit_id или label
            visit_number = self._extract_visit_number(visit_id, label)
            day_value = self._parse_day_value(day_str)

            if visit_number is not None and day_value is not None:
                visits_with_days.append((visit_number, day_value, visit_id, visit.get("anchor_id")))

        # Сортируем по номеру визита
        visits_with_days.sort(key=lambda x: x[0])

        # Проверяем последовательность
        for i in range(len(visits_with_days) - 1):
            current_num, current_day, current_id, current_anchor = visits_with_days[i]
            next_num, next_day, next_id, next_anchor = visits_with_days[i + 1]

            if current_day >= next_day:
                # Получаем все anchor_id для факта
                anchors = await self._get_fact_anchors(fact_id)

                issues.append(
                    AuditIssue(
                        severity=AuditSeverity.MAJOR,
                        category=AuditCategory.LOGIC,
                        description=(
                            f"Нарушение последовательности визитов: "
                            f"Visit {current_num} (Day {current_day}) >= "
                            f"Visit {next_num} (Day {next_day}). "
                            f"День визита должен увеличиваться с номером визита."
                        ),
                        location_anchors=(
                            ([current_anchor] if current_anchor else [])
                            + ([next_anchor] if next_anchor else [])
                            + (anchors[:3] if anchors else [])  # Добавляем несколько общих анкоров
                        )[:10],  # Ограничиваем общее количество
                        suggested_fix=(
                            f"Проверить правильность указания дней визитов. "
                            f"Visit {current_num} должен быть раньше Visit {next_num}"
                        ),
                    )
                )

        return issues

    async def _check_visit_windows_overlap(
        self, visits_data: list[dict], fact_id: UUID
    ) -> list[AuditIssue]:
        """Проверяет пересечение окон визитов (например, Day 10 +/- 2 и Day 13 +/- 2)."""
        issues: list[AuditIssue] = []

        # Извлекаем визиты с окнами
        visits_with_windows: list[tuple[int, float, float, str, str | None]] = []

        for visit in visits_data:
            if not isinstance(visit, dict):
                continue

            visit_id = visit.get("visit_id", "")
            label = visit.get("label", "")
            day_str = visit.get("day")

            if not day_str:
                continue

            visit_number = self._extract_visit_number(visit_id, label)
            day_window = self._parse_day_window(day_str)

            if visit_number is not None and day_window:
                center, window = day_window
                visits_with_windows.append(
                    (visit_number, center - window, center + window, visit_id, visit.get("anchor_id"))
                )

        # Сортируем по центральному дню
        visits_with_windows.sort(key=lambda x: x[1] + (x[2] - x[1]) / 2)

        # Проверяем пересечения
        for i in range(len(visits_with_windows) - 1):
            current_num, current_start, current_end, current_id, current_anchor = visits_with_windows[
                i
            ]
            next_num, next_start, next_end, next_id, next_anchor = visits_with_windows[i + 1]

            # Окна пересекаются, если current_end >= next_start
            if current_end >= next_start:
                anchors = await self._get_fact_anchors(fact_id)

                issues.append(
                    AuditIssue(
                        severity=AuditSeverity.MAJOR,
                        category=AuditCategory.LOGIC,
                        description=(
                            f"Пересечение окон визитов: "
                            f"Visit {current_num} (Day {current_start:.0f} - {current_end:.0f}) и "
                            f"Visit {next_num} (Day {next_start:.0f} - {next_end:.0f}) пересекаются. "
                            f"Окна визитов не должны пересекаться."
                        ),
                        location_anchors=(
                            ([current_anchor] if current_anchor else [])
                            + ([next_anchor] if next_anchor else [])
                            + (anchors[:3] if anchors else [])
                        )[:10],
                        suggested_fix=(
                            f"Изменить окна визитов так, чтобы они не пересекались. "
                            f"Рекомендуется увеличить интервал между визитами или уменьшить размер окон"
                        ),
                    )
                )

        return issues

    def _extract_visit_number(self, visit_id: str, label: str) -> int | None:
        """Извлекает номер визита из visit_id или label."""
        # Пытаемся найти число после "Visit" или "V"
        patterns = [
            re.compile(r"(?:Visit|V|Визит)\s*(\d+)", re.IGNORECASE),
            re.compile(r"^V(\d+)$", re.IGNORECASE),
            re.compile(r"(\d+)", re.IGNORECASE),  # Любое число
        ]

        text = f"{visit_id} {label}".strip()

        for pattern in patterns:
            match = pattern.search(text)
            if match:
                try:
                    return int(match.group(1))
                except (ValueError, IndexError):
                    continue

        return None

    def _parse_day_value(self, day_str: str) -> float | None:
        """Парсит значение дня из строки (например, "10", "Day 10", "10±2")."""
        if not isinstance(day_str, str):
            return None

        # Убираем "Day" и пробелы
        day_str = day_str.replace("Day", "").replace("день", "").strip()

        # Пытаемся извлечь число (если есть ±, берем центральное значение)
        match = re.search(r"(\d+(?:\.\d+)?)", day_str)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None

        return None

    def _parse_day_window(self, day_str: str) -> tuple[float, float] | None:
        """Парсит окно дня (например, "10±2" -> (10, 2), "Day 10 ± 2" -> (10, 2))."""
        if not isinstance(day_str, str):
            return None

        # Паттерн для "10±2" или "10 ± 2" или "Day 10 +/- 2"
        patterns = [
            re.compile(r"(\d+(?:\.\d+)?)\s*[±+/]\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
            re.compile(r"(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
            re.compile(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
        ]

        for pattern in patterns:
            match = pattern.search(day_str)
            if match:
                try:
                    center = float(match.group(1))
                    window = float(match.group(2))
                    return (center, window)
                except (ValueError, IndexError):
                    continue

        # Если нет окна, но есть просто число, считаем окно = 0
        day_value = self._parse_day_value(day_str)
        if day_value is not None:
            return (day_value, 0.0)

        return None

    async def _get_fact_anchors(self, fact_id: UUID) -> list[str]:
        """Получает список anchor_id для факта из его evidence."""
        stmt = select(FactEvidence.anchor_id).where(FactEvidence.fact_id == fact_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

