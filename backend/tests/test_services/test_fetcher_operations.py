"""Tests for the fetcher operations service
(backend/app/services/fetcher_operations.py).

See `docs/features/platform/fetcher-operations.md` (Fetcher Operations
Service, `list_fetchers`, `list_fetcher_runs`, `get_fetcher_run`,
`get_fetcher_timeline`, Disabled Period Derivation) for the contract
under test.

Pure calculation helpers (`_is_stale`, `_count_recognized_settings`) are
unit tests (no DB, no Redis). Every public function performs real
database reads and is tested with `db_session`/the fetcher factory
fixtures. `list_fetchers`'s RedBeat integration uses the shared
`celery_test_app` fixture (`tests/conftest.py`) against the isolated
worker Redis logical database (see
`docs/features/platform/testing-strategy.md`, Redis Strategy).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from celery import Celery
from celery.schedules import crontab
from pydantic import BaseModel, Field
from redbeat import RedBeatSchedulerEntry
from redbeat.schedulers import get_redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.fetcher_operations as fetcher_operations_module
from app.models.fetcher_audit_event import FetcherAuditEvent
from app.models.fetcher_config import FetcherConfig
from app.models.fetcher_run import FetcherRun
from app.models.user import User
from app.services.base_fetcher import FETCHER_REGISTRY, BaseFetcher
from app.services.fetcher_operations import (
    FetcherNotFoundError,
    FetcherRunNotFoundError,
    _count_recognized_settings,
    _is_stale,
    get_fetcher_config,
    get_fetcher_run,
    get_fetcher_timeline,
    list_fetcher_audit_events,
    list_fetcher_runs,
    list_fetchers,
)

FetcherConfigFactory = Callable[..., Awaitable[FetcherConfig]]
FetcherRunFactory = Callable[..., Awaitable[FetcherRun]]
FetcherAuditEventFactory = Callable[..., Awaitable[FetcherAuditEvent]]
UserFactory = Callable[..., Awaitable[User]]


# ---------------------------------------------------------------------------
# Fixtures / stub fetchers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_registry() -> Generator[None]:
    """Snapshot/restore `FETCHER_REGISTRY` around every test in this
    file — mirrors `tests/test_services/test_fetcher_schedule.py`."""
    original = dict(FETCHER_REGISTRY)
    yield
    FETCHER_REGISTRY.clear()
    FETCHER_REGISTRY.update(original)


class _SettingsModel(BaseModel):
    results_per_page: int = Field(default=100, ge=10, le=1000)


class _NoSettingsFetcherStub:
    """Minimal `FETCHER_REGISTRY` entry stub with no `Settings` model
    and no `cve_source_type` — deliberately NOT a `BaseFetcher`
    subclass (see `test_fetcher_schedule.py`, Test Independence)."""

    name = "test_ops_no_settings"
    description = "Stub fetcher without custom settings"
    default_schedule = "0 3 * * *"
    queue: str | None = None
    Settings: type[BaseModel] | None = None


class _WithSettingsFetcherStub:
    """Same rationale, with a `Settings` model and `cve_source_type`."""

    name = "test_ops_with_settings"
    description = "Stub fetcher with custom settings"
    default_schedule = "0 4 * * *"
    queue: str | None = None
    cve_source_type = "nvd"
    Settings = _SettingsModel


_NoSettingsFetcher = cast("type[BaseFetcher]", _NoSettingsFetcherStub)
_WithSettingsFetcher = cast("type[BaseFetcher]", _WithSettingsFetcherStub)


def _register(*stubs: type[Any]) -> None:
    """Replace `FETCHER_REGISTRY` with exactly the given stub classes."""
    FETCHER_REGISTRY.clear()
    for stub in stubs:
        FETCHER_REGISTRY[stub.name] = stub


def _ops_log_records(caplog: pytest.LogCaptureFixture) -> list[Any]:
    """Return only the log records emitted by this module — mirrors
    the identical pattern in `test_fetcher_schedule.py`."""
    return [
        record
        for record in caplog.records
        if record.name == "app.services.fetcher_operations"
    ]


# ---------------------------------------------------------------------------
# Pure calculation unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsStale:
    def test_non_running_status_is_never_stale(self) -> None:
        now = datetime.now(UTC)
        assert _is_stale("success", now - timedelta(hours=10), 3600, now) is False

    def test_running_under_threshold_is_not_stale(self) -> None:
        now = datetime.now(UTC)
        started_at = now - timedelta(seconds=3600 + 59)
        assert _is_stale("running", started_at, 3600, now) is False

    def test_running_at_exact_threshold_is_not_stale(self) -> None:
        """Boundary: `elapsed == run_timeout + 60` is NOT stale — the
        contract requires strictly greater than the threshold."""
        now = datetime.now(UTC)
        started_at = now - timedelta(seconds=3660)
        assert _is_stale("running", started_at, 3600, now) is False

    def test_running_over_threshold_is_stale(self) -> None:
        now = datetime.now(UTC)
        started_at = now - timedelta(seconds=3661)
        assert _is_stale("running", started_at, 3600, now) is True


@pytest.mark.unit
class TestCountRecognizedSettings:
    def test_no_settings_model_returns_zero(self) -> None:
        assert _count_recognized_settings(_NoSettingsFetcher, {"foo": 1}) == 0

    def test_counts_only_recognized_keys(self) -> None:
        count = _count_recognized_settings(
            _WithSettingsFetcher, {"results_per_page": 50, "orphaned_key": True}
        )
        assert count == 1

    def test_empty_custom_settings_is_zero(self) -> None:
        assert _count_recognized_settings(_WithSettingsFetcher, {}) == 0


# ---------------------------------------------------------------------------
# list_fetchers
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListFetchersMergeAndSort:
    async def test_registered_fetcher_without_config_uses_synthesized_defaults(
        self, db_session: AsyncSession, celery_test_app: Celery
    ) -> None:
        _register(_NoSettingsFetcher)

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert len(items) == 1
        item = items[0]
        assert item.fetcher_name == _NoSettingsFetcher.name
        assert item.registered is True
        assert item.enabled is True
        assert item.effective_schedule == _NoSettingsFetcher.default_schedule
        assert item.schedule_is_override is False
        assert item.custom_settings_count == 0
        assert item.last_run is None
        assert item.next_run_at is None

    async def test_registered_fetcher_with_config_uses_stored_values(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_WithSettingsFetcher)
        await fetcher_config_factory(
            fetcher_name=_WithSettingsFetcher.name,
            enabled=False,
            schedule_override="0 */2 * * *",
            custom_settings={"results_per_page": 250, "orphaned": True},
        )

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert len(items) == 1
        item = items[0]
        assert item.enabled is False
        assert item.effective_schedule == "0 */2 * * *"
        assert item.schedule_is_override is True
        assert item.default_schedule == _WithSettingsFetcher.default_schedule
        assert item.cve_source_type == "nvd"
        assert item.custom_settings_count == 1  # orphaned key excluded

    async def test_deregistered_fetcher_has_null_code_fields(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        FETCHER_REGISTRY.clear()
        config = await fetcher_config_factory(
            custom_settings={"a": 1, "b": 2},
        )

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert len(items) == 1
        item = items[0]
        assert item.fetcher_name == config.fetcher_name
        assert item.registered is False
        assert item.description is None
        assert item.default_schedule is None
        assert item.schedule_is_override is None
        assert item.cve_source_type is None
        assert item.effective_schedule == config.schedule_override
        assert item.custom_settings_count == 2  # total key count, no schema
        assert item.next_run_at is None

    async def test_sorts_registered_and_deregistered_alphabetically(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(
            _NoSettingsFetcher, _WithSettingsFetcher
        )  # "test_ops_no_settings", "test_ops_with_settings"
        await fetcher_config_factory(fetcher_name="test_ops_aaa_deregistered")
        await fetcher_config_factory(fetcher_name="test_ops_zzz_deregistered")

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        names = [item.fetcher_name for item in items]
        assert names == sorted(names)
        assert "test_ops_aaa_deregistered" in names
        assert "test_ops_zzz_deregistered" in names


@pytest.mark.integration
class TestListFetchersLastRun:
    async def test_never_run_is_null(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name)

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert items[0].last_run is None

    async def test_resolves_most_recent_by_started_at_desc(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        config = await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name)
        now = datetime.now(UTC)
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            started_at=now - timedelta(hours=2),
            status="success",
        )
        latest = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            started_at=now - timedelta(minutes=1),
            status="success",
        )

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert items[0].last_run is not None
        assert items[0].last_run.id == latest.id

    async def test_equal_started_at_breaks_tie_by_id_desc(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        config = await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name)
        fixed_time = datetime.now(UTC) - timedelta(hours=1)
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name, started_at=fixed_time, status="success"
        )
        # uuid7 primary keys are time-ordered, so the run created second
        # has the larger id and wins the `id DESC` tiebreak.
        second = await fetcher_run_factory(
            fetcher_name=config.fetcher_name, started_at=fixed_time, status="success"
        )

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert items[0].last_run is not None
        assert items[0].last_run.id == second.id

    async def test_running_status_has_null_finished_at_and_duration(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        config = await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name)
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            finished_at=None,
            duration_seconds=None,
        )

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        last_run = items[0].last_run
        assert last_run is not None
        assert last_run.status == "running"
        assert last_run.finished_at is None
        assert last_run.duration_seconds is None

    async def test_stale_reflects_run_timeout_plus_margin(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        config = await fetcher_config_factory(
            fetcher_name=_NoSettingsFetcher.name, run_timeout=60
        )
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            started_at=datetime.now(UTC) - timedelta(seconds=200),
        )

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        last_run = items[0].last_run
        assert last_run is not None
        assert last_run.stale is True

    async def test_triggered_by_user_hidden_without_manage_fetchers(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
        user_factory: UserFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        config = await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name)
        actor = await user_factory()
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            triggered_by="manual",
            triggered_by_user_id=actor.id,
        )

        anonymous_items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )
        privileged_items = await list_fetchers(
            db_session, has_manage_fetchers=True, celery_app=celery_test_app
        )

        assert anonymous_items[0].last_run is not None
        assert anonymous_items[0].last_run.triggered_by_user is None
        assert privileged_items[0].last_run is not None
        assert privileged_items[0].last_run.triggered_by_user is not None
        assert privileged_items[0].last_run.triggered_by_user.id == actor.id


@pytest.mark.integration
class TestListFetchersNextRunAt:
    async def test_reads_due_at_from_existing_redbeat_entry(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name, enabled=True)
        entry = RedBeatSchedulerEntry(
            name=_NoSettingsFetcher.name,
            task="run_fetcher",
            schedule=crontab.from_string(_NoSettingsFetcher.default_schedule),
            args=[],
            kwargs={
                "fetcher_name": _NoSettingsFetcher.name,
                "triggered_by": "schedule",
            },
            app=celery_test_app,
        )
        entry.save()
        entry.reschedule(datetime.now(UTC))
        expected_due_at = RedBeatSchedulerEntry.from_key(
            RedBeatSchedulerEntry.generate_key(
                celery_test_app, _NoSettingsFetcher.name
            ),
            app=celery_test_app,
        ).due_at

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        # `due_at` is recomputed relative to wall-clock time at read time
        # (see redbeat's `RedBeatSchedulerEntry.due_at`), so two separate
        # reads a few milliseconds apart legitimately differ by a similar
        # margin — assert near-equality rather than exact equality.
        assert items[0].next_run_at is not None
        assert abs((items[0].next_run_at - expected_due_at).total_seconds()) < 5

    async def test_no_entry_is_null(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name, enabled=True)

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert items[0].next_run_at is None

    async def test_disabled_fetcher_is_null_even_with_leftover_entry(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        await fetcher_config_factory(
            fetcher_name=_NoSettingsFetcher.name, enabled=False
        )
        entry = RedBeatSchedulerEntry(
            name=_NoSettingsFetcher.name,
            task="run_fetcher",
            schedule=crontab.from_string(_NoSettingsFetcher.default_schedule),
            args=[],
            kwargs={
                "fetcher_name": _NoSettingsFetcher.name,
                "triggered_by": "schedule",
            },
            app=celery_test_app,
        )
        entry.save()

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert items[0].next_run_at is None

    async def test_deregistered_fetcher_is_null_even_with_leftover_entry(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        FETCHER_REGISTRY.clear()
        config = await fetcher_config_factory(
            fetcher_name="test_ops_deregistered_leftover"
        )
        entry = RedBeatSchedulerEntry(
            name=config.fetcher_name,
            task="run_fetcher",
            schedule=crontab.from_string("0 3 * * *"),
            args=[],
            kwargs={"fetcher_name": config.fetcher_name, "triggered_by": "schedule"},
            app=celery_test_app,
        )
        entry.save()

        items = await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )

        assert items[0].next_run_at is None

    async def test_redis_error_nulls_all_fetchers_and_logs_safely(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        _register(_NoSettingsFetcher, _WithSettingsFetcher)
        await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name, enabled=True)
        await fetcher_config_factory(
            fetcher_name=_WithSettingsFetcher.name, enabled=True
        )

        sensitive_message = (
            "Connection to redis://user:supersecret@10.0.0.5:6379 failed"
        )

        def _raise_redis_error(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise RedisError(sensitive_message)

        monkeypatch.setattr(
            fetcher_operations_module, "_read_due_times", _raise_redis_error
        )

        with caplog.at_level("WARNING", logger="app.services.fetcher_operations"):
            items = await list_fetchers(
                db_session, has_manage_fetchers=False, celery_app=celery_test_app
            )

        assert all(item.next_run_at is None for item in items)
        records = _ops_log_records(caplog)
        assert len(records) == 1
        assert "fetcher_redbeat_next_run_unavailable" in caplog.text
        assert "supersecret" not in caplog.text
        assert "10.0.0.5" not in caplog.text

    async def test_redbeat_read_does_not_block_event_loop(
        self,
        db_session: AsyncSession,
        celery_test_app: Celery,
        fetcher_config_factory: FetcherConfigFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Proves the synchronous RedBeat read runs in a worker thread
        (`asyncio.to_thread`), not on the event loop — a competing
        coroutine must keep making progress while the reader blocks."""
        _register(_NoSettingsFetcher)
        await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name, enabled=True)

        reader_started = threading.Event()
        release_reader = threading.Event()

        def _blocking_read(
            _celery_app: Celery, names: list[str]
        ) -> dict[str, datetime | None]:
            reader_started.set()
            release_reader.wait(timeout=5)
            return dict.fromkeys(names, None)

        monkeypatch.setattr(
            fetcher_operations_module, "_read_due_times", _blocking_read
        )

        ticks = 0

        async def _heartbeat() -> int:
            nonlocal ticks
            await asyncio.to_thread(reader_started.wait, 5)
            for _ in range(5):
                await asyncio.sleep(0.01)
                ticks += 1
            release_reader.set()
            return ticks

        heartbeat_task = asyncio.create_task(_heartbeat())
        await list_fetchers(
            db_session, has_manage_fetchers=False, celery_app=celery_test_app
        )
        heartbeat_ticks = await heartbeat_task

        assert heartbeat_ticks == 5

    def test_redbeat_client_has_bounded_socket_timeouts(
        self, celery_test_app: Celery
    ) -> None:
        """The RedBeat Redis client (the same singleton `_read_due_times`
        reads through) carries explicit socket timeouts, so a hung
        (blackholed/firewalled) connection raises `RedisError` within a
        bounded time instead of blocking the worker thread indefinitely
        — see docs/features/platform/fetcher-infrastructure.md (Redbeat
        Configuration, API endpoint failure handling)."""
        client = get_redis(celery_test_app)
        pool_kwargs = client.connection_pool.connection_kwargs
        assert pool_kwargs["socket_connect_timeout"] == 2
        assert pool_kwargs["socket_timeout"] == 2


# ---------------------------------------------------------------------------
# list_fetcher_runs
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListFetcherRuns:
    async def test_raises_not_found_for_unknown_fetcher(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(FetcherNotFoundError):
            await list_fetcher_runs(
                db_session,
                fetcher_name="no_such_fetcher",
                has_manage_fetchers=False,
                page=1,
                per_page=20,
            )

    async def test_registered_without_config_returns_empty_page(
        self, db_session: AsyncSession
    ) -> None:
        _register(_NoSettingsFetcher)

        page = await list_fetcher_runs(
            db_session,
            fetcher_name=_NoSettingsFetcher.name,
            has_manage_fetchers=False,
            page=1,
            per_page=20,
        )

        assert page.items == []
        assert page.total == 0

    async def test_filters_by_exact_status(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_run_factory(fetcher_name=config.fetcher_name, status="success")
        await fetcher_run_factory(fetcher_name=config.fetcher_name, status="failure")

        page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            page=1,
            per_page=20,
            status="failure",
        )

        assert page.total == 1
        assert page.items[0].status == "failure"

    async def test_invalid_status_returns_empty_page_not_error(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_run_factory(fetcher_name=config.fetcher_name, status="success")

        page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            page=1,
            per_page=20,
            status="not-a-real-status",
        )

        assert page.total == 0
        assert page.items == []

    async def test_filters_by_inclusive_date_range(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        in_range = await fetcher_run_factory(
            fetcher_name=config.fetcher_name, started_at=now - timedelta(days=1)
        )
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name, started_at=now - timedelta(days=10)
        )

        page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            page=1,
            per_page=20,
            from_date=now - timedelta(days=2),
            to_date=now,
        )

        assert page.total == 1
        assert page.items[0].id == in_range.id

    async def test_pagination_reports_filtered_total_and_empty_out_of_range_page(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        for _ in range(3):
            await fetcher_run_factory(fetcher_name=config.fetcher_name)

        page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            page=1,
            per_page=2,
        )
        out_of_range_page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            page=5,
            per_page=2,
        )

        assert page.total == 3
        assert len(page.items) == 2
        assert out_of_range_page.total == 3
        assert out_of_range_page.items == []

    async def test_orders_started_at_desc_id_desc(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        older = await fetcher_run_factory(
            fetcher_name=config.fetcher_name, started_at=now - timedelta(hours=1)
        )
        newer = await fetcher_run_factory(
            fetcher_name=config.fetcher_name, started_at=now
        )

        page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            page=1,
            per_page=20,
        )

        assert [item.id for item in page.items] == [newer.id, older.id]

    async def test_triggered_by_user_visibility(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory()
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            triggered_by="manual",
            triggered_by_user_id=actor.id,
        )

        anonymous_page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            page=1,
            per_page=20,
        )
        privileged_page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=True,
            page=1,
            per_page=20,
        )

        assert anonymous_page.items[0].triggered_by_user is None
        assert privileged_page.items[0].triggered_by_user is not None
        assert privileged_page.items[0].triggered_by_user.id == actor.id

    async def test_stale_field_uses_fetcher_run_timeout(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory(run_timeout=60)
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            started_at=datetime.now(UTC) - timedelta(seconds=200),
        )

        page = await list_fetcher_runs(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            page=1,
            per_page=20,
        )

        assert page.items[0].stale is True


# ---------------------------------------------------------------------------
# get_fetcher_run
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetFetcherRun:
    async def test_returns_full_detail(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="failure",
            items_created=3,
            items_updated=5,
            items_failed=2,
            error_message="sanitized message",
            error_detail="raw TimeoutError detail",
            error_traceback="Traceback...",
        )

        result = await get_fetcher_run(
            db_session,
            fetcher_name=config.fetcher_name,
            run_id=run.id,
            has_manage_fetchers=False,
        )

        assert result.id == run.id
        assert result.fetcher_name == config.fetcher_name
        assert result.started_at == run.started_at
        assert result.finished_at == run.finished_at
        assert result.duration_seconds == run.duration_seconds
        assert result.status == "failure"
        assert result.items_created == 3
        assert result.items_updated == 5
        assert result.items_failed == 2
        assert result.error_message == "sanitized message"
        assert result.triggered_by == run.triggered_by

    async def test_raw_diagnostics_always_populated_on_the_dataclass(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        """The service always returns the raw values regardless of
        `has_manage_fetchers` — visibility (absence in the HTTP
        response) is a router/schema-layer decision, per
        `fetcher_operations.get_fetcher_run` docstring."""
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="failure",
            error_detail="raw detail",
            error_traceback="raw traceback",
        )

        result = await get_fetcher_run(
            db_session,
            fetcher_name=config.fetcher_name,
            run_id=run.id,
            has_manage_fetchers=False,
        )

        assert result.error_detail == "raw detail"
        assert result.error_traceback == "raw traceback"

    async def test_raises_not_found_for_unknown_fetcher(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(FetcherNotFoundError):
            await get_fetcher_run(
                db_session,
                fetcher_name="no_such_fetcher",
                run_id=uuid4(),
                has_manage_fetchers=False,
            )

    async def test_raises_run_not_found_for_nonexistent_run(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        config = await fetcher_config_factory()
        with pytest.raises(FetcherRunNotFoundError):
            await get_fetcher_run(
                db_session,
                fetcher_name=config.fetcher_name,
                run_id=uuid4(),
                has_manage_fetchers=False,
            )

    async def test_raises_run_not_found_for_run_of_different_fetcher(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config_a = await fetcher_config_factory()
        config_b = await fetcher_config_factory()
        run = await fetcher_run_factory(fetcher_name=config_a.fetcher_name)

        with pytest.raises(FetcherRunNotFoundError):
            await get_fetcher_run(
                db_session,
                fetcher_name=config_b.fetcher_name,
                run_id=run.id,
                has_manage_fetchers=False,
            )

    async def test_triggered_by_user_hidden_without_manage_fetchers(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            triggered_by="manual",
            triggered_by_user_id=actor.id,
        )

        result = await get_fetcher_run(
            db_session,
            fetcher_name=config.fetcher_name,
            run_id=run.id,
            has_manage_fetchers=False,
        )

        assert result.triggered_by_user is None


# ---------------------------------------------------------------------------
# get_fetcher_timeline
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetFetcherTimelinePoints:
    async def test_orders_points_chronologically(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        newer = await fetcher_run_factory(
            fetcher_name=config.fetcher_name, started_at=now - timedelta(hours=1)
        )
        older = await fetcher_run_factory(
            fetcher_name=config.fetcher_name, started_at=now - timedelta(hours=5)
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=1),
            to_date=now,
        )

        assert [point.run_id for point in timeline.points] == [older.id, newer.id]

    async def test_running_point_has_null_duration(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            started_at=now,
            finished_at=None,
            duration_seconds=None,
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(hours=1),
            to_date=now + timedelta(hours=1),
        )

        assert timeline.points[0].duration_seconds is None
        assert timeline.points[0].status == "running"

    async def test_bounds_are_inclusive(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        boundary = datetime.now(UTC)
        await fetcher_run_factory(fetcher_name=config.fetcher_name, started_at=boundary)

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=boundary,
            to_date=boundary,
        )

        assert len(timeline.points) == 1

    async def test_raises_not_found_for_unknown_fetcher(
        self, db_session: AsyncSession
    ) -> None:
        now = datetime.now(UTC)
        with pytest.raises(FetcherNotFoundError):
            await get_fetcher_timeline(
                db_session,
                fetcher_name="no_such_fetcher",
                has_manage_fetchers=False,
                from_date=now - timedelta(days=1),
                to_date=now,
            )


@pytest.mark.integration
class TestDisabledPeriodDerivation:
    async def test_pairs_disabled_with_next_enabled(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        disabled_at = now - timedelta(days=5)
        enabled_at = now - timedelta(days=3)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=disabled_at,
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="enabled",
            created_at=enabled_at,
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=10),
            to_date=now,
        )

        assert len(timeline.disabled_periods) == 1
        period = timeline.disabled_periods[0]
        assert period.disabled_at == disabled_at
        assert period.enabled_at == enabled_at

    async def test_trailing_disabled_is_open_ended(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        disabled_at = now - timedelta(days=2)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=disabled_at,
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=10),
            to_date=now,
        )

        assert len(timeline.disabled_periods) == 1
        assert timeline.disabled_periods[0].enabled_at is None

    async def test_leading_orphaned_enabled_is_ignored(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="enabled",
            created_at=now - timedelta(days=9),
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=10),
            to_date=now,
        )

        assert timeline.disabled_periods == []

    async def test_consecutive_disabled_events_use_earliest(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        earliest = now - timedelta(days=6)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, event_type="disabled", created_at=earliest
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=now - timedelta(days=5),
        )
        enabled_at = now - timedelta(days=1)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="enabled",
            created_at=enabled_at,
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=10),
            to_date=now,
        )

        assert len(timeline.disabled_periods) == 1
        assert timeline.disabled_periods[0].disabled_at == earliest

    async def test_excludes_period_entirely_outside_range(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=now - timedelta(days=100),
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="enabled",
            created_at=now - timedelta(days=90),
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=10),
            to_date=now,
        )

        assert timeline.disabled_periods == []

    async def test_includes_partial_overlap_without_clipping_timestamps(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        disabled_at = now - timedelta(days=20)  # before the requested range
        enabled_at = now - timedelta(days=8)  # inside the requested range
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=disabled_at,
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="enabled",
            created_at=enabled_at,
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=10),
            to_date=now,
        )

        assert len(timeline.disabled_periods) == 1
        # Actual event timestamps are preserved, not clipped to from_date.
        assert timeline.disabled_periods[0].disabled_at == disabled_at
        assert timeline.disabled_periods[0].enabled_at == enabled_at

    async def test_actors_hidden_without_manage_fetchers(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory()
        now = datetime.now(UTC)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=now - timedelta(days=5),
            user_id=actor.id,
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="enabled",
            created_at=now - timedelta(days=3),
            user_id=actor.id,
        )

        anonymous_timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=10),
            to_date=now,
        )
        privileged_timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=True,
            from_date=now - timedelta(days=10),
            to_date=now,
        )

        assert anonymous_timeline.disabled_periods[0].disabled_by is None
        assert anonymous_timeline.disabled_periods[0].enabled_by is None
        assert privileged_timeline.disabled_periods[0].disabled_by is not None
        assert privileged_timeline.disabled_periods[0].disabled_by.id == actor.id
        assert privileged_timeline.disabled_periods[0].enabled_by is not None
        assert privileged_timeline.disabled_periods[0].enabled_by.id == actor.id

    async def test_equal_created_at_pairs_correctly_via_id_ordering(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        """Ordering is `id ASC` (UUIDv7), not `created_at ASC` — proven
        by giving both events the exact same `created_at` and relying
        on `id` (generated in call order) to pair them correctly."""
        config = await fetcher_config_factory()
        now = datetime.now(UTC)
        same_time = now - timedelta(days=5)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=same_time,
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="enabled",
            created_at=same_time,
        )

        timeline = await get_fetcher_timeline(
            db_session,
            fetcher_name=config.fetcher_name,
            has_manage_fetchers=False,
            from_date=now - timedelta(days=10),
            to_date=now,
        )

        assert len(timeline.disabled_periods) == 1
        assert timeline.disabled_periods[0].disabled_at == same_time
        assert timeline.disabled_periods[0].enabled_at == same_time


@pytest.mark.integration
class TestGetFetcherConfig:
    async def test_registered_fetcher_with_override_returns_merged_config(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_WithSettingsFetcher)
        config = await fetcher_config_factory(
            fetcher_name=_WithSettingsFetcher.name,
            schedule_override="0 */2 * * *",
            custom_settings={"results_per_page": 250},
        )

        result = await get_fetcher_config(db_session, fetcher_name=config.fetcher_name)

        assert result.fetcher_name == config.fetcher_name
        assert result.enabled is True
        assert result.schedule_override == "0 */2 * * *"
        assert result.default_schedule == _WithSettingsFetcher.default_schedule
        assert result.effective_schedule == "0 */2 * * *"
        assert result.run_timeout == config.run_timeout
        assert result.request_delay == config.request_delay
        assert result.custom_settings == {"results_per_page": 250}
        assert result.updated_at == config.updated_at

    async def test_registered_fetcher_without_override_uses_default_schedule(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        config = await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name)

        result = await get_fetcher_config(db_session, fetcher_name=config.fetcher_name)

        assert result.schedule_override is None
        assert result.effective_schedule == _NoSettingsFetcher.default_schedule

    async def test_registered_fetcher_settings_schema_matches_model(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_WithSettingsFetcher)
        config = await fetcher_config_factory(fetcher_name=_WithSettingsFetcher.name)

        result = await get_fetcher_config(db_session, fetcher_name=config.fetcher_name)

        assert result.settings_schema == _SettingsModel.model_json_schema()

    async def test_registered_fetcher_without_settings_model_schema_is_none(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_NoSettingsFetcher)
        config = await fetcher_config_factory(fetcher_name=_NoSettingsFetcher.name)

        result = await get_fetcher_config(db_session, fetcher_name=config.fetcher_name)

        assert result.settings_schema is None

    async def test_deregistered_fetcher_returns_raw_snapshot(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        FETCHER_REGISTRY.clear()
        config = await fetcher_config_factory(
            schedule_override="0 5 * * *",
            custom_settings={"orphaned_key": "raw_value"},
        )

        result = await get_fetcher_config(db_session, fetcher_name=config.fetcher_name)

        assert result.default_schedule is None
        assert result.settings_schema is None
        assert result.effective_schedule == "0 5 * * *"
        assert result.custom_settings == {"orphaned_key": "raw_value"}

    async def test_deregistered_fetcher_without_override_effective_schedule_is_none(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        FETCHER_REGISTRY.clear()
        config = await fetcher_config_factory()

        result = await get_fetcher_config(db_session, fetcher_name=config.fetcher_name)

        assert result.effective_schedule is None

    async def test_unknown_fetcher_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        FETCHER_REGISTRY.clear()
        with pytest.raises(FetcherNotFoundError):
            await get_fetcher_config(db_session, fetcher_name="no-such-fetcher")

    async def test_registered_fetcher_without_config_row_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        """A `FetcherConfig` row is a hard prerequisite for this
        function — unlike the Public read functions, a registered
        fetcher with no row yet (bootstrap not run) still raises,
        consistent with `trigger_fetcher`/`update_fetcher_config`."""
        _register(_NoSettingsFetcher)

        with pytest.raises(FetcherNotFoundError):
            await get_fetcher_config(db_session, fetcher_name=_NoSettingsFetcher.name)

    async def test_database_error_propagates_unchanged(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _boom(*args: Any, **kwargs: Any) -> None:
            raise OperationalError("simulated", {}, Exception("boom"))

        monkeypatch.setattr(db_session, "execute", _boom)

        with pytest.raises(OperationalError):
            await get_fetcher_config(db_session, fetcher_name="irrelevant")


@pytest.mark.integration
class TestListFetcherAuditEvents:
    async def test_unknown_fetcher_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        FETCHER_REGISTRY.clear()
        with pytest.raises(FetcherNotFoundError):
            await list_fetcher_audit_events(
                db_session, fetcher_name="no-such-fetcher", page=1, per_page=20
            )

    async def test_all_invalid_event_types_still_validates_fetcher_existence(
        self, db_session: AsyncSession
    ) -> None:
        """An entirely-invalid `event_type` filter set must not mask a
        nonexistent fetcher behind an empty `200`-equivalent page — the
        existence check always runs first."""
        FETCHER_REGISTRY.clear()
        with pytest.raises(FetcherNotFoundError):
            await list_fetcher_audit_events(
                db_session,
                fetcher_name="no-such-fetcher",
                page=1,
                per_page=20,
                event_type=["not_a_real_type"],
            )

    async def test_isolates_by_fetcher_name(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config_a = await fetcher_config_factory()
        config_b = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config_a.fetcher_name)
        await fetcher_audit_event_factory(fetcher_name=config_b.fetcher_name)

        page = await list_fetcher_audit_events(
            db_session, fetcher_name=config_a.fetcher_name, page=1, per_page=20
        )

        assert page.total == 1
        assert page.items[0].fetcher_name == config_a.fetcher_name

    async def test_actor_is_eagerly_loaded(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory(username="eagerloadfetcheractor")
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, user_id=actor.id
        )

        page = await list_fetcher_audit_events(
            db_session, fetcher_name=config.fetcher_name, page=1, per_page=20
        )

        assert page.items[0].actor is not None
        assert page.items[0].actor.username == "eagerloadfetcheractor"

    async def test_filter_by_actor_uuid(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory()
        other_actor = await user_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, user_id=actor.id
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, user_id=other_actor.id
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            actor=str(actor.id),
        )

        assert page.total == 1
        assert page.items[0].user_id == actor.id

    async def test_filter_by_actor_username(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory(username="filterbyusernamefetcher")
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, user_id=actor.id
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            actor="filterbyusernamefetcher",
        )

        assert page.total == 1

    async def test_unknown_actor_returns_empty_page(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            actor="no-such-actor",
        )

        assert page.total == 0

    async def test_system_literal_returns_empty_page(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        """Every fetcher audit event has a human actor — `"system"`
        never matches any row (`FetcherAuditLog.log_event()` enforces a
        non-null `user_id`)."""
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            actor="system",
        )

        assert page.total == 0

    async def test_matching_event_type_is_included(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, event_type="disabled"
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            event_type=["disabled"],
        )

        assert page.total == 1

    async def test_repeatable_event_types_combine_with_or(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, event_type="disabled"
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, event_type="enabled"
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, event_type="triggered"
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            event_type=["disabled", "enabled"],
        )

        assert page.total == 2

    async def test_mixed_valid_and_invalid_event_types_keeps_valid(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, event_type="disabled"
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            event_type=["disabled", "not_a_real_type"],
        )

        assert page.total == 1

    async def test_all_invalid_event_types_returns_empty_page(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            event_type=["not_a_real_type"],
        )

        assert page.total == 0
        assert page.items == []

    async def test_comma_separated_value_is_treated_as_single_invalid_value(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, event_type="disabled"
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            event_type=["disabled,enabled"],
        )

        assert page.total == 0

    async def test_empty_event_type_list_applies_no_filter(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            event_type=[],
        )

        assert page.total == 2

    async def test_inclusive_from_and_to_date(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        in_range = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 13, tzinfo=UTC),
        )
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            from_date=datetime(2026, 5, 1, tzinfo=UTC),
            to_date=datetime(2026, 5, 31, tzinfo=UTC),
        )

        assert page.total == 1
        assert page.items[0].id == in_range.id

    async def test_date_only_bounds_cover_full_day(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        event = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 13, 23, 59, 0, tzinfo=UTC),
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            from_date=datetime(2026, 5, 13, tzinfo=UTC).date(),
            to_date=None,
        )

        assert page.total == 1
        assert page.items[0].id == event.id

    async def test_filters_combine_with_and(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, user_id=actor.id, event_type="disabled"
        )
        other_actor = await user_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            user_id=other_actor.id,
            event_type="disabled",
        )

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=20,
            actor=str(actor.id),
            event_type=["disabled"],
        )

        assert page.total == 1

    async def test_reports_filtered_total(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        for _ in range(3):
            await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=1,
            per_page=2,
        )

        assert page.total == 3
        assert len(page.items) == 2

    async def test_page_beyond_last_page_returns_empty_with_correct_total(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        page = await list_fetcher_audit_events(
            db_session,
            fetcher_name=config.fetcher_name,
            page=2,
            per_page=20,
        )

        assert page.items == []
        assert page.total == 1

    async def test_orders_newest_first_via_id_desc(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        older = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        newer = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 2, tzinfo=UTC),
        )

        page = await list_fetcher_audit_events(
            db_session, fetcher_name=config.fetcher_name, page=1, per_page=20
        )

        assert [item.id for item in page.items] == [newer.id, older.id]

    async def test_equal_created_at_breaks_tie_by_id_desc(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        same_time = datetime(2026, 5, 13, tzinfo=UTC)
        first = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, created_at=same_time
        )
        second = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, created_at=same_time
        )
        expected_order = sorted([first.id, second.id], reverse=True)

        page = await list_fetcher_audit_events(
            db_session, fetcher_name=config.fetcher_name, page=1, per_page=20
        )

        assert [item.id for item in page.items] == expected_order

    async def test_no_audit_event_or_mutation_created(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        await list_fetcher_audit_events(
            db_session, fetcher_name=config.fetcher_name, page=1, per_page=20
        )

        rows = (await db_session.execute(select(FetcherAuditEvent))).scalars().all()
        assert len(rows) == 1

    async def test_database_error_propagates_unchanged(
        self,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def _boom(*args: Any, **kwargs: Any) -> None:
            raise OperationalError("simulated", {}, Exception("boom"))

        monkeypatch.setattr(db_session, "execute", _boom)

        with pytest.raises(OperationalError):
            await list_fetcher_audit_events(
                db_session, fetcher_name="irrelevant", page=1, per_page=20
            )
