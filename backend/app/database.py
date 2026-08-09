"""Database setup and session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = structlog.get_logger(__name__)

engine = create_async_engine(settings.database_url, hide_parameters=True)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


# Key under which post-commit callbacks are stashed in `AsyncSession.info`
# (a per-session, user-modifiable dict SQLAlchemy provides for exactly
# this kind of use). See `register_post_commit_callback()`.
_POST_COMMIT_CALLBACKS_KEY = "post_commit_callbacks"


def register_post_commit_callback(
    session: AsyncSession, callback: Callable[[], Awaitable[None]]
) -> None:
    """Register a callback to run after `get_db()`'s commit succeeds.

    Per `docs/conventions.md` (Transaction Hygiene Rules), network I/O
    (e.g. a Redis cache purge) must never execute before a transaction
    commits. FastAPI's `BackgroundTasks` mechanism does not guarantee
    this ordering relative to a `yield`-based dependency's post-yield
    code (empirically, `BackgroundTasks` run *before* `get_db()`'s
    post-yield `commit()` completes) — see `docs/conventions.md`:
    "The internal callback or framework mechanism used to bridge
    handler return and dependency completion is an implementation
    choice." This function is that mechanism: `get_db()` invokes every
    registered callback, best-effort, strictly after its own
    `session.commit()` succeeds. Callbacks registered on a request that
    ends in rollback (an exception escaped the handler) never run.
    """
    callbacks: list[Callable[[], Awaitable[None]]] = session.info.setdefault(
        _POST_COMMIT_CALLBACKS_KEY, []
    )
    callbacks.append(callback)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Dependency that provides a database session.

    Commits exactly once after the handler succeeds, or rolls back
    exactly once when an exception escapes — see `docs/conventions.md`
    (Caller-Owned Service Transactions). On the success path only, runs
    every callback registered via `register_post_commit_callback()`,
    each independently caught and logged so one failing best-effort
    side effect never blocks the others.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        else:
            for callback in session.info.get(_POST_COMMIT_CALLBACKS_KEY, []):
                try:
                    await callback()
                except Exception:
                    logger.error("post_commit_callback_failed", exc_info=True)
