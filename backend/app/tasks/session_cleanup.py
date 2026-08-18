"""Celery task: weekly session cleanup.

See `docs/features/identity/authentication.md` (Session cleanup) for
the authoritative contract this module implements, and
`docs/features/platform/fetcher-infrastructure.md` (Non-Fetcher
Periodic Tasks) for the static Beat registration mechanism this task
uses (see `app/celery_app.py`).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog

from app.celery_app import celery_app
from app.database import async_session_factory, engine
from app.services.session_service import cleanup_sessions

logger = structlog.get_logger(__name__)


async def run_cleanup_sessions() -> int:
    """Async workflow: delete eligible sessions in one committed transaction.

    Logs `session_cleanup_started` at INFO before the transaction and
    `session_cleanup_completed` at INFO with `deleted_count` after
    commit — neither event contains a user or session identifier (see
    authentication.md, Session cleanup). Opens one session, calls
    `cleanup_sessions(db, now)` with one UTC snapshot, commits once on
    success, and rolls back once when an exception escapes (the
    exception then propagates to the Celery task wrapper).

    This function is repeatedly invoked within the same long-lived
    Celery worker child. `engine.dispose()` is awaited in a `finally`
    block so no pooled connection outlives this invocation's
    `asyncio.run()` event loop — see `docs/conventions.md` (Cross-loop
    pooled connection lifecycle).
    """
    try:
        now = datetime.now(UTC)
        logger.info("session_cleanup_started")
        async with async_session_factory() as session:
            try:
                deleted_count = await cleanup_sessions(session, now)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        logger.info("session_cleanup_completed", deleted_count=deleted_count)
        return deleted_count
    finally:
        await engine.dispose()


def _cleanup_sessions_sync() -> int:
    """Thin synchronous Celery wrapper — calls `asyncio.run()` exactly
    once per invocation to execute `run_cleanup_sessions()`.

    Registered as a Celery task via an explicit function call below
    (rather than `@celery_app.task(...)` decorator syntax) so this
    function itself remains fully typed — Celery's task decorator has
    no type stubs (see the mypy override for `celery.*` in
    `pyproject.toml`), matching the same rationale documented in
    `app/celery_app.py` for its explicit `.connect(...)` signal
    handlers.
    """
    return asyncio.run(run_cleanup_sessions())


cleanup_sessions_task = celery_app.task(name="cleanup_sessions")(_cleanup_sessions_sync)
