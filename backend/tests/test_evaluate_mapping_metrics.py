"""
Юнит-тесты для вспомогательных функций вычисления метрик в evaluate_mapping.py.
"""

from __future__ import annotations

import pytest

# Импортируем функции из evaluate_mapping
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.passport_tuning.evaluate_mapping import (
    compute_percentile,
    extract_anchor_hash,
    jaccard_similarity,
    longest_common_prefix_ratio,
)


class TestComputePercentile:
    """Тесты для функции compute_percentile."""

    def test_empty_list(self):
        """Тест с пустым списком."""
        assert compute_percentile([], 50.0) == 0.0

    def test_single_value(self):
        """Тест с одним значением."""
        assert compute_percentile([5.0], 50.0) == 5.0
        assert compute_percentile([10.0], 90.0) == 10.0

    def test_multiple_values_p50(self):
        """Тест P50 (медиана) для нескольких значений."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert compute_percentile(values, 50.0) == 3.0

    def test_multiple_values_p90(self):
        """Тест P90 для нескольких значений."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        # P90 должен быть 9-м элементом (индекс 8)
        result = compute_percentile(values, 90.0)
        assert result == 9.0

    def test_unsorted_values(self):
        """Тест с несортированными значениями."""
        values = [5.0, 1.0, 9.0, 3.0, 7.0]
        # Функция должна сортировать значения
        assert compute_percentile(values, 50.0) == 5.0

    def test_p10(self):
        """Тест P10."""
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        result = compute_percentile(values, 10.0)
        # P10 должен быть примерно 1-м элементом
        assert result == 1.0


class TestJaccardSimilarity:
    """Тесты для функции jaccard_similarity."""

    def test_identical_sets(self):
        """Тест с идентичными множествами."""
        set1 = {"a", "b", "c"}
        set2 = {"a", "b", "c"}
        assert jaccard_similarity(set1, set2) == 1.0

    def test_disjoint_sets(self):
        """Тест с непересекающимися множествами."""
        set1 = {"a", "b"}
        set2 = {"c", "d"}
        assert jaccard_similarity(set1, set2) == 0.0

    def test_partial_overlap(self):
        """Тест с частичным пересечением."""
        set1 = {"a", "b", "c"}
        set2 = {"b", "c", "d"}
        # intersection = {b, c} = 2, union = {a, b, c, d} = 4
        assert jaccard_similarity(set1, set2) == 0.5

    def test_empty_sets(self):
        """Тест с пустыми множествами."""
        assert jaccard_similarity(set(), set()) == 1.0

    def test_one_empty_set(self):
        """Тест с одним пустым множеством."""
        set1 = {"a", "b"}
        set2 = set()
        assert jaccard_similarity(set1, set2) == 0.0
        assert jaccard_similarity(set2, set1) == 0.0

    def test_no_overlap(self):
        """Тест без пересечения."""
        set1 = {"a"}
        set2 = {"b"}
        assert jaccard_similarity(set1, set2) == 0.0


class TestLongestCommonPrefixRatio:
    """Тесты для функции longest_common_prefix_ratio."""

    def test_identical_paths(self):
        """Тест с идентичными путями."""
        assert longest_common_prefix_ratio("1.2.3", "1.2.3") == 1.0
        assert longest_common_prefix_ratio("a/b/c", "a/b/c") == 1.0

    def test_common_prefix(self):
        """Тест с общим префиксом."""
        # "1.2.3" и "1.2.4" имеют общий префикс "1.2" (2 части из 3)
        result = longest_common_prefix_ratio("1.2.3", "1.2.4")
        assert result == pytest.approx(2.0 / 3.0, rel=1e-6)

    def test_no_common_prefix(self):
        """Тест без общего префикса."""
        assert longest_common_prefix_ratio("1.2.3", "4.5.6") == 0.0

    def test_empty_paths(self):
        """Тест с пустыми путями."""
        assert longest_common_prefix_ratio("", "") == 0.0
        assert longest_common_prefix_ratio("1.2", "") == 0.0
        assert longest_common_prefix_ratio("", "1.2") == 0.0

    def test_slash_separator(self):
        """Тест с разделителем '/'."""
        result = longest_common_prefix_ratio("a/b/c", "a/b/d")
        assert result == pytest.approx(2.0 / 3.0, rel=1e-6)

    def test_different_lengths(self):
        """Тест с путями разной длины."""
        # "1.2" и "1.2.3.4" имеют общий префикс "1.2" (2 части)
        # max_length = 4
        result = longest_common_prefix_ratio("1.2", "1.2.3.4")
        assert result == pytest.approx(2.0 / 4.0, rel=1e-6)

    def test_single_part(self):
        """Тест с одной частью."""
        assert longest_common_prefix_ratio("1", "1") == 1.0
        assert longest_common_prefix_ratio("1", "2") == 0.0


class TestExtractAnchorHash:
    """Тесты для функции extract_anchor_hash."""

    def test_full_format(self):
        """Тест с полным форматом anchor_id."""
        anchor_id = "doc-version-id:1.2.3:hdr:1:abc123hash"
        assert extract_anchor_hash(anchor_id) == "abc123hash"

    def test_minimal_format(self):
        """Тест с минимальным форматом (только hash)."""
        anchor_id = "hash123"
        assert extract_anchor_hash(anchor_id) == "hash123"

    def test_with_colon_no_full_format(self):
        """Тест с двоеточием, но не полным форматом."""
        anchor_id = "prefix:hash123"
        assert extract_anchor_hash(anchor_id) == "hash123"

    def test_empty_string(self):
        """Тест с пустой строкой."""
        assert extract_anchor_hash("") == ""

    def test_multiple_colons(self):
        """Тест с несколькими двоеточиями."""
        anchor_id = "a:b:c:d:e:f"
        assert extract_anchor_hash(anchor_id) == "f"

    def test_real_world_example(self):
        """Тест с реальным примером формата."""
        # Формат: {doc_version_id}:{section_path}:{content_type}:{ordinal}:{hash}
        anchor_id = "550e8400-e29b-41d4-a716-446655440000:1.2.3:p:5:a1b2c3d4e5f6"
        assert extract_anchor_hash(anchor_id) == "a1b2c3d4e5f6"

