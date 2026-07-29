"""Fixtures for the black-box image smoke test suite.

This suite exercises the built Docker image as a running container, over
HTTP and via ``compose exec`` — it is NOT an in-process test suite. It
therefore deliberately does NOT reuse the ``db_session`` / ``client``
fixtures from ``backend/tests/conftest.py`` (those wire an ASGI
transport and a test database into the in-process app). Here the app
runs inside a container started by ``scripts/image-smoke.sh``.

See docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing) and docs/drafts/image-testing-setup.md.
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
    the COMPOSE_CMD env var exported by scripts/image-smoke.sh; it
    defaults to ``docker compose`` for direct/manual use.

    Returns a callable ``(service, *args) -> subprocess.CompletedProcess``.
    """
    compose_cmd = shlex.split(os.environ.get("COMPOSE_CMD", "docker compose"))
    compose_files = os.environ.get("COMPOSE_FILES", "docker-compose.smoke.yml").split(
        ":"
    )
    file_args: list[str] = []
    for compose_file in compose_files:
        file_args.extend(["-f", compose_file])

    def _exec(service: str, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = [*compose_cmd, *file_args, "exec", "-T", service, *args]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

    return _exec
