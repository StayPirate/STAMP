"""Celery worker startup — fetcher config bootstrap and engine disposal.

See `docs/features/platform/fetcher-infrastructure.md` (Worker Startup
Handler) for the full specification this module implements.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import structlog
from celery.signals import celeryd_after_setup

from app.database import async_session_factory, engine
from app.services.fetcher_bootstrap import bootstrap_fetcher_configs

logger = structlog.get_logger(__name__)


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


def _worker_startup_handler(**_kwargs: Any) -> None:
    """`celeryd_after_setup` receiver — fail-fast fetcher config bootstrap.

    Calls `asyncio.run(worker_async_bootstrap())` exactly once. On
    success, logs INFO `worker_startup_completed`. On any exception
    (bootstrap, commit, or disposal failure), logs CRITICAL
    `worker_startup_failed` with `stage="fetcher_config_bootstrap"`,
    `error_type`, and `error`, then calls `sys.exit(1)`.

    `sys.exit(1)` (raising `SystemExit`, a `BaseException`) is required
    because Celery's signal dispatcher catches ordinary `Exception`
    raised by receivers — only `SystemExit` propagates through it to
    abort the worker before it starts consuming tasks.
    """
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
