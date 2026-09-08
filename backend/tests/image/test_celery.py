"""OCI artifact and process-topology probes for Celery worker and Beat.

Focused tests own Celery configuration validation and reconciliation semantics.
The image suite retains packaged task registration, production-registry
exclusion, Beat schedule visibility, broker delivery, and the lifecycle probes
assigned to the subsequent image-smoke consolidation work item.

See ``docs/features/platform/testing-strategy.md`` (Artifact-Risk Rule).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

import pytest

from tests.image.conftest import IsolatedComposeStack

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

# Seeds two corrupted `run_fetcher`-tasked redbeat entries directly via
# `RedBeatSchedulerEntry`'s public API only (no raw Redis key
# construction — see docs/conventions.md, Redis Key Conventions):
# one deregistered (well-formed kwargs, but "fetcher_name" is absent
# from FETCHER_REGISTRY — there is no production fetcher registered in
# the shipped image), and one malformed (empty kwargs, no
# "fetcher_name" at all). Both simulate operator-induced or historical
# drift that only a Beat restart's reconciliation step 4 may clean up
# (see docs/features/platform/fetcher-infrastructure.md, Reconciliation
# Steps).
_SEED_CORRUPTED_ENTRIES_SCRIPT = """
from celery.schedules import crontab
from redbeat import RedBeatSchedulerEntry

from app.celery_app import celery_app

_SCHEDULE = crontab(minute=0, hour=3)

RedBeatSchedulerEntry(
    name="image_smoke_deregistered_fetcher",
    task="run_fetcher",
    schedule=_SCHEDULE,
    args=[],
    kwargs={
        "fetcher_name": "image_smoke_deregistered_fetcher",
        "triggered_by": "schedule",
    },
    app=celery_app,
).save()

RedBeatSchedulerEntry(
    name="image_smoke_malformed_fetcher",
    task="run_fetcher",
    schedule=_SCHEDULE,
    args=[],
    kwargs={},
    app=celery_app,
).save()

print("SEED-OK")
"""

# Confirms both corrupted entries are still present, unchanged — used
# right after a worker restart to prove the worker never writes to or
# removes entries from redbeat (see fetcher-infrastructure.md, Who
# Writes Where). `ensure_conf()` is called explicitly because this
# runs in a brand-new one-off process where `celery_app.redbeat_conf`
# has not yet been set by any prior entry construction (unlike the
# long-running `beat`/`worker` processes, where redbeat itself always
# populates it first).
_VERIFY_ENTRIES_PRESENT_SCRIPT = """
from redbeat import RedBeatSchedulerEntry
from redbeat.schedulers import ensure_conf

from app.celery_app import celery_app

ensure_conf(celery_app)

for name in ("image_smoke_deregistered_fetcher", "image_smoke_malformed_fetcher"):
    key = RedBeatSchedulerEntry.generate_key(celery_app, name)
    entry = RedBeatSchedulerEntry.from_key(key, app=celery_app)
    assert entry.task == "run_fetcher", (name, entry.task)

print("ENTRIES-PRESENT-OK")
"""

# Polls (bounded) until both corrupted entries are gone — Beat's own
# startup reconciliation runs asynchronously relative to the container
# being reported "running" (no healthcheck exists for `beat`) — then
# confirms the static `cleanup_sessions` entry was left untouched.
# `ensure_conf()` — see `_VERIFY_ENTRIES_PRESENT_SCRIPT` above for why
# it is called explicitly here.
_VERIFY_RECONCILIATION_CLEANUP_SCRIPT = """
import time

from redbeat import RedBeatSchedulerEntry
from redbeat.schedulers import ensure_conf

from app.celery_app import celery_app

ensure_conf(celery_app)


def _is_gone(name: str) -> bool:
    key = RedBeatSchedulerEntry.generate_key(celery_app, name)
    try:
        RedBeatSchedulerEntry.from_key(key, app=celery_app)
    except KeyError:
        return True
    return False


deadline = time.monotonic() + 20
deregistered_gone = malformed_gone = False
while time.monotonic() < deadline:
    deregistered_gone = _is_gone("image_smoke_deregistered_fetcher")
    malformed_gone = _is_gone("image_smoke_malformed_fetcher")
    if deregistered_gone and malformed_gone:
        break
    time.sleep(1)

assert deregistered_gone, "deregistered entry was not removed by reconciliation"
assert malformed_gone, "malformed entry was not removed by reconciliation"

key = RedBeatSchedulerEntry.generate_key(celery_app, "cleanup_sessions")
entry = RedBeatSchedulerEntry.from_key(key, app=celery_app)
assert entry.task == "cleanup_sessions", entry.task
print("RECONCILE-CLEANUP-OK")
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
class TestWorkerStartup:
    """The packaged worker loads representative production tasks."""

    def test_representative_tasks_registered_and_test_fetcher_excluded(
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
            assert "run_fetcher" in registered_tasks, registered_tasks
            assert "evaluate_test_pipeline" not in registered_tasks, registered_tasks

        registry_result = compose_exec(
            "worker",
            "python",
            "-c",
            "import app.celery_app\n"
            "from app.services.base_fetcher import FETCHER_REGISTRY\n"
            "assert 'evaluate_test_pipeline' not in FETCHER_REGISTRY, "
            "FETCHER_REGISTRY.keys()\n"
            "print('SYSTEM-FETCHER-ABSENT-OK')",
        )
        assert registry_result.returncode == 0, (
            f"expected exit 0 for FETCHER_REGISTRY exclusion check "
            f"(stdout={registry_result.stdout!r}, stderr={registry_result.stderr!r})"
        )
        assert "SYSTEM-FETCHER-ABSENT-OK" in registry_result.stdout


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


@pytest.mark.image
class TestWorkerStartupBootstrapFailFast:
    """docs/features/platform/fetcher-infrastructure.md (Worker Startup
    Handler): the `celeryd_after_setup` handler exits the worker
    process with a non-zero code when the fetcher config bootstrap
    fails (e.g. PostgreSQL unreachable) — the worker must never start
    consuming tasks in that case.

    Uses an isolated, independently-named compose project (not the
    primary stack shared by every other test in this suite) so this
    scenario's deliberately-broken worker `DATABASE_URL` cannot affect
    other tests. `up()` is restricted to the `worker` service (see
    `IsolatedComposeStack.up()`) so this isolated project never starts
    its own `api` container, which would otherwise collide with the
    primary stack's published host port. The broken `DATABASE_URL`
    targets this isolated project's own `postgres` service on a closed
    port (`:1`) rather than an unresolvable hostname, so the connection
    is refused immediately instead of depending on DNS-resolution
    timing.
    """

    def test_unreachable_database_prevents_worker_from_starting(
        self, isolated_compose_stack: IsolatedComposeStack
    ) -> None:
        override = (
            "services:\n"
            "  worker:\n"
            "    environment:\n"
            "      DATABASE_URL: postgresql+asyncpg://sentinel:sentinel@"
            "postgres:1/sentinel\n"
        )

        isolated_compose_stack.up(override, "worker")
        isolated_compose_stack.wait_until_exited("worker")

        logs = isolated_compose_stack.logs("worker")
        assert "worker_startup_failed" in logs, (
            f"expected the fail-fast log marker in worker logs: {logs!r}"
        )
        assert "worker_startup_completed" not in logs, (
            f"worker unexpectedly completed startup despite the "
            f"unreachable database: {logs!r}"
        )


@pytest.mark.image
class TestWorkerStartupPoolValidationFailFast:
    """docs/deployment.md (Celery Worker Pool Requirement) and
    docs/features/platform/fetcher-infrastructure.md (Worker Startup
    Handler, step 1): a worker started with any pool other than
    `prefork` must exit with a non-zero code — before the fetcher
    config bootstrap runs and before the consumer starts accepting
    tasks.

    Uses an isolated, independently-named compose project (not the
    primary stack shared by every other test in this suite) — see
    `TestWorkerStartupBootstrapFailFast` above for why (avoiding a
    host-port collision with the primary stack's `api` container). The
    worker's `command` is overridden to `--pool=solo` (Compose replaces
    the base service's `command` list entirely, it does not append);
    no `DATABASE_URL` override is needed here, since the pool
    validation gate runs — and fails — before any database access is
    attempted.
    """

    def test_solo_pool_prevents_worker_from_starting(
        self, isolated_compose_stack: IsolatedComposeStack
    ) -> None:
        override = (
            "services:\n"
            "  worker:\n"
            "    command:\n"
            "      - celery\n"
            "      - -A\n"
            "      - app.celery_app\n"
            "      - worker\n"
            "      - --pool=solo\n"
            "      - --loglevel=info\n"
        )

        isolated_compose_stack.up(override, "worker")
        isolated_compose_stack.wait_until_exited("worker")

        logs = isolated_compose_stack.logs("worker")
        assert "worker_startup_failed" in logs, (
            f"expected the fail-fast log marker in worker logs: {logs!r}"
        )
        assert "worker_pool_validation" in logs, (
            f"expected the pool-validation stage marker in worker logs: {logs!r}"
        )
        assert "worker_startup_completed" not in logs, (
            f"worker unexpectedly completed startup despite the "
            f"unsupported pool: {logs!r}"
        )


@pytest.mark.image
class TestBeatStartupBootstrapFailFast:
    """docs/features/platform/fetcher-infrastructure.md (Startup
    Reconciliation, Wiring Mechanism): the `beat_init` handler exits
    the Beat process with a non-zero code when the fetcher config
    bootstrap fails — Beat must never begin its tick loop in that case.

    Uses an isolated, independently-named compose project (not the
    primary stack shared by every other test in this suite) so this
    scenario's deliberately-broken Beat `DATABASE_URL` cannot affect
    other tests. `up()` is restricted to the `beat` service (see
    `IsolatedComposeStack.up()`) so this isolated project never starts
    its own `api` container, which would otherwise collide with the
    primary stack's published host port. `beat` has no container
    healthcheck (see docker-compose.smoke.yml), so `up()`'s return code
    cannot be trusted as a pass/fail signal here — `wait_until_exited()`
    and a log-content assertion are used instead. The broken
    `DATABASE_URL` targets this isolated project's own `postgres`
    service on a closed port (`:1`) rather than an unresolvable
    hostname, so the connection is refused immediately instead of
    depending on DNS-resolution timing.
    """

    def test_unreachable_database_prevents_beat_from_starting(
        self, isolated_compose_stack: IsolatedComposeStack
    ) -> None:
        override = (
            "services:\n"
            "  beat:\n"
            "    environment:\n"
            "      DATABASE_URL: postgresql+asyncpg://sentinel:sentinel@"
            "postgres:1/sentinel\n"
        )

        isolated_compose_stack.up(override, "beat")
        isolated_compose_stack.wait_until_exited("beat")

        logs = isolated_compose_stack.logs("beat")
        assert "beat_startup_failed" in logs, (
            f"expected the fail-fast log marker in beat logs: {logs!r}"
        )
        assert "beat_startup_completed" not in logs, (
            f"beat unexpectedly completed startup despite the "
            f"unreachable database: {logs!r}"
        )


@pytest.mark.image
class TestBeatScheduleReconciliation:
    """docs/features/platform/fetcher-infrastructure.md (Startup
    Reconciliation, Reconciliation Steps): a Beat restart removes
    deregistered and malformed `run_fetcher`-tasked redbeat entries via
    RedBeat's public API only, while a worker restart never writes to
    or removes any redbeat entry, and the static `cleanup_sessions`
    entry survives both untouched.

    `FETCHER_REGISTRY` has no production fetcher yet (see module
    docstring), so this suite cannot exercise reconciliation steps 2-3
    (write an enabled fetcher's entry / remove a disabled one) against
    a real registered fetcher — that positive path, together with the
    PostgreSQL-authority and idempotency guarantees, is covered against
    real Postgres/Redis by
    `tests/test_services/test_fetcher_schedule.py`. What IS
    container-observable here is step 4 (deregistered/malformed
    cleanup) and the "workers never write schedule entries" invariant,
    both of which operate independently of the current registry
    content.
    """

    def test_worker_restart_preserves_then_beat_restart_removes_corrupted_entries(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
        compose_restart: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        seed_result = compose_exec(
            "beat", "python", "-c", _SEED_CORRUPTED_ENTRIES_SCRIPT
        )
        assert seed_result.returncode == 0, (
            f"expected exit 0 seeding corrupted entries "
            f"(stdout={seed_result.stdout!r}, stderr={seed_result.stderr!r})"
        )
        assert "SEED-OK" in seed_result.stdout

        worker_restart = compose_restart("worker")
        assert worker_restart.returncode == 0, (
            f"expected exit 0 restarting worker "
            f"(stdout={worker_restart.stdout!r}, stderr={worker_restart.stderr!r})"
        )
        verify_present = compose_exec(
            "worker", "python", "-c", _VERIFY_ENTRIES_PRESENT_SCRIPT
        )
        assert verify_present.returncode == 0, (
            f"expected exit 0 verifying entries survived worker restart "
            f"(stdout={verify_present.stdout!r}, stderr={verify_present.stderr!r})"
        )
        assert "ENTRIES-PRESENT-OK" in verify_present.stdout

        beat_restart = compose_restart("beat")
        assert beat_restart.returncode == 0, (
            f"expected exit 0 restarting beat "
            f"(stdout={beat_restart.stdout!r}, stderr={beat_restart.stderr!r})"
        )
        verify_cleanup = compose_exec(
            "beat",
            "python",
            "-c",
            _VERIFY_RECONCILIATION_CLEANUP_SCRIPT,
            timeout=30.0,
        )
        assert verify_cleanup.returncode == 0, (
            f"expected exit 0 verifying reconciliation cleanup "
            f"(stdout={verify_cleanup.stdout!r}, stderr={verify_cleanup.stderr!r})"
        )
        assert "RECONCILE-CLEANUP-OK" in verify_cleanup.stdout
