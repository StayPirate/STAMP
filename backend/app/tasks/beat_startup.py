"""Celery Beat startup — fetcher config bootstrap (fail-fast).

See `docs/features/platform/fetcher-infrastructure.md` (Startup
Reconciliation, Wiring Mechanism) for the full specification this
module implements. RedBeat schedule reconciliation is out of scope for
this module — see `docs/drafts/implementation-plan.md` (P3-05).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

# Imported for its side effect: `redbeat.schedulers` registers its own
# `beat_init` receiver (`acquire_distributed_beat_lock`) at import time.
# Importing it here — before this module's own `beat_init.connect(...)`
# call below — guarantees that receiver registers first, so RedBeat's
# distributed lock is acquired before Sentinel's handler runs, even
# though Celery's `beat.Service.start()` would otherwise import
# `redbeat` lazily (via `beat_scheduler` symbol resolution) at an
# import-order-dependent point relative to this module. See
# `docs/features/platform/fetcher-infrastructure.md` (Startup
# Reconciliation) for why this ordering matters for the future RedBeat
# reconciliation step.
import redbeat  # noqa: F401
import structlog
from celery.signals import beat_init

from app.database import async_session_factory
from app.services.fetcher_bootstrap import bootstrap_fetcher_configs

logger = structlog.get_logger(__name__)


async def beat_async_bootstrap() -> None:
    """Bootstrap `FetcherConfig` rows for Celery Beat startup.

    Opens one session, calls `bootstrap_fetcher_configs(db)`, commits
    once on success or rolls back once on failure — exceptions from
    either step propagate to the caller uncaught. This is the first
    operation in Beat's fetcher startup sequence; RedBeat schedule
    reconciliation is added after this step by a later work item.
    """
    async with async_session_factory() as session:
        try:
            await bootstrap_fetcher_configs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _beat_startup_handler(**_kwargs: Any) -> None:
    """`beat_init` receiver — fail-fast fetcher config bootstrap.

    Calls `asyncio.run(beat_async_bootstrap())` exactly once. On
    success, logs INFO `beat_startup_completed`. On any exception, logs
    CRITICAL `beat_startup_failed` with
    `stage="fetcher_config_bootstrap"`, `error_type`, and `error`, then
    calls `sys.exit(1)` — required because Celery's signal dispatcher
    catches ordinary `Exception` raised by receivers; only
    `SystemExit` (a `BaseException`) propagates through it to abort
    Beat before it begins its tick loop.
    """
    try:
        asyncio.run(beat_async_bootstrap())
    except Exception as exc:
        logger.critical(
            "beat_startup_failed",
            stage="fetcher_config_bootstrap",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        sys.exit(1)
    logger.info("beat_startup_completed")


# Explicit `.connect(...)` (rather than the `@beat_init.connect`
# decorator form) for the same reason documented in `app/celery_app.py`:
# `celery.signals` ships no type stubs, and the decorator form would make
# mypy infer `_beat_startup_handler` as untyped under strict mode.
beat_init.connect(
    _beat_startup_handler,
    dispatch_uid="sentinel.tasks.beat_startup.beat_init",
)
