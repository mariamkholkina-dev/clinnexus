from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.db.enums import DocumentType


class MappingMode(str, Enum):
    """Режим маппинга кластера."""

    SINGLE = "single"
    AMBIGUOUS = "ambiguous"
    SKIP = "skip"
    NEEDS_SPLIT = "needs_split"


class ClusterExample(BaseModel):
    """Пример заголовка из кластера."""

    doc_version_id: str
    section_path: str
    heading_text_raw: str


class ClusterStats(BaseModel):
    """Статистика кластера."""

    heading_level_histogram: dict[str, int] = Field(default_factory=dict)
    content_type_distribution: dict[str, int] = Field(default_factory=dict)
    avg_total_chars: float = 0.0


class CandidateSection(BaseModel):
    """Кандидат секции для кластера."""

    section_key: str
    title_ru: str
    score: float


class Cluster(BaseModel):
    """Кластер заголовков."""

    cluster_id: str | int
    top_titles_ru: list[str] = Field(default_factory=list)
    top_titles_en: list[str] = Field(default_factory=list)
    examples: list[ClusterExample] = Field(default_factory=list)
    stats: ClusterStats | dict[str, Any] = Field(default_factory=dict)
    candidate_section_1: CandidateSection | None = None
    candidate_section_2: CandidateSection | None = None
    candidate_section_3: CandidateSection | None = None
    default_section: str | None = None  # Для обратной совместимости


class ClusterMappingItem(BaseModel):
    """Элемент маппинга кластера."""

    doc_type: DocumentType | None = None
    section_key: str = ""
    title_ru: str | None = None
    mapping_mode: MappingMode = Field(default=MappingMode.SINGLE)
    notes: str | None = Field(None, max_length=500)

    @field_validator("section_key", mode="before")
    @classmethod
    def validate_section_key(cls, v: str | None) -> str:
        """Нормализация section_key."""
        if v is None:
            return ""
        return v.strip()

    @field_validator("title_ru", mode="before")
    @classmethod
    def validate_title_ru(cls, v: str | None) -> str | None:
        """Проверка, что title_ru либо пустой, либо непустая строка."""
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            return v
        return v

    @field_validator("doc_type", mode="before")
    @classmethod
    def validate_doc_type(cls, v: Any) -> DocumentType | None:
        """Нормализация doc_type."""
        if v is None:
            return None
        if isinstance(v, str):
            return DocumentType(v)
        return v

    @field_validator("notes", mode="before")
    @classmethod
    def validate_notes(cls, v: str | None) -> str | None:
        """Проверка и нормализация notes."""
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
            return v[:500]  # Ограничение длины
        return v

    @model_validator(mode="after")
    def validate_mapping_rules(self) -> "ClusterMappingItem":
        """Валидация правил маппинга в зависимости от режима."""
        if self.mapping_mode == MappingMode.SKIP:
            # Для skip разрешаем пустые doc_type и section_key
            if not self.doc_type:
                self.doc_type = DocumentType.OTHER
            # section_key может быть пустым
            return self

        # Для остальных режимов section_key обязателен
        if not self.section_key or not self.section_key.strip():
            raise ValueError("section_key обязателен для режима маппинга, отличного от 'skip'")

        # Проверка соответствия doc_type и section_key
        if self.doc_type and self.section_key:
            expected_prefix = f"{self.doc_type.value}."
            if not self.section_key.startswith(expected_prefix):
                # Предупреждение, но не блокируем сохранение
                pass

        return self




class ClustersResponse(BaseModel):
    """Ответ со списком кластеров."""

    items: list[Cluster]
    total: int


class MappingResponse(BaseModel):
    """Ответ с текущим маппингом."""

    mapping: dict[str, dict[str, str | None]]

