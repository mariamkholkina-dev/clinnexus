from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import logger
from app.db.models.generation import GenerationRun, GeneratedTargetSection, Template
from app.db.models.sections import TargetSectionContract
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact, FactEvidence
from app.db.enums import FactStatus, GenerationStatus, QCStatus
from app.schemas.generation import (
    ArtifactsSchema,
    ClaimArtifact,
    GenerateSectionRequest,
    GenerateSectionResult,
    QCReportSchema,
    QCErrorSchema,
)
from app.services.lean_passport import LeanContextBuilder, normalize_passport
from app.services.core_facts_extractor import CoreFactsExtractor


class GenerationService:
    """Сервис для генерации секций документов."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate_section(
        self,
        req: GenerateSectionRequest,
        *,
        byo_key: str | None = None,
    ) -> GenerateSectionResult:
        """
        Генерирует секцию документа на основе шаблона и источников.

        TODO: Реальная реализация должна:
        - Получить template и contract
        - Использовать RetrievalService для получения релевантных chunks
        - Вызвать LLM для генерации контента
        - Извлечь artifacts (claims, numbers, citations)
        - Вызвать ValidationService для QC
        - Сохранить GenerationRun и GeneratedSection
        """
        logger.info(f"Генерация секции {req.section_key} для study {req.study_id}")

        # Получаем template
        template = await self.db.get(Template, req.template_id)
        if not template:
            raise ValueError(f"Template {req.template_id} не найден")

        # Получаем contract
        contract = await self.db.get(TargetSectionContract, req.contract_id)
        if not contract:
            raise ValueError(f"TargetSectionContract {req.contract_id} не найден")

        passport = normalize_passport(
            required_facts_json=contract.required_facts_json,
            allowed_sources_json=contract.allowed_sources_json,
            retrieval_recipe_json=contract.retrieval_recipe_json,
            qc_ruleset_json=contract.qc_ruleset_json,
        )

        # MVP: "secure_mode_required" теперь означает "требуется BYO ключ".
        # Никаких флагов окружения для этого gate не используем.
        if passport.retrieval_recipe.security.secure_mode_required and not (byo_key or "").strip():
            qc_report = QCReportSchema(
                status=QCStatus.BLOCKED,
                errors=[
                    QCErrorSchema(
                        type="byo_key_required",
                        message="Контракт требует BYO ключ (X-LLM-API-Key) для secure_mode_required=true",
                    )
                ],
            )
            # Создаём run/section как blocked для трассировки
            generation_run = GenerationRun(
                study_id=req.study_id,
                target_doc_type=req.target_doc_type,
                section_key=req.section_key,
                template_id=req.template_id,
                contract_id=req.contract_id,
                input_snapshot_json={
                    "source_doc_version_ids": [str(vid) for vid in req.source_doc_version_ids],
                    "user_instruction": req.user_instruction,
                },
                status=GenerationStatus.BLOCKED,
            )
            self.db.add(generation_run)
            await self.db.flush()
            generated_section = GeneratedTargetSection(
                generation_run_id=generation_run.id,
                content_text="",
                artifacts_json=ArtifactsSchema().model_dump(),
                qc_status=qc_report.status,
                qc_report_json=qc_report.model_dump(),
            )
            self.db.add(generated_section)
            await self.db.commit()
            return GenerateSectionResult(
                content_text="",
                artifacts_json=ArtifactsSchema(),
                qc_status=qc_report.status,
                qc_report_json=qc_report,
                generation_run_id=generation_run.id,
            )

        # Создаём GenerationRun
        generation_run = GenerationRun(
            study_id=req.study_id,
            target_doc_type=req.target_doc_type,
            section_key=req.section_key,
            template_id=req.template_id,
            contract_id=req.contract_id,
            input_snapshot_json={
                "source_doc_version_ids": [str(vid) for vid in req.source_doc_version_ids],
                "user_instruction": req.user_instruction,
            },
            status=GenerationStatus.RUNNING,
        )
        self.db.add(generation_run)
        await self.db.flush()

        # MVP: контекст строим детерминированно по passport.allowed_sources + section_maps
        context_builder = LeanContextBuilder(self.db)
        context_text, used_anchor_ids = await context_builder.build_context(
            study_id=req.study_id,
            contract=contract,
            source_doc_version_ids=req.source_doc_version_ids,
        )

        # Получаем core facts для включения в промпт
        core_facts_extractor = CoreFactsExtractor(self.db)
        core_facts = await core_facts_extractor.get_latest_core_facts(req.study_id)
        core_facts_json = core_facts.facts_json if core_facts else {}

        # Формируем промпт с core facts
        # В реальной реализации это будет частью LLM промпта
        core_facts_prompt = self._format_core_facts_for_prompt(core_facts_json)

        # TODO: Здесь будет инстанцирование LLM-клиента с учётом BYO ключа (byo_key),
        # без логирования/персистинга ключа и с безопасной конфигурацией провайдера.
        # В промпт LLM должен быть включен:
        # - core_facts_json (компактный JSON)
        # - Инструкция: "Hard constraints: do not contradict these facts; if conflict found in sources, mark conflict instead of guessing."

        # MVP one-shot structured output:
        # В полноценном режиме здесь должен быть LLM, но для MVP-каркаса
        # делаем детерминированный черновик, чтобы QC мог отработать.
        content_text = template.template_body if getattr(template, "template_body", None) else ""
        if not content_text:
            # Включаем core facts в контекст для демонстрации
            content_text = f"Черновик секции {req.section_key}.\n\n{core_facts_prompt}\n\n{context_text[:2000]}"

        # Post-processing: детерминированная подстановка фактов из core_facts_json
        content_text, core_fact_anchor_ids = self.replace_fact_placeholders(
            content_text, core_facts_json
        )

        # Fact-Injection: замена {{fact:fact_key}} по верифицированным фактам (core_facts или Facts.status=validated)
        content_text, injected_fact_anchor_ids = await self._inject_verified_facts(
            content_text=content_text,
            study_id=req.study_id,
            core_facts_json=core_facts_json,
        )

        # Проверяем конфликты между core facts и контекстом
        detected_conflicts = await self._detect_conflicts_with_core_facts(
            core_facts_json=core_facts_json,
            context_text=context_text,
            study_id=req.study_id,
        )

        # Объединяем anchor_ids из контекста и из фактов
        all_citation_anchor_ids = list(used_anchor_ids[:10])
        # core_fact_anchor_ids — из core_facts_json["citations"] при подстановке плейсхолдеров
        all_citation_anchor_ids.extend(core_fact_anchor_ids)
        # injected_fact_anchor_ids — из FactEvidence при validated фактах
        all_citation_anchor_ids.extend(injected_fact_anchor_ids)
        # Удаляем дубликаты с сохранением порядка
        seen_citations: set[str] = set()
        unique_citations: list[str] = []
        for aid in all_citation_anchor_ids:
            if aid not in seen_citations:
                seen_citations.add(aid)
                unique_citations.append(aid)

        artifacts = ArtifactsSchema(
            claim_items=[
                ClaimArtifact(
                    text=f"Секция {req.section_key} сформирована на основе предоставленного контекста.",
                    anchor_ids=used_anchor_ids[:3],
                    fact_refs=[],
                    numbers=[],
                )
            ],
            citations=unique_citations,
            core_facts_used=bool(core_facts_json),
            detected_conflicts=detected_conflicts,
        )

        # Вызываем валидацию
        validation_service = ValidationService(self.db)
        qc_report = await validation_service.validate(
            content_text=content_text,
            artifacts_json=artifacts.model_dump(),
            contract_id=req.contract_id,
            study_id=req.study_id,
            source_doc_version_ids=req.source_doc_version_ids,
        )

        # Обновляем artifacts с конфликтами из QC (если есть)
        # Конфликты уже добавлены в artifacts_json внутри ValidationService

        # Обновляем статус
        if qc_report.status == QCStatus.BLOCKED:
            generation_run.status = GenerationStatus.BLOCKED
        else:
            generation_run.status = GenerationStatus.COMPLETED

        # Сохраняем конфликты в input_snapshot_json для трассировки
        if artifacts.detected_conflicts:
            if "conflicts" not in generation_run.input_snapshot_json:
                generation_run.input_snapshot_json["conflicts"] = []
            generation_run.input_snapshot_json["conflicts"] = artifacts.detected_conflicts

        # Создаём GeneratedTargetSection
        generated_section = GeneratedTargetSection(
            generation_run_id=generation_run.id,
            content_text=content_text,
            artifacts_json=artifacts.model_dump(),
            qc_status=qc_report.status,
            qc_report_json=qc_report.model_dump(),
        )
        self.db.add(generated_section)

        await self.db.commit()
        await self.db.refresh(generation_run)

        logger.info(f"Генерация завершена: run_id={generation_run.id}")

        return GenerateSectionResult(
            content_text=content_text,
            artifacts_json=artifacts,
            qc_status=qc_report.status,
            qc_report_json=qc_report,
            generation_run_id=generation_run.id,
        )

    def _format_core_facts_for_prompt(self, core_facts_json: dict[str, Any]) -> str:
        """Форматирует core facts для включения в промпт LLM."""
        if not core_facts_json:
            return ""

        lines = ["=== Основные факты исследования (Hard Constraints) ==="]
        lines.append("Эти факты должны быть согласованы во всех секциях. При обнаружении конфликта в источниках - пометьте конфликт вместо угадывания.")
        lines.append("")
        available_keys = [k for k in core_facts_json.keys() if k != "citations"] if core_facts_json else []
        keys_str = ", ".join(available_keys) if available_keys else "нет доступных fact_key"
        lines.append(
            "You are a GxP-compliant writer. When mentioning core study parameters like Sample Size (N), Phase, or Arms, "
            "you MUST use placeholders like {{fact:sample_size}} instead of writing numbers. "
            f"Available fact keys: [{keys_str}]."
        )
        lines.append("")

        if core_facts_json.get("study_title"):
            lines.append(f"Название исследования: {core_facts_json['study_title']}")

        if core_facts_json.get("phase"):
            lines.append(f"Фаза: {core_facts_json['phase']}")

        if core_facts_json.get("study_design_type"):
            lines.append(f"Тип дизайна: {core_facts_json['study_design_type']}")

        if core_facts_json.get("sample_size"):
            sample = core_facts_json["sample_size"]
            lines.append(f"Размер выборки: {sample.get('value')} {sample.get('unit', '')}")

        if core_facts_json.get("primary_endpoints"):
            endpoints = core_facts_json["primary_endpoints"]
            lines.append(f"Первичные endpoints: {', '.join(endpoints[:5])}")

        if core_facts_json.get("arms"):
            arms = core_facts_json["arms"]
            arm_names = [a.get("name", "") for a in arms if a.get("name")]
            if arm_names:
                lines.append(f"Группы исследования: {', '.join(arm_names)}")

        lines.append("")
        return "\n".join(lines)

    async def _detect_conflicts_with_core_facts(
        self,
        *,
        core_facts_json: dict[str, Any],
        context_text: str,
        study_id: UUID,
    ) -> list[dict[str, Any]]:
        """
        Обнаруживает конфликты между core facts и контекстом/источниками.

        MVP: упрощенная проверка на основе ключевых чисел и названий.
        """
        conflicts: list[dict[str, Any]] = []

        if not core_facts_json:
            return conflicts

        # Проверяем sample_size
        if core_facts_json.get("sample_size"):
            expected_n = core_facts_json["sample_size"].get("value")
            if expected_n:
                # Ищем упоминания N в контексте
                import re
                n_patterns = [
                    re.compile(rf"\bN\s*=\s*(\d+)\b", re.IGNORECASE),
                    re.compile(rf"\b(?:total|всего)\s+(\d+)\s+(?:participants|участников)\b", re.IGNORECASE),
                ]
                for pattern in n_patterns:
                    matches = pattern.findall(context_text)
                    for match in matches:
                        found_n = int(match)
                        if found_n != expected_n:
                            conflicts.append({
                                "fact_key": "sample_size",
                                "expected": expected_n,
                                "found": found_n,
                                "severity": "high",
                            })
                            break

        # Проверяем primary_endpoints
        if core_facts_json.get("primary_endpoints"):
            expected_endpoints = set(core_facts_json["primary_endpoints"])
            # Упрощенная проверка: ищем упоминания endpoints в контексте
            # В реальной реализации это должно быть более сложным
            pass

        return conflicts

    def replace_fact_placeholders(
        self,
        text: str,
        core_facts_json: dict[str, Any],
    ) -> tuple[str, list[str]]:
        """
        Детерминированно заменяет плейсхолдеры {{fact:fact_key}} значениями из core_facts_json.
        Возвращает обновленный текст и список anchor_id из core_facts_json["citations"] для замененных фактов.
        """
        import re

        if not text or not core_facts_json:
            return text, []

        citations_map = core_facts_json.get("citations") or {}
        if not isinstance(citations_map, dict):
            citations_map = {}

        placeholder_re = re.compile(r"\{\{fact:([^}]+)\}\}")
        matches = placeholder_re.findall(text)
        if not matches:
            return text, []

        replaced_anchor_ids: list[str] = []
        result_text = text

        for fact_key in set(matches):
            if fact_key not in core_facts_json:
                continue
            value_json = core_facts_json[fact_key]
            replacement = self._format_fact_value(value_json)
            placeholder = f"{{{{fact:{fact_key}}}}}"
            result_text = result_text.replace(placeholder, replacement)

            # Добавляем цитаты, если есть
            fact_citations = citations_map.get(fact_key) or []
            if isinstance(fact_citations, list):
                for aid in fact_citations:
                    if isinstance(aid, str):
                        replaced_anchor_ids.append(aid)

            logger.debug(
                f"Post-processing: заменен плейсхолдер {placeholder} -> '{replacement}' "
                f"(fact_key={fact_key}, citations={len(fact_citations) if isinstance(fact_citations, list) else 0})"
            )

        # Убираем дубликаты anchor_id с сохранением порядка
        unique_anchor_ids = list(dict.fromkeys(replaced_anchor_ids))
        return result_text, unique_anchor_ids

    async def _inject_verified_facts(
        self,
        *,
        content_text: str,
        study_id: UUID,
        core_facts_json: dict[str, Any],
    ) -> tuple[str, list[str]]:
        """
        Заменяет {{fact:fact_key}} значениями из core_facts_json или Facts (status=validated).
        Возвращает обновленный текст и anchor_ids (из core_facts_json.citations или fact_evidence).
        """
        import re

        if not content_text:
            return content_text, []

        citations_map = core_facts_json.get("citations") if core_facts_json else {}
        if not isinstance(citations_map, dict):
            citations_map = {}

        placeholder_re = re.compile(r"\{\{fact:([^}]+)\}\}")
        matches = placeholder_re.findall(content_text)
        if not matches:
            return content_text, []

        replaced_anchor_ids: list[str] = []
        result_text = content_text

        for fact_key in set(matches):
            replacement: str | None = None
            fact_anchor_ids: list[str] = []

            # 1) Пробуем core_facts_json
            if core_facts_json and fact_key in core_facts_json:
                value_json = core_facts_json[fact_key]
                replacement = self._format_fact_value(value_json)
                fact_citations = citations_map.get(fact_key) or []
                if isinstance(fact_citations, list):
                    for aid in fact_citations:
                        if isinstance(aid, str):
                            fact_anchor_ids.append(aid)

            # 2) Если нет в core_facts_json, ищем в Facts (validated)
            if replacement is None:
                stmt = (
                    select(Fact)
                    .where(
                        Fact.study_id == study_id,
                        Fact.fact_key == fact_key,
                        Fact.status == FactStatus.VALIDATED,
                    )
                    .order_by(Fact.updated_at.desc())
                )
                res = await self.db.execute(stmt)
                fact = res.scalars().first()
                if fact:
                    replacement = self._format_fact_value(fact.value_json, fact.unit)
                    ev_stmt = select(FactEvidence.anchor_id).where(FactEvidence.fact_id == fact.id)
                    ev_res = await self.db.execute(ev_stmt)
                    fact_anchor_ids.extend([row[0] for row in ev_res.all()])

            # 3) Применяем замену, если есть replacement
            if replacement is not None:
                placeholder = f"{{{{fact:{fact_key}}}}}"
                result_text = result_text.replace(placeholder, replacement)
                replaced_anchor_ids.extend(fact_anchor_ids)
                logger.debug(
                    f"Fact injection: {placeholder} -> '{replacement}' "
                    f"(fact_key={fact_key}, anchors={len(fact_anchor_ids)})"
                )

        # Дедуп anchor_ids с сохранением порядка
        unique_anchor_ids = list(dict.fromkeys(replaced_anchor_ids))
        return result_text, unique_anchor_ids

    async def _replace_fact_placeholders(
        self,
        content_text: str,
        study_id: UUID,
        core_facts_json: dict[str, Any],
    ) -> tuple[str, list[str]]:
        """
        Заменяет плейсхолдеры {{fact:fact_key}} на реальные значения из фактов.
        
        Args:
            content_text: Текст с плейсхолдерами
            study_id: ID исследования
            core_facts_json: Core facts из CoreFactsExtractor
            
        Returns:
            (обновленный текст, список anchor_id из фактов для citations)
        """
        import re
        
        # Регулярное выражение для поиска плейсхолдеров {{fact:fact_key}}
        placeholder_pattern = re.compile(r'\{\{fact:([^}]+)\}\}')
        
        fact_anchor_ids: list[str] = []
        replacements: list[tuple[str, str]] = []  # (placeholder, replacement)
        
        # Находим все плейсхолдеры
        matches = placeholder_pattern.findall(content_text)
        unique_fact_keys = list(set(matches))
        
        if not unique_fact_keys:
            return content_text, []
        
        # Ищем факты для каждого fact_key
        for fact_key in unique_fact_keys:
            fact_value = None
            fact_anchor_ids_for_key: list[str] = []
            
            # Сначала проверяем core_facts_json
            if core_facts_json:
                fact_value = self._get_fact_value_from_core_facts(core_facts_json, fact_key)
            
            # Если не найдено в core_facts_json, ищем в таблице facts
            if fact_value is None:
                fact_value, fact_anchor_ids_for_key = await self._get_fact_from_db(
                    study_id=study_id,
                    fact_key=fact_key,
                )
            
            if fact_value is not None:
                # Заменяем плейсхолдер на значение
                placeholder = f"{{{{fact:{fact_key}}}}}"
                replacements.append((placeholder, fact_value))
                fact_anchor_ids.extend(fact_anchor_ids_for_key)
                logger.debug(
                    f"Заменён плейсхолдер {placeholder} на значение '{fact_value}' "
                    f"(fact_key={fact_key}, anchors={len(fact_anchor_ids_for_key)})"
                )
            else:
                logger.warning(
                    f"Плейсхолдер {{fact:{fact_key}}} не найден в core_facts_json или таблице facts"
                )
        
        # Выполняем замены
        result_text = content_text
        for placeholder, replacement in replacements:
            result_text = result_text.replace(placeholder, replacement)
        
        # Удаляем дубликаты anchor_ids
        unique_anchor_ids = list(dict.fromkeys(fact_anchor_ids))  # Сохраняет порядок
        
        return result_text, unique_anchor_ids

    def _get_fact_value_from_core_facts(
        self,
        core_facts_json: dict[str, Any],
        fact_key: str,
    ) -> str | None:
        """
        Извлекает значение факта из core_facts_json.
        
        Args:
            core_facts_json: JSON с core facts
            fact_key: Ключ факта для поиска
            
        Returns:
            Отформатированное значение или None
        """
        # Прямое совпадение ключа
        if fact_key in core_facts_json:
            value = core_facts_json[fact_key]
            return self._format_fact_value(value)
        
        # Специальные случаи для известных ключей
        if fact_key == "sample_size" and "sample_size" in core_facts_json:
            sample = core_facts_json["sample_size"]
            if isinstance(sample, dict):
                value = sample.get("value")
                unit = sample.get("unit", "")
                if value is not None:
                    return f"{value} {unit}".strip()
        
        if fact_key == "phase" and "phase" in core_facts_json:
            phase = core_facts_json["phase"]
            if phase:
                return str(phase)
        
        if fact_key.startswith("primary_endpoint_"):
            # primary_endpoint_1, primary_endpoint_2 и т.д.
            if "primary_endpoints" in core_facts_json:
                endpoints = core_facts_json["primary_endpoints"]
                if isinstance(endpoints, list):
                    try:
                        idx = int(fact_key.split("_")[-1]) - 1
                        if 0 <= idx < len(endpoints):
                            return str(endpoints[idx])
                    except (ValueError, IndexError):
                        pass
        
        # Проверяем вложенные структуры (например, arms)
        if "arms" in core_facts_json:
            arms = core_facts_json["arms"]
            if isinstance(arms, list):
                for arm in arms:
                    if isinstance(arm, dict) and arm.get("name") == fact_key:
                        return arm.get("name", "")
        
        return None

    async def _get_fact_from_db(
        self,
        study_id: UUID,
        fact_key: str,
    ) -> tuple[str | None, list[str]]:
        """
        Получает факт из таблицы facts по study_id и fact_key.
        
        Args:
            study_id: ID исследования
            fact_key: Ключ факта
            
        Returns:
            (отформатированное значение, список anchor_id из evidence)
        """
        # Ищем факт с подходящим статусом (validated или extracted)
        stmt = (
            select(Fact)
            .where(
                Fact.study_id == study_id,
                Fact.fact_key == fact_key,
                Fact.status.in_([FactStatus.VALIDATED, FactStatus.EXTRACTED]),
            )
            .order_by(Fact.status.desc(), Fact.updated_at.desc())  # Приоритет validated
        )
        result = await self.db.execute(stmt)
        fact = result.scalars().first()
        
        if not fact:
            return None, []
        
        # Форматируем значение
        formatted_value = self._format_fact_value(fact.value_json, fact.unit)
        
        # Получаем anchor_ids из evidence
        evidence_stmt = select(FactEvidence.anchor_id).where(
            FactEvidence.fact_id == fact.id
        )
        evidence_result = await self.db.execute(evidence_stmt)
        anchor_ids = [row[0] for row in evidence_result.all()]
        
        return formatted_value, anchor_ids

    def _format_fact_value(
        self,
        value_json: dict[str, Any] | Any,
        unit: str | None = None,
    ) -> str:
        """
        Форматирует значение факта из value_json в читаемую строку.
        
        Args:
            value_json: JSON значение факта
            unit: Единица измерения (опционально)
            
        Returns:
            Отформатированная строка
        """
        if isinstance(value_json, dict):
            # Если это словарь, пытаемся извлечь значение
            if "value" in value_json:
                value = value_json["value"]
            elif "name" in value_json:
                value = value_json["name"]
            elif len(value_json) == 1:
                # Если один ключ, используем его значение
                value = next(iter(value_json.values()))
            else:
                # Множественные поля - форматируем как JSON
                import json
                value = json.dumps(value_json, ensure_ascii=False)
        elif isinstance(value_json, list):
            # Список - объединяем через запятую
            value = ", ".join(str(v) for v in value_json)
        else:
            value = str(value_json)
        
        # Добавляем единицу измерения, если есть
        if unit:
            return f"{value} {unit}".strip()
        return str(value)


class ValidationService:
    """Сервис для валидации сгенерированного контента."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def validate(
        self,
        content_text: str,
        artifacts_json: dict[str, Any],
        contract_id: UUID,
        study_id: UUID | None = None,
        source_doc_version_ids: list[UUID] | None = None,
    ) -> QCReportSchema:
        """
        Валидирует сгенерированный контент по правилам из contract.

        TODO: Реальная реализация должна:
        - Получить qc_ruleset_json из TargetSectionContract
        - Проверить соответствие required_facts_json
        - Проверить citation_policy
        - Вернуть QCReport с ошибками
        """
        logger.info(f"Валидация контента по contract {contract_id}")

        contract = await self.db.get(TargetSectionContract, contract_id)
        if not contract:
            return QCReportSchema(
                status=QCStatus.FAILED,
                errors=[
                    QCErrorSchema(
                        type="contract_not_found",
                        message=f"TargetSectionContract {contract_id} не найден",
                    )
                ],
            )

        passport = normalize_passport(
            required_facts_json=contract.required_facts_json,
            allowed_sources_json=contract.allowed_sources_json,
            retrieval_recipe_json=contract.retrieval_recipe_json,
            qc_ruleset_json=contract.qc_ruleset_json,
        )

        artifacts = ArtifactsSchema.model_validate(artifacts_json or {})
        errors: list[QCErrorSchema] = []

        # 1) input_qc: все anchor_ids существуют
        cited_anchor_ids = self._collect_anchor_ids(artifacts)
        if cited_anchor_ids:
            existing = await self._fetch_existing_anchor_ids(cited_anchor_ids)
            missing = [a for a in cited_anchor_ids if a not in existing]
            if missing:
                errors.append(
                    QCErrorSchema(
                        type="missing_anchor_ids",
                        message="Некоторые anchor_id отсутствуют в БД",
                        anchor_ids=missing[:50],
                    )
                )

        # 2) input_qc: anchor_ids принадлежат разрешённым источникам
        if study_id is not None and source_doc_version_ids is not None and cited_anchor_ids:
            allowed_doc_versions = await self._allowed_doc_versions_for_qc(
                study_id=study_id,
                source_doc_version_ids=source_doc_version_ids,
                passport=passport,
            )
            bad = await self._anchors_outside_allowed_sources(
                anchor_ids=cited_anchor_ids,
                allowed_doc_version_ids=set(allowed_doc_versions),
            )
            if bad:
                errors.append(
                    QCErrorSchema(
                        type="anchor_outside_allowed_sources",
                        message="Есть ссылки на anchor_id вне разрешённых источников",
                        anchor_ids=bad[:50],
                    )
                )

        # 3) citation_qc: per_claim => каждый claim должен иметь >=1 anchor_id
        if contract.citation_policy.value == "per_claim":
            if not artifacts.claim_items:
                errors.append(
                    QCErrorSchema(
                        type="missing_claim_items",
                        message="citation_policy=per_claim требует artifacts.claim_items[]",
                    )
                )
            else:
                missing_claim_citations = [
                    i
                    for i, c in enumerate(artifacts.claim_items)
                    if not c.anchor_ids
                ]
                if missing_claim_citations:
                    errors.append(
                        QCErrorSchema(
                            type="claim_missing_citation",
                            message="Некоторые claims не имеют anchor_ids при per_claim",
                        )
                    )

        # 4) required_facts: наличие фактов с min_status
        if study_id is not None and passport.required_facts.facts:
            missing_facts = await self._missing_required_facts(
                study_id=study_id,
                passport=passport,
            )
            if missing_facts:
                errors.append(
                    QCErrorSchema(
                        type="missing_required_facts",
                        message="Отсутствуют required факты с минимальным статусом",
                    )
                )

        # 5) numbers_match_facts: сравниваем только явно размеченные числа
        if study_id is not None and passport.qc_ruleset.numbers_match_facts:
            number_mismatches = await self._numbers_mismatch_facts(
                study_id=study_id,
                artifacts=artifacts,
            )
            if number_mismatches:
                errors.append(
                    QCErrorSchema(
                        type="numbers_mismatch_facts",
                        message="Некоторые числа не совпали с фактами (MVP сравнение по явной разметке)",
                    )
                )

        # 6) check_zone_conflicts: проверка конфликтов фактов из разных source_zone
        blocking_conflicts: list[dict[str, Any]] = []
        if study_id is not None and passport.qc_ruleset.check_zone_conflicts:
            from app.services.fact_conflict_detector import FactConflictDetector

            conflict_detector = FactConflictDetector(self.db)
            conflict_result = await conflict_detector.detect(
                study_id=study_id,
                doc_version_ids=source_doc_version_ids,
                prefer_source_zones=passport.retrieval_recipe.prefer_source_zones or [],
            )

            if conflict_result.conflicts:
                # Сохраняем конфликты в artifacts для трассировки (если artifacts - это объект)
                if hasattr(artifacts, "detected_conflicts"):
                    artifacts.detected_conflicts.extend([c.model_dump() for c in conflict_result.conflicts])
                # Также сохраняем в artifacts_json для совместимости
                if "detected_conflicts" not in artifacts_json:
                    artifacts_json["detected_conflicts"] = []
                artifacts_json["detected_conflicts"].extend([c.model_dump() for c in conflict_result.conflicts])

                # Формируем ошибки QC
                for conflict in conflict_result.conflicts:
                    if conflict.severity == "block":
                        blocking_conflicts.append(conflict.model_dump())
                        errors.append(
                            QCErrorSchema(
                                type="fact_conflict",
                                message=f"Обнаружен конфликт факта '{conflict.fact_key}': {len(conflict.values)} различных значений",
                                anchor_ids=[
                                    aid for ev in conflict.evidence for aid in ev.anchor_ids
                                ],
                                details=conflict.model_dump(),
                            )
                        )
                    else:
                        errors.append(
                            QCErrorSchema(
                                type="fact_conflict_warning",
                                message=f"Предупреждение: возможный конфликт факта '{conflict.fact_key}'",
                                anchor_ids=[
                                    aid for ev in conflict.evidence for aid in ev.anchor_ids
                                ],
                                details=conflict.model_dump(),
                            )
                        )

        status = QCStatus.PASSED if not errors else QCStatus.FAILED
        # Gate policy MVP: missing_required_fact -> blocked, low_mapping_confidence -> blocked
        for e in errors:
            if e.type in ("missing_required_facts",):
                if passport.qc_ruleset.gate_policy.on_missing_required_fact == "blocked":
                    status = QCStatus.BLOCKED
            if e.type in ("anchor_outside_allowed_sources", "missing_anchor_ids"):
                if passport.qc_ruleset.gate_policy.on_low_mapping_confidence == "blocked":
                    status = QCStatus.BLOCKED
            if e.type in ("claim_missing_citation", "missing_claim_items"):
                if passport.qc_ruleset.gate_policy.on_citation_missing == "fail":
                    status = QCStatus.FAILED
            if e.type == "fact_conflict":
                # Конфликты фактов -> BLOCKED + задача resolve_conflict
                status = QCStatus.BLOCKED

        qc_report = QCReportSchema(status=status, errors=errors)
        logger.info(f"Валидация завершена: {qc_report.status}")

        # Создаём задачи для блокирующих конфликтов
        if blocking_conflicts and study_id is not None:
            await self._create_conflict_tasks(study_id=study_id, conflicts=blocking_conflicts)

        return qc_report

    def _collect_anchor_ids(self, artifacts: ArtifactsSchema) -> list[str]:
        out: list[str] = []
        out.extend(artifacts.citations or [])
        for c in artifacts.claim_items or []:
            out.extend(c.anchor_ids or [])
        for ci in artifacts.citation_items or []:
            out.append(ci.anchor_id)
        # Уникализация с сохранением порядка
        seen: set[str] = set()
        return [a for a in out if a and not (a in seen or seen.add(a))]

    async def _fetch_existing_anchor_ids(self, anchor_ids: list[str]) -> set[str]:
        stmt = select(Anchor.anchor_id).where(Anchor.anchor_id.in_(anchor_ids))
        res = await self.db.execute(stmt)
        return {row[0] for row in res.all()}

    async def _allowed_doc_versions_for_qc(
        self,
        *,
        study_id: UUID,
        source_doc_version_ids: list[UUID],
        passport: Any,
    ) -> list[UUID]:
        # Используем ту же логику, что и контекст-билдер.
        builder = LeanContextBuilder(self.db)
        return await builder._filter_allowed_doc_versions(  # noqa: SLF001 (локально в MVP)
            study_id=study_id,
            source_doc_version_ids=source_doc_version_ids,
            allowed_sources=passport.allowed_sources,
        )

    async def _anchors_outside_allowed_sources(
        self,
        *,
        anchor_ids: list[str],
        allowed_doc_version_ids: set[UUID],
    ) -> list[str]:
        if not anchor_ids or not allowed_doc_version_ids:
            return []
        stmt = select(Anchor.anchor_id, Anchor.doc_version_id).where(
            Anchor.anchor_id.in_(anchor_ids)
        )
        res = await self.db.execute(stmt)
        bad: list[str] = []
        for aid, vid in res.all():
            if vid not in allowed_doc_version_ids:
                bad.append(aid)
        return bad

    async def _missing_required_facts(self, *, study_id: UUID, passport: Any) -> list[str]:
        # MVP: simple rank ordering for FactStatus
        rank = {
            "conflicting": 0,
            "tbd": 1,
            "needs_review": 1,
            "extracted": 2,
            "validated": 3,
        }
        missing: list[str] = []
        for spec in passport.required_facts.facts:
            if not spec.required:
                continue
            min_rank = rank.get(spec.min_status, 2)
            stmt = select(Fact.fact_key, Fact.status).where(
                Fact.study_id == study_id,
                Fact.fact_key == spec.fact_key,
            )
            res = await self.db.execute(stmt)
            rows = res.all()
            ok = False
            for _, st in rows:
                st_rank = rank.get(st.value, 0)
                if st_rank >= min_rank:
                    ok = True
                    break
            if not ok:
                missing.append(spec.fact_key)
        return missing

    async def _numbers_mismatch_facts(
        self,
        *,
        study_id: UUID,
        artifacts: ArtifactsSchema,
    ) -> list[dict[str, Any]]:
        # MVP: сравниваем только claim_items[].numbers где указан fact_key
        mismatches: list[dict[str, Any]] = []
        for claim in artifacts.claim_items or []:
            for num in claim.numbers or []:
                if not num.fact_key:
                    continue
                stmt = select(Fact.value_json, Fact.unit).where(
                    Fact.study_id == study_id,
                    Fact.fact_key == num.fact_key,
                )
                res = await self.db.execute(stmt)
                row = res.first()
                if not row:
                    mismatches.append({"fact_key": num.fact_key, "reason": "fact_not_found"})
                    continue
                value_json, unit = row
                fact_val = value_json.get("value")
                if fact_val != num.value or (num.unit and unit and num.unit != unit):
                    mismatches.append(
                        {
                            "fact_key": num.fact_key,
                            "expected": {"value": fact_val, "unit": unit},
                            "got": {"value": num.value, "unit": num.unit},
                        }
                    )
        return mismatches

    async def _create_conflict_tasks(
        self,
        *,
        study_id: UUID,
        conflicts: list[dict[str, Any]],
    ) -> None:
        """Создаёт задачи для разрешения конфликтов фактов."""
        from app.db.enums import TaskStatus, TaskType
        from app.db.models.change import Task

        for conflict in conflicts:
            fact_key = conflict.get("fact_key", "unknown")
            evidence_list = conflict.get("evidence", [])
            values = conflict.get("values", [])

            # Формируем описание конфликта
            value_descriptions = []
            for val in values:
                val_str = str(val.get("value", "N/A"))
                if val.get("is_low_confidence"):
                    val_str += " (низкая уверенность)"
                value_descriptions.append(val_str)

            description = f"Обнаружен конфликт факта '{fact_key}': найдено {len(values)} различных значений.\n\n"
            description += "Значения:\n"
            for i, val_desc in enumerate(value_descriptions, 1):
                description += f"  {i}. {val_desc}\n"

            description += "\nИсточники:\n"
            anchor_ids_all: list[str] = []
            for ev in evidence_list:
                zones = ev.get("source_zone", "unknown")
                anchor_ids = ev.get("anchor_ids", [])
                anchor_ids_all.extend(anchor_ids)
                description += f"  - Зона '{zones}': {len(anchor_ids)} anchor(s)\n"

            # Создаём задачу
            task = Task(
                study_id=study_id,
                type=TaskType.RESOLVE_CONFLICT,
                status=TaskStatus.OPEN,
                payload_json={
                    "fact_key": fact_key,
                    "conflict": conflict,
                    "anchor_ids": list(set(anchor_ids_all)),
                    "description": description,
                },
            )
            self.db.add(task)
            logger.info(f"Создана задача resolve_conflict для fact_key={fact_key}, study_id={study_id}")

        await self.db.flush()
