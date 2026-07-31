"""Tests for Settings startup validation (backend/app/config.py)."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.unit
class TestJwtSecretKeyValidation:
    """JWT_SECRET_KEY startup validation."""

    def test_missing_jwt_secret_key_raises(self, monkeypatch):
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        with pytest.raises(ValidationError, match="jwt_secret_key"):
            Settings(_env_file=None)

    def test_short_jwt_secret_key_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "short")
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(_env_file=None)

    def test_31_chars_jwt_secret_key_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 31)
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(_env_file=None)

    def test_exactly_32_chars_accepted(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        s = Settings(_env_file=None)
        assert s.jwt_secret_key.get_secret_value() == "a" * 32


@pytest.mark.unit
class TestJwtExpiryValidation:
    """JWT_EXPIRY_HOURS startup validation."""

    def test_zero_expiry_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "0")
        with pytest.raises(ValidationError, match="must be >= 1"):
            Settings(_env_file=None)

    def test_negative_expiry_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "-1")
        with pytest.raises(ValidationError, match="must be >= 1"):
            Settings(_env_file=None)

    def test_excessive_expiry_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "721")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert ">720 hours" in caplog.text

    def test_720_does_not_warn(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "720")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert ">720 hours" not in caplog.text

    def test_expiry_1_accepted(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("JWT_EXPIRY_HOURS", "1")
        s = Settings(_env_file=None)
        assert s.jwt_expiry_hours == 1


@pytest.mark.unit
class TestLogLevelValidation:
    """LOG_LEVEL startup validation (docs/features/platform/logging.md)."""

    @pytest.mark.parametrize("value", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    def test_valid_levels_accepted(self, monkeypatch, value):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("LOG_LEVEL", value)
        s = Settings(_env_file=None)
        assert s.log_level == value

    @pytest.mark.parametrize(
        ("input_value", "expected"),
        [
            ("debug", "DEBUG"),
            ("Debug", "DEBUG"),
            ("info", "INFO"),
            ("WARNING", "WARNING"),
            ("error", "ERROR"),
            ("Critical", "CRITICAL"),
        ],
    )
    def test_case_insensitive_normalization(self, monkeypatch, input_value, expected):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("LOG_LEVEL", input_value)
        s = Settings(_env_file=None)
        assert s.log_level == expected

    def test_default_is_info(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        s = Settings(_env_file=None)
        assert s.log_level == "INFO"

    def test_invalid_value_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("LOG_LEVEL", "BOGUS")
        with pytest.raises(ValidationError, match="Invalid LOG_LEVEL"):
            Settings(_env_file=None)

    def test_empty_value_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("LOG_LEVEL", "")
        with pytest.raises(ValidationError, match="Invalid LOG_LEVEL"):
            Settings(_env_file=None)


@pytest.mark.unit
class TestLogFormatValidation:
    """LOG_FORMAT startup validation (docs/features/platform/logging.md)."""

    @pytest.mark.parametrize("value", ["auto", "json", "console"])
    def test_valid_formats_accepted(self, monkeypatch, value):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("LOG_FORMAT", value)
        s = Settings(_env_file=None)
        assert s.log_format == value

    @pytest.mark.parametrize(
        ("input_value", "expected"),
        [
            ("AUTO", "auto"),
            ("Json", "json"),
            ("CONSOLE", "console"),
        ],
    )
    def test_case_insensitive_normalization(self, monkeypatch, input_value, expected):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("LOG_FORMAT", input_value)
        s = Settings(_env_file=None)
        assert s.log_format == expected

    def test_default_is_auto(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        s = Settings(_env_file=None)
        assert s.log_format == "auto"

    def test_invalid_value_raises(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("LOG_FORMAT", "xml")
        with pytest.raises(ValidationError, match="Invalid LOG_FORMAT"):
            Settings(_env_file=None)


@pytest.mark.unit
class TestDebugLogLevelOrthogonality:
    """DEBUG and LOG_LEVEL are fully independent configuration axes."""

    def test_debug_true_does_not_change_log_level(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("DEBUG", "true")
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        s = Settings(_env_file=None)
        assert s.log_level == "INFO"
        assert s.debug is True

    def test_log_level_debug_does_not_change_debug_flag(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.delenv("DEBUG", raising=False)
        s = Settings(_env_file=None)
        assert s.log_level == "DEBUG"
        assert s.debug is False


@pytest.mark.unit
class TestIbsCredentialWarning:
    """IBS credential startup warning."""

    def test_empty_ibs_credentials_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_USERNAME", "")
        monkeypatch.setenv("IBS_PASSWORD", "")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert "IBS credentials not configured" in caplog.text

    def test_only_username_empty_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_USERNAME", "")
        monkeypatch.setenv("IBS_PASSWORD", "secret")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert "IBS credentials not configured" in caplog.text

    def test_only_password_empty_warns(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_USERNAME", "jdoe")
        monkeypatch.setenv("IBS_PASSWORD", "")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert "IBS credentials not configured" in caplog.text

    def test_configured_ibs_credentials_no_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_USERNAME", "jdoe")
        monkeypatch.setenv("IBS_PASSWORD", "secret-password-here")
        with caplog.at_level(logging.WARNING):
            Settings(_env_file=None)
        assert "IBS credentials not configured" not in caplog.text


@pytest.mark.unit
class TestSecretFieldRedaction:
    """Secret field redaction, covering two distinct mechanisms:

    - `SecretStr` fields (`jwt_secret_key`, `ibs_password`, `nvd_api_key`):
      masked in both `repr()`/`str()` AND `model_dump()`/`model_dump_json()`.
    - `Field(..., repr=False)` URL fields (`database_url`, `redis_url`,
      `celery_broker_url`): the field is entirely excluded from `repr()`,
      but the plain value IS still returned by `model_dump()` (repr=False
      only affects repr, not serialization).
    """

    def test_repr_does_not_expose_jwt_secret_key(self, monkeypatch):
        secret_value = "x" * 32
        monkeypatch.setenv("JWT_SECRET_KEY", secret_value)
        s = Settings(_env_file=None)
        assert secret_value not in repr(s)
        assert secret_value not in str(s)

    def test_repr_does_not_expose_ibs_password(self, monkeypatch):
        secret_value = "super-secret-ibs-password"
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("IBS_PASSWORD", secret_value)
        s = Settings(_env_file=None)
        assert secret_value not in repr(s)

    def test_repr_does_not_expose_nvd_api_key(self, monkeypatch):
        secret_value = "super-secret-nvd-api-key"
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("NVD_API_KEY", secret_value)
        s = Settings(_env_file=None)
        assert secret_value not in repr(s)

    def test_repr_does_not_expose_database_url_credentials(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://sentinel_user:sentinel_pw@db:5432/sentinel",
        )
        s = Settings(_env_file=None)
        assert "sentinel_pw" not in repr(s)
        assert "database_url" not in repr(s)

    def test_repr_does_not_expose_redis_url_credentials(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv(
            "REDIS_URL",
            "redis://:redis_secret_pw@redis:6379/0",
        )
        s = Settings(_env_file=None)
        assert "redis_secret_pw" not in repr(s)
        assert "redis_url" not in repr(s)

    def test_repr_does_not_expose_celery_broker_url_credentials(self, monkeypatch):
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv(
            "CELERY_BROKER_URL",
            "redis://:celery_secret_pw@redis:6379/1",
        )
        s = Settings(_env_file=None)
        assert "celery_secret_pw" not in repr(s)
        assert "celery_broker_url" not in repr(s)

    def test_repr_exposes_non_secret_fields(self, monkeypatch):
        """Non-secret fields must remain visible in repr() — guards against
        over-broad redaction being applied by mistake in the future."""
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("APP_NAME", "sentinel-test-instance")
        s = Settings(_env_file=None)
        assert "sentinel-test-instance" in repr(s)

    def test_model_dump_masks_secret_str_fields(self, monkeypatch):
        secret_value = "x" * 32
        monkeypatch.setenv("JWT_SECRET_KEY", secret_value)
        s = Settings(_env_file=None)
        dumped = s.model_dump()
        assert dumped["jwt_secret_key"].get_secret_value() == secret_value
        assert secret_value not in repr(dumped["jwt_secret_key"])
        assert secret_value not in str(dumped)

    def test_model_dump_json_masks_secret_str_fields(self, monkeypatch):
        secret_value = "x" * 32
        monkeypatch.setenv("JWT_SECRET_KEY", secret_value)
        s = Settings(_env_file=None)
        dumped_json = s.model_dump_json()
        assert secret_value not in dumped_json

    def test_model_dump_exposes_plain_repr_false_url_fields(self, monkeypatch):
        """`repr=False` only affects repr(); model_dump() must still return
        the plain string value for these fields (no masking on dump)."""
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        db_url = "postgresql+asyncpg://sentinel_user:sentinel_pw@db:5432/sentinel"
        monkeypatch.setenv("DATABASE_URL", db_url)
        s = Settings(_env_file=None)
        dumped = s.model_dump()
        assert dumped["database_url"] == db_url
