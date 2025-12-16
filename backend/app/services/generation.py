from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.logging import logger
from app.core.config import settings
from app.db.models.generation import GenerationRun, GeneratedSection, Template
from app.db.models.sections import SectionContract
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact
from app.db.enums import GenerationStatus, QCStatus
from app.schemas.generation import (
    ArtifactsSchema,
    ClaimArtifact,
    GenerateSectionRequest,
    GenerateSectionResult,
    QCReportSchema,
    QCErrorSchema,
)
from app.services.lean_passport import LeanContextBuilder, normalize_passport


class GenerationService:
    """Сервис для генерации секций документов."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate_section(
        self, req: GenerateSectionRequest
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
        contract = await self.db.get(SectionContract, req.contract_id)
        if not contract:
            raise ValueError(f"SectionContract {req.contract_id} не найден")

        passport = normalize_passport(
            required_facts_json=contract.required_facts_json,
            allowed_sources_json=contract.allowed_sources_json,
            retrieval_recipe_json=contract.retrieval_recipe_json,
            qc_ruleset_json=contract.qc_ruleset_json,
        )

        # MVP: если контракт требует secure_mode, а он выключен — BLOCKED
        if passport.retrieval_recipe.security.secure_mode_required and not settings.secure_mode:
            qc_report = QCReportSchema(
                status=QCStatus.BLOCKED,
                errors=[
                    QCErrorSchema(
                        type="secure_mode_required",
                        message="SECURE_MODE=false, а контракт требует secure_mode_required=true",
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
            generated_section = GeneratedSection(
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

        # MVP one-shot structured output:
        # В полноценном режиме здесь должен быть LLM, но для MVP-каркаса
        # делаем детерминированный черновик, чтобы QC мог отработать.
        content_text = template.template_body if getattr(template, "template_body", None) else ""
        if not content_text:
            content_text = f"Черновик секции {req.section_key}.\n\n{context_text[:2000]}"

        artifacts = ArtifactsSchema(
            claim_items=[
                ClaimArtifact(
                    text=f"Секция {req.section_key} сформирована на основе предоставленного контекста.",
                    anchor_ids=used_anchor_ids[:3],
                    fact_refs=[],
                    numbers=[],
                )
            ],
            citations=used_anchor_ids[:10],
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

        # Обновляем статус
        generation_run.status = GenerationStatus.COMPLETED

        # Создаём GeneratedSection
        generated_section = GeneratedSection(
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
        - Получить qc_ruleset_json из SectionContract
        - Проверить соответствие required_facts_json
        - Проверить citation_policy
        - Вернуть QCReport с ошибками
        """
        logger.info(f"Валидация контента по contract {contract_id}")

        contract = await self.db.get(SectionContract, contract_id)
        if not contract:
            return QCReportSchema(
                status=QCStatus.FAILED,
                errors=[
                    QCErrorSchema(
                        type="contract_not_found",
                        message=f"SectionContract {contract_id} не найден",
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

        qc_report = QCReportSchema(status=status, errors=errors)
        logger.info(f"Валидация завершена: {qc_report.status}")
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
