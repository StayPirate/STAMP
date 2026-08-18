"""Image smoke assertions for structured logging and request correlation.

See docs/features/platform/logging.md and docs/api-spec.md (Request
Tracing) for the specifications exercised here. These tests verify
container-observable behavior: X-Request-ID handling over real HTTP
against the running image, and fail-fast startup validation for
invalid LOG_LEVEL/LOG_FORMAT values in a fresh process inside the
running container (see docs/features/platform/testing-strategy.md,
Growth Rule).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable

import httpx
import pytest

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@pytest.mark.image
class TestRequestIdHeader:
    """X-Request-ID adoption/generation over real HTTP."""

    def test_generates_uuid_when_absent(self, http_client: httpx.Client) -> None:
        response = http_client.get("/openapi.json")
        assert response.status_code == 200
        assert _UUID_RE.match(response.headers["x-request-id"])

    def test_adopts_valid_client_supplied_value(
        self, http_client: httpx.Client
    ) -> None:
        response = http_client.get(
            "/openapi.json", headers={"X-Request-ID": "smoke-test-id-123"}
        )
        assert response.headers["x-request-id"] == "smoke-test-id-123"

    def test_rejects_invalid_value_falls_back_to_uuid(
        self, http_client: httpx.Client
    ) -> None:
        response = http_client.get(
            "/openapi.json", headers={"X-Request-ID": "invalid header!"}
        )
        assert _UUID_RE.match(response.headers["x-request-id"])


@pytest.mark.image
class TestLoggingStartupValidation:
    """Invalid LOG_LEVEL/LOG_FORMAT fail fast in a fresh process."""

    def test_invalid_log_level_fails_fast(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec(
            "api",
            "python",
            "-c",
            "import app.main",
            env={"LOG_LEVEL": "BOGUS"},
        )
        assert result.returncode != 0, (
            f"expected non-zero exit for invalid LOG_LEVEL "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "LOG_LEVEL" in result.stderr

    def test_invalid_log_format_fails_fast(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        result = compose_exec(
            "api",
            "python",
            "-c",
            "import app.main",
            env={"LOG_FORMAT": "xml"},
        )
        assert result.returncode != 0, (
            f"expected non-zero exit for invalid LOG_FORMAT "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "LOG_FORMAT" in result.stderr

    def test_valid_log_level_still_starts(
        self,
        compose_exec: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        """Control case: confirms the failures above are due to the
        invalid value, not to the exec/env-override mechanism itself."""
        result = compose_exec(
            "api",
            "python",
            "-c",
            "import app.main; print('import-ok')",
            env={"LOG_LEVEL": "DEBUG"},
        )
        assert result.returncode == 0, (
            f"expected exit 0 for valid LOG_LEVEL override "
            f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
        )
        assert "import-ok" in result.stdout
