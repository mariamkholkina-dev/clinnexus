from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.enums import DocumentLifecycleStatus, DocumentType
from app.db.models.anchors import Anchor
from app.db.models.sections import TargetSectionContract, TargetSectionMap
from app.db.models.studies import Document, DocumentVersion
from app.schemas.sections import (
    AllowedSourcesMVP,
    QCRulesetMVP,
    RequiredFactsMVP,
    RetrievalRecipeMVP,
)
from app.services.zone_config import get_zone_config_service


@dataclass(frozen=True)
class LeanPassport:
    """Нормализованный MVP-паспорт (Lean) с sensible defaults."""

    required_facts: RequiredFactsMVP
    allowed_sources: AllowedSourcesMVP
    retrieval_recipe: RetrievalRecipeMVP
    qc_ruleset: QCRulesetMVP


def normalize_passport(
    *,
    required_facts_json: dict[str, Any] | list[Any] | None,
    allowed_sources_json: dict[str, Any] | None,
    retrieval_recipe_json: dict[str, Any] | None,
    qc_ruleset_json: dict[str, Any] | None,
) -> LeanPassport:
    """Приводит произвольные JSON (в т.ч. legacy/overloaded) к MVP форме.

    Важно: любые новые поля должны быть optional, а дефолты задаются здесь.
    
    Обрабатывает legacy формат, где required_facts_json может содержать:
    - список строк: ["study.phase", "study.design.type"] -> преобразуется в список объектов
    - список объектов: [{"fact_key": "study.phase", ...}] -> используется как есть
    - словарь с ключом "facts": {"facts": [...]} -> используется как есть
    """
    # Нормализуем required_facts_json: обрабатываем legacy формат (список строк)
    normalized_required_facts = required_facts_json or {}
    
    # Если это список (legacy формат), преобразуем в словарь с ключом "facts"
    if isinstance(normalized_required_facts, list):
        # Проверяем, список ли это строк или объектов
        if normalized_required_facts and isinstance(normalized_required_facts[0], str):
            # Legacy формат: список строк -> преобразуем в список объектов
            normalized_required_facts = {
                "facts": [
                    {"fact_key": fact_key, "required": True}
                    for fact_key in normalized_required_facts
                ]
            }
        else:
            # Список объектов -> оборачиваем в словарь
            normalized_required_facts = {"facts": normalized_required_facts}
    elif isinstance(normalized_required_facts, dict) and "facts" in normalized_required_facts:
        # Если это словарь с ключом "facts", проверяем формат элементов
        facts_list = normalized_required_facts["facts"]
        if isinstance(facts_list, list) and facts_list and isinstance(facts_list[0], str):
            # Legacy формат: {"facts": ["study.phase", ...]} -> преобразуем
            normalized_required_facts = {
                "facts": [
                    {"fact_key": fact_key, "required": True}
                    for fact_key in facts_list
                ]
            }
    
    required_facts = RequiredFactsMVP.model_validate(normalized_required_facts)
    allowed_sources = AllowedSourcesMVP.model_validate(allowed_sources_json or {})
    retrieval_recipe = RetrievalRecipeMVP.model_validate(retrieval_recipe_json or {})
    qc_ruleset = QCRulesetMVP.model_validate(qc_ruleset_json or {})
    return LeanPassport(
        required_facts=required_facts,
        allowed_sources=allowed_sources,
        retrieval_recipe=retrieval_recipe,
        qc_ruleset=qc_ruleset,
    )


class LeanContextBuilder:
    """MVP построение контекста: section_maps -> anchors (и при необходимости chunks)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build_context(
        self,
        *,
        study_id: UUID,
        contract: TargetSectionContract,
        source_doc_version_ids: list[UUID],
    ) -> tuple[str, list[str]]:
        """
        Возвращает (context_text, used_anchor_ids).

        Стратегия (Sensible Defaults):
        - Берём якоря из section_maps по dependency_sources.
        - Фильтруем по allowed_content_types (и prefer_content_types приоритетно).
        - Fallback search (если задано) — только внутри разрешённых doc_version_id.
        """
        passport = normalize_passport(
            required_facts_json=contract.required_facts_json,
            allowed_sources_json=contract.allowed_sources_json,
            retrieval_recipe_json=contract.retrieval_recipe_json,
            qc_ruleset_json=contract.qc_ruleset_json,
        )

        # MVP: secure_mode_required
        if passport.retrieval_recipe.security.secure_mode_required:
            # Проверка secure_mode фактически выполняется в API/конфиге для LLM,
            # но здесь фиксируем намерение контракта.
            pass

        # 1) Разрешённые doc_version_id по dependency_sources + scope
        allowed_doc_version_ids = await self._filter_allowed_doc_versions(
            study_id=study_id,
            source_doc_version_ids=source_doc_version_ids,
            allowed_sources=passport.allowed_sources,
        )

        # 2) Выбираем section_keys для маппинга
        section_keys_to_use: list[str] = []
        for ds in passport.allowed_sources.dependency_sources:
            section_keys_to_use.extend(ds.section_keys)
        # Если не задано явно — fallback на текущий section_key контракта
        if not section_keys_to_use:
            section_keys_to_use = [contract.section_key]

        # 3) Читаем section_maps и собираем anchor_ids
        stmt_maps = select(TargetSectionMap).where(
            TargetSectionMap.doc_version_id.in_(allowed_doc_version_ids),
            TargetSectionMap.target_section.in_(section_keys_to_use),
        )
        maps_result = await self.db.execute(stmt_maps)
        maps = maps_result.scalars().all()

        mapped_anchor_ids: list[str] = []
        for m in maps:
            if m.anchor_ids:
                mapped_anchor_ids.extend(m.anchor_ids)

        # 4) Загружаем anchors и фильтруем по content_type и source_zone
        anchors = await self._load_anchors_by_anchor_ids(mapped_anchor_ids)

        prefer = passport.retrieval_recipe.prefer_content_types or []
        prefer_zones = passport.retrieval_recipe.prefer_source_zones or []
        fallback_zones = passport.retrieval_recipe.fallback_source_zones or []
        prefer_lang = passport.retrieval_recipe.language.prefer_language
        max_chars = passport.retrieval_recipe.context_build.max_chars or 12000

        # allowed_content_types: если задано в dependency_sources, используем объединение
        allowed_ct = self._union_allowed_content_types(passport.allowed_sources)

        # Фильтруем по content_type
        filtered = [
            a
            for a in anchors
            if (not allowed_ct or a.content_type.value in allowed_ct)
        ]

        # Стратегия выбора зон: сначала пытаемся собрать из prefer_source_zones, если пусто - fallback_source_zones
        # Также применяем zone_crosswalk для cross-doc retrieval
        zone_config = get_zone_config_service()
        
        # Проверяем, нужен ли cross-doc retrieval (если dependency_sources содержит другой doc_type)
        needs_crosswalk = False
        source_doc_type = None
        target_doc_type = contract.doc_type
        
        for ds in passport.allowed_sources.dependency_sources:
            if ds.doc_type != target_doc_type:
                needs_crosswalk = True
                source_doc_type = ds.doc_type
                break
        
        if needs_crosswalk and source_doc_type:
            # Применяем zone_crosswalk для перевода зон
            crosswalk_zones: list[str] = []
            for source_zone in (prefer_zones or fallback_zones or []):
                crosswalk_result = zone_config.get_crosswalk_zones(
                    source_doc_type=source_doc_type,
                    source_zone=source_zone,
                    target_doc_type=target_doc_type,
                )
                # Берём топ-3 целевые зоны с наибольшим весом
                top_target_zones = [zone for zone, _ in crosswalk_result[:3]]
                crosswalk_zones.extend(top_target_zones)
            
            # Объединяем prefer_zones с переведёнными зонами
            if crosswalk_zones:
                prefer_zones = list(set(prefer_zones + crosswalk_zones))
                logger.info(
                    f"Применён zone_crosswalk: {source_doc_type} -> {target_doc_type}, "
                    f"crosswalk_zones={crosswalk_zones}"
                )
        
        if prefer_zones or fallback_zones:
            # Разделяем anchors по зонам
            prefer_zone_anchors = [
                a for a in filtered
                if a.source_zone in prefer_zones
            ]
            fallback_zone_anchors = [
                a for a in filtered
                if a.source_zone in fallback_zones and a not in prefer_zone_anchors
            ]
            
            # Если есть anchors в prefer зонах - используем их, иначе fallback
            if prefer_zone_anchors:
                filtered = prefer_zone_anchors
            elif fallback_zone_anchors:
                filtered = fallback_zone_anchors
            # Если обе пусты - используем все filtered (без фильтрации по зонам)

        # Фильтрация по языку, если указан prefer_language
        if prefer_lang and prefer_lang != "auto":
            lang_filtered = [
                a for a in filtered
                if a.language.value == prefer_lang
            ]
            # Если нашли anchors с нужным языком - используем их, иначе все
            if lang_filtered:
                filtered = lang_filtered

        # Сортировка: prefer_content_types (например cell) первыми, затем prefer_source_zones, затем ordinal
        prefer_rank = {ct: i for i, ct in enumerate(prefer)}
        zone_rank = {zone: i for i, zone in enumerate(prefer_zones)}
        filtered.sort(
            key=lambda a: (
                prefer_rank.get(a.content_type.value, 10_000),
                zone_rank.get(a.source_zone, 10_000 if prefer_zones else 0),
                a.ordinal,
            )
        )

        context_parts: list[str] = []
        used_anchor_ids: list[str] = []
        total = 0
        for a in filtered:
            piece = a.text_raw.strip()
            if not piece:
                continue
            # лёгкий префикс для дебага/трассировки
            prefix = f"[{a.anchor_id} {a.content_type.value}] "
            chunk = prefix + piece
            if total + len(chunk) + 2 > max_chars:
                break
            context_parts.append(chunk)
            used_anchor_ids.append(a.anchor_id)
            total += len(chunk) + 2

        context_text = "\n\n".join(context_parts)
        if not context_text:
            logger.warning(
                f"Пустой контекст для section_key={contract.section_key} "
                f"(allowed_doc_versions={len(allowed_doc_version_ids)}, maps={len(maps)})"
            )
        return context_text, used_anchor_ids

    async def _filter_allowed_doc_versions(
        self,
        *,
        study_id: UUID,
        source_doc_version_ids: list[UUID],
        allowed_sources: AllowedSourcesMVP,
    ) -> list[UUID]:
        """Фильтрует source_doc_version_ids по scope и doc_type из dependency_sources."""
        if not source_doc_version_ids:
            return []

        # Выбираем версии документов и их doc_type/study через join на Document
        stmt = (
            select(DocumentVersion.id, Document.doc_type, Document.study_id, Document.lifecycle_status)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id.in_(source_doc_version_ids))
        )
        res = await self.db.execute(stmt)
        rows = res.all()

        allowed_doc_types: set[DocumentType] = {ds.doc_type for ds in allowed_sources.dependency_sources}
        if not allowed_doc_types:
            # Если dependency_sources пуст — разрешаем все source versions (в рамках scope)
            allowed_doc_types = set()

        out: list[UUID] = []
        for (vid, doc_type, row_study_id, lifecycle_status) in rows:
            if allowed_sources.document_scope.same_study_only and row_study_id != study_id:
                continue
            if (
                not allowed_sources.document_scope.allow_superseded
                and lifecycle_status == DocumentLifecycleStatus.SUPERSEDED
            ):
                continue
            if allowed_doc_types and doc_type not in allowed_doc_types:
                continue
            out.append(vid)
        return out

    async def _load_anchors_by_anchor_ids(self, anchor_ids: list[str]) -> list[Anchor]:
        if not anchor_ids:
            return []
        # Уникализируем, но сохраняем исходный порядок
        seen: set[str] = set()
        uniq = [a for a in anchor_ids if not (a in seen or seen.add(a))]

        stmt = select(Anchor).where(Anchor.anchor_id.in_(uniq))
        res = await self.db.execute(stmt)
        anchors = res.scalars().all()

        by_id = {a.anchor_id: a for a in anchors}
        return [by_id[a] for a in uniq if a in by_id]

    def _union_allowed_content_types(self, allowed_sources: AllowedSourcesMVP) -> set[str]:
        out: set[str] = set()
        for ds in allowed_sources.dependency_sources:
            for ct in ds.allowed_content_types:
                out.add(ct)
        return out


