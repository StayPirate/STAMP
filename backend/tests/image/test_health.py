"""Black-box image smoke assertions for the public health/readiness
endpoints.

See docs/features/platform/health-endpoints.md for the contract and
docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing, Growth Rule) for why this file exists: the api container's
own healthcheck (docker-compose.smoke.yml) now probes `/health`
instead of `/openapi.json`, so container-observable behavior for these
endpoints must be covered here.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.image
def test_health_endpoint_returns_exact_ok_body(http_client: httpx.Client) -> None:
    response = http_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.image
def test_ready_endpoint_reports_both_dependencies_healthy(
    http_client: httpx.Client,
) -> None:
    """postgres and redis are both healthy dependencies in the smoke
    stack (the api service's `depends_on: condition: service_healthy`
    already blocks readiness on them) — so `/ready` must report a fully
    healthy response."""
    response = http_client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"postgresql": "ok", "redis": "ok"},
    }
