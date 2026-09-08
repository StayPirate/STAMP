"""OCI artifact probes for the packaged networking trust stores.

The image gate verifies the certificate file, its system trust-store
installation, and real TLS-context construction. HTTP client defaults remain
in ``tests/test_services/test_http_client.py``.

See ``docs/features/platform/testing-strategy.md`` (Artifact-Risk Rule).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

_SYSTEM_TRUST_STORE_CHECK_SCRIPT = """
import ssl

context = ssl.create_default_context()
subjects = [
    dict(rdn[0] for rdn in cert.get("subject", ()))
    for cert in context.get_ca_certs()
]
names = {subject.get("commonName") for subject in subjects}
assert "SUSE Trust Root" in names, sorted(name for name in names if name)
print("SYSTEM-TRUST-STORE-OK")
"""

_TLS_CONTEXT_CHECK_SCRIPT = """
from app.services.http_client import build_tls_context

context = build_tls_context()
assert context.verify_mode.name == "CERT_REQUIRED"
print("TLS-CONTEXT-OK")
"""


@pytest.mark.image
def test_suse_ca_certificate_present_at_expected_path(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "test", "-f", "certs/SUSE_Trust_Root.crt")
    assert result.returncode == 0, (
        "CA certificate missing at repository-relative path "
        f"(rc={result.returncode}): stderr={result.stderr!r}"
    )


@pytest.mark.image
def test_suse_ca_present_in_system_trust_store(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _SYSTEM_TRUST_STORE_CHECK_SCRIPT)
    assert result.returncode == 0, (
        f"system trust store check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "SYSTEM-TRUST-STORE-OK" in result.stdout


@pytest.mark.image
def test_build_tls_context_succeeds(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _TLS_CONTEXT_CHECK_SCRIPT)
    assert result.returncode == 0, (
        f"TLS context check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "TLS-CONTEXT-OK" in result.stdout
