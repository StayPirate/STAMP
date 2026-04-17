"""Application configuration using pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "stamp"
    debug: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://stamp:stamp@localhost:5432/stamp"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # CORS
    cors_origins: list[str] = ["http://localhost:5173"]

    # OBS / IBS Integration
    obs_api_url: str = ""
    obs_username: str = ""
    obs_password: str = ""
    ibs_download_base_url: str = "https://download.suse.de/ibs"

    # NVD API
    nvd_api_key: str = ""

    # Security
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 60


settings = Settings()
