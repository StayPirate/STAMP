"""Response schemas for the public health/readiness endpoints.

See `docs/features/platform/health-endpoints.md` for the authoritative
response body contracts. These schemas intentionally do NOT use the
standard `{"data": ...}` API envelope — see `docs/api-spec.md` (Response
Conventions) and `docs/conventions.md` (FastAPI Conventions) for the
envelope rule and its exemption for `/health` and `/ready`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.core.enums import HealthCheckStatus


class LivenessResponse(BaseModel):
    """Response body for `GET /health`.

    Always `{"status": "ok"}` — this endpoint has no failure path (see
    `health-endpoints.md`, Liveness — GET /health).
    """

    status: Literal["ok"] = "ok"


class ReadinessChecks(BaseModel):
    """Per-dependency readiness check results."""

    postgresql: HealthCheckStatus
    redis: HealthCheckStatus


class ReadinessResponse(BaseModel):
    """Response body for `GET /ready`.

    `status` is `"ok"` only when every check in `checks` is
    `HealthCheckStatus.OK`; otherwise `"unavailable"` (see
    `health-endpoints.md`, Readiness — GET /ready).
    """

    status: Literal["ok", "unavailable"]
    checks: ReadinessChecks
