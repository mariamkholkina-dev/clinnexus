"""Сервис для извлечения основных фактов исследования (Core Study Facts)."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.models.anchors import Anchor
from app.db.models.core_facts import StudyCoreFacts
from app.db.models.facts import Fact, FactEvidence
from app.db.models.studies import DocumentVersion, Study
from app.db.models.topics import TopicEvidence


class CoreFactsExtractor:
    """Извлекает основные факты исследования для обеспечения консистентности между секциями."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build(self, doc_version_id: UUID) -> dict[str, Any]:
        """
        Извлекает основные факты исследования из документа.

        Args:
            doc_version_id: UUID версии документа

        Returns:
            Словарь с основными фактами исследования (facts_json)
        """
        logger.info(f"Извлечение core facts из doc_version_id={doc_version_id}")

        # Получаем версию документа и study_id
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        study_id = doc_version.document.study_id

        # Извлекаем факты из различных источников
        facts: dict[str, Any] = {
            "study_title": None,
            "phase": None,
            "study_design_type": None,
            "population_short": None,
            "arms": [],
            "primary_endpoints": [],
            "sample_size": None,
            "duration": None,
            "citations": {},  # anchor_ids для каждого факта
        }

        # 1. Извлекаем study_title из Study
        study = await self.db.get(Study, study_id)
        if study:
            facts["study_title"] = study.title
            facts["citations"]["study_title"] = []

        # 2. Извлекаем из Facts (KB)
        facts_from_kb = await self._extract_from_facts(study_id)
        if facts_from_kb.get("sample_size"):
            facts["sample_size"] = facts_from_kb["sample_size"]
            if facts_from_kb.get("_citations_sample_size"):
                facts["citations"]["sample_size"] = facts_from_kb["_citations_sample_size"]

        # 3. Извлекаем из topic_evidence (endpoints)
        endpoints = await self._extract_endpoints_from_topics(doc_version_id)
        if endpoints:
            facts["primary_endpoints"] = endpoints.get("primary", [])
            if "citations" in endpoints:
                facts["citations"]["primary_endpoints"] = endpoints["citations"]

        # 4. Извлекаем из anchors (statistics zone для sample_size, IP zone для arms)
        anchors_facts = await self._extract_from_anchors(doc_version_id)
        if anchors_facts.get("sample_size"):
            facts["sample_size"] = anchors_facts["sample_size"]
            facts["citations"]["sample_size"] = anchors_facts.get("citations", {}).get("sample_size", [])
        if anchors_facts.get("arms"):
            facts["arms"] = anchors_facts["arms"]
            facts["citations"]["arms"] = anchors_facts.get("citations", {}).get("arms", [])

        # 5. Извлекаем phase и study_design_type из заголовков
        design_facts = await self._extract_design_from_headers(doc_version_id)
        if design_facts.get("phase"):
            facts["phase"] = design_facts["phase"]
            facts["citations"]["phase"] = design_facts.get("citations", {}).get("phase", [])
        if design_facts.get("study_design_type"):
            facts["study_design_type"] = design_facts["study_design_type"]
            facts["citations"]["study_design_type"] = design_facts.get("citations", {}).get("study_design_type", [])

        logger.info(f"Извлечено core facts для doc_version_id={doc_version_id}")
        return facts

    async def _extract_from_facts(self, study_id: UUID) -> dict[str, Any]:
        """Извлекает факты из Study KB (таблица facts)."""
        result: dict[str, Any] = {}

        # Ищем planned_n_total
        stmt = select(Fact).where(
            Fact.study_id == study_id,
            Fact.fact_type == "population",
            Fact.fact_key == "planned_n_total",
        )
        res = await self.db.execute(stmt)
        fact = res.scalar_one_or_none()
        if fact and fact.value_json.get("value"):
            result["sample_size"] = {
                "value": fact.value_json.get("value"),
                "unit": fact.unit or "participants",
            }
            # Получаем anchor_ids из evidence
            evidence_stmt = select(FactEvidence.anchor_id).where(
                FactEvidence.fact_id == fact.id
            )
            evidence_res = await self.db.execute(evidence_stmt)
            result["_citations_sample_size"] = [row[0] for row in evidence_res.all()]

        return result

    async def _extract_endpoints_from_topics(self, doc_version_id: UUID) -> dict[str, Any] | None:
        """Извлекает endpoints из topic_evidence с topic_key='endpoints'."""
        stmt = select(TopicEvidence).where(
            TopicEvidence.doc_version_id == doc_version_id,
            TopicEvidence.topic_key == "endpoints",
        )
        res = await self.db.execute(stmt)
        evidence_list = res.scalars().all()

        if not evidence_list:
            return None

        # Берем первую запись (можно улучшить логику выбора)
        evidence = evidence_list[0]
        # Парсим endpoints из evidence_json или anchor_ids
        # MVP: упрощенная логика - просто возвращаем anchor_ids
        endpoints: list[str] = []
        if evidence.evidence_json and isinstance(evidence.evidence_json, dict):
            endpoints = evidence.evidence_json.get("endpoints", [])
        else:
            # Если нет структурированных данных, возвращаем пустой список
            # В будущем можно добавить LLM-извлечение из anchor_ids
            pass

        return {
            "primary": endpoints[:5],  # Ограничиваем первыми 5
            "citations": evidence.anchor_ids[:10] if evidence.anchor_ids else [],
        }

    async def _extract_from_anchors(self, doc_version_id: UUID) -> dict[str, Any]:
        """Извлекает sample_size из statistics zone и arms из IP zone."""
        result: dict[str, Any] = {"citations": {}}

        # Ищем sample_size в statistics zone
        stmt = (
            select(Anchor)
            .where(
                Anchor.doc_version_id == doc_version_id,
                Anchor.source_zone == "statistics",
            )
            .order_by(Anchor.ordinal)
            .limit(50)
        )
        res = await self.db.execute(stmt)
        stats_anchors = res.scalars().all()

        for anchor in stats_anchors:
            text = anchor.text_norm or anchor.text_raw
            if not text:
                continue

            # Паттерны для sample_size
            n_pattern = re.compile(r"\bN\s*=\s*(\d{1,7}(?:[ ,]\d{3})*)\b", re.IGNORECASE)
            total_pattern = re.compile(
                r"\b(?:total|планируемое\s+число|всего)\s+[^0-9]{0,25}(\d{1,7}(?:[ ,]\d{3})*)\b",
                re.IGNORECASE,
            )

            match = n_pattern.search(text) or total_pattern.search(text)
            if match:
                raw_num = match.group(1).replace(" ", "").replace(",", "")
                try:
                    n = int(raw_num)
                    if 1 <= n <= 1_000_000:
                        result["sample_size"] = {"value": n, "unit": "participants"}
                        result["citations"]["sample_size"] = [anchor.anchor_id]
                        break
                except ValueError:
                    pass

        # Ищем arms в IP zone
        stmt = (
            select(Anchor)
            .where(
                Anchor.doc_version_id == doc_version_id,
                Anchor.source_zone == "ip",
            )
            .order_by(Anchor.ordinal)
            .limit(100)
        )
        res = await self.db.execute(stmt)
        ip_anchors = res.scalars().all()

        arms: list[dict[str, Any]] = []
        arm_citations: list[str] = []

        # Упрощенная логика: ищем упоминания групп/arms в тексте
        # В будущем можно улучшить с помощью LLM
        for anchor in ip_anchors:
            text = anchor.text_norm or anchor.text_raw
            if not text:
                continue

            # Паттерны для arms: "Group A", "Arm 1", "Treatment group" и т.д.
            arm_patterns = [
                re.compile(r"\b(?:group|arm|treatment)\s+([A-Z0-9]+)\b", re.IGNORECASE),
                re.compile(r"\b([A-Z0-9]+)\s+(?:group|arm)\b", re.IGNORECASE),
            ]

            for pattern in arm_patterns:
                matches = pattern.findall(text)
                for match in matches:
                    arm_name = match.strip()
                    if arm_name and arm_name not in [a.get("name") for a in arms]:
                        arms.append({"name": arm_name, "dose": None, "regimen": None})
                        arm_citations.append(anchor.anchor_id)

        if arms:
            result["arms"] = arms[:10]  # Ограничиваем 10 arms
            result["citations"]["arms"] = arm_citations[:20]

        return result

    async def _extract_design_from_headers(self, doc_version_id: UUID) -> dict[str, Any]:
        """Извлекает phase и study_design_type из заголовков."""
        result: dict[str, Any] = {"citations": {}}

        # Ищем заголовки
        stmt = (
            select(Anchor)
            .where(
                Anchor.doc_version_id == doc_version_id,
                Anchor.content_type == "hdr",
            )
            .order_by(Anchor.ordinal)
            .limit(100)
        )
        res = await self.db.execute(stmt)
        headers = res.scalars().all()

        # Паттерны для phase
        phase_pattern = re.compile(
            r"\b(?:phase|фаза)\s+([I1-4IV]+|I|II|III|IV|1|2|3|4)\b",
            re.IGNORECASE,
        )

        # Паттерны для study_design_type
        design_patterns = [
            (re.compile(r"\b(randomized|рандомизированн[а-я]+)\b", re.IGNORECASE), "randomized"),
            (re.compile(r"\b(open[-\s]?label|открыт[а-я]+)\b", re.IGNORECASE), "open-label"),
            (re.compile(r"\b(double[-\s]?blind|двойн[а-я]+)\b", re.IGNORECASE), "double-blind"),
            (re.compile(r"\b(single[-\s]?blind|одинарн[а-я]+)\b", re.IGNORECASE), "single-blind"),
            (re.compile(r"\b(placebo[-\s]?controlled|плацебо[-\s]?контролируем[а-я]+)\b", re.IGNORECASE), "placebo-controlled"),
        ]

        for header in headers:
            text = header.text_norm or header.text_raw
            if not text:
                continue

            # Проверяем phase
            if not result.get("phase"):
                phase_match = phase_pattern.search(text)
                if phase_match:
                    phase_val = phase_match.group(1).upper()
                    # Нормализуем римские цифры
                    phase_map = {"I": "I", "II": "II", "III": "III", "IV": "IV", "1": "I", "2": "II", "3": "III", "4": "IV"}
                    result["phase"] = phase_map.get(phase_val, phase_val)
                    result["citations"]["phase"] = [header.anchor_id]

            # Проверяем study_design_type
            if not result.get("study_design_type"):
                for pattern, design_type in design_patterns:
                    if pattern.search(text):
                        result["study_design_type"] = design_type
                        result["citations"]["study_design_type"] = [header.anchor_id]
                        break

            # Если нашли оба, можно прервать
            if result.get("phase") and result.get("study_design_type"):
                break

        return result

    async def save_core_facts(
        self,
        study_id: UUID,
        facts_json: dict[str, Any],
        *,
        doc_version_id: UUID | None = None,
        derived_from_doc_version_id: UUID | None = None,
    ) -> StudyCoreFacts:
        """
        Сохраняет core facts в БД.

        Args:
            study_id: UUID исследования
            facts_json: Словарь с фактами
            doc_version_id: Опциональный doc_version_id для привязки
            derived_from_doc_version_id: doc_version_id, из которого извлечены факты

        Returns:
            Сохраненная запись StudyCoreFacts
        """
        # Получаем следующую версию
        stmt = select(func.max(StudyCoreFacts.facts_version)).where(
            StudyCoreFacts.study_id == study_id
        )
        res = await self.db.execute(stmt)
        max_version = res.scalar_one()
        next_version = (max_version or 0) + 1

        core_facts = StudyCoreFacts(
            study_id=study_id,
            doc_version_id=doc_version_id,
            facts_json=facts_json,
            facts_version=next_version,
            derived_from_doc_version_id=derived_from_doc_version_id,
        )
        self.db.add(core_facts)
        await self.db.flush()

        logger.info(
            f"Сохранены core facts для study_id={study_id}, version={next_version}"
        )
        return core_facts

    async def get_latest_core_facts(
        self,
        study_id: UUID,
        *,
        version: int | None = None,
    ) -> StudyCoreFacts | None:
        """
        Получает последние или указанную версию core facts для исследования.

        Args:
            study_id: UUID исследования
            version: Опциональный номер версии (если None, возвращает latest)

        Returns:
            StudyCoreFacts или None
        """
        if version is not None:
            stmt = select(StudyCoreFacts).where(
                StudyCoreFacts.study_id == study_id,
                StudyCoreFacts.facts_version == version,
            )
        else:
            stmt = (
                select(StudyCoreFacts)
                .where(StudyCoreFacts.study_id == study_id)
                .order_by(StudyCoreFacts.facts_version.desc())
                .limit(1)
            )

        res = await self.db.execute(stmt)
        return res.scalar_one_or_none()

