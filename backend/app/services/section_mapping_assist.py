"""LLM-assisted section mapping service."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import LLMProvider, settings
from app.core.logging import logger
from app.db.enums import (
    AnchorContentType,
    DocumentLanguage,
    DocumentType,
    SectionMapMappedBy,
    SectionMapStatus,
)
from app.db.models.anchors import Anchor
from app.db.models.sections import SectionContract, SectionMap
from app.db.models.studies import Document, DocumentVersion
from app.services.llm_client import LLMClient
from app.services.section_mapping import DocumentOutline, SectionMappingService
from app.services.section_mapping_qc import QCError, QCResult, SectionMappingQCGate


@dataclass
class SectionQCReport:
    """Отчёт QC для одной секции."""

    status: str  # "mapped" | "needs_review" | "rejected"
    selected_heading_anchor_id: str | None
    errors: list[dict[str, str]]  # [{"type": "...", "message": "..."}]


@dataclass
class AssistResult:
    """Результат LLM assist."""

    version_id: UUID
    document_language: DocumentLanguage
    secure_mode: bool
    llm_used: bool
    candidates: dict[str, list[dict[str, Any]]]  # {section_key: [candidate]}
    qc: dict[str, SectionQCReport]  # {section_key: qc_report}


class SectionMappingAssistService:
    """Сервис для LLM-assisted section mapping."""

    def __init__(self, db: AsyncSession) -> None:
        """
        Инициализация сервиса.

        Args:
            db: AsyncSession для доступа к БД
        """
        self.db = db
        self.mapping_service = SectionMappingService(db)
        self.qc_gate = SectionMappingQCGate(db)

    async def assist(
        self,
        doc_version_id: UUID,
        section_keys: list[str],
        max_candidates_per_section: int = 3,
        allow_visual_headings: bool = False,
        apply: bool = False,
    ) -> AssistResult:
        """
        Выполняет LLM-assisted mapping для указанных секций.

        Args:
            doc_version_id: ID версии документа
            section_keys: Список section_key для маппинга
            max_candidates_per_section: Максимум кандидатов на секцию
            allow_visual_headings: Разрешить визуальные заголовки
            apply: Если True, применить изменения в section_maps

        Returns:
            AssistResult с кандидатами и QC отчётом

        Raises:
            ValueError: Если secure_mode=false или нет ключей
        """
        # 1. Проверяем secure_mode и ключи
        if not settings.secure_mode:
            raise ValueError(
                "SECURE_MODE=false. LLM вызовы запрещены. "
                "Установите SECURE_MODE=true и настройте LLM ключи."
            )

        if not settings.llm_provider or not settings.llm_base_url or not settings.llm_api_key:
            raise ValueError(
                "LLM не настроен. Требуются: LLM_PROVIDER, LLM_BASE_URL, LLM_API_KEY"
            )

        # 2. Получаем версию документа и document
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        document = await self.db.get(Document, doc_version.document_id)
        if not document:
            raise ValueError(f"Document {doc_version.document_id} не найден")

        doc_type = document.doc_type

        # 3. Получаем SectionContracts
        contracts_stmt = select(SectionContract).where(
            SectionContract.doc_type == doc_type,
            SectionContract.section_key.in_(section_keys),
            SectionContract.is_active == True,
        )
        contracts_result = await self.db.execute(contracts_stmt)
        contracts = {c.section_key: c for c in contracts_result.scalars().all()}

        missing_keys = set(section_keys) - set(contracts.keys())
        if missing_keys:
            raise ValueError(f"SectionContracts не найдены для: {missing_keys}")

        # 4. Получаем все anchors
        anchors_stmt = select(Anchor).where(Anchor.doc_version_id == doc_version_id)
        anchors_result = await self.db.execute(anchors_stmt)
        all_anchors = anchors_result.scalars().all()

        all_anchors = sorted(
            all_anchors,
            key=lambda a: a.location_json.get("para_index", 999999)
            if isinstance(a.location_json, dict)
            else 999999,
        )

        # 5. Строим document outline
        outline = self.mapping_service._build_document_outline(all_anchors)

        # 6. Получаем существующие маппинги
        existing_maps_stmt = select(SectionMap).where(
            SectionMap.doc_version_id == doc_version_id
        )
        existing_maps_result = await self.db.execute(existing_maps_stmt)
        existing_maps = {m.section_key: m for m in existing_maps_result.scalars().all()}

        # 7. Формируем payload для LLM
        headings_payload = self._build_headings_payload(outline, all_anchors)
        contracts_payload = self._build_contracts_payload(contracts, doc_version.document_language)

        # 8. Вызываем LLM
        request_id = str(uuid.uuid4())
        llm_client = LLMClient()
        system_prompt = self._build_system_prompt(max_candidates_per_section, doc_version.document_language)
        user_prompt = {
            "document_language": doc_version.document_language.value,
            "headings": headings_payload,
            "contracts": contracts_payload,
        }

        logger.debug(
            "[Assist] LLM payload подготовлен "
            f"(request_id={request_id}, doc_version_id={doc_version_id}, "
            f"sections={len(section_keys)}, headings={len(headings_payload)})"
        )
        if logger.isEnabledFor(10):  # DEBUG
            logger.debug(
                "[Assist] sections=" + ", ".join(section_keys)
                + f"; doc_language={doc_version.document_language.value}"
            )

        try:
            llm_response = await llm_client.generate_candidates(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                request_id=request_id,
            )
            llm_used = True
        except Exception as e:
            # Важно: не маскируем сбой LLM "успешным" 200-ответом.
            # Smoke-check ожидает llm_used=true при успешном вызове, поэтому
            # в случае проблем с LLM поднимаем исключение, чтобы API вернул 5xx.
            logger.exception(f"[Assist] Ошибка вызова LLM (request_id={request_id})")
            raise RuntimeError(f"LLM вызов не удался (request_id={request_id})") from e

        # 9. Прогоняем QC Gate для каждого кандидата
        qc_reports: dict[str, SectionQCReport] = {}
        candidates_dict: dict[str, list[dict[str, Any]]] = {}

        for section_key in section_keys:
            contract = contracts[section_key]
            llm_candidates = llm_response.candidates.get(section_key, [])
            logger.debug(
                f"[Assist] section_key={section_key} получено кандидатов от LLM={len(llm_candidates)} "
                f"(request_id={request_id})"
            )

            # Валидируем, что все heading_anchor_id существуют
            valid_candidates = []
            available_anchor_ids = {a.anchor_id for a in all_anchors}
            for candidate in llm_candidates:
                if candidate.heading_anchor_id in available_anchor_ids:
                    valid_candidates.append(candidate)
                else:
                    logger.warning(
                        f"[Assist] LLM предложил несуществующий anchor_id: "
                        f"{candidate.heading_anchor_id} для {section_key}"
                    )
            if logger.isEnabledFor(10):  # DEBUG
                logger.debug(
                    f"[Assist] section_key={section_key} валидных кандидатов={len(valid_candidates)} "
                    f"(max_candidates_per_section={max_candidates_per_section})"
                )

            candidates_dict[section_key] = [
                {
                    "heading_anchor_id": c.heading_anchor_id,
                    "confidence": c.confidence,
                    "rationale": c.rationale,
                }
                for c in valid_candidates[:max_candidates_per_section]
            ]

            # Прогоняем QC Gate для каждого кандидата (сверху вниз)
            selected_result: QCResult | None = None
            for candidate in valid_candidates[:max_candidates_per_section]:
                logger.debug(
                    "[Assist] QC validate_candidate "
                    f"(section_key={section_key}, heading_anchor_id={candidate.heading_anchor_id}, "
                    f"confidence={candidate.confidence:.2f}, request_id={request_id})"
                )
                qc_result = await self.qc_gate.validate_candidate(
                    doc_version_id=doc_version_id,
                    section_key=section_key,
                    heading_anchor_id=candidate.heading_anchor_id,
                    contract=contract,
                    all_anchors=all_anchors,
                    outline=outline,
                    existing_maps=existing_maps,
                    document_language=doc_version.document_language,
                )
                logger.debug(
                    "[Assist] QC result "
                    f"(section_key={section_key}, heading_anchor_id={candidate.heading_anchor_id}, "
                    f"status={qc_result.status}, errors={len(qc_result.errors)})"
                )

                # Выбираем первый прошедший mapped или needs_review
                if qc_result.status in ("mapped", "needs_review"):
                    selected_result = qc_result
                    break

            # Если ни один не прошёл, берём первый (rejected)
            if selected_result is None and valid_candidates:
                first_candidate = valid_candidates[0]
                selected_result = await self.qc_gate.validate_candidate(
                    doc_version_id=doc_version_id,
                    section_key=section_key,
                    heading_anchor_id=first_candidate.heading_anchor_id,
                    contract=contract,
                    all_anchors=all_anchors,
                    outline=outline,
                    existing_maps=existing_maps,
                    document_language=doc_version.document_language,
                )

            # Формируем QC отчёт
            if selected_result:
                logger.debug(
                    "[Assist] selected кандидат "
                    f"(section_key={section_key}, status={selected_result.status}, "
                    f"heading_anchor_id={selected_result.selected_heading_anchor_id})"
                )
                qc_reports[section_key] = SectionQCReport(
                    status=selected_result.status,
                    selected_heading_anchor_id=selected_result.selected_heading_anchor_id,
                    errors=[
                        {"type": e.type, "message": e.message} for e in selected_result.errors
                    ],
                )
            else:
                qc_reports[section_key] = SectionQCReport(
                    status="rejected",
                    selected_heading_anchor_id=None,
                    errors=[{"type": "no_candidates", "message": "Нет валидных кандидатов"}],
                )

        # 10. Если apply=true, обновляем section_maps
        if apply:
            logger.info(
                f"[Assist] apply=true: применяем section_maps (doc_version_id={doc_version_id}, "
                f"sections={len(section_keys)}, request_id={request_id})"
            )
            await self._apply_mappings(
                doc_version_id=doc_version_id,
                qc_reports=qc_reports,
                contracts=contracts,
                all_anchors=all_anchors,
                outline=outline,
                existing_maps=existing_maps,
                request_id=request_id,
            )
        else:
            logger.info(
                f"[Assist] apply=false: изменения не применяются (doc_version_id={doc_version_id}, request_id={request_id})"
            )

        return AssistResult(
            version_id=doc_version_id,
            document_language=doc_version.document_language,
            secure_mode=True,
            llm_used=llm_used,
            candidates=candidates_dict,
            qc={
                section_key: SectionQCReport(
                    status=report.status,
                    selected_heading_anchor_id=report.selected_heading_anchor_id,
                    errors=report.errors,
                )
                for section_key, report in qc_reports.items()
            },
        )

    def _build_headings_payload(
        self, outline: DocumentOutline, all_anchors: list[Anchor]
    ) -> list[dict[str, Any]]:
        """Формирует payload заголовков для LLM."""
        headings: list[dict[str, Any]] = []
        anchor_dict = {a.anchor_id: a for a in all_anchors}

        for heading_anchor, level in outline.headings:
            # Получаем сниппет (1-2 paragraph после заголовка)
            snippet = self.qc_gate._get_snippet(heading_anchor, all_anchors)

            headings.append(
                {
                    "heading_anchor_id": heading_anchor.anchor_id,
                    "heading_text": heading_anchor.text_norm,
                    "level": level,
                    "section_path": heading_anchor.section_path,
                    "snippet": snippet[:300],  # Ограничиваем 300 символами
                }
            )

        return headings

    def _build_contracts_payload(
        self, contracts: dict[str, SectionContract], document_language: DocumentLanguage
    ) -> list[dict[str, Any]]:
        """Формирует payload контрактов для LLM с учетом языка документа."""
        contracts_list: list[dict[str, Any]] = []
        mapping_service = SectionMappingService(self.db)

        for section_key, contract in contracts.items():
            recipe = contract.retrieval_recipe_json
            # Получаем language-aware signals
            signals = mapping_service._get_signals(recipe, document_language)

            contracts_list.append(
                {
                    "section_key": section_key,
                    "title": contract.title,
                    "keywords": {
                        "must": signals.must_keywords,
                        "should": signals.should_keywords,
                        "not": signals.not_keywords,
                    },
                    "regex": {"heading": signals.regex_patterns},
                    "description": contract.title,  # Используем title как описание
                }
            )

        return contracts_list

    def _build_system_prompt(self, max_candidates: int, document_language: DocumentLanguage) -> str:
        """Формирует системный промпт для LLM с учетом языка документа."""
        lang_instruction = ""
        if document_language == DocumentLanguage.RU:
            lang_instruction = "\nВАЖНО: Документ на русском языке. Выбирай кандидаты заголовков на русском языке."
        elif document_language == DocumentLanguage.EN:
            lang_instruction = "\nВАЖНО: Документ на английском языке. Выбирай кандидаты заголовков на английском языке."
        elif document_language == DocumentLanguage.MIXED:
            lang_instruction = "\nВАЖНО: Документ смешанного языка (RU/EN). Выбирай кандидаты заголовков, соответствующие keywords в нужном языке."

        return f"""Ты ассистент для маппинга секций документа.{lang_instruction}

Твоя задача: предложить кандидатов заголовков (heading_anchor_id) для каждой секции из списка contracts.

ВАЖНО:
- Возвращай ТОЛЬКО валидный JSON по схеме ниже
- НЕ выдумывай anchor_id - используй ТОЛЬКО те, что есть в списке headings
- Для каждой секции предложи до {max_candidates} кандидатов, отсортированных по confidence (убывание)
- confidence должен быть от 0.0 до 1.0
- rationale должен быть коротким (≤200 символов)
- Учитывай document_language из payload при выборе кандидатов

Схема ответа:
{{
  "candidates": {{
    "<section_key>": [
      {{
        "heading_anchor_id": "<string from headings list>",
        "confidence": 0.0-1.0,
        "rationale": "<short explanation>"
      }}
    ]
  }}
}}"""

    async def _apply_mappings(
        self,
        doc_version_id: UUID,
        qc_reports: dict[str, SectionQCReport],
        contracts: dict[str, SectionContract],
        all_anchors: list[Anchor],
        outline: DocumentOutline,
        existing_maps: dict[str, SectionMap],
        request_id: str,
    ) -> None:
        """
        Применяет маппинги в section_maps (только для status=mapped и derived_confidence >= 0.75).

        Args:
            doc_version_id: ID версии документа
            qc_reports: Отчёты QC для каждой секции
            contracts: SectionContracts
            all_anchors: Все anchors
            outline: Структура документа
            existing_maps: Существующие маппинги
            request_id: ID запроса для логирования
        """
        for section_key, qc_report in qc_reports.items():
            # Пропускаем overridden маппинги
            if section_key in existing_maps:
                existing_map = existing_maps[section_key]
                if existing_map.status == SectionMapStatus.OVERRIDDEN:
                    logger.info(
                        f"[Assist] Пропуск overridden маппинга для {section_key} "
                        f"(request_id={request_id})"
                    )
                    continue

            # Применяем только если status=mapped
            if qc_report.status != "mapped":
                logger.info(
                    f"[Assist] Пропуск применения для {section_key}: status={qc_report.status} "
                    f"(request_id={request_id})"
                )
                continue

            if not qc_report.selected_heading_anchor_id:
                continue

            # Находим heading anchor
            heading_anchor = None
            for anchor in all_anchors:
                if anchor.anchor_id == qc_report.selected_heading_anchor_id:
                    heading_anchor = anchor
                    break

            if not heading_anchor:
                logger.warning(
                    f"[Assist] Не найден heading_anchor для {qc_report.selected_heading_anchor_id} "
                    f"(request_id={request_id})"
                )
                continue

            # Захватываем блок
            contract = contracts[section_key]
            heading_level = self.qc_gate._extract_heading_level(heading_anchor)
            heading_para_index = heading_anchor.location_json.get("para_index", 0)

            # Находим следующий заголовок
            end_para_index = None
            for anchor, level in outline.headings:
                anchor_para_index = anchor.location_json.get("para_index", 0)
                if anchor_para_index > heading_para_index and level <= heading_level:
                    end_para_index = anchor_para_index
                    break

            # Собираем anchor_ids
            anchor_ids: list[str] = []
            for anchor in all_anchors:
                para_index = anchor.location_json.get("para_index", 0)
                if para_index >= heading_para_index:
                    if end_para_index is None or para_index < end_para_index:
                        anchor_ids.append(anchor.anchor_id)
                    else:
                        break

            # Вычисляем confidence (используем QC derived_confidence)
            # Для этого нужно получить QCResult заново
            doc_version_result = await self.db.get(DocumentVersion, doc_version_id)
            qc_result = await self.qc_gate.validate_candidate(
                doc_version_id=doc_version_id,
                section_key=section_key,
                heading_anchor_id=qc_report.selected_heading_anchor_id,
                contract=contract,
                all_anchors=all_anchors,
                outline=outline,
                existing_maps=existing_maps,
                document_language=doc_version_result.document_language if doc_version_result else DocumentLanguage.UNKNOWN,
            )

            confidence = qc_result.derived_confidence
            if confidence < 0.75:
                logger.info(
                    f"[Assist] Пропуск применения для {section_key}: "
                    f"confidence={confidence} < 0.75 (request_id={request_id})"
                )
                continue

            # Обновляем или создаём SectionMap
            if section_key in existing_maps:
                section_map = existing_maps[section_key]
                section_map.anchor_ids = anchor_ids
                section_map.confidence = confidence
                section_map.status = SectionMapStatus.MAPPED
                section_map.mapped_by = SectionMapMappedBy.SYSTEM
                section_map.notes = (
                    f"LLM assist used (request_id={request_id}); "
                    f"qc_status=mapped; confidence={confidence:.2f}"
                )
            else:
                section_map = SectionMap(
                    doc_version_id=doc_version_id,
                    section_key=section_key,
                    anchor_ids=anchor_ids,
                    chunk_ids=None,
                    confidence=confidence,
                    status=SectionMapStatus.MAPPED,
                    mapped_by=SectionMapMappedBy.SYSTEM,
                    notes=(
                        f"LLM assist used (request_id={request_id}); "
                        f"qc_status=mapped; confidence={confidence:.2f}"
                    ),
                )
                self.db.add(section_map)

        await self.db.commit()
        logger.info(f"[Assist] Применены маппинги (request_id={request_id})")

