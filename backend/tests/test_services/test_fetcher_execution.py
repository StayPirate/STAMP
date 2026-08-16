"""Tests for the atomic fetcher run acquisition service
(backend/app/services/fetcher_execution.py).

See `docs/features/platform/fetcher-infrastructure.md` (Concurrency
Control — Atomic Run Acquisition Protocol, Stale Run Detection) for
the contract under test: `FetcherConfig`-root locking, disabled/active/
stale run evaluation, scheduled acquisition vs. manual adoption, and
the exact stale-threshold boundary (`elapsed > run_timeout + 60`).

Functional (non-concurrency) assertions use the standard `db_session`
fixture — `SELECT ... FOR UPDATE` is a no-op within a single
transaction, but the business logic executes identically (see
`docs/features/platform/testing-strategy.md`, Concurrency Testing).
True lock-serialization assertions use `db_session_factory` with the
canonical two-session pattern from the same section.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import FetcherRunTriggeredBy
from app.models.fetcher_config import FetcherConfig
from app.models.fetcher_run import FetcherRun
from app.services.fetcher_execution import (
    FetcherAcquisition,
    FetcherConfigMissingError,
    acquire_fetcher_run,
    finalize_manual_run_as_failure,
)


def _service_log_text(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "app.services.fetcher_execution"
    )


# ---------------------------------------------------------------------------
# Missing FetcherConfig
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAcquireFetcherRunConfigMissing:
    async def test_missing_config_raises(self, db_session: AsyncSession) -> None:
        with pytest.raises(FetcherConfigMissingError, match="no_such_fetcher"):
            await acquire_fetcher_run(
                db_session,
                fetcher_name="no_such_fetcher",
                triggered_by=FetcherRunTriggeredBy.SCHEDULE,
                run_id=None,
                now=datetime.now(UTC),
            )


# ---------------------------------------------------------------------------
# Disabled fetcher
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAcquireFetcherRunDisabled:
    async def test_scheduled_creates_no_run_and_logs_debug(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config = await fetcher_config_factory(enabled=False)

        with caplog.at_level("DEBUG"):
            result = await acquire_fetcher_run(
                db_session,
                fetcher_name=config.fetcher_name,
                triggered_by=FetcherRunTriggeredBy.SCHEDULE,
                run_id=None,
                now=datetime.now(UTC),
            )

        assert result is None
        rows = (
            (
                await db_session.execute(
                    select(FetcherRun).where(
                        FetcherRun.fetcher_name == config.fetcher_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
        assert "fetcher_disabled_skipping_run" in _service_log_text(caplog)

    async def test_manual_finalizes_supplied_run(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
    ) -> None:
        config = await fetcher_config_factory(enabled=False)
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            triggered_by="manual",
        )
        now = datetime.now(UTC)

        result = await acquire_fetcher_run(
            db_session,
            fetcher_name=config.fetcher_name,
            triggered_by=FetcherRunTriggeredBy.MANUAL,
            run_id=run.id,
            now=now,
        )

        assert result is None
        await db_session.refresh(run)
        assert run.status == "failure"
        assert run.error_message == ("Fetcher disabled between trigger and execution")
        assert run.finished_at == now
        assert run.duration_seconds == 0


# ---------------------------------------------------------------------------
# Scheduled acquisition
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAcquireFetcherRunScheduledCreate:
    async def test_no_active_run_creates_new_running_row(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory(
            run_timeout=1800,
            request_delay=2.5,
            custom_settings={"page_size": 50},
            schedule_override="0 */6 * * *",
        )
        now = datetime.now(UTC)

        result = await acquire_fetcher_run(
            db_session,
            fetcher_name=config.fetcher_name,
            triggered_by=FetcherRunTriggeredBy.SCHEDULE,
            run_id=None,
            now=now,
        )

        assert isinstance(result, FetcherAcquisition)
        assert result.config.fetcher_name == config.fetcher_name
        assert result.config.run_timeout == 1800
        assert result.config.request_delay == 2.5
        assert result.config.custom_settings == {"page_size": 50}
        assert result.config.schedule_override == "0 */6 * * *"

        created = await db_session.get(FetcherRun, result.run_id)
        assert created is not None
        assert created.status == "running"
        assert created.triggered_by == "schedule"
        assert created.started_at == now


@pytest.mark.integration
class TestAcquireFetcherRunScheduledDuplicate:
    async def test_active_non_stale_run_returns_none_and_creates_no_row(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config = await fetcher_config_factory(run_timeout=3600)
        now = datetime.now(UTC)
        active = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            triggered_by="schedule",
            started_at=now,
        )

        with caplog.at_level("INFO"):
            result = await acquire_fetcher_run(
                db_session,
                fetcher_name=config.fetcher_name,
                triggered_by=FetcherRunTriggeredBy.SCHEDULE,
                run_id=None,
                now=now,
            )

        assert result is None
        rows = (
            (
                await db_session.execute(
                    select(FetcherRun).where(
                        FetcherRun.fetcher_name == config.fetcher_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == active.id
        assert rows[0].status == "running"
        assert "scheduled_run_skipped_active_run_exists" in _service_log_text(caplog)


# ---------------------------------------------------------------------------
# Stale run boundary and finalization
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAcquireFetcherRunStaleBoundary:
    async def test_elapsed_equal_to_threshold_is_not_stale(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
    ) -> None:
        config = await fetcher_config_factory(run_timeout=60)
        started_at = datetime.now(UTC)
        active = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            started_at=started_at,
        )
        # Exactly run_timeout + 60 == 120: NOT stale (strict `>`).
        now = started_at + timedelta(seconds=120)

        result = await acquire_fetcher_run(
            db_session,
            fetcher_name=config.fetcher_name,
            triggered_by=FetcherRunTriggeredBy.SCHEDULE,
            run_id=None,
            now=now,
        )

        assert result is None
        await db_session.refresh(active)
        assert active.status == "running"

    async def test_elapsed_one_second_beyond_threshold_is_stale(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config = await fetcher_config_factory(run_timeout=60)
        started_at = datetime.now(UTC)
        stale_run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            triggered_by="schedule",
            started_at=started_at,
        )
        # One second beyond run_timeout + 60 == 120: stale.
        now = started_at + timedelta(seconds=121)

        with caplog.at_level("WARNING"):
            result = await acquire_fetcher_run(
                db_session,
                fetcher_name=config.fetcher_name,
                triggered_by=FetcherRunTriggeredBy.SCHEDULE,
                run_id=None,
                now=now,
            )

        # Scheduled acquisition proceeds after stale finalization.
        assert isinstance(result, FetcherAcquisition)
        assert result.run_id != stale_run.id

        await db_session.refresh(stale_run)
        assert stale_run.status == "failure"
        assert stale_run.error_message == (
            "Marked as stale (running for 121s, timeout 60s)"
        )
        assert stale_run.finished_at == now
        assert stale_run.duration_seconds == 121.0

        new_run = await db_session.get(FetcherRun, result.run_id)
        assert new_run is not None
        assert new_run.status == "running"

        log_text = _service_log_text(caplog)
        assert "fetcher_run_marked_stale" in log_text
        assert config.fetcher_name in log_text
        assert "60" in log_text


# ---------------------------------------------------------------------------
# Manual adoption
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAcquireFetcherRunManualAdoption:
    async def test_adopts_precreated_run_not_treated_as_competitor(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
    ) -> None:
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            triggered_by="manual",
        )

        result = await acquire_fetcher_run(
            db_session,
            fetcher_name=config.fetcher_name,
            triggered_by=FetcherRunTriggeredBy.MANUAL,
            run_id=run.id,
            now=datetime.now(UTC),
        )

        assert isinstance(result, FetcherAcquisition)
        assert result.run_id == run.id

        rows = (
            (
                await db_session.execute(
                    select(FetcherRun).where(
                        FetcherRun.fetcher_name == config.fetcher_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_run_not_found_raises_value_error(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory()
        missing_run_id = uuid4()

        with pytest.raises(ValueError, match=str(missing_run_id)):
            await acquire_fetcher_run(
                db_session,
                fetcher_name=config.fetcher_name,
                triggered_by=FetcherRunTriggeredBy.MANUAL,
                run_id=missing_run_id,
                now=datetime.now(UTC),
            )

    async def test_run_fetcher_name_mismatch_raises_value_error(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
    ) -> None:
        config = await fetcher_config_factory()
        other_config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=other_config.fetcher_name,
            status="running",
            triggered_by="manual",
        )

        with pytest.raises(ValueError, match="belongs to fetcher"):
            await acquire_fetcher_run(
                db_session,
                fetcher_name=config.fetcher_name,
                triggered_by=FetcherRunTriggeredBy.MANUAL,
                run_id=run.id,
                now=datetime.now(UTC),
            )

    async def test_already_finalized_run_returns_none_without_error(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="failure",
            triggered_by="manual",
            finished_at=datetime.now(UTC),
        )

        with caplog.at_level("INFO"):
            result = await acquire_fetcher_run(
                db_session,
                fetcher_name=config.fetcher_name,
                triggered_by=FetcherRunTriggeredBy.MANUAL,
                run_id=run.id,
                now=datetime.now(UTC),
            )

        assert result is None
        await db_session.refresh(run)
        assert run.status == "failure"
        assert "manual_run_already_finalized" in _service_log_text(caplog)

    async def test_manual_run_stale_before_adoption_marks_stale_and_returns_none(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A manual run picked up so late that its own pre-created row has
        already crossed the stale threshold is marked stale at step 5 and
        then discarded as already-finalized at step 6 — it is never
        (re-)executed. See fetcher-infrastructure.md, Atomic Run
        Acquisition Protocol, step 5 note.
        """
        config = await fetcher_config_factory(run_timeout=60)
        started_at = datetime.now(UTC)
        manual_run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            triggered_by="manual",
            started_at=started_at,
        )
        # One second beyond run_timeout + 60 == 120: stale.
        now = started_at + timedelta(seconds=121)

        with caplog.at_level("INFO"):
            result = await acquire_fetcher_run(
                db_session,
                fetcher_name=config.fetcher_name,
                triggered_by=FetcherRunTriggeredBy.MANUAL,
                run_id=manual_run.id,
                now=now,
            )

        assert result is None
        # The caller (`run_fetcher_async`) always commits the session
        # after `acquire_fetcher_run` returns, regardless of the return
        # value — this flush simulates that unconditional commit so the
        # in-place stale mutation becomes visible to a fresh SELECT.
        await db_session.flush()
        await db_session.refresh(manual_run)
        assert manual_run.status == "failure"
        assert "Marked as stale" in (manual_run.error_message or "")
        assert "manual_run_already_finalized" in _service_log_text(caplog)

        # No replacement run was created for the manual trigger.
        rows = (
            (
                await db_session.execute(
                    select(FetcherRun).where(
                        FetcherRun.fetcher_name == config.fetcher_name
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# finalize_manual_run_as_failure()
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFinalizeManualRunAsFailure:
    async def test_updates_fields_and_flushes(
        self,
        db_session: AsyncSession,
        fetcher_run_factory: Callable[..., Awaitable[FetcherRun]],
    ) -> None:
        run = await fetcher_run_factory(status="running", triggered_by="manual")
        now = datetime.now(UTC)

        await finalize_manual_run_as_failure(
            db_session,
            run_id=run.id,
            fetcher_name=run.fetcher_name,
            error_message="Fetcher deregistered between trigger and execution",
            now=now,
        )

        await db_session.refresh(run)
        assert run.status == "failure"
        assert run.error_message == (
            "Fetcher deregistered between trigger and execution"
        )
        assert run.finished_at == now
        assert run.duration_seconds == 0

    async def test_missing_run_raises_value_error(
        self, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing_run_id = uuid4()

        with (
            caplog.at_level("ERROR"),
            pytest.raises(ValueError, match=str(missing_run_id)),
        ):
            await finalize_manual_run_as_failure(
                db_session,
                run_id=missing_run_id,
                fetcher_name="some_fetcher",
                error_message="irrelevant",
                now=datetime.now(UTC),
            )

        assert "manual_run_finalization_target_missing" in _service_log_text(caplog)


# ---------------------------------------------------------------------------
# True concurrency — FetcherConfig-root lock serialization
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAcquireFetcherRunConcurrency:
    """See docs/features/platform/testing-strategy.md (Concurrency
    Testing) for the canonical two-session pattern applied here."""

    async def test_concurrent_scheduled_starts_only_one_creates_a_run(
        self,
        db_session_factory: Callable[[], Awaitable[AsyncSession]],
    ) -> None:
        session_a = await db_session_factory()
        session_b = await db_session_factory()
        fetcher_name = f"concurrency_fetcher_{uuid4().hex[:8]}"

        session_a.add(FetcherConfig(fetcher_name=fetcher_name, run_timeout=3600))
        await session_a.commit()

        now = datetime.now(UTC)
        # Session A acquires the FetcherConfig lock, creates the run,
        # and flushes — but does not commit yet, so the lock is held.
        acquisition_a = await acquire_fetcher_run(
            session_a,
            fetcher_name=fetcher_name,
            triggered_by=FetcherRunTriggeredBy.SCHEDULE,
            run_id=None,
            now=now,
        )
        assert acquisition_a is not None

        # Session B's concurrent acquisition attempt blocks on the same
        # FetcherConfig row — this is the empty-result race the
        # FetcherConfig-root lock (rather than a FetcherRun-only lock)
        # is designed to prevent (see fetcher-infrastructure.md,
        # Atomic Run Acquisition Protocol).
        task_b = asyncio.create_task(
            acquire_fetcher_run(
                session_b,
                fetcher_name=fetcher_name,
                triggered_by=FetcherRunTriggeredBy.SCHEDULE,
                run_id=None,
                now=now,
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task_b), timeout=0.3)

        # Releasing A's lock lets B proceed — B now observes A's
        # committed running run and silently discards.
        await session_a.commit()
        acquisition_b = await asyncio.wait_for(task_b, timeout=5)
        await session_b.commit()

        assert acquisition_b is None

        rows = (
            (
                await session_a.execute(
                    select(FetcherRun).where(FetcherRun.fetcher_name == fetcher_name)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == acquisition_a.run_id

        # Explicit cleanup — committed rows are not covered by
        # db_session_factory's rollback-on-teardown.
        await session_a.execute(
            delete(FetcherRun).where(FetcherRun.fetcher_name == fetcher_name)
        )
        await session_a.execute(
            delete(FetcherConfig).where(FetcherConfig.fetcher_name == fetcher_name)
        )
        await session_a.commit()

    async def test_concurrent_manual_adoption_and_scheduled_duplicate(
        self,
        db_session_factory: Callable[[], Awaitable[AsyncSession]],
    ) -> None:
        session_a = await db_session_factory()
        session_b = await db_session_factory()
        fetcher_name = f"concurrency_fetcher_{uuid4().hex[:8]}"

        session_a.add(FetcherConfig(fetcher_name=fetcher_name, run_timeout=3600))
        manual_run = FetcherRun(
            fetcher_name=fetcher_name,
            status="running",
            triggered_by="manual",
            started_at=datetime.now(UTC),
        )
        session_a.add(manual_run)
        await session_a.commit()

        now = datetime.now(UTC)
        # Session A adopts its own pre-created manual run and holds
        # the FetcherConfig lock (no commit yet).
        acquisition_a = await acquire_fetcher_run(
            session_a,
            fetcher_name=fetcher_name,
            triggered_by=FetcherRunTriggeredBy.MANUAL,
            run_id=manual_run.id,
            now=now,
        )
        assert acquisition_a is not None
        assert acquisition_a.run_id == manual_run.id

        # A concurrent scheduled trigger blocks on the same lock.
        task_b = asyncio.create_task(
            acquire_fetcher_run(
                session_b,
                fetcher_name=fetcher_name,
                triggered_by=FetcherRunTriggeredBy.SCHEDULE,
                run_id=None,
                now=now,
            )
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task_b), timeout=0.3)

        await session_a.commit()
        acquisition_b = await asyncio.wait_for(task_b, timeout=5)
        await session_b.commit()

        # "Schedule fires while manual run is active" — silent discard.
        assert acquisition_b is None

        rows = (
            (
                await session_a.execute(
                    select(FetcherRun).where(FetcherRun.fetcher_name == fetcher_name)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == manual_run.id
        assert rows[0].status == "running"

        await session_a.execute(
            delete(FetcherRun).where(FetcherRun.fetcher_name == fetcher_name)
        )
        await session_a.execute(
            delete(FetcherConfig).where(FetcherConfig.fetcher_name == fetcher_name)
        )
        await session_a.commit()
