"""Аудитор для проверки согласованности между Протоколом и ICF (Informed Consent Form)."""

from __future__ import annotations

import re
from uuid import UUID

from difflib import SequenceMatcher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import AuditCategory, AuditSeverity, DocumentType
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact, FactEvidence
from app.db.models.studies import Document, DocumentVersion
from app.schemas.audit import AuditIssue
from app.services.audit.base import BaseAuditor


class ProtocolIcfConsistencyAuditor(BaseAuditor):
    """Проверяет согласованность между Протоколом и ICF.

    Проверки:
    - Процедуры из Protocol должны быть упомянуты в ICF
    - Количество визитов должно совпадать
    - Объем забираемой крови должен совпадать
    """

    @property
    def name(self) -> str:
        return "ProtocolIcfConsistencyAuditor"

    async def run(
        self, primary_doc_version_id: UUID, secondary_doc_version_id: UUID
    ) -> list[AuditIssue]:
        """Запускает проверку согласованности между Протоколом и ICF.

        Args:
            primary_doc_version_id: ID версии Протокола
            secondary_doc_version_id: ID версии ICF

        Returns:
            Список найденных проблем
        """
        logger.info(
            f"[{self.name}] Запуск проверки Protocol ({primary_doc_version_id}) vs "
            f"ICF ({secondary_doc_version_id})"
        )

        issues: list[AuditIssue] = []

        # Проверяем, что документы правильных типов
        protocol_version = await self.db.get(DocumentVersion, primary_doc_version_id)
        icf_version = await self.db.get(DocumentVersion, secondary_doc_version_id)

        if not protocol_version or not icf_version:
            return issues

        protocol_doc = await self.db.get(Document, protocol_version.document_id)
        icf_doc = await self.db.get(Document, icf_version.document_id)

        if not protocol_doc or protocol_doc.doc_type != DocumentType.PROTOCOL:
            logger.warning(f"Документ {primary_doc_version_id} не является Протоколом")
            return issues

        if not icf_doc or icf_doc.doc_type != DocumentType.ICF:
            logger.warning(f"Документ {secondary_doc_version_id} не является ICF")
            return issues

        # Получаем текст ICF для поиска
        icf_text = await self._get_document_text(icf_version.id)

        # 1. Проверка процедур
        issues.extend(
            await self._check_procedures_in_icf(primary_doc_version_id, icf_text, icf_version.id)
        )

        # 2. Проверка количества визитов
        issues.extend(
            await self._check_visit_count_consistency(
                primary_doc_version_id, icf_text, icf_version.id
            )
        )

        # 3. Проверка объема крови
        issues.extend(
            await self._check_blood_volume_consistency(
                primary_doc_version_id, icf_text, icf_version.id
            )
        )

        logger.info(f"[{self.name}] Найдено проблем: {len(issues)}")
        return issues

    async def _check_procedures_in_icf(
        self, protocol_version_id: UUID, icf_text: str, icf_version_id: UUID
    ) -> list[AuditIssue]:
        """Проверяет, что процедуры из Protocol упомянуты в ICF."""
        issues: list[AuditIssue] = []

        # Получаем процедуры из Protocol
        procedures_fact_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "soa",
            Fact.fact_key == "procedures",
            Fact.created_from_doc_version_id == protocol_version_id,
        )
        procedures_result = await self.db.execute(procedures_fact_stmt)
        procedures_fact = procedures_result.scalar_one_or_none()

        if not procedures_fact:
            return issues

        value_json = procedures_fact.value_json
        if not isinstance(value_json, dict) or "procedures" not in value_json:
            return issues

        procedures_data = value_json["procedures"]
        if not isinstance(procedures_data, list):
            return issues

        # Получаем anchor_id процедур из Protocol
        protocol_anchors = await self._get_fact_anchors(procedures_fact.id)

        # Получаем anchor_id из ICF (для локализации проблемы)
        icf_anchors_stmt = select(Anchor.anchor_id).where(Anchor.doc_version_id == icf_version_id)
        icf_anchors_result = await self.db.execute(icf_anchors_stmt)
        icf_anchors = list(icf_anchors_result.scalars().all()[:10])  # Ограничиваем для UI

        # Нормализуем текст ICF для поиска (убираем регистр)
        icf_text_lower = icf_text.lower()

        # Проверяем каждую процедуру
        missing_procedures = []

        for proc in procedures_data:
            if not isinstance(proc, dict):
                continue

            proc_label = proc.get("label", "").strip()
            if not proc_label:
                continue

            # Ищем упоминание процедуры в ICF (fuzzy match)
            found = False

            # Точное совпадение (без учета регистра)
            if proc_label.lower() in icf_text_lower:
                found = True
            else:
                # Fuzzy match: ищем похожие фразы
                words = proc_label.lower().split()
                if len(words) >= 2:
                    # Для многословных процедур ищем каждое слово
                    if all(word in icf_text_lower for word in words if len(word) > 3):
                        found = True
                else:
                    # Для однословных проверяем fuzzy match
                    for sentence in icf_text.split("."):
                        similarity = SequenceMatcher(
                            None, proc_label.lower(), sentence.lower()
                        ).ratio()
                        if similarity > 0.7:  # 70% схожести
                            found = True
                            break

            if not found:
                missing_procedures.append(proc_label)

        if missing_procedures:
            issues.append(
                AuditIssue(
                    severity=AuditSeverity.CRITICAL,
                    category=AuditCategory.COMPLIANCE,
                    description=(
                        f"Процедуры из Протокола не упомянуты в ICF: "
                        f"{', '.join(missing_procedures[:5])}"
                        f"{' и другие...' if len(missing_procedures) > 5 else ''}"
                    ),
                    location_anchors=protocol_anchors[:5] + icf_anchors[:5],
                    suggested_fix=(
                        f"Добавить упоминание следующих процедур в ICF: "
                        f"{', '.join(missing_procedures)}. "
                        f"Все процедуры исследования должны быть описаны в форме информированного согласия."
                    ),
                )
            )

        return issues

    async def _check_visit_count_consistency(
        self, protocol_version_id: UUID, icf_text: str, icf_version_id: UUID
    ) -> list[AuditIssue]:
        """Проверяет соответствие количества визитов между Protocol и ICF."""
        issues: list[AuditIssue] = []

        # Получаем количество визитов из Protocol
        visits_fact_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "soa",
            Fact.fact_key == "visits",
            Fact.created_from_doc_version_id == protocol_version_id,
        )
        visits_result = await self.db.execute(visits_fact_stmt)
        visits_fact = visits_result.scalar_one_or_none()

        if not visits_fact:
            return issues

        value_json = visits_fact.value_json
        if not isinstance(value_json, dict) or "visits" not in value_json:
            return issues

        visits_data = value_json["visits"]
        protocol_visit_count = len(visits_data) if isinstance(visits_data, list) else 0

        if protocol_visit_count == 0:
            return issues

        # Ищем упоминание количества визитов в ICF
        visit_count_patterns = [
            re.compile(rf"{protocol_visit_count}\s*(?:визит|visit)", re.IGNORECASE),
            re.compile(rf"(?:визит|visit).*?{protocol_visit_count}", re.IGNORECASE),
            re.compile(rf"{protocol_visit_count}\s*(?:раз|times)", re.IGNORECASE),
        ]

        found_match = any(pattern.search(icf_text) for pattern in visit_count_patterns)

        protocol_anchors = await self._get_fact_anchors(visits_fact.id)
        icf_anchors_stmt = select(Anchor.anchor_id).where(Anchor.doc_version_id == icf_version_id)
        icf_anchors_result = await self.db.execute(icf_anchors_stmt)
        icf_anchors = list(icf_anchors_result.scalars().all()[:5])

        if not found_match:
            issues.append(
                AuditIssue(
                    severity=AuditSeverity.MAJOR,
                    category=AuditCategory.CONSISTENCY,
                    description=(
                        f"Количество визитов не согласовано: "
                        f"В Протоколе указано {protocol_visit_count} визит(ов), "
                        f"но это количество не упомянуто в ICF"
                    ),
                    location_anchors=protocol_anchors[:5] + icf_anchors,
                    suggested_fix=(
                        f"Указать в ICF точное количество визитов: {protocol_visit_count}. "
                        f"Количество визитов должно совпадать между Протоколом и ICF."
                    ),
                )
            )

        return issues

    async def _check_blood_volume_consistency(
        self, protocol_version_id: UUID, icf_text: str, icf_version_id: UUID
    ) -> list[AuditIssue]:
        """Проверяет соответствие объема забираемой крови."""
        issues: list[AuditIssue] = []

        # Получаем объем крови из Protocol
        blood_volume_fact_stmt = select(Fact).where(
            Fact.study_id == self.study_id,
            Fact.fact_type == "safety",
            Fact.fact_key == "total_blood_volume",
        )
        blood_volume_result = await self.db.execute(blood_volume_fact_stmt)
        blood_volume_fact = blood_volume_result.scalar_one_or_none()

        if not blood_volume_fact:
            return issues

        value_json = blood_volume_fact.value_json
        protocol_volume = self._extract_numeric_value(value_json)

        if protocol_volume is None:
            return issues

        # Ищем упоминание объема крови в ICF
        # Паттерны для поиска: "150 мл", "150ml", "150 миллилитров"
        volume_patterns = [
            re.compile(rf"{int(protocol_volume)}\s*(?:мл|ml|миллилитр)", re.IGNORECASE),
            re.compile(
                rf"(?:заберут|возьмут|забор).*?{int(protocol_volume)}\s*(?:мл|ml)", re.IGNORECASE
            ),
        ]

        found_match = any(pattern.search(icf_text) for pattern in volume_patterns)

        protocol_anchors = await self._get_fact_anchors(blood_volume_fact.id)
        icf_anchors_stmt = select(Anchor.anchor_id).where(Anchor.doc_version_id == icf_version_id)
        icf_anchors_result = await self.db.execute(icf_anchors_stmt)
        icf_anchors = list(icf_anchors_result.scalars().all()[:5])

        if not found_match:
            issues.append(
                AuditIssue(
                    severity=AuditSeverity.MAJOR,
                    category=AuditCategory.CONSISTENCY,
                    description=(
                        f"Объем забираемой крови не согласован: "
                        f"В Протоколе указано {protocol_volume} мл, "
                        f"но это значение не упомянуто в ICF"
                    ),
                    location_anchors=protocol_anchors[:5] + icf_anchors,
                    suggested_fix=(
                        f"Указать в ICF точный объем забираемой крови: {protocol_volume} мл. "
                        f"Объем должен совпадать между Протоколом и ICF."
                    ),
                )
            )

        return issues

    async def _get_document_text(self, doc_version_id: UUID) -> str:
        """Получает весь текст документа из анкоров."""
        stmt = select(Anchor.text_raw).where(Anchor.doc_version_id == doc_version_id)
        result = await self.db.execute(stmt)
        texts = result.scalars().all()
        return "\n".join(texts)

    def _extract_numeric_value(self, value_json: dict) -> float | None:
        """Извлекает числовое значение из value_json."""
        if isinstance(value_json, (int, float)):
            return float(value_json)
        if isinstance(value_json, dict):
            if "value" in value_json:
                val = value_json["value"]
                if isinstance(val, (int, float)):
                    return float(val)
        return None

    async def _get_fact_anchors(self, fact_id: UUID) -> list[str]:
        """Получает список anchor_id для факта из его evidence."""
        stmt = select(FactEvidence.anchor_id).where(FactEvidence.fact_id == fact_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

