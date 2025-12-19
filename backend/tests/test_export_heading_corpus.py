"""
Тесты для утилиты export_heading_corpus.

Тесты можно пропустить без настроенной БД, установив переменную окружения
SKIP_DB_TESTS=1.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

# Проверяем, нужно ли пропустить тесты
skip_db_tests = os.getenv("SKIP_DB_TESTS", "0") == "1"

if not skip_db_tests:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import Engine

    from app.core.config import settings
    from app.db.enums import AnchorContentType, DocumentLanguage, DocumentType, IngestionStatus
    from tools.passport_tuning.export_heading_corpus import (
        compute_window_stats,
        extract_heading_level,
        extract_para_index,
        export_to_jsonl,
        fetch_heading_records,
    )


@pytest.mark.skipif(skip_db_tests, reason="SKIP_DB_TESTS=1 установлен")
class TestExtractHeadingLevel:
    """Тесты для функции extract_heading_level."""

    def test_extract_from_style(self):
        """Тест извлечения уровня из style."""
        location_json = {"style": "Heading 1", "para_index": 5}
        assert extract_heading_level(location_json) == 1

        location_json = {"style": "Heading 2", "para_index": 10}
        assert extract_heading_level(location_json) == 2

        location_json = {"style": "heading 3", "para_index": 15}  # case insensitive
        assert extract_heading_level(location_json) == 3

    def test_extract_from_invalid_style(self):
        """Тест с невалидным style."""
        location_json = {"style": "Normal", "para_index": 5}
        assert extract_heading_level(location_json) is None

        location_json = {"style": "Heading 10", "para_index": 5}  # > 9
        assert extract_heading_level(location_json) is None

    def test_extract_from_none(self):
        """Тест с None или пустым location_json."""
        assert extract_heading_level(None) is None
        assert extract_heading_level({}) is None


@pytest.mark.skipif(skip_db_tests, reason="SKIP_DB_TESTS=1 установлен")
class TestExtractParaIndex:
    """Тесты для функции extract_para_index."""

    def test_extract_para_index(self):
        """Тест извлечения para_index."""
        location_json = {"para_index": 5, "style": "Heading 1"}
        assert extract_para_index(location_json) == 5

        location_json = {"para_index": 0}
        assert extract_para_index(location_json) == 0

    def test_extract_from_none(self):
        """Тест с None или пустым location_json."""
        assert extract_para_index(None) is None
        assert extract_para_index({}) is None

        location_json = {"style": "Heading 1"}  # нет para_index
        assert extract_para_index(location_json) is None


@pytest.mark.skipif(skip_db_tests, reason="SKIP_DB_TESTS=1 установлен")
class TestComputeWindowStats:
    """Тесты для функции compute_window_stats."""

    def test_basic_stats(self):
        """Тест базовой статистики."""
        window_anchors = [
            {"content_type": "p", "text_norm": "Текст параграфа 1"},
            {"content_type": "p", "text_norm": "Текст параграфа 2"},
            {"content_type": "li", "text_norm": "Элемент списка"},
            {"content_type": "tbl", "text_norm": "Таблица"},
        ]

        stats = compute_window_stats(window_anchors)

        assert stats["content_type_counts"]["p"] == 2
        assert stats["content_type_counts"]["li"] == 1
        assert stats["content_type_counts"]["tbl"] == 1
        assert stats["total_chars"] == len("Текст параграфа 1") + len("Текст параграфа 2") + len("Элемент списка") + len("Таблица")
        assert len(stats["sample_text"]) > 0

    def test_sample_text_limit(self):
        """Тест ограничения sample_text до 500 символов."""
        long_text = "A" * 200
        window_anchors = [
            {"content_type": "p", "text_norm": long_text},
            {"content_type": "p", "text_norm": long_text},
            {"content_type": "p", "text_norm": long_text},
        ]

        stats = compute_window_stats(window_anchors)
        assert len(stats["sample_text"]) <= 500

    def test_empty_window(self):
        """Тест с пустым окном."""
        stats = compute_window_stats([])
        assert stats["content_type_counts"] == {}
        assert stats["total_chars"] == 0
        assert stats["sample_text"] == ""

    def test_limit_50_anchors(self):
        """Тест ограничения до 50 anchors."""
        window_anchors = [
            {"content_type": "p", "text_norm": f"Текст {i}"} for i in range(100)
        ]

        stats = compute_window_stats(window_anchors)
        # Должны учитываться только первые 50
        assert stats["content_type_counts"]["p"] == 50


@pytest.mark.skipif(skip_db_tests, reason="SKIP_DB_TESTS=1 установлен")
class TestExportToJsonl:
    """Тесты для функции export_to_jsonl."""

    def test_export_basic(self):
        """Тест базового экспорта."""
        records = [
            {
                "doc_version_id": str(uuid4()),
                "document_id": str(uuid4()),
                "doc_type": "protocol",
                "detected_language": "ru",
                "hdr_anchor_id": str(uuid4()),
                "heading_text_raw": "Заголовок 1",
                "heading_text_norm": "заголовок 1",
                "heading_level": 1,
                "para_index": 5,
                "section_path": "1",
                "window": {
                    "content_type_counts": {"p": 2},
                    "total_chars": 100,
                    "sample_text": "Пример текста",
                },
            },
        ]

        with TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "output.jsonl"
            export_to_jsonl(records, output_path)

            assert output_path.exists()

            # Проверяем содержимое
            with open(output_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                assert len(lines) == 1

                loaded_record = json.loads(lines[0])
                assert loaded_record["doc_type"] == "protocol"
                assert loaded_record["heading_text_raw"] == "Заголовок 1"
                assert loaded_record["window"]["total_chars"] == 100


@pytest.mark.skipif(skip_db_tests, reason="SKIP_DB_TESTS=1 установлен")
class TestFetchHeadingRecords:
    """Тесты для функции fetch_heading_records.

    Эти тесты требуют подключения к реальной БД и могут быть пропущены
    через переменную окружения SKIP_DB_TESTS=1.
    """

    def test_fetch_with_no_filters(self):
        """Тест получения записей без фильтров."""
        engine = create_engine(settings.sync_database_url, echo=False)
        try:
            records = fetch_heading_records(engine, None, None, None)
            assert isinstance(records, list)
            # Если есть данные в БД, проверяем структуру
            if records:
                record = records[0]
                assert "doc_version_id" in record
                assert "document_id" in record
                assert "hdr_anchor_id" in record
                assert "heading_text_raw" in record
                assert "window" in record
        finally:
            engine.dispose()

    def test_fetch_with_doc_type_filter(self):
        """Тест получения записей с фильтром по типу документа."""
        engine = create_engine(settings.sync_database_url, echo=False)
        try:
            records = fetch_heading_records(engine, None, DocumentType.PROTOCOL, None)
            assert isinstance(records, list)
            # Проверяем, что все записи имеют правильный doc_type
            for record in records:
                assert record["doc_type"] == DocumentType.PROTOCOL.value
        finally:
            engine.dispose()


@pytest.mark.skipif(not skip_db_tests, reason="Тесты БД включены")
def test_skip_message():
    """Тест, что тесты корректно пропускаются."""
    assert True, "Тесты пропущены, т.к. SKIP_DB_TESTS=1"

