"""Smoke tests for the test infrastructure itself.

These tests verify that the database fixture and test client work
correctly. They serve as the green baseline before any feature code
is implemented.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
async def test_db_session_executes_query(db_session: AsyncSession) -> None:
    """The db_session fixture should provide a working DB connection."""
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest.mark.e2e
async def test_client_connects_to_app(client: AsyncClient) -> None:
    """The test client should be able to make requests to the app."""
    # Any request works — we're testing the client fixture, not an endpoint.
    # A 404 from an unknown path is a valid response (proves the app is running).
    response = await client.get("/nonexistent-path-for-smoke-test")
    assert response.status_code in (404, 405)
