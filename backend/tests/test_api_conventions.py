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

from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.main import app

# Endpoints intentionally outside the `/api/v1/` prefix — public,
# non-versioned operational endpoints. See
# `docs/features/platform/health-endpoints.md`. Their responses are
# also exempt from the `{"data": ...}` envelope (e.g. `/health` returns
# `{"status": "ok"}` directly) — see docs/api-spec.md Response Format,
# which scopes the envelope to API endpoints.
_PREFIX_EXEMPT_PATHS = {"/health", "/ready"}

# The only HTTP methods used across the documented API surface — see
# `docs/api-spec.md` (Mutation Patterns: PATCH/POST) and the CORS
# `allow_methods` configuration in `app/main.py`. A route using any
# other method (e.g. PUT, HEAD) would be unreachable by the CORS
# configuration the application itself declares.
_ALLOWED_METHODS = {"GET", "POST", "PATCH", "DELETE"}

# Routes with this status code return no body — see docs/api-spec.md
# Response Format. They are exempt from both the response_model
# presence and the envelope-format checks below.
_NO_BODY_STATUS_CODE = 204


def _api_routes() -> list[APIRoute]:
    return [route for route in app.routes if isinstance(route, APIRoute)]


def _resolve_schema_ref(
    openapi_schema: dict[str, Any], node: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a `$ref` pointer (e.g. `#/components/schemas/TicketDetail`)
    to the schema object it points to. Returns `node` unchanged if it is
    not a `$ref`.
    """
    ref = node.get("$ref")
    if not ref:
        return node
    target: Any = openapi_schema
    for part in ref.removeprefix("#/").split("/"):
        target = target[part]
    return target  # type: ignore[no-any-return]


def _response_schema(
    openapi_schema: dict[str, Any], route: APIRoute
) -> dict[str, Any] | None:
    """The resolved JSON schema for `route`'s primary success response
    (`route.status_code`, default 200), or `None` if the OpenAPI
    document does not describe a JSON body for it (e.g. no `response_model`
    was declared and FastAPI could not infer one).
    """
    method = next(iter(route.methods or ()), "").lower()
    operation = openapi_schema.get("paths", {}).get(route.path, {}).get(method, {})
    status_code = str(route.status_code or 200)
    response = operation.get("responses", {}).get(status_code, {})
    schema = response.get("content", {}).get("application/json", {}).get("schema")
    if schema is None:
        return None
    return _resolve_schema_ref(openapi_schema, schema)


@pytest.mark.unit
class TestRoutePrefixConvention:
    """Every route is prefixed with `/api/v1/`, except the documented
    health-endpoint exemption.

    See `docs/api-spec.md` (Base URL): "All API endpoints are prefixed
    with `/api/v1/`."
    """

    def test_every_route_has_api_v1_prefix_or_is_exempt(self) -> None:
        violations = [
            f"Route '{route.path}' is missing the required '/api/v1/' prefix"
            for route in _api_routes()
            if route.path not in _PREFIX_EXEMPT_PATHS
            and not route.path.startswith("/api/v1/")
        ]
        assert not violations, "\n".join(violations)


@pytest.mark.unit
class TestRouteHttpMethods:
    """No route uses an HTTP method outside the documented set.

    See `docs/api-spec.md` (Mutation Patterns): only PATCH and POST are
    used for modification, GET for reads, DELETE for removal. `PUT` and
    `HEAD` are never used.
    """

    def test_every_route_uses_only_allowed_methods(self) -> None:
        violations: list[str] = []
        for route in _api_routes():
            methods = route.methods or set()
            disallowed = methods - _ALLOWED_METHODS
            if disallowed:
                violations.append(
                    f"Route '{route.path}' uses disallowed HTTP method(s) "
                    f"{disallowed} (allowed: {_ALLOWED_METHODS})"
                )
        assert not violations, "\n".join(violations)


@pytest.mark.unit
class TestRouteDocumentation:
    """Every route has OpenAPI documentation.

    See `docs/conventions.md` (FastAPI Conventions): "All endpoints
    must have OpenAPI documentation (summary, description)." FastAPI
    derives `description` from the endpoint's docstring when not set
    explicitly, so a docstring alone satisfies this.
    """

    def test_every_route_has_summary_or_description(self) -> None:
        violations = [
            f"Route '{route.path}' has no OpenAPI summary or description "
            "(add a docstring or explicit summary/description)"
            for route in _api_routes()
            if not (route.summary or route.description)
        ]
        assert not violations, "\n".join(violations)


@pytest.mark.unit
class TestAuditLogEndpointNaming:
    """Every audit trail retrieval endpoint uses the `/audit-log`
    suffix.

    See `docs/api-spec.md` (Audit Trail Endpoint Naming): "Every audit
    trail retrieval endpoint MUST use the `/audit-log` suffix."
    """

    def test_audit_related_routes_end_with_audit_log_suffix(self) -> None:
        violations = [
            f"Route '{route.path}' references audit trails but does not "
            "end with the required '/audit-log' suffix"
            for route in _api_routes()
            if "audit" in route.path.lower() and not route.path.endswith("/audit-log")
        ]
        assert not violations, "\n".join(violations)


@pytest.mark.unit
class TestResponseModelPresence:
    """Every route that returns a body declares a `response_model`.

    See `docs/conventions.md` (FastAPI Conventions): "Use appropriate
    HTTP status codes and response models." A route with status code
    204 (No Content) is exempt — it has no body to model.
    """

    def test_every_body_returning_route_has_a_response_model(self) -> None:
        violations = [
            f"Route '{route.path}' has no response_model (and status "
            f"code {route.status_code} is not 204 No Content)"
            for route in _api_routes()
            if route.status_code != _NO_BODY_STATUS_CODE
            and route.response_model is None
        ]
        assert not violations, "\n".join(violations)


@pytest.mark.unit
class TestResponseEnvelopeFormat:
    """Every route that returns a body wraps it in the standard
    `{"data": ...}` envelope.

    See `docs/api-spec.md` (Response Format): paginated list endpoints
    return `{"data": [...], "meta": {...}}`; single-resource and
    unpaginated list endpoints return `{"data": {...}}`. A route with
    status code 204 (No Content) is exempt — it has no body. The
    `/health` and `/ready` exemption (see `TestRoutePrefixConvention`)
    also applies here — those endpoints are outside the API envelope
    contract by design.
    """

    def test_every_body_returning_route_has_a_data_property(self) -> None:
        openapi_schema = app.openapi()
        violations: list[str] = []
        for route in _api_routes():
            if route.status_code == _NO_BODY_STATUS_CODE:
                continue
            if route.path in _PREFIX_EXEMPT_PATHS:
                continue
            schema = _response_schema(openapi_schema, route)
            if schema is None or "data" not in schema.get("properties", {}):
                violations.append(
                    f"Route '{route.path}' response schema does not have "
                    "a top-level 'data' property (required by the "
                    "standard response envelope)"
                )
        assert not violations, "\n".join(violations)
