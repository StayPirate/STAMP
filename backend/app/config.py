"""Application configuration using pydantic-settings."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from pydantic import (
    BeforeValidator,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)


def _split_comma(value: Any) -> Any:
    """Split a comma-separated string into a list of stripped, non-empty items."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


CommaSeparated = Annotated[list[str], NoDecode, BeforeValidator(_split_comma)]

_VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_VALID_LOG_FORMATS = ("auto", "json", "console")


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "sentinel"
    debug: bool = False

    # Logging
    log_level: str = "INFO"
    log_format: str = "auto"

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel",
        repr=False,
    )

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False)

    # Celery
    celery_broker_url: str = Field(default="redis://localhost:6379/1", repr=False)

    # CORS
    cors_origins: CommaSeparated = ["http://localhost:5173"]

    # IBS Integration
    ibs_api_url: str = "https://api.suse.de"
    ibs_username: str = ""
    ibs_password: SecretStr = SecretStr("")
    ibs_download_base_url: str = "https://download.suse.de/ibs"

    # NVD API
    nvd_api_key: SecretStr = SecretStr("")

    # Security
    jwt_secret_key: SecretStr
    jwt_expiry_hours: int = 72

    @field_validator("log_level", mode="before")
    @classmethod
    def _validate_log_level(cls, value: Any) -> Any:
        """Normalize and validate LOG_LEVEL (case-insensitive)."""
        if isinstance(value, str):
            normalized = value.upper()
            if normalized not in _VALID_LOG_LEVELS:
                msg = (
                    f"Invalid LOG_LEVEL: {value!r}. Must be one of "
                    f"{', '.join(_VALID_LOG_LEVELS)} (case-insensitive)."
                )
                raise ValueError(msg)
            return normalized
        return value

    @field_validator("log_format", mode="before")
    @classmethod
    def _validate_log_format(cls, value: Any) -> Any:
        """Normalize and validate LOG_FORMAT (case-insensitive)."""
        if isinstance(value, str):
            normalized = value.lower()
            if normalized not in _VALID_LOG_FORMATS:
                msg = (
                    f"Invalid LOG_FORMAT: {value!r}. Must be one of "
                    f"{', '.join(_VALID_LOG_FORMATS)} (case-insensitive)."
                )
                raise ValueError(msg)
            return normalized
        return value

    @model_validator(mode="after")
    def _validate_security_settings(self) -> Settings:
        """Fail fast on invalid security configuration."""
        jwt_secret_key_length = len(self.jwt_secret_key.get_secret_value())
        if jwt_secret_key_length < 32:
            msg = (
                f"Invalid JWT_SECRET_KEY: must be at least 32 characters "
                f"(got: {jwt_secret_key_length})"
            )
            raise ValueError(msg)
        if self.jwt_expiry_hours < 1:
            msg = (
                f"Invalid JWT_EXPIRY_HOURS: must be >= 1 (got: {self.jwt_expiry_hours})"
            )
            raise ValueError(msg)
        if self.jwt_expiry_hours > 720:
            logger.warning(
                "JWT_EXPIRY_HOURS is set to %d (>720 hours). "
                "Long-lived tokens increase the window of exposure "
                "if a token is compromised.",
                self.jwt_expiry_hours,
            )
        return self

    @model_validator(mode="after")
    def _validate_ibs_settings(self) -> Settings:
        """Warn if IBS credentials are not configured."""
        if not self.ibs_username or not self.ibs_password.get_secret_value():
            logger.warning(
                "IBS credentials not configured (IBS_USERNAME / IBS_PASSWORD "
                "empty). IBS-dependent fetchers will fail at runtime."
            )
        return self


settings = Settings()
