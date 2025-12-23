"""Модуль для проверки качества ингестии документов."""

from __future__ import annotations

from typing import Any

from app.db.enums import DocumentType
from app.services.ingestion.metrics import IngestionMetrics


class QualityGate:
    """Проверка качества ингестии на основе метрик."""
    
    # 12 core sections для протокола (используется для проверки критических секций)
    # Примечание: список используется только для обратной совместимости при проверке
    # критических секций. Реальный список core sections определён в SectionMappingService
    CRITICAL_SECTIONS = [
        "protocol.study_design",  # design
        "protocol.procedures",    # procedures
        "protocol.statistics",    # statistics
        "protocol.safety",        # safety
        "protocol.endpoints",     # endpoints
        "protocol.population",    # population
        "protocol.ip",            # ip
    ]
    
    # Обязательные факты для протокола
    REQUIRED_FACTS = [
        "protocol_meta.protocol_version",
        "population.planned_n_total",
    ]
    
    @classmethod
    def evaluate(
        cls,
        metrics: IngestionMetrics,
        doc_type: DocumentType | str,
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Оценивает качество ингестии на основе метрик.
        
        Args:
            metrics: Метрики ингестии
            doc_type: Тип документа
        
        Returns:
            Кортеж (quality_json, warnings), где:
            - quality_json: словарь с флагами качества
            - warnings: список предупреждений для добавления в metrics.warnings
        """
        quality: dict[str, Any] = {
            "needs_review": False,
            "flags": {
                "high_unknown_source_zone": False,
                "soa_missing": False,
                "soa_suspicious": False,
                "low_mapping_coverage": False,
                "facts_missing_required": False,
                "facts_conflicting": False,
                "parse_suspicious": False,
            },
            "scores": {
                "unknown_rate": metrics.anchors.unknown_rate,
                "mapping_coverage": metrics.section_maps.coverage_rate,
                "soa_density": metrics.soa.matrix_density,
            },
        }
        
        warnings: list[str] = []
        
        # 1. Проверка unknown source_zone
        if metrics.anchors.unknown_rate > 0.25:
            quality["flags"]["high_unknown_source_zone"] = True
            quality["needs_review"] = True
            warnings.append(
                f"Высокий уровень unknown source_zone: {metrics.anchors.unknown_rate:.1%}"
            )
        elif metrics.anchors.unknown_rate > 0.10:
            warnings.append(
                f"Повышенный уровень unknown source_zone: {metrics.anchors.unknown_rate:.1%}"
            )
        
        # 2. Проверка SoA
        doc_type_str = doc_type.value if hasattr(doc_type, "value") else str(doc_type)
        if doc_type_str == "protocol":
            if not metrics.soa.found:
                quality["flags"]["soa_missing"] = True
                quality["needs_review"] = True
                warnings.append("SoA таблица не найдена в протоколе")
            elif metrics.soa.found:
                # Проверяем качество SoA
                soa_suspicious = False
                if metrics.soa.matrix_density is not None and metrics.soa.matrix_density < 0.02:
                    soa_suspicious = True
                    warnings.append(
                        f"Низкая плотность SoA матрицы: {metrics.soa.matrix_density:.1%}"
                    )
                if metrics.soa.visits_count is not None and metrics.soa.visits_count < 4:
                    soa_suspicious = True
                    warnings.append(
                        f"Мало визитов в SoA: {metrics.soa.visits_count}"
                    )
                if metrics.soa.procedures_count is not None and metrics.soa.procedures_count < 5:
                    soa_suspicious = True
                    warnings.append(
                        f"Мало процедур в SoA: {metrics.soa.procedures_count}"
                    )
                
                if soa_suspicious:
                    quality["flags"]["soa_suspicious"] = True
                    quality["needs_review"] = True
        
        # 3. Проверка маппинга секций (coverage_rate по 12 core sections)
        if metrics.section_maps.coverage_rate < 0.75:
            quality["flags"]["low_mapping_coverage"] = True
            quality["needs_review"] = True
            warnings.append(
                f"Низкое покрытие маппинга секций: {metrics.section_maps.coverage_rate:.1%} "
                f"({metrics.section_maps.total} из {metrics.section_maps.expected} core секций)"
            )
        
        # 4. Проверка обязательных фактов
        missing_required = len(metrics.facts.missing_required)
        if missing_required > 0:
            quality["flags"]["facts_missing_required"] = True
            quality["needs_review"] = True
            warnings.append(
                f"Отсутствуют {missing_required} обязательных фактов: {', '.join(metrics.facts.missing_required)}"
            )
        
        # 5. Проверка конфликтующих фактов
        if metrics.facts.conflicting_count >= 1:
            quality["flags"]["facts_conflicting"] = True
            quality["needs_review"] = True
            warnings.append(
                f"Обнаружено {metrics.facts.conflicting_count} конфликтующих фактов"
            )
        
        # 6. Проверка подозрительного парсинга
        if metrics.anchors.total < 100:
            quality["flags"]["parse_suspicious"] = True
            quality["needs_review"] = True
            warnings.append(
                f"Мало anchors ({metrics.anchors.total}), возможно проблема с парсингом"
            )
        
        if metrics.anchors.by_content_type.get("hdr", 0) < 20:
            quality["flags"]["parse_suspicious"] = True
            warnings.append(
                f"Мало заголовков ({metrics.anchors.by_content_type.get('hdr', 0)}), возможно проблема с детекцией заголовков"
            )
        
        return quality, warnings

