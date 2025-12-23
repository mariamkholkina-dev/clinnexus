from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.enums import (
    CitationPolicy,
    DocumentLanguage,
    DocumentType,
    SectionMapMappedBy,
    SectionMapStatus,
)
from app.core.section_standardization import (
    CANONICAL_SECTION_KEYS,
    get_prefer_source_zones,
    is_valid_target_section,
)


class RequiredFactSpecMVP(BaseModel):
    """MVP спецификация одного required fact."""

    model_config = ConfigDict(extra="ignore")

    fact_key: str
    required: bool = True
    min_status: str = "extracted"
    expected_type: str | None = None
    unit_allowed: list[str] | None = None
    aliases: list[str] | None = None
    family: str | None = None


class RequiredFactsMVP(BaseModel):
    """MVP required_facts_json: только список facts[]."""

    model_config = ConfigDict(extra="ignore")

    facts: list[RequiredFactSpecMVP] = Field(default_factory=list)


class DependencySourceMVP(BaseModel):
    """MVP dependency source для allowed_sources_json."""

    model_config = ConfigDict(extra="ignore")

    doc_type: DocumentType
    section_keys: list[str] = Field(default_factory=list)
    required: bool = True
    role: str = "primary"  # primary|supporting
    precedence: int = 0
    min_mapping_confidence: float = 0.0
    allowed_content_types: list[str] = Field(default_factory=list)  # например ["p","cell"]


class DocumentScopeMVP(BaseModel):
    model_config = ConfigDict(extra="ignore")

    same_study_only: bool = True
    allow_superseded: bool = False


class AllowedSourcesMVP(BaseModel):
    """MVP allowed_sources_json."""

    model_config = ConfigDict(extra="ignore")

    dependency_sources: list[DependencySourceMVP] = Field(default_factory=list)
    document_scope: DocumentScopeMVP = Field(default_factory=DocumentScopeMVP)


class RetrievalLanguageMVP(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: str = "auto"  # auto|ru|en
    prefer_language: str | None = None  # ru|en|auto (приоритет языка для извлечения)


class ContextBuildMVP(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_chars: int | None = None


class FallbackSearchMVP(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query_templates: dict[str, list[str]] | None = None  # {"ru": [...], "en": [...]}


class SecurityMVP(BaseModel):
    model_config = ConfigDict(extra="ignore")

    secure_mode_required: bool = True


class RetrievalRecipeMVP(BaseModel):
    """MVP retrieval_recipe_json (Lean)."""

    model_config = ConfigDict(extra="ignore")

    language: RetrievalLanguageMVP = Field(default_factory=RetrievalLanguageMVP)
    context_build: ContextBuildMVP = Field(default_factory=ContextBuildMVP)
    prefer_content_types: list[str] | None = None
    prefer_source_zones: list[str] | None = None  # Приоритетные source_zone для извлечения evidence
    fallback_source_zones: list[str] | None = None  # Резервные source_zone, если prefer пуст
    fallback_search: FallbackSearchMVP | None = None
    security: SecurityMVP = Field(default_factory=SecurityMVP)


class GatePolicyMVP(BaseModel):
    model_config = ConfigDict(extra="ignore")

    on_missing_required_fact: str = "blocked"  # blocked|fail|warn
    on_low_mapping_confidence: str = "blocked"
    on_citation_missing: str = "fail"


class QCRulesetMVP(BaseModel):
    """MVP qc_ruleset_json."""

    model_config = ConfigDict(extra="ignore")

    phases: list[str] = Field(default_factory=lambda: ["input_qc", "citation_qc"])
    gate_policy: GatePolicyMVP = Field(default_factory=GatePolicyMVP)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    numbers_match_facts: bool = False
    check_zone_conflicts: bool = False  # Проверка конфликтов фактов из разных source_zone


class ViewTopicsConfig(BaseModel):
    """Конфигурация required_topics для view_key."""

    model_config = ConfigDict(extra="ignore")

    view_key: str
    required_topics: list[str] = Field(default_factory=list)  # Жёсткий список топиков для view


class SectionContractCreate(BaseModel):
    """Схема для создания контракта секции."""

    workspace_id: UUID
    doc_type: DocumentType
    section_key: str
    title: str
    view_key: str | None = None  # Ключ view для ограничения топиков
    view_topics_config: list[ViewTopicsConfig] | None = None  # Конфигурация required_topics по view_key
    required_facts_json: RequiredFactsMVP = Field(default_factory=RequiredFactsMVP)
    allowed_sources_json: AllowedSourcesMVP = Field(default_factory=AllowedSourcesMVP)
    retrieval_recipe_json: RetrievalRecipeMVP = Field(default_factory=RetrievalRecipeMVP)
    qc_ruleset_json: QCRulesetMVP = Field(default_factory=QCRulesetMVP)
    citation_policy: CitationPolicy
    version: int = 2
    is_active: bool = True
    
    @field_validator("section_key")
    @classmethod
    def validate_section_key(cls, v: str) -> str:
        """Валидирует section_key (target_section) на соответствие 12 каноническим ключам."""
        if not is_valid_target_section(v):
            raise ValueError(
                f"section_key должен быть одним из 12 канонических ключей: {CANONICAL_SECTION_KEYS}, "
                f"получено: {v}"
            )
        return v
    
    def model_post_init(self, __context: Any) -> None:
        """После инициализации обновляет prefer_source_zones в retrieval_recipe_json, если они не заданы."""
        if self.retrieval_recipe_json and not self.retrieval_recipe_json.prefer_source_zones:
            prefer_zones = get_prefer_source_zones(self.section_key)
            if prefer_zones["prefer"]:
                self.retrieval_recipe_json.prefer_source_zones = prefer_zones["prefer"]
                self.retrieval_recipe_json.fallback_source_zones = prefer_zones.get("fallback", [])


class SectionContractOut(BaseModel):
    """Схема для вывода контракта секции."""

    id: UUID
    workspace_id: UUID
    doc_type: DocumentType
    section_key: str
    title: str
    required_facts_json: dict[str, Any]
    allowed_sources_json: dict[str, Any]
    retrieval_recipe_json: dict[str, Any]
    qc_ruleset_json: dict[str, Any]
    citation_policy: CitationPolicy
    version: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SectionMapOut(BaseModel):
    """Схема для вывода маппинга секции."""

    id: UUID
    doc_version_id: UUID
    section_key: str
    anchor_ids: list[str] | None
    chunk_ids: list[UUID] | None
    confidence: float
    status: SectionMapStatus
    mapped_by: SectionMapMappedBy
    notes: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class SectionMapOverrideRequest(BaseModel):
    """Схема для переопределения маппинга секции."""

    anchor_ids: list[str] | None = None
    chunk_ids: list[UUID] | None = None
    notes: str | None = None


class SectionMappingAssistRequest(BaseModel):
    """Схема запроса для LLM-assisted mapping."""

    doc_type: DocumentType
    section_keys: list[str]
    max_candidates_per_section: int = 3
    allow_visual_headings: bool = False
    apply: bool = False  # Если True, применить изменения в section_maps


class CandidateOut(BaseModel):
    """Кандидат заголовка."""

    heading_anchor_id: str
    confidence: float
    rationale: str


class SectionQCOut(BaseModel):
    """QC отчёт для секции."""

    status: str  # "mapped" | "needs_review" | "rejected"
    selected_heading_anchor_id: str | None
    errors: list[dict[str, str]]  # [{"type": "...", "message": "..."}]


class SectionMappingAssistResponse(BaseModel):
    """Схема ответа для LLM-assisted mapping."""

    version_id: UUID
    document_language: DocumentLanguage
    secure_mode: bool
    llm_used: bool
    candidates: dict[str, list[CandidateOut]]
    qc: dict[str, SectionQCOut]
