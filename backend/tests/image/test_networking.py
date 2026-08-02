"""Image smoke assertions for the shared networking/TLS client (P1-02).

Verifies container-observable outcomes that only manifest inside the
built image: the SUSE CA certificate file is present at the
repository-relative path `build_tls_context()` reads, TLS context
construction succeeds without a runtime error, and `create_http_client()`
produces the specified defaults. These complement the in-process unit
tests in `backend/tests/test_services/test_http_client.py`, which mock
all I/O and never touch a real filesystem/container layout.

See docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing, Growth Rule).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

# Inline Python snippet run inside the `api` container to exercise
# build_tls_context() and create_http_client() against the real image
# filesystem and installed CA bundle. Kept as a single string (not a
# helper script file) because it is short, self-contained, and only ever
# executed via `compose exec`.
_NETWORKING_CHECK_SCRIPT = """
import asyncio

from app.services.http_client import build_tls_context, create_http_client


async def main() -> None:
    context = build_tls_context()
    assert context.verify_mode.name == "CERT_REQUIRED"

    client = create_http_client("image_smoke_test")
    try:
        ua = client.headers["user-agent"]
        assert ua.startswith("Sentinel/"), ua
        assert "(image_smoke_test; +https://github.com/SUSE/sentinel)" in ua
        assert client.follow_redirects is False
        assert client.timeout.connect == 10.0
        assert client.timeout.read == 30.0
    finally:
        await client.aclose()

    print("NETWORKING-OK")


asyncio.run(main())
"""


@pytest.mark.image
def test_suse_ca_certificate_present_at_expected_path(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """The SUSE Trust Root CA is present at the repository-relative path
    `build_tls_context()` reads by default (`certs/SUSE_Trust_Root.crt`,
    relative to the container's `/app` working directory) — not only
    installed into the system-wide CA bundle.
    """
    result = compose_exec("api", "test", "-f", "certs/SUSE_Trust_Root.crt")
    assert result.returncode == 0, (
        f"CA certificate missing at repository-relative path "
        f"(rc={result.returncode}): stderr={result.stderr!r}"
    )


@pytest.mark.image
def test_build_tls_context_and_create_http_client_succeed(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`build_tls_context()` and `create_http_client()` construct
    successfully inside the running container, and the client carries
    the specified defaults (User-Agent, timeouts, redirect policy)."""
    result = compose_exec("api", "python", "-c", _NETWORKING_CHECK_SCRIPT)
    assert result.returncode == 0, (
        f"networking check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "NETWORKING-OK" in result.stdout
