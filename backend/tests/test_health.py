"""Test the health check endpoint.

Marked xfail(strict=True) until /health is implemented per
docs/features/platform/health-endpoints.md. When the endpoint is
implemented, this test will pass — strict mode will then cause CI
to fail, signaling that the xfail marker should be removed.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.e2e
@pytest.mark.xfail(strict=True, reason="Health endpoint not yet implemented")
async def test_health_check_returns_ok(client: AsyncClient) -> None:
    """Health check endpoint should return status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
