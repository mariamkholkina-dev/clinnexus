from enum import Enum
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    """Провайдер LLM для section mapping assist."""

    AZURE_OPENAI = "azure_openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    LOCAL = "local"


# Определяем путь к .env файлу относительно расположения config.py
# config.py находится в backend/app/core/, поэтому .env должен быть в backend/
_CONFIG_DIR = Path(__file__).parent.parent.parent
_ENV_FILE = _CONFIG_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # Игнорировать дополнительные поля из переменных окружения
    )

    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    # Уровень логирования приложения.
    # По умолчанию в dev включаем DEBUG, чтобы видеть полный трейс пайплайна.
    # В prod можно переопределить через LOG_LEVEL=info|warning|error.
    log_level: str = "DEBUG"

    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "clinnexus"
    db_user: str = "clinnexus"
    db_password: str = "clinnexus"

    # Storage
    storage_base_path: str = ".data/uploads"

    # LLM Assist Configuration
    secure_mode: bool = False  # Если False, LLM вызовы запрещены
    llm_provider: LLMProvider | None = None  # azure_openai|openai_compatible|local
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    # Для ассиста payload может быть довольно большим (headings + contracts),
    # поэтому даём провайдеру больше времени, чем "обычный" чат.
    llm_timeout_sec: int = 60

    # MVP: UI/HTTP редактирование section_contracts запрещено по умолчанию.
    # Паспорта должны загружаться сидером из репозитория.
    enable_contract_editing: bool = False

    # Диагностические логи маппинга секций (SectionMappingService).
    # Если True, дополнительно логируем top-3 кандидата заголовков и детали скоринга.
    # Включается через env var: MAPPING_DEBUG_LOGS=1
    mapping_debug_logs: bool = False

    # Passport Tuning: пути к файлам кластеров и маппинга
    passport_tuning_clusters_path: str = "app/data/passport_tuning/clusters.json"
    passport_tuning_mapping_path: str = "app/data/passport_tuning/cluster_to_section_key.json"

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def async_database_url(self) -> str:
        """Формирует async URL для SQLAlchemy с psycopg 3.x (async по умолчанию)."""
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


settings = Settings()


class APIErrorResponse(BaseModel):
    detail: str
    code: str


