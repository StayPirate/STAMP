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
        assert s.jwt_secret_key == "a" * 32


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
