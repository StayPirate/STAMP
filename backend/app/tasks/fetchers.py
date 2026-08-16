"""Celery task: generic fetcher execution.

See `docs/features/platform/fetcher-infrastructure.md` (Celery
Integration, Concurrency Control, Stale Run Detection) for the
authoritative contract this module implements.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog

from app.celery_app import celery_app
from app.core.enums import FetcherRunTriggeredBy
from app.database import async_session_factory
from app.services.base_fetcher import FETCHER_REGISTRY
from app.services.fetcher_execution import (
    acquire_fetcher_run,
    finalize_manual_run_as_failure,
)

logger = structlog.get_logger(__name__)


def _is_valid_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _validate_arguments(
    fetcher_name: str,
    triggered_by: str,
    user_id: str | None,
    run_id: str | None,
) -> FetcherRunTriggeredBy:
    """Validate the task's argument combination.

    See `docs/features/platform/fetcher-infrastructure.md` (Celery
    Integration — Accepted argument combinations, Argument validation
    order) for the full contract.

    Raises:
        ValueError: `triggered_by` is neither `"schedule"` nor
            `"manual"`; a manual trigger is missing a valid `user_id`
            or `run_id`; or a scheduled trigger supplies either.
    """
    if triggered_by not in (
        FetcherRunTriggeredBy.SCHEDULE.value,
        FetcherRunTriggeredBy.MANUAL.value,
    ):
        logger.error(
            "run_fetcher_invalid_triggered_by",
            fetcher_name=fetcher_name,
            triggered_by=triggered_by,
        )
        raise ValueError(f"Invalid triggered_by: {triggered_by!r}")

    if triggered_by == FetcherRunTriggeredBy.MANUAL.value:
        if not user_id or not _is_valid_uuid(user_id):
            logger.error(
                "run_fetcher_invalid_manual_user_id", fetcher_name=fetcher_name
            )
            raise ValueError("Manual trigger requires a valid user_id")
        if not run_id or not _is_valid_uuid(run_id):
            logger.error("run_fetcher_invalid_manual_run_id", fetcher_name=fetcher_name)
            raise ValueError("Manual trigger requires a valid run_id")
    else:
        if user_id is not None or run_id is not None:
            logger.error(
                "run_fetcher_unexpected_scheduled_arguments",
                fetcher_name=fetcher_name,
            )
            raise ValueError("Scheduled trigger must not supply user_id or run_id")

    return FetcherRunTriggeredBy(triggered_by)


async def _handle_unknown_fetcher(fetcher_name: str, run_id: UUID | None) -> None:
    """Handle a `fetcher_name` absent from `FETCHER_REGISTRY`.

    See `docs/features/platform/fetcher-infrastructure.md` (Celery
    Integration — Unknown and deregistered fetcher handling). The log
    events follow the project's structlog convention
    (`docs/conventions.md`, Logging) — snake_case event names with
    structured keyword context — rather than the spec's illustrative
    message text.
    """
    if run_id is None:
        logger.warning("scheduled_run_unknown_fetcher", fetcher_name=fetcher_name)
        return

    async with async_session_factory() as session:
        try:
            await finalize_manual_run_as_failure(
                session,
                run_id=run_id,
                fetcher_name=fetcher_name,
                error_message="Fetcher deregistered between trigger and execution",
                now=datetime.now(UTC),
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def run_fetcher_async(
    fetcher_name: str,
    triggered_by: str = "schedule",
    user_id: str | None = None,
    run_id: str | None = None,
) -> None:
    """Validate arguments, acquire/adopt a `FetcherRun`, and execute it.

    See `docs/features/platform/fetcher-infrastructure.md` (Celery
    Integration, Concurrency Control) for the complete contract. Always
    returns `None`; exceptions from argument validation, acquisition,
    or `BaseFetcher.run()` propagate uncaught (no retry — Celery
    result backend is disabled).
    """
    trigger = _validate_arguments(fetcher_name, triggered_by, user_id, run_id)
    parsed_run_id = UUID(run_id) if run_id is not None else None

    if fetcher_name not in FETCHER_REGISTRY:
        await _handle_unknown_fetcher(fetcher_name, parsed_run_id)
        return

    now = datetime.now(UTC)
    async with async_session_factory() as session:
        try:
            acquisition = await acquire_fetcher_run(
                session,
                fetcher_name=fetcher_name,
                triggered_by=trigger,
                run_id=parsed_run_id,
                now=now,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    if acquisition is None:
        return

    fetcher_cls = FETCHER_REGISTRY[fetcher_name]
    fetcher = fetcher_cls()
    await fetcher.run(run_id=acquisition.run_id, config=acquisition.config)


def _run_fetcher_sync(
    # `self` is the bound Celery Task instance (celery ships no stubs —
    # see the mypy override in pyproject.toml). Unused: no
    # `self.retry()` call — see "No top-level retry".
    self: Any,
    fetcher_name: str,
    triggered_by: str = "schedule",
    user_id: str | None = None,
    run_id: str | None = None,
) -> None:
    """Thin synchronous Celery wrapper — calls `asyncio.run()` exactly
    once per invocation to execute `run_fetcher_async()`.

    Registered as a Celery task via an explicit function call below
    (rather than `@celery_app.task(...)` decorator syntax) so this
    function itself remains fully typed — Celery's task decorator has
    no type stubs (see the mypy override for `celery.*` in
    `pyproject.toml`), matching the pattern in
    `app/tasks/session_cleanup.py` and the explicit `.connect(...)`
    signal handlers in `app/celery_app.py`.
    """
    asyncio.run(run_fetcher_async(fetcher_name, triggered_by, user_id, run_id))


run_fetcher = celery_app.task(bind=True, name="run_fetcher")(_run_fetcher_sync)
