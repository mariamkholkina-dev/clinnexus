"""Модуль для сбора метрик ингестии документов."""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import logger


def get_git_sha() -> str:
    """Получает короткий SHA текущего коммита git."""
    try:
        # Пытаемся получить SHA из git
        repo_root = Path(__file__).parent.parent.parent.parent
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        logger.warning(f"Не удалось получить git SHA: {e}")
    return "unknown"


def hash_configs() -> str:
    """
    Вычисляет хеш конфигураций, влияющих на ингестию.
    
    Использует get_pipeline_config_hash() для детерминированного хеша всех конфигураций пайплайна.
    """
    try:
        from app.utils.config_hash import get_pipeline_config_hash
        
        return get_pipeline_config_hash()
    except Exception as e:
        logger.warning(f"Не удалось вычислить hash конфигов: {e}")
        return "unknown"


@dataclass
class AnchorMetrics:
    """Метрики по anchors."""
    
    total: int = 0
    by_content_type: dict[str, int] = field(default_factory=dict)
    by_source_zone: dict[str, int] = field(default_factory=dict)
    by_language: dict[str, int] = field(default_factory=dict)
    text_len: dict[str, float] = field(default_factory=dict)  # avg, p50, p95
    empty_or_short: int = 0  # anchors с text_norm длиной < 10
    unknown_rate: float = 0.0  # доля source_zone='unknown'
    low_confidence_rate: float = 0.0  # доля confidence < 0.5
    top_unknown_headings: list[dict[str, Any]] = field(default_factory=list)  # [{heading, count}]


@dataclass
class ChunkMetrics:
    """Метрики по chunks."""
    
    total: int = 0
    by_source_zone: dict[str, int] = field(default_factory=dict)
    by_language: dict[str, int] = field(default_factory=dict)
    token_estimate: dict[str, float] = field(default_factory=dict)  # avg, p50, p95
    embedding_missing: int = 0  # chunks без embedding (не должно быть, но на всякий случай)
    anchor_count: dict[str, float] = field(default_factory=dict)  # avg, p95


@dataclass
class SoAMetrics:
    """Метрики по Schedule of Activities."""
    
    found: bool = False
    table_score: float | None = None
    visits_count: int | None = None
    procedures_count: int | None = None
    matrix_cells_total: int | None = None
    matrix_marked_cells: int | None = None
    matrix_density: float | None = None  # marked_cells / total_cells


@dataclass
class FactsMetrics:
    """Метрики по фактам."""
    
    total: int = 0
    by_fact_key: dict[str, int] = field(default_factory=dict)  # fact_type/fact_key -> count
    by_status: dict[str, int] = field(default_factory=dict)
    missing_required: list[str] = field(default_factory=list)  # список отсутствующих обязательных фактов
    conflicting_count: int = 0  # количество фактов со статусом 'conflicting'


@dataclass
class SectionMapsMetrics:
    """Метрики по маппингу секций."""
    
    expected: int = 12  # ожидаемое количество core секций для протокола
    total: int = 0  # количество маппированных core секций (для coverage_rate)
    by_status: dict[str, int] = field(default_factory=dict)
    per_target_section: dict[str, dict[str, Any]] = field(default_factory=dict)  # target_section -> {status, confidence}
    coverage_rate: float = 0.0  # mapped_core / expected (только по core секций)
    missing_core_keys: list[str] = field(default_factory=list)  # список отсутствующих core секций


@dataclass
class SourceZonesMetrics:
    """Метрики по source_zones."""
    
    zone_set_key: str = ""  # ключ набора зон (protocol/csr)
    allowed_zones: list[str] = field(default_factory=list)  # список разрешённых зон для doc_type
    by_zone_counts: dict[str, int] = field(default_factory=dict)  # количество anchors по каждой зоне


@dataclass
class IngestionMetrics:
    """Полные метрики ингестии документа."""
    
    timings_ms: dict[str, int] = field(default_factory=dict)  # этап -> время в мс
    anchors: AnchorMetrics = field(default_factory=AnchorMetrics)
    chunks: ChunkMetrics = field(default_factory=ChunkMetrics)
    soa: SoAMetrics = field(default_factory=SoAMetrics)
    facts: FactsMetrics = field(default_factory=FactsMetrics)
    section_maps: SectionMapsMetrics = field(default_factory=SectionMapsMetrics)
    source_zones: SourceZonesMetrics = field(default_factory=SourceZonesMetrics)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    
    def finalize(self) -> None:
        """
        Вычисляет финальные метрики: процентили, проценты и т.д.
        Должен вызываться после сбора всех данных.
        """
        # Вычисляем unknown_rate для anchors
        if self.anchors.total > 0:
            unknown_count = self.anchors.by_source_zone.get("unknown", 0)
            self.anchors.unknown_rate = unknown_count / self.anchors.total
        
        # Вычисляем low_confidence_rate для anchors
        # (это будет вычислено при сборе данных из БД)
        
        # Вычисляем matrix_density для SoA
        if (
            self.soa.matrix_cells_total is not None
            and self.soa.matrix_cells_total > 0
            and self.soa.matrix_marked_cells is not None
        ):
            self.soa.matrix_density = (
                self.soa.matrix_marked_cells / self.soa.matrix_cells_total
            )
        
        # Вычисляем coverage_rate для section_maps
        if self.section_maps.expected > 0:
            self.section_maps.coverage_rate = (
                self.section_maps.total / self.section_maps.expected
            )
    
    def to_summary_json(self) -> dict[str, Any]:
        """
        Преобразует метрики в стабильный JSON для сохранения в БД.
        Схема должна быть стабильной для сравнения между запусками.
        """
        return {
            "timings_ms": self.timings_ms,
            "anchors": {
                "total": self.anchors.total,
                "by_content_type": self.anchors.by_content_type,
                "by_source_zone": self.anchors.by_source_zone,
                "by_language": self.anchors.by_language,
                "text_len": self.anchors.text_len,
                "empty_or_short": self.anchors.empty_or_short,
                "unknown_rate": round(self.anchors.unknown_rate, 4),
                "low_confidence_rate": round(self.anchors.low_confidence_rate, 4),
                "top_unknown_headings": self.anchors.top_unknown_headings[:10],  # Ограничиваем топ-10
            },
            "chunks": {
                "total": self.chunks.total,
                "by_source_zone": self.chunks.by_source_zone,
                "by_language": self.chunks.by_language,
                "token_estimate": self.chunks.token_estimate,
                "embedding_missing": self.chunks.embedding_missing,
                "anchor_count": self.chunks.anchor_count,
            },
            "soa": {
                "found": self.soa.found,
                "table_score": self.soa.table_score,
                "visits_count": self.soa.visits_count,
                "procedures_count": self.soa.procedures_count,
                "matrix_cells_total": self.soa.matrix_cells_total,
                "matrix_marked_cells": self.soa.matrix_marked_cells,
                "matrix_density": round(self.soa.matrix_density, 4) if self.soa.matrix_density is not None else None,
            },
            "facts": {
                "total": self.facts.total,
                "by_fact_key": self.facts.by_fact_key,
                "by_status": self.facts.by_status,
                "missing_required": self.facts.missing_required,
                "conflicting_count": self.facts.conflicting_count,
            },
            "section_maps": {
                "expected": self.section_maps.expected,
                "total": self.section_maps.total,
                "by_status": self.section_maps.by_status,
                "per_target_section": self.section_maps.per_target_section,
                "coverage_rate": round(self.section_maps.coverage_rate, 4),
                "missing_core_keys": self.section_maps.missing_core_keys,
            },
            "source_zones": {
                "zone_set_key": self.source_zones.zone_set_key,
                "allowed_zones": self.source_zones.allowed_zones,
                "by_zone_counts": self.source_zones.by_zone_counts,
            },
            "warnings": self.warnings,
            "errors": self.errors,
        }


def compute_percentiles(values: list[float], percentiles: list[int] = [50, 95]) -> dict[str, float]:
    """
    Вычисляет процентили для списка значений.
    
    Args:
        values: Список числовых значений
        percentiles: Список процентилей для вычисления (по умолчанию [50, 95])
    
    Returns:
        Словарь {p50: ..., p95: ...}
    """
    if not values:
        return {}
    
    sorted_values = sorted(values)
    result = {}
    
    for p in percentiles:
        if p == 50:
            # Медиана
            idx = len(sorted_values) // 2
            if len(sorted_values) % 2 == 0:
                result["p50"] = (sorted_values[idx - 1] + sorted_values[idx]) / 2
            else:
                result["p50"] = sorted_values[idx]
        else:
            # Другие процентили
            idx = int(len(sorted_values) * p / 100)
            idx = min(idx, len(sorted_values) - 1)
            result[f"p{p}"] = sorted_values[idx]
    
    # Среднее значение
    result["avg"] = statistics.mean(values)
    
    return result

