"""Shared test fixtures for the Sentinel backend.

See docs/features/platform/testing-strategy.md for the full testing strategy.
"""

from __future__ import annotations

import atexit
import itertools
import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from testcontainers.community.postgres import PostgresContainer
    from testcontainers.community.redis import RedisContainer

# Provide required settings for test environment (must precede app imports)
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-secret-key-not-for-production-min-32-chars"
)

from app.api.health import get_readiness_redis_urls
from app.database import Base, get_db
from app.main import app

# Import the models package (not just the User class) so all model
# tables — including ones only referenced via TYPE_CHECKING forward
# refs, like UserRole — are registered on Base.metadata before
# _engine's create_all runs.
from app.models import ApiKey, IdentityAuditEvent, Session, User
from tests.support.audit_models import SampleAuditEvent

# Fictional bcrypt-shaped value — never a real hash (see AGENTS.md Guardrail 23)
_FICTIONAL_PASSWORD_HASH = "$2b$12$" + "a" * 53

# Module-level cache for the auto-provisioned testcontainers PostgreSQL
# instance (started once per session, reused across tests). Kept as a
# module global — rather than function attributes — for clean typing.
_container: PostgresContainer | None = None
_container_url: str | None = None


def _database_url() -> str:
    """Resolve the test database URL.

    Priority:
    1. TEST_DATABASE_URL env var (set by CI or developer override)
    2. Auto-provisioned PostgreSQL via testcontainers (local dev)
    """
    global _container, _container_url

    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url

    if _container_url is not None:
        return _container_url

    # Lazy import — testcontainers is only needed when no URL is provided
    from testcontainers.community.postgres import PostgresContainer

    container = PostgresContainer("postgres:16")
    container.start()
    atexit.register(container.stop)
    # Build asyncpg URL from the container's connection params
    _container_url = container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    )
    _container = container
    return _container_url


# Module-level cache for the auto-provisioned testcontainers Redis
# instance (started once per session, reused across tests). Mirrors the
# PostgreSQL pattern above.
_redis_container: RedisContainer | None = None
_redis_container_url: str | None = None

# Standard Redis server logical database count (`databases 16` default
# config). Used to validate worker-to-database allocation safety — see
# docs/features/platform/testing-strategy.md (Worker and Test Isolation).
_REDIS_LOGICAL_DB_COUNT = 16


def _redis_base_url() -> str:
    """Resolve the base Redis test-harness URL, before the per-worker
    logical database offset is applied.

    Priority:
    1. TEST_REDIS_URL env var (set by CI or developer override)
    2. Auto-provisioned Redis 7 via testcontainers (local dev)
    """
    global _redis_container, _redis_container_url

    url = os.environ.get("TEST_REDIS_URL")
    if url:
        return url

    if _redis_container_url is not None:
        return _redis_container_url

    # Lazy import — testcontainers is only needed when no URL is provided
    from testcontainers.community.redis import RedisContainer

    container = RedisContainer("redis:7")
    container.start()
    atexit.register(container.stop)
    host = container.get_container_host_ip()
    port = container.get_exposed_port(container.port)
    _redis_container_url = f"redis://{host}:{port}/0"
    _redis_container = container
    return _redis_container_url


def _redis_worker_db_index(base_index: int) -> int:
    """This pytest worker's dedicated logical database index.

    Workers are identified by `PYTEST_XDIST_WORKER` (`"gw0"`, `"gw1"`,
    ...), set by pytest-xdist when parallel execution is active; absent
    otherwise (single-process run). Each worker's index is
    `base_index + worker_number`, so consecutive workers use consecutive
    databases with no overlap. Raises explicitly — never silently
    shares a database — if the resulting index would meet or exceed the
    number of databases the server offers.
    """
    worker_input = os.environ.get("PYTEST_XDIST_WORKER")
    offset = 0 if not worker_input else int(worker_input.removeprefix("gw"))
    index = base_index + offset
    if index >= _REDIS_LOGICAL_DB_COUNT:
        raise RuntimeError(
            f"Redis test harness requires logical database {index}, but the "
            f"server only offers {_REDIS_LOGICAL_DB_COUNT} (0-"
            f"{_REDIS_LOGICAL_DB_COUNT - 1}). Reduce the number of parallel "
            "test workers or configure a server with more logical databases."
        )
    return index


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _redis_test_url() -> str:
    """This test session's dedicated Redis URL.

    Combines the harness's base host/port (see `_redis_base_url`) with
    this worker's dedicated logical database (see
    `_redis_worker_db_index`). Verifies connectivity with `PING` before
    returning. Provisioning or connectivity failures raise rather than
    skip the test suite (see docs/features/platform/testing-strategy.md,
    Redis Strategy).
    """
    base_url = _redis_base_url()
    parsed = urlsplit(base_url)
    base_index = int((parsed.path or "/0").lstrip("/") or "0")
    db_index = _redis_worker_db_index(base_index)
    url = urlunsplit((parsed.scheme, parsed.netloc, f"/{db_index}", "", ""))

    client = redis_asyncio.Redis.from_url(url)
    try:
        await client.ping()
    except RedisError as exc:
        raise RuntimeError(f"Redis test harness unreachable at {url}: {exc}") from exc
    finally:
        await client.aclose()

    return url


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
def real_session_factory(_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """A real `async_sessionmaker` bound to the shared test engine.

    Mirrors the production shape in `app/database.py`. Used by tests
    that need a session factory (rather than a single `AsyncSession`)
    against a real database — e.g. the readiness PostgreSQL check,
    which opens its own fresh session per invocation.

    Unlike `db_session`/`db_session_factory`, sessions opened through
    this factory are NOT covered by the per-test savepoint rollback:
    use it for read-only checks only. A test that commits writes
    through it would leak state into the shared test database across
    tests.
    """
    return async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db_session(_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
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

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def redis_client(
    _redis_test_url: str,
) -> AsyncGenerator[redis_asyncio.Redis]:
    """Provide an async Redis client bound to this test's dedicated
    logical database.

    See docs/features/platform/testing-strategy.md (Redis Strategy,
    Fixture Catalog) for the full contract. Before yielding: flushes the
    dedicated database (never `FLUSHALL` — other workers use other
    databases) and overrides `get_readiness_redis_urls` — currently the
    only application-owned Redis dependency — so readiness checks
    exercised during the test observe this same instance. Teardown
    restores the override, flushes again, and closes the client.
    Cleanup/provisioning failures fail the test rather than skip.
    """
    client = redis_asyncio.Redis.from_url(_redis_test_url, decode_responses=True)
    await client.flushdb()

    app.dependency_overrides[get_readiness_redis_urls] = lambda: [_redis_test_url]
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_readiness_redis_urls, None)
        await client.flushdb()
        await client.aclose()


@pytest.fixture
def user_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[User]]:
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


@pytest.fixture
def sample_audit_event_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[SampleAuditEvent]]:
    """Factory fixture for `SampleAuditEvent` instances.

    `SampleAuditEvent` (`tests/support/audit_models.py`) is the
    test-only concrete `AuditEventMixin` subclass used to exercise the
    mixin and `BaseAuditLog` without depending on a production audit
    trail. Defaults: `event_type="sample_event"`; `user_id` is left
    unset (NULL — a system-initiated event) unless overridden.
    """

    async def _create(**overrides: Any) -> SampleAuditEvent:
        defaults: dict[str, Any] = {"event_type": "sample_event"}
        defaults.update(overrides)
        instance = SampleAuditEvent(**defaults)
        db_session.add(instance)
        await db_session.flush()
        return instance

    return _create


@pytest.fixture
def session_factory(
    db_session: AsyncSession,
    user_factory: Callable[..., Awaitable[User]],
) -> Callable[..., Awaitable[Session]]:
    """Factory fixture for `Session` model instances.

    See docs/features/platform/testing-strategy.md (Model Factory
    Fixtures) for the canonical shape this fixture follows.

    Defaults:
    - `user_id`: a freshly created user, when not overridden.
    - `expires_at`: `now() + Settings().session_max_lifetime_days`, mirroring
      the production calculation at login time (see
      `docs/features/identity/authentication.md`, Session Management).
      This is a test-time default only — the model itself never computes
      `expires_at`; login-time calculation is out of scope for this
      persistence-only piece (P2-01).
    """

    async def _create(**overrides: Any) -> Session:
        if "user_id" not in overrides:
            overrides["user_id"] = (await user_factory()).id
        defaults: dict[str, Any] = {
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        }
        defaults.update(overrides)
        instance = Session(**defaults)
        db_session.add(instance)
        await db_session.flush()
        return instance

    return _create


@pytest.fixture
def api_key_factory(
    db_session: AsyncSession,
    user_factory: Callable[..., Awaitable[User]],
) -> Callable[..., Awaitable[ApiKey]]:
    """Factory fixture for `ApiKey` model instances.

    See docs/features/platform/testing-strategy.md (Model Factory
    Fixtures) for the canonical shape this fixture follows.

    Defaults:
    - `user_id`: a freshly created user, when not overridden.
    - `key_hash`: a per-fixture-counter-derived 64-character hex string
      (fictional — never a real SHA-256 digest), unique within the test.
    - `prefix`: a fictional key prefix (`stl_ak_` + 5 hex chars).
    - `name`: derived from the same counter, unique per call — satisfies
      `uq_api_key_user_id_name_active` for the common case (distinct
      names per call).
    """

    counter = itertools.count(1)

    async def _create(**overrides: Any) -> ApiKey:
        if "user_id" not in overrides:
            overrides["user_id"] = (await user_factory()).id
        n = next(counter)
        # Fictional 64-char hex digest — never a real SHA-256 hash
        # (see AGENTS.md Guardrail 23).
        fake_hash = f"{n:064x}"
        defaults: dict[str, Any] = {
            "key_hash": fake_hash,
            "prefix": f"stl_ak_{n:05x}"[:12],
            "name": f"test-key-{n}",
        }
        defaults.update(overrides)
        instance = ApiKey(**defaults)
        db_session.add(instance)
        await db_session.flush()
        return instance

    return _create


@pytest.fixture
def identity_audit_event_factory(
    db_session: AsyncSession,
    user_factory: Callable[..., Awaitable[User]],
) -> Callable[..., Awaitable[IdentityAuditEvent]]:
    """Factory fixture for `IdentityAuditEvent` model instances.

    See docs/features/platform/testing-strategy.md (Model Factory
    Fixtures) for the canonical shape this fixture follows.

    Bypasses `IdentityAuditLog.log_event()` validation on purpose —
    model-layer tests (`tests/test_models/test_identity_audit_event.py`)
    exercise the raw persistence contract (columns, constraints,
    indexes) independently of the service-layer validation rules,
    which are covered by `tests/test_services/test_identity_audit_log.py`.

    Defaults:
    - `event_type`: `"user_created"` (a fictional, valid string value;
      not validated against `IdentityAuditEventType` at this layer).
    - `target_user_id`: a freshly created user, when not overridden.
    """

    async def _create(**overrides: Any) -> IdentityAuditEvent:
        if "target_user_id" not in overrides:
            overrides["target_user_id"] = (await user_factory()).id
        defaults: dict[str, Any] = {"event_type": "user_created"}
        defaults.update(overrides)
        instance = IdentityAuditEvent(**defaults)
        db_session.add(instance)
        await db_session.flush()
        return instance

    return _create
