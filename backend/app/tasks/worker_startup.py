"""Celery worker startup — pool validation, fetcher config bootstrap,
and engine disposal.

See `docs/features/platform/fetcher-infrastructure.md` (Worker Startup
Handler) and `docs/deployment.md` (Celery Worker Pool Requirement) for
the full specification this module implements.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import structlog
from celery.concurrency import get_implementation
from celery.signals import celeryd_after_setup

from app.database import async_session_factory, engine
from app.services.fetcher_bootstrap import bootstrap_fetcher_configs

logger = structlog.get_logger(__name__)


class _WorkerPoolValidationError(Exception):
    """Raised internally when the resolved worker pool is not `prefork`.

    Carries the best-effort qualified name of the offending pool class
    for structured logging. Never escapes `_worker_startup_handler` —
    it is caught and translated into a fail-fast `sys.exit(1)`.
    """

    def __init__(self, pool_class: str) -> None:
        self.pool_class = pool_class
        super().__init__(f"Unsupported Celery worker pool: {pool_class}")


def _qualified_name(cls: object) -> str:
    """Best-effort `module.qualname` for a class, for diagnostic logging.

    Returns `"unknown"` if either attribute is unavailable — this
    function must never raise, since it is used to describe the
    offending value in an already-failing validation path.
    """
    module = getattr(cls, "__module__", None)
    name = getattr(cls, "__qualname__", None) or getattr(cls, "__name__", None)
    if module and name:
        return f"{module}.{name}"
    return "unknown"


def _validate_worker_pool(instance: Any) -> None:
    """Reject any worker pool other than Celery's `prefork` implementation.

    See `docs/features/platform/fetcher-infrastructure.md` (Worker
    Startup Handler, step 1) and `docs/deployment.md` (Celery Worker
    Pool Requirement) for the full rationale: the fetcher framework's
    Running Stale Threshold assumes the Celery hard time limit reliably
    terminates an over-limit task — a guarantee only the `prefork` pool
    provides.

    `instance` is the `celeryd_after_setup` signal's `instance` keyword
    argument (the `WorkController` the signal was sent from), whose
    `pool_cls` attribute already holds the resolved concrete pool class
    by the time this signal fires.

    Compares `instance.pool_cls` for identity against the class Celery's
    own public alias resolution returns for `"prefork"`
    (`celery.concurrency.get_implementation("prefork")`) — not a
    hardcoded internal module path, so the check remains correct if a
    future Celery version reorganizes `celery.concurrency`'s internal
    layout while preserving the `--pool=prefork` alias.

    Raises `_WorkerPoolValidationError` if `instance.pool_cls` cannot be
    read, if the `prefork` alias cannot be resolved, or if the resolved
    pool class does not match by identity. All three cases are treated
    identically — the caller does not distinguish "wrong pool" from
    "pool undeterminable".
    """
    try:
        pool_cls = instance.pool_cls
        expected = get_implementation("prefork")
    except Exception as exc:
        raise _WorkerPoolValidationError("unknown") from exc
    if pool_cls is not expected:
        raise _WorkerPoolValidationError(_qualified_name(pool_cls))


async def worker_async_bootstrap() -> None:
    """Bootstrap `FetcherConfig` rows, then dispose the parent engine's
    pooled connections before Celery forks worker children.

    Opens one session, calls `bootstrap_fetcher_configs(db)`, commits
    once on success or rolls back once on failure — exceptions from
    either step propagate to the caller uncaught. Disposal
    (`engine.dispose()`) only runs after a successful commit, closing
    the parent process's pooled connections so forked prefork children
    do not inherit live connections from the parent — must happen
    inside this coroutine's event loop because `AsyncEngine.dispose()`
    itself is a coroutine.
    """
    async with async_session_factory() as session:
        try:
            await bootstrap_fetcher_configs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


def _worker_startup_handler(instance: Any = None, **_kwargs: Any) -> None:
    """`celeryd_after_setup` receiver — pool validation then fetcher
    config bootstrap, both fail-fast.

    Step 1: validates the resolved worker pool via
    `_validate_worker_pool(instance)`. On failure, logs CRITICAL
    `worker_startup_failed` with `stage="worker_pool_validation"` and
    `pool_class`, then calls `sys.exit(1)` — the fetcher config
    bootstrap (step 2) is NOT attempted.

    Step 2: calls `asyncio.run(worker_async_bootstrap())` exactly once.
    On success, logs INFO `worker_startup_completed`. On any exception
    (bootstrap, commit, or disposal failure), logs CRITICAL
    `worker_startup_failed` with `stage="fetcher_config_bootstrap"`,
    `error_type`, and `error`, then calls `sys.exit(1)`.

    `sys.exit(1)` (raising `SystemExit`, a `BaseException`) is required
    because Celery's signal dispatcher catches ordinary `Exception`
    raised by receivers — only `SystemExit` propagates through it to
    abort the worker before it starts consuming tasks.
    """
    try:
        _validate_worker_pool(instance)
    except Exception as exc:
        pool_class = (
            exc.pool_class if isinstance(exc, _WorkerPoolValidationError) else "unknown"
        )
        logger.critical(
            "worker_startup_failed",
            stage="worker_pool_validation",
            pool_class=pool_class,
        )
        sys.exit(1)

    try:
        asyncio.run(worker_async_bootstrap())
    except Exception as exc:
        logger.critical(
            "worker_startup_failed",
            stage="fetcher_config_bootstrap",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        sys.exit(1)
    logger.info("worker_startup_completed")


# Explicit `.connect(...)` (rather than the `@celeryd_after_setup.connect`
# decorator form) for the same reason documented in `app/celery_app.py`:
# `celery.signals` ships no type stubs, and the decorator form would make
# mypy infer `_worker_startup_handler` as untyped under strict mode.
celeryd_after_setup.connect(
    _worker_startup_handler,
    dispatch_uid="sentinel.tasks.worker_startup.celeryd_after_setup",
)
