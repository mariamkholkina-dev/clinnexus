from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.db.models.generation import GenerationRun, GeneratedSection, Template
from app.db.enums import GenerationStatus, QCStatus
from app.schemas.generation import (
    ArtifactsSchema,
    GenerateSectionRequest,
    GenerateSectionResult,
    QCReportSchema,
    QCErrorSchema,
)


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

        # TODO: Реальная логика генерации
        # Здесь должна быть логика:
        # 1. Получить релевантные chunks через RetrievalService
        # 2. Сформировать prompt из template и chunks
        # 3. Вызвать LLM
        # 4. Извлечь artifacts
        # 5. Вызвать ValidationService для QC

        # Заглушка
        content_text = f"Сгенерированный контент для секции {req.section_key} (заглушка)"
        artifacts = ArtifactsSchema(
            claims=["Claim 1", "Claim 2"],
            numbers=[{"value": 100, "unit": "patients"}],
            citations=["anchor_1", "anchor_2"],
        )

        # Вызываем валидацию
        validation_service = ValidationService(self.db)
        qc_report = await validation_service.validate(
            content_text=content_text,
            artifacts_json=artifacts.model_dump(),
            contract_id=req.contract_id,
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

        # TODO: Реальная логика валидации
        # Здесь должна быть логика:
        # 1. Получить SectionContract
        # 2. Применить qc_ruleset_json
        # 3. Проверить наличие required facts
        # 4. Проверить citation policy
        # 5. Вернуть QCReport

        # Заглушка: всегда PASSED
        qc_report = QCReportSchema(
            status=QCStatus.PASSED,
            errors=[],
        )

        logger.info(f"Валидация завершена: {qc_report.status}")
        return qc_report
