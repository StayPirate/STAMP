"""Image smoke assertions for the Celery application bootstrap (P1-06).

Verifies container-observable outcomes of `backend/app/celery_app.py`
that only manifest against the actual built artifact: `celery -A
app.celery_app` can discover and report the app without starting a
worker, the shipped image carries the mandatory redbeat lock and UTC
configuration active (not merely correct in a local venv), and invalid
`CELERY_TIMEZONE`/`CELERY_ENABLE_UTC` values fail fast in a fresh
process — mirroring the existing LOG_LEVEL/LOG_FORMAT startup
validation pattern in test_logging.py.

See docs/features/platform/fetcher-infrastructure.md (Startup
Validation, Redbeat Configuration) and
docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing, Growth Rule).

Scope note on the redbeat lock startup rejection: `redbeat_lock_key`/
`redbeat_lock_timeout` are fixed application-level constants with no
corresponding environment variable (by design — see
docs/features/platform/fetcher-infrastructure.md, Redbeat
Configuration), so an invalid value can only be introduced by a future
code change, not by container/environment configuration. That
regression is exercised by the unit test suite
(`tests/test_celery_app.py::TestValidateCeleryConfigLockRejection`),
which is the correct place for a code-only invariant. What IS
container-observable and covered here instead is the positive
assertion that the lock ships enabled in the actual built image.

Scope note on the `setup_logging` signal wiring: whether the connected
receiver actually replaces Celery's own logging setup
(`_install_structured_logging` in `celery_app.py`) is verified only at
unit level (`tests/test_celery_app.py::TestSetupLoggingSignalReplacesCeleryDefault`).
It is not re-verified here because no `worker`/`beat` container role
exists yet in this phase (both remain commented out in
docker-compose.smoke.yml until Phase 3) to observe it against, and
`celery report` — used above — does not itself trigger
`setup_logging_subsystem`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

# Inline Python snippet confirming the redbeat lock and scheduler are
# active in the app object as constructed inside the shipped image
# (not a local venv). `celery -A app.celery_app report` alone is
# insufficient here because celery masks any setting whose key matches
# its HIDDEN_SETTINGS pattern (which includes "*_key") as "********".
_LOCK_CONFIG_CHECK_SCRIPT = """
from app.celery_app import celery_app

conf = celery_app.conf
assert conf.redbeat_lock_key == "redbeat::lock", conf.redbeat_lock_key
assert conf.redbeat_lock_timeout == 300, conf.redbeat_lock_timeout
assert conf.beat_scheduler == "redbeat.RedBeatScheduler", conf.beat_scheduler
assert conf.timezone == "UTC", conf.timezone
assert conf.enable_utc is True, conf.enable_utc
assert conf.result_backend is None, conf.result_backend
print("lock-config-ok")
"""


@pytest.mark.image
class TestCeleryReport:
    """`celery -A app.celery_app report` succeeds without starting a worker."""

    def test_report_succeeds(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec("api", "celery", "-A", "app.celery_app", "report")
        assert result.returncode == 0, (
            f"expected exit 0 for celery report "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "timezone: 'UTC'" in result.stdout
        assert "enable_utc: True" in result.stdout
        assert "result_backend: None" in result.stdout


@pytest.mark.image
class TestShippedImageLockConfiguration:
    """The redbeat lock ships enabled in the actual built image."""

    def test_lock_and_scheduler_active(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec("api", "python", "-c", _LOCK_CONFIG_CHECK_SCRIPT)
        assert result.returncode == 0, (
            f"expected exit 0 for lock config check "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "lock-config-ok" in result.stdout


@pytest.mark.image
class TestCeleryTimezoneStartupValidation:
    """Invalid CELERY_TIMEZONE/CELERY_ENABLE_UTC fail fast in a fresh process."""

    def test_invalid_timezone_fails_fast(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec(
            "api",
            "python",
            "-c",
            "import app.celery_app",
            env={"CELERY_TIMEZONE": "Europe/Rome"},
        )
        assert result.returncode != 0, (
            f"expected non-zero exit for invalid CELERY_TIMEZONE "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "FATAL: Celery timezone must be UTC" in result.stderr

    def test_disabled_utc_fails_fast(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec(
            "api",
            "python",
            "-c",
            "import app.celery_app",
            env={"CELERY_ENABLE_UTC": "false"},
        )
        assert result.returncode != 0, (
            f"expected non-zero exit for disabled CELERY_ENABLE_UTC "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "FATAL: Celery timezone must be UTC" in result.stderr

    def test_valid_override_still_imports(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        """Control case: confirms the failures above are due to the
        invalid value, not to the exec/env-override mechanism itself."""
        result = compose_exec(
            "api",
            "python",
            "-c",
            "import app.celery_app; print('import-ok')",
            env={"CELERY_TIMEZONE": "UTC", "CELERY_ENABLE_UTC": "true"},
        )
        assert result.returncode == 0, (
            f"expected exit 0 for valid CELERY_TIMEZONE/CELERY_ENABLE_UTC "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "import-ok" in result.stdout
