from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import router as api_router
from app.core.config import settings
from app.core.errors import configure_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(title="ClinNexus MVP", version="0.1.0")

    # CORS для dev
    if settings.app_env == "dev":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Обработка ошибок
    configure_error_handlers(app)

    # Health endpoint
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # API роутеры
    app.include_router(api_router, prefix="/api")

    return app


app = create_app()


