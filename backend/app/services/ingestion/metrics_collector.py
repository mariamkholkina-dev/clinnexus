"""Модуль для сбора метрик ингестии из базы данных."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import DocumentType, FactStatus, SectionMapStatus, SourceZone
from app.db.models.anchors import Anchor, Chunk
from app.db.models.facts import Fact
from app.db.models.sections import TargetSectionMap
from app.db.models.studies import Document, DocumentVersion
from app.services.zone_set_registry import get_registry
from app.services.ingestion.metrics import (
    AnchorMetrics,
    ChunkMetrics,
    FactsMetrics,
    IngestionMetrics,
    SectionMapsMetrics,
    SoAMetrics,
    compute_percentiles,
)


class MetricsCollector:
    """Сборщик метрик из базы данных."""
    
    def __init__(self, db: AsyncSession, doc_version_id: str) -> None:
        self.db = db
        self.doc_version_id = doc_version_id
        self.metrics = IngestionMetrics()
        self._timing_start: dict[str, float] = {}
    
    def start_timing(self, step: str) -> None:
        """Начинает отсчёт времени для этапа."""
        self._timing_start[step] = time.time()
    
    def end_timing(self, step: str) -> None:
        """Заканчивает отсчёт времени для этапа и сохраняет в метрики."""
        if step in self._timing_start:
            duration_ms = int((time.time() - self._timing_start[step]) * 1000)
            self.metrics.timings_ms[step] = duration_ms
            del self._timing_start[step]
    
    async def collect_anchor_metrics(self) -> None:
        """Собирает метрики по anchors."""
        # Запрос для подсчёта anchors
        stmt = select(
            func.count(Anchor.id).label("total"),
            func.count().filter(Anchor.source_zone == SourceZone.UNKNOWN).label("unknown_count"),
            func.count().filter(func.length(Anchor.text_norm) < 10).label("empty_or_short"),
            func.count().filter(Anchor.confidence < 0.5).label("low_confidence"),
        ).where(Anchor.doc_version_id == UUID(self.doc_version_id))
        
        result = await self.db.execute(stmt)
        row = result.one()
        
        self.metrics.anchors.total = row.total or 0
        self.metrics.anchors.empty_or_short = row.empty_or_short or 0
        
        if self.metrics.anchors.total > 0:
            unknown_count = row.unknown_count or 0
            self.metrics.anchors.unknown_rate = unknown_count / self.metrics.anchors.total
            low_confidence_count = row.low_confidence or 0
            self.metrics.anchors.low_confidence_rate = low_confidence_count / self.metrics.anchors.total
        
        # Группировка по content_type
        stmt = select(
            Anchor.content_type,
            func.count(Anchor.id).label("count"),
        ).where(Anchor.doc_version_id == UUID(self.doc_version_id)).group_by(Anchor.content_type)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            self.metrics.anchors.by_content_type[row.content_type.value] = row.count
        
        # Группировка по source_zone
        stmt = select(
            Anchor.source_zone,
            func.count(Anchor.id).label("count"),
        ).where(Anchor.doc_version_id == UUID(self.doc_version_id)).group_by(Anchor.source_zone)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            self.metrics.anchors.by_source_zone[row.source_zone.value] = row.count
        
        # Группировка по language
        stmt = select(
            Anchor.language,
            func.count(Anchor.id).label("count"),
        ).where(Anchor.doc_version_id == UUID(self.doc_version_id)).group_by(Anchor.language)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            self.metrics.anchors.by_language[row.language.value] = row.count
        
        # Длины текстов для процентилей
        stmt = select(func.length(Anchor.text_norm)).where(
            Anchor.doc_version_id == UUID(self.doc_version_id)
        )
        result = await self.db.execute(stmt)
        text_lengths = [row[0] for row in result.all() if row[0] is not None]
        
        if text_lengths:
            self.metrics.anchors.text_len = compute_percentiles(text_lengths)
        
        # Топ unknown headings (заголовки с source_zone=unknown)
        from app.db.enums import AnchorContentType
        stmt = select(
            Anchor.text_norm,
            func.count(Anchor.id).label("count"),
        ).where(
            Anchor.doc_version_id == UUID(self.doc_version_id),
            Anchor.content_type == AnchorContentType.HDR,
            Anchor.source_zone == SourceZone.UNKNOWN,
        ).group_by(Anchor.text_norm).order_by(func.count(Anchor.id).desc()).limit(10)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            self.metrics.anchors.top_unknown_headings.append({
                "heading": row.text_norm[:100],  # Ограничиваем длину
                "count": row.count,
            })
    
    async def collect_chunk_metrics(self) -> None:
        """Собирает метрики по chunks."""
        # Подсчёт chunks
        stmt = select(func.count(Chunk.id)).where(Chunk.doc_version_id == UUID(self.doc_version_id))
        result = await self.db.execute(stmt)
        self.metrics.chunks.total = result.scalar() or 0
        
        # Группировка по source_zone
        stmt = select(
            Chunk.source_zone,
            func.count(Chunk.id).label("count"),
        ).where(Chunk.doc_version_id == UUID(self.doc_version_id)).group_by(Chunk.source_zone)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            self.metrics.chunks.by_source_zone[row.source_zone.value] = row.count
        
        # Группировка по language
        stmt = select(
            Chunk.language,
            func.count(Chunk.id).label("count"),
        ).where(Chunk.doc_version_id == UUID(self.doc_version_id)).group_by(Chunk.language)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            self.metrics.chunks.by_language[row.language.value] = row.count
        
        # Token estimates из metadata_json
        stmt = select(Chunk.metadata_json).where(Chunk.doc_version_id == UUID(self.doc_version_id))
        result = await self.db.execute(stmt)
        token_estimates = []
        for row in result.all():
            if row.metadata_json and isinstance(row.metadata_json, dict):
                tokens = row.metadata_json.get("token_estimate")
                if isinstance(tokens, (int, float)):
                    token_estimates.append(float(tokens))
        
        if token_estimates:
            self.metrics.chunks.token_estimate = compute_percentiles(token_estimates)
        
        # Anchor counts из anchor_ids
        stmt = select(func.array_length(Chunk.anchor_ids, 1)).where(
            Chunk.doc_version_id == UUID(self.doc_version_id)
        )
        result = await self.db.execute(stmt)
        anchor_counts = [row[0] for row in result.all() if row[0] is not None]
        
        if anchor_counts:
            self.metrics.chunks.anchor_count = compute_percentiles(anchor_counts, [50, 95])
    
    async def collect_facts_metrics(self, study_id: str) -> None:
        """Собирает метрики по фактам."""
        # Подсчёт фактов
        stmt = select(func.count(Fact.id)).where(
            Fact.study_id == UUID(study_id),
            Fact.created_from_doc_version_id == UUID(self.doc_version_id),
        )
        result = await self.db.execute(stmt)
        self.metrics.facts.total = result.scalar() or 0
        
        # Группировка по fact_key (fact_type/fact_key)
        stmt = select(
            Fact.fact_type,
            Fact.fact_key,
            func.count(Fact.id).label("count"),
        ).where(
            Fact.study_id == UUID(study_id),
            Fact.created_from_doc_version_id == UUID(self.doc_version_id),
        ).group_by(Fact.fact_type, Fact.fact_key)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            key = f"{row.fact_type}/{row.fact_key}"
            self.metrics.facts.by_fact_key[key] = row.count
        
        # Группировка по status
        stmt = select(
            Fact.status,
            func.count(Fact.id).label("count"),
        ).where(
            Fact.study_id == UUID(study_id),
            Fact.created_from_doc_version_id == UUID(self.doc_version_id),
        ).group_by(Fact.status)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            self.metrics.facts.by_status[row.status.value] = row.count
        
        # Конфликтующие факты
        conflicting_count = self.metrics.facts.by_status.get(FactStatus.CONFLICTING.value, 0)
        self.metrics.facts.conflicting_count = conflicting_count
        
        # Проверка обязательных фактов (будет заполнено позже при проверке)
        # Здесь просто инициализируем список
        self.metrics.facts.missing_required = []
    
    async def collect_section_maps_metrics(
        self, 
        expected_sections: int = 12,
        core_sections: list[str] | None = None
    ) -> None:
        """
        Собирает метрики по маппингу секций.
        
        Args:
            expected_sections: Ожидаемое количество секций (по умолчанию 12)
            core_sections: Список core section_key для подсчёта coverage_rate
                          (если None, используется общий подсчёт всех section_maps)
        """
        self.metrics.section_maps.expected = expected_sections
        
        # Подсчёт всех section_maps
        stmt = select(func.count(TargetSectionMap.id)).where(
            TargetSectionMap.doc_version_id == UUID(self.doc_version_id)
        )
        result = await self.db.execute(stmt)
        total_count = result.scalar() or 0
        
        # Если указаны core_sections, считаем coverage_rate только по ним
        core_section_keys = None
        if core_sections:
            core_section_keys = set(core_sections)
            stmt = select(func.count(TargetSectionMap.id)).where(
                TargetSectionMap.doc_version_id == UUID(self.doc_version_id),
                TargetSectionMap.target_section.in_(core_section_keys),
                TargetSectionMap.status.in_([SectionMapStatus.MAPPED, SectionMapStatus.NEEDS_REVIEW]),  # Учитываем только не-overridden
            )
            result = await self.db.execute(stmt)
            mapped_core_count = result.scalar() or 0
            self.metrics.section_maps.total = mapped_core_count
        else:
            # Без core_sections используем общий подсчёт
            self.metrics.section_maps.total = total_count
        
        # Группировка по status
        stmt = select(
            TargetSectionMap.status,
            func.count(TargetSectionMap.id).label("count"),
        ).where(TargetSectionMap.doc_version_id == UUID(self.doc_version_id)).group_by(TargetSectionMap.status)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            self.metrics.section_maps.by_status[row.status.value] = row.count
        
        # Информация по каждой target_section
        stmt = select(
            TargetSectionMap.target_section,
            TargetSectionMap.status,
            TargetSectionMap.confidence,
        ).where(TargetSectionMap.doc_version_id == UUID(self.doc_version_id))
        
        result = await self.db.execute(stmt)
        mapped_core_keys = set()
        for row in result.all():
            self.metrics.section_maps.per_target_section[row.target_section] = {
                "status": row.status.value,
                "confidence": float(row.confidence) if row.confidence else None,
            }
            # Собираем ключи маппированных core секций (статус mapped или needs_review, но не overridden)
            if core_sections and core_section_keys and row.target_section in core_section_keys:
                if row.status in (SectionMapStatus.MAPPED, SectionMapStatus.NEEDS_REVIEW):
                    mapped_core_keys.add(row.target_section)
        
        # Вычисляем missing_core_keys
        if core_sections:
            self.metrics.section_maps.missing_core_keys = [
                key for key in core_sections if key not in mapped_core_keys
            ]
    
    def set_soa_metrics(
        self,
        found: bool,
        table_score: float | None = None,
        visits_count: int | None = None,
        procedures_count: int | None = None,
        matrix_cells_total: int | None = None,
        matrix_marked_cells: int | None = None,
    ) -> None:
        """Устанавливает метрики SoA."""
        self.metrics.soa.found = found
        self.metrics.soa.table_score = table_score
        self.metrics.soa.visits_count = visits_count
        self.metrics.soa.procedures_count = procedures_count
        self.metrics.soa.matrix_cells_total = matrix_cells_total
        self.metrics.soa.matrix_marked_cells = matrix_marked_cells
    
    def check_required_facts(self, required_facts: list[str]) -> None:
        """Проверяет наличие обязательных фактов."""
        missing = []
        for fact_key in required_facts:
            if fact_key not in self.metrics.facts.by_fact_key:
                missing.append(fact_key)
        self.metrics.facts.missing_required = missing
    
    async def collect_source_zones_metrics(self, doc_type: DocumentType) -> None:
        """
        Собирает метрики по source_zones для данного doc_type.
        
        Args:
            doc_type: Тип документа
        """
        # Получаем реестр наборов зон
        registry = get_registry()
        
        # Устанавливаем zone_set_key и allowed_zones
        self.metrics.source_zones.zone_set_key = doc_type.value
        self.metrics.source_zones.allowed_zones = registry.get_allowed_zones(doc_type)
        
        # Собираем статистику по зонам из anchors
        stmt = select(
            Anchor.source_zone,
            func.count(Anchor.id).label("count"),
        ).where(Anchor.doc_version_id == UUID(self.doc_version_id)).group_by(Anchor.source_zone)
        
        result = await self.db.execute(stmt)
        for row in result.all():
            zone_key = row.source_zone.value if hasattr(row.source_zone, 'value') else str(row.source_zone)
            self.metrics.source_zones.by_zone_counts[zone_key] = row.count
    
    def finalize(self) -> None:
        """Финализирует метрики (вычисляет проценты и процентили)."""
        self.metrics.finalize()

