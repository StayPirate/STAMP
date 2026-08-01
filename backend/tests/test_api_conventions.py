"""Structural tests over FastAPI route-level API conventions.

Verifies invariants declared in `docs/api-spec.md` across every
registered route. See `docs/features/platform/testing-strategy.md`
(Structural Tests) for scope and governing principle.

This module intentionally does not defer to "until the first real
endpoint exists": every check below iterates `app.routes`, so it passes
vacuously today (zero `APIRoute` instances registered) and starts
enforcing automatically the moment the first endpoint is added — no
further action is required from whoever adds it.

Out of scope: the RBAC Endpoint Permission Map cross-reference (see
`docs/features/identity/rbac.md`) is not verified here — it would
require parsing a Markdown table, which the governing principle
forbids. That cross-reference remains with `@docs-reviewer` /
`@api-parity-reviewer`.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

from app.main import app

# Endpoints intentionally outside the `/api/v1/` prefix — public,
# non-versioned operational endpoints. See
# `docs/features/platform/health-endpoints.md`.
_PREFIX_EXEMPT_PATHS = {"/health", "/ready"}

# The only HTTP methods used across the documented API surface — see
# `docs/api-spec.md` (Mutation Patterns: PATCH/POST) and the CORS
# `allow_methods` configuration in `app/main.py`. A route using any
# other method (e.g. PUT, HEAD) would be unreachable by the CORS
# configuration the application itself declares.
_ALLOWED_METHODS = {"GET", "POST", "PATCH", "DELETE"}


def _api_routes() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


@pytest.mark.unit
class TestRoutePrefixConvention:
    """Every route is prefixed with `/api/v1/`, except the documented
    health-endpoint exemption.

    See `docs/api-spec.md` (Base URL): "All API endpoints are prefixed
    with `/api/v1/`."
    """

    def test_every_route_has_api_v1_prefix_or_is_exempt(self) -> None:
        for route in _api_routes():
            if route.path in _PREFIX_EXEMPT_PATHS:
                continue
            assert route.path.startswith("/api/v1/"), (
                f"Route '{route.path}' is missing the required '/api/v1/' prefix"
            )


@pytest.mark.unit
class TestRouteHttpMethods:
    """No route uses an HTTP method outside the documented set.

    See `docs/api-spec.md` (Mutation Patterns): only PATCH and POST are
    used for modification, GET for reads, DELETE for removal. `PUT` and
    `HEAD` are never used.
    """

    def test_every_route_uses_only_allowed_methods(self) -> None:
        for route in _api_routes():
            methods = route.methods or set()
            disallowed = methods - _ALLOWED_METHODS
            assert not disallowed, (
                f"Route '{route.path}' uses disallowed HTTP method(s) "
                f"{disallowed} (allowed: {_ALLOWED_METHODS})"
            )


@pytest.mark.unit
class TestRouteDocumentation:
    """Every route has OpenAPI documentation.

    See `docs/conventions.md` (FastAPI Conventions): "All endpoints
    must have OpenAPI documentation (summary, description)." FastAPI
    derives `description` from the endpoint's docstring when not set
    explicitly, so a docstring alone satisfies this.
    """

    def test_every_route_has_summary_or_description(self) -> None:
        for route in _api_routes():
            assert route.summary or route.description, (
                f"Route '{route.path}' has no OpenAPI summary or "
                "description (add a docstring or explicit summary/"
                "description)"
            )


@pytest.mark.unit
class TestAuditLogEndpointNaming:
    """Every audit trail retrieval endpoint uses the `/audit-log`
    suffix.

    See `docs/api-spec.md` (Audit Trail Endpoint Naming): "Every audit
    trail retrieval endpoint MUST use the `/audit-log` suffix."
    """

    def test_audit_related_routes_end_with_audit_log_suffix(self) -> None:
        for route in _api_routes():
            if "audit" in route.path.lower():
                assert route.path.endswith("/audit-log"), (
                    f"Route '{route.path}' references audit trails but "
                    "does not end with the required '/audit-log' suffix"
                )
