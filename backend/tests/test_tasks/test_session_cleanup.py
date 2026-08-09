"""Tests for the weekly session cleanup Celery task."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.tasks import session_cleanup


class _SessionContext:
    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


@pytest.mark.unit
class TestRunCleanupSessions:
    async def test_success_commits_once_and_logs_completion(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = AsyncMock()
        cleanup = AsyncMock(return_value=3)
        monkeypatch.setattr(
            session_cleanup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(session_cleanup, "cleanup_sessions", cleanup)

        with caplog.at_level("INFO"):
            result = await session_cleanup.run_cleanup_sessions()

        assert result == 3
        cleanup.assert_awaited_once()
        session.commit.assert_awaited_once_with()
        session.rollback.assert_not_awaited()
        assert "session_cleanup_started" in caplog.text
        assert "session_cleanup_completed" in caplog.text
        assert "deleted_count" in caplog.text

    async def test_failure_rolls_back_once_and_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = AsyncMock()
        cleanup = AsyncMock(side_effect=RuntimeError("database failure"))
        monkeypatch.setattr(
            session_cleanup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(session_cleanup, "cleanup_sessions", cleanup)

        with pytest.raises(RuntimeError, match="database failure"):
            await session_cleanup.run_cleanup_sessions()

        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once_with()


@pytest.mark.unit
def test_sync_wrapper_calls_async_workflow_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = AsyncMock(return_value=4)
    monkeypatch.setattr(session_cleanup, "run_cleanup_sessions", workflow)

    result = session_cleanup._cleanup_sessions_sync()

    assert result == 4
    workflow.assert_awaited_once_with()
