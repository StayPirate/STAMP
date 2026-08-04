"""Image smoke assertions for the shared networking/TLS client (P1-02).

Verifies container-observable outcomes that only manifest inside the
built image: the SUSE CA certificate file is present at the
repository-relative path `build_tls_context()` reads, the SUSE CA is
also present in the container's system-wide trust store (layer 1 — see
docs/features/platform/networking.md, Trust Store Layering), TLS
context construction succeeds without a runtime error, and
`create_http_client()` produces the specified defaults. These
complement the in-process unit tests in
`backend/tests/test_services/test_http_client.py`, which mock all I/O
and never touch a real filesystem/container layout.

See docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing, Growth Rule).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

# Inline Python snippet run inside the `api` container to confirm the
# SUSE CA is present in the *system-wide* trust store (layer 1), i.e.
# that `update-ca-certificates` ran successfully during the image build.
# This does NOT verify build_tls_context() (layer 2) in isolation: since
# build_tls_context()'s base (ssl.create_default_context()) already reads
# the system trust store, layer 2's own explicit CA load is not
# separately observable from inside a container where layer 1 is present
# — asserting the SUSE CA appears in build_tls_context()'s output would
# pass even if SUSE_CA_CERT_PATH were missing entirely (layer 1 alone
# would already satisfy it). This test therefore targets
# ssl.create_default_context() directly, which guards specifically
# against `update-ca-certificates` silently being dropped from the
# Dockerfile.
_SYSTEM_TRUST_STORE_CHECK_SCRIPT = """
import ssl

context = ssl.create_default_context()
subjects = [
    dict(rdn[0] for rdn in cert.get("subject", ()))
    for cert in context.get_ca_certs()
]
names = {s.get("commonName") for s in subjects}
assert "SUSE Trust Root" in names, sorted(n for n in names if n)
print("SYSTEM-TRUST-STORE-OK")
"""

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
        assert "(image_smoke_test; +https://github.com/StayPirate/sentinel)" in ua
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
def test_suse_ca_present_in_system_trust_store(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """The SUSE CA is present in the container's system-wide trust store
    (Trust Store Layering, layer 1) — guards against the
    `update-ca-certificates` Dockerfile step being silently dropped.

    Layer 2 (`build_tls_context()`'s own explicit CA load) is not
    separately verifiable from inside a container where layer 1 is
    present — see the module-level comment on
    `_SYSTEM_TRUST_STORE_CHECK_SCRIPT`.
    """
    result = compose_exec("api", "python", "-c", _SYSTEM_TRUST_STORE_CHECK_SCRIPT)
    assert result.returncode == 0, (
        f"system trust store check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "SYSTEM-TRUST-STORE-OK" in result.stdout


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
