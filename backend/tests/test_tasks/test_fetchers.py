"""Tests for the generic `run_fetcher` Celery task
(backend/app/tasks/fetchers.py).

See `docs/features/platform/fetcher-infrastructure.md` (Celery
Integration, Concurrency Control) for the contract under test:
argument validation and its order, unknown/deregistered fetcher
handling, the synchronous `asyncio.run()` bridge, task registration
under the exact name `run_fetcher`, no-retry/no-result-backend
behavior, and delegation to `BaseFetcher.run()` only after the
acquisition transaction commits.

Acquisition logic itself (locking, staleness, adoption) is exercised
against real PostgreSQL in
`tests/test_services/test_fetcher_execution.py`. This module mocks
`acquire_fetcher_run`/`finalize_manual_run_as_failure` to isolate the
task's own orchestration and validation responsibilities.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.base_fetcher import FETCHER_REGISTRY, FetcherRunConfig
from app.services.fetcher_execution import FetcherAcquisition
from app.tasks import fetchers


class _SessionContext:
    """Async context manager returning a fixed `AsyncMock` session —
    mirrors `tests/test_tasks/test_session_cleanup.py`."""

    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _StubFetcher:
    """Minimal `FETCHER_REGISTRY` entry stub.

    `run_fetcher_async` instantiates the registered class with no
    arguments and awaits `.run(run_id=..., config=...)` — this stub
    captures every instance created so tests can assert on the call
    made to the specific instance the task code produced.
    """

    created: ClassVar[list[_StubFetcher]] = []

    def __init__(self) -> None:
        self.run = AsyncMock()
        _StubFetcher.created.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.created = []


@pytest.fixture(autouse=True)
def _isolated_registry() -> Generator[None]:
    """Snapshot/restore `FETCHER_REGISTRY` around every test — mirrors
    `tests/test_services/test_base_fetcher.py` (Test Independence)."""
    original = dict(FETCHER_REGISTRY)
    _StubFetcher.reset()
    yield
    FETCHER_REGISTRY.clear()
    FETCHER_REGISTRY.update(original)


def _make_config(fetcher_name: str = "test_fetcher") -> FetcherRunConfig:
    return FetcherRunConfig(
        fetcher_name=fetcher_name,
        enabled=True,
        run_timeout=3600,
        request_delay=0,
        custom_settings={},
        schedule_override=None,
    )


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateArguments:
    def test_scheduled_valid_combination_returns_schedule(self) -> None:
        trigger = fetchers._validate_arguments("test_fetcher", "schedule", None, None)
        assert trigger.value == "schedule"

    def test_manual_valid_combination_returns_manual(self) -> None:
        trigger = fetchers._validate_arguments(
            "test_fetcher", "manual", str(uuid4()), str(uuid4())
        )
        assert trigger.value == "manual"

    def test_invalid_triggered_by_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid triggered_by"):
            fetchers._validate_arguments("test_fetcher", "bogus", None, None)

    def test_scheduled_with_user_id_raises(self) -> None:
        with pytest.raises(ValueError, match="must not supply"):
            fetchers._validate_arguments("test_fetcher", "schedule", str(uuid4()), None)

    def test_scheduled_with_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="must not supply"):
            fetchers._validate_arguments("test_fetcher", "schedule", None, str(uuid4()))

    def test_manual_missing_user_id_raises(self) -> None:
        with pytest.raises(ValueError, match="valid user_id"):
            fetchers._validate_arguments("test_fetcher", "manual", None, str(uuid4()))

    def test_manual_empty_user_id_raises(self) -> None:
        with pytest.raises(ValueError, match="valid user_id"):
            fetchers._validate_arguments("test_fetcher", "manual", "", str(uuid4()))

    def test_manual_invalid_uuid_user_id_raises(self) -> None:
        with pytest.raises(ValueError, match="valid user_id"):
            fetchers._validate_arguments(
                "test_fetcher", "manual", "not-a-uuid", str(uuid4())
            )

    def test_manual_missing_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="valid run_id"):
            fetchers._validate_arguments("test_fetcher", "manual", str(uuid4()), None)

    def test_manual_empty_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="valid run_id"):
            fetchers._validate_arguments("test_fetcher", "manual", str(uuid4()), "")

    def test_manual_invalid_uuid_run_id_raises(self) -> None:
        with pytest.raises(ValueError, match="valid run_id"):
            fetchers._validate_arguments(
                "test_fetcher", "manual", str(uuid4()), "not-a-uuid"
            )


# ---------------------------------------------------------------------------
# Unknown fetcher handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleUnknownFetcher:
    async def test_scheduled_unknown_logs_warning_and_opens_no_session(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        factory = MagicMock(side_effect=AssertionError("must not open a session"))
        monkeypatch.setattr(fetchers, "async_session_factory", factory)

        with caplog.at_level("WARNING"):
            await fetchers._handle_unknown_fetcher("ghost_fetcher", None)

        factory.assert_not_called()
        assert any(
            "scheduled_run_unknown_fetcher" in record.getMessage()
            and "ghost_fetcher" in record.getMessage()
            for record in caplog.records
        )

    async def test_manual_unknown_finalizes_and_commits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = AsyncMock()
        monkeypatch.setattr(
            fetchers, "async_session_factory", lambda: _SessionContext(session)
        )
        finalize = AsyncMock()
        monkeypatch.setattr(fetchers, "finalize_manual_run_as_failure", finalize)
        run_id = uuid4()

        await fetchers._handle_unknown_fetcher("ghost_fetcher", run_id)

        finalize.assert_awaited_once()
        _, kwargs = finalize.call_args
        assert kwargs["run_id"] == run_id
        assert kwargs["fetcher_name"] == "ghost_fetcher"
        assert kwargs["error_message"] == (
            "Fetcher deregistered between trigger and execution"
        )
        assert kwargs["now"].tzinfo is UTC
        session.commit.assert_awaited_once_with()
        session.rollback.assert_not_awaited()

    async def test_manual_unknown_finalize_failure_rolls_back_and_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = AsyncMock()
        monkeypatch.setattr(
            fetchers, "async_session_factory", lambda: _SessionContext(session)
        )
        finalize = AsyncMock(side_effect=ValueError("not found"))
        monkeypatch.setattr(fetchers, "finalize_manual_run_as_failure", finalize)

        with pytest.raises(ValueError, match="not found"):
            await fetchers._handle_unknown_fetcher("ghost_fetcher", uuid4())

        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# run_fetcher_async orchestration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunFetcherAsync:
    async def test_invalid_arguments_raise_before_touching_registry_or_db(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory = MagicMock(side_effect=AssertionError("must not open a session"))
        monkeypatch.setattr(fetchers, "async_session_factory", factory)

        with pytest.raises(ValueError, match="Invalid triggered_by"):
            await fetchers.run_fetcher_async("test_fetcher", "bogus")

        factory.assert_not_called()

    async def test_unknown_scheduled_fetcher_delegates_and_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        handle = AsyncMock()
        monkeypatch.setattr(fetchers, "_handle_unknown_fetcher", handle)

        await fetchers.run_fetcher_async("ghost_fetcher", "schedule")

        handle.assert_awaited_once_with("ghost_fetcher", None)

    async def test_unknown_manual_fetcher_delegates_with_parsed_run_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        handle = AsyncMock()
        monkeypatch.setattr(fetchers, "_handle_unknown_fetcher", handle)
        run_id = uuid4()
        user_id = uuid4()

        await fetchers.run_fetcher_async(
            "ghost_fetcher", "manual", str(user_id), str(run_id)
        )

        handle.assert_awaited_once_with("ghost_fetcher", run_id)

    async def test_acquisition_none_commits_and_does_not_execute_fetcher(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FETCHER_REGISTRY["test_fetcher"] = _StubFetcher  # type: ignore[assignment]
        session = AsyncMock()
        monkeypatch.setattr(
            fetchers, "async_session_factory", lambda: _SessionContext(session)
        )
        acquire = AsyncMock(return_value=None)
        monkeypatch.setattr(fetchers, "acquire_fetcher_run", acquire)

        await fetchers.run_fetcher_async("test_fetcher", "schedule")

        session.commit.assert_awaited_once_with()
        session.rollback.assert_not_awaited()
        assert _StubFetcher.created == []

    async def test_acquisition_success_executes_fetcher_after_commit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FETCHER_REGISTRY["test_fetcher"] = _StubFetcher  # type: ignore[assignment]
        session = AsyncMock()
        commit_order: list[str] = []
        session.commit = AsyncMock(side_effect=lambda: commit_order.append("commit"))
        monkeypatch.setattr(
            fetchers, "async_session_factory", lambda: _SessionContext(session)
        )
        acquired_run_id = uuid4()
        config = _make_config()
        acquire = AsyncMock(
            return_value=FetcherAcquisition(run_id=acquired_run_id, config=config)
        )
        monkeypatch.setattr(fetchers, "acquire_fetcher_run", acquire)

        await fetchers.run_fetcher_async("test_fetcher", "schedule")

        assert len(_StubFetcher.created) == 1
        instance = _StubFetcher.created[0]
        instance.run.assert_awaited_once_with(run_id=acquired_run_id, config=config)
        # The fetcher must only execute after the acquisition commit.
        assert commit_order == ["commit"]

    async def test_acquisition_passes_parsed_run_id_and_trigger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FETCHER_REGISTRY["test_fetcher"] = _StubFetcher  # type: ignore[assignment]
        session = AsyncMock()
        monkeypatch.setattr(
            fetchers, "async_session_factory", lambda: _SessionContext(session)
        )
        acquire = AsyncMock(return_value=None)
        monkeypatch.setattr(fetchers, "acquire_fetcher_run", acquire)
        run_id = uuid4()
        user_id = uuid4()

        await fetchers.run_fetcher_async(
            "test_fetcher", "manual", str(user_id), str(run_id)
        )

        _, kwargs = acquire.call_args
        assert kwargs["fetcher_name"] == "test_fetcher"
        assert kwargs["triggered_by"].value == "manual"
        assert kwargs["run_id"] == run_id
        assert isinstance(kwargs["now"], datetime)
        assert kwargs["now"].tzinfo is UTC

    async def test_acquisition_failure_rolls_back_and_propagates_no_execution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FETCHER_REGISTRY["test_fetcher"] = _StubFetcher  # type: ignore[assignment]
        session = AsyncMock()
        monkeypatch.setattr(
            fetchers, "async_session_factory", lambda: _SessionContext(session)
        )
        acquire = AsyncMock(side_effect=RuntimeError("db unreachable"))
        monkeypatch.setattr(fetchers, "acquire_fetcher_run", acquire)

        with pytest.raises(RuntimeError, match="db unreachable"):
            await fetchers.run_fetcher_async("test_fetcher", "schedule")

        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once_with()
        assert _StubFetcher.created == []

    async def test_fetcher_run_exception_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        FETCHER_REGISTRY["test_fetcher"] = _StubFetcher  # type: ignore[assignment]
        session = AsyncMock()
        monkeypatch.setattr(
            fetchers, "async_session_factory", lambda: _SessionContext(session)
        )
        acquire = AsyncMock(
            return_value=FetcherAcquisition(run_id=uuid4(), config=_make_config())
        )
        monkeypatch.setattr(fetchers, "acquire_fetcher_run", acquire)

        # Configure the instance's `.run()` to fail once it is created.
        original_init = _StubFetcher.__init__

        def _failing_init(self: _StubFetcher) -> None:
            original_init(self)
            self.run = AsyncMock(side_effect=RuntimeError("execution failed"))

        monkeypatch.setattr(_StubFetcher, "__init__", _failing_init)

        with pytest.raises(RuntimeError, match="execution failed"):
            await fetchers.run_fetcher_async("test_fetcher", "schedule")


# ---------------------------------------------------------------------------
# Synchronous Celery wrapper
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_sync_wrapper_calls_async_workflow_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = AsyncMock(return_value=None)
    monkeypatch.setattr(fetchers, "run_fetcher_async", workflow)

    fetchers._run_fetcher_sync(object(), "test_fetcher", "manual", "user-id", "run-id")

    workflow.assert_awaited_once_with("test_fetcher", "manual", "user-id", "run-id")


@pytest.mark.unit
def test_sync_wrapper_default_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = AsyncMock(return_value=None)
    monkeypatch.setattr(fetchers, "run_fetcher_async", workflow)

    fetchers._run_fetcher_sync(object(), "test_fetcher")

    workflow.assert_awaited_once_with("test_fetcher", "schedule", None, None)


# ---------------------------------------------------------------------------
# Task registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTaskRegistration:
    def test_registered_under_exact_unqualified_name(self) -> None:
        assert fetchers.run_fetcher.name == "run_fetcher"

    def test_registered_on_the_singleton_celery_app(self) -> None:
        from app.celery_app import celery_app

        assert celery_app.tasks["run_fetcher"].name == fetchers.run_fetcher.name
