from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, DatabaseError

from app.core.config import APIErrorResponse
from app.core.logging import logger


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


class ConflictError(AppError):
    """Ошибка конфликта (например, ресурс уже обрабатывается)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message=message, code="conflict", details=details)


def configure_error_handlers(app: FastAPI) -> None:
    """Настройка обработчиков ошибок для FastAPI."""

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        # Определяем статус код на основе типа ошибки
        if exc.code == "not_found":
            status_code = 404
        elif exc.code == "conflict":
            status_code = 409
        elif exc.code == "validation_error":
            status_code = 400
        else:
            status_code = 400
        
        return JSONResponse(
            status_code=status_code,
            content={
                "detail": exc.message,
                "code": exc.code,
                "details": exc.details,
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_: Request, exc: IntegrityError) -> JSONResponse:
        """Обработка ошибок целостности базы данных."""
        logger.error(f"IntegrityError: {exc}", exc_info=True)
        error_msg = str(exc.orig) if hasattr(exc, "orig") else str(exc)
        
        # Определяем тип ошибки
        if "foreign key" in error_msg.lower():
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Нарушение внешнего ключа. Проверьте существование связанных ресурсов.",
                    "code": "validation_error",
                    "details": {"error": error_msg},
                },
            )
        elif "unique" in error_msg.lower() or "duplicate" in error_msg.lower():
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Нарушение уникальности. Ресурс с такими данными уже существует.",
                    "code": "conflict",
                    "details": {"error": error_msg},
                },
            )
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Ошибка целостности данных.",
                    "code": "validation_error",
                    "details": {"error": error_msg},
                },
            )

    @app.exception_handler(DatabaseError)
    async def database_error_handler(_: Request, exc: DatabaseError) -> JSONResponse:
        """Обработка общих ошибок базы данных."""
        logger.error(f"DatabaseError: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Ошибка базы данных",
                "code": "database_error",
                "details": {},
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        """Обработка необработанных исключений."""
        logger.error(f"Unhandled error: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "code": "internal_error",
                "details": {},
            },
        )


