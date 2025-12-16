from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class BaseResponse(BaseModel):
    """Базовый ответ API."""

    success: bool = True


class ErrorResponse(BaseModel):
    """Стандартный формат ошибки."""

    detail: str
    code: str
    details: dict[str, Any] = {}


class SoAVisit(BaseModel):
    """Визит в Schedule of Activities."""

    visit_id: str
    label: str
    day: str | None = None
    anchor_id: str | None = None


class SoAProcedure(BaseModel):
    """Процедура в Schedule of Activities."""

    proc_id: str
    label: str
    category: str | None = None
    anchor_id: str | None = None


class SoAMatrixEntry(BaseModel):
    """Запись в матрице visits × procedures."""

    visit_id: str
    proc_id: str
    value: str
    anchor_id: str | None = None


class SoANote(BaseModel):
    """Примечание к Schedule of Activities."""

    note_id: str
    text: str
    anchor_id: str | None = None


class SoAResult(BaseModel):
    """Результат извлечения Schedule of Activities."""

    table_index: int
    section_path: str
    visits: list[SoAVisit]
    procedures: list[SoAProcedure]
    matrix: list[SoAMatrixEntry]
    notes: list[SoANote] = []
    confidence: float = 0.0
    warnings: list[str] = []


class FactItem(BaseModel):
    """Элемент факта для извлечения."""

    fact_type: str
    fact_key: str
    value_json: dict[str, Any]
    status: str