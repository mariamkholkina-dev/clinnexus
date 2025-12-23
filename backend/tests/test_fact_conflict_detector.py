"""Unit тесты для FactConflictDetector."""

from __future__ import annotations

import pytest
from uuid import uuid4

from app.services.fact_conflict_detector import FactConflictDetector, UNCERTAINTY_MARKERS_RU, UNCERTAINTY_MARKERS_EN


class TestNumericValueParsing:
    """Тесты парсинга числовых значений из текста."""

    def test_parse_simple_number(self):
        """Парсинг простого числа."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._parse_numeric_value("100") == 100
        assert detector._parse_numeric_value("42") == 42

    def test_parse_n_equals_format(self):
        """Парсинг формата N=100."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._parse_numeric_value("N=100") == 100
        assert detector._parse_numeric_value("N = 120") == 120
        assert detector._parse_numeric_value("n=50") == 50

    def test_parse_with_uncertainty_markers_ru(self):
        """Парсинг с маркерами неопределённости (RU)."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._parse_numeric_value("примерно 100") == 100
        assert detector._parse_numeric_value("около 120") == 120
        assert detector._parse_numeric_value("приблизительно 50") == 50
        assert detector._parse_numeric_value("~100") == 100
        assert detector._parse_numeric_value("порядка 200") == 200

    def test_parse_with_uncertainty_markers_en(self):
        """Парсинг с маркерами неопределённости (EN)."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._parse_numeric_value("approximately 100") == 100
        assert detector._parse_numeric_value("about 120") == 120
        assert detector._parse_numeric_value("around 50") == 50
        assert detector._parse_numeric_value("~100") == 100
        assert detector._parse_numeric_value("nearly 200") == 200

    def test_parse_with_units(self):
        """Парсинг с единицами измерения."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._parse_numeric_value("100 участников") == 100
        assert detector._parse_numeric_value("120 participants") == 120
        assert detector._parse_numeric_value("50 subjects") == 50

    def test_parse_float_values(self):
        """Парсинг дробных значений."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._parse_numeric_value("100.5") == 100.5
        assert detector._parse_numeric_value("42.0") == 42.0

    def test_parse_invalid_values(self):
        """Парсинг невалидных значений."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._parse_numeric_value("не число") is None
        assert detector._parse_numeric_value("") is None
        assert detector._parse_numeric_value("abc") is None

    def test_parse_ratio_format(self):
        """Парсинг формата соотношения."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._parse_ratio_value("1:1") == "1:1"
        assert detector._parse_ratio_value("2:1") == "2:1"
        assert detector._parse_ratio_value("allocation ratio 1:1") == "1:1"
        assert detector._parse_ratio_value("соотношение 2:1") == "2:1"


class TestLowConfidenceDetection:
    """Тесты определения низкой уверенности."""

    def test_detect_uncertainty_markers_ru(self):
        """Определение маркеров неопределённости (RU)."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._is_low_confidence_value("примерно 100", "sample_size") is True
        assert detector._is_low_confidence_value("около 120", "sample_size") is True
        assert detector._is_low_confidence_value("приблизительно 50", "sample_size") is True
        assert detector._is_low_confidence_value("~100", "sample_size") is True
        assert detector._is_low_confidence_value("порядка 200", "sample_size") is True

    def test_detect_uncertainty_markers_en(self):
        """Определение маркеров неопределённости (EN)."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._is_low_confidence_value("approximately 100", "sample_size") is True
        assert detector._is_low_confidence_value("about 120", "sample_size") is True
        assert detector._is_low_confidence_value("around 50", "sample_size") is True
        assert detector._is_low_confidence_value("~100", "sample_size") is True
        assert detector._is_low_confidence_value("nearly 200", "sample_size") is True

    def test_detect_high_confidence(self):
        """Определение высокой уверенности."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._is_low_confidence_value("100", "sample_size") is False
        assert detector._is_low_confidence_value("N=120", "sample_size") is False
        assert detector._is_low_confidence_value("50 участников", "sample_size") is False


class TestValueNormalization:
    """Тесты нормализации значений для группировки."""

    def test_normalize_numeric_values(self):
        """Нормализация числовых значений."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._normalize_value_key(100) == "100"
        assert detector._normalize_value_key(120.5) == "120.5"
        assert detector._normalize_value_key("100") == "100"

    def test_normalize_string_values(self):
        """Нормализация строковых значений."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._normalize_value_key("Primary Endpoint") == "primary endpoint"
        assert detector._normalize_value_key("  Multiple   Spaces  ") == "multiple spaces"

    def test_normalize_list_values(self):
        """Нормализация списков значений."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._normalize_value_key(["a", "b", "c"]) == "a|b|c"
        assert detector._normalize_value_key(["c", "a", "b"]) == "a|b|c"  # Сортировка

    def test_normalize_dict_values(self):
        """Нормализация словарных значений."""
        detector = FactConflictDetector(None)  # type: ignore
        assert detector._normalize_value_key({"value": 100}) == "100"
        assert detector._normalize_value_key({"value": "test"}) == "test"


class TestConflictAggregation:
    """Тесты агрегации конфликтов."""

    def test_group_evidence_by_value(self):
        """Группировка доказательств по значениям."""
        from app.schemas.fact_conflicts import FactEvidence as FactEvidenceSchema

        detector = FactConflictDetector(None)  # type: ignore

        evidence_list = [
            FactEvidenceSchema(
                value=100,
                source_zone="study_design",
                anchor_ids=["anchor1"],
                confidence=0.9,
            ),
            FactEvidenceSchema(
                value=100,
                source_zone="statistics",
                anchor_ids=["anchor2"],
                confidence=0.8,
            ),
            FactEvidenceSchema(
                value=120,
                source_zone="study_design",
                anchor_ids=["anchor3"],
                confidence=0.9,
            ),
        ]

        groups = detector._group_evidence_by_value(evidence_list)
        assert len(groups) == 2  # Две группы: 100 и 120
        assert len(groups["100"]) == 2  # Два доказательства со значением 100
        assert len(groups["120"]) == 1  # Одно доказательство со значением 120

    def test_analyze_conflict_block_severity(self):
        """Анализ конфликта с severity=block (два высокоуверенных значения)."""
        from app.schemas.fact_conflicts import FactEvidence as FactEvidenceSchema

        detector = FactConflictDetector(None)  # type: ignore

        value_groups = {
            "100": [
                FactEvidenceSchema(
                    value=100,
                    source_zone="study_design",
                    anchor_ids=["anchor1"],
                    confidence=0.9,
                )
            ],
            "120": [
                FactEvidenceSchema(
                    value=120,
                    source_zone="statistics",
                    anchor_ids=["anchor2"],
                    confidence=0.85,
                )
            ],
        }

        conflict = detector._analyze_conflict(
            fact_key="sample_size",
            value_groups=value_groups,
            prefer_source_zones=[],
        )

        assert conflict is not None
        assert conflict.severity == "block"
        assert len(conflict.values) == 2
        assert conflict.can_auto_resolve is False

    def test_analyze_conflict_warn_severity(self):
        """Анализ конфликта с severity=warn (одно низкоуверенное значение)."""
        from app.schemas.fact_conflicts import FactEvidence as FactEvidenceSchema

        detector = FactConflictDetector(None)  # type: ignore

        value_groups = {
            "100": [
                FactEvidenceSchema(
                    value=100,
                    source_zone="study_design",
                    anchor_ids=["anchor1"],
                    confidence=0.9,
                )
            ],
            "примерно 120": [
                FactEvidenceSchema(
                    value="примерно 120",
                    source_zone="statistics",
                    anchor_ids=["anchor2"],
                    confidence=0.5,
                )
            ],
        }

        conflict = detector._analyze_conflict(
            fact_key="sample_size",
            value_groups=value_groups,
            prefer_source_zones=[],
        )

        assert conflict is not None
        assert conflict.severity == "warn"
        assert len(conflict.values) == 2

    def test_analyze_conflict_auto_resolve(self):
        """Анализ конфликта с возможностью авторазрешения."""
        from app.schemas.fact_conflicts import FactEvidence as FactEvidenceSchema

        detector = FactConflictDetector(None)  # type: ignore

        value_groups = {
            "100": [
                FactEvidenceSchema(
                    value=100,
                    source_zone="study_design",
                    anchor_ids=["anchor1"],
                    confidence=0.9,
                )
            ],
            "примерно 120": [
                FactEvidenceSchema(
                    value="примерно 120",
                    source_zone="other_zone",
                    anchor_ids=["anchor2"],
                    confidence=0.3,
                )
            ],
        }

        conflict = detector._analyze_conflict(
            fact_key="sample_size",
            value_groups=value_groups,
            prefer_source_zones=["study_design"],
        )

        assert conflict is not None
        assert conflict.severity == "warn"
        # Авторазрешение возможно, так как prefer zone имеет высокую уверенность,
        # а другая зона - низкую
        assert conflict.can_auto_resolve is True

    def test_analyze_conflict_no_auto_resolve_when_both_high_confidence(self):
        """Авторазрешение невозможно, если оба значения высокоуверенные."""
        from app.schemas.fact_conflicts import FactEvidence as FactEvidenceSchema

        detector = FactConflictDetector(None)  # type: ignore

        value_groups = {
            "100": [
                FactEvidenceSchema(
                    value=100,
                    source_zone="study_design",
                    anchor_ids=["anchor1"],
                    confidence=0.9,
                )
            ],
            "120": [
                FactEvidenceSchema(
                    value=120,
                    source_zone="statistics",
                    anchor_ids=["anchor2"],
                    confidence=0.85,
                )
            ],
        }

        conflict = detector._analyze_conflict(
            fact_key="sample_size",
            value_groups=value_groups,
            prefer_source_zones=["study_design"],
        )

        assert conflict is not None
        assert conflict.severity == "block"
        # Авторазрешение невозможно, так как оба значения высокоуверенные
        assert conflict.can_auto_resolve is False

