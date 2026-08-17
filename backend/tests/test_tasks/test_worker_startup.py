"""Tests for the Celery worker startup handler
(backend/app/tasks/worker_startup.py).

See `docs/features/platform/fetcher-infrastructure.md` (Worker Startup
Handler) for the contract under test: `celeryd_after_setup` signal
wiring, bootstrap-then-commit-or-rollback ordering, post-commit engine
disposal before forking, and fail-fast `SystemExit(1)` on any failure.

`worker_async_bootstrap()` is exercised directly with a mocked session
(mirrors `tests/test_tasks/test_session_cleanup.py`) — the real
behavior of `bootstrap_fetcher_configs()` itself is already covered by
`tests/test_services/test_fetcher_bootstrap.py`; this module verifies
orchestration only. `_worker_startup_handler()` is a synchronous entry
point (calls `asyncio.run()`) — its tests are plain `def`, not
`async def` (see `docs/features/platform/testing-strategy.md`, Sync
Entry-Point Tests), and mock the entire async workflow, mirroring
`tests/test_tasks/test_fetchers.py` (Synchronous Celery wrapper).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from celery.signals import celeryd_after_setup

from app.tasks import worker_startup


class _SessionContext:
    """Async context manager returning a fixed `AsyncMock` session —
    mirrors `tests/test_tasks/test_session_cleanup.py`."""

    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeEngine:
    """Substitute for the module-level `engine` singleton.

    `AsyncEngine.dispose` is a read-only attribute on the real engine
    (cannot be monkeypatched directly on the instance), so tests rebind
    `worker_startup.engine` itself to this fake object instead.
    """

    def __init__(self, dispose: AsyncMock | None = None) -> None:
        self.dispose = dispose or AsyncMock()


def _log_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.tasks.worker_startup"
    ]


@pytest.mark.unit
class TestWorkerStartupSignalRegistration:
    def test_receiver_registered_for_celeryd_after_setup(self) -> None:
        assert celeryd_after_setup.has_listeners()

    def test_dispatch_uid_prevents_duplicate_registration(self) -> None:
        uid = "sentinel.tasks.worker_startup.celeryd_after_setup"
        before = [key for key, _ in celeryd_after_setup.receivers if key[0] == uid]

        celeryd_after_setup.connect(
            worker_startup._worker_startup_handler, dispatch_uid=uid
        )

        after = [key for key, _ in celeryd_after_setup.receivers if key[0] == uid]
        assert len(before) == 1
        assert len(after) == 1


@pytest.mark.unit
class TestWorkerAsyncBootstrap:
    async def test_success_bootstraps_commits_then_disposes_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = AsyncMock()
        call_order: list[str] = []
        session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

        async def _bootstrap_spy(db: object) -> None:
            assert db is session
            call_order.append("bootstrap")

        async def _dispose_spy() -> None:
            call_order.append("dispose")

        monkeypatch.setattr(
            worker_startup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(worker_startup, "bootstrap_fetcher_configs", _bootstrap_spy)
        monkeypatch.setattr(
            worker_startup,
            "engine",
            _FakeEngine(dispose=AsyncMock(side_effect=_dispose_spy)),
        )

        await worker_startup.worker_async_bootstrap()

        assert call_order == ["bootstrap", "commit", "dispose"]
        session.rollback.assert_not_awaited()

    async def test_bootstrap_failure_rolls_back_and_skips_disposal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = AsyncMock()
        fake_engine = _FakeEngine()
        monkeypatch.setattr(
            worker_startup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(
            worker_startup,
            "bootstrap_fetcher_configs",
            AsyncMock(side_effect=RuntimeError("db down")),
        )
        monkeypatch.setattr(worker_startup, "engine", fake_engine)

        with pytest.raises(RuntimeError, match="db down"):
            await worker_startup.worker_async_bootstrap()

        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once_with()
        fake_engine.dispose.assert_not_awaited()

    async def test_commit_failure_rolls_back_and_skips_disposal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = AsyncMock()
        session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
        fake_engine = _FakeEngine()
        monkeypatch.setattr(
            worker_startup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(worker_startup, "bootstrap_fetcher_configs", AsyncMock())
        monkeypatch.setattr(worker_startup, "engine", fake_engine)

        with pytest.raises(RuntimeError, match="commit failed"):
            await worker_startup.worker_async_bootstrap()

        session.rollback.assert_awaited_once_with()
        fake_engine.dispose.assert_not_awaited()


@pytest.mark.unit
class TestWorkerStartupHandler:
    def test_success_logs_completed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        run_spy = AsyncMock()
        monkeypatch.setattr(worker_startup, "worker_async_bootstrap", run_spy)

        with caplog.at_level("INFO"):
            worker_startup._worker_startup_handler()

        run_spy.assert_awaited_once()
        assert any("worker_startup_completed" in m for m in _log_messages(caplog))

    def test_failure_logs_critical_and_exits_with_1(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _boom() -> None:
            raise RuntimeError("bootstrap exploded")

        monkeypatch.setattr(worker_startup, "worker_async_bootstrap", _boom)

        with (
            caplog.at_level("CRITICAL"),
            pytest.raises(SystemExit) as exc_info,
        ):
            worker_startup._worker_startup_handler()

        assert exc_info.value.code == 1
        messages = _log_messages(caplog)
        assert any(
            "worker_startup_failed" in m
            and "'stage': 'fetcher_config_bootstrap'" in m
            and "'error_type': 'RuntimeError'" in m
            and "bootstrap exploded" in m
            for m in messages
        )

    def test_failure_does_not_log_completed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _boom() -> None:
            raise RuntimeError("bootstrap exploded")

        monkeypatch.setattr(worker_startup, "worker_async_bootstrap", _boom)

        with caplog.at_level("INFO"), pytest.raises(SystemExit):
            worker_startup._worker_startup_handler()

        assert not any("worker_startup_completed" in m for m in _log_messages(caplog))
