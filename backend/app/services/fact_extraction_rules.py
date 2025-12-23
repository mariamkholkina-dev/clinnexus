"""Реестр правил извлечения фактов (rules-first подход).

Каждое правило содержит:
- fact_key: dot path (например "study.phase")
- patterns: список regex паттернов (RU/EN)
- parser: функция для парсинга извлеченного значения
- confidence_policy: функция для вычисления confidence
- priority: приоритет правила (выше = важнее)
- preferred_source_zones: предпочтительные source_zone для поиска
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from app.db.enums import FactStatus


# Версия экстрактора (увеличивается при изменении логики)
EXTRACTOR_VERSION = 1


@dataclass(frozen=True)
class ExtractionRule:
    """Правило извлечения факта."""
    
    fact_type: str
    fact_key: str
    patterns_ru: list[re.Pattern]
    patterns_en: list[re.Pattern]
    parser: Callable[[str, re.Match], dict[str, Any] | None]
    confidence_policy: Callable[[str, re.Match], float]
    priority: int = 100  # По умолчанию средний приоритет
    preferred_source_zones: list[str] | None = None  # Например ["statistics", "endpoints"]


@dataclass(frozen=True)
class ExtractedFactCandidate:
    """Кандидат на извлеченный факт."""
    
    fact_type: str
    fact_key: str
    value_json: dict[str, Any]
    raw_value: str | None
    confidence: float
    evidence_anchor_ids: list[str]
    extractor_version: int
    meta_json: dict[str, Any] | None = None


# ============================================================================
# Утилиты нормализации
# ============================================================================


def normalize_whitespace(text: str) -> str:
    """Нормализует пробелы: множественные -> один, убирает начальные/конечные."""
    return re.sub(r"\s+", " ", text.strip())


def parse_int(raw: str) -> int | None:
    """Парсит целое число, убирая пробелы и запятые."""
    if not raw:
        return None
    cleaned = raw.strip().replace("\u00a0", " ").replace(" ", "").replace(",", "")
    if not cleaned.isdigit():
        return None
    try:
        val = int(cleaned)
    except ValueError:
        return None
    if val <= 0 or val > 1_000_000:
        return None
    return val


def parse_float(raw: str) -> float | None:
    """Парсит число с плавающей точкой."""
    if not raw:
        return None
    cleaned = raw.strip().replace("\u00a0", " ").replace(",", ".")
    try:
        val = float(cleaned)
    except ValueError:
        return None
    return val


def parse_range(raw: str) -> dict[str, int] | None:
    """Парсит диапазон вида "18–65", "от 18 до 65", "18-65 years"."""
    if not raw:
        return None
    
    # Паттерны для диапазонов
    patterns = [
        r"(\d+)\s*[–\-]\s*(\d+)",  # 18–65, 18-65
        r"от\s+(\d+)\s+до\s+(\d+)",  # от 18 до 65
        r"(\d+)\s+to\s+(\d+)",  # 18 to 65
        r"(\d+)\s*-\s*(\d+)",  # 18 - 65
    ]
    
    for pattern in patterns:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            min_val = parse_int(m.group(1))
            max_val = parse_int(m.group(2))
            if min_val is not None and max_val is not None and min_val <= max_val:
                return {"min": min_val, "max": max_val}
    
    return None


def parse_ratio(raw: str) -> str | None:
    """Парсит и нормализует соотношение вида "2:1", "2/1", "2 к 1" -> "2:1"."""
    if not raw:
        return None
    
    # Паттерны для соотношений
    patterns = [
        (r"(\d+)\s*:\s*(\d+)", lambda m: f"{m.group(1)}:{m.group(2)}"),  # 2:1
        (r"(\d+)\s*/\s*(\d+)", lambda m: f"{m.group(1)}:{m.group(2)}"),  # 2/1
        (r"(\d+)\s+к\s+(\d+)", lambda m: f"{m.group(1)}:{m.group(2)}"),  # 2 к 1
        (r"(\d+)\s+to\s+(\d+)", lambda m: f"{m.group(1)}:{m.group(2)}"),  # 2 to 1
    ]
    
    for pattern, formatter in patterns:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            num1 = parse_int(m.group(1))
            num2 = parse_int(m.group(2))
            if num1 is not None and num2 is not None and num2 > 0:
                return formatter(m)
    
    return None


def parse_duration(raw: str) -> dict[str, Any] | None:
    """Парсит длительность: "12 недель" -> {"value": 12, "unit": "week"}."""
    if not raw:
        return None
    
    # Паттерны для длительности
    patterns = [
        (r"(\d+)\s+недели?", {"unit": "week"}),
        (r"(\d+)\s+нед\.?", {"unit": "week"}),
        (r"(\d+)\s+weeks?", {"unit": "week"}),
        (r"(\d+)\s+месяц(?:а|ев)?", {"unit": "month"}),
        (r"(\d+)\s+мес\.?", {"unit": "month"}),
        (r"(\d+)\s+months?", {"unit": "month"}),
        (r"(\d+)\s+дн(?:я|ей)?", {"unit": "day"}),
        (r"(\d+)\s+дн\.?", {"unit": "day"}),
        (r"(\d+)\s+days?", {"unit": "day"}),
        (r"(\d+)\s+год(?:а|ов)?", {"unit": "year"}),
        (r"(\d+)\s+г\.?", {"unit": "year"}),
        (r"(\d+)\s+years?", {"unit": "year"}),
    ]
    
    for pattern, unit_info in patterns:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            value = parse_int(m.group(1))
            if value is not None:
                return {"value": value, **unit_info}
    
    return None


def parse_date_to_iso(raw: str) -> str | None:
    """Парсит дату из RU/EN форматов и возвращает ISO YYYY-MM-DD."""
    s = (raw or "").strip()
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip(" ,.;")
    
    # ISO
    m = re.match(r"^(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})$", s)
    if m:
        return _iso_from_ymd(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    
    # DD.MM.YYYY or DD/MM/YYYY
    m = re.match(r"^(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})$", s)
    if m:
        return _iso_from_ymd(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    
    # "05 March 2021" / "5 Mar 2021" / "5 марта 2021"
    m = re.match(r"^(?P<d>\d{1,2})\s+(?P<mon>[A-Za-zА-Яа-яёЁ]+)\s+(?P<y>\d{4})$", s)
    if m:
        mon = _month_to_int(m.group("mon"))
        if mon is not None:
            return _iso_from_ymd(int(m.group("y")), mon, int(m.group("d")))
    
    return None


def _iso_from_ymd(y: int, m: int, d: int) -> str | None:
    try:
        dt = date(y, m, d)
    except ValueError:
        return None
    return dt.isoformat()


def _month_to_int(mon: str) -> int | None:
    t = (mon or "").strip().lower().replace(".", "")
    ru = {
        "января": 1, "янв": 1, "февраля": 2, "фев": 2, "марта": 3, "мар": 3,
        "апреля": 4, "апр": 4, "мая": 5, "май": 5, "июня": 6, "июн": 6,
        "июля": 7, "июл": 7, "августа": 8, "авг": 8, "сентября": 9, "сен": 9, "сент": 9,
        "октября": 10, "окт": 10, "ноября": 11, "ноя": 11, "декабря": 12, "дек": 12,
    }
    en = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
        "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
        "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    }
    return ru.get(t) or en.get(t)


# ============================================================================
# Политики confidence
# ============================================================================


def confidence_high(text: str, match: re.Match) -> float:
    """Высокая уверенность (0.9) для точных совпадений."""
    return 0.9


def confidence_medium(text: str, match: re.Match) -> float:
    """Средняя уверенность (0.7) для вероятных совпадений."""
    return 0.7


def confidence_low(text: str, match: re.Match) -> float:
    """Низкая уверенность (0.5) для неопределенных совпадений."""
    return 0.5


def confidence_by_match_quality(text: str, match: re.Match) -> float:
    """Вычисляет confidence на основе качества совпадения."""
    # Если паттерн содержит ключевые слова - выше confidence
    if any(kw in text.lower() for kw in ["protocol", "study", "planned", "total"]):
        return 0.85
    return 0.7


# ============================================================================
# Парсеры для различных типов фактов
# ============================================================================


def parse_string_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит строковое значение."""
    val = match.group(1).strip() if match.lastindex >= 1 else match.group(0).strip()
    if not val:
        return None
    return {"value": normalize_whitespace(val)}


def parse_int_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит целочисленное значение."""
    raw = match.group(1) if match.lastindex >= 1 else match.group(0)
    val = parse_int(raw)
    if val is None:
        return None
    return {"value": val}


def parse_float_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит значение с плавающей точкой."""
    raw = match.group(1) if match.lastindex >= 1 else match.group(0)
    val = parse_float(raw)
    if val is None:
        return None
    return {"value": val}


def parse_date_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит дату."""
    raw = match.group(1) if match.lastindex >= 1 else match.group(0)
    iso = parse_date_to_iso(raw)
    if iso is None:
        return None
    return {"value": iso, "raw": raw.strip()}


def parse_range_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит диапазон."""
    raw = match.group(0)
    range_dict = parse_range(raw)
    if range_dict is None:
        return None
    return {"value": range_dict, "raw": raw}


def parse_ratio_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит соотношение."""
    raw = match.group(0)
    ratio = parse_ratio(raw)
    if ratio is None:
        return None
    return {"value": ratio, "raw": raw}


def parse_duration_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит длительность."""
    raw = match.group(0)
    duration = parse_duration(raw)
    if duration is None:
        return None
    return {"value": duration, "raw": raw}


def parse_age_min_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит минимальный возраст."""
    if match.lastindex and match.lastindex >= 1:
        val = parse_int(match.group(1))
        if val is not None:
            return {"value": val}
    return None


def parse_age_max_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит максимальный возраст."""
    if match.lastindex and match.lastindex >= 2:
        val = parse_int(match.group(2))
        if val is not None:
            return {"value": val}
    return None


def parse_boolean_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит булево значение."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["да", "yes", "true", "есть", "is"]):
        return {"value": True}
    if any(kw in text_lower for kw in ["нет", "no", "false", "нет", "not"]):
        return {"value": False}
    return None


# ============================================================================
# Реестр правил извлечения
# ============================================================================


def get_extraction_rules() -> list[ExtractionRule]:
    """Возвращает список всех правил извлечения фактов."""
    
    rules: list[ExtractionRule] = []
    
    # ========================================================================
    # Protocol Meta
    # ========================================================================
    
    # protocol_meta.protocol_version (уже есть, но обновим)
    rules.append(ExtractionRule(
        fact_type="protocol_meta",
        fact_key="protocol_version",
        patterns_ru=[
            re.compile(r"\b(?:версия|номер)\s+протокола\b\s*[:#]?\s*([A-Za-z0-9А-Яа-я][A-Za-z0-9А-Яа-я._/\-]{0,64})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bprotocol\s*(?:version|no\.?|number)\b\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9._/\-]{0,64})", re.IGNORECASE),
        ],
        parser=parse_string_value,
        confidence_policy=confidence_high,
        priority=200,
    ))
    
    # protocol_meta.protocol_date
    rules.append(ExtractionRule(
        fact_type="protocol_meta",
        fact_key="protocol_date",
        patterns_ru=[
            re.compile(r"\bдата\s+протокола\b\s*[:#]?\s*(.+)$", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bprotocol\s+date\b\s*[:#]?\s*(.+)$", re.IGNORECASE),
        ],
        parser=parse_date_value,
        confidence_policy=confidence_high,
        priority=200,
    ))
    
    # protocol_meta.amendment_date (уже есть)
    rules.append(ExtractionRule(
        fact_type="protocol_meta",
        fact_key="amendment_date",
        patterns_ru=[
            re.compile(r"\b(?:дата\s+(?:внесения\s+изменений|поправки|изменения)|дата\s+амендмента)\b\s*[:#]?\s*(.+)$", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:amendment\s+date|date\s+of\s+amendment)\b\s*[:#]?\s*(.+)$", re.IGNORECASE),
        ],
        parser=parse_date_value,
        confidence_policy=confidence_high,
        priority=200,
    ))
    
    # protocol_meta.sponsor_name
    rules.append(ExtractionRule(
        fact_type="protocol_meta",
        fact_key="sponsor_name",
        patterns_ru=[
            re.compile(r"\bспонсор\b[^:]{0,50}[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я\s.,\-]{5,100})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bsponsor\b[^:]{0,50}[:#]?\s*([A-Z][A-Za-z\s.,\-]{5,100})", re.IGNORECASE),
        ],
        parser=parse_string_value,
        confidence_policy=confidence_medium,
        priority=150,
    ))
    
    # protocol_meta.cro_name
    rules.append(ExtractionRule(
        fact_type="protocol_meta",
        fact_key="cro_name",
        patterns_ru=[
            re.compile(r"\b(?:cro|контрактная\s+исследовательская\s+организация)\b[^:]{0,50}[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я\s.,\-]{5,100})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:cro|contract\s+research\s+organization)\b[^:]{0,50}[:#]?\s*([A-Z][A-Za-z\s.,\-]{5,100})", re.IGNORECASE),
        ],
        parser=parse_string_value,
        confidence_policy=confidence_medium,
        priority=150,
    ))
    
    # protocol_meta.study_title
    rules.append(ExtractionRule(
        fact_type="protocol_meta",
        fact_key="study_title",
        patterns_ru=[
            re.compile(r"\b(?:название\s+исследования|заголовок\s+протокола)\b\s*[:#]?\s*(.+)$", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:study\s+title|protocol\s+title)\b\s*[:#]?\s*(.+)$", re.IGNORECASE),
        ],
        parser=parse_string_value,
        confidence_policy=confidence_medium,
        priority=150,
    ))
    
    # ========================================================================
    # Study Design
    # ========================================================================
    
    # study.phase
    rules.append(ExtractionRule(
        fact_type="study",
        fact_key="phase",
        patterns_ru=[
            re.compile(r"\bфаза\s+(?:исследования\s+)?([I1-4IV]+(?:[–\-]?[I1-4IV]+[a-z]?)?)", re.IGNORECASE),
            re.compile(r"\b(?:исследование\s+)?([I1-4IV]+(?:[–\-]?[I1-4IV]+[a-z]?)?)\s+фазы?", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bphase\s+([I1-4IV]+(?:[–\-]?[I1-4IV]+[a-z]?)?)", re.IGNORECASE),
            re.compile(r"\b([I1-4IV]+(?:[–\-]?[I1-4IV]+[a-z]?)?)\s+phase", re.IGNORECASE),
        ],
        parser=parse_string_value,
        confidence_policy=confidence_high,
        priority=200,
        preferred_source_zones=["overview", "design"],
    ))
    
    # study.design.randomized
    rules.append(ExtractionRule(
        fact_type="study",
        fact_key="design.randomized",
        patterns_ru=[
            re.compile(r"\b(?:рандомизированное|рандомизация)", re.IGNORECASE),
            re.compile(r"\b(?:не\s+рандомизированное|без\s+рандомизации)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:randomized|randomization)", re.IGNORECASE),
            re.compile(r"\b(?:non-?randomized|without\s+randomization)", re.IGNORECASE),
        ],
        parser=parse_boolean_value,
        confidence_policy=confidence_high,
        priority=200,
        preferred_source_zones=["design"],
    ))
    
    # study.design.blinding
    rules.append(ExtractionRule(
        fact_type="study",
        fact_key="design.blinding",
        patterns_ru=[
            re.compile(r"\b(?:открытое|open\s*label)", re.IGNORECASE),
            re.compile(r"\b(?:одинарное\s+ослепление|single\s*blind)", re.IGNORECASE),
            re.compile(r"\b(?:двойное\s+ослепление|double\s*blind)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:open\s*label|open\s*label)", re.IGNORECASE),
            re.compile(r"\b(?:single\s*blind|single\s*blind)", re.IGNORECASE),
            re.compile(r"\b(?:double\s*blind|double\s*blind)", re.IGNORECASE),
        ],
        parser=lambda text, m: {"value": _normalize_blinding(text)},
        confidence_policy=confidence_high,
        priority=200,
        preferred_source_zones=["design"],
    ))
    
    # study.design.control_type
    rules.append(ExtractionRule(
        fact_type="study",
        fact_key="design.control_type",
        patterns_ru=[
            re.compile(r"\b(?:плацебо|placebo)", re.IGNORECASE),
            re.compile(r"\b(?:активный\s+контроль|active\s+control)", re.IGNORECASE),
            re.compile(r"\b(?:неконтролируемое|uncontrolled)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bplacebo", re.IGNORECASE),
            re.compile(r"\bactive\s+control", re.IGNORECASE),
            re.compile(r"\buncontrolled", re.IGNORECASE),
        ],
        parser=lambda text, m: {"value": _normalize_control_type(text)},
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["design"],
    ))
    
    # study.design.parallel_or_crossover
    rules.append(ExtractionRule(
        fact_type="study",
        fact_key="design.parallel_or_crossover",
        patterns_ru=[
            re.compile(r"\b(?:параллельное|parallel)", re.IGNORECASE),
            re.compile(r"\b(?:кроссоверное|crossover)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bparallel", re.IGNORECASE),
            re.compile(r"\bcrossover", re.IGNORECASE),
        ],
        parser=lambda text, m: {"value": _normalize_parallel_crossover(text)},
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["design"],
    ))
    
    # study.design.multicenter
    rules.append(ExtractionRule(
        fact_type="study",
        fact_key="design.multicenter",
        patterns_ru=[
            re.compile(r"\b(?:многocentровое|multicenter)", re.IGNORECASE),
            re.compile(r"\b(?:одноцентровое|single\s*center)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bmulticenter", re.IGNORECASE),
            re.compile(r"\bsingle\s*center", re.IGNORECASE),
        ],
        parser=parse_boolean_value,
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["design"],
    ))
    
    # study.design.duration_subject
    rules.append(ExtractionRule(
        fact_type="study",
        fact_key="design.duration_subject",
        patterns_ru=[
            re.compile(r"\b(?:длительность\s+для\s+субъекта|продолжительность\s+лечения)\b[^0-9]{0,30}(\d+\s+(?:недели?|месяц|дн|год))", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:subject\s+duration|treatment\s+duration)\b[^0-9]{0,30}(\d+\s+(?:weeks?|months?|days?|years?))", re.IGNORECASE),
        ],
        parser=parse_duration_value,
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["design"],
    ))
    
    # ========================================================================
    # Population
    # ========================================================================
    
    # population.planned_n_total (уже есть)
    rules.append(ExtractionRule(
        fact_type="population",
        fact_key="planned_n_total",
        patterns_ru=[
            re.compile(r"\b(?:всего\s+n|общее\s+число|планируем(?:ое|ая)\s+число|планируем(?:ый|ая)\s+набор|планируется\s+включить)\b[^0-9]{0,35}(\d{1,7}(?:[ ,]\d{3})*)", re.IGNORECASE),
            re.compile(r"\bN\s*=\s*(\d{1,7}(?:[ ,]\d{3})*)\b", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:total\s*n|planned\s+enrollment|target\s+enrollment|enrollment)\b[^0-9]{0,25}(\d{1,7}(?:[ ,]\d{3})*)", re.IGNORECASE),
            re.compile(r"\bN\s*=\s*(\d{1,7}(?:[ ,]\d{3})*)\b", re.IGNORECASE),
        ],
        parser=parse_int_value,
        confidence_policy=confidence_high,
        priority=200,
    ))
    
    # population.age_min / age_max (обрабатываем отдельно для каждого)
    rules.append(ExtractionRule(
        fact_type="population",
        fact_key="age_min",
        patterns_ru=[
            re.compile(r"\b(?:возраст|age)\b[^0-9]{0,30}(?:от\s+)?(\d+)\s*(?:до|–|-)\s*\d+", re.IGNORECASE),
            re.compile(r"\b(?:возраст|age)\s*[:#]?\s*(\d+)\s*[–\-]\s*\d+", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:age|age\s+range)\b[^0-9]{0,30}(?:from\s+)?(\d+)\s*(?:to|–|-)\s*\d+", re.IGNORECASE),
            re.compile(r"\b(?:age|age\s+range)\s*[:#]?\s*(\d+)\s*[–\-]\s*\d+", re.IGNORECASE),
        ],
        parser=parse_age_min_value,
        confidence_policy=confidence_high,
        priority=200,
    ))

    rules.append(ExtractionRule(
        fact_type="population",
        fact_key="age_max",
        patterns_ru=[
            re.compile(r"\b(?:возраст|age)\b[^0-9]{0,30}(?:от\s+)?\d+\s*(?:до|–|-)\s*(\d+)", re.IGNORECASE),
            re.compile(r"\b(?:возраст|age)\s*[:#]?\s*\d+\s*[–\-]\s*(\d+)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:age|age\s+range)\b[^0-9]{0,30}(?:from\s+)?\d+\s*(?:to|–|-)\s*(\d+)", re.IGNORECASE),
            re.compile(r"\b(?:age|age\s+range)\s*[:#]?\s*\d+\s*[–\-]\s*(\d+)", re.IGNORECASE),
        ],
        parser=parse_age_max_value,
        confidence_policy=confidence_high,
        priority=200,
    ))
    
    # population.sex
    rules.append(ExtractionRule(
        fact_type="population",
        fact_key="sex",
        patterns_ru=[
            re.compile(r"\b(?:пол|sex|gender)\b[^:]{0,30}[:#]?\s*(?:все|all|оба|both)", re.IGNORECASE),
            re.compile(r"\b(?:пол|sex|gender)\b[^:]{0,30}[:#]?\s*(?:мужской|male|мужчины)", re.IGNORECASE),
            re.compile(r"\b(?:пол|sex|gender)\b[^:]{0,30}[:#]?\s*(?:женский|female|женщины)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:sex|gender)\b[^:]{0,30}[:#]?\s*(?:all|both)", re.IGNORECASE),
            re.compile(r"\b(?:sex|gender)\b[^:]{0,30}[:#]?\s*(?:male|men)", re.IGNORECASE),
            re.compile(r"\b(?:sex|gender)\b[^:]{0,30}[:#]?\s*(?:female|women)", re.IGNORECASE),
        ],
        parser=lambda text, m: {"value": _normalize_sex(text)},
        confidence_policy=confidence_medium,
        priority=150,
    ))
    
    # population.condition
    rules.append(ExtractionRule(
        fact_type="population",
        fact_key="condition",
        patterns_ru=[
            re.compile(r"\b(?:заболевание|состояние|диагноз|condition|disease)\b[^:]{0,50}[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я\s.,\-]{5,200})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:condition|disease|diagnosis)\b[^:]{0,50}[:#]?\s*([A-Z][A-Za-z\s.,\-]{5,200})", re.IGNORECASE),
        ],
        parser=parse_string_value,
        confidence_policy=confidence_low,
        priority=100,
    ))
    
    # ========================================================================
    # Treatment
    # ========================================================================
    
    # treatment.randomization_ratio
    rules.append(ExtractionRule(
        fact_type="treatment",
        fact_key="randomization_ratio",
        patterns_ru=[
            re.compile(r"\b(?:соотношение\s+рандомизации|randomization\s+ratio)\b[^:]{0,30}[:#]?\s*(\d+\s*[:\-/]\s*\d+)", re.IGNORECASE),
            re.compile(r"\b(\d+\s*[:\-/]\s*\d+)\s*(?:соотношение|ratio)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:randomization\s+ratio)\b[^:]{0,30}[:#]?\s*(\d+\s*[:\-/]\s*\d+)", re.IGNORECASE),
            re.compile(r"\b(\d+\s*[:\-/]\s*\d+)\s*(?:ratio)", re.IGNORECASE),
        ],
        parser=parse_ratio_value,
        confidence_policy=confidence_high,
        priority=200,
        preferred_source_zones=["ip", "design"],
    ))
    
    # treatment.arm_count
    rules.append(ExtractionRule(
        fact_type="treatment",
        fact_key="arm_count",
        patterns_ru=[
            re.compile(r"\b(?:число\s+групп|количество\s+рукавов|number\s+of\s+arms)\b[^0-9]{0,30}(\d+)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:number\s+of\s+arms|arms?)\b[^0-9]{0,30}(\d+)", re.IGNORECASE),
        ],
        parser=parse_int_value,
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["ip", "design"],
    ))
    
    # treatment.ip_name
    rules.append(ExtractionRule(
        fact_type="treatment",
        fact_key="ip_name",
        patterns_ru=[
            re.compile(r"\b(?:исследуемый\s+препарат|ip|investigational\s+product)\b[^:]{0,50}[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я0-9\s.,\-]{3,200})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:investigational\s+product|ip|study\s+drug)\b[^:]{0,50}[:#]?\s*([A-Z][A-Za-z0-9\s.,\-]{3,200})", re.IGNORECASE),
        ],
        parser=parse_string_value,
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["ip"],
    ))
    
    # treatment.dose
    rules.append(ExtractionRule(
        fact_type="treatment",
        fact_key="dose",
        patterns_ru=[
            re.compile(r"\b(?:доза|dose)\b[^:]{0,30}[:#]?\s*(\d+(?:[.,]\d+)?)\s*([a-zа-я]{1,20})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:dose|dosage)\b[^:]{0,30}[:#]?\s*(\d+(?:[.,]\d+)?)\s*([a-z]{1,20})", re.IGNORECASE),
        ],
        parser=lambda text, m: _parse_dose(text, m),
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["ip"],
    ))
    
    # treatment.route
    rules.append(ExtractionRule(
        fact_type="treatment",
        fact_key="route",
        patterns_ru=[
            re.compile(r"\b(?:путь\s+введения|route)\b[^:]{0,30}[:#]?\s*(?:перорально|po|per\s*os)", re.IGNORECASE),
            re.compile(r"\b(?:путь\s+введения|route)\b[^:]{0,30}[:#]?\s*(?:внутривенно|iv|intravenous)", re.IGNORECASE),
            re.compile(r"\b(?:путь\s+введения|route)\b[^:]{0,30}[:#]?\s*(?:подкожно|sc|subcutaneous)", re.IGNORECASE),
            re.compile(r"\b(?:путь\s+введения|route)\b[^:]{0,30}[:#]?\s*(?:внутримышечно|im|intramuscular)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:route)\b[^:]{0,30}[:#]?\s*(?:po|per\s*os|oral)", re.IGNORECASE),
            re.compile(r"\b(?:route)\b[^:]{0,30}[:#]?\s*(?:iv|intravenous)", re.IGNORECASE),
            re.compile(r"\b(?:route)\b[^:]{0,30}[:#]?\s*(?:sc|subcutaneous)", re.IGNORECASE),
            re.compile(r"\b(?:route)\b[^:]{0,30}[:#]?\s*(?:im|intramuscular)", re.IGNORECASE),
        ],
        parser=lambda text, m: {"value": _normalize_route(text)},
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["ip"],
    ))
    
    # treatment.frequency
    rules.append(ExtractionRule(
        fact_type="treatment",
        fact_key="frequency",
        patterns_ru=[
            re.compile(r"\b(?:частота|frequency)\b[^:]{0,30}[:#]?\s*(?:раз\s+в\s+день|qd|once\s+daily)", re.IGNORECASE),
            re.compile(r"\b(?:частота|frequency)\b[^:]{0,30}[:#]?\s*(?:дважды\s+в\s+день|bid|twice\s+daily)", re.IGNORECASE),
            re.compile(r"\b(?:частота|frequency)\b[^:]{0,30}[:#]?\s*(?:еженедельно|weekly)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:frequency)\b[^:]{0,30}[:#]?\s*(?:qd|once\s+daily)", re.IGNORECASE),
            re.compile(r"\b(?:frequency)\b[^:]{0,30}[:#]?\s*(?:bid|twice\s+daily)", re.IGNORECASE),
            re.compile(r"\b(?:frequency)\b[^:]{0,30}[:#]?\s*(?:weekly)", re.IGNORECASE),
        ],
        parser=lambda text, m: {"value": _normalize_frequency(text)},
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["ip"],
    ))
    
    # ========================================================================
    # Endpoints (специальная обработка для массивов)
    # ========================================================================
    # Endpoints обрабатываются отдельно в основном сервисе, так как требуют
    # извлечения нескольких элементов из списка после заголовка
    
    # ========================================================================
    # Statistics
    # ========================================================================
    
    # statistics.alpha
    rules.append(ExtractionRule(
        fact_type="statistics",
        fact_key="alpha",
        patterns_ru=[
            re.compile(r"\b(?:альфа|alpha|уровень\s+значимости)\b[^0-9]{0,30}(?:[:#]?\s*)?(?:=)?\s*(0\.\d{1,4}|\d+\.\d{1,4})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:alpha|significance\s+level)\b[^0-9]{0,30}(?:[:#]?\s*)?(?:=)?\s*(0\.\d{1,4}|\d+\.\d{1,4})", re.IGNORECASE),
        ],
        parser=parse_float_value,
        confidence_policy=confidence_high,
        priority=200,
        preferred_source_zones=["statistics"],
    ))
    
    # statistics.power
    rules.append(ExtractionRule(
        fact_type="statistics",
        fact_key="power",
        patterns_ru=[
            re.compile(r"\b(?:мощность|power|статистическая\s+мощность)\b[^0-9]{0,30}(?:[:#]?\s*)?(?:=)?\s*(\d{1,3})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:power|statistical\s+power)\b[^0-9]{0,30}(?:[:#]?\s*)?(?:=)?\s*(\d{1,3})", re.IGNORECASE),
        ],
        parser=parse_int_value,
        confidence_policy=confidence_high,
        priority=200,
        preferred_source_zones=["statistics"],
    ))
    
    # statistics.primary_method
    rules.append(ExtractionRule(
        fact_type="statistics",
        fact_key="primary_method",
        patterns_ru=[
            re.compile(r"\b(?:первичный\s+метод\s+анализа|primary\s+analysis\s+method)\b[^:]{0,50}[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я\s.,\-]{5,200})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:primary\s+analysis\s+method|primary\s+method)\b[^:]{0,50}[:#]?\s*([A-Z][A-Za-z\s.,\-]{5,200})", re.IGNORECASE),
        ],
        parser=parse_string_value,
        confidence_policy=confidence_low,
        priority=100,
        preferred_source_zones=["statistics"],
    ))
    
    # statistics.interim_analysis
    rules.append(ExtractionRule(
        fact_type="statistics",
        fact_key="interim_analysis",
        patterns_ru=[
            re.compile(r"\b(?:промежуточный\s+анализ|interim\s+analysis)", re.IGNORECASE),
            re.compile(r"\b(?:без\s+промежуточного\s+анализа|no\s+interim\s+analysis)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:interim\s+analysis)", re.IGNORECASE),
            re.compile(r"\b(?:no\s+interim\s+analysis)", re.IGNORECASE),
        ],
        parser=parse_boolean_value,
        confidence_policy=confidence_medium,
        priority=150,
        preferred_source_zones=["statistics"],
    ))
    
    return rules


# ============================================================================
# Вспомогательные функции нормализации
# ============================================================================


def _normalize_blinding(text: str) -> str:
    """Нормализует тип ослепления."""
    text_lower = text.lower()
    if "open" in text_lower or "открыт" in text_lower:
        return "open-label"
    if "double" in text_lower or "двойн" in text_lower:
        return "double"
    if "single" in text_lower or "одинарн" in text_lower:
        return "single"
    return "unknown"


def _normalize_control_type(text: str) -> str:
    """Нормализует тип контроля."""
    text_lower = text.lower()
    if "placebo" in text_lower or "плацебо" in text_lower:
        return "placebo"
    if "active" in text_lower or "активн" in text_lower:
        return "active"
    if "uncontrolled" in text_lower or "неконтрол" in text_lower:
        return "uncontrolled"
    return "unknown"


def _normalize_parallel_crossover(text: str) -> str:
    """Нормализует тип дизайна (parallel/crossover)."""
    text_lower = text.lower()
    if "crossover" in text_lower or "кроссовер" in text_lower:
        return "crossover"
    if "parallel" in text_lower or "параллельн" in text_lower:
        return "parallel"
    return "unknown"


def _normalize_sex(text: str) -> str:
    """Нормализует пол."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["all", "оба", "both", "все"]):
        return "all"
    if any(kw in text_lower for kw in ["male", "мужск", "мужчин"]):
        return "male"
    if any(kw in text_lower for kw in ["female", "женск", "женщин"]):
        return "female"
    return "unknown"


def _normalize_route(text: str) -> str:
    """Нормализует путь введения."""
    text_lower = text.lower()
    if "po" in text_lower or "per os" in text_lower or "пероральн" in text_lower or "oral" in text_lower:
        return "po"
    if "iv" in text_lower or "intravenous" in text_lower or "внутривенн" in text_lower:
        return "iv"
    if "sc" in text_lower or "subcutaneous" in text_lower or "подкожн" in text_lower:
        return "sc"
    if "im" in text_lower or "intramuscular" in text_lower or "внутримышечн" in text_lower:
        return "im"
    return "unknown"


def _normalize_frequency(text: str) -> str:
    """Нормализует частоту приема."""
    text_lower = text.lower()
    if "qd" in text_lower or "once daily" in text_lower or "раз в день" in text_lower:
        return "qd"
    if "bid" in text_lower or "twice daily" in text_lower or "дважды в день" in text_lower:
        return "bid"
    if "weekly" in text_lower or "еженедел" in text_lower:
        return "weekly"
    return "unknown"


def _parse_dose(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит дозу с единицами измерения."""
    if match.lastindex >= 1:
        value_str = match.group(1)
        unit_str = match.group(2) if match.lastindex >= 2 else None
        value = parse_float(value_str)
        if value is not None:
            return {"value": value, "unit": unit_str.strip() if unit_str else None}
    return None

