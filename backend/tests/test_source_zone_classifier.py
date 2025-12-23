"""Unit-тесты для source_zone_classifier."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.source_zone_classifier import SourceZoneClassifier, SourceZoneResult


@pytest.fixture
def classifier() -> SourceZoneClassifier:
    """Фикстура для создания классификатора."""
    rules_file = Path(__file__).parent.parent / "app" / "data" / "source_zone_rules.yaml"
    return SourceZoneClassifier(rules_file=rules_file)


class TestSourceZoneClassifier:
    """Тесты для SourceZoneClassifier."""

    def test_randomization_ru(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации рандомизации на русском."""
        # Прямое совпадение
        result = classifier.classify("Рандомизация")
        assert result.zone == "randomization"
        assert result.confidence > 0.5

        # Вариации
        result2 = classifier.classify("Метод рандомизации")
        assert result2.zone == "randomization"

        result3 = classifier.classify("Случайное распределение")
        assert result3.zone == "randomization"

        # В составе пути
        result4 = classifier.classify("Дизайн исследования/Рандомизация пациентов")
        assert result4.zone == "randomization"

    def test_randomization_en(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации рандомизации на английском."""
        result = classifier.classify("Randomization")
        assert result.zone == "randomization"
        assert result.confidence > 0.5

        result2 = classifier.classify("Random Allocation")
        assert result2.zone == "randomization"

        result3 = classifier.classify("Study Design/Randomization")
        assert result3.zone == "randomization"

    def test_endpoints_ru(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации endpoints на русском."""
        result = classifier.classify("Первичная конечная точка")
        assert result.zone == "endpoints"

        result2 = classifier.classify("Вторичные конечные точки")
        assert result2.zone == "endpoints"

        result3 = classifier.classify("Эффективность")
        assert result3.zone == "endpoints"

    def test_endpoints_en(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации endpoints на английском."""
        result = classifier.classify("Primary Endpoint")
        assert result.zone == "endpoints"

        result2 = classifier.classify("Secondary Endpoints")
        assert result2.zone == "endpoints"

        result3 = classifier.classify("Efficacy Endpoint")
        assert result3.zone == "endpoints"

    def test_adverse_events_ru(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации нежелательных явлений на русском."""
        result = classifier.classify("Нежелательные явления")
        assert result.zone == "adverse_events"

        result2 = classifier.classify("Побочные эффекты")
        assert result2.zone == "adverse_events"

        result3 = classifier.classify("Безопасность")
        assert result3.zone == "adverse_events"

        result4 = classifier.classify("AE")
        assert result4.zone == "adverse_events"

    def test_adverse_events_en(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации нежелательных явлений на английском."""
        result = classifier.classify("Adverse Events")
        assert result.zone == "adverse_events"

        result2 = classifier.classify("Safety")
        assert result2.zone == "adverse_events"

        result3 = classifier.classify("AEs")
        assert result3.zone == "adverse_events"

    def test_serious_adverse_events_ru(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации серьезных нежелательных явлений на русском."""
        result = classifier.classify("Серьезные нежелательные явления")
        assert result.zone == "serious_adverse_events"

        result2 = classifier.classify("SAE")
        assert result2.zone == "serious_adverse_events"

        result3 = classifier.classify("ТНЯ")
        assert result3.zone == "serious_adverse_events"

    def test_serious_adverse_events_en(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации серьезных нежелательных явлений на английском."""
        result = classifier.classify("Serious Adverse Events")
        assert result.zone == "serious_adverse_events"

        result2 = classifier.classify("SAEs")
        assert result2.zone == "serious_adverse_events"

    def test_statistical_methods_ru(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации статистических методов на русском."""
        result = classifier.classify("Статистические методы")
        assert result.zone == "statistical_methods"

        result2 = classifier.classify("Статистический анализ")
        assert result2.zone == "statistical_methods"

        result3 = classifier.classify("Статистика")
        assert result3.zone == "statistical_methods"

    def test_statistical_methods_en(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации статистических методов на английском."""
        result = classifier.classify("Statistical Methods")
        assert result.zone == "statistical_methods"

        result2 = classifier.classify("Statistical Analysis")
        assert result2.zone == "statistical_methods"

        result3 = classifier.classify("Statistics")
        assert result3.zone == "statistical_methods"

    def test_eligibility_ru(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации критериев включения/исключения на русском."""
        result = classifier.classify("Критерии включения")
        assert result.zone == "eligibility"

        result2 = classifier.classify("Критерии исключения")
        assert result2.zone == "eligibility"

        result3 = classifier.classify("Отбор пациентов")
        assert result3.zone == "eligibility"

        result4 = classifier.classify("Критерии отбора")
        assert result4.zone == "eligibility"

    def test_eligibility_en(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации критериев включения/исключения на английском."""
        result = classifier.classify("Eligibility")
        assert result.zone == "eligibility"

        result2 = classifier.classify("Inclusion Criteria")
        assert result2.zone == "eligibility"

        result3 = classifier.classify("Exclusion Criteria")
        assert result3.zone == "eligibility"

        result4 = classifier.classify("Selection Criteria")
        assert result4.zone == "eligibility"

    def test_ip_handling_ru(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации обращения с исследуемым препаратом на русском."""
        result = classifier.classify("Исследуемый препарат")
        assert result.zone == "ip_handling"

        result2 = classifier.classify("Дозировка")
        assert result2.zone == "ip_handling"

        result3 = classifier.classify("Режим доз")
        assert result3.zone == "ip_handling"

        result4 = classifier.classify("ИП")
        assert result4.zone == "ip_handling"

    def test_ip_handling_en(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации обращения с исследуемым препаратом на английском."""
        result = classifier.classify("Investigational Product")
        assert result.zone == "ip_handling"

        result2 = classifier.classify("Study Drug")
        assert result2.zone == "ip_handling"

        result3 = classifier.classify("Dosing")
        assert result3.zone == "ip_handling"

        result4 = classifier.classify("IP")
        assert result4.zone == "ip_handling"

        result5 = classifier.classify("Dose Regimen")
        assert result5.zone == "ip_handling"

    def test_unknown_fallback(self, classifier: SourceZoneClassifier) -> None:
        """Тест fallback на unknown для неподходящих заголовков."""
        result = classifier.classify("Введение")
        assert result.zone == "unknown"
        assert result.confidence == 0.0

        result2 = classifier.classify("ROOT")
        assert result2.zone == "unknown"
        assert result2.confidence == 0.0

        result3 = classifier.classify("__FRONTMATTER__")
        assert result3.zone == "unknown"
        assert result3.confidence == 0.0

        result4 = classifier.classify("FOOTNOTES")
        assert result4.zone == "unknown"
        assert result4.confidence == 0.0

    def test_complex_path(self, classifier: SourceZoneClassifier) -> None:
        """Тест классификации для сложного пути с несколькими сегментами."""
        # Матч должен быть по любому сегменту пути
        result = classifier.classify("Раздел 1/Методология/Статистические методы")
        assert result.zone == "statistical_methods"

        result2 = classifier.classify("Study Design/Randomization/Patient Allocation")
        assert result2.zone == "randomization"

    def test_case_insensitive(self, classifier: SourceZoneClassifier) -> None:
        """Тест на регистронезависимость паттернов."""
        result = classifier.classify("RANDOMIZATION")
        assert result.zone == "randomization"

        result2 = classifier.classify("randomization")
        assert result2.zone == "randomization"

        result3 = classifier.classify("Randomization")
        assert result3.zone == "randomization"

    def test_same_concept_different_names(self, classifier: SourceZoneClassifier) -> None:
        """Тест, что одинаковая суть с разными названиями попадает в одну zone."""
        # Разные варианты названия одного понятия должны попадать в одну zone
        
        # Рандомизация
        results_randomization = [
            classifier.classify("Рандомизация"),
            classifier.classify("Randomization"),
            classifier.classify("Случайное распределение"),
            classifier.classify("Random Allocation"),
        ]
        assert all(r.zone == "randomization" for r in results_randomization)

        # Endpoints
        results_endpoints = [
            classifier.classify("Первичная конечная точка"),
            classifier.classify("Primary Endpoint"),
            classifier.classify("Эффективность"),
            classifier.classify("Efficacy Endpoint"),
        ]
        assert all(r.zone == "endpoints" for r in results_endpoints)

        # AE
        results_ae = [
            classifier.classify("Нежелательные явления"),
            classifier.classify("Adverse Events"),
            classifier.classify("Побочные эффекты"),
            classifier.classify("Безопасность"),
            classifier.classify("Safety"),
        ]
        assert all(r.zone == "adverse_events" for r in results_ae)

        # Statistical methods
        results_stats = [
            classifier.classify("Статистические методы"),
            classifier.classify("Statistical Methods"),
            classifier.classify("Статистический анализ"),
            classifier.classify("Statistical Analysis"),
        ]
        assert all(r.zone == "statistical_methods" for r in results_stats)

        # Eligibility
        results_eligibility = [
            classifier.classify("Критерии включения"),
            classifier.classify("Inclusion Criteria"),
            classifier.classify("Критерии исключения"),
            classifier.classify("Exclusion Criteria"),
        ]
        assert all(r.zone == "eligibility" for r in results_eligibility)

        # IP handling
        results_ip = [
            classifier.classify("Исследуемый препарат"),
            classifier.classify("Investigational Product"),
            classifier.classify("Дозировка"),
            classifier.classify("Dosing"),
        ]
        assert all(r.zone == "ip_handling" for r in results_ip)

