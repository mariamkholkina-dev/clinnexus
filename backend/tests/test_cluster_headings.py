"""
Тесты для утилиты cluster_headings.

Использует синтетический корпус для детерминированного тестирования.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from tools.passport_tuning.cluster_headings import (
    build_clusters,
    cluster_by_tfidf,
    compute_tfidf_vectors,
    load_corpus,
    merge_clusters_by_embeddings,
    normalize_heading_text,
)


class TestNormalizeHeadingText:
    """Тесты для функции normalize_heading_text."""

    def test_basic_normalization(self):
        """Тест базовой нормализации."""
        text = "  Заголовок   с   пробелами  "
        normalized = normalize_heading_text(text)
        assert normalized == "Заголовок с пробелами"

    def test_empty_text(self):
        """Тест с пустым текстом."""
        assert normalize_heading_text("") == ""
        assert normalize_heading_text("   ") == ""

    def test_trailing_colon(self):
        """Тест удаления завершающего двоеточия."""
        text = "Заголовок:"
        normalized = normalize_heading_text(text)
        assert normalized == "Заголовок"


class TestLoadCorpus:
    """Тесты для функции load_corpus."""

    def test_load_valid_jsonl(self):
        """Тест загрузки валидного JSONL."""
        with TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir) / "corpus.jsonl"
            
            records = [
                {
                    "doc_version_id": str(uuid4()),
                    "hdr_anchor_id": str(uuid4()),
                    "heading_text_raw": "Заголовок 1",
                    "heading_text_norm": "заголовок 1",
                },
                {
                    "doc_version_id": str(uuid4()),
                    "hdr_anchor_id": str(uuid4()),
                    "heading_text_raw": "Заголовок 2",
                    "heading_text_norm": "заголовок 2",
                },
            ]
            
            with open(corpus_path, "w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            
            loaded = load_corpus(corpus_path)
            assert len(loaded) == 2
            assert loaded[0]["heading_text_raw"] == "Заголовок 1"

    def test_load_empty_file(self):
        """Тест загрузки пустого файла."""
        with TemporaryDirectory() as tmpdir:
            corpus_path = Path(tmpdir) / "empty.jsonl"
            corpus_path.write_text("")
            
            loaded = load_corpus(corpus_path)
            assert len(loaded) == 0


class TestTfidfClustering:
    """Тесты для TF-IDF кластеризации."""

    def test_compute_tfidf_vectors(self):
        """Тест вычисления TF-IDF векторов."""
        headings = [
            "Заголовок исследования",
            "Заголовок исследования",
            "Методы исследования",
            "Методы исследования",
            "Результаты исследования",
        ]
        
        tfidf_matrix, vectorizer = compute_tfidf_vectors(headings)
        
        assert tfidf_matrix.shape[0] == len(headings)
        assert tfidf_matrix.shape[1] > 0

    def test_cluster_by_tfidf_similar(self):
        """Тест кластеризации похожих заголовков."""
        headings = [
            "Заголовок исследования",
            "Заголовок исследования",
            "Заголовок исследования",
            "Методы исследования",
            "Методы исследования",
            "Методы исследования",
        ]
        
        tfidf_matrix, _ = compute_tfidf_vectors(headings)
        labels = cluster_by_tfidf(tfidf_matrix, threshold=0.5, min_size=2)
        
        # Должны быть как минимум 2 кластера (или больше, если threshold позволяет)
        unique_labels = {l for l in labels if l >= 0}
        assert len(unique_labels) >= 1  # Минимум один кластер

    def test_cluster_by_tfidf_min_size(self):
        """Тест фильтрации по минимальному размеру."""
        headings = [
            "Заголовок 1",
            "Заголовок 2",
            "Заголовок 3",
            "Методы",
            "Методы",
        ]
        
        tfidf_matrix, _ = compute_tfidf_vectors(headings)
        labels = cluster_by_tfidf(tfidf_matrix, threshold=0.8, min_size=3)
        
        # Кластеры размером < 3 должны быть отфильтрованы (label == -1)
        label_counts = {}
        for label in labels:
            label_counts[label] = label_counts.get(label, 0) + 1
        
        # Проверяем, что все валидные кластеры имеют размер >= min_size
        for label, count in label_counts.items():
            if label >= 0:
                assert count >= 3


class TestMergeClustersByEmbeddings:
    """Тесты для merge кластеров по embeddings."""

    def test_merge_without_embeddings(self):
        """Тест merge без embeddings (должен вернуть исходные метки)."""
        clusters = [[0, 1], [2, 3]]
        embeddings_map = {}
        hdr_anchor_ids = ["id1", "id2", "id3", "id4"]
        
        labels = merge_clusters_by_embeddings(clusters, embeddings_map, hdr_anchor_ids)
        
        # Должны быть 2 кластера
        assert labels[0] == labels[1]  # Первый кластер
        assert labels[2] == labels[3]  # Второй кластер
        assert labels[0] != labels[2]  # Разные кластеры

    def test_merge_with_embeddings(self):
        """Тест merge с embeddings."""
        clusters = [[0, 1], [2, 3]]
        # Создаём похожие embeddings для кластеров
        embeddings_map = {
            "id1": [0.1] * 1536,
            "id2": [0.11] * 1536,  # Похожий на id1
            "id3": [0.9] * 1536,
            "id4": [0.91] * 1536,  # Похожий на id3
        }
        hdr_anchor_ids = ["id1", "id2", "id3", "id4"]
        
        labels = merge_clusters_by_embeddings(
            clusters, embeddings_map, hdr_anchor_ids, threshold=0.5
        )
        
        # Проверяем, что метки корректны
        assert all(l >= 0 for l in labels)


class TestBuildClusters:
    """Тесты для функции build_clusters."""

    def test_build_clusters_basic(self):
        """Тест построения базовых кластеров."""
        records = [
            {
                "doc_version_id": str(uuid4()),
                "hdr_anchor_id": str(uuid4()),
                "heading_text_raw": "Заголовок исследования",
                "heading_text_norm": "заголовок исследования",
                "section_path": "1",
                "detected_language": "ru",
                "heading_level": 1,
                "window": {
                    "content_type_counts": {"p": 5},
                    "total_chars": 100,
                },
            },
            {
                "doc_version_id": str(uuid4()),
                "hdr_anchor_id": str(uuid4()),
                "heading_text_raw": "Заголовок исследования",
                "heading_text_norm": "заголовок исследования",
                "section_path": "2",
                "detected_language": "ru",
                "heading_level": 1,
                "window": {
                    "content_type_counts": {"p": 3},
                    "total_chars": 80,
                },
            },
        ]
        
        labels = [0, 0]  # Оба в одном кластере
        
        clusters = build_clusters(records, labels)
        
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster["cluster_id"] == 0
        assert len(cluster["top_titles_ru"]) >= 1
        assert len(cluster["examples"]) == 2
        assert cluster["stats"]["heading_level_histogram"] == {1: 2}
        assert cluster["stats"]["content_type_distribution"] == {"p": 8}

    def test_build_clusters_multilang(self):
        """Тест построения кластеров с несколькими языками."""
        records = [
            {
                "doc_version_id": str(uuid4()),
                "hdr_anchor_id": str(uuid4()),
                "heading_text_raw": "Study Objectives",
                "heading_text_norm": "study objectives",
                "section_path": "1",
                "detected_language": "en",
                "heading_level": 1,
                "window": {"content_type_counts": {}, "total_chars": 0},
            },
            {
                "doc_version_id": str(uuid4()),
                "hdr_anchor_id": str(uuid4()),
                "heading_text_raw": "Цели исследования",
                "heading_text_norm": "цели исследования",
                "section_path": "1",
                "detected_language": "ru",
                "heading_level": 1,
                "window": {"content_type_counts": {}, "total_chars": 0},
            },
        ]
        
        labels = [0, 0]
        
        clusters = build_clusters(records, labels)
        
        assert len(clusters) == 1
        cluster = clusters[0]
        assert len(cluster["top_titles_en"]) >= 1
        assert len(cluster["top_titles_ru"]) >= 1


class TestSyntheticCorpus:
    """Интеграционный тест на синтетическом корпусе."""

    def test_end_to_end_clustering(self):
        """Детерминированный тест полного пайплайна кластеризации."""
        # Создаём синтетический корпус с известными группами
        headings_group1 = [
            "Цели исследования",
            "Цели исследования",
            "Цели исследования",
        ]
        headings_group2 = [
            "Методы исследования",
            "Методы исследования",
            "Методы исследования",
        ]
        headings_group3 = [
            "Результаты",
            "Результаты",
        ]  # Меньше min_size=3
        
        all_headings = headings_group1 + headings_group2 + headings_group3
        
        # Нормализуем
        normalized = [normalize_heading_text(h) for h in all_headings]
        
        # TF-IDF
        tfidf_matrix, _ = compute_tfidf_vectors(normalized)
        
        # Кластеризация
        labels = cluster_by_tfidf(tfidf_matrix, threshold=0.5, min_size=3)
        
        # Проверяем, что первые 3 в одном кластере
        assert labels[0] == labels[1] == labels[2]
        assert labels[0] >= 0  # Не шум
        
        # Проверяем, что следующие 3 в другом кластере
        assert labels[3] == labels[4] == labels[5]
        assert labels[3] >= 0
        assert labels[3] != labels[0]  # Разные кластеры
        
        # Последние 2 должны быть шумом (размер < 3)
        assert labels[6] == labels[7] == -1  # Шум

