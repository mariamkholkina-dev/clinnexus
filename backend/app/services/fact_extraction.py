from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.models.facts import Fact, FactEvidence
from app.db.models.studies import DocumentVersion, Study
from app.db.enums import EvidenceRole, FactStatus


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

    async def extract_and_upsert(self, doc_version_id: UUID) -> FactExtractionResult:
        """
        Извлекает факты из документа и сохраняет их в БД.

        TODO: Реальная реализация должна:
        - Использовать LLM для извлечения фактов из anchors/chunks
        - Сопоставлять с required_facts_json из section_contracts
        - Создавать Fact записи с evidence
        - Обновлять существующие факты при необходимости
        """
        logger.info(f"Извлечение фактов из документа {doc_version_id}")

        # Получаем версию документа
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        study_id = doc_version.document.study_id

        # TODO: Реальная логика извлечения фактов
        # Здесь должна быть логика:
        # 1. Получить anchors/chunks для документа
        # 2. Использовать LLM для извлечения фактов
        # 3. Создать Fact записи
        # 4. Создать FactEvidence записи

        # Заглушка: создаём несколько тестовых фактов
        facts_created = []

        # Пример факта
        fact = Fact(
            study_id=study_id,
            fact_type="sample_size",
            fact_key="primary_endpoint",
            value_json={"value": 100, "unit": "patients"},
            unit="patients",
            status=FactStatus.EXTRACTED,
            created_from_doc_version_id=doc_version_id,
        )
        self.db.add(fact)
        await self.db.flush()

        # Создаём evidence
        evidence = FactEvidence(
            fact_id=fact.id,
            anchor_id="anchor_1",  # TODO: реальный anchor_id
            evidence_role=EvidenceRole.PRIMARY,
        )
        self.db.add(evidence)

        facts_created.append(fact)

        await self.db.commit()

        logger.info(f"Извлечено {len(facts_created)} фактов из {doc_version_id}")

        return FactExtractionResult(
            doc_version_id=doc_version_id,
            facts_count=len(facts_created),
            facts=facts_created,
        )

