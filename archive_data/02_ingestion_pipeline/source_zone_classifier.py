"""Классификатор source_zone для section_path заголовков документов."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import logger


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
        """
        if rules_file is None:
            # Путь относительно расположения этого файла
            rules_file = Path(__file__).parent.parent / "data" / "source_zone_rules.yaml"

        self.rules_file = Path(rules_file)
        if not self.rules_file.exists():
            raise FileNotFoundError(f"Файл правил не найден: {self.rules_file}")

        self.rules = self._load_rules()
        self._compiled_patterns = self._compile_patterns()

    def _load_rules(self) -> list[dict[str, Any]]:
        """Загружает правила из YAML файла."""
        with open(self.rules_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("source_zones", [])

    def _compile_patterns(self) -> list[tuple[str, list[re.Pattern[str]]]]:
        """
        Компилирует regex-паттерны для ускорения работы.

        Возвращает паттерны в порядке, приоритетном для более специфичных зон
        (например, serious_adverse_events должен проверяться раньше adverse_events).

        Returns:
            Список кортежей (zone_name, list[compiled_patterns])
        """
        compiled: list[tuple[str, list[re.Pattern[str]]]] = []
        # Список зон в порядке приоритета (более специфичные первыми)
        priority_order = [
            "serious_adverse_events",  # Более специфичная, чем adverse_events
        ]
        
        # Сначала добавляем зоны в порядке приоритета
        priority_zones = {z: None for z in priority_order}
        for zone_config in self.rules:
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
        for zone_config in self.rules:
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

        return compiled

    def classify(
        self, 
        section_path: str | list[str], 
        heading_text: str | None = None,
        language: str | None = None
    ) -> SourceZoneResult:
        """
        Классифицирует section_path и возвращает source_zone с confidence.

        Args:
            section_path: Путь секции вида "H1/H2/H3" или список заголовков ["H1", "H2", "H3"]
            heading_text: Текст текущего заголовка (опционально, для дополнительного контекста)
            language: Язык контента ("ru" или "en", опционально)

        Returns:
            SourceZoneResult с zone, confidence и matched_rule_id
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

        # Добавляем heading_text в список для проверки, если он предоставлен
        text_to_check = path_segments.copy()
        if heading_text:
            text_to_check.append(heading_text.strip())

        # Ищем совпадения по всем сегментам пути и heading_text
        # Проверяем каждый zone с его паттернами в порядке приоритета
        # (первый найденный матч возвращаем сразу - более специфичные зоны идут первыми)
        best_match: tuple[str, float, str | None] | None = None
        
        for zone_name, patterns in self._compiled_patterns:
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
                    return SourceZoneResult(
                        zone=zone_name, 
                        confidence=confidence,
                        matched_rule_id=zone_name
                    )

        # Возвращаем лучший матч или unknown
        if best_match:
            return SourceZoneResult(
                zone=best_match[0],
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

