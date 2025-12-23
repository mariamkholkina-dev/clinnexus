"""Классификатор source_zone для section_path заголовков документов."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import logger
from app.db.enums import DocumentType
from app.services.zone_set_registry import get_registry


@dataclass
class SourceZoneResult:
    """Результат классификации source_zone."""

    zone: str
    confidence: float
    matched_rule_id: str | None = None  # ID правила, которое сработало (для отладки)


class SourceZoneClassifier:
    """Классификатор source_zone на основе правил из YAML."""

    def __init__(self, rules_file: Path | str | None = None) -> None:
        """
        Инициализация классификатора.

        Args:
            rules_file: Путь к YAML файлу с правилами. Если None, используется файл по умолчанию.
                       Устаревший параметр - теперь правила загружаются по doc_type.
        """
        # Кэш правил для каждого doc_type
        self._rules_cache: dict[str, list[dict[str, Any]]] = {}
        self._compiled_patterns_cache: dict[str, list[tuple[str, list[re.Pattern[str]]]]] = {}
        
        # Реестр наборов зон для валидации
        self.zone_registry = get_registry()
        
        # Базовый путь к директории с правилами
        self.rules_dir = Path(__file__).parent.parent / "data" / "source_zones"

    def _get_rules_file_for_doc_type(self, doc_type: DocumentType) -> Path:
        """
        Возвращает путь к файлу правил для данного типа документа.
        
        Args:
            doc_type: Тип документа
            
        Returns:
            Путь к файлу правил
        """
        doc_type_str = doc_type.value
        rules_file = self.rules_dir / f"rules_{doc_type_str}.yaml"
        
        if not rules_file.exists():
            raise FileNotFoundError(
                f"Файл правил для doc_type={doc_type_str} не найден: {rules_file}"
            )
        
        return rules_file
    
    def _load_rules_for_doc_type(self, doc_type: DocumentType) -> list[dict[str, Any]]:
        """
        Загружает правила из YAML файла для данного типа документа.
        Использует кэш для переиспользования.
        
        Args:
            doc_type: Тип документа
            
        Returns:
            Список правил классификации
        """
        doc_type_str = doc_type.value
        
        # Проверяем кэш
        if doc_type_str in self._rules_cache:
            return self._rules_cache[doc_type_str]
        
        # Загружаем правила
        rules_file = self._get_rules_file_for_doc_type(doc_type)
        with open(rules_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            rules = data.get("source_zones", [])
        
        # Сохраняем в кэш
        self._rules_cache[doc_type_str] = rules
        
        return rules

    def _compile_patterns_for_doc_type(self, doc_type: DocumentType) -> list[tuple[str, list[re.Pattern[str]]]]:
        """
        Компилирует regex-паттерны для ускорения работы для данного типа документа.
        Использует кэш для переиспользования.

        Возвращает паттерны в порядке, приоритетном для более специфичных зон
        (например, serious_adverse_events должен проверяться раньше adverse_events).

        Args:
            doc_type: Тип документа

        Returns:
            Список кортежей (zone_name, list[compiled_patterns])
        """
        doc_type_str = doc_type.value
        
        # Проверяем кэш
        if doc_type_str in self._compiled_patterns_cache:
            return self._compiled_patterns_cache[doc_type_str]
        
        # Загружаем правила для данного doc_type
        rules = self._load_rules_for_doc_type(doc_type)
        
        compiled: list[tuple[str, list[re.Pattern[str]]]] = []
        # Список зон в порядке приоритета (более специфичные первыми)
        priority_order = [
            "serious_adverse_events",  # Более специфичная, чем adverse_events
        ]
        
        # Сначала добавляем зоны в порядке приоритета
        priority_zones = {z: None for z in priority_order}
        for zone_config in rules:
            zone_name = zone_config["zone"]
            if zone_name in priority_zones:
                patterns: list[re.Pattern[str]] = []
                for lang in ["ru", "en"]:
                    lang_patterns = zone_config.get("patterns", {}).get(lang, [])
                    for pattern_str in lang_patterns:
                        try:
                            patterns.append(re.compile(pattern_str))
                        except re.error as e:
                            logger.warning(
                                f"Ошибка компиляции паттерна для zone={zone_name}, lang={lang}, "
                                f"pattern={pattern_str}: {e}"
                            )
                priority_zones[zone_name] = patterns
        
        # Добавляем приоритетные зоны
        for zone_name in priority_order:
            if priority_zones[zone_name] is not None:
                compiled.append((zone_name, priority_zones[zone_name]))
        
        # Затем добавляем остальные зоны
        for zone_config in rules:
            zone_name = zone_config["zone"]
            if zone_name not in priority_order:
                patterns: list[re.Pattern[str]] = []
                for lang in ["ru", "en"]:
                    lang_patterns = zone_config.get("patterns", {}).get(lang, [])
                    for pattern_str in lang_patterns:
                        try:
                            patterns.append(re.compile(pattern_str))
                        except re.error as e:
                            logger.warning(
                                f"Ошибка компиляции паттерна для zone={zone_name}, lang={lang}, "
                                f"pattern={pattern_str}: {e}"
                            )
                compiled.append((zone_name, patterns))
        
        # Сохраняем в кэш
        self._compiled_patterns_cache[doc_type_str] = compiled

        return compiled

    def classify(
        self, 
        doc_type: DocumentType,
        section_path: str | list[str], 
        heading_text: str | None = None,
        language: str | None = None
    ) -> SourceZoneResult:
        """
        Классифицирует section_path и возвращает source_zone с confidence.

        Args:
            doc_type: Тип документа (определяет набор правил и разрешённые зоны)
            section_path: Путь секции вида "H1/H2/H3" или список заголовков ["H1", "H2", "H3"]
            heading_text: Текст текущего заголовка (опционально, для дополнительного контекста)
            language: Язык контента ("ru" или "en", опционально)

        Returns:
            SourceZoneResult с zone, confidence и matched_rule_id
            zone будет нормализован через ZoneSetRegistry (неразрешённые зоны → "unknown")
        """
        # Нормализуем section_path: если строка, разбиваем на сегменты
        if isinstance(section_path, str):
            if not section_path or section_path in ("ROOT", "__FRONTMATTER__", "FOOTNOTES"):
                return SourceZoneResult(zone="unknown", confidence=0.0, matched_rule_id=None)
            path_segments = [seg.strip() for seg in section_path.split("/") if seg.strip()]
        else:
            path_segments = [seg.strip() for seg in section_path if seg.strip()]

        if not path_segments:
            return SourceZoneResult(zone="unknown", confidence=0.0, matched_rule_id=None)

        # Загружаем скомпилированные паттерны для данного doc_type
        compiled_patterns = self._compile_patterns_for_doc_type(doc_type)

        # Добавляем heading_text в список для проверки, если он предоставлен
        text_to_check = path_segments.copy()
        if heading_text:
            text_to_check.append(heading_text.strip())

        # Ищем совпадения по всем сегментам пути и heading_text
        # Проверяем каждый zone с его паттернами в порядке приоритета
        # (первый найденный матч возвращаем сразу - более специфичные зоны идут первыми)
        best_match: tuple[str, float, str | None] | None = None
        
        for zone_name, patterns in compiled_patterns:
            matches_count = 0
            total_segments = len(text_to_check)
            match_strength = 0.0  # Сила совпадения: exact phrase > regex partial > weak token

            # Проверяем каждый сегмент пути на соответствие паттернам этой zone
            for segment in text_to_check:
                segment_lower = segment.lower()
                for pattern in patterns:
                    match = pattern.search(segment)
                    if match:
                        matches_count += 1
                        # Определяем силу совпадения
                        if match.group(0).lower() == segment_lower:
                            match_strength += 1.0  # Точное совпадение
                        elif len(match.group(0)) >= len(segment) * 0.8:
                            match_strength += 0.7  # Сильное частичное совпадение
                        else:
                            match_strength += 0.4  # Слабое частичное совпадение
                        break  # Один матч на сегмент достаточно

            if matches_count > 0:
                # Confidence = комбинация доли совпавших сегментов и силы совпадения
                base_confidence = matches_count / total_segments
                strength_bonus = match_strength / total_segments
                confidence = min(base_confidence + strength_bonus * 0.3, 1.0)
                
                # Сохраняем лучший матч (по confidence)
                if best_match is None or confidence > best_match[1]:
                    best_match = (zone_name, confidence, zone_name)
                
                # Если confidence очень высокий, возвращаем сразу
                if confidence >= 0.9:
                    # Нормализуем зону через реестр перед возвратом
                    normalized_zone = self.zone_registry.normalize_zone(doc_type, zone_name)
                    return SourceZoneResult(
                        zone=normalized_zone, 
                        confidence=confidence,
                        matched_rule_id=zone_name
                    )

        # Возвращаем лучший матч или unknown
        if best_match:
            # Нормализуем зону через реестр
            normalized_zone = self.zone_registry.normalize_zone(doc_type, best_match[0])
            return SourceZoneResult(
                zone=normalized_zone,
                confidence=best_match[1],
                matched_rule_id=best_match[2]
            )
        
        return SourceZoneResult(zone="unknown", confidence=0.0, matched_rule_id=None)


# Глобальный экземпляр классификатора (singleton pattern для переиспользования)
_classifier_instance: SourceZoneClassifier | None = None


def get_classifier(rules_file: Path | str | None = None) -> SourceZoneClassifier:
    """
    Получить глобальный экземпляр классификатора.

    Args:
        rules_file: Путь к YAML файлу с правилами. Используется только при первом вызове.

    Returns:
        Экземпляр SourceZoneClassifier
    """
    global _classifier_instance
    if _classifier_instance is None:
        _classifier_instance = SourceZoneClassifier(rules_file=rules_file)
    return _classifier_instance

