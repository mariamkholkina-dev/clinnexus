from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import ImpactStatus, RecommendedAction, TaskStatus, TaskType
from app.db.models.anchors import Anchor
from app.db.models.anchor_matches import AnchorMatch
from app.db.models.change import ChangeEvent, ImpactItem, Task
from app.db.models.facts import Fact, FactEvidence
from app.db.models.generation import GeneratedTargetSection, GenerationRun
from app.db.models.studies import Document
from app.db.models.topics import TopicEvidence
from app.schemas.impact import ImpactItemOut
from app.services.anchor_aligner import AnchorAligner


class ImpactService:
    """Сервис для вычисления воздействия изменений документов."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def compute_impact(
        self, change_event_id: UUID
    ) -> list[ImpactItemOut]:
        """
        Вычисляет воздействие изменения документа на другие документы.
        
        Использует anchor_matches для определения измененных якорей и находит
        затронутые факты и секции.
        """
        logger.info(f"Вычисление воздействия для change_event {change_event_id}")

        # Получаем change_event
        change_event = await self.db.get(ChangeEvent, change_event_id)
        if not change_event:
            raise ValueError(f"ChangeEvent {change_event_id} не найден")

        # Шаг 1: Получаем AnchorMatch со score < 0.95 (изменённые/удалённые)
        stmt = select(AnchorMatch).where(
            AnchorMatch.from_doc_version_id == change_event.from_version_id,
            AnchorMatch.to_doc_version_id == change_event.to_version_id,
            AnchorMatch.score < 0.95,
        )
        result = await self.db.execute(stmt)
        matches = result.scalars().all()

        if not matches:
            logger.info("Вычисление воздействия: нет изменённых anchor_matches")
            return []

        # Карта anchor_id -> score
        changed_anchors: dict[str, float] = {m.from_anchor_id: m.score for m in matches}

        # Шаг 2: Находим GeneratedTargetSection, которые ссылаются на старые anchor_ids
        stmt_sections = (
            select(GeneratedTargetSection, GenerationRun)
            .join(GenerationRun, GeneratedTargetSection.generation_run_id == GenerationRun.id)
            .where(GenerationRun.study_id == change_event.study_id)
        )
        sec_res = await self.db.execute(stmt_sections)
        rows = sec_res.all()

        impact_items: list[ImpactItem] = []

        for section, gen_run in rows:
            anchors_in_section = self._extract_anchor_ids_from_artifacts(section.artifacts_json)
            impacted = anchors_in_section & set(changed_anchors.keys())
            if not impacted:
                continue

            # Создаём ImpactItem для каждого затронутого anchor_id
            for aid in impacted:
                impact_item = ImpactItem(
                    change_event_id=change_event_id,
                    affected_doc_type=gen_run.target_doc_type,
                    affected_target_section=gen_run.target_section,
                    reason_json={
                        "type": "source_changed",
                        "anchor_id": aid,
                        "change_score": changed_anchors.get(aid, 0.0),
                    },
                    recommended_action=RecommendedAction.MANUAL_REVIEW,
                    status=ImpactStatus.PENDING,
                )
                impact_items.append(impact_item)
                self.db.add(impact_item)

        # Создаём Task для пользователя
        if impact_items:
            task = Task(
                study_id=change_event.study_id,
                type=TaskType.REVIEW_IMPACT,
                status=TaskStatus.OPEN,
                payload_json={
                    "title": "Review impact on CSR sections due to Protocol v2 update",
                    "change_event_id": str(change_event_id),
                    "impact_items_count": len(impact_items),
                },
            )
            self.db.add(task)
            logger.info(
                f"Создана задача review_impact для change_event {change_event_id}, "
                f"затронуто элементов: {len(impact_items)}"
            )

        await self.db.commit()

        logger.info(
            f"Вычисление воздействия завершено: {len(impact_items)} элементов"
        )
        return [ImpactItemOut.model_validate(item) for item in impact_items]

    async def _find_affected_facts(
        self, study_id: UUID, anchor_ids: set[str]
    ) -> list[Fact]:
        """Находит факты, которые ссылаются на указанные anchor_ids."""
        if not anchor_ids:
            return []

        # Получаем fact_evidence для этих anchor_ids
        stmt = select(FactEvidence).where(
            FactEvidence.anchor_id.in_(list(anchor_ids))  # type: ignore
        )
        result = await self.db.execute(stmt)
        evidence_items = result.scalars().all()

        # Получаем уникальные fact_id
        fact_ids = {ev.fact_id for ev in evidence_items}

        if not fact_ids:
            return []

        # Получаем факты
        stmt = select(Fact).where(
            Fact.study_id == study_id,
            Fact.id.in_(list(fact_ids)),  # type: ignore
        )
        result = await self.db.execute(stmt)
        facts = result.scalars().all()

        return list(facts)

    async def _find_affected_topics(
        self, doc_version_id: UUID, anchor_ids: set[str]
    ) -> list[TopicEvidence]:
        """Находит topic_evidence, которые ссылаются на указанные anchor_ids."""
        if not anchor_ids:
            return []

        # Получаем topic_evidence для этой версии
        stmt = select(TopicEvidence).where(
            TopicEvidence.doc_version_id == doc_version_id
        )
        result = await self.db.execute(stmt)
        all_topic_evidences = result.scalars().all()

        # Фильтруем те, которые содержат затронутые anchor_ids
        affected: list[TopicEvidence] = []
        for topic_ev in all_topic_evidences:
            if set(topic_ev.anchor_ids) & anchor_ids:
                affected.append(topic_ev)

        return affected

    async def _find_updated_fact_ids(
        self,
        study_id: UUID,
        changed_anchor_ids: set[str],
    ) -> set[str]:
        """
        Находит fact_key фактов, которые были обновлены через fact_evidence.
        
        Args:
            study_id: ID исследования
            changed_anchor_ids: Множество измененных anchor_id
            
        Returns:
            Множество fact_key обновленных фактов
        """
        if not changed_anchor_ids:
            return set()

        # Получаем fact_evidence для измененных anchor_ids
        stmt = select(FactEvidence.fact_id).where(
            FactEvidence.anchor_id.in_(list(changed_anchor_ids))  # type: ignore
        )
        result = await self.db.execute(stmt)
        fact_ids = {row[0] for row in result}

        if not fact_ids:
            return set()

        # Получаем fact_key для этих фактов
        stmt = select(Fact.fact_key).where(
            Fact.study_id == study_id,
            Fact.id.in_(list(fact_ids)),  # type: ignore
        )
        result = await self.db.execute(stmt)
        fact_keys = {row[0] for row in result}

        return fact_keys

    async def _find_affected_generated_sections(
        self,
        study_id: UUID,
        changed_anchor_ids: set[str],
        updated_fact_keys: set[str],
    ) -> list[GeneratedTargetSection]:
        """
        Находит GeneratedTargetSection, которые ссылаются на измененные anchor_ids или обновленные факты.
        
        Args:
            study_id: ID исследования
            changed_anchor_ids: Множество измененных anchor_id
            updated_fact_keys: Множество fact_key обновленных фактов
            
        Returns:
            Список затронутых GeneratedTargetSection
        """
        # Получаем все GeneratedTargetSection для этого study_id
        stmt = (
            select(GeneratedTargetSection)
            .join(GenerationRun)
            .where(GenerationRun.study_id == study_id)
        )
        result = await self.db.execute(stmt)
        all_sections = result.scalars().all()

        affected_sections: list[GeneratedTargetSection] = []

        for section in all_sections:
            artifacts_json = section.artifacts_json if isinstance(section.artifacts_json, dict) else {}

            # Извлекаем anchor_ids из artifacts_json
            section_anchor_ids = self._extract_anchor_ids_from_artifacts(artifacts_json)

            # Извлекаем fact_keys из artifacts_json
            section_fact_keys = self._extract_fact_keys_from_artifacts(artifacts_json)

            # Проверяем, есть ли пересечение с измененными anchor_ids или обновленными фактами
            if (section_anchor_ids & changed_anchor_ids) or (section_fact_keys & updated_fact_keys):
                affected_sections.append(section)

        return affected_sections

    def _extract_anchor_ids_from_artifacts(
        self,
        artifacts_json: dict[str, Any],
    ) -> set[str]:
        """
        Извлекает все anchor_id из artifacts_json.
        
        Args:
            artifacts_json: JSON с артефактами генерации
            
        Returns:
            Множество anchor_id
        """
        anchor_ids: set[str] = set()

        # Извлекаем из citations (legacy)
        if "citations" in artifacts_json and isinstance(artifacts_json["citations"], list):
            anchor_ids.update(artifacts_json["citations"])

        # Извлекаем из claim_items
        if "claim_items" in artifacts_json and isinstance(artifacts_json["claim_items"], list):
            for claim in artifacts_json["claim_items"]:
                if isinstance(claim, dict) and "anchor_ids" in claim:
                    if isinstance(claim["anchor_ids"], list):
                        anchor_ids.update(claim["anchor_ids"])

        # Извлекаем из citation_items
        if "citation_items" in artifacts_json and isinstance(artifacts_json["citation_items"], list):
            for citation in artifacts_json["citation_items"]:
                if isinstance(citation, dict) and "anchor_id" in citation:
                    anchor_ids.add(citation["anchor_id"])

        return anchor_ids

    def _extract_fact_keys_from_artifacts(
        self,
        artifacts_json: dict[str, Any],
    ) -> set[str]:
        """
        Извлекает все fact_key из artifacts_json.
        
        Args:
            artifacts_json: JSON с артефактами генерации
            
        Returns:
            Множество fact_key
        """
        fact_keys: set[str] = set()

        # Извлекаем из claim_items.fact_refs
        if "claim_items" in artifacts_json and isinstance(artifacts_json["claim_items"], list):
            for claim in artifacts_json["claim_items"]:
                if isinstance(claim, dict) and "fact_refs" in claim:
                    if isinstance(claim["fact_refs"], list):
                        fact_keys.update(claim["fact_refs"])

        return fact_keys

