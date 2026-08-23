"""Unit tests for `build_system_process_env` — the pure function behind
the `system_process_env` fixture in `conftest.py`.

Verifies the subprocess environment isolation contract from
`docs/features/platform/testing-strategy.md` (Registration Boundary)
without spawning any process: a developer's shell-exported application
configuration (or `backend/.env` file content it stands in for) must
never reach the environment handed to a spawned worker/Beat process —
only the explicit safe overrides and a minimal OS-level allowlist may.

These tests carry no `@pytest.mark.system` marker and spawn no
subprocess, so they run as part of the ordinary default suite.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from tests.system.conftest import (
    _FIXED_SAFE_SETTINGS_OVERRIDES,
    _INHERITED_OS_ENV_KEYS,
    build_system_process_env,
)

_DEFAULT_KWARGS: dict[str, str] = {
    "database_url": "postgresql+asyncpg://test-user:test-pass@localhost/test-db",
    "redis_url": "redis://localhost:6379/5",
    "jwt_secret_key": "a" * 32,
}


@pytest.mark.unit
class TestBuildSystemProcessEnv:
    def test_ambient_application_config_is_never_inherited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A developer's shell-exported credential must not reach the
        built environment, regardless of casing."""
        monkeypatch.setenv("IBS_PASSWORD", "canary-real-password-do-not-leak")
        monkeypatch.setenv("ibs_password", "canary-lowercase-do-not-leak")
        monkeypatch.setenv("NVD_API_KEY", "canary-nvd-key-do-not-leak")
        monkeypatch.setenv(
            "DATABASE_URL", "postgresql+asyncpg://canary/should-not-leak"
        )
        monkeypatch.setenv(
            "JWT_SECRET_KEY", "canary-jwt-secret-do-not-leak-313233343536"
        )

        env = build_system_process_env(**_DEFAULT_KWARGS)

        assert env["IBS_PASSWORD"] == ""
        assert env["NVD_API_KEY"] == ""
        assert env["DATABASE_URL"] == _DEFAULT_KWARGS["database_url"]
        assert env["JWT_SECRET_KEY"] == _DEFAULT_KWARGS["jwt_secret_key"]
        for value in env.values():
            assert "canary" not in value

    def test_only_allowlisted_os_keys_can_pass_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-application, non-allowlisted ambient variable must not
        leak into the built environment either."""
        monkeypatch.setenv("SOME_UNRELATED_SHELL_VARIABLE", "canary-unrelated-value")

        env = build_system_process_env(**_DEFAULT_KWARGS)

        assert "SOME_UNRELATED_SHELL_VARIABLE" not in env
        for key in env:
            assert key in _INHERITED_OS_ENV_KEYS or key.lower() in (
                *Settings.model_fields,
                "pythonpath",
            )

    def test_covers_every_settings_field(self) -> None:
        """Every `Settings` field must have a corresponding entry in the
        built environment — a gap here would mean the field falls
        through to whatever the ambient environment happens to hold."""
        env = build_system_process_env(**_DEFAULT_KWARGS)

        for field_name in Settings.model_fields:
            assert field_name.upper() in env, field_name

    def test_missing_override_raises_assertion_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulates a `Settings` field added without a corresponding
        safe override — the completeness check must fail loudly rather
        than silently falling through to the ambient environment.
        `monkeypatch.delitem` restores the entry automatically on
        teardown, regardless of test outcome.
        """
        monkeypatch.delitem(_FIXED_SAFE_SETTINGS_OVERRIDES, "app_name")

        with pytest.raises(AssertionError, match="app_name"):
            build_system_process_env(**_DEFAULT_KWARGS)

    def test_pythonpath_points_at_backend_directory(self) -> None:
        env = build_system_process_env(**_DEFAULT_KWARGS)

        assert env["PYTHONPATH"].endswith("backend")

    def test_dynamic_values_take_precedence_over_fixed_defaults(self) -> None:
        """`celery_broker_url` must mirror `redis_url`, not any fixed
        default — both are dynamic per-invocation values."""
        env = build_system_process_env(**_DEFAULT_KWARGS)

        assert env["REDIS_URL"] == _DEFAULT_KWARGS["redis_url"]
        assert env["CELERY_BROKER_URL"] == _DEFAULT_KWARGS["redis_url"]
