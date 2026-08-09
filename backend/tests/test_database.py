"""Regression tests for database engine construction (app/database.py).

`app/database.py` is excluded from the coverage gate (module-level
side-effecting engine construction — see pyproject.toml), but these
structural assertions guard two real regressions:

1. `DEBUG` must never re-couple to SQLAlchemy's `echo` parameter (see
   docs/features/platform/logging.md, "Relationship with DEBUG").
2. `hide_parameters=True` must remain set so bound SQL parameters
   (which may include credential material) never leak into an
   unhandled `StatementError`'s traceback/message, independent of
   `LOG_LEVEL` (see docs/conventions.md, Secrets and PII Discipline).

Also covers the post-commit callback mechanism (`get_db()`,
`register_post_commit_callback()`) used by API workflows that need a
best-effort side effect (e.g. a Redis cache purge) to run strictly
after the request's transaction commits — see docs/conventions.md
(Transaction Hygiene Rules). A minimal standalone FastAPI app with a
fake session (rather than a real database) isolates the ordering
behavior under test from any real I/O.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from app import database
from app.database import engine, get_db, register_post_commit_callback


class _FakeSession:
    """Minimal double standing in for `AsyncSession`: only the surface
    `get_db()` touches (`commit`, `rollback`, `info`)."""

    def __init__(self, order: list[str]) -> None:
        self._order = order
        self.info: dict[str, Any] = {}

    async def commit(self) -> None:
        self._order.append("commit")

    async def rollback(self) -> None:
        self._order.append("rollback")


class _FakeSessionCM:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


_Db = Annotated[Any, Depends(get_db)]


def _build_app(order: list[str]) -> FastAPI:
    """A minimal FastAPI app wired only with the real `get_db`."""
    app = FastAPI()

    @app.get("/ok")
    async def ok_endpoint(db: _Db, fail: bool = False) -> dict[str, bool]:
        if fail:
            raise HTTPException(status_code=500, detail="boom")
        return {"ok": True}

    @app.get("/with-callback")
    async def with_callback_endpoint(db: _Db) -> dict[str, bool]:
        async def _callback() -> None:
            order.append("callback")

        register_post_commit_callback(db, _callback)
        return {"ok": True}

    @app.get("/with-failing-callback")
    async def with_failing_callback_endpoint(db: _Db) -> dict[str, bool]:
        async def _callback() -> None:
            raise RuntimeError("callback boom")

        register_post_commit_callback(db, _callback)
        return {"ok": True}

    @app.get("/with-multiple-callbacks")
    async def with_multiple_callbacks_endpoint(db: _Db) -> dict[str, bool]:
        async def _first() -> None:
            order.append("first")

        async def _second() -> None:
            order.append("second")

        register_post_commit_callback(db, _first)
        register_post_commit_callback(db, _second)
        return {"ok": True}

    @app.get("/with-failing-then-ok-callback")
    async def with_failing_then_ok_callback_endpoint(db: _Db) -> dict[str, bool]:
        async def _failing() -> None:
            raise RuntimeError("callback boom")

        async def _second() -> None:
            order.append("second")

        register_post_commit_callback(db, _failing)
        register_post_commit_callback(db, _second)
        return {"ok": True}

    @app.get("/failing-with-callback")
    async def failing_with_callback_endpoint(db: _Db) -> dict[str, bool]:
        async def _callback() -> None:
            order.append("callback")

        register_post_commit_callback(db, _callback)
        raise HTTPException(status_code=500, detail="boom")

    return app


@pytest.mark.unit
class TestEngineConstruction:
    """Structural regression guards on the module-level `engine`."""

    def test_hide_parameters_is_enabled(self) -> None:
        """Bound SQL parameters must never leak via exception messages,
        regardless of LOG_LEVEL — see security discipline in
        docs/conventions.md."""
        assert engine.sync_engine.hide_parameters is True

    def test_echo_is_not_enabled_by_default(self) -> None:
        """DEBUG must not control SQLAlchemy echo; echo must remain
        unset (falsy) regardless of the DEBUG setting."""
        assert not engine.sync_engine.echo


@pytest.fixture
def order() -> list[str]:
    return []


@pytest.fixture
def fake_session_factory(
    order: list[str], monkeypatch: pytest.MonkeyPatch
) -> Callable[[], _FakeSessionCM]:
    session = _FakeSession(order)

    def factory() -> _FakeSessionCM:
        return _FakeSessionCM(session)

    monkeypatch.setattr(database, "async_session_factory", factory)
    return factory


@pytest.fixture
async def fake_client(
    order: list[str], fake_session_factory: Callable[[], _FakeSessionCM]
) -> AsyncGenerator[AsyncClient]:
    app = _build_app(order)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.unit
class TestPostCommitCallbacks:
    """`get_db()` + `register_post_commit_callback()` ordering contract."""

    async def test_no_callback_registered_just_commits(
        self, fake_client: AsyncClient, order: list[str]
    ) -> None:
        response = await fake_client.get("/ok")
        assert response.status_code == 200
        assert order == ["commit"]

    async def test_callback_runs_after_commit(
        self, fake_client: AsyncClient, order: list[str]
    ) -> None:
        response = await fake_client.get("/with-callback")
        assert response.status_code == 200
        assert order == ["commit", "callback"]

    async def test_multiple_callbacks_all_run_in_order(
        self, fake_client: AsyncClient, order: list[str]
    ) -> None:
        response = await fake_client.get("/with-multiple-callbacks")
        assert response.status_code == 200
        assert order == ["commit", "first", "second"]

    async def test_callback_not_invoked_when_handler_raises(
        self, fake_client: AsyncClient, order: list[str]
    ) -> None:
        response = await fake_client.get("/failing-with-callback")
        assert response.status_code == 500
        assert order == ["rollback"]

    async def test_failing_callback_is_logged_and_swallowed(
        self,
        fake_client: AsyncClient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level("ERROR"):
            response = await fake_client.get("/with-failing-callback")
        assert response.status_code == 200
        assert "post_commit_callback_failed" in caplog.text

    async def test_failing_callback_does_not_block_next_callback(
        self,
        fake_client: AsyncClient,
        order: list[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """One failing callback must not prevent subsequent registered
        callbacks from running — see `register_post_commit_callback()`
        docstring: "each independently caught and logged so one failing
        best-effort side effect never blocks the others."""
        with caplog.at_level("ERROR"):
            response = await fake_client.get("/with-failing-then-ok-callback")
        assert response.status_code == 200
        assert order == ["commit", "second"]
        assert "post_commit_callback_failed" in caplog.text

    async def test_exception_path_still_rolls_back(
        self, fake_client: AsyncClient, order: list[str]
    ) -> None:
        response = await fake_client.get("/ok", params={"fail": "true"})
        assert response.status_code == 500
        assert order == ["rollback"]
