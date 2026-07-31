"""Shared test fixtures for the Sentinel backend.

See docs/features/platform/testing-strategy.md for the full testing strategy.
"""

from __future__ import annotations

import atexit
import itertools
import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

# Provide required settings for test environment (must precede app imports)
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-secret-key-not-for-production-min-32-chars"
)

from app.database import Base, get_db
from app.main import app

# Import the models package (not just the User class) so all model
# tables — including ones only referenced via TYPE_CHECKING forward
# refs, like UserRole — are registered on Base.metadata before
# _engine's create_all runs.
from app.models import User

# Fictional bcrypt-shaped value — never a real hash (see AGENTS.md Guardrail 23)
_FICTIONAL_PASSWORD_HASH = "$2b$12$" + "a" * 53


def _database_url() -> str:
    """Resolve the test database URL.

    Priority:
    1. TEST_DATABASE_URL env var (set by CI or developer override)
    2. Auto-provisioned PostgreSQL via testcontainers (local dev)
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url

    # Lazy import — testcontainers is only needed when no URL is provided
    from testcontainers.community.postgres import PostgresContainer

    # Module-level container — started once per session, reused across tests
    if not hasattr(_database_url, "_container"):
        container = PostgresContainer("postgres:16")
        container.start()
        atexit.register(container.stop)
        # Build asyncpg URL from the container's connection params
        url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
        _database_url._container = container
        _database_url._url = url
    return _database_url._url


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _engine() -> AsyncGenerator[AsyncEngine]:
    """Create the async engine and tables once per session."""
    engine = create_async_engine(_database_url(), echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(_engine) -> AsyncGenerator[AsyncSession]:
    """Provide an async DB session with per-test transaction rollback.

    Uses the SQLAlchemy 2.0 recommended pattern: the session joins an
    external transaction with join_transaction_mode="create_savepoint".
    When test code (or service code) calls session.commit(), it commits
    a savepoint — not the outer transaction. The outer transaction is
    rolled back in teardown, reverting all changes.

    See: https://docs.sqlalchemy.org/en/20/orm/session_transaction.html
         #joining-a-session-into-an-external-transaction-such-as-for-test-suites
    """
    async with _engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient]:
    """Provide an async HTTP test client with DB session override.

    The FastAPI app's get_db dependency is overridden to use the test
    session, ensuring e2e tests share the test transaction.
    """

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def user_factory(db_session: AsyncSession):
    """Factory fixture for User model instances.

    See docs/features/platform/testing-strategy.md (Model Factory
    Fixtures) for the canonical shape this fixture follows.

    Defaults:
    - `username` / `email`: derived from a per-fixture counter, unique
      within the test.
    - `password_hash`: a fictional bcrypt-shaped hash, set only when the
      caller does not supply `external_id` — satisfies
      `chk_user_auth_exclusive` for the common case (local user).
      Callers creating an external user must pass `external_id` and
      leave `password_hash` unset (or explicitly `None`).
    """
    counter = itertools.count(1)

    async def _create(**overrides: Any) -> User:
        n = next(counter)
        defaults: dict[str, Any] = {
            "username": f"user{n}",
            "email": f"user{n}@example.com",
        }
        defaults.update(overrides)
        # chk_user_auth_exclusive: a local user has a password hash and no
        # external_id; an external user has the inverse.
        if not defaults.get("external_id"):
            defaults.setdefault("password_hash", _FICTIONAL_PASSWORD_HASH)
        instance = User(**defaults)
        db_session.add(instance)
        await db_session.flush()
        return instance

    return _create
