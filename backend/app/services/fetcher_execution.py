"""Atomic fetcher run acquisition.

See `docs/features/platform/fetcher-infrastructure.md` (Concurrency
Control — Atomic Run Acquisition Protocol, Stale Run Detection) for the
full specification this module implements.

This module is called exclusively by the `run_fetcher` Celery task
wrapper (`app/tasks/fetchers.py`). It is not an API-facing service —
it raises no HTTP-mapped exceptions. Every exception defined or raised
here either propagates uncaught to Celery (permanent task failure, no
retry — see fetcher-infrastructure.md, Celery Integration, "No
top-level retry") or is a plain `ValueError` per the specification's
literal text for manual `run_id` rejection.

Registry lookup (`FETCHER_REGISTRY` membership) is the caller's
responsibility: the task wrapper determines whether `fetcher_name` is
a known, registered fetcher before calling `acquire_fetcher_run()`.
This module only handles fetchers that are registered but may be
disabled, and the run-record acquisition/adoption/stale-recovery
protocol for those fetchers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import FetcherRunStatus, FetcherRunTriggeredBy
from app.models.fetcher_config import FetcherConfig
from app.models.fetcher_run import FetcherRun
from app.services.base_fetcher import FetcherRunConfig

logger = structlog.get_logger(__name__)

# Hardcoded stale-detection margin (seconds) — see
# fetcher-infrastructure.md (Stale Run Detection). Not configurable.
_STALE_MARGIN_SECONDS = 60


class FetcherConfigMissingError(RuntimeError):
    """Raised when a registered fetcher has no `FetcherConfig` row.

    Indicates `bootstrap_fetcher_configs()`
    (`app/services/fetcher_bootstrap.py`) has not yet run for this
    fetcher — see fetcher-infrastructure.md (Data Model —
    FetcherConfig). Propagates uncaught to the `run_fetcher` task
    wrapper — no retry is attempted.
    """


@dataclass(frozen=True)
class FetcherAcquisition:
    """A successfully acquired (or adopted) `FetcherRun`, ready for
    `BaseFetcher.run()`.

    `run_id` identifies the committed `running` row — freshly inserted
    for a scheduled trigger, or adopted for a manual trigger. `config`
    is the immutable runtime configuration snapshot taken under the
    same `FetcherConfig` lock.
    """

    run_id: UUID
    config: FetcherRunConfig


async def acquire_fetcher_run(
    db: AsyncSession,
    *,
    fetcher_name: str,
    triggered_by: FetcherRunTriggeredBy,
    run_id: UUID | None,
    now: datetime,
) -> FetcherAcquisition | None:
    """Lock `FetcherConfig`, evaluate active/stale run state, and
    acquire (schedule) or adopt (manual) the `FetcherRun` for
    execution.

    Implements steps 1-6 of "Atomic Run Acquisition Protocol" in
    `docs/features/platform/fetcher-infrastructure.md`. The caller
    (the `run_fetcher` task wrapper) owns the transaction: it commits
    after this function returns (including a `None` return) and rolls
    back only if an exception propagates from this function.

    Assumes `fetcher_name` is present in `FETCHER_REGISTRY` — the
    caller checks registry membership before invoking this function.
    `triggered_by` and `run_id` are assumed already validated for
    mutual consistency (`SCHEDULE` implies `run_id is None`, `MANUAL`
    implies `run_id is not None`) — the caller performs that
    validation before invoking this function.

    Returns:
        `FetcherAcquisition` when a run was created or adopted and
        execution should proceed. `None` when the caller should return
        without executing the fetcher: the fetcher is disabled, a
        non-stale scheduled duplicate was discarded, or the manually
        supplied run was already finalized (e.g., by broker-failure
        handling).

    Raises:
        FetcherConfigMissingError: no `FetcherConfig` row exists for
            `fetcher_name`.
        ValueError: (manual trigger only) the supplied `run_id` does
            not exist, or exists for a different `fetcher_name`.
    """
    result = await db.execute(
        select(FetcherConfig)
        .where(FetcherConfig.fetcher_name == fetcher_name)
        .with_for_update()
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise FetcherConfigMissingError(
            f"No FetcherConfig row for registered fetcher '{fetcher_name}'"
        )

    if not config.enabled:
        if run_id is not None:
            await finalize_manual_run_as_failure(
                db,
                run_id=run_id,
                fetcher_name=fetcher_name,
                error_message="Fetcher disabled between trigger and execution",
                now=now,
            )
        else:
            logger.debug("fetcher_disabled_skipping_run", fetcher_name=fetcher_name)
        return None

    run_config = FetcherRunConfig(
        fetcher_name=fetcher_name,
        enabled=config.enabled,
        run_timeout=config.run_timeout,
        request_delay=config.request_delay,
        custom_settings=config.custom_settings,
        schedule_override=config.schedule_override,
    )

    active_result = await db.execute(
        select(FetcherRun).where(
            FetcherRun.fetcher_name == fetcher_name,
            FetcherRun.status == FetcherRunStatus.RUNNING.value,
        )
    )
    active_run = active_result.scalar_one_or_none()

    if active_run is not None:
        elapsed = (now - active_run.started_at).total_seconds()
        stale_threshold = config.run_timeout + _STALE_MARGIN_SECONDS
        if elapsed > stale_threshold:
            _mark_run_stale(
                active_run,
                now=now,
                run_timeout=config.run_timeout,
                fetcher_name=fetcher_name,
            )
        elif run_id is None:
            logger.info(
                "scheduled_run_skipped_active_run_exists",
                fetcher_name=fetcher_name,
            )
            return None
        # else: manual trigger — the active row IS the supplied
        # run_id (already guarded at the API level). Fall through to
        # the adoption logic below, which re-fetches and validates it
        # explicitly by identity.

    if run_id is None:
        new_run = FetcherRun(
            fetcher_name=fetcher_name,
            status=FetcherRunStatus.RUNNING.value,
            triggered_by=FetcherRunTriggeredBy.SCHEDULE.value,
            started_at=now,
        )
        db.add(new_run)
        await db.flush()
        return FetcherAcquisition(run_id=new_run.id, config=run_config)

    run = await db.get(FetcherRun, run_id)
    if run is None:
        logger.error(
            "manual_run_not_found", run_id=str(run_id), fetcher_name=fetcher_name
        )
        raise ValueError(f"FetcherRun '{run_id}' not found")
    if run.fetcher_name != fetcher_name:
        logger.error(
            "manual_run_fetcher_mismatch",
            run_id=str(run_id),
            fetcher_name=fetcher_name,
            actual_fetcher_name=run.fetcher_name,
        )
        raise ValueError(
            f"FetcherRun '{run_id}' belongs to fetcher '{run.fetcher_name}', "
            f"not '{fetcher_name}'"
        )
    if run.status != FetcherRunStatus.RUNNING.value:
        logger.info(
            "manual_run_already_finalized",
            run_id=str(run_id),
            status=run.status,
        )
        return None

    return FetcherAcquisition(run_id=run.id, config=run_config)


async def finalize_manual_run_as_failure(
    db: AsyncSession,
    *,
    run_id: UUID,
    fetcher_name: str,
    error_message: str,
    now: datetime,
) -> None:
    """Finalize a manually pre-created `FetcherRun` as an immediate
    failure with zero duration and no accompanying audit event.

    Shared by two acquisition-time failure paths where the fetcher
    never executes: the fetcher was deregistered (caller: the
    `run_fetcher` task wrapper, before any `FetcherConfig` lock is
    taken), or the fetcher was disabled (caller: `acquire_fetcher_run`
    above, under the `FetcherConfig` lock). See
    `docs/features/platform/fetcher-infrastructure.md` (Celery
    Integration — Unknown and deregistered fetcher handling; and
    Concurrency Control — Atomic Run Acquisition Protocol, step 2).

    Sets `status = failure`, `error_message` to the caller-supplied
    message, `finished_at = now`, `duration_seconds = 0`. Flushes but
    does not commit — the caller's transaction owns durability.

    Raises:
        ValueError: `run_id` does not identify an existing
            `FetcherRun`.
    """
    run = await db.get(FetcherRun, run_id)
    if run is None:
        logger.error(
            "manual_run_finalization_target_missing",
            run_id=str(run_id),
            fetcher_name=fetcher_name,
        )
        raise ValueError(f"FetcherRun '{run_id}' not found")
    run.status = FetcherRunStatus.FAILURE.value
    run.error_message = error_message
    run.finished_at = now
    run.duration_seconds = 0
    await db.flush()


def _mark_run_stale(
    run: FetcherRun, *, now: datetime, run_timeout: int, fetcher_name: str
) -> None:
    """Finalize a stale `FetcherRun` in place (caller flushes/commits).

    See `docs/features/platform/fetcher-infrastructure.md` (Stale Run
    Detection) for the exact field values. The log event follows the
    project's structlog convention (`docs/conventions.md`, Logging) —
    a snake_case event name with structured keyword context — rather
    than the spec's illustrative message text.
    """
    elapsed = (now - run.started_at).total_seconds()
    run.status = FetcherRunStatus.FAILURE.value
    run.error_message = (
        f"Marked as stale (running for {elapsed:.0f}s, timeout {run_timeout}s)"
    )
    run.finished_at = now
    run.duration_seconds = elapsed
    logger.warning(
        "fetcher_run_marked_stale",
        run_id=str(run.id),
        fetcher_name=fetcher_name,
        started_at=run.started_at.isoformat(),
        run_timeout=run_timeout,
    )
