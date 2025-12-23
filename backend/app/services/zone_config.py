"""Сервис для загрузки и работы с конфигурациями зон: zone_set, zone_crosswalk, topic_zone_priors."""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from app.core.logging import logger
from app.db.enums import DocumentType


class ZoneConfigService:
    """Сервис для работы с конфигурациями зон."""

    def __init__(self):
        self._zone_sets: dict[str, list[str]] | None = None
        self._zone_crosswalk: dict[str, dict[str, dict[str, float]]] | None = None
        self._topic_zone_priors: dict[str, dict[str, float]] | None = None

    def _load_zone_sets(self) -> dict[str, list[str]]:
        """Загружает zone_sets из YAML файла."""
        if self._zone_sets is not None:
            return self._zone_sets

        config_path = Path(__file__).parent.parent / "data" / "source_zones" / "zone_sets.yaml"
        if not config_path.exists():
            logger.warning(f"Файл zone_sets.yaml не найден: {config_path}")
            return {}

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # Преобразуем ключи doc_type в строки для совместимости
        zone_sets: dict[str, list[str]] = {}
        for doc_type, zones in data.items():
            if isinstance(zones, list):
                zone_sets[doc_type] = [str(z) for z in zones]

        self._zone_sets = zone_sets
        logger.info(f"Загружены zone_sets для {len(zone_sets)} типов документов")
        return zone_sets

    def _load_zone_crosswalk(self) -> dict[str, dict[str, dict[str, float]]]:
        """Загружает zone_crosswalk из YAML файла."""
        if self._zone_crosswalk is not None:
            return self._zone_crosswalk

        config_path = Path(__file__).parent.parent / "data" / "source_zones" / "zone_crosswalk.yaml"
        if not config_path.exists():
            logger.warning(f"Файл zone_crosswalk.yaml не найден: {config_path}")
            return {}

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # Формат: {source_doc_type: {source_zone: {target_zone: weight}}}
        self._zone_crosswalk = data
        logger.info("Загружен zone_crosswalk")
        return data

    def _load_topic_zone_priors(self) -> dict[str, dict[str, float]]:
        """Загружает topic_zone_priors из YAML файла."""
        if self._topic_zone_priors is not None:
            return self._topic_zone_priors

        config_path = Path(__file__).parent.parent / "data" / "source_zones" / "topic_zone_priors.yaml"
        if not config_path.exists():
            logger.warning(f"Файл topic_zone_priors.yaml не найден: {config_path}")
            return {}

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        # Формат: {topic_key: {zone: priority}}
        self._topic_zone_priors = data
        logger.info(f"Загружены topic_zone_priors для {len(data)} топиков")
        return data

    def get_zone_set(self, doc_type: DocumentType | str) -> list[str]:
        """Возвращает набор разрешённых зон для doc_type."""
        zone_sets = self._load_zone_sets()
        doc_type_str = doc_type.value if isinstance(doc_type, DocumentType) else str(doc_type)
        return zone_sets.get(doc_type_str, [])

    def validate_zones(
        self,
        doc_type: DocumentType | str,
        prefer_zones: list[str] | None = None,
        fallback_zones: list[str] | None = None,
    ) -> tuple[bool, list[str]]:
        """
        Валидирует, что prefer_zones и fallback_zones являются подмножеством zone_set[doc_type].

        Returns:
            (is_valid, errors) - кортеж из флага валидности и списка ошибок
        """
        allowed_zones = set(self.get_zone_set(doc_type))
        errors: list[str] = []

        if prefer_zones:
            invalid_prefer = [z for z in prefer_zones if z not in allowed_zones]
            if invalid_prefer:
                errors.append(
                    f"prefer_source_zones содержит недопустимые зоны для doc_type={doc_type}: {invalid_prefer}"
                )

        if fallback_zones:
            invalid_fallback = [z for z in fallback_zones if z not in allowed_zones]
            if invalid_fallback:
                errors.append(
                    f"fallback_source_zones содержит недопустимые зоны для doc_type={doc_type}: {invalid_fallback}"
                )

        return len(errors) == 0, errors

    def get_crosswalk_zones(
        self,
        source_doc_type: DocumentType | str,
        source_zone: str,
        target_doc_type: DocumentType | str,
    ) -> list[tuple[str, float]]:
        """
        Возвращает список целевых зон с весами для cross-doc retrieval.

        Args:
            source_doc_type: Тип исходного документа
            source_zone: Исходная зона
            target_doc_type: Тип целевого документа
            target_zone: Целевая зона

        Returns:
            Список кортежей (target_zone, weight), отсортированный по убыванию веса
        """
        crosswalk = self._load_zone_crosswalk()
        source_doc_type_str = (
            source_doc_type.value if isinstance(source_doc_type, DocumentType) else str(source_doc_type)
        )
        target_doc_type_str = (
            target_doc_type.value if isinstance(target_doc_type, DocumentType) else str(target_doc_type)
        )

        # Ищем путь: source_doc_type -> source_zone -> target_doc_type -> target_zone
        source_zones = crosswalk.get(source_doc_type_str, {})
        source_zone_map = source_zones.get(source_zone, {})
        target_doc_map = source_zone_map.get(target_doc_type_str, {})

        # Преобразуем в список кортежей и сортируем по весу
        result = [(zone, weight) for zone, weight in target_doc_map.items()]
        result.sort(key=lambda x: x[1], reverse=True)

        return result

    def get_topic_zone_priors(self, topic_key: str) -> dict[str, float]:
        """Возвращает приоритеты зон для заданного топика."""
        priors = self._load_topic_zone_priors()
        return priors.get(topic_key, {})

    def apply_topic_zone_priors(
        self,
        zones: list[str],
        topic_key: str | None = None,
    ) -> list[str]:
        """
        Применяет приоритеты зон на основе topic_key.

        Args:
            zones: Список зон для приоритизации
            topic_key: Ключ топика (опционально)

        Returns:
            Отсортированный список зон по приоритету (сначала высокий приоритет)
        """
        if not topic_key:
            return zones

        priors = self.get_topic_zone_priors(topic_key)
        if not priors:
            return zones

        # Сортируем зоны по приоритету (убывание), затем по исходному порядку
        def get_priority(zone: str) -> float:
            return priors.get(zone, 0.0)

        return sorted(zones, key=get_priority, reverse=True)


# Глобальный экземпляр сервиса
_zone_config_service = ZoneConfigService()


def get_zone_config_service() -> ZoneConfigService:
    """Возвращает глобальный экземпляр ZoneConfigService."""
    return _zone_config_service

