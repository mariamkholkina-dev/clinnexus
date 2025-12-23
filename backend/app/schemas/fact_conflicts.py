from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CanonicalFactValue(BaseModel):
    """Нормализованное значение канонического факта."""

    value: int | float | str | list[str] | dict[str, Any] | None
    unit: str | None = None
    confidence: float | None = None  # 0.0-1.0, None означает неизвестно
    is_low_confidence: bool = False  # True если содержит маркеры неопределённости


class FactEvidence(BaseModel):
    """Доказательство факта из конкретного источника."""

    value: Any
    source_zone: str
    anchor_ids: list[str]
    confidence: float | None = None
    fact_id: UUID | None = None


class FactConflict(BaseModel):
    """Обнаруженный конфликт фактов."""

    fact_key: str
    values: list[CanonicalFactValue] = Field(
        default_factory=list,
        description="Список различных нормализованных значений",
    )
    evidence: list[FactEvidence] = Field(
        default_factory=list,
        description="Список доказательств с разными значениями",
    )
    severity: str = Field(
        description="block|warn - блокирующий конфликт или предупреждение"
    )
    can_auto_resolve: bool = Field(
        default=False,
        description="Можно ли автоматически разрешить конфликт (только если одна зона имеет значение, а другие - низкая уверенность)",
    )


class ConflictDetectionResult(BaseModel):
    """Результат обнаружения конфликтов."""

    conflicts: list[FactConflict] = Field(default_factory=list)
    total_conflicts: int = 0
    blocking_conflicts: int = 0
    warning_conflicts: int = 0

