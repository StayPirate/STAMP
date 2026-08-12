"""Tests for the readiness/liveness check business logic
(backend/app/services/health_service.py).

See `docs/features/platform/health-endpoints.md` for the contract
under test: check timeouts, error classification, Redis instance
discovery/deduplication, and severity aggregation.

Per docs/features/platform/testing-strategy.md (Test Pyramid), both
Tier 1 (unit) and Tier 2 (integration) tests must avoid external
network I/O. Failure-path tests (unreachable, timeout, unexpected
exception) therefore use in-process fakes rather than real sockets to
unresponsive/refusing endpoints; only the success ("ok") path
exercises the real PostgreSQL/Redis test fixtures.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import redis.asyncio as redis_asyncio
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.core.enums import HealthCheckStatus
from app.services import health_service
from app.services.health_service import (
    ReadinessCheckResult,
    _check_postgresql,
    _check_redis,
    aggregate_redis_status,
    check_readiness,
    discover_redis_targets,
)
from tests.support.redis import redis_url_from_client

# ---------------------------------------------------------------------------
# discover_redis_targets()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoverRedisTargets:
    def test_identical_urls_deduplicate_to_one(self) -> None:
        urls = ["redis://localhost:6379/0", "redis://localhost:6379/1"]
        assert discover_redis_targets(urls) == ["redis://localhost:6379/0"]

    def test_different_hosts_are_kept_independent(self) -> None:
        urls = ["redis://host-a:6379/0", "redis://host-b:6379/0"]
        assert discover_redis_targets(urls) == urls

    def test_different_ports_are_kept_independent(self) -> None:
        urls = ["redis://host:6379/0", "redis://host:6380/0"]
        assert discover_redis_targets(urls) == urls

    def test_default_port_applied_when_omitted(self) -> None:
        """A URL without an explicit port is treated as using the
        standard Redis port (6379) for dedup purposes."""
        urls = ["redis://host/0", "redis://host:6379/1"]
        assert discover_redis_targets(urls) == ["redis://host/0"]

    def test_first_seen_url_is_preserved(self) -> None:
        urls = ["redis://host:6379/0", "redis://host:6379/5"]
        assert discover_redis_targets(urls) == ["redis://host:6379/0"]

    def test_preserves_input_order(self) -> None:
        urls = ["redis://c:6379/0", "redis://a:6379/0", "redis://b:6379/0"]
        assert discover_redis_targets(urls) == urls

    def test_empty_input_returns_empty_list(self) -> None:
        assert discover_redis_targets([]) == []

    def test_malformed_port_does_not_raise_and_is_kept(self) -> None:
        """A URL with an unparseable port (e.g. a typo or an
        unexpanded environment variable) must never raise — it is kept
        as its own dedup key so `_check_redis` still reports it as
        `unreachable` rather than the discovery step producing an
        unhandled 500 (see health-endpoints.md, Error responses)."""
        urls = ["redis://host:abc/0", "redis://good:6379/0"]
        assert discover_redis_targets(urls) == urls

    def test_malformed_port_duplicates_deduplicate_by_raw_string(self) -> None:
        urls = ["redis://host:abc/0", "redis://host:abc/0"]
        assert discover_redis_targets(urls) == ["redis://host:abc/0"]

    def test_unparseable_url_does_not_raise_and_is_kept(self) -> None:
        urls = ["redis://[::1:6379/0"]
        assert discover_redis_targets(urls) == urls


# ---------------------------------------------------------------------------
# aggregate_redis_status()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAggregateRedisStatus:
    def test_all_ok_returns_ok(self) -> None:
        result = aggregate_redis_status([HealthCheckStatus.OK, HealthCheckStatus.OK])
        assert result == HealthCheckStatus.OK

    def test_one_timeout_outranks_ok(self) -> None:
        result = aggregate_redis_status(
            [HealthCheckStatus.OK, HealthCheckStatus.TIMEOUT]
        )
        assert result == HealthCheckStatus.TIMEOUT

    def test_one_unreachable_outranks_ok(self) -> None:
        result = aggregate_redis_status(
            [HealthCheckStatus.OK, HealthCheckStatus.UNREACHABLE]
        )
        assert result == HealthCheckStatus.UNREACHABLE

    def test_unreachable_outranks_timeout_in_mixed_case(self) -> None:
        """The resolved precedence for the mixed failure case — see
        docs/features/platform/health-endpoints.md (Readiness — GET
        /ready)."""
        result = aggregate_redis_status(
            [HealthCheckStatus.TIMEOUT, HealthCheckStatus.UNREACHABLE]
        )
        assert result == HealthCheckStatus.UNREACHABLE

    def test_order_of_results_does_not_affect_precedence(self) -> None:
        result = aggregate_redis_status(
            [HealthCheckStatus.UNREACHABLE, HealthCheckStatus.TIMEOUT]
        )
        assert result == HealthCheckStatus.UNREACHABLE

    def test_empty_results_returns_ok(self) -> None:
        """No Redis instance discovered is not itself a failure."""
        assert aggregate_redis_status([]) == HealthCheckStatus.OK


# ---------------------------------------------------------------------------
# _check_postgresql()
# ---------------------------------------------------------------------------


class _FakeAsyncSession:
    """Minimal async-context-manager double standing in for
    `AsyncSession`, used to trigger specific `execute()` outcomes
    without a real database connection (see module docstring — Tier 1
    and Tier 2 tests must avoid external network I/O)."""

    def __init__(self, execute_effect: Any) -> None:
        self._execute_effect = execute_effect

    async def __aenter__(self) -> _FakeAsyncSession:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def execute(self, *args: object, **kwargs: object) -> None:
        if isinstance(self._execute_effect, BaseException):
            raise self._execute_effect
        await self._execute_effect()


@pytest.mark.integration
class TestCheckPostgresqlReal:
    """The only real-database scenario: a reachable server."""

    async def test_reachable_database_returns_ok(
        self, real_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        assert await _check_postgresql(real_session_factory) == HealthCheckStatus.OK


@pytest.mark.unit
class TestCheckPostgresqlFailureModes:
    """Failure paths, exercised via fakes — no real database or
    network I/O."""

    async def test_connection_error_returns_unreachable(self) -> None:
        def factory() -> _FakeAsyncSession:
            return _FakeAsyncSession(OSError("simulated connection refused"))

        result = await _check_postgresql(factory)  # type: ignore[arg-type]
        assert result == HealthCheckStatus.UNREACHABLE

    async def test_slow_query_returns_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(health_service, "_CHECK_TIMEOUT_SECONDS", 0.05)

        async def _slow() -> None:
            await asyncio.sleep(0.2)

        def factory() -> _FakeAsyncSession:
            return _FakeAsyncSession(_slow)

        result = await _check_postgresql(factory)  # type: ignore[arg-type]
        assert result == HealthCheckStatus.TIMEOUT

    async def test_unexpected_exception_logs_error_and_returns_unreachable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def factory() -> _FakeAsyncSession:
            return _FakeAsyncSession(RuntimeError("boom"))

        with caplog.at_level("ERROR"):
            result = await _check_postgresql(factory)  # type: ignore[arg-type]
        assert result == HealthCheckStatus.UNREACHABLE
        assert "readiness_check_unexpected_error" in caplog.text
        assert "postgresql" in caplog.text

    async def test_unexpected_exception_does_not_leak_detail_in_result(self) -> None:
        """The returned status is a fixed enum value — no exception
        text is ever part of the check's return value."""

        def factory() -> _FakeAsyncSession:
            return _FakeAsyncSession(RuntimeError("credentials=super-secret"))

        result = await _check_postgresql(factory)  # type: ignore[arg-type]
        assert result == HealthCheckStatus.UNREACHABLE


# ---------------------------------------------------------------------------
# _check_redis()
# ---------------------------------------------------------------------------


class _FakeRedisClient:
    """Minimal double standing in for `redis.asyncio.Redis`, used to
    trigger specific `ping()` outcomes without a real connection."""

    def __init__(self, ping_effect: Any) -> None:
        self._ping_effect = ping_effect
        self.closed = False

    async def ping(self) -> bool:
        if isinstance(self._ping_effect, BaseException):
            raise self._ping_effect
        result: bool = await self._ping_effect()
        return result

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.integration
class TestCheckRedisReal:
    """The only real-Redis scenario: a reachable instance."""

    async def test_reachable_instance_returns_ok(
        self, redis_client: redis_asyncio.Redis
    ) -> None:
        url = redis_url_from_client(redis_client)
        assert await _check_redis(url) == HealthCheckStatus.OK


@pytest.mark.unit
class TestCheckRedisFailureModes:
    """Failure paths, exercised via fakes — no real Redis or network
    I/O."""

    async def test_connection_error_returns_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _FakeRedisClient(RedisConnectionError("simulated refusal"))
        monkeypatch.setattr(redis_asyncio.Redis, "from_url", lambda *a, **kw: fake)
        result = await _check_redis("redis://irrelevant:6379/0")
        assert result == HealthCheckStatus.UNREACHABLE
        assert fake.closed

    async def test_slow_ping_returns_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(health_service, "_CHECK_TIMEOUT_SECONDS", 0.05)

        async def _slow() -> bool:
            await asyncio.sleep(0.2)
            return True

        fake = _FakeRedisClient(_slow)
        monkeypatch.setattr(redis_asyncio.Redis, "from_url", lambda *a, **kw: fake)
        result = await _check_redis("redis://irrelevant:6379/0")
        assert result == HealthCheckStatus.TIMEOUT
        assert fake.closed

    async def test_malformed_url_logs_error_and_returns_unreachable(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`Redis.from_url` raises `ValueError` synchronously (no
        connection attempt) for an unsupported scheme — this must not
        escape `_check_redis` (see health-endpoints.md, Error
        responses)."""
        with caplog.at_level("ERROR"):
            result = await _check_redis("http://example.com")
        assert result == HealthCheckStatus.UNREACHABLE
        assert "readiness_check_unexpected_error" in caplog.text
        assert "redis" in caplog.text

    async def test_unexpected_exception_does_not_leak_detail_in_result(self) -> None:
        result = await _check_redis("not-a-valid-url-at-all")
        assert result == HealthCheckStatus.UNREACHABLE


# ---------------------------------------------------------------------------
# check_readiness() — orchestration, concurrency, fresh checks
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCheckReadinessReal:
    async def test_all_healthy_returns_ok_for_both(
        self,
        real_session_factory: async_sessionmaker[AsyncSession],
        redis_client: redis_asyncio.Redis,
    ) -> None:
        url = redis_url_from_client(redis_client)
        result = await check_readiness(real_session_factory, [url])
        assert result == ReadinessCheckResult(
            postgresql=HealthCheckStatus.OK, redis=HealthCheckStatus.OK
        )

    async def test_no_redis_instances_reports_ok(
        self, real_session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """An empty Redis URL list (no instance discovered) is not
        itself a failure."""
        result = await check_readiness(real_session_factory, [])
        assert result.redis == HealthCheckStatus.OK


@pytest.mark.unit
class TestCheckReadinessOrchestration:
    async def test_checks_each_unique_redis_instance_independently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two distinct instances must each be checked, and the
        aggregate must reflect the failing one — proving per-instance
        (not just first-instance) checking."""
        calls: list[str] = []

        async def _fake_check_redis(url: str) -> HealthCheckStatus:
            calls.append(url)
            return (
                HealthCheckStatus.OK
                if url == "redis://a:6379/0"
                else HealthCheckStatus.UNREACHABLE
            )

        async def _fake_check_postgresql(
            _session_factory: async_sessionmaker[AsyncSession],
        ) -> HealthCheckStatus:
            return HealthCheckStatus.OK

        monkeypatch.setattr(health_service, "_check_redis", _fake_check_redis)
        monkeypatch.setattr(health_service, "_check_postgresql", _fake_check_postgresql)

        result = await check_readiness(
            object(),  # type: ignore[arg-type]
            ["redis://a:6379/0", "redis://b:6379/0"],
        )

        assert set(calls) == {"redis://a:6379/0", "redis://b:6379/0"}
        assert result.redis == HealthCheckStatus.UNREACHABLE

    async def test_malformed_redis_url_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed configured Redis URL must degrade to
        `unreachable`, never escape as an unhandled exception (see
        health-endpoints.md, Error responses: "never produces a 500
        response")."""

        async def _fake_check_postgresql(
            _session_factory: async_sessionmaker[AsyncSession],
        ) -> HealthCheckStatus:
            return HealthCheckStatus.OK

        monkeypatch.setattr(health_service, "_check_postgresql", _fake_check_postgresql)

        result = await check_readiness(
            object(),  # type: ignore[arg-type]
            ["redis://host:not-a-port/0"],
        )
        assert result.redis == HealthCheckStatus.UNREACHABLE

    async def test_checks_run_concurrently_not_sequentially(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deterministic proof of concurrency: both fake checks
        rendezvous at a shared `asyncio.Barrier` before either returns.
        A sequential implementation would start the second check only
        after the first had already returned past the barrier — but the
        first is blocked waiting for the second party, so a sequential
        `check_readiness()` would deadlock here and the bounding
        `asyncio.wait_for` below would raise `TimeoutError`. This
        replaces a prior wall-clock comparison (elapsed time against a
        fixed multiple of an artificial delay), which is inherently
        susceptible to CI scheduling jitter in either direction."""
        barrier = asyncio.Barrier(2)

        async def _rendezvous_postgresql(
            _session_factory: async_sessionmaker[AsyncSession],
        ) -> HealthCheckStatus:
            await barrier.wait()
            return HealthCheckStatus.OK

        async def _rendezvous_redis(_url: str) -> HealthCheckStatus:
            await barrier.wait()
            return HealthCheckStatus.OK

        monkeypatch.setattr(health_service, "_check_postgresql", _rendezvous_postgresql)
        monkeypatch.setattr(health_service, "_check_redis", _rendezvous_redis)

        result = await asyncio.wait_for(
            check_readiness(object(), ["redis://host:6379/0"]),  # type: ignore[arg-type]
            timeout=2.0,
        )

        assert result.postgresql == HealthCheckStatus.OK
        assert result.redis == HealthCheckStatus.OK
