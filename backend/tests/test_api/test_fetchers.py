"""End-to-end tests for the Public fetcher observation API
(`backend/app/api/v1/fetchers.py`).

See `docs/features/platform/fetcher-operations.md` (List Fetchers, List
Fetcher Runs, Get Fetcher Run Detail, Get Fetcher Run Timeline Data) for
the authoritative endpoint contracts under test. Filter/query
combination coverage for the underlying query lives in
`tests/test_services/test_fetcher_operations.py` — these tests focus on
the HTTP/route contract (status codes, envelopes, error mapping,
optional-authentication visibility, and OpenAPI route inventory).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable, Generator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from celery import Celery
from fastapi import routing as fastapi_routing
from fastapi.routing import APIRoute
from httpx import AsyncClient
from pydantic import BaseModel, Field

import app.api.v1.fetchers as fetchers_module
from app.api import dependencies
from app.core.enums import Role
from app.main import app
from app.models.api_key import ApiKey
from app.models.fetcher_audit_event import FetcherAuditEvent
from app.models.fetcher_config import FetcherConfig
from app.models.fetcher_run import FetcherRun
from app.models.user import User
from app.models.user_role import UserRole
from app.services.base_fetcher import FETCHER_REGISTRY, BaseFetcher

FetcherConfigFactory = Callable[..., Awaitable[FetcherConfig]]
FetcherRunFactory = Callable[..., Awaitable[FetcherRun]]
FetcherAuditEventFactory = Callable[..., Awaitable[FetcherAuditEvent]]
UserFactory = Callable[..., Awaitable[User]]
UserRoleFactory = Callable[..., Awaitable[UserRole]]
ApiKeyFactory = Callable[..., Awaitable[ApiKey]]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_api_key_credential() -> tuple[str, str]:
    """Return `(plaintext_token, sha256_hex_digest)` for a synthetic key.

    Mirrors the identical helper in `tests/test_api/test_settings.py`.
    """
    token = "stl_ak_" + secrets.token_hex(16)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token, digest


@pytest_asyncio.fixture
async def admin_api_key_client(
    client: AsyncClient,
    user_factory: UserFactory,
    user_role_factory: UserRoleFactory,
    api_key_factory: ApiKeyFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncClient:
    """The shared `client`, authenticated as an admin via an API key
    (`Authorization: Bearer`) instead of a JWT session cookie."""
    user = await user_factory()
    await user_role_factory(user_id=user.id, role=Role.ADMIN.value)
    token, digest = _make_api_key_credential()
    await api_key_factory(user_id=user.id, key_hash=digest)
    monkeypatch.setattr(dependencies._last_used_debouncer, "touch", AsyncMock())
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture(autouse=True)
def _isolated_registry() -> Generator[None]:
    """Snapshot/restore `FETCHER_REGISTRY` around every test in this
    file — mirrors `tests/test_services/test_fetcher_schedule.py`."""
    original = dict(FETCHER_REGISTRY)
    yield
    FETCHER_REGISTRY.clear()
    FETCHER_REGISTRY.update(original)


class _StubFetcherStub:
    """Minimal `FETCHER_REGISTRY` entry stub — deliberately NOT a
    `BaseFetcher` subclass (see `test_fetcher_schedule.py`, Test
    Independence)."""

    name = "test_api_fetcher"
    description = "Stub fetcher for API tests"
    default_schedule = "0 3 * * *"
    queue: str | None = None
    Settings: type[BaseModel] | None = None


class _StubSettingsModel(BaseModel):
    results_per_page: int = Field(default=100, ge=10, le=1000)


class _StubFetcherWithSettingsStub:
    """Same rationale as `_StubFetcherStub`, with a `Settings` model —
    used to exercise the generated `settings_schema` in `GET .../config`
    responses."""

    name = "test_api_fetcher_with_settings"
    description = "Stub fetcher with custom settings for API tests"
    default_schedule = "0 4 * * *"
    queue: str | None = None
    Settings = _StubSettingsModel


_StubFetcher = cast("type[BaseFetcher]", _StubFetcherStub)
_StubFetcherWithSettings = cast("type[BaseFetcher]", _StubFetcherWithSettingsStub)


def _register(*stubs: type[Any]) -> None:
    """Replace `FETCHER_REGISTRY` with exactly the given stub classes."""
    FETCHER_REGISTRY.clear()
    for stub in stubs:
        FETCHER_REGISTRY[stub.name] = stub


@pytest_asyncio.fixture(autouse=True)
async def _patch_celery_app(
    celery_test_app: Celery, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the router's module-level `celery_app` reference at the
    isolated test Celery/Redis instance, so `GET /api/v1/fetchers`
    never touches an uncontrolled production Redis instance during
    tests. Requesting `celery_test_app` also composes its Redis
    isolation automatically for every test in this file."""
    monkeypatch.setattr(fetchers_module, "celery_app", celery_test_app)


# ---------------------------------------------------------------------------
# Route inventory
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestRouteInventory:
    def test_only_the_six_read_endpoints_are_registered(self) -> None:
        """No trigger, PATCH config, or IBS consumer endpoint is
        introduced by this work item — see fetcher-operations.md
        (Scope). Route discovery uses `iter_route_contexts()` — see
        `tests/test_api_conventions.py` for why `app.routes` alone does
        not expose routes included via `include_router()`.
        """
        fetcher_routes = {
            (context.path, method)
            for context in fastapi_routing.iter_route_contexts(app.routes)
            if isinstance(context.original_route, APIRoute)
            and context.path is not None
            and context.path.startswith("/api/v1/fetchers")
            for method in (context.methods or set()) - {"HEAD", "OPTIONS"}
        }
        assert fetcher_routes == {
            ("/api/v1/fetchers", "GET"),
            ("/api/v1/fetchers/{fetcher_name}/runs", "GET"),
            ("/api/v1/fetchers/{fetcher_name}/runs/{run_id}", "GET"),
            ("/api/v1/fetchers/{fetcher_name}/timeline", "GET"),
            ("/api/v1/fetchers/{fetcher_name}/config", "GET"),
            ("/api/v1/fetchers/{fetcher_name}/audit-log", "GET"),
        }


# ---------------------------------------------------------------------------
# GET /api/v1/fetchers
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestListFetchersEndpoint:
    async def test_anonymous_returns_200_with_empty_envelope(
        self, client: AsyncClient
    ) -> None:
        FETCHER_REGISTRY.clear()
        response = await client.get("/api/v1/fetchers")
        assert response.status_code == 200
        assert response.json() == {"data": []}

    async def test_includes_deregistered_fetcher(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        FETCHER_REGISTRY.clear()
        config = await fetcher_config_factory()

        response = await client.get("/api/v1/fetchers")

        assert response.status_code == 200
        names = [item["fetcher_name"] for item in response.json()["data"]]
        assert config.fetcher_name in names

    async def test_anonymous_does_not_see_triggered_by_user(
        self,
        client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
        user_factory: UserFactory,
    ) -> None:
        _register(_StubFetcher)
        config = await fetcher_config_factory(fetcher_name=_StubFetcher.name)
        actor = await user_factory()
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            triggered_by="manual",
            triggered_by_user_id=actor.id,
        )

        response = await client.get("/api/v1/fetchers")

        assert response.status_code == 200
        item = next(
            i for i in response.json()["data"] if i["fetcher_name"] == _StubFetcher.name
        )
        assert item["last_run"]["triggered_by_user"] is None

    async def test_admin_jwt_sees_triggered_by_user(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
        user_factory: UserFactory,
    ) -> None:
        _register(_StubFetcher)
        config = await fetcher_config_factory(fetcher_name=_StubFetcher.name)
        actor = await user_factory()
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            triggered_by="manual",
            triggered_by_user_id=actor.id,
        )

        response = await admin_client.get("/api/v1/fetchers")

        assert response.status_code == 200
        item = next(
            i for i in response.json()["data"] if i["fetcher_name"] == _StubFetcher.name
        )
        assert item["last_run"]["triggered_by_user"]["id"] == str(actor.id)

    async def test_admin_api_key_sees_triggered_by_user(
        self,
        admin_api_key_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
        user_factory: UserFactory,
    ) -> None:
        _register(_StubFetcher)
        config = await fetcher_config_factory(fetcher_name=_StubFetcher.name)
        actor = await user_factory()
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            triggered_by="manual",
            triggered_by_user_id=actor.id,
        )

        response = await admin_api_key_client.get("/api/v1/fetchers")

        assert response.status_code == 200
        item = next(
            i for i in response.json()["data"] if i["fetcher_name"] == _StubFetcher.name
        )
        assert item["last_run"]["triggered_by_user"]["id"] == str(actor.id)

    async def test_authenticated_without_manage_fetchers_does_not_see_triggered_by_user(
        self,
        authenticated_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
        user_factory: UserFactory,
    ) -> None:
        """An authenticated caller with no roles (and therefore no
        `manage_fetchers` capability) is treated identically to
        anonymous — capability, not mere authentication, gates
        visibility. See `_resolve_has_manage_fetchers`
        (`app/api/v1/fetchers.py`)."""
        _register(_StubFetcher)
        config = await fetcher_config_factory(fetcher_name=_StubFetcher.name)
        actor = await user_factory()
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            triggered_by="manual",
            triggered_by_user_id=actor.id,
        )

        response = await authenticated_client.get("/api/v1/fetchers")

        assert response.status_code == 200
        item = next(
            i for i in response.json()["data"] if i["fetcher_name"] == _StubFetcher.name
        )
        assert item["last_run"]["triggered_by_user"] is None

    async def test_invalid_selected_credential_returns_401(
        self, client: AsyncClient
    ) -> None:
        """A selected but rejected credential is never silently ignored
        — see `docs/api-spec.md` (Optional Authentication on Public
        Endpoints)."""
        response = await client.get(
            "/api/v1/fetchers", headers={"Authorization": "Bearer not-a-real-token"}
        )

        assert response.status_code == 401
        assert response.json()["code"] == "AUTH_NOT_AUTHENTICATED"


# ---------------------------------------------------------------------------
# GET /api/v1/fetchers/{fetcher_name}/runs
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestListFetcherRunsEndpoint:
    async def test_unknown_fetcher_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/fetchers/no-such-fetcher/runs")
        assert response.status_code == 404
        assert response.json()["code"] == "FETCHER_NOT_FOUND"

    async def test_returns_paginated_envelope(
        self,
        client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_run_factory(fetcher_name=config.fetcher_name)

        response = await client.get(f"/api/v1/fetchers/{config.fetcher_name}/runs")

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body
        assert body["meta"] == {"total": 1, "page": 1, "per_page": 20}

    async def test_raw_diagnostics_never_present_in_list_items(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="failure",
            error_detail="raw detail",
            error_traceback="raw traceback",
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs"
        )

        assert response.status_code == 200
        item = response.json()["data"][0]
        assert "error_detail" not in item
        assert "error_traceback" not in item

    async def test_invalid_page_returns_422(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs", params={"page": 0}
        )
        assert response.status_code == 422

    async def test_invalid_per_page_returns_422(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs", params={"per_page": 101}
        )
        assert response.status_code == 422

    async def test_malformed_date_returns_422(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs",
            params={"from_date": "not-a-date"},
        )
        assert response.status_code == 422

    async def test_inverted_range_returns_400(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs",
            params={"from_date": "2025-06-01", "to_date": "2025-01-01"},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "DATE_RANGE_INVERTED"

    async def test_invalid_status_returns_empty_page_not_error(
        self,
        client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_run_factory(fetcher_name=config.fetcher_name, status="success")

        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs",
            params={"status": "not-a-real-status"},
        )

        assert response.status_code == 200
        assert response.json()["data"] == []
        assert response.json()["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestGetFetcherRunEndpoint:
    async def test_unknown_fetcher_returns_404(self, client: AsyncClient) -> None:
        response = await client.get(f"/api/v1/fetchers/no-such-fetcher/runs/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["code"] == "FETCHER_NOT_FOUND"

    async def test_unknown_run_returns_404(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs/{uuid4()}"
        )
        assert response.status_code == 404
        assert response.json()["code"] == "FETCHER_NOT_FOUND"

    async def test_run_of_different_fetcher_returns_404(
        self,
        client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config_a = await fetcher_config_factory()
        config_b = await fetcher_config_factory()
        run = await fetcher_run_factory(fetcher_name=config_a.fetcher_name)

        response = await client.get(
            f"/api/v1/fetchers/{config_b.fetcher_name}/runs/{run.id}"
        )

        assert response.status_code == 404
        assert response.json()["code"] == "FETCHER_NOT_FOUND"

    async def test_anonymous_does_not_see_raw_diagnostics(
        self,
        client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="failure",
            error_message="sanitized",
            error_detail="raw detail",
            error_traceback="raw traceback",
        )

        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs/{run.id}"
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["error_message"] == "sanitized"
        assert "error_detail" not in data
        assert "error_traceback" not in data

    async def test_admin_jwt_sees_raw_diagnostics(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="failure",
            error_detail="raw detail",
            error_traceback="raw traceback",
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs/{run.id}"
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["error_detail"] == "raw detail"
        assert data["error_traceback"] == "raw traceback"

    async def test_admin_api_key_sees_raw_diagnostics(
        self,
        admin_api_key_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="failure",
            error_detail="raw detail",
            error_traceback="raw traceback",
        )

        response = await admin_api_key_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs/{run.id}"
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["error_detail"] == "raw detail"
        assert data["error_traceback"] == "raw traceback"

    async def test_authenticated_without_manage_fetchers_does_not_see_raw_diagnostics(
        self,
        authenticated_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        """An authenticated caller with no roles (and therefore no
        `manage_fetchers` capability) is treated identically to
        anonymous — capability, not mere authentication, gates
        visibility. See `_resolve_has_manage_fetchers`
        (`app/api/v1/fetchers.py`)."""
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="failure",
            error_message="sanitized",
            error_detail="raw detail",
            error_traceback="raw traceback",
        )

        response = await authenticated_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs/{run.id}"
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["error_message"] == "sanitized"
        assert "error_detail" not in data
        assert "error_traceback" not in data

    async def test_running_run_has_null_finished_at_and_duration(
        self,
        client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_run_factory: FetcherRunFactory,
    ) -> None:
        config = await fetcher_config_factory()
        run = await fetcher_run_factory(
            fetcher_name=config.fetcher_name,
            status="running",
            finished_at=None,
            duration_seconds=None,
        )

        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/runs/{run.id}"
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["finished_at"] is None
        assert data["duration_seconds"] is None


# ---------------------------------------------------------------------------
# GET /api/v1/fetchers/{fetcher_name}/timeline
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestGetFetcherTimelineEndpoint:
    async def test_unknown_fetcher_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/fetchers/no-such-fetcher/timeline")
        assert response.status_code == 404
        assert response.json()["code"] == "FETCHER_NOT_FOUND"

    async def test_default_range_returns_envelope(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(f"/api/v1/fetchers/{config.fetcher_name}/timeline")

        assert response.status_code == 200
        data = response.json()["data"]
        assert "points" in data
        assert "disabled_periods" in data

    async def test_date_range_too_wide_returns_400(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/timeline",
            params={"from_date": "2000-01-01", "to_date": "2010-01-01"},
        )

        assert response.status_code == 400
        assert response.json()["code"] == "DATE_RANGE_TOO_WIDE"

    async def test_exactly_1825_days_is_accepted(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        """Uses full ISO datetimes (not bare dates) so neither bound is
        widened to a day boundary (`docs/api-spec.md`, Date Range
        Interpretation) — the actual interval is exactly 1825 days,
        the documented maximum, and must be accepted."""
        config = await fetcher_config_factory()
        from_date = datetime(2020, 1, 1, tzinfo=UTC)
        to_date = from_date + timedelta(days=1825)

        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/timeline",
            params={
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            },
        )

        assert response.status_code == 200

    async def test_disabled_period_actor_hidden_without_manage_fetchers(
        self,
        client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory()
        now = datetime.now(UTC)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=now - timedelta(days=1),
            user_id=actor.id,
        )

        response = await client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/timeline",
            params={
                "from_date": (now - timedelta(days=5)).date().isoformat(),
                "to_date": now.date().isoformat(),
            },
        )

        assert response.status_code == 200
        period = response.json()["data"]["disabled_periods"][0]
        assert period["disabled_by"] is None

    async def test_disabled_period_actor_visible_with_manage_fetchers(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory()
        now = datetime.now(UTC)
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="disabled",
            created_at=now - timedelta(days=1),
            user_id=actor.id,
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/timeline",
            params={
                "from_date": (now - timedelta(days=5)).date().isoformat(),
                "to_date": now.date().isoformat(),
            },
        )

        assert response.status_code == 200
        period = response.json()["data"]["disabled_periods"][0]
        assert period["disabled_by"]["id"] == str(actor.id)


# ---------------------------------------------------------------------------
# Out-of-scope endpoints
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestOutOfScopeEndpoints:
    """No mutation or IBS consumer endpoint is introduced by this work
    item — see `docs/features/platform/fetcher-operations.md` (Scope).
    Verified here in addition to `TestRouteInventory` since a client
    request to an unregistered path is the actually-observable contract
    at the HTTP layer."""

    async def test_trigger_endpoint_not_found(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.post(f"/api/v1/fetchers/{config.fetcher_name}/trigger")
        assert response.status_code == 404

    async def test_patch_config_method_not_allowed(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        """The `config` path now exists (`GET`), but no mutation method
        is registered on it — a `PATCH` yields `405`, not `404`."""
        config = await fetcher_config_factory()
        response = await admin_client.patch(
            f"/api/v1/fetchers/{config.fetcher_name}/config"
        )
        assert response.status_code == 405

    async def test_ibs_consumer_status_endpoint_not_found(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/ibs-consumer/status")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/fetchers/{fetcher_name}/config
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestGetFetcherConfigEndpoint:
    async def test_unauthenticated_returns_401(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(f"/api/v1/fetchers/{config.fetcher_name}/config")
        assert response.status_code == 401
        assert response.json()["code"] == "AUTH_NOT_AUTHENTICATED"

    async def test_ordinary_user_returns_403(
        self,
        authenticated_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        config = await fetcher_config_factory()
        response = await authenticated_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/config"
        )
        assert response.status_code == 403
        assert response.json()["code"] == "AUTH_INSUFFICIENT_PERMISSION"

    async def test_admin_jwt_returns_the_config(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/config"
        )
        assert response.status_code == 200

    async def test_admin_api_key_returns_the_config(
        self,
        admin_api_key_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_api_key_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/config"
        )
        assert response.status_code == 200

    async def test_registered_fetcher_exact_item_shape_with_schema(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_StubFetcherWithSettings)
        config = await fetcher_config_factory(
            fetcher_name=_StubFetcherWithSettings.name,
            custom_settings={"results_per_page": 250},
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/config"
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert set(data.keys()) == {
            "fetcher_name",
            "enabled",
            "schedule_override",
            "default_schedule",
            "effective_schedule",
            "run_timeout",
            "request_delay",
            "custom_settings",
            "settings_schema",
            "updated_at",
        }
        assert data["fetcher_name"] == config.fetcher_name
        assert data["default_schedule"] == _StubFetcherWithSettings.default_schedule
        assert data["custom_settings"] == {"results_per_page": 250}
        assert data["settings_schema"] == _StubSettingsModel.model_json_schema()

    async def test_registered_fetcher_without_settings_model_schema_is_null(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        _register(_StubFetcher)
        config = await fetcher_config_factory(fetcher_name=_StubFetcher.name)

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/config"
        )

        assert response.json()["data"]["settings_schema"] is None

    async def test_deregistered_fetcher_returns_raw_snapshot(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        FETCHER_REGISTRY.clear()
        config = await fetcher_config_factory(
            schedule_override="0 5 * * *",
            custom_settings={"orphaned_key": "raw_value"},
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/config"
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["default_schedule"] is None
        assert data["settings_schema"] is None
        assert data["effective_schedule"] == "0 5 * * *"
        assert data["custom_settings"] == {"orphaned_key": "raw_value"}

    async def test_unknown_fetcher_returns_404(self, admin_client: AsyncClient) -> None:
        FETCHER_REGISTRY.clear()
        response = await admin_client.get("/api/v1/fetchers/no-such-fetcher/config")
        assert response.status_code == 404
        assert response.json()["code"] == "FETCHER_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /api/v1/fetchers/{fetcher_name}/audit-log
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestListFetcherAuditEventsEndpoint:
    async def test_unauthenticated_returns_401(
        self, client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await client.get(f"/api/v1/fetchers/{config.fetcher_name}/audit-log")
        assert response.status_code == 401
        assert response.json()["code"] == "AUTH_NOT_AUTHENTICATED"

    async def test_ordinary_user_returns_403(
        self,
        authenticated_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        config = await fetcher_config_factory()
        response = await authenticated_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log"
        )
        assert response.status_code == 403
        assert response.json()["code"] == "AUTH_INSUFFICIENT_PERMISSION"

    async def test_admin_jwt_can_list(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log"
        )
        assert response.status_code == 200

    async def test_admin_api_key_can_list(
        self,
        admin_api_key_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_api_key_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log"
        )
        assert response.status_code == 200

    async def test_empty_result_has_correct_envelope(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log"
        )

        assert response.json() == {
            "data": [],
            "meta": {"total": 0, "page": 1, "per_page": 20},
        }

    async def test_exact_item_shape(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log"
        )

        item = response.json()["data"][0]
        assert set(item.keys()) == {
            "id",
            "fetcher_name",
            "event_type",
            "actor",
            "old_value",
            "new_value",
            "detail",
            "created_at",
        }
        assert item["fetcher_name"] == config.fetcher_name
        assert set(item["actor"].keys()) == {"id", "username", "full_name", "active"}

    async def test_populated_old_new_value_and_detail_round_trip(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        """Regression: `old_value`/`new_value`/`detail` must survive the
        ORM -> service -> schema -> JSON round trip unchanged. All prior
        fixtures relied on factory defaults (all `None`), which would
        not catch a field-swap or serialization bug."""
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            event_type="config_changed",
            old_value="0 */6 * * *",
            new_value="0 */4 * * *",
            detail={"field": "schedule_override"},
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log"
        )

        item = response.json()["data"][0]
        assert item["event_type"] == "config_changed"
        assert item["old_value"] == "0 */6 * * *"
        assert item["new_value"] == "0 */4 * * *"
        assert item["detail"] == {"field": "schedule_override"}

    async def test_unknown_fetcher_returns_404(self, admin_client: AsyncClient) -> None:
        FETCHER_REGISTRY.clear()
        response = await admin_client.get("/api/v1/fetchers/no-such-fetcher/audit-log")
        assert response.status_code == 404
        assert response.json()["code"] == "FETCHER_NOT_FOUND"

    async def test_all_invalid_event_types_still_checks_fetcher_existence(
        self, admin_client: AsyncClient
    ) -> None:
        """Regression for the fix in this work item: an entirely
        invalid `event_type` filter must not mask a nonexistent
        fetcher behind an empty `200` — the existence check always
        runs first."""
        FETCHER_REGISTRY.clear()
        response = await admin_client.get(
            "/api/v1/fetchers/no-such-fetcher/audit-log",
            params=[("event_type", "not-a-real-type")],
        )
        assert response.status_code == 404
        assert response.json()["code"] == "FETCHER_NOT_FOUND"

    async def test_event_type_filter(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, event_type="disabled"
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params=[("event_type", "disabled")],
        )

        assert response.json()["meta"]["total"] == 1

    async def test_all_invalid_event_types_return_empty_result(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params=[("event_type", "not-a-real-type")],
        )

        assert response.status_code == 200
        assert response.json()["meta"]["total"] == 0

    async def test_actor_filter_by_uuid(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
        user_factory: UserFactory,
    ) -> None:
        config = await fetcher_config_factory()
        actor = await user_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name, user_id=actor.id
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params={"actor": str(actor.id)},
        )

        assert response.json()["meta"]["total"] == 1

    async def test_unknown_actor_returns_empty_result_not_404(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params={"actor": "no-such-actor"},
        )
        assert response.status_code == 200
        assert response.json()["meta"]["total"] == 0

    async def test_system_actor_returns_empty_result(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        """Every fetcher audit event has a human actor — `system` never
        matches (`fetcher-operations.md`, Get Fetcher Audit Log)."""
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params={"actor": "system"},
        )

        assert response.status_code == 200
        assert response.json()["meta"]["total"] == 0

    async def test_inclusive_date_range(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 13, tzinfo=UTC),
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params={"from_date": "2026-05-01", "to_date": "2026-05-31"},
        )

        assert response.json()["meta"]["total"] == 1

    async def test_malformed_date_returns_422(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params={"from_date": "not-a-date"},
        )
        assert response.status_code == 422

    async def test_inverted_date_range_returns_400(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params={"from_date": "2026-05-16", "to_date": "2026-05-15"},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "DATE_RANGE_INVERTED"

    async def test_page_below_one_returns_422(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log", params={"page": 0}
        )
        assert response.status_code == 422

    async def test_per_page_out_of_range_returns_422(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params={"per_page": 101},
        )
        assert response.status_code == 422

    async def test_page_beyond_last_page_returns_empty_with_correct_total(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log", params={"page": 2}
        )

        assert response.status_code == 200
        assert response.json()["data"] == []
        assert response.json()["meta"]["total"] == 1

    async def test_fixed_newest_first_ordering(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        older = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        newer = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 2, tzinfo=UTC),
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log"
        )

        ids = [item["id"] for item in response.json()["data"]]
        assert ids == [str(newer.id), str(older.id)]

    async def test_sort_by_and_sort_order_are_ignored(
        self,
        admin_client: AsyncClient,
        fetcher_config_factory: FetcherConfigFactory,
        fetcher_audit_event_factory: FetcherAuditEventFactory,
    ) -> None:
        config = await fetcher_config_factory()
        older = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 1, tzinfo=UTC),
        )
        newer = await fetcher_audit_event_factory(
            fetcher_name=config.fetcher_name,
            created_at=datetime(2026, 5, 2, tzinfo=UTC),
        )

        response = await admin_client.get(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log",
            params={"sort_by": "created_at", "sort_order": "asc"},
        )

        ids = [item["id"] for item in response.json()["data"]]
        assert ids == [str(newer.id), str(older.id)]

    async def test_no_mutation_methods_are_exposed(
        self, admin_client: AsyncClient, fetcher_config_factory: FetcherConfigFactory
    ) -> None:
        config = await fetcher_config_factory()
        response = await admin_client.post(
            f"/api/v1/fetchers/{config.fetcher_name}/audit-log"
        )
        assert response.status_code == 405


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchersConfigAndAuditLogOpenAPISurface:
    def test_both_endpoints_have_summary_and_description(self) -> None:
        openapi_paths = app.openapi()["paths"]

        config_get = openapi_paths["/api/v1/fetchers/{fetcher_name}/config"]["get"]
        assert config_get["summary"]
        assert config_get["description"]

        audit_get = openapi_paths["/api/v1/fetchers/{fetcher_name}/audit-log"]["get"]
        assert audit_get["summary"]
        assert audit_get["description"]

    def test_audit_log_query_parameters_are_declared(self) -> None:
        openapi_paths = app.openapi()["paths"]
        audit_get = openapi_paths["/api/v1/fetchers/{fetcher_name}/audit-log"]["get"]
        param_names = {p["name"] for p in audit_get.get("parameters", [])}

        assert {"event_type", "actor", "from_date", "to_date", "page", "per_page"} <= (
            param_names
        )
        assert "sort_by" not in param_names
        assert "sort_order" not in param_names

    def test_event_type_parameter_is_repeatable(self) -> None:
        openapi_paths = app.openapi()["paths"]
        audit_get = openapi_paths["/api/v1/fetchers/{fetcher_name}/audit-log"]["get"]
        event_type_param = next(
            p for p in audit_get["parameters"] if p["name"] == "event_type"
        )
        assert event_type_param["schema"]["type"] == "array"
