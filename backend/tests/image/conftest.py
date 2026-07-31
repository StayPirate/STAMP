"""Fixtures for the black-box image smoke test suite.

This suite exercises the built Docker image as a running container, over
HTTP and via ``compose exec`` — it is NOT an in-process test suite. It
therefore deliberately does NOT reuse the ``db_session`` / ``client``
fixtures from ``backend/tests/conftest.py`` (those wire an ASGI
transport and a test database into the in-process app). Here the app
runs inside a container started by ``scripts/image-smoke.sh``.

See docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Iterator

import httpx
import pytest


@pytest.fixture(scope="session")
def base_url() -> str:
    """Base URL of the running ``api`` container.

    Defaults to http://localhost:18000 (the host port the ``api`` service
    is published on by docker-compose.smoke.yml — deliberately not 8000,
    to avoid clashing with a local uvicorn dev server). The runner script
    (scripts/image-smoke.sh) sets IMAGE_SMOKE_BASE_URL explicitly.
    """
    return os.environ.get("IMAGE_SMOKE_BASE_URL", "http://localhost:18000")


@pytest.fixture(scope="session")
def http_client(base_url: str) -> Iterator[httpx.Client]:
    """Synchronous HTTP client pointed at the running container.

    Synchronous by design: these tests talk to a real container over the
    network, so there is no benefit to the async ASGI transport used by
    the in-process suite.
    """
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        yield client


@pytest.fixture(scope="session")
def compose_exec():
    """Run a command inside a running compose service and return output.

    Used by later-phase assertions (e.g. running a ``sentinel`` CLI
    command inside the container). The compose invocation is read from
    env vars exported by scripts/image-smoke.sh:

    - ``COMPOSE_CMD`` — the compose binary (defaults to ``docker compose``
      for direct/manual use).
    - ``COMPOSE_FILES`` — colon-separated compose file paths (defaults to
      ``docker-compose.smoke.yml``).
    - ``COMPOSE_PROJECT`` — the compose project name (defaults to
      ``sentinel-smoke``). This MUST match the project name the runner
      brought the stack up with (``scripts/image-smoke.sh`` uses
      ``-p sentinel-smoke``); otherwise ``compose exec`` would target the
      default project and fail to find the running containers.

    Returns a callable ``(service, *args, env=None) -> CompletedProcess``.
    ``env``, when given, overrides/adds environment variables for that
    single invocation only (via ``compose exec -e KEY=VAL``) — it does
    not persist across calls or affect the long-running service process.
    Used to exercise startup validation (e.g. an invalid ``LOG_LEVEL``)
    in a fresh process without restarting the service.
    """
    compose_cmd = shlex.split(os.environ.get("COMPOSE_CMD", "docker compose"))
    compose_files = os.environ.get("COMPOSE_FILES", "docker-compose.smoke.yml").split(
        ":"
    )
    project = os.environ.get("COMPOSE_PROJECT", "sentinel-smoke")
    file_args: list[str] = []
    for compose_file in compose_files:
        file_args.extend(["-f", compose_file])

    def _exec(
        service: str, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env_args: list[str] = []
        for key, value in (env or {}).items():
            env_args.extend(["-e", f"{key}={value}"])
        cmd = [
            *compose_cmd,
            "-p",
            project,
            *file_args,
            "exec",
            "-T",
            *env_args,
            service,
            *args,
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

    return _exec
