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
EXTRACTOR_VERSION = 2


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
    required_topics: list[str] | None = None  # Приоритетные топики (target_section), например ["sample_size_justification", "study_population"]
    preferred_topics: list[str] | None = None  # Предпочтительные мастер-топики для поиска значений


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
    
    # "05 March 2021" / "5 Mar 2021" / "5 марта 2021" / "12 апреля 2010"
    # Используем search вместо match, чтобы находить дату в середине текста
    m = re.search(r"(?P<d>\d{1,2})\s+(?P<mon>[A-Za-zА-Яа-яёЁ]+)\s+(?P<y>\d{4})", s)
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
    val = match.group(1).strip() if match.lastindex is not None and match.lastindex >= 1 else match.group(0).strip()
    if not val:
        return None
    return {"value": normalize_whitespace(val)}


def parse_sponsor_name_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит название спонсора с поддержкой кавычек, исключений и ограничением длины."""
    val = match.group(1).strip() if match.lastindex is not None and match.lastindex >= 1 else match.group(0).strip()
    if not val:
        return None
    
    # Убираем кавычки в начале и конце (поддержка "", "", «»)
    val = re.sub(r'^[""«»]+|[""«»]+$', '', val.strip())
    
    normalized_val = normalize_whitespace(val)
    
    # ВАЛИДАЦИЯ: значение ОБЯЗАТЕЛЬНО должно начинаться с заглавной буквы (А-Я, A-Z) или кавычки
    if normalized_val:
        first_char = normalized_val[0]
        # Если начинается с маленькой буквы (a-z, а-я) - отбрасываем кандидата
        if first_char.islower():
            return None
        # Проверяем, что это либо заглавная буква, либо кавычка
        if not (first_char.isupper() or first_char in '""«»'):
            # Если это не заглавная буква и не кавычка - тоже отбрасываем
            return None
    
    # Список исключений (stop words) для ICH GCP и Хельсинкской декларации
    stop_phrases = [
        "ICH GCP", "ICH-GCP", "ICH E6", "ICH-E6",
        "Good Clinical Practice", "Good clinical practice",
        "Хельсинкская декларация", "Хельсинкской декларации",
        "Helsinki Declaration", "Declaration of Helsinki",
        "clinical trial", "clinical study",
    ]
    
    # Проверяем, содержит ли значение стоп-фразы
    normalized_lower = normalized_val.lower()
    for phrase in stop_phrases:
        if phrase.lower() in normalized_lower:
            return None  # Пропускаем значение со стоп-фразами
    
    # СТОП-СЛОВА: если в тексте есть слова "обязан", "должен", "согласно", "храниться" - это не имя спонсора
    stop_words = ["обязан", "должен", "согласно", "храниться"]
    for stop_word in stop_words:
        if stop_word in normalized_lower:
            return None  # Это описание процедур, а не имя спонсора
    
    # Ограничиваем длину до 80 символов
    if len(normalized_val) > 80:
        normalized_val = normalized_val[:80].rstrip()
    
    if not normalized_val:
        return None
    
    return {"value": normalized_val}


def parse_ip_name_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит название исследуемого препарата с проверкой на общие значения."""
    val = match.group(1).strip() if match.lastindex is not None and match.lastindex >= 1 else match.group(0).strip()
    if not val:
        return None
    
    normalized_val = normalize_whitespace(val)
    
    # ВАЛИДАЦИЯ: значение ОБЯЗАТЕЛЬНО должно начинаться с заглавной буквы (А-Я, A-Z) или кавычки
    if normalized_val:
        first_char = normalized_val[0]
        # Если начинается с маленькой буквы (a-z, а-я) - отбрасываем кандидата
        if first_char.islower():
            return None
        # Проверяем, что это либо заглавная буква, либо кавычка
        if not (first_char.isupper() or first_char in '""«»'):
            # Если это не заглавная буква и не кавычка - тоже отбрасываем
            return None
    
    # Проверяем на общие значения, которые требуют проверки
    generic_values = ["ИП", "ЛП", "Исследуемый препарат", "препарат", "IP", "Investigational Product", "product", "ЛС"]
    if normalized_val in generic_values or normalized_val.lower() in [gv.lower() for gv in generic_values]:
        # Возвращаем None, чтобы не засорять Study KB мусором
        return None
    
    return {"value": normalized_val}


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
    """Парсит дату и возвращает ISO формат YYYY-MM-DD для корректного сравнения."""
    raw = match.group(1) if match.lastindex >= 1 else match.group(0)
    iso = parse_date_to_iso(raw)
    if iso is None:
        return None
    # Возвращаем только ISO формат в value для корректного сравнения
    # (без raw, чтобы избежать конфликтов при сравнении)
    return {"value": iso}


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


def parse_ratio_list_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит список соотношений из текста (для сложных протоколов с несколькими когортами)."""
    # Извлекаем все соотношения из текста
    ratios = []
    raw_values = []
    
    # Паттерны для поиска всех соотношений в тексте
    ratio_patterns = [
        r"(\d+)\s*[:\/]\s*(\d+)",  # 2:1, 2/1
        r"(\d+)\s+к\s+(\d+)",  # 2 к 1
        r"(\d+)\s+to\s+(\d+)",  # 2 to 1
    ]
    
    for pattern in ratio_patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            num1 = parse_int(m.group(1))
            num2 = parse_int(m.group(2))
            if num1 is not None and num2 is not None and num2 > 0:
                ratio_str = f"{num1}:{num2}"
                if ratio_str not in ratios:  # Избегаем дубликатов
                    ratios.append(ratio_str)
                    raw_values.append(m.group(0))
    
    if not ratios:
        return None
    
    # Если найдено одно соотношение, возвращаем как раньше (для обратной совместимости)
    if len(ratios) == 1:
        return {"value": ratios[0], "raw": raw_values[0]}
    
    # Если найдено несколько соотношений, возвращаем список
    # LLM-нормализатор выберет главное (первое или наиболее часто упоминаемое)
    return {"value": ratios, "raw": ", ".join(raw_values), "all_ratios": ratios}


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
    rules: list[ExtractionRule] = []
    
    # --- МЕТАДАННЫЕ ПРОТОКОЛА ---
    rules.append(ExtractionRule(
        fact_type="protocol_meta", fact_key="protocol_version",
        patterns_ru=[
            # Основные варианты с разными падежами
            re.compile(r"\b(?:версия|версии|версией|версию)\s+протокола\b\s*[:#]?\s*([A-Za-z0-9А-Яа-я._/\-]{1,64})", re.IGNORECASE),
            re.compile(r"\b(?:редакция|редакции|редакцией|редакцию)\s+протокола\b\s*[:#]?\s*([A-Za-z0-9А-Яа-я._/\-]{1,64})", re.IGNORECASE),
            re.compile(r"\b(?:номер|номера|номером)\s+протокола\b\s*[:#]?\s*([A-Za-z0-9А-Яа-я._/\-]{1,64})", re.IGNORECASE),
            re.compile(r"\bпротокол\s+(?:версия|версии|версией|версию|номер|номера|номером|ред\.|редакция|редакции|редакцией|редакцию|издание|издания|изданием|поправка|поправки|поправкой)\s*[:#]?\s*([A-Za-z0-9А-Яа-я._/\-]{1,64})", re.IGNORECASE),
            re.compile(r"\b(?:ред\.|издание|издания|изданием|поправка|поправки|поправкой)\s+протокола\b\s*[:#]?\s*([A-Za-z0-9А-Яа-я._/\-]{1,64})", re.IGNORECASE),
            # Поддержка слова "Издание" отдельно
            re.compile(r"(?i)издание\s*[:#]?\s*([A-Za-z0-9._\-]{1,20})", re.IGNORECASE),
        ],
        patterns_en=[re.compile(r"\bprotocol\s*(?:version|no\.?|number|edition|ver\.|amendment)\b\s*[:#]?\s*([A-Za-z0-9._/\-]{1,64})", re.IGNORECASE)],
        parser=parse_string_value, confidence_policy=confidence_high, priority=250,
        preferred_topics=['admin_ethics', 'overview_objectives']
    ))

    rules.append(ExtractionRule(
        fact_type="protocol_meta", fact_key="protocol_date",
        patterns_ru=[
            # Сначала более специфичные паттерны с ограниченной длиной
            re.compile(r"\bот\s+(\d{1,2}\.\d{1,2}\.\d{4})\b", re.IGNORECASE),  # "от 10.06.2024"
            re.compile(r"\bдата\s*[:#]?\s*(\d{1,2}\.\d{1,2}\.\d{4})\b", re.IGNORECASE),  # "Дата: 10.06.2024"
            re.compile(r"\b(?:от|дата)\s+(\d{1,2}\s+(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4})\b", re.IGNORECASE),  # "от 10 июня 2024" или "12 апреля 2010"
            # Более общий паттерн, но с ограничением длины (максимум 50 символов)
            re.compile(r"\b(?:дата|даты|датой)\s+(?:протокола|редакции|утверждения)\b\s*[:#]?\s*([^.\n]{1,50}?)(?:\.|$|\n)", re.IGNORECASE),
        ],
        patterns_en=[
            # Сначала более специфичные паттерны
            re.compile(r"\b(?:protocol|release|issue)\s+date\s*[:#]?\s*(\d{1,2}[./]\d{1,2}[./]\d{4})\b", re.IGNORECASE),  # "Protocol date: 10/06/2024"
            re.compile(r"\b(?:protocol|release|issue)\s+date\s*[:#]?\s*(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b", re.IGNORECASE),  # "Protocol date: 12 April 2010"
            # Более общий паттерн с ограничением длины
            re.compile(r"\b(?:protocol|release|issue)\s+date\b\s*[:#]?\s*([^.\n]{1,50}?)(?:\.|$|\n)", re.IGNORECASE),
        ],
        parser=parse_date_value, confidence_policy=confidence_high, priority=200,
        preferred_topics=['admin_ethics', 'overview_objectives']
    ))

    rules.append(ExtractionRule(
        fact_type="protocol_meta", fact_key="sponsor_name",
        patterns_ru=[
            # Паттерн с организационными формами: ООО "...", АО «...» (ограничен до 4-5 слов или до первой запятой/точки, нежадный)
            re.compile(r"(?i)спонсор\s*[:\-]?\s*(?:ООО|АО|ПАО|ЗАО|ОАО)\s*([A-ZА-Я][A-Za-zА-Яа-я0-9\s.\-]{2,40}?)(?:[,.]|$)", re.IGNORECASE),
            # Стандартный паттерн: ограничен до 4-5 слов или до первой запятой/точки (нежадный захват)
            re.compile(r"(?i)спонсор\s*[:\-]?\s*([A-ZА-Я][A-Za-zА-Яа-я0-9\s.\-]{2,40}?)(?:[,.]|$)", re.IGNORECASE),
        ],
        patterns_en=[
            # Паттерн с организационными формами (ограничен до 4-5 слов или до первой запятой/точки, нежадный)
            re.compile(r"(?i)sponsor\s*[:\-]?\s*(?:LLC|Inc\.?|Ltd\.?|Corp\.?|Corporation)\s*([A-Z][A-Za-z0-9\s.\-]{2,40}?)(?:[,.]|$)", re.IGNORECASE),
            # Стандартный паттерн с ограничением (нежадный захват)
            re.compile(r"(?i)sponsor\s*[:\-]?\s*([A-Z][A-Za-z0-9\s.\-]{2,40}?)(?:[,.]|$)", re.IGNORECASE),
        ],
        parser=parse_sponsor_name_value, confidence_policy=confidence_medium, priority=150,
        preferred_topics=['admin_ethics']
    ))

    # --- ДИЗАЙН ИССЛЕДОВАНИЯ ---
    rules.append(ExtractionRule(
        fact_type="study", fact_key="phase",
        patterns_ru=[
            re.compile(r"\bфаза\s+(?:исследования\s+)?([I1-4IV]+(?:[–\-]?[I1-4IV]+)?)\b", re.IGNORECASE),
            re.compile(r"\b([I1-4IV]+(?:[–\-]?[I1-4IV]+)?)\s+фаза\b", re.IGNORECASE),
            re.compile(r"\b(?:первой|второй|третьей|четвертой)\s+фазы\b", re.IGNORECASE)
        ],
        patterns_en=[re.compile(r"\bphase\s+([I1-4IV]+(?:[–\-]?[I1-4IV]+)?)\b", re.IGNORECASE)],
        parser=parse_string_value, confidence_policy=confidence_high, priority=200,
        preferred_topics=['overview_objectives', 'design_plan']
    ))

    rules.append(ExtractionRule(
        fact_type="study", fact_key="design.randomized",
        patterns_ru=[re.compile(r"\b(?:рандомизированное|рандомизация|с\s+рандомизацией)\b", re.IGNORECASE)],
        patterns_en=[re.compile(r"\b(?:randomized|randomization)\b", re.IGNORECASE)],
        parser=parse_boolean_value, confidence_policy=confidence_high, priority=200,
        preferred_topics=['design_plan', 'overview_objectives']
    ))

    # --- ПОПУЛЯЦИЯ И РАЗМЕР ВЫБОРКИ ---
    rules.append(ExtractionRule(
        fact_type="population", fact_key="planned_n_total",
        patterns_ru=[
            # Базовые паттерны с разными падежами
            re.compile(r"\b(?:всего\s+n|общее\s+число|величина\s+выборки|объем\s+выборки|набор\s+составит)\b[^0-9]{0,40}(\d{1,7}(?:[ ,]\d{3})*)", re.IGNORECASE),
            # Новые паттерны с разными вариантами написания
            re.compile(r"\b(?:количество\s+субъектов|число\s+субъектов|всего\s+субъектов)\b[^0-9]{0,40}(\d{1,7}(?:[ ,]\d{3})*)", re.IGNORECASE),
            re.compile(r"\b(?:количество\s+пациентов|число\s+пациентов|всего\s+пациентов)\b[^0-9]{0,40}(\d{1,7}(?:[ ,]\d{3})*)", re.IGNORECASE),
            re.compile(r"\b(?:планируется\s+включить|будет\s+включено|предполагается\s+включить)\b[^0-9]{0,40}(\d{1,7}(?:[ ,]\d{3})*)", re.IGNORECASE),
            re.compile(r"\bвсего\s+(\d{1,7}(?:[ ,]\d{3})*)\s+(?:добровольцев|пациентов|субъектов|участников)\b", re.IGNORECASE),
            # Простые паттерны для N
            re.compile(r"\bN\s*=\s*(\d{1,7})\b", re.IGNORECASE),
            re.compile(r"\b(?:всего|общее\s+количество|количество)\s+(?:пациентов|субъектов|участников)\b[^0-9]{0,40}(\d{1,7}(?:[ ,]\d{3})*)", re.IGNORECASE),
        ],
        patterns_en=[re.compile(r"\b(?:total\s+n|sample\s+size|planned\s+enrollment|target\s+number|number\s+of\s+patients|total\s+subjects)\b[^0-9]{0,40}(\d{1,7}(?:[ ,]\d{3})*)", re.IGNORECASE)],
        parser=parse_int_value, confidence_policy=confidence_high, priority=300,
        preferred_topics=['stats_sample_size', 'population_eligibility', 'overview_objectives']  # Уже включает overview_objectives для гибкости MVP
    ))

    rules.append(ExtractionRule(
        fact_type="population", fact_key="age_min",
        patterns_ru=[
            re.compile(r"\b(?:возраст|age)\b[^0-9]{0,30}(?:от\s+)?(\d+)\s*(?:до|–|-)\s*\d+", re.IGNORECASE),
            re.compile(r"\bсовершеннолетн(?:ие|ые)\b", re.IGNORECASE) # Parser должен вернуть 18
        ],
        patterns_en=[re.compile(r"\b(?:age|age\s+range)\b[^0-9]{0,30}(?:from\s+)?(\d+)\s*(?:to|–|-)\s*\d+", re.IGNORECASE)],
        parser=lambda t, m: {"value": 18} if "совершеннолетн" in t.lower() else parse_age_min_value(t, m),
        confidence_policy=confidence_high, priority=200,
        preferred_topics=['population_eligibility']
    ))

    # --- ПРЕПАРАТЫ (IP & COMPARATOR) ---
    rules.append(ExtractionRule(
        fact_type="treatment", fact_key="ip_name",
        patterns_ru=[
            # Ограниченный захват до 4-5 слов или до первой запятой/точки (нежадный), если это не заголовок
            re.compile(r"\b(?:исследуемый\s+препарат|ип|investigational\s+product)\b[^:]{0,50}[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я0-9\s.\-]{2,50}?)(?:[,.]|$)", re.IGNORECASE),
        ],
        patterns_en=[
            # Ограниченный захват до 4-5 слов или до первой запятой/точки (нежадный)
            re.compile(r"\b(?:investigational\s+product|ip|study\s+drug)\b[^:]{0,50}[:#]?\s*([A-Z][A-Za-z0-9\s.\-]{2,50}?)(?:[,.]|$)", re.IGNORECASE),
        ],
        parser=parse_ip_name_value, confidence_policy=confidence_medium, priority=200,
        preferred_topics=['ip_management']
    ))

    rules.append(ExtractionRule(
        fact_type="treatment", fact_key="comparator_name",
        patterns_ru=[re.compile(r"\b(?:препарат\s+сравнения|активный\s+контроль|плацебо-контроль)\b\s*[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я0-9\s.,\-]{3,100})", re.IGNORECASE)],
        patterns_en=[re.compile(r"\b(?:comparator|active\s+control|reference\s+drug|placebo)\b\s*[:#]?\s*([A-Z][A-Za-z0-9\s.,\-]{3,100})", re.IGNORECASE)],
        parser=parse_string_value, confidence_policy=confidence_medium, priority=180,
        preferred_topics=['ip_management', 'design_plan']
    ))

    # Дозировка (Dosage)
    rules.append(ExtractionRule(
        fact_type="treatment", fact_key="dosage",
        patterns_ru=[
            re.compile(r"\bв\s+дозе\s+(\d+(?:[.,]\d+)?)\s*(мг|мкг|мл|г|кг)\b", re.IGNORECASE),
            re.compile(r"\bдоза\s*[:#]?\s*(\d+(?:[.,]\d+)?)\s*(мг|мкг|мл|г|кг)\b", re.IGNORECASE),
            re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(мг|мкг|мл|г|кг)\s+(?:в\s+дозе|дозировка)\b", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bdose\s*[:#]?\s*(\d+(?:[.,]\d+)?)\s*(mg|mcg|ml|g|kg)\b", re.IGNORECASE),
            re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(mg|mcg|ml|g|kg)\s+(?:dose|dosage)\b", re.IGNORECASE),
        ],
        parser=_parse_dose,
        confidence_policy=confidence_medium, priority=190,
        preferred_topics=['ip_management'],
        preferred_source_zones=['ip']
    ))

    # Путь введения (Route)
    rules.append(ExtractionRule(
        fact_type="treatment", fact_key="route",
        patterns_ru=[
            re.compile(r"\b(?:перорально|внутривенно|подкожно|внутримышечно|пероральный|внутривенный|подкожный|внутримышечный)\b", re.IGNORECASE),
            re.compile(r"\b(?:путь\s+введения|способ\s+введения)\b[^:]{0,50}[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я\s.,\-]{3,60})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:oral|intravenous|subcutaneous|intramuscular|po|iv|sc|im)\b", re.IGNORECASE),
            re.compile(r"\b(?:route\s+of\s+administration|administration\s+route)\b[^:]{0,50}[:#]?\s*([A-Z][A-Za-z\s.,\-]{3,60})", re.IGNORECASE),
        ],
        parser=lambda t, m: {"value": _normalize_route(m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0))},
        confidence_policy=confidence_medium, priority=190,
        preferred_topics=['ip_management'],
        preferred_source_zones=['ip']
    ))

    # Частота приема (Frequency)
    rules.append(ExtractionRule(
        fact_type="treatment", fact_key="frequency",
        patterns_ru=[
            re.compile(r"\b(?:раз\s+в\s+день|дважды\s+в\s+день|ежедневно|еженедельно|qd|bid)\b", re.IGNORECASE),
            re.compile(r"\b(?:частота\s+приема|режим\s+приема)\b[^:]{0,50}[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я\s.,\-]{3,60})", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:once\s+daily|twice\s+daily|daily|weekly|qd|bid)\b", re.IGNORECASE),
            re.compile(r"\b(?:frequency|dosing\s+frequency)\b[^:]{0,50}[:#]?\s*([A-Z][A-Za-z\s.,\-]{3,60})", re.IGNORECASE),
        ],
        parser=lambda t, m: {"value": _normalize_frequency(m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0))},
        confidence_policy=confidence_medium, priority=190,
        preferred_topics=['ip_management'],
        preferred_source_zones=['ip']
    ))

    # --- СТАТИСТИКА ---
    rules.append(ExtractionRule(
        fact_type="statistics", fact_key="alpha",
        patterns_ru=[re.compile(r"\b(?:альфа|alpha|уровень\s+значимости)\b[^0-9]{0,30}(?:[:#]?\s*)?(?:=)?\s*(0\.\d{1,4})", re.IGNORECASE)],
        patterns_en=[re.compile(r"\b(?:alpha|significance\s+level)\b[^0-9]{0,30}(?:[:#]?\s*)?(?:=)?\s*(0\.\d{1,4})", re.IGNORECASE)],
        parser=parse_float_value, confidence_policy=confidence_high, priority=200,
        preferred_topics=['stats_sample_size']
    ))

    # 1. Конечные точки (Endpoints)
    rules.append(ExtractionRule(
        fact_type="endpoints", fact_key="primary",
        patterns_ru=[re.compile(r"\b(?:первичная|основная)\s+(?:конечная\s+точка|цель|переменная)\b\s*[:#]?\s*([A-ZА-Я][A-Za-zА-Яа-я0-9\s.,\-]{10,500})", re.IGNORECASE)],
        patterns_en=[re.compile(r"\bprimary\s+(?:endpoint|objective)\b\s*[:#]?\s*([A-Z][A-Za-z0-9\s.,\-]{10,500})", re.IGNORECASE)],
        parser=parse_string_value, confidence_policy=confidence_medium, priority=200,
        preferred_topics=['endpoints_efficacy'],
        preferred_source_zones=['endpoints']
    ))

    # 2. Тип ослепления (Blinding)
    rules.append(ExtractionRule(
        fact_type="study", fact_key="design.blinding",
        patterns_ru=[re.compile(r"\b(?:открытое|слепое|двойное\s+слепое|заслепленное|ослепленное|плацебо-контролируемое)\b", re.IGNORECASE)],
        patterns_en=[re.compile(r"\b(?:open-label|double-blind|blinded|placebo-controlled)\b", re.IGNORECASE)],
        parser=lambda t, m: {"value": _normalize_blinding(t)}, 
        confidence_policy=confidence_high, priority=180,
        preferred_topics=['design_plan'],
        preferred_source_zones=['design']
    ))

    # 3. Тип дизайна (Type: parallel/crossover)
    rules.append(ExtractionRule(
        fact_type="study", fact_key="design.type",
        patterns_ru=[
            re.compile(r"\b(?:параллельных\s+группах|параллельное|параллельный\s+дизайн)\b", re.IGNORECASE),
            re.compile(r"\b(?:перекрестное|кроссовер|перекрестный\s+дизайн)\b", re.IGNORECASE)
        ],
        patterns_en=[
            re.compile(r"\b(?:parallel|parallel\s+group)\b", re.IGNORECASE),
            re.compile(r"\b(?:crossover|cross-over)\b", re.IGNORECASE)
        ],
        parser=lambda t, m: {"value": _normalize_parallel_crossover(t)},
        confidence_policy=confidence_high, priority=180,
        preferred_topics=['design_plan'],
        preferred_source_zones=['design']
    ))

    # 4. Соотношение рандомизации (Randomization Ratio)
    # Используем parse_ratio_list_value для поддержки нескольких соотношений в сложных протоколах
    rules.append(ExtractionRule(
        fact_type="study", fact_key="design.randomization_ratio",
        patterns_ru=[
            re.compile(r"\b(?:соотношении|соотношение|рандомизированы)\b[^0-9]{0,30}(\d+\s*[:\/]\s*\d+)", re.IGNORECASE),
            re.compile(r"\b(?:соотношении|соотношение|рандомизированы)\b[^0-9]{0,30}(\d+\s+к\s+\d+)", re.IGNORECASE),
            re.compile(r"\b(\d+\s*[:\/]\s*\d+)\s*(?:соотношение|ratio)\b", re.IGNORECASE),
            re.compile(r"\b(\d+\s*:\s*\d+)\b", re.IGNORECASE),  # Просто 1:1, 2:1 и т.д.
        ],
        patterns_en=[
            re.compile(r"\b(?:ratio|randomization\s+ratio)\b[^0-9]{0,30}(\d+\s*[:\/]\s*\d+)", re.IGNORECASE),
            re.compile(r"\b(\d+\s*[:\/]\s*\d+)\s*(?:ratio|randomization)\b", re.IGNORECASE),
            re.compile(r"\b(\d+\s*:\s*\d+)\b", re.IGNORECASE),  # Просто 1:1, 2:1 etc.
        ],
        parser=parse_ratio_list_value,  # Используем парсер, который извлекает все соотношения
        confidence_policy=confidence_medium, priority=170,
        preferred_topics=['design_plan'],
        preferred_source_zones=['design']
    ))

    # 5. Длительность исследования
    rules.append(ExtractionRule(
        fact_type="study", fact_key="duration",
        patterns_ru=[re.compile(r"\bпродолжительность\s+исследования\b[^0-9]{0,30}(\d+\s+(?:недель|месяцев|дней|лет))", re.IGNORECASE)],
        patterns_en=[re.compile(r"\bstudy\s+duration\b[^0-9]{0,30}(\d+\s+(?:weeks|months|days|years))", re.IGNORECASE)],
        parser=parse_duration_value, confidence_policy=confidence_high, priority=150,
        preferred_topics=['design_plan']
    ))

    # ============================================================================
    # РАСШИРЕННЫЕ ПРАВИЛА ДЛЯ ПОЛНОФУНКЦИОНАЛЬНОГО ИЗВЛЕЧЕНИЯ (EXTRACTOR_VERSION 2)
    # ============================================================================

    # --- PROTOCOL METADATA (расширение) ---
    # Protocol ID
    rules.append(ExtractionRule(
        fact_type="protocol_meta", fact_key="protocol_id",
        patterns_ru=[
            re.compile(r"\b(?:протокол|protocol|study)\s*(?:no\.?|number|id|код|номер)\s*[:#]?\s*(?P<value>[A-Za-z0-9А-Яа-я._/\-]{1,64})", re.IGNORECASE),
            re.compile(r"\b(?:study\s+id|код\s+исследования)\s*[:#]?\s*(?P<value>[A-Za-z0-9А-Яа-я._/\-]{1,64})", re.IGNORECASE),
            re.compile(r"\b(?P<value>[A-Z]{2,10}[–\-]?\d{3,10}(?:[–\-]?[A-Z0-9]+)?)\s*(?:protocol|протокол)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bprotocol\s*(?:no\.?|number|id)\s*[:#]?\s*(?P<value>[A-Za-z0-9._/\-]{1,64})", re.IGNORECASE),
            re.compile(r"\bstudy\s+id\s*[:#]?\s*(?P<value>[A-Za-z0-9._/\-]{1,64})", re.IGNORECASE),
            re.compile(r"\b(?P<value>[A-Z]{2,10}[–\-]?\d{3,10}(?:[–\-]?[A-Z0-9]+)?)\s*protocol", re.IGNORECASE),
        ],
        parser=parse_protocol_id_value, confidence_policy=confidence_high, priority=240,
        preferred_topics=['admin_ethics', 'overview_objectives']
    ))

    # Phase (расширение существующего правила)
    rules.append(ExtractionRule(
        fact_type="protocol_meta", fact_key="phase",
        patterns_ru=[
            re.compile(r"\b(?:фаза|phase)\s+[:\-]?\s*(?P<value>[I1-4IV]+(?:[–\-/]?[I1-4IV]+)?)\b", re.IGNORECASE),
            re.compile(r"\b(?P<value>[I1-4IV]+(?:[–\-/]?[I1-4IV]+)?)\s+(?:фаза|phase)\s+(?:study|исследование)", re.IGNORECASE),
            re.compile(r"\b(?:первой|второй|третьей|четвертой)\s+фазы", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bphase\s+[:\-]?\s*(?P<value>[I1-4IV]+(?:[–\-/]?[I1-4IV]+)?)\b", re.IGNORECASE),
            re.compile(r"\b(?P<value>[I1-4IV]+(?:[–\-/]?[I1-4IV]+)?)\s+phase\s+study", re.IGNORECASE),
        ],
        parser=lambda t, m: {"value": _normalize_phase(t)}, confidence_policy=confidence_high, priority=210,
        preferred_topics=['overview_objectives', 'design_plan']
    ))

    # Therapeutic Area
    rules.append(ExtractionRule(
        fact_type="protocol_meta", fact_key="therapeutic_area",
        patterns_ru=[
            re.compile(r"\b(?:показание|indication|терапевтическая\s+область|therapeutic\s+area)\s*[:#]?\s*(?P<value>[A-ZА-Я][A-Za-zА-Яа-я0-9\s.,\-]{5,200}?)(?:\.|$|\n)", re.IGNORECASE),
            re.compile(r"\bдля\s+лечения\s+(?P<value>[A-ZА-Я][A-Za-zА-Яа-я0-9\s.,\-]{5,200}?)(?:\.|$|\n)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:indication|therapeutic\s+area)\s*[:#]?\s*(?P<value>[A-Z][A-Za-z0-9\s.,\-]{5,200}?)(?:\.|$|\n)", re.IGNORECASE),
            re.compile(r"\bfor\s+the\s+treatment\s+of\s+(?P<value>[A-Z][A-Za-z0-9\s.,\-]{5,200}?)(?:\.|$|\n)", re.IGNORECASE),
        ],
        parser=parse_therapeutic_area_value, confidence_policy=confidence_medium, priority=180,
        preferred_topics=['overview_objectives', 'indication']
    ))

    # --- STUDY DESIGN (расширение) ---
    # Design Configuration
    rules.append(ExtractionRule(
        fact_type="study", fact_key="design.configuration",
        patterns_ru=[
            re.compile(r"\b(?:параллельн|parallel)\s+(?:групп|group|дизайн|design)", re.IGNORECASE),
            re.compile(r"\b(?:перекрестн|crossover|cross-over)\s+(?:дизайн|design)", re.IGNORECASE),
            re.compile(r"\b(?:факторн|factorial)\s+(?:дизайн|design)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bparallel\s+(?:group|design)\b", re.IGNORECASE),
            re.compile(r"\b(?:crossover|cross-over)\s+design\b", re.IGNORECASE),
            re.compile(r"\bfactorial\s+design\b", re.IGNORECASE),
            re.compile(r"\bsequential\s+design\b", re.IGNORECASE),
        ],
        parser=lambda t, m: {"value": _normalize_design_configuration(t)}, confidence_policy=confidence_high, priority=190,
        preferred_topics=['design_plan']
    ))

    # Masking (расширение существующего правила)
    # Уже есть, но добавим дополнительные паттерны
    rules.append(ExtractionRule(
        fact_type="study", fact_key="design.masking",
        patterns_ru=[
            re.compile(r"\b(?:открытое|open-label|open\s+label)\s+(?:исследование|study)", re.IGNORECASE),
            re.compile(r"\b(?:двойное\s+слепое|double-blind|double\s+blind)\s+(?:исследование|study)", re.IGNORECASE),
            re.compile(r"\b(?:одинарное\s+слепое|single-blind|single\s+blind)\s+(?:исследование|study)", re.IGNORECASE),
            re.compile(r"\b(?:заслепленн|ослепленн|blinded)\s+(?:исследование|study)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:open-label|open\s+label)\s+study\b", re.IGNORECASE),
            re.compile(r"\b(?:double-blind|double\s+blind)\s+study\b", re.IGNORECASE),
            re.compile(r"\b(?:single-blind|single\s+blind)\s+study\b", re.IGNORECASE),
            re.compile(r"\b(?:triple-blind|triple\s+blind)\s+study\b", re.IGNORECASE),
        ],
        parser=lambda t, m: {"value": _normalize_masking(t)}, confidence_policy=confidence_high, priority=185,
        preferred_topics=['design_plan']
    ))

    # Control Type
    rules.append(ExtractionRule(
        fact_type="study", fact_key="design.control_type",
        patterns_ru=[
            re.compile(r"\b(?:плацебо-контролируем|placebo-controlled|placebo\s+control)\b", re.IGNORECASE),
            re.compile(r"\b(?:активный\s+контроль|active-controlled|active\s+control)\b", re.IGNORECASE),
            re.compile(r"\b(?:без\s+контроля|uncontrolled|no\s+control)\b", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bplacebo-controlled\b", re.IGNORECASE),
            re.compile(r"\bactive-controlled\b", re.IGNORECASE),
            re.compile(r"\buncontrolled\b", re.IGNORECASE),
            re.compile(r"\bno\s+control\b", re.IGNORECASE),
        ],
        parser=lambda t, m: {"value": _normalize_control_type(t)}, confidence_policy=confidence_high, priority=175,
        preferred_topics=['design_plan']
    ))

    # --- POPULATION (расширение) ---
    # Age Range (минимальный и максимальный возраст уже есть, добавим общий диапазон)
    rules.append(ExtractionRule(
        fact_type="population", fact_key="age_range",
        patterns_ru=[
            re.compile(r"\b(?:возраст|age)\s*[:\-]?\s*(?:от\s+)?(?P<min>\d+)\s*(?:до|–|-|to)\s*(?P<max>\d+)\s*(?:лет|years?)\b", re.IGNORECASE),
            re.compile(r"\b(?:возраст|age)\s+(?P<min>\d+)\s*[–\-]\s*(?P<max>\d+)\s*(?:лет|years?)\b", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:age|age\s+range)\s*[:\-]?\s*(?:from\s+)?(?P<min>\d+)\s*(?:to|–|-)\s*(?P<max>\d+)\s*years?\b", re.IGNORECASE),
            re.compile(r"\b(?:age|age\s+range)\s+(?P<min>\d+)\s*[–\-]\s*(?P<max>\d+)\s*years?\b", re.IGNORECASE),
        ],
        parser=parse_age_range_value, confidence_policy=confidence_high, priority=210,
        preferred_topics=['population_eligibility']
    ))

    # Gender
    rules.append(ExtractionRule(
        fact_type="population", fact_key="gender",
        patterns_ru=[
            re.compile(r"\b(?:пол|gender|sex)\s*[:\-]?\s*(?P<value>мужской|женский|оба|все|любой|male|female|both|all)", re.IGNORECASE),
            re.compile(r"\b(?:мужчин|male|мужской)\s+(?:и\s+)?(?:женщин|female|женский)", re.IGNORECASE),
            re.compile(r"\b(?:здоровые\s+мужские|healthy\s+male)\s+(?:субъекты|subjects)", re.IGNORECASE),
            re.compile(r"\b(?:здоровые\s+женские|healthy\s+female)\s+(?:субъекты|subjects)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:gender|sex)\s*[:\-]?\s*(?P<value>male|female|both|all)", re.IGNORECASE),
            re.compile(r"\b(?:male\s+and\s+female|both\s+genders|all\s+genders)\b", re.IGNORECASE),
            re.compile(r"\bhealthy\s+male\s+subjects\b", re.IGNORECASE),
            re.compile(r"\bhealthy\s+female\s+subjects\b", re.IGNORECASE),
        ],
        parser=lambda t, m: {"value": _normalize_gender(m.group("value") if "value" in m.groupdict() and m.group("value") else t)}, 
        confidence_policy=confidence_medium, priority=170,
        preferred_topics=['population_eligibility']
    ))

    # Population Type (Healthy Volunteers vs Patients)
    rules.append(ExtractionRule(
        fact_type="population", fact_key="population_type",
        patterns_ru=[
            re.compile(r"\b(?:здоровые\s+добровольцы|healthy\s+volunteers|здоровые\s+субъекты|healthy\s+subjects)\b", re.IGNORECASE),
            re.compile(r"\b(?:пациенты|patients|больные)\s+с\s+", re.IGNORECASE),
            re.compile(r"\b(?:субъекты|subjects)\s+с\s+(?:диабетом|диагнозом)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bhealthy\s+(?:volunteers|subjects)\b", re.IGNORECASE),
            re.compile(r"\bpatients\s+with\b", re.IGNORECASE),
            re.compile(r"\bsubjects\s+with\b", re.IGNORECASE),
        ],
        parser=lambda t, m: {"value": _normalize_population_type(t)}, confidence_policy=confidence_medium, priority=160,
        preferred_topics=['population_eligibility']
    ))

    # --- INTERVENTION / TREATMENT (расширение) ---
    # IMP Name (расширение существующего ip_name)
    # Уже есть правило для ip_name, но добавим дополнительные паттерны
    rules.append(ExtractionRule(
        fact_type="treatment", fact_key="imp_name",
        patterns_ru=[
            re.compile(r"\b(?:исследуемый\s+препарат|test\s+product|investigational\s+product|ип)\s*[:#]?\s*(?P<value>[A-ZА-Я][A-Za-zА-Яа-я0-9\s.\-]{2,50}?)(?:[,.]|$)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:investigational\s+product|test\s+product|ip|study\s+drug)\s*[:#]?\s*(?P<value>[A-Z][A-Za-z0-9\s.\-]{2,50}?)(?:[,.]|$)", re.IGNORECASE),
        ],
        parser=parse_ip_name_value, confidence_policy=confidence_medium, priority=195,
        preferred_topics=['ip_management']
    ))

    # Treatment Duration
    rules.append(ExtractionRule(
        fact_type="treatment", fact_key="treatment_duration",
        patterns_ru=[
            re.compile(r"\b(?:длительность\s+лечения|treatment\s+duration|duration\s+of\s+treatment)\b[^0-9]{0,30}(?P<value>\d+\s+(?:недель|месяцев|дней|лет|weeks?|months?|days?|years?))", re.IGNORECASE),
            re.compile(r"\b(?:лечение|treatment)\s+(?:в\s+течение|for|в\s+течение)\s+(?P<value>\d+\s+(?:недель|месяцев|дней|лет|weeks?|months?|days?|years?))", re.IGNORECASE),
            re.compile(r"\b(?:прием|administration)\s+(?:в\s+течение|for)\s+(?P<value>\d+\s+(?:недель|месяцев|дней|лет|weeks?|months?|days?|years?))", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:treatment\s+duration|duration\s+of\s+treatment)\b[^0-9]{0,30}(?P<value>\d+\s+(?:weeks?|months?|days?|years?))", re.IGNORECASE),
            re.compile(r"\b(?:treatment|administration)\s+for\s+(?P<value>\d+\s+(?:weeks?|months?|days?|years?))", re.IGNORECASE),
            re.compile(r"\bfor\s+(?P<value>\d+\s+(?:weeks?|months?|days?|years?))\s+(?:treatment|administration)", re.IGNORECASE),
        ],
        parser=parse_duration_value, confidence_policy=confidence_high, priority=185,
        preferred_topics=['ip_management']
    ))

    # --- BIOEQUIVALENCE SPECIFIC ---
    # Washout Period
    rules.append(ExtractionRule(
        fact_type="bioequivalence", fact_key="washout_period",
        patterns_ru=[
            re.compile(r"\b(?:период\s+отмывки|washout\s+period|период\s+вымывания)\b[^0-9]{0,30}(?P<value>\d+\s+(?:дн|день|дней|дн\.|days?|weeks?|недель))", re.IGNORECASE),
            re.compile(r"\b(?:период\s+отмывки|washout)\s+(?:не\s+менее|at\s+least|минимум|minimum)\s+(?P<value>\d+\s+(?:дн|день|дней|дн\.|days?|weeks?|недель))", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bwashout\s+period\b[^0-9]{0,30}(?P<value>\d+\s+(?:days?|weeks?))", re.IGNORECASE),
            re.compile(r"\bwashout\s+(?:period\s+of\s+)?(?:at\s+least|minimum)\s+(?P<value>\d+\s+(?:days?|weeks?))", re.IGNORECASE),
        ],
        parser=parse_washout_period_value, confidence_policy=confidence_high, priority=180,
        preferred_topics=['design_plan', 'bioequivalence']
    ))

    # Fasting Condition
    rules.append(ExtractionRule(
        fact_type="bioequivalence", fact_key="fasting_condition",
        patterns_ru=[
            re.compile(r"\b(?:натощак|fasting|на\s+голодный\s+желудок)\s+(?:условиях|conditions?)\b", re.IGNORECASE),
            re.compile(r"\b(?:после\s+еды|fed|после\s+приема\s+пищи|non-fasting)\s+(?:условиях|conditions?)\b", re.IGNORECASE),
            re.compile(r"\b(?:условия|conditions?)\s*[:\-]?\s*(?:натощак|fasting|fed|после\s+еды)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\bfasting\s+conditions?\b", re.IGNORECASE),
            re.compile(r"\bfed\s+conditions?\b", re.IGNORECASE),
            re.compile(r"\bnon-fasting\s+conditions?\b", re.IGNORECASE),
            re.compile(r"\b(?:conditions?|state)\s*[:\-]?\s*(?:fasting|fed|non-fasting)", re.IGNORECASE),
        ],
        parser=parse_fasting_condition_value, confidence_policy=confidence_high, priority=175,
        preferred_topics=['ip_management', 'bioequivalence']
    ))

    # Blood Sampling Volume
    rules.append(ExtractionRule(
        fact_type="bioequivalence", fact_key="blood_sampling_volume",
        patterns_ru=[
            re.compile(r"\b(?:объем\s+крови|blood\s+volume|blood\s+sampling\s+volume)\b[^0-9]{0,30}(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>мл|ml|л|l)\b", re.IGNORECASE),
            re.compile(r"\b(?:всего\s+крови|total\s+blood\s+volume)\s+(?:приблизительно|approximately|около|about)\s+(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>мл|ml|л|l)\b", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:blood\s+volume|blood\s+sampling\s+volume|total\s+blood\s+volume)\b[^0-9]{0,30}(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>ml|l)\b", re.IGNORECASE),
            re.compile(r"\b(?:approximately|about)\s+(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>ml|l)\s+(?:of\s+)?blood\b", re.IGNORECASE),
        ],
        parser=parse_blood_volume_value, confidence_policy=confidence_medium, priority=170,
        preferred_topics=['bioequivalence', 'stats_sample_size']
    ))

    # PK Parameters
    rules.append(ExtractionRule(
        fact_type="bioequivalence", fact_key="pk_parameters",
        patterns_ru=[
            re.compile(r"\b(?:pk\s+параметры|pharmacokinetic\s+parameters?|параметры\s+фармакокинетики)\b", re.IGNORECASE),
            re.compile(r"\b(?:оценке|evaluation|analysis)\s+(?:фармакокинетических\s+параметров|pk\s+parameters)", re.IGNORECASE),
        ],
        patterns_en=[
            re.compile(r"\b(?:pk\s+parameters?|pharmacokinetic\s+parameters?)\b", re.IGNORECASE),
            re.compile(r"\b(?:evaluation|analysis)\s+of\s+(?:pk|pharmacokinetic)\s+parameters?", re.IGNORECASE),
            re.compile(r"\b(?:AUC|Cmax|Tmax|T1/2|Half-life)\b", re.IGNORECASE),
        ],
        parser=parse_pk_parameters_value, confidence_policy=confidence_low, priority=160,
        preferred_topics=['endpoints_efficacy', 'bioequivalence']
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
    if "qd" in text_lower or "once daily" in text_lower or "раз в день" in text_lower or "один раз в день" in text_lower:
        return "qd"
    if "bid" in text_lower or "twice daily" in text_lower or "дважды в день" in text_lower or "два раза в день" in text_lower:
        return "bid"
    if "tid" in text_lower or "three times daily" in text_lower or "три раза в день" in text_lower:
        return "tid"
    if "qid" in text_lower or "four times daily" in text_lower or "четыре раза в день" in text_lower:
        return "qid"
    if "weekly" in text_lower or "еженедел" in text_lower or "qw" in text_lower:
        return "weekly"
    return "unknown"


def _normalize_gender(text: str) -> str:
    """Нормализует пол (gender)."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["all", "оба", "both", "все", "любой"]):
        return "all"
    if any(kw in text_lower for kw in ["male", "мужск", "мужчин", "мужской"]):
        return "male"
    if any(kw in text_lower for kw in ["female", "женск", "женщин", "женский"]):
        return "female"
    return "unknown"


def _normalize_population_type(text: str) -> str:
    """Нормализует тип популяции (здоровые добровольцы vs пациенты)."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["healthy", "здоров", "доброволь", "volunteer"]):
        return "healthy_volunteers"
    if any(kw in text_lower for kw in ["patient", "пациент", "больной", "subjects with"]):
        return "patients"
    return "unknown"


def _normalize_control_type(text: str) -> str:
    """Нормализует тип контроля."""
    text_lower = text.lower()
    if "placebo" in text_lower or "плацебо" in text_lower:
        return "placebo"
    if "active" in text_lower or "активн" in text_lower:
        return "active"
    if "uncontrolled" in text_lower or "неконтрол" in text_lower or "без контроля" in text_lower:
        return "uncontrolled"
    return "unknown"


def _normalize_design_configuration(text: str) -> str:
    """Нормализует конфигурацию дизайна (parallel, crossover, factorial)."""
    text_lower = text.lower()
    if "crossover" in text_lower or "кроссовер" in text_lower or "cross-over" in text_lower:
        return "crossover"
    if "parallel" in text_lower or "параллельн" in text_lower:
        return "parallel"
    if "factorial" in text_lower or "фактор" in text_lower:
        return "factorial"
    if "sequential" in text_lower or "последовательно" in text_lower:
        return "sequential"
    return "unknown"


def _normalize_masking(text: str) -> str:
    """Нормализует тип ослепления (маскирование)."""
    text_lower = text.lower()
    if "open" in text_lower or "открыт" in text_lower or "open-label" in text_lower:
        return "open-label"
    if "double" in text_lower or "двойн" in text_lower or "double-blind" in text_lower:
        return "double-blind"
    if "single" in text_lower or "одинарн" in text_lower or "single-blind" in text_lower:
        return "single-blind"
    if "triple" in text_lower or "тройн" in text_lower or "triple-blind" in text_lower:
        return "triple-blind"
    return "unknown"


def _normalize_phase(text: str) -> str:
    """Нормализует фазу исследования."""
    text_lower = text.lower()
    # Римские цифры и арабские
    phase_match = re.search(r"phase\s*[:\-]?\s*([I1-4IV]+(?:[–\-/]?[I1-4IV]+)?)", text, re.IGNORECASE)
    if phase_match:
        phase_val = phase_match.group(1).upper().strip()
        # Нормализуем римские цифры
        phase_val = phase_val.replace("1", "I").replace("2", "II").replace("3", "III").replace("4", "IV")
        return phase_val
    # Текстовые варианты
    if any(kw in text_lower for kw in ["phase i", "фаза i", "первой фазы"]):
        return "I"
    if any(kw in text_lower for kw in ["phase ii", "фаза ii", "второй фазы"]):
        return "II"
    if any(kw in text_lower for kw in ["phase iii", "фаза iii", "третьей фазы"]):
        return "III"
    if any(kw in text_lower for kw in ["phase iv", "фаза iv", "четвертой фазы"]):
        return "IV"
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


def parse_protocol_id_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит ID протокола."""
    # Пробуем извлечь из именованной группы
    if "value" in match.groupdict() and match.group("value"):
        val = match.group("value")
    elif match.lastindex and match.lastindex >= 1:
        val = match.group(1)
    else:
        val = match.group(0)
    
    if not val:
        return None
    normalized = normalize_whitespace(val)
    # Ограничиваем длину протокольного ID
    if len(normalized) > 64:
        normalized = normalized[:64].rstrip()
    return {"value": normalized}


def parse_therapeutic_area_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит терапевтическую область после ключевых слов."""
    # Пробуем извлечь из именованной группы
    if "value" in match.groupdict() and match.group("value"):
        val = match.group("value")
    elif match.lastindex and match.lastindex >= 1:
        val = match.group(1)
    else:
        val = match.group(0)
    
    if not val:
        return None
    normalized = normalize_whitespace(val)
    # Ограничиваем длину
    if len(normalized) > 200:
        normalized = normalized[:200].rstrip()
    # Проверяем, что значение начинается с заглавной буквы
    if normalized and normalized[0].islower():
        return None
    return {"value": normalized}


def parse_age_max_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит максимальный возраст."""
    if match.lastindex and match.lastindex >= 2:
        val = parse_int(match.group(2))
        if val is not None:
            return {"value": val}
    # Пробуем найти возраст после диапазона
    age_pattern = r"(\d+)\s*(?:до|–|-|to)\s*(\d+)"
    m = re.search(age_pattern, text, re.IGNORECASE)
    if m and m.lastindex >= 2:
        val = parse_int(m.group(2))
        if val is not None:
            return {"value": val}
    return None


def parse_age_range_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит диапазон возраста."""
    # Пробуем извлечь из именованных групп
    if "min" in match.groupdict() and "max" in match.groupdict():
        min_val = parse_int(match.group("min"))
        max_val = parse_int(match.group("max"))
        if min_val is not None and max_val is not None and min_val <= max_val:
            return {"value": {"min": min_val, "max": max_val}, "raw": match.group(0)}
    # Fallback на общий парсер диапазона
    raw = match.group(0)
    range_dict = parse_range(raw)
    if range_dict is None:
        return None
    return {"value": range_dict, "raw": raw}


def parse_washout_period_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит период отмывки."""
    # Пробуем извлечь из именованной группы
    if "value" in match.groupdict() and match.group("value"):
        raw = match.group("value")
    elif match.lastindex and match.lastindex >= 1:
        raw = match.group(1)
    else:
        raw = match.group(0)
    
    duration = parse_duration(raw)
    if duration is None:
        # Пробуем извлечь только число дней
        days_match = re.search(r"(\d+)\s*(?:дн|день|days?)", raw, re.IGNORECASE)
        if days_match:
            days_val = parse_int(days_match.group(1))
            if days_val is not None:
                return {"value": {"value": days_val, "unit": "day"}, "raw": raw}
    return {"value": duration, "raw": raw} if duration else None


def parse_blood_volume_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит объем крови с единицами измерения."""
    # Пробуем извлечь из именованных групп
    if "value" in match.groupdict() and match.group("value"):
        val_str = match.group("value")
        unit_str = match.group("unit") if "unit" in match.groupdict() and match.group("unit") else None
    elif match.lastindex and match.lastindex >= 1:
        val_str = match.group(1)
        unit_str = match.group(2) if match.lastindex >= 2 else None
    else:
        # Пробуем извлечь из полного текста
        volume_match = re.search(r"(\d+(?:[.,]\d+)?)\s*(мл|ml|л|l)", text, re.IGNORECASE)
        if volume_match:
            val_str = volume_match.group(1)
            unit_str = volume_match.group(2)
        else:
            return None
    
    if not val_str:
        return None
    value = parse_float(val_str)
    if value is None:
        return None
    unit = unit_str.strip().lower() if unit_str else "ml"
    # Нормализуем единицы
    if unit in ["мл", "ml"]:
        unit = "ml"
    elif unit in ["л", "l"]:
        unit = "l"
    return {"value": value, "unit": unit}


def parse_pk_parameters_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит список PK параметров (AUC, Cmax, Tmax, T1/2 и т.д.)."""
    # Извлекаем все PK параметры из текста
    pk_params = []
    param_patterns = [
        r"\bAUC(?:0[–\-]?∞|0[–\-]?inf|last|t)\b",
        r"\bCmax\b",
        r"\bTmax\b",
        r"\bT\s*1/2\b",
        r"\bT\s*½\b",
        r"\bHalf-life\b",
        r"\bПериод\s+полувыведения\b",
        r"\bKe\b",
        r"\bVd\b",
        r"\bCL\b",
        r"\bClearance\b",
    ]
    for pattern in param_patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            param = m.group(0).upper()
            if param not in pk_params:
                pk_params.append(param)
    if not pk_params:
        return None
    return {"value": pk_params}


def parse_fasting_condition_value(text: str, match: re.Match) -> dict[str, Any] | None:
    """Парсит условие приема пищи (натощак/после еды)."""
    # Используем полный текст для определения условия
    text_lower = text.lower()
    if any(kw in text_lower for kw in ["fasting", "натощак", "на голодный желудок"]):
        return {"value": "fasting"}
    if any(kw in text_lower for kw in ["fed", "после еды", "после приема пищи", "non-fasting", "после еды"]):
        return {"value": "fed"}
    return None

