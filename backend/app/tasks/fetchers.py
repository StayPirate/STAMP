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
from app.database import async_session_factory, engine
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


def _extract_hard_time_limit(timelimit: tuple[Any, ...] | list[Any] | None) -> int:
    """Validate and extract the effective hard time limit from Celery's
    per-message time limit header (`self.request.timelimit`).

    See `docs/features/platform/fetcher-infrastructure.md` (Celery
    Integration — Hard time limit extraction) for the full contract.
    Celery 5.x transmits the per-message time limit as a two-element
    sequence `[hard, soft]` (or `None` if no limit was set); this
    function reads the first element.

    Raises:
        ValueError: the hard limit is missing, `None`, not coercible to
            a positive `int`, or outside the range [60, 604800].
    """
    if not timelimit:
        logger.error("run_fetcher_missing_time_limit")
        raise ValueError("Missing Celery hard time limit")

    hard_limit = timelimit[0]
    if hard_limit is None:
        logger.error("run_fetcher_missing_time_limit")
        raise ValueError("Missing Celery hard time limit")

    try:
        hard_limit_int = int(hard_limit)
    except TypeError, ValueError:
        logger.error("run_fetcher_invalid_time_limit", value=repr(hard_limit))
        raise ValueError(f"Invalid Celery hard time limit: {hard_limit!r}") from None

    if not (60 <= hard_limit_int <= 604800):
        logger.error(
            "run_fetcher_time_limit_out_of_range", hard_time_limit=hard_limit_int
        )
        raise ValueError(
            f"Celery hard time limit {hard_limit_int} out of range [60, 604800]"
        )

    return hard_limit_int


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
    *,
    hard_time_limit: int,
) -> None:
    """Validate arguments, acquire/adopt a `FetcherRun`, and execute it.

    See `docs/features/platform/fetcher-infrastructure.md` (Celery
    Integration, Concurrency Control) for the complete contract. Always
    returns `None`; exceptions from argument validation, acquisition,
    or `BaseFetcher.run()` propagate uncaught (no retry — Celery
    result backend is disabled).

    `hard_time_limit` is the already-validated effective Celery hard
    time limit (see `_extract_hard_time_limit`), extracted by the
    synchronous task wrapper before this function is invoked.

    This function is repeatedly invoked within the same long-lived
    Celery worker child. `engine.dispose()` is awaited in a `finally`
    block so no pooled connection outlives this invocation's
    `asyncio.run()` event loop — see `docs/conventions.md` (Cross-loop
    pooled connection lifecycle). This is the single choke point
    through which every fetcher's `execute()` (and any `fetch_single()`
    call made from within it) runs, so disposal here automatically
    protects all of them; individual fetchers do not dispose the engine
    themselves.
    """
    try:
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
                    hard_time_limit=hard_time_limit,
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
    finally:
        await engine.dispose()


def _run_fetcher_sync(
    # `self` is the bound Celery Task instance (celery ships no stubs —
    # see the mypy override in pyproject.toml). Unused beyond time
    # limit extraction: no `self.retry()` call — see "No top-level
    # retry".
    self: Any,
    fetcher_name: str,
    triggered_by: str = "schedule",
    user_id: str | None = None,
    run_id: str | None = None,
) -> None:
    """Thin synchronous Celery wrapper — calls `asyncio.run()` exactly
    once per invocation to execute `run_fetcher_async()`.

    Extracts and validates the effective hard time limit from
    `self.request.timelimit` before invoking the async workflow (see
    `_extract_hard_time_limit`). If extraction fails, the `ValueError`
    propagates immediately — no database or registry operation, and no
    `asyncio.run()` call, occurs.

    Registered as a Celery task via an explicit function call below
    (rather than `@celery_app.task(...)` decorator syntax) so this
    function itself remains fully typed — Celery's task decorator has
    no type stubs (see the mypy override for `celery.*` in
    `pyproject.toml`), matching the pattern in
    `app/tasks/session_cleanup.py` and the explicit `.connect(...)`
    signal handlers in `app/celery_app.py`.
    """
    hard_time_limit = _extract_hard_time_limit(self.request.timelimit)
    asyncio.run(
        run_fetcher_async(
            fetcher_name,
            triggered_by,
            user_id,
            run_id,
            hard_time_limit=hard_time_limit,
        )
    )


run_fetcher = celery_app.task(bind=True, name="run_fetcher")(_run_fetcher_sync)
