"""Аудитор для проверки использования аббревиатур в документе."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AuditCategory, AuditSeverity, SourceZone
from app.db.models.anchors import Anchor
from app.schemas.audit import AuditIssue
from app.services.audit.base import BaseAuditor


class AbbreviationAuditor(BaseAuditor):
    """Проверяет правильность использования аббревиатур.

    Проверки:
    - Аббревиатура в тексте, но нет в списке аббревиатур
    - Аббревиатура в списке, но не используется
    - Первое использование без расшифровки в скобках
    """

    # Регулярное выражение для поиска аббревиатур (2+ заглавных буквы подряд)
    ABBREVIATION_PATTERN = re.compile(r"\b[A-ZА-Я]{2,}\b")

    @property
    def name(self) -> str:
        return "AbbreviationAuditor"

    async def run(self, doc_version_id: UUID) -> list[AuditIssue]:
        """Запускает проверку аббревиатур."""
        logger.info(f"[{self.name}] Запуск проверки для doc_version_id={doc_version_id}")

        issues: list[AuditIssue] = []

        # Получаем все анкоры документа
        stmt = select(Anchor).where(Anchor.doc_version_id == doc_version_id)
        result = await self.db.execute(stmt)
        anchors = result.scalars().all()

        if not anchors:
            return issues

        # Собираем все аббревиатуры из текста
        text_abbreviations = self._extract_abbreviations_from_text(anchors)

        # Ищем раздел со списком аббревиатур (обычно в appendix)
        abbreviation_list = await self._find_abbreviation_list(doc_version_id)

        # Проверка 1: Аббревиатура в тексте, но нет в списке
        issues.extend(
            self._check_missing_in_list(text_abbreviations, abbreviation_list, anchors)
        )

        # Проверка 2: В списке, но не используется
        issues.extend(
            self._check_unused_in_list(text_abbreviations, abbreviation_list, doc_version_id)
        )

        # Проверка 3: Первое использование без расшифровки
        issues.extend(
            self._check_first_use_without_expansion(text_abbreviations, anchors)
        )

        logger.info(f"[{self.name}] Найдено проблем: {len(issues)}")
        return issues

    def _extract_abbreviations_from_text(self, anchors: list[Anchor]) -> dict[str, list[str]]:
        """Извлекает все аббревиатуры из текста анкоров.

        Returns:
            Словарь {abbreviation: [anchor_id, ...]} - где каждая аббревиатура встречается
        """
        abbreviations: dict[str, list[str]] = {}

        for anchor in anchors:
            text = anchor.text_raw
            matches = self.ABBREVIATION_PATTERN.findall(text)

            for match in matches:
                # Исключаем некоторые общие слова/паттерны
                if self._is_valid_abbreviation(match):
                    if match not in abbreviations:
                        abbreviations[match] = []
                    abbreviations[match].append(anchor.anchor_id)

        return abbreviations

    def _is_valid_abbreviation(self, text: str) -> bool:
        """Проверяет, является ли текст валидной аббревиатурой для проверки."""
        # Исключаем однобуквенные и слишком короткие
        if len(text) < 2:
            return False

        # Исключаем числа (например, "COVID-19" обрабатывается отдельно)
        if any(char.isdigit() for char in text):
            return False

        # Исключаем некоторые общие паттерны, которые не являются аббревиатурами
        common_exceptions = {
            "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",  # Римские цифры
            "COVID", "SARS",  # Медицинские термины, которые всегда пишутся заглавными
        }

        if text.upper() in common_exceptions:
            return False

        # Если все символы заглавные и их >= 2 - считаем аббревиатурой
        return text.isupper() and len(text) >= 2

    async def _find_abbreviation_list(self, doc_version_id: UUID) -> dict[str, str]:
        """Ищет раздел со списком аббревиатур (обычно в appendix).

        Returns:
            Словарь {abbreviation: expansion} из списка аббревиатур
        """
        # Ищем анкоры в секции appendix
        stmt = select(Anchor).where(
            Anchor.doc_version_id == doc_version_id,
            Anchor.source_zone == SourceZone.APPENDIX,
        )
        result = await self.db.execute(stmt)
        appendix_anchors = result.scalars().all()

        abbreviation_list: dict[str, str] = {}

        for anchor in appendix_anchors:
            text = anchor.text_raw.lower()

            # Проверяем, является ли это разделом со списком аббревиатур
            list_keywords = [
                "list of abbreviations",
                "абbreviations",
                "список сокращений",
                "сокращения",
                "abbreviations and acronyms",
            ]

            if any(keyword in text for keyword in list_keywords):
                # Пытаемся извлечь пары аббревиатура-расшифровка
                # Обычный формат: "АББР - Расшифровка" или "АББР: Расшифровка"
                lines = anchor.text_raw.split("\n")
                for line in lines:
                    # Паттерны: "ABC - Description" или "ABC: Description" или "ABC (Description)"
                    patterns = [
                        re.compile(r"^([A-ZА-Я]{2,})\s*[:\-]\s*(.+)$", re.MULTILINE),
                        re.compile(r"^([A-ZА-Я]{2,})\s*\((.+?)\)", re.MULTILINE),
                    ]

                    for pattern in patterns:
                        matches = pattern.findall(line)
                        for abbrev, expansion in matches:
                            abbrev = abbrev.strip().upper()
                            expansion = expansion.strip()
                            if abbrev and expansion:
                                abbreviation_list[abbrev] = expansion

        return abbreviation_list

    def _check_missing_in_list(
        self,
        text_abbreviations: dict[str, list[str]],
        abbreviation_list: dict[str, str],
        anchors: list[Anchor],
    ) -> list[AuditIssue]:
        """Проверка: аббревиатура в тексте, но нет в списке."""
        issues: list[AuditIssue] = []

        for abbrev, anchor_ids in text_abbreviations.items():
            if abbrev not in abbreviation_list:
                issues.append(
                    AuditIssue(
                        severity=AuditSeverity.MINOR,
                        category=AuditCategory.TERMINOLOGY,
                        description=(
                            f"Аббревиатура '{abbrev}' используется в тексте, "
                            f"но отсутствует в разделе 'Список аббревиатур'"
                        ),
                        location_anchors=anchor_ids[:5],  # Ограничиваем количество якорей
                        suggested_fix=(
                            f"Добавить '{abbrev}' в раздел 'Список аббревиатур' "
                            f"с расшифровкой"
                        ),
                    )
                )

        return issues

    def _check_unused_in_list(
        self,
        text_abbreviations: dict[str, list[str]],
        abbreviation_list: dict[str, str],
        doc_version_id: UUID,
    ) -> list[AuditIssue]:
        """Проверка: аббревиатура в списке, но не используется в тексте."""
        issues: list[AuditIssue] = []

        for abbrev in abbreviation_list:
            if abbrev not in text_abbreviations:
                issues.append(
                    AuditIssue(
                        severity=AuditSeverity.MINOR,
                        category=AuditCategory.TERMINOLOGY,
                        description=(
                            f"Аббревиатура '{abbrev}' присутствует в списке аббревиатур, "
                            f"но не используется в тексте документа"
                        ),
                        location_anchors=[],  # Нет конкретного места в тексте
                        suggested_fix=(
                            f"Удалить '{abbrev}' из списка аббревиатур, "
                            f"если она действительно не используется, или проверить правильность написания"
                        ),
                    )
                )

        return issues

    def _check_first_use_without_expansion(
        self, text_abbreviations: dict[str, list[str]], anchors: list[Anchor]
    ) -> list[AuditIssue]:
        """Проверка: первое использование аббревиатуры без расшифровки в скобках."""
        issues: list[AuditIssue] = []

        # Сортируем анкоры по ordinal, чтобы найти первое использование
        anchors_by_id = {a.anchor_id: a for a in anchors}

        for abbrev, anchor_ids in text_abbreviations.items():
            # Находим первое использование по ordinal
            first_anchor_id = min(
                anchor_ids,
                key=lambda aid: anchors_by_id.get(aid, anchors[0]).ordinal if aid in anchors_by_id else float("inf"),
            )

            first_anchor = anchors_by_id.get(first_anchor_id)
            if not first_anchor:
                continue

            text = first_anchor.text_raw

            # Проверяем, есть ли расшифровка в скобках рядом с аббревиатурой
            # Паттерн: "Full Name (АББР)" или "АББР (Full Name)"
            expansion_patterns = [
                rf"\b{re.escape(abbrev)}\s*\([^)]+\)",  # ABC (Description)
                rf"\([^)]+\)\s*\b{re.escape(abbrev)}\b",  # (Description) ABC
                rf"\b[A-ZА-Я][a-zа-я]+\s+\b{re.escape(abbrev)}\b",  # Description ABC
            ]

            has_expansion = any(re.search(pattern, text, re.IGNORECASE) for pattern in expansion_patterns)

            if not has_expansion:
                issues.append(
                    AuditIssue(
                        severity=AuditSeverity.MINOR,
                        category=AuditCategory.TERMINOLOGY,
                        description=(
                            f"Аббревиатура '{abbrev}' используется в первый раз "
                            f"без расшифровки в скобках"
                        ),
                        location_anchors=[first_anchor_id],
                        suggested_fix=(
                            f"При первом использовании добавить расшифровку: "
                            f"'Полное название ({abbrev})' или '{abbrev} (Полное название)'"
                        ),
                    )
                )

        return issues

