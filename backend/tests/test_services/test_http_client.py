"""Tests for the shared HTTP client factory and TLS trust store
(backend/app/services/http_client.py).

All tests are unit tests: no database, no real network I/O, no real
sleeps. Retry sleeps are verified via a monkeypatched `asyncio.sleep`
that records call arguments instead of waiting. TLS tests never rely on
the default `SUSE_CA_CERT_PATH` (or the process CWD) — every path is
passed explicitly.
"""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings as app_settings
from app.core.exceptions import TLSConfigurationError
from app.services import http_client as hc

# The real SUSE Trust Root CA committed in the repository. Not a secret —
# a public CA certificate — safe to reference directly in tests instead
# of generating a throwaway certificate.
_REAL_CA_PATH = Path(__file__).resolve().parents[2] / "certs" / "SUSE_Trust_Root.crt"


@pytest.fixture(autouse=True)
def _isolated_ca_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure every test uses an explicit CA path, never the CWD-relative
    default — prevents tests from depending on the working directory."""
    monkeypatch.setattr(app_settings, "suse_ca_cert_path", str(_REAL_CA_PATH))


# ---------------------------------------------------------------------------
# build_tls_context()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildTlsContext:
    def test_valid_ca_returns_combined_context(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            context = hc.build_tls_context()
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        # Distinguishes "SUSE CA successfully loaded" from the "missing"
        # fallback path below, which also returns a CERT_REQUIRED context
        # (system-only) — the absence of the missing-CA warning is the
        # signal that the combined trust store branch actually ran.
        assert "suse_ca_cert_missing" not in caplog.text

    def test_missing_ca_warns_and_returns_system_only_context(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        missing_path = tmp_path / "does-not-exist.crt"
        monkeypatch.setattr(app_settings, "suse_ca_cert_path", str(missing_path))
        with caplog.at_level("WARNING"):
            context = hc.build_tls_context()
        assert isinstance(context, ssl.SSLContext)
        assert "suse_ca_cert_missing" in caplog.text

    def test_corrupt_ca_raises_tls_configuration_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        corrupt_path = tmp_path / "corrupt.crt"
        corrupt_path.write_text("this is not a valid certificate")
        monkeypatch.setattr(app_settings, "suse_ca_cert_path", str(corrupt_path))
        with pytest.raises(TLSConfigurationError) as exc_info:
            hc.build_tls_context()
        assert str(corrupt_path) in str(exc_info.value)

    def test_context_is_fresh_on_every_call(self) -> None:
        """No module-level caching — supports certificate rotation without
        process restart (docs/features/platform/networking.md)."""
        assert hc.build_tls_context() is not hc.build_tls_context()


# ---------------------------------------------------------------------------
# create_http_client() — defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateHttpClientDefaults:
    async def test_user_agent_format(self) -> None:
        client = hc.create_http_client("test_component")
        try:
            ua = client.headers["user-agent"]
            assert ua.startswith("Sentinel/")
            assert "(test_component; +https://github.com/StayPirate/sentinel)" in ua
        finally:
            await client.aclose()

    async def test_user_agent_dev_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_not_found(_name: str) -> str:
            from importlib.metadata import PackageNotFoundError

            raise PackageNotFoundError

        monkeypatch.setattr(hc, "get_version", _raise_not_found)
        client = hc.create_http_client("test_component")
        try:
            assert "Sentinel/dev (" in client.headers["user-agent"]
        finally:
            await client.aclose()

    async def test_accept_header_default(self) -> None:
        client = hc.create_http_client("test_component")
        try:
            assert client.headers["accept"] == "application/json"
        finally:
            await client.aclose()

    async def test_accept_header_overridable(self) -> None:
        client = hc.create_http_client(
            "ibs_client", headers={"Accept": "application/xml"}
        )
        try:
            assert client.headers["accept"] == "application/xml"
        finally:
            await client.aclose()

    async def test_timeout_defaults(self) -> None:
        client = hc.create_http_client("test_component")
        try:
            timeout = client.timeout
            assert timeout.connect == 10.0
            assert timeout.read == 30.0
            assert timeout.write == 10.0
            assert timeout.pool == 10.0
        finally:
            await client.aclose()

    async def test_follow_redirects_disabled_by_default(self) -> None:
        client = hc.create_http_client("test_component")
        try:
            assert client.follow_redirects is False
        finally:
            await client.aclose()

    async def test_no_warning_on_defaults(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            client = hc.create_http_client("test_component")
        try:
            assert "tls_verify_overridden" not in caplog.text
            assert "redirects_enabled" not in caplog.text
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# create_http_client() — override safety
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateHttpClientOverrideSafety:
    async def test_user_agent_not_overridable_via_headers(self) -> None:
        client = hc.create_http_client(
            "test_component", headers={"User-Agent": "custom-agent/1.0"}
        )
        try:
            ua = client.headers["user-agent"]
            assert ua != "custom-agent/1.0"
            assert ua.startswith("Sentinel/")
        finally:
            await client.aclose()

    async def test_user_agent_not_overridable_case_insensitive(self) -> None:
        """A differently-cased 'user-agent' key must not slip through as a
        duplicate header alongside the template-generated one."""
        client = hc.create_http_client(
            "test_component", headers={"USER-AGENT": "custom-agent/1.0"}
        )
        try:
            values = client.headers.get_list("user-agent")
            assert len(values) == 1
            assert values[0].startswith("Sentinel/")
        finally:
            await client.aclose()

    async def test_verify_false_warns_with_caller_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            client = hc.create_http_client("sync_nvd_cves", verify=False)
        try:
            assert "tls_verify_overridden" in caplog.text
            assert "sync_nvd_cves" in caplog.text
        finally:
            await client.aclose()

    async def test_custom_ssl_context_warns_with_caller_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        custom_context = ssl.create_default_context()
        with caplog.at_level("WARNING"):
            client = hc.create_http_client("custom_client", verify=custom_context)
        try:
            assert "tls_verify_overridden" in caplog.text
            assert "custom_client" in caplog.text
        finally:
            await client.aclose()

    async def test_follow_redirects_true_warns_with_caller_name(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            client = hc.create_http_client("sync_example", follow_redirects=True)
        try:
            assert "redirects_enabled" in caplog.text
            assert "sync_example" in caplog.text
            assert client.follow_redirects is True
        finally:
            await client.aclose()

    async def test_timeout_overridable(self) -> None:
        client = hc.create_http_client("test_component", timeout=httpx.Timeout(5.0))
        try:
            assert client.timeout.connect == 5.0
        finally:
            await client.aclose()

    async def test_limits_default(self) -> None:
        client = hc.create_http_client("test_component")
        try:
            transport = client._transport
            assert isinstance(transport, hc._RetryTransport)
            pool = transport._inner._pool  # type: ignore[attr-defined]
            assert pool._max_connections == 100
            assert pool._max_keepalive_connections == 20
        finally:
            await client.aclose()

    async def test_limits_overridable(self) -> None:
        client = hc.create_http_client(
            "test_component",
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
        try:
            transport = client._transport
            assert isinstance(transport, hc._RetryTransport)
            pool = transport._inner._pool  # type: ignore[attr-defined]
            assert pool._max_connections == 5
            assert pool._max_keepalive_connections == 2
        finally:
            await client.aclose()

    async def test_retry_transport_is_wired_into_client(self) -> None:
        """The client returned by the factory actually uses
        `_RetryTransport` — guards against a regression where the retry
        policy silently stops being applied (e.g., accidentally
        re-enabling httpx's built-in transport retry instead)."""
        client = hc.create_http_client("test_component")
        try:
            assert isinstance(client._transport, hc._RetryTransport)
        finally:
            await client.aclose()

    async def test_retry_non_idempotent_is_forwarded_to_transport(self) -> None:
        client = hc.create_http_client("test_component", retry_non_idempotent=True)
        try:
            transport = client._transport
            assert isinstance(transport, hc._RetryTransport)
            assert transport._retry_non_idempotent is True
        finally:
            await client.aclose()


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseRetryAfter:
    def test_integer_seconds(self) -> None:
        assert hc._parse_retry_after("30") == 30.0

    def test_zero_is_valid(self) -> None:
        assert hc._parse_retry_after("0") == 0.0

    def test_negative_integer_treated_as_absent(self) -> None:
        assert hc._parse_retry_after("-5") is None

    def test_decimal_value_treated_as_absent(self) -> None:
        """RFC 7231 delay-seconds is `1*DIGIT` — fractional values are not
        a valid delay-seconds and must not be silently rounded/accepted."""
        assert hc._parse_retry_after("30.5") is None

    def test_leading_decimal_point_treated_as_absent(self) -> None:
        assert hc._parse_retry_after(".5") is None

    def test_malformed_string_treated_as_absent(self) -> None:
        assert hc._parse_retry_after("not-a-value") is None

    def test_empty_string_treated_as_absent(self) -> None:
        assert hc._parse_retry_after("") is None

    def test_http_date_in_future(self) -> None:
        from datetime import UTC, datetime, timedelta
        from email.utils import format_datetime

        future = datetime.now(UTC) + timedelta(seconds=45)
        header_value = format_datetime(future, usegmt=True)
        wait = hc._parse_retry_after(header_value)
        assert wait is not None
        # Allow small scheduling jitter between construction and parsing.
        assert 40 <= wait <= 46

    def test_http_date_already_elapsed_treated_as_absent(self) -> None:
        from datetime import UTC, datetime, timedelta
        from email.utils import format_datetime

        past = datetime.now(UTC) - timedelta(seconds=10)
        header_value = format_datetime(past, usegmt=True)
        assert hc._parse_retry_after(header_value) is None

    def test_http_date_with_unknown_offset_is_treated_as_utc(self) -> None:
        """RFC 5322 uses a `-0000` offset to signal "no timezone
        information" (as opposed to `+0000`, meaning "confirmed UTC").
        `email.utils.parsedate_to_datetime` parses `-0000` into a
        timezone-naive `datetime`. Sentinel treats an absent timezone
        as UTC rather than propagating a naive datetime into the
        subsequent arithmetic against `datetime.now(UTC)`."""
        from datetime import UTC, datetime, timedelta
        from email.utils import format_datetime

        # Naive datetime whose wall-clock value matches UTC-now + 45s, so
        # that treating it as UTC (as the code under test does) yields the
        # expected delay regardless of the host's local timezone.
        future_utc_naive = (datetime.now(UTC) + timedelta(seconds=45)).replace(
            tzinfo=None
        )
        header_value = format_datetime(future_utc_naive)  # renders "-0000"
        wait = hc._parse_retry_after(header_value)
        assert wait is not None
        # Allow small scheduling jitter between construction and parsing.
        assert 40 <= wait <= 46


# ---------------------------------------------------------------------------
# _RetryTransport — test double
# ---------------------------------------------------------------------------


class _ScriptedTransport(httpx.AsyncBaseTransport):
    """A fake inner transport that plays back a scripted sequence of
    responses/exceptions, one per call to `handle_async_request`."""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        item = self._script[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, httpx.Response)
        item.request = request
        return item

    async def aclose(self) -> None:
        pass


def _response(
    status_code: int, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status_code, headers=headers or {})


def _request(method: str = "GET") -> httpx.Request:
    return httpx.Request(method, "https://example.suse.de/resource")


@pytest.fixture
def mock_sleep(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)
    return sleep_mock


# ---------------------------------------------------------------------------
# Transport-level retry — 5xx
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetryOn5xx:
    async def test_5xx_retried_four_attempts_then_returned(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [_response(500) for _ in range(4)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 500
        assert inner.calls == 4
        assert mock_sleep.await_count == 3
        assert [c.args[0] for c in mock_sleep.await_args_list] == [1.0, 2.0, 4.0]

    async def test_5xx_succeeds_after_retry(self, mock_sleep: AsyncMock) -> None:
        script = [_response(500), _response(500), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 3
        assert mock_sleep.await_count == 2

    async def test_5xx_retried_for_post(self, mock_sleep: AsyncMock) -> None:
        """5xx retry applies to all methods, including non-idempotent ones."""
        script = [_response(503, {}), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("POST"))

        assert response.status_code == 200
        assert inner.calls == 2

    async def test_4xx_non_429_not_retried(self, mock_sleep: AsyncMock) -> None:
        script = [_response(404)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 404
        assert inner.calls == 1
        assert mock_sleep.await_count == 0

    async def test_discarded_response_is_closed_in_fixed_backoff_path(
        self, mock_sleep: AsyncMock
    ) -> None:
        """A bare 5xx with no `Retry-After` header takes the fixed-backoff
        path (not the guided path). The discarded response must still be
        closed before the retry, same as the guided path."""
        discarded = _response(500)
        script = [discarded, _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        await transport.handle_async_request(_request("GET"))

        assert discarded.is_closed


# ---------------------------------------------------------------------------
# Transport-level retry — connection/timeout/proxy/protocol errors
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetryOnConnectionErrors:
    async def test_connect_error_retried_for_idempotent_method(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [
            httpx.ConnectError("boom"),
            httpx.ConnectError("boom"),
            _response(200),
        ]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 3
        assert mock_sleep.await_count == 2

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "PUT", "DELETE"])
    async def test_connect_error_retried_for_every_idempotent_method(
        self, mock_sleep: AsyncMock, method: str
    ) -> None:
        """The full idempotent-method matrix: GET, HEAD, OPTIONS, PUT, and
        DELETE all retry on connection error without any opt-in, per the
        Method Safety contract in `docs/features/platform/networking.md`."""
        script = [httpx.ConnectError("boom"), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request(method))

        assert response.status_code == 200
        assert inner.calls == 2
        assert mock_sleep.await_count == 1

    @pytest.mark.parametrize("method", ["POST", "PATCH"])
    async def test_connect_error_not_retried_for_non_idempotent_method_by_default(
        self, mock_sleep: AsyncMock, method: str
    ) -> None:
        """Neither POST nor PATCH retries on connection error without the
        `retry_non_idempotent` opt-in — retrying risks duplicate writes."""
        script = [httpx.ConnectError("boom")]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(_request(method))

        assert inner.calls == 1
        assert mock_sleep.await_count == 0

    async def test_connect_error_exhausts_after_four_attempts(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [httpx.ConnectError("boom") for _ in range(4)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(_request("GET"))

        assert inner.calls == 4
        assert mock_sleep.await_count == 3

    async def test_read_timeout_not_retried_for_post_by_default(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [httpx.ReadTimeout("timed out")]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        with pytest.raises(httpx.ReadTimeout):
            await transport.handle_async_request(_request("POST"))

        assert inner.calls == 1
        assert mock_sleep.await_count == 0

    async def test_read_timeout_retried_for_post_when_opted_in(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [httpx.ReadTimeout("timed out"), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=True)

        response = await transport.handle_async_request(_request("POST"))

        assert response.status_code == 200
        assert inner.calls == 2

    async def test_proxy_error_retried_like_connection_error(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [httpx.ProxyError("tunnel failed"), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 2

    async def test_remote_protocol_error_retried_like_connection_error(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [httpx.RemoteProtocolError("bad response"), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 2


# ---------------------------------------------------------------------------
# Transport-level retry — Retry-After guided path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetryAfterGuidedPath:
    async def test_429_with_retry_after_under_boundary_retried_once(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [_response(429, {"Retry-After": "5"}), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 2
        mock_sleep.assert_awaited_once_with(5.0)

    async def test_503_with_retry_after_under_boundary_retried_once(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [_response(503, {"Retry-After": "10"}), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 2
        mock_sleep.assert_awaited_once_with(10.0)

    async def test_exactly_120_seconds_is_retried(self, mock_sleep: AsyncMock) -> None:
        script = [_response(429, {"Retry-After": "120"}), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 2
        mock_sleep.assert_awaited_once_with(120.0)

    async def test_over_120_seconds_not_retried(self, mock_sleep: AsyncMock) -> None:
        script = [_response(429, {"Retry-After": "121"})]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 429
        assert inner.calls == 1
        assert mock_sleep.await_count == 0

    async def test_503_over_120_seconds_not_retried_even_though_5xx(
        self, mock_sleep: AsyncMock
    ) -> None:
        """A Retry-After > 120s on a 503 overrides the generic 5xx
        fixed-backoff eligibility entirely — no retry at all."""
        script = [_response(503, {"Retry-After": "300"})]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 503
        assert inner.calls == 1
        assert mock_sleep.await_count == 0

    async def test_429_without_retry_after_not_retried(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [_response(429)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 429
        assert inner.calls == 1
        assert mock_sleep.await_count == 0

    async def test_503_without_retry_after_uses_fixed_backoff(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [_response(503), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 2
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_malformed_retry_after_falls_back_to_fixed_backoff(
        self, mock_sleep: AsyncMock
    ) -> None:
        script = [_response(503, {"Retry-After": "garbage"}), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_guided_retry_is_final_attempt_even_if_it_fails_again(
        self, mock_sleep: AsyncMock
    ) -> None:
        """Path exclusivity: after the single guided retry, no further
        fixed-backoff retries are attempted even if the second response
        is again a 5xx/429."""
        script = [_response(429, {"Retry-After": "1"}), _response(500)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 500
        assert inner.calls == 2
        assert mock_sleep.await_count == 1

    async def test_discarded_response_is_closed_before_retry(
        self, mock_sleep: AsyncMock
    ) -> None:
        discarded = _response(503, {"Retry-After": "1"})
        script = [discarded, _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        await transport.handle_async_request(_request("GET"))

        assert discarded.is_closed

    async def test_guided_retry_propagates_exception_as_final_attempt(
        self, mock_sleep: AsyncMock
    ) -> None:
        """If the single guided retry itself raises a connection error,
        the exception propagates — this is the last attempt, there is no
        further catch/retry around it."""
        script = [
            _response(429, {"Retry-After": "1"}),
            httpx.ConnectError("boom"),
        ]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(_request("GET"))

        assert inner.calls == 2
        assert mock_sleep.await_count == 1

    async def test_502_with_retry_after_ignores_guided_path(
        self, mock_sleep: AsyncMock
    ) -> None:
        """The guided Retry-After path is restricted to 429/503 per the
        dispatch rule. A 502 carrying Retry-After does not qualify — it
        falls through to the generic 5xx fixed-backoff rule instead."""
        script = [_response(502, {"Retry-After": "5"}), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        response = await transport.handle_async_request(_request("GET"))

        assert response.status_code == 200
        assert inner.calls == 2
        mock_sleep.assert_awaited_once_with(1.0)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetrySleepCancellation:
    async def test_sleep_uses_asyncio_sleep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retry sleeps must go through asyncio.sleep() so they are
        cancellable on SoftTimeLimitExceeded / task revocation."""
        calls: list[float] = []

        async def _tracking_sleep(seconds: float) -> None:
            calls.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", _tracking_sleep)
        script = [_response(500), _response(200)]
        inner = _ScriptedTransport(script)
        transport = hc._RetryTransport(inner, retry_non_idempotent=False)

        await transport.handle_async_request(_request("GET"))

        assert calls == [1.0]


# ---------------------------------------------------------------------------
# is_infrastructure_failure()
# ---------------------------------------------------------------------------


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = _request()
    response = _response(status_code)
    response.request = request
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.mark.unit
class TestIsInfrastructureFailure:
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("x"),
            httpx.ReadError("x"),
            httpx.WriteError("x"),
            httpx.CloseError("x"),
            httpx.ConnectTimeout("x"),
            httpx.ReadTimeout("x"),
            httpx.WriteTimeout("x"),
            httpx.PoolTimeout("x"),
            httpx.ProxyError("x"),
            httpx.RemoteProtocolError("x"),
        ],
    )
    def test_network_and_timeout_errors_are_infra_failures(
        self, exc: Exception
    ) -> None:
        assert hc.is_infrastructure_failure(exc) is True

    def test_5xx_status_error_is_infra_failure(self) -> None:
        assert hc.is_infrastructure_failure(_status_error(500)) is True
        assert hc.is_infrastructure_failure(_status_error(503)) is True

    def test_4xx_status_error_is_not_infra_failure(self) -> None:
        assert hc.is_infrastructure_failure(_status_error(404)) is False
        assert hc.is_infrastructure_failure(_status_error(429)) is False

    def test_decoding_error_is_not_infra_failure(self) -> None:
        assert hc.is_infrastructure_failure(httpx.DecodingError("x")) is False

    def test_too_many_redirects_is_not_infra_failure(self) -> None:
        assert hc.is_infrastructure_failure(httpx.TooManyRedirects("x")) is False

    def test_local_protocol_error_is_not_infra_failure(self) -> None:
        assert hc.is_infrastructure_failure(httpx.LocalProtocolError("x")) is False

    def test_unsupported_protocol_is_not_infra_failure(self) -> None:
        assert hc.is_infrastructure_failure(httpx.UnsupportedProtocol("x")) is False

    def test_generic_exception_is_not_infra_failure(self) -> None:
        assert hc.is_infrastructure_failure(ValueError("bad data")) is False


# ---------------------------------------------------------------------------
# is_retryable_condition()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsRetryableCondition:
    def test_network_errors_are_retryable(self) -> None:
        assert hc.is_retryable_condition(httpx.ConnectError("x")) is True

    def test_5xx_is_retryable(self) -> None:
        assert hc.is_retryable_condition(_status_error(500)) is True

    def test_429_is_retryable(self) -> None:
        """Unlike is_infrastructure_failure(), 429 IS retryable here — the
        server is reachable but rate-limiting, which clears after backoff."""
        assert hc.is_retryable_condition(_status_error(429)) is True

    def test_403_is_not_retryable(self) -> None:
        assert hc.is_retryable_condition(_status_error(403)) is False

    def test_404_is_not_retryable(self) -> None:
        assert hc.is_retryable_condition(_status_error(404)) is False

    def test_generic_exception_is_not_retryable(self) -> None:
        assert hc.is_retryable_condition(ValueError("bad data")) is False

    def test_superset_relationship_holds(self) -> None:
        """Every condition classified as an infrastructure failure must
        also be classified as retryable (superset invariant)."""
        candidates: list[Exception] = [
            httpx.ConnectError("x"),
            httpx.ReadTimeout("x"),
            httpx.ProxyError("x"),
            httpx.RemoteProtocolError("x"),
            _status_error(500),
            _status_error(503),
        ]
        for exc in candidates:
            if hc.is_infrastructure_failure(exc):
                assert hc.is_retryable_condition(exc) is True
