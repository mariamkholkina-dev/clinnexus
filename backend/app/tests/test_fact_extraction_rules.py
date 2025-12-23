"""Unit-тесты для парсеров правил извлечения фактов."""

from __future__ import annotations

import re

import pytest

from app.services.fact_extraction_rules import (
    parse_date_to_iso,
    parse_duration,
    parse_float,
    parse_int,
    parse_ratio,
    parse_range,
)


class TestParseInt:
    """Тесты для parse_int."""

    def test_parse_simple_int(self):
        assert parse_int("120") == 120
        assert parse_int("0") is None  # <= 0
        assert parse_int("1000001") is None  # > 1_000_000

    def test_parse_int_with_spaces(self):
        assert parse_int("1 200") == 1200
        assert parse_int("1,200") == 1200

    def test_parse_int_invalid(self):
        assert parse_int("abc") is None
        assert parse_int("12.5") is None
        assert parse_int("") is None


class TestParseFloat:
    """Тесты для parse_float."""

    def test_parse_simple_float(self):
        assert parse_float("0.05") == 0.05
        assert parse_float("0.5") == 0.5

    def test_parse_float_with_comma(self):
        assert parse_float("0,05") == 0.05

    def test_parse_float_invalid(self):
        assert parse_float("abc") is None
        assert parse_float("") is None


class TestParseRange:
    """Тесты для parse_range."""

    def test_parse_range_dash(self):
        result = parse_range("18–65")
        assert result == {"min": 18, "max": 65}

    def test_parse_range_hyphen(self):
        result = parse_range("18-65")
        assert result == {"min": 18, "max": 65}

    def test_parse_range_ru(self):
        result = parse_range("от 18 до 65")
        assert result == {"min": 18, "max": 65}

    def test_parse_range_en(self):
        result = parse_range("18 to 65")
        assert result == {"min": 18, "max": 65}

    def test_parse_range_invalid(self):
        assert parse_range("abc") is None
        assert parse_range("65-18") is None  # min > max
        assert parse_range("") is None


class TestParseRatio:
    """Тесты для parse_ratio."""

    def test_parse_ratio_colon(self):
        assert parse_ratio("2:1") == "2:1"
        assert parse_ratio("1:1") == "1:1"

    def test_parse_ratio_slash(self):
        assert parse_ratio("2/1") == "2:1"

    def test_parse_ratio_ru(self):
        assert parse_ratio("2 к 1") == "2:1"

    def test_parse_ratio_en(self):
        assert parse_ratio("2 to 1") == "2:1"

    def test_parse_ratio_invalid(self):
        assert parse_ratio("abc") is None
        assert parse_ratio("") is None


class TestParseDuration:
    """Тесты для parse_duration."""

    def test_parse_duration_weeks_ru(self):
        result = parse_duration("12 недель")
        assert result == {"value": 12, "unit": "week"}

    def test_parse_duration_weeks_en(self):
        result = parse_duration("12 weeks")
        assert result == {"value": 12, "unit": "week"}

    def test_parse_duration_months_ru(self):
        result = parse_duration("6 месяцев")
        assert result == {"value": 6, "unit": "month"}

    def test_parse_duration_months_en(self):
        result = parse_duration("6 months")
        assert result == {"value": 6, "unit": "month"}

    def test_parse_duration_days_ru(self):
        result = parse_duration("30 дней")
        assert result == {"value": 30, "unit": "day"}

    def test_parse_duration_days_en(self):
        result = parse_duration("30 days")
        assert result == {"value": 30, "unit": "day"}

    def test_parse_duration_invalid(self):
        assert parse_duration("abc") is None
        assert parse_duration("") is None


class TestParseDateToIso:
    """Тесты для parse_date_to_iso."""

    def test_parse_date_iso(self):
        assert parse_date_to_iso("2021-03-05") == "2021-03-05"

    def test_parse_date_dd_mm_yyyy_dot(self):
        assert parse_date_to_iso("05.03.2021") == "2021-03-05"

    def test_parse_date_dd_mm_yyyy_slash(self):
        assert parse_date_to_iso("05/03/2021") == "2021-03-05"

    def test_parse_date_en_month(self):
        assert parse_date_to_iso("05 March 2021") == "2021-03-05"
        assert parse_date_to_iso("5 Mar 2021") == "2021-03-05"

    def test_parse_date_ru_month(self):
        assert parse_date_to_iso("5 марта 2021") == "2021-03-05"
        assert parse_date_to_iso("5 мар 2021") == "2021-03-05"

    def test_parse_date_invalid(self):
        assert parse_date_to_iso("abc") is None
        assert parse_date_to_iso("32.13.2021") is None  # Invalid date
        assert parse_date_to_iso("") is None


class TestExtractionRules:
    """Тесты для правил извлечения."""

    def test_phase_extraction_ru(self):
        """Тест извлечения фазы исследования (RU)."""
        from app.services.fact_extraction_rules import get_extraction_rules

        rules = get_extraction_rules()
        phase_rules = [r for r in rules if r.fact_key == "phase"]

        assert len(phase_rules) > 0
        rule = phase_rules[0]

        # Тест RU паттернов
        text_ru = "Фаза II исследования"
        for pattern in rule.patterns_ru:
            match = pattern.search(text_ru)
            if match:
                parsed = rule.parser(text_ru, match)
                assert parsed is not None
                assert "II" in parsed["value"] or "2" in parsed["value"]
                break

    def test_phase_extraction_en(self):
        """Тест извлечения фазы исследования (EN)."""
        from app.services.fact_extraction_rules import get_extraction_rules

        rules = get_extraction_rules()
        phase_rules = [r for r in rules if r.fact_key == "phase"]

        assert len(phase_rules) > 0
        rule = phase_rules[0]

        # Тест EN паттернов
        text_en = "Phase II study"
        for pattern in rule.patterns_en:
            match = pattern.search(text_en)
            if match:
                parsed = rule.parser(text_en, match)
                assert parsed is not None
                assert "II" in parsed["value"] or "2" in parsed["value"]
                break

    def test_randomization_ratio_extraction(self):
        """Тест извлечения соотношения рандомизации."""
        from app.services.fact_extraction_rules import get_extraction_rules

        rules = get_extraction_rules()
        ratio_rules = [r for r in rules if r.fact_key == "randomization_ratio"]

        assert len(ratio_rules) > 0
        rule = ratio_rules[0]

        # Тест RU
        text_ru = "Соотношение рандомизации: 2:1"
        for pattern in rule.patterns_ru:
            match = pattern.search(text_ru)
            if match:
                parsed = rule.parser(text_ru, match)
                assert parsed is not None
                assert parsed["value"] == "2:1"
                break

        # Тест EN
        text_en = "Randomization ratio: 2:1"
        for pattern in rule.patterns_en:
            match = pattern.search(text_en)
            if match:
                parsed = rule.parser(text_en, match)
                assert parsed is not None
                assert parsed["value"] == "2:1"
                break

    def test_blinding_extraction(self):
        """Тест извлечения типа ослепления."""
        from app.services.fact_extraction_rules import get_extraction_rules

        rules = get_extraction_rules()
        blinding_rules = [r for r in rules if r.fact_key == "design.blinding"]

        assert len(blinding_rules) > 0
        rule = blinding_rules[0]

        # Тест open-label
        text = "Open-label study"
        for pattern in rule.patterns_en:
            match = pattern.search(text)
            if match:
                parsed = rule.parser(text, match)
                assert parsed is not None
                assert parsed["value"] == "open-label"
                break

        # Тест double-blind
        text = "Double-blind study"
        for pattern in rule.patterns_en:
            match = pattern.search(text)
            if match:
                parsed = rule.parser(text, match)
                assert parsed is not None
                assert parsed["value"] == "double"
                break

    def test_alpha_extraction(self):
        """Тест извлечения уровня значимости."""
        from app.services.fact_extraction_rules import get_extraction_rules

        rules = get_extraction_rules()
        alpha_rules = [r for r in rules if r.fact_key == "alpha"]

        assert len(alpha_rules) > 0
        rule = alpha_rules[0]

        # Тест EN
        text_en = "Alpha = 0.05"
        for pattern in rule.patterns_en:
            match = pattern.search(text_en)
            if match:
                parsed = rule.parser(text_en, match)
                assert parsed is not None
                assert parsed["value"] == 0.05
                break

        # Тест RU
        text_ru = "Альфа: 0,05"
        for pattern in rule.patterns_ru:
            match = pattern.search(text_ru)
            if match:
                parsed = rule.parser(text_ru, match)
                assert parsed is not None
                assert parsed["value"] == 0.05
                break

    def test_power_extraction(self):
        """Тест извлечения статистической мощности."""
        from app.services.fact_extraction_rules import get_extraction_rules

        rules = get_extraction_rules()
        power_rules = [r for r in rules if r.fact_key == "power"]

        assert len(power_rules) > 0
        rule = power_rules[0]

        # Тест EN
        text_en = "Power = 80"
        for pattern in rule.patterns_en:
            match = pattern.search(text_en)
            if match:
                parsed = rule.parser(text_en, match)
                assert parsed is not None
                assert parsed["value"] == 80
                break

        # Тест RU
        text_ru = "Мощность: 80"
        for pattern in rule.patterns_ru:
            match = pattern.search(text_ru)
            if match:
                parsed = rule.parser(text_ru, match)
                assert parsed is not None
                assert parsed["value"] == 80
                break

