"""Модули для ингестии документов."""
from __future__ import annotations

import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.db.enums import DocumentType, EvidenceRole, FactStatus, IngestionStatus, SectionMapStatus
from app.db.models.anchors import Anchor, Chunk
from app.db.models.audit import AuditLog
from app.db.models.facts import Fact, FactEvidence
from app.db.models.ingestion_runs import IngestionRun
from app.db.models.sections import TargetSectionContract, TargetSectionMap
from app.db.models.studies import Document, DocumentVersion
from app.services.anchor_aligner import AnchorAligner
from app.services.ingestion.docx_ingestor import DocxIngestor
from app.services.ingestion.metrics import get_git_sha, hash_configs
from app.services.ingestion.metrics_collector import MetricsCollector
from app.services.ingestion.quality_gate import QualityGate
from app.services.fact_extraction import FactExtractionService
from app.services.chunking import ChunkingService
from app.services.section_mapping import SectionMappingService
from app.services.section_mapping_assist import SectionMappingAssistService
from app.services.soa_extraction import SoAExtractionService
from app.services.heading_clustering import HeadingClusteringService
from app.services.topic_mapping import TopicMappingService
from app.services.fact_consistency import FactConsistencyService


class IngestionResult:
    """Результат ингестии документа."""

    def __init__(
        self,
        doc_version_id: UUID,
        anchors_created: int = 0,
        chunks_created: int = 0,
        soa_detected: bool = False,
        soa_table_index: int | None = None,
        soa_section_path: str | None = None,
        soa_confidence: float | None = None,
        cell_anchors_created: int = 0,
        facts_count: int = 0,
        facts_needs_review: list[str] | None = None,
        warnings: list[str] | None = None,
        needs_review: bool = False,
        docx_summary: dict[str, Any] | None = None,
    ) -> None:
        self.doc_version_id = doc_version_id
        self.anchors_created = anchors_created
        self.chunks_created = chunks_created
        self.soa_detected = soa_detected
        self.soa_table_index = soa_table_index
        self.soa_section_path = soa_section_path
        self.soa_confidence = soa_confidence
        self.cell_anchors_created = cell_anchors_created
        self.facts_count = facts_count
        self.facts_needs_review = facts_needs_review or []
        self.warnings = warnings or []
        self.needs_review = needs_review
        self.docx_summary = docx_summary or {}


class IngestionService:
    """Сервис для ингестии документов (извлечение структуры и создание anchors/chunks)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _uri_to_path(self, uri: str) -> Path:
        """
        Преобразует URI (file:// или относительный путь) в локальный путь.
        
        Args:
            uri: URI файла (file:///path/to/file или относительный путь)
            
        Returns:
            Path объект с локальным путём
        """
        if uri.startswith("file://"):
            # Преобразуем file:// URI в путь
            parsed = urllib.parse.urlparse(uri)
            path = urllib.parse.unquote(parsed.path)
            # На Windows file:///C:/path становится /C:/path, убираем ведущий /
            if path.startswith("/") and len(path) > 3 and path[2] == ":":
                path = path[1:]
            return Path(path)
        else:
            # Относительный путь
            return Path(uri)

    async def ingest(self, doc_version_id: UUID, force: bool = False) -> IngestionResult:
        """
        Ингестия документа: извлечение структуры, создание anchors и chunks.

        Примечание: Этот метод НЕ меняет статус документа и НЕ делает commit.
        Управление статусом и commit выполняется вызывающим кодом (эндпоинтом).

        Args:
            doc_version_id: ID версии документа
            force: Принудительная переингестия (удаляет существующие данные)

        Returns:
            IngestionResult с результатами ингестии
        """
        ingestion_start_time = time.time()
        logger.info(f"Начало ингестии документа {doc_version_id}")

        # Получаем версию документа
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        # Проверяем наличие файла
        if not doc_version.source_file_uri:
            raise ValueError(f"DocumentVersion {doc_version_id} не имеет source_file_uri")

        # Преобразуем URI в локальный путь
        file_path = self._uri_to_path(doc_version.source_file_uri)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Файл не найден: {file_path}")

        # Определяем расширение файла
        file_ext = file_path.suffix.lower()
        
        # Получаем document для получения study_id
        document = await self.db.get(Document, doc_version.document_id)
        if not document:
            raise ValueError(f"Document {doc_version.document_id} не найден")
        
        study_id = document.study_id
        
        # Создаём IngestionRun для отслеживания
        ingestion_run = IngestionRun(
            doc_version_id=doc_version_id,
            status="partial",
            pipeline_version=get_git_sha(),
            pipeline_config_hash=hash_configs(),
        )
        self.db.add(ingestion_run)
        await self.db.flush()
        
        # Создаём сборщик метрик
        metrics_collector = MetricsCollector(self.db, str(doc_version_id))
        
        errors: list[str] = []
        warnings: list[str] = []
        anchors_created = 0
        chunks_created = 0
        soa_detected = False
        soa_table_index: int | None = None
        soa_section_path: str | None = None
        soa_confidence: float | None = None
        cell_anchors_created = 0
        facts_count = 0
        facts_needs_review: list[str] = []
        needs_review = False
        docx_summary: dict[str, Any] | None = None
        
        alignment_summary: dict[str, Any] | None = None
        conflicts_count = 0
        llm_info: dict[str, Any] | None = None

        try:
            # Re-ingest: удаляем существующие anchors и facts для этого doc_version
            metrics_collector.start_timing("cleanup")
            logger.info(f"Удаление существующих chunks для doc_version_id={doc_version_id}")
            await self.db.execute(delete(Chunk).where(Chunk.doc_version_id == doc_version_id))
            await self.db.flush()

            logger.info(f"Удаление существующих anchors для doc_version_id={doc_version_id}")
            delete_stmt = delete(Anchor).where(Anchor.doc_version_id == doc_version_id)
            await self.db.execute(delete_stmt)
            await self.db.flush()
            
            # Удаляем существующие facts, созданные из этого doc_version
            logger.info(f"Удаление существующих facts для doc_version_id={doc_version_id}")
            facts_to_delete = await self.db.execute(
                select(Fact.id).where(Fact.created_from_doc_version_id == doc_version_id)
            )
            fact_ids = [row[0] for row in facts_to_delete.all()]
            if fact_ids:
                delete_evidence_stmt = delete(FactEvidence).where(FactEvidence.fact_id.in_(fact_ids))
                await self.db.execute(delete_evidence_stmt)
                delete_facts_stmt = delete(Fact).where(Fact.id.in_(fact_ids))
                await self.db.execute(delete_facts_stmt)
                await self.db.flush()
            metrics_collector.end_timing("cleanup")

            # Обрабатываем DOCX
            if file_ext == ".docx":
                metrics_collector.start_timing("parse_anchors")
                logger.info(f"Парсинг DOCX файла: {file_path}")
                ingestor = DocxIngestor()
                result = ingestor.ingest(
                    file_path, 
                    doc_version_id, 
                    doc_version.document_language,
                    document.doc_type
                )
            
            # Bulk insert anchors
            if result.anchors:
                anchor_objects = [
                    Anchor(
                        doc_version_id=anchor.doc_version_id,
                        anchor_id=anchor.anchor_id,
                        section_path=anchor.section_path,
                        content_type=anchor.content_type,
                        ordinal=anchor.ordinal,
                        text_raw=anchor.text_raw,
                        text_norm=anchor.text_norm,
                        text_hash=anchor.text_hash,
                        location_json=anchor.location_json,
                        source_zone=anchor.source_zone,
                        language=anchor.language,
                    )
                    for anchor in result.anchors
                ]
                self.db.add_all(anchor_objects)
                await self.db.flush()
                
                anchors_created = len(result.anchors)
                logger.info(f"Создано {anchors_created} anchors")
                metrics_collector.end_timing("parse_anchors")
                
                # Собираем метрики по anchors
                await metrics_collector.collect_anchor_metrics()
                
                # Собираем метрики по source_zones
                await metrics_collector.collect_source_zones_metrics(document.doc_type)
            
                # Собираем warnings
                warnings.extend(result.warnings)
                
                # Сохраняем summary из DocxIngestor для передачи в ingestion_summary_json
                docx_summary = result.summary
                
                # Шаг 5: Извлечение SoA
                metrics_collector.start_timing("soa_extraction")
                logger.info(f"Запуск извлечения SoA для doc_version_id={doc_version_id}")
                soa_service = SoAExtractionService(self.db)
                cell_anchors, soa_result = await soa_service.extract_soa(doc_version_id)
            
                if soa_result:
                    soa_detected = True
                    soa_table_index = soa_result.table_index
                    soa_section_path = soa_result.section_path
                    soa_confidence = soa_result.confidence
                    logger.info(
                        f"SoA найден: table_index={soa_result.table_index}, "
                        f"confidence={soa_result.confidence:.2f}, "
                        f"visits={len(soa_result.visits)}, procedures={len(soa_result.procedures)}"
                    )
                    
                    # Вычисляем метрики SoA
                    matrix_cells_total = None
                    matrix_marked_cells = None
                    if soa_result.matrix:
                        matrix_cells_total = len(soa_result.matrix)
                        # Все записи в матрице уже имеют значение (добавляются только non-empty)
                        # Поэтому все они считаются "marked"
                        matrix_marked_cells = len(soa_result.matrix)
                    
                    metrics_collector.set_soa_metrics(
                        found=True,
                        table_score=soa_result.confidence,
                        visits_count=len(soa_result.visits) if soa_result.visits else None,
                        procedures_count=len(soa_result.procedures) if soa_result.procedures else None,
                        matrix_cells_total=matrix_cells_total,
                        matrix_marked_cells=matrix_marked_cells,
                    )
                    
                    # Сохраняем cell anchors
                    if cell_anchors:
                        cell_anchor_objects = [
                            Anchor(
                                doc_version_id=anchor.doc_version_id,
                                anchor_id=anchor.anchor_id,
                                section_path=anchor.section_path,
                                content_type=anchor.content_type,
                                ordinal=anchor.ordinal,
                                text_raw=anchor.text_raw,
                                text_norm=anchor.text_norm,
                                text_hash=anchor.text_hash,
                                location_json=anchor.location_json,
                                source_zone=getattr(anchor, "source_zone", "unknown"),
                                language=anchor.language,
                            )
                            for anchor in cell_anchors
                        ]
                        self.db.add_all(cell_anchor_objects)
                        await self.db.flush()
                        cell_anchors_created = len(cell_anchors)
                        anchors_created += len(cell_anchors)
                        logger.info(f"Создано {len(cell_anchors)} cell anchors")
                    
                    # Определяем статус фактов на основе confidence
                    fact_status = FactStatus.EXTRACTED if soa_result.confidence >= 0.7 else FactStatus.NEEDS_REVIEW

                    # Перед созданием SoA-фактов удаляем ранее сохранённые факты
                    # по (study_id, fact_type="soa", fact_key in ["visits", "procedures", "matrix"])
                    # чтобы избежать конфликта уникального индекса uq_facts_study_type_key.
                    await self.db.execute(
                        delete(Fact).where(
                            Fact.study_id == study_id,
                            Fact.fact_type == "soa",
                            Fact.fact_key.in_(["visits", "procedures", "matrix"]),
                        )
                    )
                    await self.db.flush()  # Применяем удаление перед созданием новых фактов

                    # Создаём факты для visits
                    if soa_result.visits:
                        visit_anchor_ids = _dedupe_keep_order([v.anchor_id for v in soa_result.visits if v.anchor_id])
                        visits_fact = Fact(
                            study_id=study_id,
                            fact_type="soa",
                            fact_key="visits",
                            value_json={"visits": [v.model_dump() for v in soa_result.visits]},
                            status=fact_status,
                            created_from_doc_version_id=doc_version_id,
                        )
                        self.db.add(visits_fact)
                        await self.db.flush()
                        
                        # Создаём evidence для visits
                        await self.db.execute(
                            delete(FactEvidence).where(FactEvidence.fact_id == visits_fact.id)
                        )
                        for anchor_id in visit_anchor_ids:
                            evidence = FactEvidence(
                                fact_id=visits_fact.id,
                                anchor_id=anchor_id,
                                evidence_role=EvidenceRole.PRIMARY,
                            )
                            self.db.add(evidence)
                    
                    # Создаём факты для procedures
                    if soa_result.procedures:
                        proc_anchor_ids = _dedupe_keep_order([p.anchor_id for p in soa_result.procedures if p.anchor_id])
                        procedures_fact = Fact(
                            study_id=study_id,
                            fact_type="soa",
                            fact_key="procedures",
                            value_json={"procedures": [p.model_dump() for p in soa_result.procedures]},
                            status=fact_status,
                            created_from_doc_version_id=doc_version_id,
                        )
                        self.db.add(procedures_fact)
                        await self.db.flush()
                        
                        # Создаём evidence для procedures
                        await self.db.execute(
                            delete(FactEvidence).where(FactEvidence.fact_id == procedures_fact.id)
                        )
                        for anchor_id in proc_anchor_ids:
                            evidence = FactEvidence(
                                fact_id=procedures_fact.id,
                                anchor_id=anchor_id,
                                evidence_role=EvidenceRole.PRIMARY,
                            )
                            self.db.add(evidence)
                    
                    # Создаём факт для matrix
                    if soa_result.matrix:
                        matrix_anchor_ids = _dedupe_keep_order([m.anchor_id for m in soa_result.matrix if m.anchor_id])
                        matrix_fact = Fact(
                            study_id=study_id,
                            fact_type="soa",
                            fact_key="matrix",
                            value_json={"matrix": [m.model_dump() for m in soa_result.matrix]},
                            status=fact_status,
                            created_from_doc_version_id=doc_version_id,
                        )
                        self.db.add(matrix_fact)
                        await self.db.flush()
                        
                        # Создаём evidence для matrix (ограничиваем размером для производительности)
                        await self.db.execute(
                            delete(FactEvidence).where(FactEvidence.fact_id == matrix_fact.id)
                        )
                        for anchor_id in matrix_anchor_ids[:100]:  # Ограничиваем первыми 100
                            evidence = FactEvidence(
                                fact_id=matrix_fact.id,
                                anchor_id=anchor_id,
                                evidence_role=EvidenceRole.PRIMARY,
                            )
                            self.db.add(evidence)
                    
                    # Добавляем warnings из SoA
                    warnings.extend(soa_result.warnings)
                    
                    # Сохраняем информацию о SoA в ingestion_summary_json (будет обновлено вызывающим кодом)
                    # Пока просто отмечаем, что SoA найден
                    
                    # Если confidence низкий, ставим needs_review
                    if soa_result.confidence < 0.7:
                        needs_review = True
                        logger.info(f"SoA найден, но confidence низкий ({soa_result.confidence:.2f}), требуется проверка")
                else:
                    # SoA не найден
                    logger.info(f"SoA не найден в документе {doc_version_id}")
                    metrics_collector.set_soa_metrics(found=False)
                    # Если это протокол, возможно стоит поставить needs_review
                    if document.doc_type.value == "protocol":
                        warnings.append("SoA таблица не найдена в протоколе (может потребоваться ручная проверка)")
                
                metrics_collector.end_timing("soa_extraction")

                # Шаг 6: Создание chunks (Narrative Index) на основе anchors (исключая cell)
                metrics_collector.start_timing("chunking")
                logger.info(f"Запуск chunking для doc_version_id={doc_version_id}")
                chunking_service = ChunkingService(self.db)
                chunks_created = await chunking_service.rebuild_chunks_for_doc_version(doc_version_id)
                metrics_collector.end_timing("chunking")
                
                # Собираем метрики по chunks
                await metrics_collector.collect_chunk_metrics()

                # Шаг 6.1: Выравнивание якорей с предыдущей версией документа (если есть)
                # Ищем предыдущую версию этого же документа по document_id и effective_date (или created_at, если effective_date NULL)
                prev_version = None
                if doc_version.effective_date is not None:
                    # Если effective_date задан, ищем по effective_date
                    prev_version_stmt = (
                        select(DocumentVersion)
                        .where(
                            DocumentVersion.document_id == doc_version.document_id,
                            DocumentVersion.id != doc_version_id,
                            DocumentVersion.effective_date.isnot(None),
                            DocumentVersion.effective_date < doc_version.effective_date,
                        )
                        .order_by(DocumentVersion.effective_date.desc())
                    )
                else:
                    # Если effective_date не задан (NULL), используем created_at для поиска
                    prev_version_stmt = (
                        select(DocumentVersion)
                        .where(
                            DocumentVersion.document_id == doc_version.document_id,
                            DocumentVersion.id != doc_version_id,
                            DocumentVersion.created_at < doc_version.created_at,
                        )
                        .order_by(DocumentVersion.created_at.desc())
                    )
                
                prev_version_result = await self.db.execute(prev_version_stmt)
                prev_version = prev_version_result.scalars().first()

                if prev_version is not None:
                    logger.debug(f"DEBUG: Finding previous version for doc {document.id}. Current version date: {doc_version.effective_date}")
                    # Убеждаемся, что все якоря текущей версии доступны в БД для Aligner
                    await self.db.flush()
                    logger.info(f"Aligning with previous version: {prev_version.id}")
                    aligner = AnchorAligner(self.db)
                    align_stats = await aligner.align(prev_version.id, doc_version_id)
                    logger.debug(f"DEBUG: Alignment stats - Matched: {align_stats.matched}, Changed: {align_stats.changed}")
                    alignment_summary = {
                        "matched_anchors": align_stats.matched,
                        "changed_anchors": align_stats.changed,
                    }
                else:
                    logger.info("No previous version found for alignment")

                # Шаг 5.5: Rules-first извлечение фактов (после сохранения anchors и завершения SoA)
                metrics_collector.start_timing("fact_extraction")
                logger.info(f"Запуск rules-first извлечения фактов для doc_version_id={doc_version_id}")
                fact_service = FactExtractionService(self.db)
                fact_res = await fact_service.extract_and_upsert(doc_version_id, commit=False)
                facts_count = fact_res.facts_count
                facts_needs_review = [
                    f"{f.fact_type}/{f.fact_key}" for f in fact_res.facts if f.status == FactStatus.NEEDS_REVIEW
                ]
                if facts_needs_review:
                    needs_review = True
                metrics_collector.end_timing("fact_extraction")
                
                # Собираем метрики по фактам
                # Используем факты из результата извлечения, так как они могут быть еще не закоммичены
                # Но также делаем запрос к базе для полноты картины
                await metrics_collector.collect_facts_metrics(str(study_id))
                # Если факты были извлечены, но не попали в метрики (из-за flush), обновляем метрики
                if facts_count > 0 and metrics_collector.metrics.facts.total == 0:
                    logger.warning(
                        f"Факты извлечены ({facts_count}), но не найдены в БД при сборе метрик. "
                        f"Используем данные из результата извлечения."
                    )
                    metrics_collector.metrics.facts.total = facts_count
                # Проверяем обязательные факты
                # QualityGate уже импортирован на верхнем уровне
                metrics_collector.check_required_facts(QualityGate.REQUIRED_FACTS)
                
                # Шаг 5.6: Проверка согласованности фактов
                metrics_collector.start_timing("fact_consistency_check")
                logger.info(f"Запуск проверки согласованности фактов для study_id={study_id}")
                consistency_service = FactConsistencyService(self.db)
                conflicts = await consistency_service.check_study_consistency(study_id)
                conflicts_count = len(conflicts)
                if conflicts_count > 0:
                    needs_review = True
                    warnings.append("Обнаружены логические несоответствия в данных исследования (факты)")
                    logger.warning(f"Найдено {conflicts_count} конфликтов в фактах исследования {study_id}")
                else:
                    logger.info(f"Конфликтов в фактах не обнаружено для study_id={study_id}")
                metrics_collector.end_timing("fact_consistency_check")
                
                # Шаг 6: Автоматический маппинг секций
                metrics_collector.start_timing("section_mapping")
                logger.info(f"Запуск маппинга секций для doc_version_id={doc_version_id}")
                section_mapping_service = SectionMappingService(self.db)
                mapping_summary = await section_mapping_service.map_sections(doc_version_id, force=False)
            
                # Добавляем предупреждения из маппинга
                if mapping_summary.mapping_warnings:
                    warnings.extend(mapping_summary.mapping_warnings)
                
                # Если есть секции, требующие проверки, ставим needs_review
                if mapping_summary.sections_needs_review_count > 0:
                    needs_review = True
                
                logger.info(
                    f"Маппинг секций завершён: mapped={mapping_summary.sections_mapped_count}, "
                    f"needs_review={mapping_summary.sections_needs_review_count}"
                )
                metrics_collector.end_timing("section_mapping")
                
                # Шаг 6.1: Автоматический LLM-assist для проблемных секций
                if settings.secure_mode and settings.llm_provider and settings.llm_base_url and settings.llm_api_key:
                    metrics_collector.start_timing("llm_assist_mapping")
                    logger.info(f"Проверка проблемных секций для LLM-assist (doc_version_id={doc_version_id})")
                    
                    try:
                        # Находим все TargetSectionMap для текущего doc_version_id
                        problem_section_maps_stmt = select(TargetSectionMap).where(
                            TargetSectionMap.doc_version_id == doc_version_id
                        )
                        problem_section_maps_result = await self.db.execute(problem_section_maps_stmt)
                        all_section_maps = problem_section_maps_result.scalars().all()
                        
                        # Фильтруем проблемные секции: статус needs_review или 0 anchors
                        problem_section_keys: list[str] = []
                        for section_map in all_section_maps:
                            is_problem = (
                                section_map.status == SectionMapStatus.NEEDS_REVIEW
                                or not section_map.anchor_ids
                            )
                            if is_problem:
                                problem_section_keys.append(section_map.target_section)
                        
                        # Получаем TargetSectionContracts для проблемных секций
                        if problem_section_keys:
                            contracts_stmt = select(TargetSectionContract).where(
                                TargetSectionContract.doc_type == document.doc_type,
                                TargetSectionContract.target_section.in_(problem_section_keys),
                                TargetSectionContract.is_active == True,
                            )
                            contracts_result = await self.db.execute(contracts_stmt)
                            problem_contracts = contracts_result.scalars().all()
                            
                            # Получаем section_keys из контрактов (на случай, если некоторые не найдены)
                            valid_section_keys = [c.target_section for c in problem_contracts]
                            
                            if valid_section_keys:
                                logger.info(
                                    f"Найдено {len(valid_section_keys)} проблемных секций для LLM-assist: {valid_section_keys}"
                                )
                                
                                # Вызываем LLM-assist для проблемных секций
                                assist_service = SectionMappingAssistService(self.db)
                                assist_result = await assist_service.assist(
                                    doc_version_id=doc_version_id,
                                    section_keys=valid_section_keys,
                                    max_candidates_per_section=3,
                                    allow_visual_headings=False,
                                    apply=True,  # Автоматически применяем результаты
                                )
                                
                                logger.info(
                                    f"LLM-assist завершён: обработано {len(valid_section_keys)} секций, "
                                    f"llm_used={assist_result.llm_used}"
                                )
                                
                                # Сохраняем информацию о LLM для логирования
                                if assist_result.llm_used:
                                    llm_info = {
                                        "model": settings.llm_model,
                                        "provider": settings.llm_provider.value if settings.llm_provider else None,
                                    }
                                    # Получаем системный промт из assist service
                                    try:
                                        system_prompt = assist_service._build_system_prompt(
                                            max_candidates_per_section=3,
                                            document_language=doc_version.document_language
                                        )
                                        llm_info["system_prompt"] = system_prompt
                                    except Exception:
                                        # Если не удалось получить промт, пропускаем
                                        pass
                                
                                # Обновляем needs_review на основе результатов QC
                                qc_needs_review_count = sum(
                                    1 for qc in assist_result.qc.values() 
                                    if qc.status == "needs_review"
                                )
                                if qc_needs_review_count > 0:
                                    needs_review = True
                                    logger.info(
                                        f"LLM-assist: {qc_needs_review_count} секций всё ещё требуют проверки"
                                    )
                            else:
                                logger.debug(
                                    f"Проблемные секции найдены, но нет активных контрактов для doc_type={document.doc_type}"
                                )
                        else:
                            logger.debug("Проблемных секций не найдено, LLM-assist не требуется")
                    
                    except Exception as e:
                        # Не прерываем ингестию при ошибках LLM-assist
                        error_msg = f"Ошибка при LLM-assist для проблемных секций: {str(e)}"
                        warnings.append(error_msg)
                        logger.warning(error_msg, exc_info=True)
                    
                    metrics_collector.end_timing("llm_assist_mapping")
                else:
                    logger.debug(
                        "LLM-assist пропущен: SECURE_MODE=false или API ключи не настроены"
                    )
                
                # Собираем метрики по section_maps (только по 12 core sections для protocol)
                from app.services.section_mapping import PROTOCOL_CORE_SECTIONS
                core_sections = (
                    PROTOCOL_CORE_SECTIONS if document.doc_type == DocumentType.PROTOCOL
                    else None
                )
                await metrics_collector.collect_section_maps_metrics(
                    expected_sections=12, core_sections=core_sections
                )
                
                # Сохраняем результаты маппинга в docx_summary для передачи в ingestion_summary_json
                if docx_summary is None:
                    docx_summary = {}
                docx_summary["sections_mapped_count"] = mapping_summary.sections_mapped_count
                docx_summary["sections_needs_review_count"] = mapping_summary.sections_needs_review_count
                docx_summary["mapping_warnings"] = mapping_summary.mapping_warnings
                
                # Шаг 7: Topic mapping (только для протоколов)
                if document.doc_type.value == "protocol":
                    metrics_collector.start_timing("topic_mapping")
                    logger.info(f"Запуск topic mapping для doc_version_id={doc_version_id}")
                    
                    try:
                        # Новый подход: маппинг блоков напрямую на топики
                        # Кластеризация опциональна и используется только как prior
                        topic_mapping_service = TopicMappingService(self.db)
                        assignments, metrics = await topic_mapping_service.map_topics_for_doc_version(
                            doc_version_id=doc_version_id,
                            mode="auto",
                            apply=True,
                            confidence_threshold=0.55,
                        )
                        logger.info(
                            f"Topic mapping завершён: assignments={len(assignments)}, "
                            f"mapped_rate={metrics.mapped_rate:.2%}, "
                            f"blocks_total={metrics.blocks_total}, "
                            f"clustering_enabled={metrics.clustering_enabled}"
                        )
                        
                        # Строим topic_evidence из block assignments
                        from app.services.topic_evidence_builder import TopicEvidenceBuilder
                        evidence_builder = TopicEvidenceBuilder(self.db)
                        evidence_count = await evidence_builder.build_evidence_for_doc_version(doc_version_id)
                        logger.info(f"Создано {evidence_count} записей topic_evidence")
                        
                        # Сохраняем метрики topic mapping в ingestion summary
                        if docx_summary is None:
                            docx_summary = {}
                        docx_summary["topics"] = {
                            "blocks_total": metrics.blocks_total,
                            "blocks_mapped": metrics.blocks_mapped,
                            "mapped_rate": metrics.mapped_rate,
                            "low_confidence_rate": metrics.low_confidence_rate,
                            "unmapped_top_headings": metrics.unmapped_top_headings[:5],
                            "topic_coverage_topN": metrics.topic_coverage_topN[:5],
                            "evidence_by_zone": metrics.evidence_by_zone,
                        }
                        if metrics.clustering_enabled:
                            docx_summary["clustering"] = {
                                "clusters_total": metrics.clusters_total,
                                "clusters_labeled": metrics.clusters_labeled,
                                "avg_cluster_size": metrics.avg_cluster_size,
                            }
                    except Exception as e:
                        # Не прерываем ингестию при ошибках topic mapping
                        error_msg = f"Ошибка при topic mapping: {str(e)}"
                        warnings.append(error_msg)
                        logger.warning(error_msg, exc_info=True)
                    
                    metrics_collector.end_timing("topic_mapping")
                    
                    # Собираем метрики по топикам
                    await metrics_collector.collect_topics_metrics(document.doc_type)
                else:
                    logger.debug(f"Topic mapping пропущен (doc_type={document.doc_type.value}, требуется protocol)")
                    # Для непротокольных документов всё равно собираем метрики (они будут нулевыми)
                    await metrics_collector.collect_topics_metrics(document.doc_type)
                
            else:
                # Неподдерживаемый формат (PDF и др.)
                warning_msg = f"Формат файла {file_ext} не поддерживается в шаге 4 (DOCX ingestion not implemented for this format)"
                warnings.append(warning_msg)
                needs_review = True
                errors.append(warning_msg)
                logger.warning(warning_msg)
            
            # Финализируем метрики
            metrics_collector.finalize()
            
            # Применяем QualityGate
            quality_json, quality_warnings = QualityGate.evaluate(
                metrics_collector.metrics,
                document.doc_type,
            )
            warnings.extend(quality_warnings)
            if quality_json.get("needs_review"):
                needs_review = True
            
            # Обновляем IngestionRun
            ingestion_duration_ms = int((time.time() - ingestion_start_time) * 1000)
            ingestion_run.status = "ok"
            ingestion_run.finished_at = datetime.now()
            ingestion_run.duration_ms = ingestion_duration_ms
            summary_json = metrics_collector.metrics.to_summary_json()
            # Принудительно записываем данные SoA и Alignment в корень для удобства извлечения
            summary_json["soa_confidence"] = soa_confidence
            summary_json["matched_anchors"] = alignment_summary.get("matched_anchors", 0) if alignment_summary else 0
            summary_json["changed_anchors"] = alignment_summary.get("changed_anchors", 0) if alignment_summary else 0
            # Добавляем информацию о LLM, если он был использован
            if llm_info:
                summary_json["llm_info"] = llm_info
            # Добавляем anchors_created и chunks_created в корень для консистентности (используем локальные переменные)
            summary_json["anchors_created"] = anchors_created
            summary_json["chunks_created"] = chunks_created
            # Добавляем количество найденных конфликтов
            summary_json["conflicts_found"] = conflicts_count
            # Добавляем метрики topic mapping и fact extraction в корень для удобства извлечения
            summary_json["topics_mapped_count"] = metrics_collector.metrics.topics.mapped_count
            summary_json["topics_mapped_rate"] = round(metrics_collector.metrics.topics.mapped_rate, 4)
            summary_json["facts_extracted_total"] = metrics_collector.metrics.facts.total
            summary_json["facts_validated_count"] = metrics_collector.metrics.facts.validated_count
            summary_json["facts_conflicting_count"] = metrics_collector.metrics.facts.conflicting_count
            ingestion_run.summary_json = summary_json
            ingestion_run.quality_json = quality_json
            ingestion_run.warnings_json = warnings
            ingestion_run.errors_json = errors
            
            # Обновляем doc_version
            doc_version.last_ingestion_run_id = ingestion_run.id
            doc_version.ingestion_summary_json = ingestion_run.summary_json  # Зеркалируем для обратной совместимости
            
            logger.info(
                f"Ингестия завершена для {doc_version_id}: "
                f"{anchors_created} anchors, {chunks_created} chunks, "
                f"soa_detected={soa_detected}, needs_review={needs_review}"
            )
            # Выводим финальную строку BENCHMARK_SIGNAL
            topics_mapped = metrics_collector.metrics.topics.mapped_count
            topics_total = metrics_collector.metrics.topics.total_topics
            facts_extracted = metrics_collector.metrics.facts.total
            facts_conflicts = metrics_collector.metrics.facts.conflicting_count
            logger.info(
                f"BENCHMARK_SIGNAL: Topics: {topics_mapped}/{topics_total}, "
                f"Facts: {facts_extracted} (Conflicts: {facts_conflicts})"
            )

            return IngestionResult(
                doc_version_id=doc_version_id,
                anchors_created=anchors_created,
                chunks_created=chunks_created,
                soa_detected=soa_detected,
                soa_table_index=soa_table_index,
                soa_section_path=soa_section_path,
                soa_confidence=soa_confidence,
                cell_anchors_created=cell_anchors_created,
                facts_count=facts_count,
                facts_needs_review=facts_needs_review,
                warnings=warnings if warnings else None,
                needs_review=needs_review,
                docx_summary=docx_summary if file_ext == ".docx" else None,
            )
            
        except Exception as e:
            # Обработка ошибок
            error_msg = str(e)
            errors.append(error_msg)
            logger.error(f"Ошибка при ингестии {doc_version_id}: {error_msg}", exc_info=True)
            
            # Обновляем IngestionRun с ошибкой
            ingestion_duration_ms = int((time.time() - ingestion_start_time) * 1000)
            ingestion_run.status = "failed"
            ingestion_run.finished_at = datetime.now()
            ingestion_run.duration_ms = ingestion_duration_ms
            ingestion_run.errors_json = errors
            ingestion_run.warnings_json = warnings
            if metrics_collector.metrics:
                ingestion_run.summary_json = metrics_collector.metrics.to_summary_json()
            
            raise


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


__all__ = ["IngestionService", "IngestionResult"]
