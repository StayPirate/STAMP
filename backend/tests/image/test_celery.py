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
It is not re-verified here: `celery report` — used above — does not
itself trigger `setup_logging_subsystem`, and the worker/beat process
tests below assert on functional outcomes (task registration, RedBeat
schedule state, a broker-delivered task's database effect), not on the
logging handler wiring.

`worker` and `beat` are active process roles in
docker-compose.smoke.yml as of this module's `TestWorkerStartup`,
`TestBeatSchedule`, and `TestCleanupSessionsBrokerExecution` — see
docs/features/platform/testing-strategy.md (Growth Rule).
"""

from __future__ import annotations

import json
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
entry = conf.beat_schedule["cleanup_sessions"]
assert entry["task"] == "cleanup_sessions", entry
assert entry["schedule"].minute == {0}, entry["schedule"]
assert entry["schedule"].hour == {3}, entry["schedule"]
assert entry["schedule"].day_of_week == {0}, entry["schedule"]
assert "cleanup_sessions" in celery_app.tasks, sorted(celery_app.tasks)
print("lock-config-ok")
"""

# Polls the `beat` container's own view of the persisted RedBeat entry
# through RedBeatSchedulerEntry's public API only — `generate_key()` +
# `from_key()` — never by constructing the `redbeat:cleanup_sessions`
# Redis key string directly (see docs/conventions.md, Redis Key
# Conventions). Bounded: Beat writes the entry via `setup_schedule()`
# during its own startup, which can take a second or two after the
# container is reported "running" (no healthcheck exists for `beat` —
# see docker-compose.smoke.yml), so a short bounded retry absorbs that
# gap instead of racing it.
_BEAT_SCHEDULE_CHECK_SCRIPT = """
import time

from app.celery_app import celery_app
from redbeat import RedBeatSchedulerEntry

key = RedBeatSchedulerEntry(name="cleanup_sessions", app=celery_app).key

entry = None
last_error = None
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    try:
        entry = RedBeatSchedulerEntry.from_key(key, app=celery_app)
        break
    except KeyError as exc:
        last_error = exc
        time.sleep(1)

if entry is None:
    raise SystemExit(f"cleanup_sessions entry not found in RedBeat: {last_error}")

assert entry.task == "cleanup_sessions", entry.task
assert entry.enabled is True, entry.enabled
schedule = entry.schedule
assert schedule.hour == {3}, schedule.hour
assert schedule.minute == {0}, schedule.minute
assert schedule.day_of_week == {0}, schedule.day_of_week
print("BEAT-SCHEDULE-OK")
"""

# Publishes `cleanup_sessions` through the real broker (no result
# backend involved) and verifies its database effect: seeds one
# already-expired `Session` row (with its required `User` parent),
# sends the task, then polls the database — bounded — until the row is
# gone. Runs inside the `api` container, which shares the same database
# and `CELERY_BROKER_URL` as `worker`/`beat`.
_TASK_EFFECT_SCRIPT = """
import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.celery_app import celery_app
from app.database import async_session_factory, engine
from app.models.session import Session
from app.models.user import User


async def seed() -> uuid.UUID:
    session_id = uuid.uuid4()
    async with async_session_factory() as db:
        user = User(
            username=f"smoke-{session_id.hex[:8]}",
            email=f"smoke-{session_id.hex[:8]}@example.com",
            external_id=uuid.uuid4(),
        )
        db.add(user)
        await db.flush()
        expired_session = Session(
            id=session_id,
            user_id=user.id,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        db.add(expired_session)
        await db.commit()
    return session_id


async def session_exists(session_id: uuid.UUID) -> bool:
    async with async_session_factory() as db:
        result = await db.execute(select(Session.id).where(Session.id == session_id))
        return result.scalar_one_or_none() is not None


async def main() -> None:
    session_id = await seed()
    assert await session_exists(session_id), "seed session not visible before task"

    celery_app.send_task("cleanup_sessions")

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if not await session_exists(session_id):
            print("TASK-EFFECT-OK")
            await engine.dispose()
            return
        await asyncio.sleep(1)

    await engine.dispose()
    raise SystemExit("expired session was not cleaned up within the poll window")


asyncio.run(main())
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


@pytest.mark.image
class TestWorkerStartup:
    """The `worker` service starts, is reachable, and loaded `cleanup_sessions`.

    `docker compose up -d --wait` already blocks on the `worker`
    service's own healthcheck (`celery inspect ping`, see
    docker-compose.smoke.yml) before returning, so by the time these
    tests run the worker is known to be responsive. `--json` is used
    for both remote-control commands so assertions parse structured
    output instead of matching on Celery's human-readable banner, which
    is not a stable contract.
    """

    def test_worker_responds_to_ping(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec(
            "worker", "celery", "-A", "app.celery_app", "inspect", "ping", "--json"
        )
        assert result.returncode == 0, (
            f"expected exit 0 for inspect ping "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        replies = json.loads(result.stdout)
        assert replies, f"no worker replied to ping: {result.stdout!r}"
        assert all(reply == {"ok": "pong"} for reply in replies.values()), replies

    def test_cleanup_sessions_registered(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec(
            "worker",
            "celery",
            "-A",
            "app.celery_app",
            "inspect",
            "registered",
            "--json",
        )
        assert result.returncode == 0, (
            f"expected exit 0 for inspect registered "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        replies = json.loads(result.stdout)
        assert replies, f"no worker replied to inspect registered: {result.stdout!r}"
        for registered_tasks in replies.values():
            assert "cleanup_sessions" in registered_tasks, registered_tasks


@pytest.mark.image
class TestBeatSchedule:
    """Beat keeps running and exposes `cleanup_sessions` via RedBeat's
    public API — never by reading raw Redis keys directly (see
    docs/conventions.md, Redis Key Conventions)."""

    def test_cleanup_sessions_schedule_visible_via_redbeat_api(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec("beat", "python", "-c", _BEAT_SCHEDULE_CHECK_SCRIPT)
        assert result.returncode == 0, (
            f"expected exit 0 for beat schedule check "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "BEAT-SCHEDULE-OK" in result.stdout


@pytest.mark.image
class TestCleanupSessionsBrokerExecution:
    """A broker-delivered `cleanup_sessions` invocation produces an
    observable database effect, with no Celery result backend involved."""

    def test_broker_delivered_task_cleans_up_expired_session(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec("api", "python", "-c", _TASK_EFFECT_SCRIPT, timeout=40.0)
        assert result.returncode == 0, (
            f"expected exit 0 for task effect check "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "TASK-EFFECT-OK" in result.stdout
