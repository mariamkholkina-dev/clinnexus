from __future__ import annotations

from types import SimpleNamespace

from app.db.enums import DocumentLanguage
from app.services.section_mapping import SectionMappingService


def test_get_signals_v2_ru() -> None:
    service = SectionMappingService(db=None)  # type: ignore[arg-type]
    recipe_json = {
        "version": 2,
        "language": {"mode": "auto", "fallback": "en"},
        "mapping": {
            "signals": {
                "lang": {
                    "ru": {
                        "must": ["цели", "цель"],
                        "should": ["задачи"],
                        "not": ["приложение"],
                        "regex": [r"\bцели(\s+исследования)?\b"],
                    },
                    "en": {
                        "must": ["objectives"],
                        "should": [],
                        "not": [],
                        "regex": [r"\bobjective(s)?\b"],
                    },
                }
            }
        },
    }

    signals = service._get_signals(recipe_json, DocumentLanguage.RU)
    assert len(signals.must_keywords) > 0
    assert len(signals.regex_patterns) > 0


def test_auto_derive_signals_v2_ru() -> None:
    service = SectionMappingService(db=None)  # type: ignore[arg-type]
    recipe_json = {
        "version": 2,
        "language": {"mode": "auto", "fallback": "en"},
        "context_build": {
            "fallback_search": {
                "lang": {
                    "ru": {"query_templates": ["Цели исследования", "Задачи исследования"]},
                    "en": {"query_templates": ["Study objectives"]},
                }
            }
        },
    }
    contract = SimpleNamespace(title="Objectives", section_key="protocol.objectives")

    signals, source = service._auto_derive_signals(
        contract=contract, recipe_json=recipe_json, document_language=DocumentLanguage.RU
    )
    assert source == "auto"
    assert len(signals.must_keywords) > 0


def test_backward_compat() -> None:
    service = SectionMappingService(db=None)  # type: ignore[arg-type]
    recipe_json = {
        "version": 1,
        "heading_match": {"must": ["objective"], "should": [], "not": []},
        "regex": {"heading": [r"\bobjective(s)?\b"]},
        "fallback_search": {"query_templates": {"ru": ["цели исследования"], "en": ["study objectives"]}},
    }

    signals = service._get_signals(recipe_json, DocumentLanguage.EN)
    assert signals.must_keywords == ["objective"]
    assert signals.regex_patterns == [r"\bobjective(s)?\b"]

    contract = SimpleNamespace(title="Objectives", section_key="protocol.objectives")
    derived, source = service._auto_derive_signals(
        contract=contract, recipe_json=recipe_json, document_language=DocumentLanguage.EN
    )
    assert source == "auto"
    assert len(derived.must_keywords) > 0


