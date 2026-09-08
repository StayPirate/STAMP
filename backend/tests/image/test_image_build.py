"""OCI artifact probes for the packaged Sentinel application.

These checks cover properties that focused tests against the local source tree
cannot establish: the packaged FastAPI application serves OpenAPI, application
imports work inside the candidate image, and test code is physically absent.

See ``docs/features/platform/testing-strategy.md`` (Artifact-Risk Rule).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import httpx
import pytest


@pytest.mark.image
def test_api_container_serves_openapi(http_client: httpx.Client) -> None:
    """The packaged ASGI application starts and serves its OpenAPI schema."""
    response = http_client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Sentinel"


@pytest.mark.image
def test_runtime_imports_app_and_excludes_test_code(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Application code is importable and ``/app/tests`` is not shipped."""
    result = compose_exec(
        "api",
        "python",
        "-c",
        "from pathlib import Path\n"
        "import app\n"
        "assert not Path('/app/tests').exists(), '/app/tests is present'\n"
        "print('RUNTIME-CONTENTS-OK')",
    )
    assert result.returncode == 0, (
        f"runtime contents check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "RUNTIME-CONTENTS-OK" in result.stdout
