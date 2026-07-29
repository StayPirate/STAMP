"""Minimal black-box smoke assertion delivered by the prep effort.

Verifies that the built image can actually run: the ``api`` container
starts and stays up (does not crash) within the bounded wait window
enforced by ``scripts/image-smoke.sh`` (``compose up --wait``). By the
time this test runs, the compose ``--wait`` has already blocked until the
``api`` healthcheck passed, so a reachable, non-erroring app confirms the
image booted correctly.

Further assertions (health endpoints, worker startup, migrations, CLI,
git worker) are added by the change that introduces the corresponding
behavior — see docs/features/platform/testing-strategy.md (Growth Rule).
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

import httpx
import pytest


@pytest.mark.image
def test_api_container_serves_openapi(http_client: httpx.Client) -> None:
    """The api container booted and serves its OpenAPI schema.

    ``/openapi.json`` is always present in a FastAPI app; a 200 here
    proves the ASGI application started successfully inside the image
    (config validation passed, dependencies importable, uvicorn bound).
    """
    response = http_client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Sentinel"


@pytest.mark.image
def test_api_container_stays_up(http_client: httpx.Client) -> None:
    """The api container does not crash shortly after startup.

    A second successful request after a short delay guards against an
    image that boots and then immediately exits (e.g. a background
    startup task that raises after the first response).
    """
    first = http_client.get("/openapi.json")
    assert first.status_code == 200

    time.sleep(3)

    second = http_client.get("/openapi.json")
    assert second.status_code == 200


@pytest.mark.image
def test_compose_exec_runs_in_api_container(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """``compose exec`` reaches the running ``api`` container.

    Also exercises the ``compose_exec`` fixture end-to-end, which requires
    the compose project name to match the one the runner brought the stack
    up with (``sentinel-smoke``). If the project name were wrong, compose
    would target the default project and fail to find the container.

    The command imports the ``app`` package inside the container, which
    doubles as a smoke check that the application code is importable in the
    built image (not just reachable over HTTP).
    """
    result = compose_exec("api", "python", "-c", "import app; print('exec-ok')")
    assert result.returncode == 0, (
        f"compose exec failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "exec-ok" in result.stdout
