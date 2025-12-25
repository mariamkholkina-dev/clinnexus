from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import DocumentType, ImpactStatus, RecommendedAction, TaskStatus, TaskType
from app.db.models.anchors import Anchor
from app.db.models.anchor_matches import AnchorMatch
from app.db.models.change import ChangeEvent, ImpactItem, Task
from app.db.models.facts import Fact, FactEvidence
from app.db.models.generation import GeneratedTargetSection, GenerationRun
from app.db.models.studies import Document, DocumentVersion
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
        затронутые секции, которые ссылаются на эти якоря.
        """
        logger.info(f"Вычисление воздействия для change_event {change_event_id}")

        # Получаем change_event
        change_event = await self.db.get(ChangeEvent, change_event_id)
        if not change_event:
            raise ValueError(f"ChangeEvent {change_event_id} не найден")

        # Получаем версию документа для получения version_label
        to_version = await self.db.get(DocumentVersion, change_event.to_version_id)
        if not to_version:
            raise ValueError(f"DocumentVersion {change_event.to_version_id} не найден")
        version_label = to_version.version_label

        # Шаг 1: Получаем все AnchorMatch для этого change_event
        stmt_matches = select(AnchorMatch).where(
            AnchorMatch.from_doc_version_id == change_event.from_version_id,
            AnchorMatch.to_doc_version_id == change_event.to_version_id,
        )
        result_matches = await self.db.execute(stmt_matches)
        all_matches = result_matches.scalars().all()

        # Карта from_anchor_id -> (score, to_anchor_id)
        anchor_matches_map: dict[str, tuple[float, str]] = {
            m.from_anchor_id: (m.score, m.to_anchor_id) for m in all_matches
        }

        # Получаем все anchor_id из старой версии для проверки отсутствующих
        stmt_old_anchors = select(Anchor.anchor_id).where(
            Anchor.doc_version_id == change_event.from_version_id
        )
        result_old_anchors = await self.db.execute(stmt_old_anchors)
        all_old_anchor_ids = {row[0] for row in result_old_anchors}

        # Определяем измененные и удаленные anchor_id
        changed_anchor_ids: set[str] = set()  # anchor_id с score < 0.95
        deleted_anchor_ids: set[str] = set()  # anchor_id из v1, отсутствующие в AnchorMatch

        for anchor_id in all_old_anchor_ids:
            if anchor_id in anchor_matches_map:
                score, _ = anchor_matches_map[anchor_id]
                if score < 0.95:
                    changed_anchor_ids.add(anchor_id)
            else:
                # Якорь был в v1, но отсутствует в AnchorMatch - значит удален
                deleted_anchor_ids.add(anchor_id)

        affected_anchor_ids = changed_anchor_ids | deleted_anchor_ids

        if not affected_anchor_ids:
            logger.info("Нет измененных или удаленных якорей")
            return []

        # Получаем информацию о якорях для создания описаний
        stmt_anchor_info = select(
            Anchor.anchor_id,
            Anchor.section_path,
            Anchor.ordinal,
            Anchor.content_type,
        ).where(Anchor.doc_version_id == change_event.from_version_id)
        result_anchor_info = await self.db.execute(stmt_anchor_info)
        anchor_info_map: dict[str, dict[str, Any]] = {}
        for row in result_anchor_info:
            anchor_info_map[row[0]] = {
                "section_path": row[1],
                "ordinal": row[2],
                "content_type": row[3].value if hasattr(row[3], "value") else str(row[3]),
            }

        # Шаг 2: Находим все GeneratedTargetSection для этого исследования
        stmt_sections = (
            select(GeneratedTargetSection, GenerationRun)
            .join(GenerationRun, GeneratedTargetSection.generation_run_id == GenerationRun.id)
            .where(GenerationRun.study_id == change_event.study_id)
        )
        sec_res = await self.db.execute(stmt_sections)
        rows = sec_res.all()

        # Группируем изменения по секциям
        # Структура: {(target_doc_type, target_section): [список измененных якорей]}
        sections_changes: dict[tuple[str, str], list[dict[str, Any]]] = {}

        for section, gen_run in rows:
            # Извлекаем все anchor_id из artifacts_json (используем существующий метод)
            artifacts_json = section.artifacts_json if isinstance(section.artifacts_json, dict) else {}
            section_anchor_ids = self._extract_anchor_ids_from_artifacts(artifacts_json)

            # Находим пересечение с измененными/удаленными якорями
            affected_in_section = section_anchor_ids & affected_anchor_ids

            if affected_in_section:
                section_key = (gen_run.target_doc_type.value, gen_run.target_section)
                if section_key not in sections_changes:
                    sections_changes[section_key] = []

                # Формируем описания изменений для каждого якоря
                for anchor_id in affected_in_section:
                    anchor_info = anchor_info_map.get(anchor_id, {})
                    section_path = anchor_info.get("section_path", "неизвестный раздел")
                    ordinal = anchor_info.get("ordinal", 0)
                    content_type = anchor_info.get("content_type", "p")

                    if anchor_id in changed_anchor_ids:
                        score, _ = anchor_matches_map[anchor_id]
                        change_percent = int((1 - score) * 100)
                        # Формируем описание в зависимости от типа контента
                        if content_type == "li":
                            description = f"Изменился текст пункта №{ordinal} в разделе {section_path} (изменение на {change_percent}%)"
                        elif content_type == "p":
                            description = f"Изменился текст параграфа в разделе {section_path} (изменение на {change_percent}%)"
                        else:
                            description = f"Изменился текст элемента в разделе {section_path} (изменение на {change_percent}%)"
                    else:  # deleted
                        if content_type == "li":
                            description = f"Удален пункт №{ordinal} в разделе {section_path}"
                        elif content_type == "p":
                            description = f"Удален параграф в разделе {section_path}"
                        else:
                            description = f"Удален элемент в разделе {section_path}"

                    sections_changes[section_key].append({
                        "anchor_id": anchor_id,
                        "section_path": section_path,
                        "description": description,
                        "ordinal": ordinal,
                        "content_type": content_type,
                        "is_deleted": anchor_id in deleted_anchor_ids,
                    })

        # Шаг 3: Создаем ImpactItem для каждой затронутой секции
        impact_items: list[ImpactItem] = []
        affected_sections: set[tuple[str, str]] = set()

        for (target_doc_type_str, target_section), changes in sections_changes.items():
            target_doc_type = DocumentType(target_doc_type_str)

            # Создаем один ImpactItem на секцию со списком изменений
            impact_item = ImpactItem(
                change_event_id=change_event_id,
                affected_doc_type=target_doc_type,
                affected_target_section=target_section,
                reason_json={
                    "type": "source_changed",
                    "changed_anchors": changes,
                    "total_changed": len(changes),
                },
                recommended_action=RecommendedAction.REGENERATE_DRAFT,
                status=ImpactStatus.PENDING,
            )
            impact_items.append(impact_item)
            self.db.add(impact_item)
            affected_sections.add((target_doc_type_str, target_section))

        # Шаг 4: Создаем системную задачу Task для пользователя
        if affected_sections:
            task = Task(
                study_id=change_event.study_id,
                type=TaskType.REVIEW_IMPACT,
                status=TaskStatus.OPEN,
                payload_json={
                    "title": f"Протокол обновлен до {version_label}. Требуется проверить изменения в затронутых секциях",
                    "change_event_id": str(change_event_id),
                    "version_label": version_label,
                    "affected_sections": [
                        {"doc_type": dt, "section": s} for dt, s in affected_sections
                    ],
                },
            )
            self.db.add(task)
            logger.info(
                f"Создана задача review_impact для change_event {change_event_id}"
            )

        await self.db.commit()

        logger.info(
            f"Вычисление воздействия завершено: {len(impact_items)} элементов, "
            f"{len(affected_sections)} затронутых секций"
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

