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

from app.core.logging import logger
from app.db.enums import DocumentType, EvidenceRole, FactStatus, IngestionStatus
from app.db.models.anchors import Anchor, Chunk
from app.db.models.audit import AuditLog
from app.db.models.facts import Fact, FactEvidence
from app.db.models.ingestion_runs import IngestionRun
from app.db.models.studies import Document, DocumentVersion
from app.services.ingestion.docx_ingestor import DocxIngestor
from app.services.ingestion.metrics import get_git_sha, hash_configs
from app.services.ingestion.metrics_collector import MetricsCollector
from app.services.ingestion.quality_gate import QualityGate
from app.services.fact_extraction import FactExtractionService
from app.services.chunking import ChunkingService
from app.services.section_mapping import SectionMappingService
from app.services.soa_extraction import SoAExtractionService
from app.services.heading_clustering import HeadingClusteringService
from app.services.topic_mapping import TopicMappingService


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
                result = ingestor.ingest(file_path, doc_version_id, doc_version.document_language)
            
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
                await metrics_collector.collect_facts_metrics(str(study_id))
                # Проверяем обязательные факты
                # QualityGate уже импортирован на верхнем уровне
                metrics_collector.check_required_facts(QualityGate.REQUIRED_FACTS)
                
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
                        # 7.1: Кластеризация заголовков
                        clustering_service = HeadingClusteringService(self.db)
                        clusters = await clustering_service.cluster_headings_for_doc_version(
                            doc_version_id=doc_version_id,
                            threshold=0.22,
                            min_size=3,
                            embedding_threshold=0.15,
                        )
                        logger.info(f"Создано/обновлено {len(clusters)} кластеров заголовков")
                        
                        # 7.2: Маппинг кластеров на топики (если есть кластеры)
                        if clusters:
                            topic_mapping_service = TopicMappingService(self.db)
                            assignments, metrics = await topic_mapping_service.map_topics_for_doc_version(
                                doc_version_id=doc_version_id,
                                mode="auto",
                                apply=True,
                                confidence_threshold=0.65,
                                ambiguity_delta=0.08,
                            )
                            logger.info(
                                f"Topic mapping завершён: assignments={len(assignments)}, "
                                f"coverage={metrics.coverage:.2%}, ambiguity={metrics.ambiguity:.2%}"
                            )
                        else:
                            logger.info("Пропущен topic mapping: нет кластеров заголовков")
                    except Exception as e:
                        # Не прерываем ингестию при ошибках topic mapping
                        error_msg = f"Ошибка при topic mapping: {str(e)}"
                        warnings.append(error_msg)
                        logger.warning(error_msg, exc_info=True)
                    
                    metrics_collector.end_timing("topic_mapping")
                else:
                    logger.debug(f"Topic mapping пропущен (doc_type={document.doc_type.value}, требуется protocol)")
                
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
            ingestion_run.summary_json = metrics_collector.metrics.to_summary_json()
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
