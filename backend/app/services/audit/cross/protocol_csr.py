"""Аудитор для проверки целостности целей между Протоколом и CSR (Clinical Study Report)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AuditCategory, AuditSeverity, DocumentType
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact, FactEvidence
from app.db.models.studies import Document, DocumentVersion
from app.schemas.audit import AuditIssue
from app.services.audit.base import BaseAuditor


class ProtocolCsrConsistencyAuditor(BaseAuditor):
    """Проверяет целостность целей исследования между Protocol и CSR.

    Проверки:
    - primary_objective должен совпадать (similarity > 0.9)
    - Если изменились - это критическая проблема целостности данных
    """

    SIMILARITY_THRESHOLD = 0.9

    @property
    def name(self) -> str:
        return "ProtocolCsrConsistencyAuditor"

    async def run(
        self, primary_doc_version_id: UUID, secondary_doc_version_id: UUID
    ) -> list[AuditIssue]:
        """Запускает проверку целостности целей между Протоколом и CSR.

        Args:
            primary_doc_version_id: ID версии Протокола
            secondary_doc_version_id: ID версии CSR

        Returns:
            Список найденных проблем
        """
        logger.info(
            f"[{self.name}] Запуск проверки Protocol ({primary_doc_version_id}) vs "
            f"CSR ({secondary_doc_version_id})"
        )

        issues: list[AuditIssue] = []

        # Проверяем, что документы правильных типов
        protocol_version = await self.db.get(DocumentVersion, primary_doc_version_id)
        csr_version = await self.db.get(DocumentVersion, secondary_doc_version_id)

        if not protocol_version or not csr_version:
            return issues

        protocol_doc = await self.db.get(Document, protocol_version.document_id)
        csr_doc = await self.db.get(Document, csr_version.document_id)

        if not protocol_doc or protocol_doc.doc_type != DocumentType.PROTOCOL:
            logger.warning(f"Документ {primary_doc_version_id} не является Протоколом")
            return issues

        if not csr_doc or csr_doc.doc_type != DocumentType.CSR:
            logger.warning(f"Документ {secondary_doc_version_id} не является CSR")
            return issues

        # Проверка целостности primary_objective
        issues.extend(
            await self._check_primary_objective_integrity(
                primary_doc_version_id, secondary_doc_version_id
            )
        )

        logger.info(f"[{self.name}] Найдено проблем: {len(issues)}")
        return issues

    async def _check_primary_objective_integrity(
        self, protocol_version_id: UUID, csr_version_id: UUID
    ) -> list[AuditIssue]:
        """Проверяет, что primary_objective совпадает между Protocol и CSR."""
        issues: list[AuditIssue] = []

        # Получаем primary_objective из Protocol
        protocol_objective_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "overview",
            Fact.fact_key == "primary_objective",
            Fact.created_from_doc_version_id == protocol_version_id,
        )
        protocol_result = await self.db.execute(protocol_objective_stmt)
        protocol_objective_fact = protocol_result.scalar_one_or_none()

        # Получаем primary_objective из CSR
        csr_objective_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "overview",
            Fact.fact_key == "primary_objective",
            Fact.created_from_doc_version_id == csr_version_id,
        )
        csr_result = await self.db.execute(csr_objective_stmt)
        csr_objective_fact = csr_result.scalar_one_or_none()

        if not protocol_objective_fact or not csr_objective_fact:
            # Если одного из фактов нет - не можем сравнить
            return issues

        protocol_objective_text = self._extract_text_from_fact(protocol_objective_fact.value_json)
        csr_objective_text = self._extract_text_from_fact(csr_objective_fact.value_json)

        if not protocol_objective_text or not csr_objective_text:
            return issues

        # Вычисляем схожесть текстов
        similarity = self._calculate_text_similarity(protocol_objective_text, csr_objective_text)

        if similarity < self.SIMILARITY_THRESHOLD:
            protocol_anchors = await self._get_fact_anchors(protocol_objective_fact.id)
            csr_anchors = await self._get_fact_anchors(csr_objective_fact.id)

            issues.append(
                AuditIssue(
                    severity=AuditSeverity.CRITICAL,
                    category=AuditCategory.COMPLIANCE,
                    description=(
                        f"Нарушение целостности данных: primary_objective изменился между "
                        f"Протоколом и CSR. Схожесть: {similarity:.2%}. "
                        f"Это может указывать на проблему целостности данных исследования. "
                        f"Цели исследования не должны изменяться между Протоколом и CSR."
                    ),
                    location_anchors=protocol_anchors[:5] + csr_anchors[:5],
                    suggested_fix=(
                        f"Проверить причину расхождения. Primary objective должен быть одинаковым "
                        f"в Протоколе и CSR. Если изменение обосновано протокольной поправкой, "
                        f"убедиться, что она задокументирована и утверждена."
                    ),
                )
            )

        return issues

    def _extract_text_from_fact(self, value_json: dict) -> str | None:
        """Извлекает текстовое значение из value_json факта."""
        if isinstance(value_json, str):
            return value_json
        if isinstance(value_json, dict):
            # Может быть структура {"value": "text"} или {"text": "..."}
            for key in ["value", "text", "description", "objective"]:
                if key in value_json and isinstance(value_json[key], str):
                    return value_json[key]
        return None

    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """Вычисляет схожесть двух текстов (простая метрика на основе слов).

        Использует простое сравнение множеств слов и Jaccard similarity.
        Для более точной оценки можно использовать библиотеки типа thefuzz или sentence-transformers.
        """
        # Нормализуем тексты: приводим к нижнему регистру, убираем пунктуацию
        import re

        words1 = set(re.findall(r"\b\w+\b", text1.lower()))
        words2 = set(re.findall(r"\b\w+\b", text2.lower()))

        if not words1 or not words2:
            return 0.0

        # Jaccard similarity
        intersection = len(words1 & words2)
        union = len(words1 | words2)

        if union == 0:
            return 0.0

        jaccard = intersection / union

        # Дополнительно проверяем порядок слов (используя последовательность)
        # Для более точной оценки можно использовать SequenceMatcher
        from difflib import SequenceMatcher

        sequence_similarity = SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

        # Возвращаем среднее между Jaccard и Sequence similarity
        return (jaccard + sequence_similarity) / 2.0

    async def _get_fact_anchors(self, fact_id: UUID) -> list[str]:
        """Получает список anchor_id для факта из его evidence."""
        stmt = select(FactEvidence.anchor_id).where(FactEvidence.fact_id == fact_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

