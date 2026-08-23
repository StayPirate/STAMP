"""Fixtures and process-orchestration harness for the local process
system test suite.

See `docs/features/platform/testing-strategy.md` (Local Process System
Testing) for the full behavioral contract these fixtures support:
registration boundary, subprocess environment isolation, bounded
waiting/diagnostics, and deterministic cleanup ordering.

Nothing in this module imports `tests.support.system_fetcher` at module
level — that import (and the `FETCHER_REGISTRY` mutation it triggers as
a side effect) happens exclusively inside `fetcher_pipeline_harness`,
so a `pytest -m 'not system'` collection of this directory never
registers the test-only fetcher in the collecting process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
from celery import Celery
from redbeat import RedBeatSchedulerEntry
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.database import Base
from app.models.fetcher_audit_event import FetcherAuditEvent
from app.models.fetcher_config import FetcherConfig
from app.models.fetcher_run import FetcherRun
from app.services.base_fetcher import FETCHER_REGISTRY
from app.services.fetcher_schedule import delete_fetcher_entry

_BACKEND_DIR = Path(__file__).resolve().parents[2]

# Duplicated from `tests.support.system_fetcher.SYSTEM_FETCHER_NAME` —
# deliberately NOT imported from that module at module level. That
# module's top-level import would execute `EvaluateTestPipeline`'s class
# body (registering it into `FETCHER_REGISTRY` as an
# `__init_subclass__` side effect) as soon as this `conftest.py` is
# collected — which pytest does unconditionally for every directory it
# walks, regardless of `-m 'not system'` deselection (deselection is a
# post-collection step). The `fetcher_pipeline_harness` fixture below
# imports that module lazily, inside the fixture body, so registration
# only ever happens for a test that actually requests the harness.
SYSTEM_FETCHER_NAME = "evaluate_test_pipeline"

# Tables owned by the fetcher infrastructure — excluded from the
# domain-mutation snapshot (see testing-strategy.md, Behavioral
# Requirements, point 6: "No domain tables are mutated").
_FETCHER_TABLE_NAMES = frozenset(
    {"fetcher_config", "fetcher_run", "fetcher_audit_event"}
)

# Bounded timeouts (seconds) — see testing-strategy.md (Bounded Waiting
# and Diagnostics). Generous enough for a loaded CI runner; still finite.
_WORKER_READY_TIMEOUT = 30.0
_CONFIG_BOOTSTRAP_TIMEOUT = 20.0
_REDBEAT_ENTRY_TIMEOUT = 20.0
_RUN_FINALIZED_TIMEOUT = 30.0
_PROCESS_TERM_TIMEOUT = 10.0
_POLL_INTERVAL = 0.5
_INSPECT_TIMEOUT = 5.0

# Fictional JWT secret — never a real credential (AGENTS.md Guardrail 23).
_SYSTEM_TEST_JWT_SECRET_KEY = "system-test-jwt-secret-key-not-for-production-32ch"


@pytest.fixture
def system_process_env(_engine: AsyncEngine, _redis_test_url: str) -> dict[str, str]:
    """Explicit environment for spawned worker/Beat subprocesses.

    Starts from a copy of the current process environment (so `PATH`,
    the venv, locale, etc. resolve normally) but unconditionally
    overrides every application-configured value the spawned process
    reads at `Settings()` construction time — `DATABASE_URL`,
    `CELERY_BROKER_URL`, `REDIS_URL`, `JWT_SECRET_KEY`,
    `CELERY_TIMEZONE`, `CELERY_ENABLE_UTC` — so the subprocess can never
    observe a developer's local `.env` or shell-exported value for any
    of them (see testing-strategy.md, Registration Boundary). Environment
    variables take precedence over `.env` file values in pydantic-settings,
    so this override is sufficient regardless of `backend/.env` content.

    `DATABASE_URL` is derived from the same test engine `_engine` (and
    therefore the same physical PostgreSQL database) the rest of the
    suite uses — `render_as_string(hide_password=False)` is required
    because `str(engine.url)` masks the password (see
    `test_cross_loop_engine_lifecycle.py` for the identical pattern).
    `CELERY_BROKER_URL`/`REDIS_URL` both point at this pytest worker's
    dedicated Redis logical database (`_redis_test_url`).
    """
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": _engine.url.render_as_string(hide_password=False),
            "CELERY_BROKER_URL": _redis_test_url,
            "REDIS_URL": _redis_test_url,
            "JWT_SECRET_KEY": _SYSTEM_TEST_JWT_SECRET_KEY,
            "CELERY_TIMEZONE": "UTC",
            "CELERY_ENABLE_UTC": "true",
        }
    )
    return env


@dataclass
class _SpawnedProcess:
    """A worker or Beat subprocess spawned via the test-owned launcher
    (`tests.support.system_fetcher_app`), with its combined stdout/stderr
    captured to a per-process log file for failure diagnostics.
    """

    name: str
    popen: subprocess.Popen[bytes]
    log_path: Path
    _log_fh: Any = field(repr=False)

    def is_alive(self) -> bool:
        return self.popen.poll() is None

    def assert_alive(self) -> None:
        """Fail immediately, with captured log output, if this process
        already exited — per testing-strategy.md: "Early failure if the
        worker or Beat process exits unexpectedly."
        """
        code = self.popen.poll()
        if code is not None:
            pytest.fail(
                f"{self.name} process exited unexpectedly with code {code}.\n"
                f"--- {self.name} log (tail) ---\n{self.tail_log()}"
            )

    def tail_log(self, max_bytes: int = 4000) -> str:
        try:
            data = self.log_path.read_bytes()
        except OSError:
            return "<log unavailable>"
        return data[-max_bytes:].decode("utf-8", errors="replace")

    def terminate(self, timeout: float) -> None:
        """SIGTERM the process group; escalate to SIGKILL after `timeout`s.

        `start_new_session=True` at spawn time makes this process its
        own session/process-group leader, so `os.killpg(self.popen.pid,
        ...)` reaches it and any child it may have forked (relevant for
        the worker's prefork pool).
        """
        if self.popen.poll() is not None:
            self._close_log()
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.popen.pid, signal.SIGTERM)
        try:
            self.popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.popen.pid, signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.popen.wait(timeout=timeout)
        self._close_log()

    def _close_log(self) -> None:
        if not self._log_fh.closed:
            self._log_fh.close()


def _spawn_celery_process(
    name: str, args: list[str], env: dict[str, str], log_dir: Path
) -> _SpawnedProcess:
    """Spawn `python -m celery -A tests.support.system_fetcher_app <args>`.

    The test-owned launcher module registers `EvaluateTestPipeline`
    before re-exporting the real `app.celery_app.celery_app` singleton
    (see `tests/support/system_fetcher_app.py`) — production entrypoints
    never see this registration since they only ever import
    `app.celery_app` directly.
    """
    log_path = log_dir / f"{name}.log"
    log_fh = log_path.open("wb")
    popen = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "tests.support.system_fetcher_app",
            *args,
        ],
        cwd=str(_BACKEND_DIR),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return _SpawnedProcess(name=name, popen=popen, log_path=log_path, _log_fh=log_fh)


@dataclass
class FetcherPipelineHarness:
    """Drives the end-to-end scheduled fetcher pipeline against real
    worker/Beat subprocesses and real PostgreSQL/Redis test
    infrastructure. See `test_fetcher_pipeline.py` for the test that
    consumes this harness's methods in sequence.
    """

    env: dict[str, str]
    log_dir: Path
    celery_app: Celery
    session_factory: async_sessionmaker[AsyncSession]
    worker_hostname: str
    worker_process: _SpawnedProcess | None = None
    beat_process: _SpawnedProcess | None = None

    # -- Process lifecycle ---------------------------------------------

    def start_worker(self) -> None:
        self.worker_process = _spawn_celery_process(
            "worker",
            [
                "worker",
                "--pool=prefork",
                "--concurrency=1",
                f"--hostname={self.worker_hostname}",
                "--loglevel=info",
            ],
            self.env,
            self.log_dir,
        )

    def start_beat(self) -> None:
        self.beat_process = _spawn_celery_process(
            "beat",
            ["beat", "--max-interval=5", "--loglevel=info"],
            self.env,
            self.log_dir,
        )

    def stop_beat(self, timeout: float = _PROCESS_TERM_TIMEOUT) -> None:
        """Stop Beat now, before it can fire a second scheduled dispatch.

        Safe to call again from fixture teardown — `_SpawnedProcess.terminate`
        is a no-op once the process has already exited.
        """
        if self.beat_process is not None:
            self.beat_process.terminate(timeout)

    def _run_inspect(self, subcommand: str) -> dict[str, Any] | None:
        """Run `celery inspect <subcommand> --json --destination
        <worker_hostname>` and parse the JSON reply, or return `None` on
        any failure (non-zero exit, timeout, unparsable output) — the
        caller's polling loop treats `None` as "not ready yet"."""
        import json

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "tests.support.system_fetcher_app",
                    "inspect",
                    subcommand,
                    "--json",
                    "--destination",
                    self.worker_hostname,
                ],
                cwd=str(_BACKEND_DIR),
                env=self.env,
                capture_output=True,
                text=True,
                timeout=_INSPECT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return None
        if result.returncode != 0:
            return None
        try:
            reply: dict[str, Any] = json.loads(result.stdout)
        except ValueError:
            return None
        return reply

    def wait_worker_ready(self, timeout: float = _WORKER_READY_TIMEOUT) -> None:
        """Poll until the worker replies to `inspect ping` under its own
        hostname AND has the generic `run_fetcher` task registered.
        """
        assert self.worker_process is not None
        deadline = time.monotonic() + timeout
        last_status = "no successful inspect reply yet"
        while time.monotonic() < deadline:
            self.worker_process.assert_alive()
            ping_reply = self._run_inspect("ping")
            if ping_reply and self.worker_hostname in ping_reply:
                registered_reply = self._run_inspect("registered")
                registered_tasks = (registered_reply or {}).get(
                    self.worker_hostname
                ) or []
                if "run_fetcher" in registered_tasks:
                    return
                last_status = (
                    f"worker replied to ping but run_fetcher not yet "
                    f"registered: {registered_reply!r}"
                )
            else:
                last_status = f"no ping reply yet: {ping_reply!r}"
            time.sleep(_POLL_INTERVAL)
        self.worker_process.assert_alive()
        pytest.fail(
            f"worker did not become ready within {timeout}s: {last_status}\n"
            f"--- worker log (tail) ---\n{self.worker_process.tail_log()}"
        )

    # -- RedBeat ---------------------------------------------------------

    def wait_redbeat_entry(
        self, timeout: float = _REDBEAT_ENTRY_TIMEOUT
    ) -> RedBeatSchedulerEntry:
        """Poll until Beat's startup reconciliation has written the
        canonical redbeat entry for the test fetcher."""
        assert self.beat_process is not None
        key = RedBeatSchedulerEntry.generate_key(self.celery_app, SYSTEM_FETCHER_NAME)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.beat_process.assert_alive()
            try:
                return RedBeatSchedulerEntry.from_key(key, app=self.celery_app)
            except KeyError:
                time.sleep(_POLL_INTERVAL)
        self.beat_process.assert_alive()
        pytest.fail(
            f"RedBeat entry for '{SYSTEM_FETCHER_NAME}' did not appear "
            f"within {timeout}s\n"
            f"--- beat log (tail) ---\n{self.beat_process.tail_log()}"
        )

    def make_due(self, *, minutes_ago: int = 5) -> None:
        """Force the existing redbeat entry to be immediately overdue,
        via RedBeat's own public `reschedule()` API — Beat still performs
        the actual dispatch through the broker on its next tick (bounded
        by `--max-interval=5`); this only removes the wait for a real
        cron boundary. See testing-strategy.md, Behavioral Requirements.
        """
        key = RedBeatSchedulerEntry.generate_key(self.celery_app, SYSTEM_FETCHER_NAME)
        entry = RedBeatSchedulerEntry.from_key(key, app=self.celery_app)
        entry.reschedule(last_run_at=datetime.now(UTC) - timedelta(minutes=minutes_ago))

    # -- PostgreSQL (FetcherConfig / FetcherRun / FetcherAuditEvent) ----

    async def wait_config_row(
        self, timeout_seconds: float = _CONFIG_BOOTSTRAP_TIMEOUT
    ) -> FetcherConfig:
        """Poll until a `FetcherConfig` row exists for the test fetcher
        (created by either the worker's or Beat's startup bootstrap —
        both call the same idempotent `bootstrap_fetcher_configs()`)."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.beat_process is not None:
                self.beat_process.assert_alive()
            async with self.session_factory() as session:
                result = await session.execute(
                    select(FetcherConfig).where(
                        FetcherConfig.fetcher_name == SYSTEM_FETCHER_NAME
                    )
                )
                config = result.scalar_one_or_none()
            if config is not None:
                return config
            await asyncio.sleep(_POLL_INTERVAL)
        pytest.fail(
            f"FetcherConfig row for '{SYSTEM_FETCHER_NAME}' did not appear "
            f"within {timeout_seconds}s"
        )

    async def wait_finalized_run(
        self, timeout_seconds: float = _RUN_FINALIZED_TIMEOUT
    ) -> FetcherRun:
        """Poll until a terminal (`success` or `failure`) `FetcherRun`
        row exists for the test fetcher."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.worker_process is not None:
                self.worker_process.assert_alive()
            if self.beat_process is not None:
                self.beat_process.assert_alive()
            async with self.session_factory() as session:
                result = await session.execute(
                    select(FetcherRun)
                    .where(FetcherRun.fetcher_name == SYSTEM_FETCHER_NAME)
                    .where(FetcherRun.status.in_(["success", "failure"]))
                    .order_by(FetcherRun.created_at.desc())
                )
                run = result.scalars().first()
            if run is not None:
                return run
            await asyncio.sleep(_POLL_INTERVAL)
        worker_log = (
            self.worker_process.tail_log() if self.worker_process else "<no worker>"
        )
        beat_log = self.beat_process.tail_log() if self.beat_process else "<no beat>"
        pytest.fail(
            f"No finalized FetcherRun for '{SYSTEM_FETCHER_NAME}' within "
            f"{timeout_seconds}s\n--- worker log (tail) ---\n{worker_log}\n"
            f"--- beat log (tail) ---\n{beat_log}"
        )

    async def list_runs(self) -> list[FetcherRun]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(FetcherRun).where(FetcherRun.fetcher_name == SYSTEM_FETCHER_NAME)
            )
            return list(result.scalars().all())

    async def list_audit_events(self) -> list[FetcherAuditEvent]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(FetcherAuditEvent).where(
                    FetcherAuditEvent.fetcher_name == SYSTEM_FETCHER_NAME
                )
            )
            return list(result.scalars().all())

    # -- Domain mutation proof -------------------------------------------

    async def snapshot_domain_tables(self) -> dict[str, list[str]]:
        """A comparable snapshot of every table's rows EXCEPT the three
        fetcher-infrastructure tables — see testing-strategy.md,
        Behavioral Requirements: "No domain tables are mutated." Row
        tuples are rendered via `repr()` (not hashed directly) so JSONB
        columns, which are unhashable dicts, do not break comparison.
        """
        async with self.session_factory() as session:
            snapshot: dict[str, list[str]] = {}
            for table in Base.metadata.sorted_tables:
                if table.name in _FETCHER_TABLE_NAMES:
                    continue
                result = await session.execute(select(table))
                snapshot[table.name] = sorted(repr(tuple(row)) for row in result.all())
            return snapshot


@pytest_asyncio.fixture
async def fetcher_pipeline_harness(
    tmp_path: Path,
    celery_test_app: Celery,
    redis_client: redis_asyncio.Redis,
    real_session_factory: async_sessionmaker[AsyncSession],
    system_process_env: dict[str, str],
) -> AsyncIterator[FetcherPipelineHarness]:
    """Registers the test-only fetcher, yields a `FetcherPipelineHarness`,
    then performs deterministic cleanup — in the exact order mandated by
    testing-strategy.md (Deterministic Cleanup) — regardless of whether
    the test succeeded or failed. A cleanup failure fails the test even
    when the primary assertion already passed.

    Depends on `redis_client` (rather than opening a separate Redis
    connection) to reuse its existing FLUSHDB-before/after isolation
    guarantee and connection lifecycle management — this fixture's own
    teardown still performs the mandated `FLUSHDB` at the documented
    step (4), `redis_client`'s own teardown is an additional safety net,
    not a substitute.
    """
    # Registration boundary: import only now — never at collection time
    # — so a `pytest -m 'not system'` run of the wider suite never
    # registers this class (see testing-strategy.md, Registration
    # Boundary).
    import tests.support.system_fetcher as system_fetcher_module

    fetcher_cls = system_fetcher_module.EvaluateTestPipeline
    assert fetcher_cls.name == SYSTEM_FETCHER_NAME, (
        f"tests/system/conftest.py's duplicated SYSTEM_FETCHER_NAME "
        f"constant ({SYSTEM_FETCHER_NAME!r}) has drifted from "
        f"EvaluateTestPipeline.name ({fetcher_cls.name!r})"
    )
    previous_registry_entry = FETCHER_REGISTRY.get(SYSTEM_FETCHER_NAME)
    FETCHER_REGISTRY[SYSTEM_FETCHER_NAME] = fetcher_cls

    async with real_session_factory() as baseline_session:
        baseline_result = await baseline_session.execute(
            select(FetcherConfig.fetcher_name)
        )
        baseline_names = set(baseline_result.scalars().all())

    worker_hostname = f"systemtest-{uuid.uuid4().hex[:10]}@{socket.gethostname()}"
    harness = FetcherPipelineHarness(
        env=system_process_env,
        log_dir=tmp_path,
        celery_app=celery_test_app,
        session_factory=real_session_factory,
        worker_hostname=worker_hostname,
    )

    cleanup_errors: list[str] = []
    try:
        yield harness
    finally:
        # 1. Stop Beat first (prevents new task enqueues). No-op if the
        # test already called stop_beat() itself.
        try:
            harness.stop_beat(_PROCESS_TERM_TIMEOUT)
        except Exception as exc:
            cleanup_errors.append(f"beat termination failed: {exc!r}")

        # 2-3. Worker: bounded grace period, then escalate to SIGKILL —
        # both handled internally by `_SpawnedProcess.terminate`.
        if harness.worker_process is not None:
            try:
                harness.worker_process.terminate(_PROCESS_TERM_TIMEOUT)
            except Exception as exc:
                cleanup_errors.append(f"worker termination failed: {exc!r}")

        # 4. Delete the test redbeat entry through the library's public API.
        try:
            delete_fetcher_entry(celery_test_app, SYSTEM_FETCHER_NAME)
        except Exception as exc:
            cleanup_errors.append(f"redbeat entry deletion failed: {exc!r}")

        # 5. Clear only the designated Redis logical database.
        try:
            await redis_client.flushdb()
        except Exception as exc:
            cleanup_errors.append(f"redis flushdb failed: {exc!r}")

        # 6. Delete committed test rows from PostgreSQL, FK-safe order,
        # restricted to fetcher names absent from the pre-spawn baseline
        # — never a table-wide delete (testing-strategy.md, Parallel
        # Safety). This also removes any `FetcherConfig` row the
        # subprocess registry's bootstrap created for a *production*
        # fetcher, not just the test fetcher's own row.
        try:
            async with real_session_factory() as cleanup_session:
                current_result = await cleanup_session.execute(
                    select(FetcherConfig.fetcher_name)
                )
                created_names = set(current_result.scalars().all()) - baseline_names
                if created_names:
                    await cleanup_session.execute(
                        delete(FetcherRun).where(
                            FetcherRun.fetcher_name.in_(created_names)
                        )
                    )
                    await cleanup_session.execute(
                        delete(FetcherAuditEvent).where(
                            FetcherAuditEvent.fetcher_name.in_(created_names)
                        )
                    )
                    await cleanup_session.execute(
                        delete(FetcherConfig).where(
                            FetcherConfig.fetcher_name.in_(created_names)
                        )
                    )
                    await cleanup_session.commit()
        except Exception as exc:
            cleanup_errors.append(f"postgres row cleanup failed: {exc!r}")

        # 7. Restore/remove the test registry entry in the pytest process
        # — without clearing the global registry.
        if previous_registry_entry is None:
            FETCHER_REGISTRY.pop(SYSTEM_FETCHER_NAME, None)
        else:
            FETCHER_REGISTRY[SYSTEM_FETCHER_NAME] = previous_registry_entry

        # 8. Verify processes are dead and test artifacts are absent.
        for proc in (harness.worker_process, harness.beat_process):
            if proc is not None and proc.is_alive():
                cleanup_errors.append(f"{proc.name} process still alive after cleanup")

        if cleanup_errors:
            pytest.fail(
                "Local process system suite cleanup failed:\n"
                + "\n".join(cleanup_errors)
            )
