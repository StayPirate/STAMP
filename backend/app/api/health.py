"""Public liveness and readiness probe endpoints.

Registered at the application root, outside `/api/v1/` — see
`docs/features/platform/health-endpoints.md` for the full specification
(design rationale, response body contracts, and access control).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.core.enums import HealthCheckStatus
from app.database import async_session_factory
from app.schemas.health import LivenessResponse, ReadinessChecks, ReadinessResponse
from app.services.health_service import check_readiness

router = APIRouter(tags=["health"])


def get_readiness_session_factory() -> async_sessionmaker[AsyncSession]:
    """Provide the session factory used by the readiness PostgreSQL check.

    Performs no I/O — returns the application's shared session factory.
    Overridable via `app.dependency_overrides` so tests can point the
    readiness check at a different factory (e.g. one that raises on use,
    to simulate an outage) independently of the `get_db` dependency used
    by domain endpoints.
    """
    return async_session_factory


def get_readiness_redis_urls() -> list[str]:
    """Provide the Redis connection URLs used by the readiness check.

    Performs no I/O — returns the configured `REDIS_URL` and
    `CELERY_BROKER_URL`. Overridable via `app.dependency_overrides` so
    tests can point the readiness check at the test-harness Redis
    instance(s), per `docs/features/platform/testing-strategy.md`
    (Redis Strategy).
    """
    return [settings.redis_url, settings.celery_broker_url]


@router.get(
    "/health",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Confirms the FastAPI process is running and able to serve HTTP "
        "requests. Performs no dependency I/O. Always returns 200."
    ),
)
async def liveness_probe() -> LivenessResponse:
    """Liveness probe — see health-endpoints.md (Liveness — GET /health)."""
    return LivenessResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Checks PostgreSQL and every unique configured Redis instance. "
        "Returns 200 when all checks pass, 503 otherwise. Never returns "
        "a 4xx or 500 response."
    ),
    responses={
        503: {
            "model": ReadinessResponse,
            "description": "One or more dependencies are unavailable.",
        },
    },
)
async def readiness_probe(
    response: Response,
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_readiness_session_factory)
    ],
    redis_urls: Annotated[list[str], Depends(get_readiness_redis_urls)],
) -> ReadinessResponse:
    """Readiness probe — see health-endpoints.md (Readiness — GET /ready)."""
    result = await check_readiness(session_factory, redis_urls)
    is_ready = (
        result.postgresql == HealthCheckStatus.OK
        and result.redis == HealthCheckStatus.OK
    )
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ok" if is_ready else "unavailable",
        checks=ReadinessChecks(postgresql=result.postgresql, redis=result.redis),
    )
