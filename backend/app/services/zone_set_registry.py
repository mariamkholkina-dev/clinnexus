"""Реестр наборов зон для каждого типа документа."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.core.logging import logger
from app.db.enums import DocumentType


class ZoneSetRegistry:
    """Реестр наборов разрешённых source_zones для каждого doc_type."""
    
    def __init__(self, zone_sets_file: Path | str | None = None) -> None:
        """
        Инициализация реестра.
        
        Args:
            zone_sets_file: Путь к YAML файлу с наборами зон. Если None, используется файл по умолчанию.
        """
        if zone_sets_file is None:
            # Путь относительно расположения этого файла
            zone_sets_file = Path(__file__).parent.parent / "data" / "source_zones" / "zone_sets.yaml"
        
        self.zone_sets_file = Path(zone_sets_file)
        if not self.zone_sets_file.exists():
            raise FileNotFoundError(f"Файл наборов зон не найден: {self.zone_sets_file}")
        
        self.zone_sets = self._load_zone_sets()
    
    def _load_zone_sets(self) -> dict[str, list[str]]:
        """Загружает наборы зон из YAML файла."""
        with open(self.zone_sets_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data or {}
    
    def get_allowed_zones(self, doc_type: DocumentType) -> list[str]:
        """
        Возвращает список разрешённых зон для данного типа документа.
        
        Args:
            doc_type: Тип документа
            
        Returns:
            Список разрешённых зон (например, ["overview", "design", ...])
        """
        doc_type_str = doc_type.value
        allowed = self.zone_sets.get(doc_type_str, [])
        
        if not allowed:
            logger.warning(
                f"Набор зон для doc_type={doc_type_str} не найден в конфигурации. "
                f"Используется пустой список."
            )
        
        return allowed
    
    def validate_zone(self, doc_type: DocumentType, zone_key: str) -> bool:
        """
        Проверяет, является ли zone_key разрешённой для данного doc_type.
        
        Args:
            doc_type: Тип документа
            zone_key: Ключ зоны для проверки
            
        Returns:
            True, если зона разрешена, False иначе
        """
        allowed_zones = self.get_allowed_zones(doc_type)
        return zone_key in allowed_zones
    
    def normalize_zone(self, doc_type: DocumentType, zone_key: str) -> str:
        """
        Нормализует zone_key: если зона не разрешена для doc_type, возвращает "unknown".
        
        Args:
            doc_type: Тип документа
            zone_key: Ключ зоны для нормализации
            
        Returns:
            zone_key, если он разрешён, иначе "unknown"
        """
        if self.validate_zone(doc_type, zone_key):
            return zone_key
        return "unknown"


# Глобальный экземпляр реестра (singleton pattern)
_registry_instance: ZoneSetRegistry | None = None


def get_registry(zone_sets_file: Path | str | None = None) -> ZoneSetRegistry:
    """
    Получить глобальный экземпляр реестра.
    
    Args:
        zone_sets_file: Путь к YAML файлу с наборами зон. Используется только при первом вызове.
    
    Returns:
        Экземпляр ZoneSetRegistry
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ZoneSetRegistry(zone_sets_file=zone_sets_file)
    return _registry_instance

