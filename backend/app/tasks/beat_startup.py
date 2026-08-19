"""Celery Beat startup — fetcher config bootstrap and RedBeat schedule
reconciliation (fail-fast).

See `docs/features/platform/fetcher-infrastructure.md` (Startup
Reconciliation, Wiring Mechanism) for the full specification this
module implements.
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
# Reconciliation) for why this ordering matters for schedule
# reconciliation below.
import redbeat  # noqa: F401
import structlog
from celery import Celery
from celery.signals import beat_init

from app.database import async_session_factory, engine
from app.services.fetcher_bootstrap import bootstrap_fetcher_configs
from app.services.fetcher_schedule import reconcile_beat_schedule

logger = structlog.get_logger(__name__)


class RedbeatLockNotAcquiredError(RuntimeError):
    """Raised when the redbeat distributed lock was not acquired by the
    time Sentinel's `beat_init` receiver runs.

    Celery's signal dispatcher swallows exceptions raised by receivers
    (see `redbeat.schedulers.acquire_distributed_beat_lock`), so a
    failed lock acquisition is otherwise silent: `scheduler.lock`
    remains `None` while `scheduler.lock_key` is still set. Proceeding
    with reconciliation in that state would risk two Beat instances
    reconciling concurrently — this exception makes the failure
    explicit and fail-fast, mirroring every other startup failure mode
    in this module.
    """


async def beat_async_bootstrap(celery_app: Celery) -> None:
    """Bootstrap `FetcherConfig` rows, then reconcile the redbeat
    schedule, for Celery Beat startup.

    Opens one session, calls `bootstrap_fetcher_configs(db)`, and
    commits once — exceptions from either step trigger a rollback and
    propagate to the caller uncaught. On successful commit, calls
    `reconcile_beat_schedule(db, celery_app)` against the same session
    (a plain read of `FetcherConfig`, followed by redbeat writes) —
    exceptions from this step propagate uncaught as well, with no
    further session action needed since the PostgreSQL changes are
    already durably committed.

    Disposes the bootstrap connections (`await engine.dispose()`) only
    after bootstrap, commit, AND reconciliation all succeed, before
    returning control to `asyncio.run()` — see `docs/conventions.md`
    (Cross-loop pooled connection lifecycle). Beat's own tick loop is
    Celery's native synchronous scheduler and does not itself open
    another event loop today, but the startup loop must not leave a
    pooled connection bound to itself once it closes. Disposal is
    skipped on any failure, since the handler's `sys.exit(1)`
    terminates the process regardless.
    """
    async with async_session_factory() as session:
        try:
            await bootstrap_fetcher_configs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        await reconcile_beat_schedule(session, celery_app)
    await engine.dispose()


def _beat_startup_handler(sender: Any = None, **_kwargs: Any) -> None:
    """`beat_init` receiver — fail-fast fetcher config bootstrap and
    RedBeat schedule reconciliation.

    First verifies that redbeat's distributed lock was actually
    acquired: `sender` is the Celery `beat.Service` instance, whose
    `.scheduler` (already instantiated by Celery before `beat_init` is
    sent) exposes `.lock`, set by RedBeat's own `beat_init` receiver
    (registered before this one — see the import comment above) only
    on successful acquisition. A `None` lock at this point means
    acquisition failed silently (see `RedbeatLockNotAcquiredError`) and
    reconciliation must not proceed.

    Then calls `asyncio.run(beat_async_bootstrap(sender.app))` exactly
    once. On success, logs INFO `beat_startup_completed`. On any
    exception (lock verification, bootstrap, commit, or reconciliation
    failure), logs CRITICAL `beat_startup_failed` with a `stage`
    identifying which phase failed (`"lock_verification"` or
    `"bootstrap_and_reconciliation"`), `error_type`, and `error`, then
    calls `sys.exit(1)` — required because Celery's signal dispatcher
    catches ordinary `Exception` raised by receivers; only
    `SystemExit` (a `BaseException`) propagates through it to abort
    Beat before it begins its tick loop.
    """
    stage = "lock_verification"
    try:
        if sender.scheduler.lock is None:
            raise RedbeatLockNotAcquiredError(
                "RedBeat distributed lock was not acquired before Beat "
                "startup reconciliation"
            )
        stage = "bootstrap_and_reconciliation"
        asyncio.run(beat_async_bootstrap(sender.app))
    except Exception as exc:
        logger.critical(
            "beat_startup_failed",
            stage=stage,
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
