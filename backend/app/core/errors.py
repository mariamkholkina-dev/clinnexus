from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import APIErrorResponse


class AppError(Exception):
    """Базовое исключение приложения с кодом ошибки."""

    def __init__(
        self, message: str, code: str = "internal_error", details: dict[str, Any] | None = None
    ) -> None:
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)


class NotFoundError(AppError):
    """Ошибка когда ресурс не найден."""

    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            message=f"{resource} с id {resource_id} не найден",
            code="not_found",
            details={"resource": resource, "resource_id": resource_id},
        )


class ValidationError(AppError):
    """Ошибка валидации данных."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="validation_error", details=details)


def configure_error_handlers(app: FastAPI) -> None:
    """Настройка обработчиков ошибок для FastAPI."""

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=400 if exc.code != "not_found" else 404,
            content={
                "detail": exc.message,
                "code": exc.code,
                "details": exc.details,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "code": "internal_error",
                "details": {},
            },
        )


