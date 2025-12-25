"""Сервис для автоматического маппинга семантических секций на anchors документа."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.db.enums import (
    AnchorContentType,
    DocumentLanguage,
    DocumentType,
    SectionMapMappedBy,
    SectionMapStatus,
)
from app.db.models.anchors import Anchor
from app.db.models.facts import Fact, FactEvidence
from app.db.models.sections import TargetSectionContract, TargetSectionMap
from app.db.models.studies import Document, DocumentVersion
from app.services.text_normalization import normalize_for_match, normalize_for_regex
from app.services.zone_config import get_zone_config_service
from app.services.lean_passport import normalize_passport
from app.services.llm_client import LLMClient


@dataclass
class MappingSummary:
    """Сводка результатов маппинга секций."""

    sections_mapped_count: int = 0
    sections_needs_review_count: int = 0
    mapping_warnings: list[str] = None

    def __post_init__(self):
        if self.mapping_warnings is None:
            self.mapping_warnings = []


@dataclass
class HeadingCandidate:
    """Кандидат заголовка для секции."""

    anchor_id: str
    anchor: Anchor
    score: float
    reason: str


@dataclass
class DocumentOutline:
    """Структура документа (заголовки в порядке появления)."""

    headings: list[tuple[Anchor, int]]  # (anchor, level)


@dataclass
class LanguageAwareSignals:
    """Сигналы для матчинга с учетом языка."""
    
    must_keywords: list[str]
    should_keywords: list[str]
    not_keywords: list[str]
    regex_patterns: list[str]
    threshold: float = 3.0  # Минимальный score для кандидата
    confidence_cap: float | None = None  # Максимальный confidence (для mixed/unknown)


# Версия SectionMappingService (увеличивается при изменении логики маппинга)
VERSION = "1.0.0"

# 12 core sections для protocol (из ingestion_campaign_guide.md)
PROTOCOL_CORE_SECTIONS = [
    "protocol.synopsis",      # overview
    "protocol.study_design",  # design
    "protocol.ip",            # ip
    "protocol.endpoints",     # endpoints
    "protocol.population",    # population
    "protocol.procedures",    # procedures
    "protocol.soa",           # procedures (SoA)
    "protocol.statistics",    # statistics
    "protocol.safety",        # safety
    "protocol.data_management",  # data_management
    "protocol.ethics",        # ethics
    "protocol.admin",         # admin
]


class SectionMappingService:
    """Сервис для автоматического маппинга семантических секций на anchors документа."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def _mapping_debug_enabled(self) -> bool:
        # Фича-флаг для подробных диагностических логов (top-3 кандидатов и т.п.)
        # По умолчанию выключено, чтобы не засорять прод-логи.
        return bool(getattr(settings, "mapping_debug_logs", False))

    def _truncate(self, s: str, max_len: int) -> str:
        if not s:
            return ""
        s = str(s)
        if len(s) <= max_len:
            return s
        return s[: max(0, max_len - 1)] + "…"

    def _signals_lang_label(self, document_language: DocumentLanguage) -> str:
        if document_language == DocumentLanguage.RU:
            return "ru"
        if document_language == DocumentLanguage.EN:
            return "en"
        # MIXED/UNKNOWN: фактически используем объединённые сигналы, если recipe v2.
        return "ru+en"

    def _coerce_str_list(self, v: Any) -> list[str]:
        """Безопасно приводит значение к list[str] (только непустые строки)."""
        if not v:
            return []
        if isinstance(v, list):
            return [x for x in v if isinstance(x, str) and x.strip()]
        return []

    def _pick_recipe_lang(
        self, *, recipe_json: dict[str, Any], document_language: DocumentLanguage
    ) -> str | None:
        """
        Возвращает ключ языка ('ru'|'en'), который нужно использовать для signals/query_templates.

        Правила (по требованиям):
        - если language.mode == 'auto' => берём язык документа (ru/en)
        - иначе => берём language.fallback (ru/en)
        - при MIXED/UNKNOWN => None (для v2 обычно объединяем ru+en)
        """
        lang_obj = recipe_json.get("language")
        mode = None
        fallback = None
        if isinstance(lang_obj, dict):
            mode = lang_obj.get("mode")
            fallback = lang_obj.get("fallback")

        if mode == "auto":
            if document_language == DocumentLanguage.RU:
                return "ru"
            if document_language == DocumentLanguage.EN:
                return "en"
            return None

        if isinstance(fallback, str):
            fb = fallback.lower().strip()
            if fb in ("ru", "en"):
                return fb
        return None

    def _is_signals_empty(self, signals: LanguageAwareSignals) -> bool:
        return (
            len(signals.must_keywords) == 0
            and len(signals.should_keywords) == 0
            and len(signals.not_keywords) == 0
            and len(signals.regex_patterns) == 0
        )

    def _tokenize_title(self, title: str) -> list[str]:
        # Берём токены из всего title, включая содержимое в скобках. Нужна детерминированность.
        if not title:
            return []
        raw = title.strip()
        # Разбиваем по любым небуквенно-цифровым символам, сохраняем слова длиной >= 3.
        parts = re.split(r"[^\w\u0400-\u04FF]+", raw, flags=re.UNICODE)
        out: list[str] = []
        for p in parts:
            p = p.strip()
            if len(p) >= 3:
                out.append(p)
        # Уникализируем, сохраняя порядок
        seen: set[str] = set()
        uniq = []
        for t in out:
            tl = t.lower()
            if tl in seen:
                continue
            seen.add(tl)
            uniq.append(t)
        return uniq

    def _section_key_synonyms(self, section_key: str) -> list[str]:
        # Мини-словарь для MVP: покрываем самые важные секции.
        # Возвращаем список "сильных" токенов (RU/EN).
        key = (section_key or "").lower()
        if key.endswith("endpoints") or ".endpoints" in key:
            return ["endpoints", "endpoint", "конечные", "точки", "конечная точка"]
        if key.endswith("objectives") or ".objectives" in key:
            return ["objectives", "objective", "цели", "задачи"]
        if ".eligibility.inclusion" in key:
            return ["inclusion", "criteria", "критерии", "включения"]
        if ".eligibility.exclusion" in key:
            return ["exclusion", "criteria", "критерии", "исключения"]
        if key.endswith("soa") or "schedule_of_activities" in key or ".soa" in key:
            return [
                "schedule",
                "activities",
                "assessments",
                "расписание",
                "процедур",
                "график",
                "процедуры",
            ]
        if key.endswith("synopsis") or ".synopsis" in key:
            return ["synopsis", "краткое", "резюме", "синопсис", "краткое изложение", "общая информация"]
        if "title_page" in key or key.endswith("title") or ".title" in key:
            return ["title", "page", "титульный", "лист", "титульная"]
        if ".design" in key:
            return ["design", "study design", "дизайн", "схема", "план"]
        if key.endswith("population") or ".population" in key:
            return ["population", "популяция", "выбор исследуемой популяции", "критерии включения", "исследуемая популяция"]
        if key.endswith("ethics") or ".ethics" in key:
            return ["ethics", "этика", "этические аспекты", "нормативное обеспечение", "этические"]
        return []

    def _auto_derive_signals(
        self,
        *,
        contract: TargetSectionContract,
        recipe_json: dict[str, Any],
        document_language: DocumentLanguage,
    ) -> tuple[LanguageAwareSignals, str]:
        """
        Runtime-автогенерация signals для маппинга, если в паспорте/контракте они пустые.
        Дефолты под MVP: must 2-4, should до 8, not (анти-appendix), regex (частые паттерны).
        """
        title_tokens = self._tokenize_title(contract.title or "")
        syn_tokens = self._section_key_synonyms(contract.target_section or "")

        # query_templates из retrieval_recipe_json (если присутствует)
        templates: list[str] = []

        # v2 (contracts): retrieval_recipe_json.context_build.fallback_search.lang.<ru|en>.query_templates
        version = recipe_json.get("version", 1)
        if version == 2:
            ctx = recipe_json.get("context_build") or {}
            fb2 = ctx.get("fallback_search") if isinstance(ctx, dict) else {}
            lang2 = fb2.get("lang") if isinstance(fb2, dict) else {}

            picked_lang = self._pick_recipe_lang(
                recipe_json=recipe_json, document_language=document_language
            )
            if picked_lang in ("ru", "en") and isinstance(lang2, dict):
                templates = self._coerce_str_list(
                    ((lang2.get(picked_lang) or {}).get("query_templates"))
                )
            elif isinstance(lang2, dict):
                # MIXED/UNKNOWN: пробуем ru + en
                templates = self._coerce_str_list(
                    ((lang2.get("ru") or {}).get("query_templates"))
                ) + self._coerce_str_list(
                    ((lang2.get("en") or {}).get("query_templates"))
                )

        # Legacy (v1/lean): retrieval_recipe_json.fallback_search.query_templates.{ru|en}
        if not templates:
            fb = recipe_json.get("fallback_search", {}) or {}
            qt = fb.get("query_templates") or {}
            if isinstance(qt, dict):
                lang_key = "ru" if document_language == DocumentLanguage.RU else "en"
                templates = [t for t in (qt.get(lang_key) or []) if isinstance(t, str)]

        template_tokens: list[str] = []
        for t in templates:
            template_tokens.extend(self._tokenize_title(t))

        # Собираем пул токенов, затем выбираем must/should детерминированно (по порядку).
        pool: list[str] = []
        pool.extend(title_tokens)
        pool.extend(syn_tokens)
        pool.extend(template_tokens)

        # Уникализируем pool (case-insensitive) сохраняя порядок
        seen: set[str] = set()
        pool_uniq: list[str] = []
        for x in pool:
            xl = x.lower()
            if xl in seen:
                continue
            seen.add(xl)
            pool_uniq.append(x)

        # Простая эвристика: must = первые 2-4 "сильных" токена.
        must = pool_uniq[:4]
        if len(must) < 2:
            must = pool_uniq[:2]

        # should = следующее, до 8
        should = [t for t in pool_uniq if t not in must][:8]

        # not: базовый анти-шум (особенно для SoA/Endpoints)
        not_keywords = [
            "приложение",
            "appendix",
            "шкала",
            "scale",
            "опросник",
            "questionnaire",
            "анкета",
            "form",
            "forms",
        ]

        regex_patterns: list[str] = []
        sk = (contract.target_section or "").lower()
        if sk.endswith("endpoints") or ".endpoints" in sk:
            regex_patterns = [
                r"\bконечн(ые|ая)\s+точк",
                r"\bendpoint(s)?\b",
            ]
        elif sk.endswith("objectives") or ".objectives" in sk:
            regex_patterns = [
                r"\bцели(\s+исследования)?\b",
                r"\bobjective(s)?\b",
            ]
        elif ".eligibility.inclusion" in sk:
            regex_patterns = [
                r"\bкритер(ии|ий)\s+включ",
                r"\binclusion(\s+criteria)?\b",
            ]
        elif ".eligibility.exclusion" in sk:
            regex_patterns = [
                r"\bкритер(ии|ий)\s+исключ",
                r"\bexclusion(\s+criteria)?\b",
            ]
        elif sk.endswith("soa") or ".soa" in sk or "schedule_of_activities" in sk:
            regex_patterns = [
                r"\bрасписан(ие|ия)\s+(процедур|мероприятий)\b",
                r"\bграфик\s+процедур\b",
                r"\bschedule\s+of\s+(activities|assessments)\b",
            ]

        # Threshold: если совсем мало сигналов — чуть мягче, иначе оставляем дефолт.
        threshold = 3.0
        if len(must) == 0 and len(should) <= 2 and len(regex_patterns) == 0:
            threshold = 2.0

        return (
            LanguageAwareSignals(
                must_keywords=must,
                should_keywords=should,
                not_keywords=not_keywords,
                regex_patterns=regex_patterns,
                threshold=threshold,
            ),
            "auto",
        )

    def _get_effective_signals(
        self,
        *,
        contract: TargetSectionContract,
        recipe_json: dict[str, Any],
        document_language: DocumentLanguage,
    ) -> tuple[LanguageAwareSignals, str]:
        """
        Возвращает signals, которые реально будут использоваться в маппинге.
        Если language.mode=auto|ru|en и signals пустые — делаем runtime-автогенерацию.
        """
        signals = self._get_signals(recipe_json, document_language)
        signals_source = "explicit"

        lang_mode = None
        lang_obj = recipe_json.get("language")
        if isinstance(lang_obj, dict):
            lang_mode = lang_obj.get("mode")

        if lang_mode in ["auto", "ru", "en"] and self._is_signals_empty(signals):
            signals, signals_source = self._auto_derive_signals(
                contract=contract,
                recipe_json=recipe_json,
                document_language=document_language,
            )
        return signals, signals_source
    
    def _get_signals(
        self, recipe_json: dict[str, Any], document_language: DocumentLanguage
    ) -> LanguageAwareSignals:
        """
        Извлекает language-aware signals из retrieval_recipe_json.
        
        Поддерживает:
        - v1 (legacy): heading_match.must/should/not как arrays без языков
        - v2 (new): lang.ru.must, lang.en.must, etc.
        
        Args:
            recipe_json: retrieval_recipe_json из контракта
            document_language: Язык документа
            
        Returns:
            LanguageAwareSignals с keywords и regex patterns
        """
        version = recipe_json.get("version", 1)
        logger.debug(
            "SectionMapping: _get_signals "
            f"(recipe_version={version}, document_language={document_language.value})"
        )
        
        if version == 2:
            # v2 (contracts): retrieval_recipe_json.mapping.signals.lang.<ru|en>.{must,should,not,regex}
            mapping_cfg = recipe_json.get("mapping") or {}
            signals_cfg = mapping_cfg.get("signals") if isinstance(mapping_cfg, dict) else {}
            signals_lang_cfg = signals_cfg.get("lang") if isinstance(signals_cfg, dict) else {}

            def _read_v2_lang(lang_key: str) -> tuple[list[str], list[str], list[str], list[str]]:
                lang_data = (
                    (signals_lang_cfg.get(lang_key) or {}) if isinstance(signals_lang_cfg, dict) else {}
                )
                must_v = self._coerce_str_list(lang_data.get("must"))
                should_v = self._coerce_str_list(lang_data.get("should"))
                not_v = self._coerce_str_list(lang_data.get("not"))
                regex_v = self._coerce_str_list(lang_data.get("regex"))
                return must_v, should_v, not_v, regex_v

            picked_lang = self._pick_recipe_lang(
                recipe_json=recipe_json, document_language=document_language
            )

            if picked_lang in ("ru", "en"):
                must_keywords, should_keywords, not_keywords, regex_patterns = _read_v2_lang(
                    picked_lang
                )
                logger.debug(
                    "SectionMapping: signals source=v2.mapping.signals.lang.%s "
                    "(must=%d, should=%d, not=%d, regex=%d)",
                    picked_lang,
                    len(must_keywords),
                    len(should_keywords),
                    len(not_keywords),
                    len(regex_patterns),
                )
                if must_keywords or should_keywords or not_keywords or regex_patterns:
                    return LanguageAwareSignals(
                        must_keywords=must_keywords,
                        should_keywords=should_keywords,
                        not_keywords=not_keywords,
                        regex_patterns=regex_patterns,
                    )

            # MIXED/UNKNOWN или структура неполная: объединяем ru+en
            ru_m, ru_s, ru_n, ru_r = _read_v2_lang("ru")
            en_m, en_s, en_n, en_r = _read_v2_lang("en")
            if ru_m or ru_s or ru_n or ru_r or en_m or en_s or en_n or en_r:
                must_keywords = list({*ru_m, *en_m})
                should_keywords = list({*ru_s, *en_s})
                not_keywords = list({*ru_n, *en_n})
                regex_patterns = ru_r + en_r
                logger.debug(
                    "SectionMapping: signals source=v2.mapping.signals.lang.ru+en "
                    "(must=%d, should=%d, not=%d, regex=%d)",
                    len(must_keywords),
                    len(should_keywords),
                    len(not_keywords),
                    len(regex_patterns),
                )
                return LanguageAwareSignals(
                    must_keywords=must_keywords,
                    should_keywords=should_keywords,
                    not_keywords=not_keywords,
                    regex_patterns=regex_patterns,
                    threshold=4.0,
                    confidence_cap=0.8,
                )

            # Fallback на старый (исторический) формат v2, если он ещё встречается:
            # retrieval_recipe_json.lang.<ru|en> + retrieval_recipe_json.regex.heading.<ru|en>
            lang_section = recipe_json.get("lang", {})
            regex_section = recipe_json.get("regex", {})

            must_keywords = []
            should_keywords = []
            not_keywords = []
            regex_patterns = []

            if document_language == DocumentLanguage.RU:
                lang_data = lang_section.get("ru", {})
                must_keywords = self._coerce_str_list(lang_data.get("must"))
                should_keywords = self._coerce_str_list(lang_data.get("should"))
                not_keywords = self._coerce_str_list(lang_data.get("not"))
                regex_patterns = self._coerce_str_list(
                    ((regex_section.get("heading") or {}).get("ru"))
                    if isinstance(regex_section, dict)
                    else None
                )
                logger.debug(
                    "SectionMapping: signals source=v2.legacy.lang.ru "
                    "(must=%d, should=%d, not=%d, regex=%d)",
                    len(must_keywords),
                    len(should_keywords),
                    len(not_keywords),
                    len(regex_patterns),
                )
                return LanguageAwareSignals(
                    must_keywords=must_keywords,
                    should_keywords=should_keywords,
                    not_keywords=not_keywords,
                    regex_patterns=regex_patterns,
                )
            if document_language == DocumentLanguage.EN:
                lang_data = lang_section.get("en", {})
                must_keywords = self._coerce_str_list(lang_data.get("must"))
                should_keywords = self._coerce_str_list(lang_data.get("should"))
                not_keywords = self._coerce_str_list(lang_data.get("not"))
                regex_patterns = self._coerce_str_list(
                    ((regex_section.get("heading") or {}).get("en"))
                    if isinstance(regex_section, dict)
                    else None
                )
                logger.debug(
                    "SectionMapping: signals source=v2.legacy.lang.en "
                    "(must=%d, should=%d, not=%d, regex=%d)",
                    len(must_keywords),
                    len(should_keywords),
                    len(not_keywords),
                    len(regex_patterns),
                )
                return LanguageAwareSignals(
                    must_keywords=must_keywords,
                    should_keywords=should_keywords,
                    not_keywords=not_keywords,
                    regex_patterns=regex_patterns,
                )

            # MIXED/UNKNOWN: объединяем RU и EN (как в предыдущей логике)
            ru_data = lang_section.get("ru", {})
            en_data = lang_section.get("en", {})
            must_keywords = list(
                {
                    *self._coerce_str_list(ru_data.get("must")),
                    *self._coerce_str_list(en_data.get("must")),
                }
            )
            should_keywords = list(
                {
                    *self._coerce_str_list(ru_data.get("should")),
                    *self._coerce_str_list(en_data.get("should")),
                }
            )
            not_keywords = list(
                {
                    *self._coerce_str_list(ru_data.get("not")),
                    *self._coerce_str_list(en_data.get("not")),
                }
            )
            regex_patterns = self._coerce_str_list(
                ((regex_section.get("heading") or {}).get("ru"))
                if isinstance(regex_section, dict)
                else None
            ) + self._coerce_str_list(
                ((regex_section.get("heading") or {}).get("en"))
                if isinstance(regex_section, dict)
                else None
            )
            logger.debug(
                "SectionMapping: signals source=v2.legacy.lang.ru+en "
                "(must=%d, should=%d, not=%d, regex=%d)",
                len(must_keywords),
                len(should_keywords),
                len(not_keywords),
                len(regex_patterns),
            )
            return LanguageAwareSignals(
                must_keywords=must_keywords,
                should_keywords=should_keywords,
                not_keywords=not_keywords,
                regex_patterns=regex_patterns,
                threshold=4.0,
                confidence_cap=0.8,
            )
        else:
            # Legacy v1 формат (без языков)
            heading_match = recipe_json.get("heading_match", {})
            regex_section = recipe_json.get("regex", {})
            logger.debug(
                "SectionMapping: signals source=legacy_v1 "
                f"(must={len(heading_match.get('must', []))}, "
                f"should={len(heading_match.get('should', []))}, "
                f"not={len(heading_match.get('not', []))}, "
                f"regex={len(regex_section.get('heading', []))})"
            )
            
            return LanguageAwareSignals(
                must_keywords=heading_match.get("must", []),
                should_keywords=heading_match.get("should", []),
                not_keywords=heading_match.get("not", []),
                regex_patterns=regex_section.get("heading", []),
            )

    async def map_sections(
        self, doc_version_id: UUID, force: bool = False
    ) -> MappingSummary:
        """
        Автоматический маппинг секций для версии документа.

        Args:
            doc_version_id: ID версии документа
            force: Если True, пересоздать все system mappings (кроме overridden)

        Returns:
            MappingSummary с результатами маппинга
        """
        logger.info(f"Начало маппинга секций для doc_version_id={doc_version_id}, force={force}")
        logger.debug(
            "SectionMapping: старт "
            f"(doc_version_id={doc_version_id}, force={force})"
        )

        # Получаем версию документа и document
        doc_version = await self.db.get(DocumentVersion, doc_version_id)
        if not doc_version:
            raise ValueError(f"DocumentVersion {doc_version_id} не найден")

        document = await self.db.get(Document, doc_version.document_id)
        if not document:
            raise ValueError(f"Document {doc_version.document_id} не найден")

        doc_type = document.doc_type

        # Получаем активные TargetSectionContracts для doc_type
        contracts_stmt = select(TargetSectionContract).where(
            TargetSectionContract.doc_type == doc_type,
            TargetSectionContract.is_active == True,
        )
        contracts_result = await self.db.execute(contracts_stmt)
        all_contracts = contracts_result.scalars().all()

        # Фильтруем контракты: только core sections для protocol + все активные для других типов
        if doc_type == DocumentType.PROTOCOL:
            # Для protocol маппим только 12 core sections
            core_section_keys = set(PROTOCOL_CORE_SECTIONS)
            contracts = [c for c in all_contracts if c.target_section in core_section_keys]
            logger.debug(
                f"SectionMapping: фильтрация core sections "
                f"(all_contracts={len(all_contracts)}, core_contracts={len(contracts)})"
            )
        else:
            # Для других типов документов маппим все активные контракты
            contracts = all_contracts

        if not contracts:
            logger.warning(f"Нет активных TargetSectionContracts для doc_type={doc_type.value}")
            return MappingSummary(
                sections_mapped_count=0,
                sections_needs_review_count=0,
                mapping_warnings=["Нет активных TargetSectionContracts для данного типа документа"],
            )

        # Получаем все anchors версии
        anchors_stmt = select(Anchor).where(Anchor.doc_version_id == doc_version_id)
        anchors_result = await self.db.execute(anchors_stmt)
        all_anchors = anchors_result.scalars().all()
        
        # Сортируем anchors по para_index
        all_anchors = sorted(
            all_anchors,
            key=lambda a: a.location_json.get("para_index", 999999) if isinstance(a.location_json, dict) else 999999
        )

        if not all_anchors:
            logger.warning(f"Нет anchors для doc_version_id={doc_version_id}")
            return MappingSummary(
                sections_mapped_count=0,
                sections_needs_review_count=0,
                mapping_warnings=["Нет anchors для маппинга"],
            )

        # Строим document outline (заголовки)
        outline = self._build_document_outline(all_anchors)
        logger.debug(
            "SectionMapping: входные данные "
            f"(contracts={len(contracts)}, anchors_total={len(all_anchors)}, headings={len(outline.headings)}, "
            f"doc_language={doc_version.document_language.value})"
        )

        # Получаем существующие маппинги
        existing_maps_stmt = select(TargetSectionMap).where(
            TargetSectionMap.doc_version_id == doc_version_id
        )
        existing_maps_result = await self.db.execute(existing_maps_stmt)
        existing_maps = {m.target_section: m for m in existing_maps_result.scalars().all()}
        logger.debug(
            "SectionMapping: existing_maps "
            f"(count={len(existing_maps)}, overridden={sum(1 for m in existing_maps.values() if m.status == SectionMapStatus.OVERRIDDEN)})"
        )

        # Маппинг для каждого контракта
        summary = MappingSummary()
        new_maps: list[TargetSectionMap] = []

        for contract in contracts:
            # Пропускаем overridden маппинги, если не force
            if not force and contract.target_section in existing_maps:
                existing_map = existing_maps[contract.target_section]
                if existing_map.status == SectionMapStatus.OVERRIDDEN:
                    logger.info(
                        f"Пропуск overridden маппинга для target_section={contract.target_section}"
                    )
                    continue

            # Специальная обработка для protocol.soa: проверяем наличие фактов SoA
            if contract.target_section == "protocol.soa":
                soa_anchor_ids = await self._get_soa_anchor_ids_from_facts(doc_version_id)
                if soa_anchor_ids:
                    # Если факты SoA найдены, создаём маппинг напрямую из anchor_ids
                    # Пропускаем поиск заголовка через ключевые слова
                    logger.info(
                        f"SectionMapping: создание маппинга для protocol.soa из фактов SoA "
                        f"(anchor_ids_count={len(soa_anchor_ids)})"
                    )
                    
                    # Создаём или обновляем маппинг с anchor_ids из фактов
                    section_map = await self._create_soa_map_from_facts(
                        doc_version_id=doc_version_id,
                        contract=contract,
                        anchor_ids=soa_anchor_ids,
                        existing_map=existing_maps.get(contract.target_section) if not force else None,
                    )
                    
                    if section_map:
                        new_maps.append(section_map)
                        if section_map.status == SectionMapStatus.MAPPED:
                            summary.sections_mapped_count += 1
                        elif section_map.status == SectionMapStatus.NEEDS_REVIEW:
                            summary.sections_needs_review_count += 1
                    continue

            # Ищем кандидатов заголовков
            heading_candidate = await self._find_heading_candidate(
                contract, outline, all_anchors, doc_version.document_language
            )

            # Создаём или обновляем маппинг
            section_map = await self._create_or_update_section_map(
                doc_version_id=doc_version_id,
                contract=contract,
                heading_candidate=heading_candidate,
                all_anchors=all_anchors,
                outline=outline,
                existing_map=existing_maps.get(contract.target_section) if not force else None,
                document_language=doc_version.document_language,
            )

            if section_map:
                new_maps.append(section_map)

                # Обновляем счётчики
                if section_map.status == SectionMapStatus.MAPPED:
                    summary.sections_mapped_count += 1
                elif section_map.status == SectionMapStatus.NEEDS_REVIEW:
                    summary.sections_needs_review_count += 1

                # Добавляем предупреждения только для core секций (для уменьшения шума)
                if section_map.notes and "No heading match" in section_map.notes:
                    # Для protocol добавляем предупреждения только для core секций
                    # Для других типов документов все секции считаются "core"
                    is_core = (
                        doc_type != DocumentType.PROTOCOL
                        or contract.target_section in PROTOCOL_CORE_SECTIONS
                    )
                    if is_core:
                        summary.mapping_warnings.append(
                            f"No heading match for {contract.target_section}"
                        )

        # Сохраняем маппинги (новые добавляем, существующие уже в сессии)
        for section_map in new_maps:
            if not section_map.id:  # Новый маппинг
                self.db.add(section_map)

        await self.db.flush()

        # Разрешаем конфликты (если один anchor попал в несколько секций)
        await self._resolve_conflicts(doc_version_id, new_maps)

        await self.db.commit()

        logger.info(
            f"Маппинг завершён для doc_version_id={doc_version_id}: "
            f"mapped={summary.sections_mapped_count}, "
            f"needs_review={summary.sections_needs_review_count}, "
            f"warnings={len(summary.mapping_warnings)}"
        )

        return summary

    def _build_document_outline(self, anchors: list[Anchor]) -> DocumentOutline:
        """
        Строит структуру документа (заголовки в порядке появления).

        Args:
            anchors: Все anchors документа

        Returns:
            DocumentOutline со списком заголовков
        """
        headings: list[tuple[Anchor, int]] = []

        for anchor in anchors:
            if anchor.content_type == AnchorContentType.HDR:
                # Определяем уровень из section_path глубины или из location_json
                level = self._extract_heading_level(anchor)
                headings.append((anchor, level))

        return DocumentOutline(headings=headings)

    def _extract_heading_level(self, anchor: Anchor) -> int:
        """
        Извлекает уровень заголовка из anchor.

        Args:
            anchor: Anchor заголовка

        Returns:
            Уровень заголовка (1..9)
        """
        # 1) Пытаемся извлечь из style (наиболее надёжно для DOCX ingestion)
        if isinstance(anchor.location_json, dict):
            style = anchor.location_json.get("style")
            if isinstance(style, str):
                m = re.match(r"^\s*Heading\s+(\d+)\s*$", style, flags=re.IGNORECASE)
                if m:
                    try:
                        lvl = int(m.group(1))
                        if 1 <= lvl <= 9:
                            return lvl
                    except ValueError:
                        pass

        # 2) Пытаемся извлечь из section_path (количество "/" + 1)
        if anchor.section_path and anchor.section_path != "ROOT":
            level = anchor.section_path.count("/") + 1
            if 1 <= level <= 9:
                return level

        # Fallback: пытаемся извлечь из нумерации в тексте
        text = anchor.text_norm
        match = re.match(r"^(\d+(?:\.\d+)*)[)\.]?\s+", text)
        if match:
            numbering_part = match.group(1)
            level = numbering_part.count(".") + 1
            if 1 <= level <= 9:
                return level

        # По умолчанию уровень 1
        return 1

    async def _find_heading_candidate(
        self,
        contract: TargetSectionContract,
        outline: DocumentOutline,
        all_anchors: list[Anchor],
        document_language: DocumentLanguage,
    ) -> HeadingCandidate | None:
        """
        Ищет кандидата заголовка для секции с учетом языка документа.

        Args:
            contract: TargetSectionContract
            outline: Структура документа
            all_anchors: Все anchors документа
            document_language: Язык документа

        Returns:
            HeadingCandidate или None
        """
        recipe = contract.retrieval_recipe_json
        if not recipe:
            return None

        # Получаем эффективные signals (explicit или auto-derived)
        signals, signals_source = self._get_effective_signals(
            contract=contract,
            recipe_json=recipe,
            document_language=document_language,
        )

        # Диагностический summary (INFO, компактно) — по каждой попытке маппинга section_key.
        # Детали top-3 кандидатов включаются только при MAPPING_DEBUG_LOGS=1.
        signals_lang = self._signals_lang_label(document_language)
        must_sample = [self._truncate(x, 48) for x in (signals.must_keywords or [])[:5]]
        should_sample = [self._truncate(x, 48) for x in (signals.should_keywords or [])[:5]]
        regex_sample = [self._truncate(x, 80) for x in (signals.regex_patterns or [])[:3]]

        logger.debug(
            "SectionMapping: signals "
            f"(target_section={contract.target_section}, version={recipe.get('version', 1)}, "
            f"must={len(signals.must_keywords)}, should={len(signals.should_keywords)}, "
            f"not={len(signals.not_keywords)}, regex={len(signals.regex_patterns)}, "
            f"threshold={signals.threshold}, confidence_cap={signals.confidence_cap}, "
            f"signals_source={signals_source})"
        )

        candidates: list[HeadingCandidate] = []

        def _get_heading_level_bounds(recipe_json: dict[str, Any]) -> tuple[int, int]:
            # Дефолт: H1–H3. Можно задать в recipe_json.mapping или recipe_json.context_build.
            min_level = 1
            max_level = 3
            mapping_cfg = recipe_json.get("mapping")
            if isinstance(mapping_cfg, dict):
                if isinstance(mapping_cfg.get("min_heading_level"), int):
                    min_level = mapping_cfg["min_heading_level"]
                if isinstance(mapping_cfg.get("max_heading_level"), int):
                    max_level = mapping_cfg["max_heading_level"]
            ctx_cfg = recipe_json.get("context_build")
            if isinstance(ctx_cfg, dict):
                if isinstance(ctx_cfg.get("min_heading_level"), int):
                    min_level = ctx_cfg["min_heading_level"]
                if isinstance(ctx_cfg.get("max_heading_level"), int):
                    max_level = ctx_cfg["max_heading_level"]
            # Safety clamp
            min_level = max(1, min(9, min_level))
            max_level = max(1, min(9, max_level))
            if min_level > max_level:
                min_level, max_level = 1, 2
            return min_level, max_level

        min_h, max_h = _get_heading_level_bounds(recipe)

        def _iter_headings_filtered(allow_any_level: bool):
            for heading_anchor, level in outline.headings:
                if allow_any_level:
                    yield heading_anchor, level
                else:
                    if min_h <= level <= max_h:
                        yield heading_anchor, level

        # Делаем 2 прохода: сначала по [min..max] (дефолт H1–H2), если нет кандидатов — по всем.
        for pass_idx, allow_any_level in enumerate([False, True], start=1):
            candidates = []
            level_mode = f"{min_h}..{max_h}" if not allow_any_level else "any"
            logger.debug(
                "SectionMapping: heading candidate pass "
                f"(target_section={contract.target_section}, pass={pass_idx}, heading_levels={level_mode})"
            )

            # Для диагностики top-3 считаем лучшие кандидаты среди ВСЕХ заголовков после фильтрации,
            # но не меняем логику отбора (threshold всё равно применяется как раньше).
            candidate_hdr_count = 0
            # (score, anchor, level, must_match_count, should_match_count, not_match_count, regex_match)
            top_scored: list[tuple[float, Anchor, int, int, int, int, bool]] = []
            max_score_seen: float | None = None
            any_regex_present = bool(signals.regex_patterns)
            any_regex_match_seen = False

            # Проходим по заголовкам
            for heading_anchor, level in _iter_headings_filtered(allow_any_level):
                candidate_hdr_count += 1
                score = 0.0
                reasons: list[str] = []
                matched_must: list[str] = []
                matched_should: list[str] = []
                matched_not: list[str] = []
                matched_regex: str | None = None

                # Нормализуем текст заголовка для матчинга keywords
                text_normalized = normalize_for_match(heading_anchor.text_norm)
                text_for_regex = normalize_for_regex(heading_anchor.text_norm)
                if logger.isEnabledFor(10):  # DEBUG
                    raw_preview = (heading_anchor.text_norm or "")[:120]
                    norm_preview = (text_normalized or "")[:120]
                    logger.debug(
                        "SectionMapping: compare heading "
                        f"(target_section={contract.target_section}, level={level}, "
                        f"anchor_id={heading_anchor.anchor_id}, "
                        f"text_raw[:120]={raw_preview!r}, text_norm_for_match[:120]={norm_preview!r})"
                    )

                # Проверка keywords must
                for keyword in signals.must_keywords:
                    keyword_normalized = normalize_for_match(keyword)
                    if keyword_normalized in text_normalized:
                        score += 2.0
                        reasons.append(f"must:'{keyword}'")
                        matched_must.append(keyword)

                # Проверка keywords should
                for keyword in signals.should_keywords:
                    keyword_normalized = normalize_for_match(keyword)
                    if keyword_normalized in text_normalized:
                        score += 1.0
                        reasons.append(f"should:'{keyword}'")
                        matched_should.append(keyword)

                # Проверка negative keywords
                for keyword in signals.not_keywords:
                    keyword_normalized = normalize_for_match(keyword)
                    if keyword_normalized in text_normalized:
                        score -= 3.0
                        reasons.append(f"not:'{keyword}'")
                        matched_not.append(keyword)

                # Проверка regex (на нормализованном тексте для regex)
                for pattern in signals.regex_patterns:
                    try:
                        if re.search(pattern, text_for_regex, re.IGNORECASE):
                            score += 3.0
                            reasons.append(f"regex:'{pattern}'")
                            matched_regex = pattern
                            any_regex_match_seen = True
                            break  # Первый матч достаточен
                    except re.error:
                        logger.warning(f"Некорректный regex pattern: {pattern}")

                # Детальный breakdown только если есть хоть какие-то совпадения
                if (matched_must or matched_should or matched_not or matched_regex) and logger.isEnabledFor(10):
                    logger.debug(
                        "SectionMapping: match breakdown "
                        f"(target_section={contract.target_section}, anchor_id={heading_anchor.anchor_id}, "
                        f"matched_must={matched_must}, matched_should={matched_should}, "
                        f"matched_not={matched_not}, matched_regex={matched_regex}, "
                        f"score={score:.1f})"
                    )

                # Если score >= threshold, добавляем кандидата
                if score >= signals.threshold:
                    candidates.append(
                        HeadingCandidate(
                            anchor_id=heading_anchor.anchor_id,
                            anchor=heading_anchor,
                            score=score,
                            reason=", ".join(reasons),
                        )
                    )
                    logger.debug(
                        "SectionMapping: heading кандидат "
                        f"(target_section={contract.target_section}, heading_level={level}, "
                        f"anchor_id={heading_anchor.anchor_id}, score={score:.1f}, reasons={reasons})"
                    )

                # Обновляем top-3 для диагностических логов (все заголовки после фильтрации).
                if max_score_seen is None or score > max_score_seen:
                    max_score_seen = score
                if self._mapping_debug_enabled():
                    top_scored.append(
                        (
                            score,
                            heading_anchor,
                            level,
                            len(matched_must),
                            len(matched_should),
                            len(matched_not),
                            bool(matched_regex),
                        )
                    )

            # Если нашли хоть что-то — выбираем top-1 и выходим
            if candidates:
                best = max(candidates, key=lambda c: c.score)
                # Summary (INFO): что использовали + какие уровни заголовков применили.
                logger.info(
                    "SectionMapping: mapping attempt summary "
                    f"(target_section={contract.target_section}, signals_lang={signals_lang}, signals_source={signals_source}, "
                    f"must_count={len(signals.must_keywords)}, should_count={len(signals.should_keywords)}, "
                    f"not_count={len(signals.not_keywords)}, regex_count={len(signals.regex_patterns)}, "
                    f"must_sample={must_sample}, should_sample={should_sample}, regex_sample={regex_sample}, "
                    f"min_heading_level={min_h}, max_heading_level={max_h}, heading_levels_used={level_mode}, "
                    f"candidate_hdr_count={candidate_hdr_count})"
                )
                # Verbose top-3 (INFO, но только при фиче-флаге)
                if self._mapping_debug_enabled():
                    top_scored_sorted = sorted(top_scored, key=lambda x: x[0], reverse=True)[:3]
                    if candidate_hdr_count == 0:
                        logger.info(
                            "SectionMapping: top_candidates "
                            f"(target_section={contract.target_section}, reason=no_candidates)"
                        )
                    elif (max_score_seen or 0.0) <= 0.0:
                        logger.info(
                            "SectionMapping: top_candidates "
                            f"(target_section={contract.target_section}, reason=all_scores_zero)"
                        )
                    else:
                        for idx, (sc, a, lvl, must_cnt, should_cnt, not_cnt, regex_match) in enumerate(
                            top_scored_sorted, start=1
                        ):
                            logger.info(
                                "SectionMapping: top_candidate "
                                f"(target_section={contract.target_section}, rank={idx}, heading_level={lvl}, "
                                f"anchor_id={a.anchor_id}, section_path={a.section_path}, "
                                f"score={sc:.2f}, regex_match={regex_match}, "
                                f"must_match_count={must_cnt}, should_match_count={should_cnt}, not_match_count={not_cnt}, "
                                f"heading_preview={self._truncate(a.text_norm or '', 80)!r})"
                            )
                logger.info(
                    "SectionMapping: выбран заголовок "
                    f"(target_section={contract.target_section}, anchor_id={best.anchor_id}, "
                    f"score={best.score:.1f}, reason={best.reason}, heading_levels_used={level_mode}, "
                    f"signals_source={signals_source})"
                )
                return best

            # Если кандидатов нет — логируем summary + (опционально) top-3 причины.
            # Логику маппинга не меняем: просто идём на следующий pass.
            logger.info(
                "SectionMapping: mapping attempt summary "
                f"(target_section={contract.target_section}, signals_lang={signals_lang}, signals_source={signals_source}, "
                f"must_count={len(signals.must_keywords)}, should_count={len(signals.should_keywords)}, "
                f"not_count={len(signals.not_keywords)}, regex_count={len(signals.regex_patterns)}, "
                f"must_sample={must_sample}, should_sample={should_sample}, regex_sample={regex_sample}, "
                f"min_heading_level={min_h}, max_heading_level={max_h}, heading_levels_used={level_mode}, "
                f"candidate_hdr_count={candidate_hdr_count})"
            )
            if self._mapping_debug_enabled():
                if candidate_hdr_count == 0:
                    logger.info(
                        "SectionMapping: top_candidates "
                        f"(target_section={contract.target_section}, reason=no_candidates)"
                    )
                elif (max_score_seen or 0.0) <= 0.0:
                    logger.info(
                        "SectionMapping: top_candidates "
                        f"(target_section={contract.target_section}, reason=all_scores_zero)"
                    )
                else:
                    top_scored_sorted = sorted(top_scored, key=lambda x: x[0], reverse=True)[:3]
                    for idx, (sc, a, lvl, must_cnt, should_cnt, not_cnt, regex_match) in enumerate(
                        top_scored_sorted, start=1
                    ):
                        logger.info(
                            "SectionMapping: top_candidate "
                            f"(target_section={contract.target_section}, rank={idx}, heading_level={lvl}, "
                            f"anchor_id={a.anchor_id}, section_path={a.section_path}, "
                            f"score={sc:.2f}, regex_match={regex_match}, "
                            f"must_match_count={must_cnt}, should_match_count={should_cnt}, not_match_count={not_cnt}, "
                            f"heading_preview={self._truncate(a.text_norm or '', 80)!r})"
                        )

            # Причина отсутствия кандидатов на этом проходе (только DEBUG).
            if not candidates and logger.isEnabledFor(10):
                if candidate_hdr_count == 0:
                    reason = "all_filtered_by_heading_level"
                elif any_regex_present and not any_regex_match_seen:
                    reason = "regex_present_but_no_match"
                else:
                    reason = "no_candidates_above_threshold"
                logger.debug(
                    "SectionMapping: no_heading_candidate_reason "
                    f"(target_section={contract.target_section}, pass={pass_idx}, reason={reason}, "
                    f"heading_levels_used={level_mode}, threshold={signals.threshold})"
                )

        # Fallback для protocol.soa: ищем по фактам или cell anchors
        if contract.target_section == "protocol.soa":
            fb = await self._find_soa_fallback(all_anchors)
            if fb:
                logger.info(
                    "SectionMapping: SOA fallback выбран "
                    f"(target_section={contract.target_section}, anchor_id={fb.anchor_id}, reason={fb.reason})"
                )
            else:
                logger.debug("SectionMapping: SOA fallback не нашёл кандидата")
            return fb

        return None

    async def _get_soa_anchor_ids_from_facts(
        self, doc_version_id: UUID
    ) -> list[str] | None:
        """
        Получает anchor_ids всех ячеек (CELL) из фактов SoA для данной версии документа.
        
        Если SoAExtractionService уже нашёл таблицу, это видно по наличию фактов
        fact_type='soa' для этой версии. В этом случае возвращаем все anchor_ids
        из FactEvidence для этих фактов.
        
        Args:
            doc_version_id: ID версии документа
            
        Returns:
            Список anchor_ids или None, если факты SoA не найдены
        """
        # Проверяем наличие фактов с fact_type='soa' для данной версии
        facts_stmt = select(Fact).where(
            Fact.fact_type == "soa",
            Fact.created_from_doc_version_id == doc_version_id,
        )
        facts_result = await self.db.execute(facts_stmt)
        soa_facts = facts_result.scalars().all()
        
        if not soa_facts:
            logger.debug(
                f"SectionMapping: факты SoA не найдены для doc_version_id={doc_version_id}"
            )
            return None
        
        # Собираем все anchor_ids из FactEvidence для всех фактов SoA
        fact_ids = [fact.id for fact in soa_facts]
        evidence_stmt = select(FactEvidence.anchor_id).where(
            FactEvidence.fact_id.in_(fact_ids)  # type: ignore
        )
        evidence_result = await self.db.execute(evidence_stmt)
        anchor_ids = list(set(evidence_result.scalars().all()))  # Убираем дубликаты
        
        if not anchor_ids:
            logger.debug(
                f"SectionMapping: anchor_ids не найдены в FactEvidence для фактов SoA "
                f"(doc_version_id={doc_version_id}, fact_count={len(soa_facts)})"
            )
            return None
        
        logger.info(
            f"SectionMapping: найдены anchor_ids из фактов SoA "
            f"(doc_version_id={doc_version_id}, fact_count={len(soa_facts)}, "
            f"anchor_ids_count={len(anchor_ids)})"
        )
        
        return anchor_ids

    async def _find_soa_fallback(
        self, all_anchors: list[Anchor]
    ) -> HeadingCandidate | None:
        """
        Fallback для поиска SoA секции (по cell anchors или фактам).

        Args:
            all_anchors: Все anchors документа

        Returns:
            HeadingCandidate или None
        """
        # Ищем cell anchors (признак SoA таблицы)
        cell_anchors = [
            a for a in all_anchors if a.content_type == AnchorContentType.CELL
        ]

        if cell_anchors:
            # Берём первый hdr anchor перед cell anchors
            first_cell_para_index = min(
                a.location_json.get("para_index", 999999) for a in cell_anchors
            )

            for anchor in all_anchors:
                if (
                    anchor.content_type == AnchorContentType.HDR
                    and anchor.location_json.get("para_index", 0) < first_cell_para_index
                ):
                    # Ищем ближайший заголовок перед таблицей
                    text_lower = anchor.text_norm.lower()
                    if any(
                        kw in text_lower
                        for kw in ["schedule", "activities", "soa", "visits", "таблица"]
                    ):
                        return HeadingCandidate(
                            anchor_id=anchor.anchor_id,
                            anchor=anchor,
                            score=2.0,
                            reason="soa_fallback:cell_anchors",
                        )

        return None

    async def _create_soa_map_from_facts(
        self,
        doc_version_id: UUID,
        contract: TargetSectionContract,
        anchor_ids: list[str],
        existing_map: TargetSectionMap | None,
    ) -> TargetSectionMap | None:
        """
        Создаёт или обновляет TargetSectionMap для protocol.soa из фактов SoA.
        
        Args:
            doc_version_id: ID версии документа
            contract: TargetSectionContract для protocol.soa
            anchor_ids: Список anchor_ids из фактов SoA
            existing_map: Существующий маппинг (если есть)
            
        Returns:
            TargetSectionMap или None
        """
        if existing_map and existing_map.status == SectionMapStatus.OVERRIDDEN:
            # Не трогаем overridden
            logger.debug(
                "SectionMapping: skip overridden (soa from facts) "
                f"(target_section={contract.target_section})"
            )
            return None
        
        # Устанавливаем confidence=0.95, если таблица была успешно извлечена
        confidence = 0.95
        status = SectionMapStatus.MAPPED
        notes = f"Автоматический маппинг из фактов SoA (anchor_ids_count={len(anchor_ids)})"
        
        if existing_map:
            # Обновляем существующий
            existing_map.anchor_ids = anchor_ids
            existing_map.confidence = confidence
            existing_map.status = status
            existing_map.notes = notes
            existing_map.mapped_by = SectionMapMappedBy.SYSTEM
            logger.info(
                "SectionMapping: soa mapped from facts (update) "
                f"(target_section={contract.target_section}, status={status.value}, "
                f"confidence={confidence:.2f}, anchors={len(anchor_ids)})"
            )
            return existing_map
        else:
            # Создаём новый
            section_map = TargetSectionMap(
                doc_version_id=doc_version_id,
                target_section=contract.target_section,
                anchor_ids=anchor_ids,
                chunk_ids=None,
                confidence=confidence,
                status=status,
                mapped_by=SectionMapMappedBy.SYSTEM,
                notes=notes,
            )
            logger.info(
                "SectionMapping: soa mapped from facts (create) "
                f"(target_section={contract.target_section}, status={status.value}, "
                f"confidence={confidence:.2f}, anchors={len(anchor_ids)})"
            )
            return section_map

    async def _create_or_update_section_map(
        self,
        doc_version_id: UUID,
        contract: TargetSectionContract,
        heading_candidate: HeadingCandidate | None,
        all_anchors: list[Anchor],
        outline: DocumentOutline,
        existing_map: TargetSectionMap | None,
        document_language: DocumentLanguage,
    ) -> TargetSectionMap | None:
        """
        Создаёт или обновляет TargetSectionMap.

        Args:
            doc_version_id: ID версии документа
            contract: TargetSectionContract
            heading_candidate: Кандидат заголовка или None
            all_anchors: Все anchors документа
            outline: Структура документа
            existing_map: Существующий маппинг (если есть)
            document_language: Язык документа

        Returns:
            TargetSectionMap или None
        """
        if not heading_candidate:
            # Нет кандидата → needs_review
            if existing_map and existing_map.status == SectionMapStatus.OVERRIDDEN:
                # Не трогаем overridden
                logger.debug(
                    "SectionMapping: skip overridden (no candidate) "
                    f"(target_section={contract.target_section})"
                )
                logger.info(
                    "SectionMapping: final_decision "
                    f"(target_section={contract.target_section}, status=skipped, "
                    f"reason=overridden_no_candidate)"
                )
                return None

            if existing_map:
                # Обновляем существующий
                existing_map.anchor_ids = []
                existing_map.confidence = 0.0
                existing_map.status = SectionMapStatus.NEEDS_REVIEW
                existing_map.notes = "No heading match"
                existing_map.mapped_by = SectionMapMappedBy.SYSTEM
                logger.info(
                    "SectionMapping: no heading match -> needs_review (update) "
                    f"(target_section={contract.target_section})"
                )
                logger.info(
                    "SectionMapping: final_decision "
                    f"(target_section={contract.target_section}, status=needs_review, confidence=0.00, "
                    f"selected_heading_anchor_id=None, anchor_ids_count=0)"
                )
                return existing_map
            else:
                # Создаём новый
                section_map = TargetSectionMap(
                    doc_version_id=doc_version_id,
                    target_section=contract.target_section,
                    anchor_ids=[],
                    chunk_ids=None,
                    confidence=0.0,
                    status=SectionMapStatus.NEEDS_REVIEW,
                    mapped_by=SectionMapMappedBy.SYSTEM,
                    notes="No heading match",
                )
                logger.info(
                    "SectionMapping: no heading match -> needs_review (create) "
                    f"(target_section={contract.target_section})"
                )
                logger.info(
                    "SectionMapping: final_decision "
                    f"(target_section={contract.target_section}, status=needs_review, confidence=0.00, "
                    f"selected_heading_anchor_id=None, anchor_ids_count=0)"
                )
                return section_map

        # Есть кандидат → захватываем блок
        anchor_ids, confidence, notes, by_zone_stats = self._capture_heading_block(
            heading_candidate, all_anchors, outline, contract, document_language
        )
        logger.debug(
            "SectionMapping: captured block "
            f"(target_section={contract.target_section}, heading_anchor_id={heading_candidate.anchor_id}, "
            f"anchors_in_block={len(anchor_ids)}, by_zone={by_zone_stats})"
        )

        # LLM Triage: если confidence в диапазоне 0.4-0.65 (зона сомнения)
        if 0.4 <= confidence <= 0.65:
            logger.info(
                f"SectionMapping: LLM Triage запущен "
                f"(target_section={contract.target_section}, confidence={confidence:.2f})"
            )
            
            # Получаем сниппет текста
            snippet = self._get_snippet(heading_candidate.anchor, all_anchors)
            
            # Получаем список доступных topic_keys (target_section) для данного doc_type
            doc_version = await self.db.get(DocumentVersion, doc_version_id)
            if doc_version:
                document = await self.db.get(Document, doc_version.document_id)
                if document:
                    contracts_stmt = select(TargetSectionContract).where(
                        TargetSectionContract.doc_type == document.doc_type,
                        TargetSectionContract.is_active == True,
                    )
                    contracts_result = await self.db.execute(contracts_stmt)
                    all_contracts = contracts_result.scalars().all()
                    available_topic_keys = [c.target_section for c in all_contracts if c.target_section]
                    
                    # Вызываем LLM Triage
                    selected_topic_key, rationale = await self._llm_triage(
                        heading_text=heading_candidate.anchor.text_norm or "",
                        snippet=snippet,
                        available_topic_keys=available_topic_keys,
                        current_topic_key=contract.target_section,
                    )
                    
                    # Применяем результат LLM только если есть четкое обоснование
                    if selected_topic_key and rationale:
                        # Если LLM выбрала другой topic_key, обновляем contract (но это сложно)
                        # Пока просто повышаем confidence, если LLM подтвердила выбор
                        if selected_topic_key == contract.target_section:
                            # LLM подтвердила текущий выбор - повышаем confidence
                            confidence = min(0.75, confidence + 0.15)
                            notes = f"{notes}; LLM Triage подтвердил (rationale: {rationale[:100]})"
                            logger.info(
                                f"SectionMapping: LLM Triage подтвердил выбор "
                                f"(target_section={contract.target_section}, "
                                f"confidence={confidence:.2f}, rationale_len={len(rationale)})"
                            )
                        elif selected_topic_key != "unknown":
                            # LLM выбрала другой topic_key - оставляем текущий, но повышаем confidence немного
                            confidence = min(0.7, confidence + 0.1)
                            notes = f"{notes}; LLM Triage предложил {selected_topic_key} (rationale: {rationale[:100]})"
                            logger.info(
                                f"SectionMapping: LLM Triage предложил другой topic_key "
                                f"(current={contract.target_section}, suggested={selected_topic_key}, "
                                f"confidence={confidence:.2f})"
                            )
                    else:
                        logger.debug(
                            f"SectionMapping: LLM Triage не дал четкого обоснования "
                            f"(target_section={contract.target_section})"
                        )

        # Определяем status
        if confidence >= 0.7:
            status = SectionMapStatus.MAPPED
        else:
            status = SectionMapStatus.NEEDS_REVIEW

        if existing_map and existing_map.status == SectionMapStatus.OVERRIDDEN:
            # Не трогаем overridden
            logger.debug(
                "SectionMapping: skip overridden (has candidate) "
                f"(target_section={contract.target_section}, heading_anchor_id={heading_candidate.anchor_id})"
            )
            logger.info(
                "SectionMapping: final_decision "
                f"(target_section={contract.target_section}, status=skipped, "
                f"reason=overridden_has_candidate, selected_heading_anchor_id={heading_candidate.anchor_id})"
            )
            return None

        if existing_map:
            # Обновляем существующий
            existing_map.anchor_ids = anchor_ids
            existing_map.confidence = confidence
            existing_map.status = status
            existing_map.notes = notes
            existing_map.mapped_by = SectionMapMappedBy.SYSTEM
            # Сохраняем статистику по зонам в notes (в будущем можно добавить отдельное поле)
            if by_zone_stats:
                zone_stats_str = ", ".join([f"{zone}:{count}" for zone, count in by_zone_stats.items()])
                existing_map.notes = f"{notes}; by_zone: {zone_stats_str}" if notes else f"by_zone: {zone_stats_str}"
            logger.info(
                "SectionMapping: mapped (update) "
                f"(target_section={contract.target_section}, status={status.value}, confidence={confidence:.2f}, "
                f"anchors={len(anchor_ids)})"
            )
            logger.info(
                "SectionMapping: final_decision "
                f"(target_section={contract.target_section}, status={'mapped' if status == SectionMapStatus.MAPPED else 'needs_review'}, "
                f"confidence={confidence:.2f}, selected_heading_anchor_id={heading_candidate.anchor_id}, "
                f"anchor_ids_count={len(anchor_ids)}, anchor_ids_first={anchor_ids[0] if anchor_ids else None}, "
                f"anchor_ids_last={anchor_ids[-1] if anchor_ids else None}, chunk_ids=None)"
            )
            return existing_map
        else:
            # Создаём новый
            # Сохраняем статистику по зонам в notes
            notes_with_stats = notes
            if by_zone_stats:
                zone_stats_str = ", ".join([f"{zone}:{count}" for zone, count in by_zone_stats.items()])
                notes_with_stats = f"{notes}; by_zone: {zone_stats_str}" if notes else f"by_zone: {zone_stats_str}"
            
            section_map = TargetSectionMap(
                doc_version_id=doc_version_id,
                target_section=contract.target_section,
                anchor_ids=anchor_ids,
                chunk_ids=None,
                confidence=confidence,
                status=status,
                mapped_by=SectionMapMappedBy.SYSTEM,
                notes=notes_with_stats,
            )
            logger.info(
                "SectionMapping: mapped (create) "
                f"(target_section={contract.target_section}, status={status.value}, confidence={confidence:.2f}, "
                f"anchors={len(anchor_ids)})"
            )
            logger.info(
                "SectionMapping: final_decision "
                f"(target_section={contract.target_section}, status={'mapped' if status == SectionMapStatus.MAPPED else 'needs_review'}, "
                f"confidence={confidence:.2f}, selected_heading_anchor_id={heading_candidate.anchor_id}, "
                f"anchor_ids_count={len(anchor_ids)}, anchor_ids_first={anchor_ids[0] if anchor_ids else None}, "
                f"anchor_ids_last={anchor_ids[-1] if anchor_ids else None}, chunk_ids=None)"
            )
            return section_map

    def _capture_heading_block(
        self,
        heading_candidate: HeadingCandidate,
        all_anchors: list[Anchor],
        outline: DocumentOutline,
        contract: TargetSectionContract,
        document_language: DocumentLanguage,
    ) -> tuple[list[str], float, str, dict[str, int]]:
        """
        Захватывает блок секции от заголовка до следующего заголовка того же/выше уровня.
        Использует prefer/fallback зоны из контракта для фильтрации и приоритизации.

        Args:
            heading_candidate: Кандидат заголовка
            all_anchors: Все anchors документа
            outline: Структура документа
            contract: TargetSectionContract
            document_language: Язык документа

        Returns:
            (anchor_ids, confidence, notes, by_zone_stats) - статистика по зонам
        """
        heading_anchor = heading_candidate.anchor
        heading_level = self._extract_heading_level(heading_anchor)

        # Находим позицию заголовка в списке anchors
        heading_para_index = heading_anchor.location_json.get("para_index", 0)

        # Находим следующий заголовок с level <= heading_level
        end_para_index = None
        for anchor, level in outline.headings:
            anchor_para_index = anchor.location_json.get("para_index", 0)
            if anchor_para_index > heading_para_index and level <= heading_level:
                end_para_index = anchor_para_index
                break
        logger.debug(
            "SectionMapping: capture_heading_block bounds "
            f"(target_section={contract.target_section}, heading_anchor_id={heading_anchor.anchor_id}, "
            f"heading_level={heading_level}, start_para_index={heading_para_index}, end_para_index={end_para_index})"
        )

        # Собираем все anchors между start и end
        candidate_anchors: list[Anchor] = []
        for anchor in all_anchors:
            para_index = anchor.location_json.get("para_index", 0)
            if para_index >= heading_para_index:
                if end_para_index is None or para_index < end_para_index:
                    candidate_anchors.append(anchor)
                else:
                    break
        
        # Получаем prefer/fallback зоны из контракта
        passport = normalize_passport(
            required_facts_json=contract.required_facts_json,
            allowed_sources_json=contract.allowed_sources_json,
            retrieval_recipe_json=contract.retrieval_recipe_json,
            qc_ruleset_json=contract.qc_ruleset_json,
        )
        prefer_zones = passport.retrieval_recipe.prefer_source_zones or []
        fallback_zones = passport.retrieval_recipe.fallback_source_zones or []
        
        # Применяем topic_zone_priors, если есть required_topics в контракте
        zone_config = get_zone_config_service()
        topic_key = None
        # Извлекаем topic_key из target_section (например, "protocol.soa" -> "schedule_of_activities")
        if contract.target_section:
            # Простая эвристика: извлекаем последнюю часть target_section
            section_parts = contract.target_section.split(".")
            if len(section_parts) > 1:
                last_part = section_parts[-1]
                # Маппинг на topic_key (можно расширить)
                topic_mapping = {
                    "soa": "schedule_of_activities",
                    "study_design": "study_design",
                    "endpoints": "study_objectives",
                    "eligibility": "eligibility_criteria",
                }
                topic_key = topic_mapping.get(last_part)
        
        # Приоритизируем зоны с учётом topic_zone_priors
        if topic_key:
            prefer_zones = zone_config.apply_topic_zone_priors(prefer_zones, topic_key)
            fallback_zones = zone_config.apply_topic_zone_priors(fallback_zones, topic_key)
        
        # Фильтруем и приоритизируем anchors по зонам
        prefer_zone_anchors = [a for a in candidate_anchors if a.source_zone in prefer_zones]
        fallback_zone_anchors = [
            a for a in candidate_anchors
            if a.source_zone in fallback_zones and a not in prefer_zone_anchors
        ]
        other_anchors = [
            a for a in candidate_anchors
            if a not in prefer_zone_anchors and a not in fallback_zone_anchors
        ]
        
        # Собираем финальный список: сначала prefer, затем fallback, затем остальные
        final_anchors = prefer_zone_anchors + fallback_zone_anchors + other_anchors
        anchor_ids = [a.anchor_id for a in final_anchors]
        
        # Собираем статистику по зонам
        by_zone_stats: dict[str, int] = {}
        for anchor in final_anchors:
            zone = anchor.source_zone or "unknown"
            by_zone_stats[zone] = by_zone_stats.get(zone, 0) + 1
        
        logger.debug(
            "SectionMapping: capture_heading_block collected "
            f"(target_section={contract.target_section}, anchors_in_block={len(anchor_ids)}, "
            f"prefer_zones={prefer_zones}, fallback_zones={fallback_zones}, "
            f"by_zone={by_zone_stats})"
        )

        # Вычисляем confidence с учетом языка
        recipe = contract.retrieval_recipe_json
        signals, _signals_source = self._get_effective_signals(
            contract=contract,
            recipe_json=recipe or {},
            document_language=document_language,
        )

        confidence = 0.5  # Базовый confidence

        # Проверяем regex match
        has_regex_match = False
        text_for_regex = normalize_for_regex(heading_anchor.text_norm)
        for pattern in signals.regex_patterns:
            try:
                if re.search(pattern, text_for_regex, re.IGNORECASE):
                    has_regex_match = True
                    break
            except re.error:
                pass

        # Проверяем must match (на нормализованном тексте)
        text_normalized = normalize_for_match(heading_anchor.text_norm)
        has_must_match = any(
            normalize_for_match(kw) in text_normalized for kw in signals.must_keywords
        )

        # Если must_keywords не дали совпадения, проверяем should_keywords со штрафом
        has_should_match = False
        if not has_must_match:
            has_should_match = any(
                normalize_for_match(kw) in text_normalized for kw in signals.should_keywords
            )

        if has_regex_match and has_must_match:
            confidence = 0.9
        elif has_regex_match or has_must_match:
            confidence = 0.7
        elif has_should_match:
            # Fallback на should_keywords со штрафом к confidence
            confidence = 0.5
        else:
            confidence = 0.5

        # Применяем confidence cap для mixed/unknown
        if signals.confidence_cap is not None:
            confidence = min(confidence, signals.confidence_cap)

        notes = f"Matched heading: {heading_anchor.text_norm[:100]} (score={heading_candidate.score:.1f}, {heading_candidate.reason})"
        logger.debug(
            "SectionMapping: capture_heading_block confidence "
            f"(target_section={contract.target_section}, confidence={confidence:.2f}, "
            f"has_regex_match={has_regex_match}, has_must_match={has_must_match}, "
            f"has_should_match={has_should_match}, confidence_cap={signals.confidence_cap})"
        )

        return anchor_ids, confidence, notes, by_zone_stats

    def _get_snippet(self, heading_anchor: Anchor, all_anchors: list[Anchor]) -> str:
        """
        Получает сниппет (1-2 первых paragraph после заголовка, до 300 символов).

        Args:
            heading_anchor: Anchor заголовка
            all_anchors: Все anchors документа

        Returns:
            Сниппет текста
        """
        heading_para_index = heading_anchor.location_json.get("para_index", 0) if isinstance(heading_anchor.location_json, dict) else 0
        snippet_parts: list[str] = []
        total_length = 0

        for anchor in all_anchors:
            para_index = anchor.location_json.get("para_index", 0) if isinstance(anchor.location_json, dict) else 0
            if para_index <= heading_para_index:
                continue

            # Берём только первые 2 paragraph после заголовка
            if anchor.content_type == AnchorContentType.P:
                text = anchor.text_norm[:300] if anchor.text_norm else ""
                if total_length + len(text) > 300:
                    text = text[: 300 - total_length]
                snippet_parts.append(text)
                total_length += len(text)
                if len(snippet_parts) >= 2 or total_length >= 300:
                    break

        return " ".join(snippet_parts)

    async def _llm_triage(
        self,
        heading_text: str,
        snippet: str,
        available_topic_keys: list[str],
        current_topic_key: str,
    ) -> tuple[str | None, str | None]:
        """
        Выполняет LLM триаж для выбора наиболее подходящего Topic Key.

        Args:
            heading_text: Текст заголовка
            snippet: Сниппет текста после заголовка
            available_topic_keys: Список доступных Topic Keys
            current_topic_key: Текущий Topic Key (который был выбран алгоритмически)

        Returns:
            (selected_topic_key, rationale) или (None, None) если LLM не дала четкого обоснования
        """
        # Проверяем, что LLM настроен
        if not settings.llm_provider or not settings.llm_base_url or not settings.llm_api_key:
            logger.debug("SectionMapping: LLM не настроен, пропускаем триаж")
            return None, None

        try:
            import json
            import httpx
            import uuid
            
            llm_client = LLMClient()
            request_id = str(uuid.uuid4())
            
            # Формируем короткий промпт
            system_prompt = """Ты помощник для маппинга секций клинических протоколов.
Твоя задача: выбрать наиболее подходящий Topic Key из списка для данного текста.
Если ничего не подходит, ответь 'unknown'.
Отвечай ТОЛЬКО в формате JSON: {"topic_key": "...", "rationale": "..."}"""

            user_prompt_text = f"""Заголовок: {heading_text}

Сниппет текста: {snippet}

Доступные Topic Keys: {', '.join(available_topic_keys)}
Текущий выбор: {current_topic_key}

Выбери наиболее подходящий Topic Key из списка. Если ничего не подходит, ответь 'unknown'."""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt_text},
            ]
            
            logger.debug(
                f"SectionMapping: LLM Triage запрос "
                f"(request_id={request_id}, heading={heading_text[:50]}, "
                f"current_topic_key={current_topic_key})"
            )

            # Вызываем LLM в зависимости от провайдера
            if llm_client.provider.value == "azure_openai":
                url = f"{llm_client.base_url}/openai/deployments/{llm_client.model}/chat/completions"
                headers = {
                    "api-key": llm_client.api_key,
                    "Content-Type": "application/json",
                }
                payload = {
                    "messages": messages,
                    "temperature": 0.0,  # Детерминированность для триажа
                    "max_tokens": 500,
                }
            elif llm_client.provider.value == "openai_compatible":
                url = f"{llm_client.base_url}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {llm_client.api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": llm_client.model,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": 500,
                }
            else:  # local
                url = f"{llm_client.base_url}/v1/chat/completions"
                headers = {"Content-Type": "application/json"}
                if llm_client.api_key:
                    headers["Authorization"] = f"Bearer {llm_client.api_key}"
                payload = {
                    "model": llm_client.model,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": 500,
                }

            async with httpx.AsyncClient(timeout=llm_client.timeout_sec) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                response_data = response.json()

            # Парсим ответ
            content = response_data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                logger.warning("SectionMapping: LLM Triage вернул пустой ответ")
                return None, None

            # Извлекаем JSON из ответа
            content_str = str(content).strip()
            if content_str.startswith("```"):
                parts = content_str.split("```")
                if len(parts) >= 3:
                    inner = parts[1].strip()
                    if "\n" in inner:
                        first_line, rest = inner.split("\n", 1)
                        if first_line.strip().lower() == "json":
                            inner = rest.strip()
                    content_str = inner.strip()

            # Ищем JSON объект
            if "{" in content_str and "}" in content_str:
                l = content_str.find("{")
                r = content_str.rfind("}")
                if l != -1 and r != -1 and r > l:
                    content_str = content_str[l : r + 1]

            # Парсим JSON
            try:
                response_json = json.loads(content_str)
                selected_topic_key = response_json.get("topic_key")
                rationale = response_json.get("rationale", "")

                # Проверяем, что есть четкое обоснование (не пустое и не слишком короткое)
                if not rationale or len(rationale.strip()) < 10:
                    logger.debug(
                        f"SectionMapping: LLM Triage вернул недостаточное обоснование "
                        f"(rationale_len={len(rationale) if rationale else 0})"
                    )
                    return None, None

                # Проверяем, что выбранный ключ валиден
                if selected_topic_key and selected_topic_key != "unknown":
                    if selected_topic_key not in available_topic_keys:
                        logger.warning(
                            f"SectionMapping: LLM Triage вернул невалидный topic_key: {selected_topic_key}"
                        )
                        return None, None

                logger.info(
                    f"SectionMapping: LLM Triage результат "
                    f"(request_id={request_id}, selected_topic_key={selected_topic_key}, "
                    f"rationale_len={len(rationale)})"
                )

                return selected_topic_key, rationale

            except json.JSONDecodeError as e:
                logger.warning(f"SectionMapping: LLM Triage ошибка парсинга JSON: {e}")
                return None, None

        except Exception as e:
            logger.warning(f"SectionMapping: LLM Triage ошибка: {e}", exc_info=True)
            return None, None

    async def _resolve_conflicts(
        self, doc_version_id: UUID, section_maps: list[SectionMap]
    ) -> None:
        """
        Разрешает конфликты маппинга (если один anchor попал в несколько секций).

        Args:
            doc_version_id: ID версии документа
            section_maps: Список маппингов
        """
        # Строим индекс: anchor_id -> список section_maps
        anchor_to_maps: dict[str, list[SectionMap]] = {}
        for section_map in section_maps:
            if section_map.anchor_ids:
                for anchor_id in section_map.anchor_ids:
                    if anchor_id not in anchor_to_maps:
                        anchor_to_maps[anchor_id] = []
                    anchor_to_maps[anchor_id].append(section_map)

        # Находим конфликты
        conflicts: list[tuple[str, list[SectionMap]]] = [
            (anchor_id, maps) for anchor_id, maps in anchor_to_maps.items() if len(maps) > 1
        ]

        if not conflicts:
            return

        logger.info(f"Найдено {len(conflicts)} конфликтов маппинга")

        # Разрешаем конфликты: предпочитаем секцию с более высоким confidence
        for anchor_id, maps in conflicts:
            # Сортируем по confidence (убывание)
            maps_sorted = sorted(maps, key=lambda m: m.confidence, reverse=True)

            # Оставляем anchor_id только в секции с максимальным confidence
            winner = maps_sorted[0]
            losers = maps_sorted[1:]

            for loser in losers:
                if loser.anchor_ids and anchor_id in loser.anchor_ids:
                    loser.anchor_ids.remove(anchor_id)
                    # Обновляем notes
                    if loser.notes:
                        loser.notes += f"; Conflict resolved: {anchor_id} -> {winner.section_key}"
                    else:
                        loser.notes = f"Conflict resolved: {anchor_id} -> {winner.section_key}"

                    # Если конфликт сильный, ставим needs_review
                    if loser.confidence >= 0.7:
                        loser.status = SectionMapStatus.NEEDS_REVIEW
