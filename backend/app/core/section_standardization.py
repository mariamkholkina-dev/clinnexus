"""Утилиты для стандартизации 12 основных секций.

Определяет канонические ключи для source_zone и target_section,
а также правила prefer_source_zones для каждой target_section.
"""

from __future__ import annotations

from app.db.enums import SourceZone

# 12 канонических ключей (без "unknown")
CANONICAL_SECTION_KEYS = [
    SourceZone.OVERVIEW.value,
    SourceZone.DESIGN.value,
    SourceZone.IP.value,
    SourceZone.STATISTICS.value,
    SourceZone.SAFETY.value,
    SourceZone.ENDPOINTS.value,
    SourceZone.POPULATION.value,
    SourceZone.PROCEDURES.value,
    SourceZone.DATA_MANAGEMENT.value,
    SourceZone.ETHICS.value,
    SourceZone.ADMIN.value,
    SourceZone.APPENDIX.value,
]

# Правила prefer_source_zones для каждой target_section
TARGET_SECTION_PREFER_SOURCE_ZONES: dict[str, dict[str, list[str]]] = {
    SourceZone.OVERVIEW.value: {
        "prefer": [SourceZone.OVERVIEW.value, SourceZone.DESIGN.value],
        "fallback": [SourceZone.ENDPOINTS.value, SourceZone.POPULATION.value, SourceZone.IP.value],
    },
    SourceZone.DESIGN.value: {
        "prefer": [SourceZone.DESIGN.value],
        "fallback": [SourceZone.OVERVIEW.value],
    },
    SourceZone.IP.value: {
        "prefer": [SourceZone.IP.value],
        "fallback": [SourceZone.OVERVIEW.value, SourceZone.DESIGN.value],
    },
    SourceZone.ENDPOINTS.value: {
        "prefer": [SourceZone.ENDPOINTS.value],
        "fallback": [SourceZone.OVERVIEW.value, SourceZone.PROCEDURES.value],
    },
    SourceZone.POPULATION.value: {
        "prefer": [SourceZone.POPULATION.value],
        "fallback": [SourceZone.DESIGN.value],
    },
    SourceZone.PROCEDURES.value: {
        "prefer": [SourceZone.PROCEDURES.value],
        "fallback": [SourceZone.DESIGN.value],
    },
    SourceZone.SAFETY.value: {
        "prefer": [SourceZone.SAFETY.value],
        "fallback": [SourceZone.PROCEDURES.value],
    },
    SourceZone.STATISTICS.value: {
        "prefer": [SourceZone.STATISTICS.value],
        "fallback": [SourceZone.DESIGN.value],
    },
    SourceZone.DATA_MANAGEMENT.value: {
        "prefer": [SourceZone.DATA_MANAGEMENT.value],
        "fallback": [SourceZone.ADMIN.value],
    },
    SourceZone.ETHICS.value: {
        "prefer": [SourceZone.ETHICS.value],
        "fallback": [SourceZone.OVERVIEW.value, SourceZone.ADMIN.value],
    },
    SourceZone.ADMIN.value: {
        "prefer": [SourceZone.ADMIN.value],
        "fallback": [SourceZone.DATA_MANAGEMENT.value, SourceZone.ETHICS.value],
    },
    SourceZone.APPENDIX.value: {
        "prefer": [SourceZone.APPENDIX.value],
        "fallback": [SourceZone.PROCEDURES.value, SourceZone.ADMIN.value],
    },
}


def is_valid_target_section(value: str) -> bool:
    """Проверяет, является ли значение валидным target_section (один из 12 канонических ключей)."""
    return value in CANONICAL_SECTION_KEYS


def is_valid_source_zone(value: str) -> bool:
    """Проверяет, является ли значение валидным source_zone (12 ключей + unknown)."""
    return value in CANONICAL_SECTION_KEYS or value == SourceZone.UNKNOWN.value


def get_prefer_source_zones(target_section: str) -> dict[str, list[str]]:
    """Возвращает prefer и fallback source_zones для заданной target_section."""
    return TARGET_SECTION_PREFER_SOURCE_ZONES.get(
        target_section,
        {"prefer": [], "fallback": []}
    )


def validate_target_section(value: str) -> None:
    """Валидирует target_section и выбрасывает ValueError, если значение невалидно."""
    if not is_valid_target_section(value):
        raise ValueError(
            f"target_section должен быть одним из 12 канонических ключей: {CANONICAL_SECTION_KEYS}, "
            f"получено: {value}"
        )


def validate_source_zone(value: str) -> None:
    """Валидирует source_zone и выбрасывает ValueError, если значение невалидно."""
    if not is_valid_source_zone(value):
        raise ValueError(
            f"source_zone должен быть одним из 12 канонических ключей или 'unknown': "
            f"{CANONICAL_SECTION_KEYS + [SourceZone.UNKNOWN.value]}, получено: {value}"
        )

