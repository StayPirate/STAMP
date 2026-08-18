"""Tests for the Celery Beat startup handler
(backend/app/tasks/beat_startup.py).

See `docs/features/platform/fetcher-infrastructure.md` (Startup
Reconciliation, Wiring Mechanism) for the contract under test:
`beat_init` signal wiring, bootstrap-then-commit-or-rollback ordering,
and fail-fast `SystemExit(1)` on any failure. RedBeat schedule
reconciliation is out of scope — see `docs/drafts/implementation-plan.md`
(P3-05).

`beat_async_bootstrap()` is exercised directly with a mocked session
(mirrors `tests/test_tasks/test_session_cleanup.py`) — the real
behavior of `bootstrap_fetcher_configs()` itself is already covered by
`tests/test_services/test_fetcher_bootstrap.py`; this module verifies
orchestration only. `_beat_startup_handler()` is a synchronous entry
point (calls `asyncio.run()`) — its tests are plain `def`, not
`async def` (see `docs/features/platform/testing-strategy.md`, Sync
Entry-Point Tests), and mock the entire async workflow, mirroring
`tests/test_tasks/test_fetchers.py` (Synchronous Celery wrapper).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from celery.signals import beat_init

from app.tasks import beat_startup


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
    `beat_startup.engine` itself to this fake object instead — mirrors
    `tests/test_tasks/test_worker_startup.py`.
    """

    def __init__(self, dispose: AsyncMock | None = None) -> None:
        self.dispose = dispose or AsyncMock()


def _log_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.tasks.beat_startup"
    ]


@pytest.mark.unit
class TestBeatStartupSignalRegistration:
    def test_receiver_registered_for_beat_init(self) -> None:
        assert beat_init.has_listeners()

    def test_dispatch_uid_prevents_duplicate_registration(self) -> None:
        uid = "sentinel.tasks.beat_startup.beat_init"
        before = [key for key, _ in beat_init.receivers if key[0] == uid]

        beat_init.connect(beat_startup._beat_startup_handler, dispatch_uid=uid)

        after = [key for key, _ in beat_init.receivers if key[0] == uid]
        assert len(before) == 1
        assert len(after) == 1

    def test_redbeat_lock_receiver_registers_before_sentinels(self) -> None:
        """`redbeat.schedulers` registers its own `beat_init` receiver
        (`acquire_distributed_beat_lock`) — since `beat_startup.py`
        imports `redbeat` before connecting its own handler, the
        RedBeat lock-acquire receiver MUST be registered (and thus run)
        before Sentinel's handler when `beat_init` actually fires. This
        proves the distributed lock is acquired before Sentinel's
        bootstrap runs — a precondition the future RedBeat
        reconciliation step relies on."""
        names_in_order = []
        for _, receiver in beat_init.receivers:
            # Receivers are stored as weakrefs (default `weak=True`) —
            # dereference to resolve the underlying function.
            resolved = receiver() if callable(receiver) else receiver
            names_in_order.append(getattr(resolved, "__name__", None))

        assert "acquire_distributed_beat_lock" in names_in_order
        assert "_beat_startup_handler" in names_in_order
        lock_index = names_in_order.index("acquire_distributed_beat_lock")
        handler_index = names_in_order.index("_beat_startup_handler")
        assert lock_index < handler_index


@pytest.mark.unit
class TestBeatAsyncBootstrap:
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
            beat_startup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(beat_startup, "bootstrap_fetcher_configs", _bootstrap_spy)
        monkeypatch.setattr(
            beat_startup,
            "engine",
            _FakeEngine(dispose=AsyncMock(side_effect=_dispose_spy)),
        )

        await beat_startup.beat_async_bootstrap()

        assert call_order == ["bootstrap", "commit", "dispose"]
        session.rollback.assert_not_awaited()

    async def test_bootstrap_failure_rolls_back_and_skips_disposal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = AsyncMock()
        fake_engine = _FakeEngine()
        monkeypatch.setattr(
            beat_startup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(
            beat_startup,
            "bootstrap_fetcher_configs",
            AsyncMock(side_effect=RuntimeError("db down")),
        )
        monkeypatch.setattr(beat_startup, "engine", fake_engine)

        with pytest.raises(RuntimeError, match="db down"):
            await beat_startup.beat_async_bootstrap()

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
            beat_startup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(beat_startup, "bootstrap_fetcher_configs", AsyncMock())
        monkeypatch.setattr(beat_startup, "engine", fake_engine)

        with pytest.raises(RuntimeError, match="commit failed"):
            await beat_startup.beat_async_bootstrap()

        session.rollback.assert_awaited_once_with()
        fake_engine.dispose.assert_not_awaited()


@pytest.mark.unit
class TestBeatStartupHandler:
    def test_success_logs_completed(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        run_spy = AsyncMock()
        monkeypatch.setattr(beat_startup, "beat_async_bootstrap", run_spy)

        with caplog.at_level("INFO"):
            beat_startup._beat_startup_handler()

        run_spy.assert_awaited_once()
        assert any("beat_startup_completed" in m for m in _log_messages(caplog))

    def test_failure_logs_critical_and_exits_with_1(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _boom() -> None:
            raise RuntimeError("bootstrap exploded")

        monkeypatch.setattr(beat_startup, "beat_async_bootstrap", _boom)

        with (
            caplog.at_level("CRITICAL"),
            pytest.raises(SystemExit) as exc_info,
        ):
            beat_startup._beat_startup_handler()

        assert exc_info.value.code == 1
        messages = _log_messages(caplog)
        assert any(
            "beat_startup_failed" in m
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

        monkeypatch.setattr(beat_startup, "beat_async_bootstrap", _boom)

        with caplog.at_level("INFO"), pytest.raises(SystemExit):
            beat_startup._beat_startup_handler()

        assert not any("beat_startup_completed" in m for m in _log_messages(caplog))
