"""API convention conformity tests.

These tests inspect the FastAPI application to verify that all registered
endpoints conform to the project's API conventions documented in
docs/api-spec.md.

Tests are structural — they introspect route metadata, response models,
and path patterns without making HTTP requests.

NOTE: This file is a placeholder. Tests will be activated as API endpoints
are implemented. Each test function documents the convention it enforces.
"""

from __future__ import annotations

import re

import pytest
from fastapi.routing import APIRoute

from app.main import app


def _api_routes() -> list[APIRoute]:
    """Return all API v1 routes registered on the app."""
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1")
    ]


# ---------------------------------------------------------------------------
# Convention: Every endpoint must have a summary and description for OpenAPI
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _api_routes(), reason="No API endpoints implemented yet")
def test_all_endpoints_have_openapi_metadata():
    """Every endpoint must define summary and description for OpenAPI docs."""
    missing = []
    for route in _api_routes():
        if not route.summary or not route.description:
            missing.append(f"{route.methods} {route.path}")
    assert not missing, (
        f"Endpoints missing summary/description:\n"
        + "\n".join(f"  - {m}" for m in missing)
    )


# ---------------------------------------------------------------------------
# Convention: Path segments use plural nouns in kebab-case
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _api_routes(), reason="No API endpoints implemented yet")
def test_path_segments_are_plural_kebab_case():
    """Path segments (excluding parameters) should be plural kebab-case."""
    # Known acceptable singular segments (e.g., /health, /me, /my)
    exceptions = {"health", "ready", "me", "my", "admin"}

    issues = []
    for route in _api_routes():
        segments = [
            seg
            for seg in route.path.split("/")
            if seg and not seg.startswith("{")
        ]
        for seg in segments:
            if seg in ("api", "v1") or seg in exceptions:
                continue
            # Check kebab-case (no camelCase or snake_case)
            if re.search(r"[A-Z]", seg) or "_" in seg:
                issues.append(f"{route.path}: segment '{seg}' not kebab-case")
    assert not issues, (
        f"Path naming violations:\n" + "\n".join(f"  - {i}" for i in issues)
    )


# ---------------------------------------------------------------------------
# Convention: Action endpoints (with verb suffix) use POST only
# ---------------------------------------------------------------------------


# Known action verbs that indicate a POST-verb endpoint
_ACTION_VERBS = {
    "assign",
    "unassign",
    "resolve",
    "reopen",
    "duplicate",
    "trigger",
    "dismiss",
    "ignore",
    "lock",
    "unlock",
    "activate",
    "deactivate",
    "run",
}


@pytest.mark.skipif(not _api_routes(), reason="No API endpoints implemented yet")
def test_action_endpoints_use_post():
    """Endpoints with a verb suffix (assign, trigger, etc.) must use POST."""
    issues = []
    for route in _api_routes():
        last_segment = route.path.rstrip("/").split("/")[-1]
        if last_segment in _ACTION_VERBS:
            non_post_methods = route.methods - {"POST"}
            if non_post_methods:
                issues.append(
                    f"{route.path}: action verb '{last_segment}' "
                    f"uses {non_post_methods} instead of POST"
                )
    assert not issues, (
        f"Action endpoint violations:\n" + "\n".join(f"  - {i}" for i in issues)
    )


# ---------------------------------------------------------------------------
# Convention: Error responses include machine-readable 'code' field
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _api_routes(), reason="No API endpoints implemented yet")
def test_error_responses_have_code_field():
    """Error response models (4xx, 5xx) must include a 'code' field."""
    issues = []
    for route in _api_routes():
        for status_code, response in (route.responses or {}).items():
            if isinstance(status_code, int) and status_code >= 400:
                model = response.get("model")
                if model is None:
                    issues.append(
                        f"{route.methods} {route.path} [{status_code}]: "
                        f"no response model defined"
                    )
                elif hasattr(model, "model_fields") and "code" not in model.model_fields:
                    issues.append(
                        f"{route.methods} {route.path} [{status_code}]: "
                        f"response model missing 'code' field"
                    )
    assert not issues, (
        f"Error response violations:\n" + "\n".join(f"  - {i}" for i in issues)
    )


# ---------------------------------------------------------------------------
# Convention: List endpoints use data+meta envelope with pagination
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _api_routes(), reason="No API endpoints implemented yet")
def test_list_endpoints_have_pagination_envelope():
    """GET endpoints returning collections must use data+meta response model."""
    issues = []
    for route in _api_routes():
        if "GET" not in route.methods:
            continue
        # Heuristic: list endpoints don't have a path parameter as last segment
        last_segment = route.path.rstrip("/").split("/")[-1]
        if last_segment.startswith("{"):
            continue  # Detail endpoint, not list
        # Check response model has 'data' and 'meta' fields
        model = route.response_model
        if model and hasattr(model, "model_fields"):
            fields = set(model.model_fields.keys())
            if "data" not in fields or "meta" not in fields:
                issues.append(
                    f"GET {route.path}: response model missing "
                    f"'data'/'meta' envelope (has: {fields})"
                )
    assert not issues, (
        f"Envelope violations:\n" + "\n".join(f"  - {i}" for i in issues)
    )
