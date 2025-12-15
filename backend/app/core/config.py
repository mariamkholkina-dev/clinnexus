from pydantic import BaseModel
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "dev"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "info"

    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "clinnexus"
    db_user: str = "clinnexus"
    db_password: str = "clinnexus"

    # Storage
    storage_base_path: str = ".data/uploads"

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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


class APIErrorResponse(BaseModel):
    detail: str
    code: str


