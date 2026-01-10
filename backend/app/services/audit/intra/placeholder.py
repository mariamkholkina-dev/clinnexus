"""Аудитор для поиска незавершенных мест (placeholders) в документе."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AuditCategory, AuditSeverity
from app.db.models.anchors import Anchor
from app.schemas.audit import AuditIssue
from app.services.audit.base import BaseAuditor


class PlaceholderAuditor(BaseAuditor):
    """Ищет паттерны незавершенности в тексте документа.

    Проверяет наличие:
    - TBD (To Be Determined)
    - TBC (To Be Confirmed)
    - XX, XXX (заполнители)
    - [Insert], [Вставить]
    - Error!
    """

    # Паттерны для поиска плейсхолдеров
    PLACEHOLDER_PATTERNS = [
        (re.compile(r"\bTBD\b", re.IGNORECASE), "TBD (To Be Determined)"),
        (re.compile(r"\bTBC\b", re.IGNORECASE), "TBC (To Be Confirmed)"),
        (re.compile(r"\bXX+\b"), "XX/XXX (заполнитель)"),
        (re.compile(r"\[Insert\]", re.IGNORECASE), "[Insert]"),
        (re.compile(r"\[Вставить\]", re.IGNORECASE), "[Вставить]"),
        (re.compile(r"\[Вставьте\]", re.IGNORECASE), "[Вставьте]"),
        (re.compile(r"Error!", re.IGNORECASE), "Error!"),
        (re.compile(r"\[TODO\]", re.IGNORECASE), "[TODO]"),
        (re.compile(r"\[FIXME\]", re.IGNORECASE), "[FIXME]"),
        (re.compile(r"\bXXX\b"), "XXX (заполнитель)"),
    ]

    @property
    def name(self) -> str:
        return "PlaceholderAuditor"

    async def run(self, doc_version_id: UUID) -> list[AuditIssue]:
        """Запускает поиск плейсхолдеров."""
        logger.info(f"[{self.name}] Запуск проверки для doc_version_id={doc_version_id}")

        issues: list[AuditIssue] = []

        # Получаем все анкоры документа
        stmt = select(Anchor).where(Anchor.doc_version_id == doc_version_id)
        result = await self.db.execute(stmt)
        anchors = result.scalars().all()

        if not anchors:
            return issues

        # Проходим по всем анкорам и ищем плейсхолдеры
        found_placeholders: dict[str, list[str]] = {}

        for anchor in anchors:
            text = anchor.text_raw

            for pattern, placeholder_name in self.PLACEHOLDER_PATTERNS:
                matches = pattern.findall(text)
                if matches:
                    if placeholder_name not in found_placeholders:
                        found_placeholders[placeholder_name] = []
                    found_placeholders[placeholder_name].append(anchor.anchor_id)

        # Создаем issues для найденных плейсхолдеров
        for placeholder_name, anchor_ids in found_placeholders.items():
            # Группируем по количеству найденных мест
            count = len(anchor_ids)

            issues.append(
                AuditIssue(
                    severity=AuditSeverity.MAJOR,
                    category=AuditCategory.COMPLIANCE,
                    description=(
                        f"Найдено незавершенное место: '{placeholder_name}'. "
                        f"Обнаружено в {count} месте(ах) документа. "
                        f"Необходимо заменить на конкретное значение перед финализацией документа."
                    ),
                    location_anchors=anchor_ids[:10],  # Ограничиваем количество для UI
                    suggested_fix=(
                        f"Заменить все вхождения '{placeholder_name}' на конкретные значения. "
                        f"Проверить все места использования перед отправкой документа на утверждение."
                    ),
                )
            )

        logger.info(f"[{self.name}] Найдено проблем: {len(issues)}")
        return issues

