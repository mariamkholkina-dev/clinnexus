"""
Unit-тесты для API passport-tuning mapping с новыми полями mapping_mode и notes.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.passport_tuning import MappingMode


@pytest.fixture
def temp_mapping_file():
    """Создает временный файл для маппинга."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({}, f)
        temp_path = Path(f.name)
    
    yield temp_path
    
    # Очистка
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def client_with_temp_mapping(temp_mapping_file):
    """Создает тестовый клиент с временным файлом маппинга."""
    with patch("app.api.v1.passport_tuning.get_mapping_file_path", return_value=temp_mapping_file):
        app = create_app()
        client = TestClient(app)
        yield client


class TestMappingModeAmbiguous:
    """Тесты для сохранения маппинга с mapping_mode='ambiguous'."""

    def test_save_mapping_with_ambiguous_mode(self, client_with_temp_mapping, temp_mapping_file):
        """Тест: сохранение маппинга с mapping_mode='ambiguous' должно быть валидным."""
        mapping_data = {
            "cluster_1": {
                "doc_type": "protocol",
                "section_key": "protocol.endpoints",
                "title_ru": "Конечные точки",
                "mapping_mode": "ambiguous",
                "notes": "Неоднозначное соответствие",
            }
        }

        response = client_with_temp_mapping.post("/api/passport-tuning/mapping", json=mapping_data)
        
        assert response.status_code == 200
        assert response.json()["message"] == "Mapping успешно сохранен"
        assert response.json()["items_count"] == 1

        # Проверяем, что данные сохранены корректно
        with open(temp_mapping_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        
        assert "cluster_1" in saved_data
        assert saved_data["cluster_1"]["mapping_mode"] == "ambiguous"
        assert saved_data["cluster_1"]["notes"] == "Неоднозначное соответствие"

    def test_get_mapping_with_ambiguous_mode(self, client_with_temp_mapping, temp_mapping_file):
        """Тест: чтение маппинга с mapping_mode='ambiguous'."""
        # Сохраняем данные
        mapping_data = {
            "cluster_1": {
                "doc_type": "protocol",
                "section_key": "protocol.endpoints",
                "title_ru": "Конечные точки",
                "mapping_mode": "ambiguous",
                "notes": "Неоднозначное соответствие",
            }
        }
        with open(temp_mapping_file, "w", encoding="utf-8") as f:
            json.dump(mapping_data, f, ensure_ascii=False, indent=2)

        response = client_with_temp_mapping.get("/api/passport-tuning/mapping")
        
        assert response.status_code == 200
        data = response.json()
        assert "cluster_1" in data["mapping"]
        assert data["mapping"]["cluster_1"]["mapping_mode"] == "ambiguous"
        assert data["mapping"]["cluster_1"]["notes"] == "Неоднозначное соответствие"


class TestMappingModeSkip:
    """Тесты для сохранения маппинга с mapping_mode='skip'."""

    def test_save_mapping_with_skip_mode_no_section_key(self, client_with_temp_mapping, temp_mapping_file):
        """Тест: сохранение с mapping_mode='skip' без section_key должно быть валидным."""
        mapping_data = {
            "cluster_2": {
                "doc_type": "other",
                "section_key": "",
                "title_ru": None,
                "mapping_mode": "skip",
                "notes": "Кластер пропущен",
            }
        }

        response = client_with_temp_mapping.post("/api/passport-tuning/mapping", json=mapping_data)
        
        assert response.status_code == 200
        assert response.json()["message"] == "Mapping успешно сохранен"

        # Проверяем, что данные сохранены корректно
        with open(temp_mapping_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        
        assert "cluster_2" in saved_data
        assert saved_data["cluster_2"]["mapping_mode"] == "skip"
        assert saved_data["cluster_2"]["section_key"] == ""
        assert saved_data["cluster_2"]["notes"] == "Кластер пропущен"

    def test_save_mapping_with_skip_mode_minimal(self, client_with_temp_mapping, temp_mapping_file):
        """Тест: сохранение с mapping_mode='skip' с минимальными данными."""
        mapping_data = {
            "cluster_3": {
                "doc_type": None,
                "section_key": "",
                "mapping_mode": "skip",
            }
        }

        response = client_with_temp_mapping.post("/api/passport-tuning/mapping", json=mapping_data)
        
        assert response.status_code == 200

        # Проверяем, что данные сохранены
        with open(temp_mapping_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        
        assert "cluster_3" in saved_data
        assert saved_data["cluster_3"]["mapping_mode"] == "skip"


class TestMappingForAutotune:
    """Тесты для endpoint /mapping/for_autotune."""

    def test_for_autotune_excludes_ambiguous_and_skip(self, client_with_temp_mapping, temp_mapping_file):
        """Тест: for_autotune корректно исключает ambiguous и skip."""
        # Подготавливаем данные с разными режимами
        mapping_data = {
            "cluster_single": {
                "doc_type": "protocol",
                "section_key": "protocol.endpoints",
                "title_ru": "Конечные точки",
                "mapping_mode": "single",
            },
            "cluster_ambiguous": {
                "doc_type": "protocol",
                "section_key": "protocol.objectives",
                "title_ru": "Цели",
                "mapping_mode": "ambiguous",
                "notes": "Неоднозначно",
            },
            "cluster_skip": {
                "doc_type": "other",
                "section_key": "",
                "mapping_mode": "skip",
                "notes": "Пропущен",
            },
            "cluster_needs_split": {
                "doc_type": "protocol",
                "section_key": "protocol.methods",
                "title_ru": "Методы",
                "mapping_mode": "needs_split",
                "notes": "Нужен сплит",
            },
        }

        with open(temp_mapping_file, "w", encoding="utf-8") as f:
            json.dump(mapping_data, f, ensure_ascii=False, indent=2)

        response = client_with_temp_mapping.get("/api/passport-tuning/mapping/for_autotune")
        
        assert response.status_code == 200
        data = response.json()

        # Проверяем included (должен быть только single, needs_split исключен по умолчанию)
        assert "cluster_single" in data["included"]
        assert "cluster_ambiguous" not in data["included"]
        assert "cluster_skip" not in data["included"]
        assert "cluster_needs_split" not in data["included"]

        # Проверяем excluded
        assert len(data["excluded"]["ambiguous"]) == 1
        assert data["excluded"]["ambiguous"][0]["cluster_id"] == "cluster_ambiguous"
        assert len(data["excluded"]["skip"]) == 1
        assert data["excluded"]["skip"][0]["cluster_id"] == "cluster_skip"
        assert len(data["excluded"]["needs_split"]) == 1
        assert data["excluded"]["needs_split"][0]["cluster_id"] == "cluster_needs_split"

    def test_for_autotune_include_needs_split(self, client_with_temp_mapping, temp_mapping_file):
        """Тест: for_autotune с include_needs_split=True включает needs_split."""
        mapping_data = {
            "cluster_single": {
                "doc_type": "protocol",
                "section_key": "protocol.endpoints",
                "mapping_mode": "single",
            },
            "cluster_needs_split": {
                "doc_type": "protocol",
                "section_key": "protocol.methods",
                "mapping_mode": "needs_split",
            },
        }

        with open(temp_mapping_file, "w", encoding="utf-8") as f:
            json.dump(mapping_data, f, ensure_ascii=False, indent=2)

        response = client_with_temp_mapping.get(
            "/api/passport-tuning/mapping/for_autotune?include_needs_split=true"
        )
        
        assert response.status_code == 200
        data = response.json()

        # Оба кластера должны быть в included
        assert "cluster_single" in data["included"]
        assert "cluster_needs_split" in data["included"]
        assert len(data["excluded"]["needs_split"]) == 0


class TestBackwardCompatibility:
    """Тесты для обратной совместимости со старым форматом."""

    def test_get_mapping_without_mapping_mode(self, client_with_temp_mapping, temp_mapping_file):
        """Тест: чтение старого формата без mapping_mode должно устанавливать дефолт 'single'."""
        # Старый формат без mapping_mode и notes
        old_format_data = {
            "cluster_old": {
                "doc_type": "protocol",
                "section_key": "protocol.endpoints",
                "title_ru": "Конечные точки",
            }
        }

        with open(temp_mapping_file, "w", encoding="utf-8") as f:
            json.dump(old_format_data, f, ensure_ascii=False, indent=2)

        response = client_with_temp_mapping.get("/api/passport-tuning/mapping")
        
        assert response.status_code == 200
        data = response.json()
        assert "cluster_old" in data["mapping"]
        # Должен быть установлен дефолт
        assert data["mapping"]["cluster_old"]["mapping_mode"] == "single"
        assert data["mapping"]["cluster_old"]["notes"] is None

    def test_save_mapping_without_mapping_mode(self, client_with_temp_mapping, temp_mapping_file):
        """Тест: сохранение без mapping_mode должно устанавливать дефолт 'single'."""
        mapping_data = {
            "cluster_new": {
                "doc_type": "protocol",
                "section_key": "protocol.endpoints",
                "title_ru": "Конечные точки",
                # mapping_mode не указан
            }
        }

        response = client_with_temp_mapping.post("/api/passport-tuning/mapping", json=mapping_data)
        
        assert response.status_code == 200

        # Проверяем, что дефолт установлен
        with open(temp_mapping_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        
        assert saved_data["cluster_new"]["mapping_mode"] == "single"

