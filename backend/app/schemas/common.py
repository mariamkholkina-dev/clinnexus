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
