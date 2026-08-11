"""Tests for the weekly session cleanup Celery task."""

from __future__ import annotations

from datetime import UTC, datetime
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


def _task_log_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Rendered messages of this module's own log records, in emission
    order. Scoped by logger name so an unrelated record sharing a
    substring (e.g. from a propagated library logger) cannot be
    mistaken for one of this task's own events.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.tasks.session_cleanup"
    ]


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

        before = datetime.now(UTC)
        with caplog.at_level("INFO"):
            result = await session_cleanup.run_cleanup_sessions()
        after = datetime.now(UTC)

        assert result == 3
        cleanup.assert_awaited_once()
        session.commit.assert_awaited_once_with()
        session.rollback.assert_not_awaited()

        # Exact session and a single timezone-aware UTC `now` snapshot —
        # see docs/features/identity/authentication.md (Session cleanup).
        called_session, called_now = cleanup.call_args.args
        assert called_session is session
        assert called_now.tzinfo is UTC
        assert before <= called_now <= after

        messages = _task_log_messages(caplog)
        assert any("session_cleanup_started" in m for m in messages)
        assert any(
            "session_cleanup_completed" in m and "'deleted_count': 3" in m
            for m in messages
        )
        started_index = next(
            i for i, m in enumerate(messages) if "session_cleanup_started" in m
        )
        completed_index = next(
            i for i, m in enumerate(messages) if "session_cleanup_completed" in m
        )
        assert started_index < completed_index, (
            "session_cleanup_started must be logged before session_cleanup_completed"
        )

    async def test_failure_rolls_back_once_and_propagates(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        session = AsyncMock()
        cleanup = AsyncMock(side_effect=RuntimeError("database failure"))
        monkeypatch.setattr(
            session_cleanup, "async_session_factory", lambda: _SessionContext(session)
        )
        monkeypatch.setattr(session_cleanup, "cleanup_sessions", cleanup)

        with (
            caplog.at_level("INFO"),
            pytest.raises(RuntimeError, match="database failure"),
        ):
            await session_cleanup.run_cleanup_sessions()

        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once_with()

        messages = _task_log_messages(caplog)
        assert any("session_cleanup_started" in m for m in messages)
        assert not any("session_cleanup_completed" in m for m in messages), (
            "session_cleanup_completed must not be logged when the workflow fails"
        )


@pytest.mark.unit
def test_sync_wrapper_calls_async_workflow_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = AsyncMock(return_value=4)
    monkeypatch.setattr(session_cleanup, "run_cleanup_sessions", workflow)

    result = session_cleanup._cleanup_sessions_sync()

    assert result == 4
    workflow.assert_awaited_once_with()
