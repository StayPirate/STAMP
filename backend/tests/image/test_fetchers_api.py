"""Black-box image smoke assertions for the fetcher observation and
admin config/audit-log/config-mutation/trigger API
(`backend/app/api/v1/fetchers.py`).

Verifies — over a real ASGI server (uvicorn) and a real HTTP client
(`urllib`), the only combination that can observe this — representative
authorized and anonymous responses from all eight
`GET|PATCH|POST /api/v1/fetchers*` endpoints against the built image. The
shipped image's `FETCHER_REGISTRY` has no production fetcher yet (see
`docs/features/platform/fetcher-infrastructure.md`, Fetcher Registry),
so this scenario arranges a deregistered `FetcherConfig` row directly —
the same "historical data only" shape the endpoints document for a
fetcher whose code has been removed. This also means the PATCH and
trigger endpoints' only fully observable outcome here is the `409
FETCHER_DEREGISTERED` guard — a successful mutation, Celery publication,
and RedBeat propagation all require a registered fetcher, already
covered by the in-process e2e suite. The exhaustive contract (merge
logic, filters, pagination, disabled-period derivation,
mutation/audit/propagation, trigger orchestration) is already covered by
the in-process e2e suite (`tests/test_api/test_fetchers.py`) and the
service suite (`tests/test_services/test_fetcher_operations.py`), which
run far faster and do not need a running container.

See docs/features/platform/testing-strategy.md (Image / Container
Smoke Testing, Growth Rule).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

_FETCHERS_API_CHECK_SCRIPT = r"""
import asyncio
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.core.enums import Role, SessionCreationReason
from app.database import async_session_factory
from app.models.fetcher_audit_event import FetcherAuditEvent
from app.models.fetcher_config import FetcherConfig
from app.models.fetcher_run import FetcherRun
from app.models.session import Session
from app.models.user import User
from app.models.user_role import UserRole
from app.services.session_service import create_session

_ADMIN_USERNAME = "imagefetchersapiadmin"
_FETCHER_NAME = "imagesmoketestfetcher"
_QUEUED_FETCHER_NAME = "imagesmoketestfetcherqueued"


async def arrange():
    async with async_session_factory() as db:
        admin = User(
            username=_ADMIN_USERNAME,
            email="imagefetchersapiadmin@example.com",
            password_hash="$2b$12$" + "a" * 53,
        )
        db.add(admin)
        await db.flush()
        db.add(UserRole(user_id=admin.id, role=Role.ADMIN.value))
        config = FetcherConfig(
            fetcher_name=_FETCHER_NAME,
            schedule_override="0 5 * * *",
            custom_settings={"orphaned_key": "raw_value"},
        )
        db.add(config)
        await db.flush()
        run = FetcherRun(
            fetcher_name=_FETCHER_NAME,
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            finished_at=datetime.now(UTC),
            duration_seconds=12.5,
            status="failure",
            items_created=1,
            items_updated=2,
            items_failed=1,
            error_message="sanitized failure",
            error_detail="raw TimeoutError detail",
            error_traceback="Traceback (most recent call last): ...",
            triggered_by="schedule",
        )
        db.add(run)
        # Uses `triggered` (not `disabled`/`enabled`) so this event does
        # not participate in Disabled Period Derivation and interfere
        # with `get_fetcher_timeline_returns_envelope`'s empty-periods
        # assertion below.
        audit_event = FetcherAuditEvent(
            fetcher_name=_FETCHER_NAME,
            event_type="triggered",
            user_id=admin.id,
        )
        db.add(audit_event)
        # A separate fetcher/run dedicated to the `queued` lifecycle
        # representation — kept independent of `_FETCHER_NAME` above so
        # it cannot become that fetcher's `last_run` and interfere with
        # the deregistered-fetcher assertions there.
        queued_config = FetcherConfig(fetcher_name=_QUEUED_FETCHER_NAME)
        db.add(queued_config)
        await db.flush()
        queued_run = FetcherRun(
            fetcher_name=_QUEUED_FETCHER_NAME,
            status="queued",
            triggered_by="manual",
            triggered_by_user_id=admin.id,
        )
        db.add(queued_run)
        await db.flush()
        admin_session = await create_session(
            db, admin, SessionCreationReason.LOCAL_LOGIN
        )
        await db.commit()
        return admin.id, admin_session.token, run.id, queued_run.id


async def cleanup(admin_id):
    async with async_session_factory() as db:
        await db.execute(
            delete(FetcherRun).where(
                FetcherRun.fetcher_name.in_([_FETCHER_NAME, _QUEUED_FETCHER_NAME])
            )
        )
        await db.execute(
            delete(FetcherAuditEvent).where(
                FetcherAuditEvent.fetcher_name == _FETCHER_NAME
            )
        )
        await db.execute(
            delete(FetcherConfig).where(
                FetcherConfig.fetcher_name.in_([_FETCHER_NAME, _QUEUED_FETCHER_NAME])
            )
        )
        await db.execute(delete(UserRole).where(UserRole.user_id == admin_id))
        await db.execute(delete(Session).where(Session.user_id == admin_id))
        admin = await db.get(User, admin_id)
        if admin is not None:
            await db.delete(admin)
        await db.commit()


def _request(method, path, token=None, body=None):
    headers = {}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"http://localhost:8000{path}", method=method, headers=headers, data=data
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def list_fetchers_anonymous_shows_deregistered_fetcher():
    status, body = _request("GET", "/api/v1/fetchers")
    assert status == 200, (status, body)
    item = next(i for i in body["data"] if i["fetcher_name"] == _FETCHER_NAME)
    assert item["registered"] is False
    assert item["description"] is None
    assert item["last_run"]["status"] == "failure"
    print("fetchers-list-anonymous-ok")


def list_fetcher_runs_returns_paginated_envelope():
    status, body = _request("GET", f"/api/v1/fetchers/{_FETCHER_NAME}/runs")
    assert status == 200, (status, body)
    assert body["meta"]["total"] == 1
    assert "error_detail" not in body["data"][0]
    print("fetchers-runs-list-ok")


def list_fetcher_runs_unknown_fetcher_returns_404():
    status, body = _request("GET", "/api/v1/fetchers/no-such-fetcher/runs")
    assert status == 404, (status, body)
    assert body["code"] == "FETCHER_NOT_FOUND"
    print("fetchers-runs-not-found-ok")


def get_fetcher_run_anonymous_hides_raw_diagnostics(run_id):
    status, body = _request(
        "GET", f"/api/v1/fetchers/{_FETCHER_NAME}/runs/{run_id}"
    )
    assert status == 200, (status, body)
    assert "error_detail" not in body["data"]
    assert "error_traceback" not in body["data"]
    print("fetchers-run-detail-anonymous-ok")


def get_fetcher_run_admin_shows_raw_diagnostics(run_id, admin_token):
    status, body = _request(
        "GET", f"/api/v1/fetchers/{_FETCHER_NAME}/runs/{run_id}", token=admin_token
    )
    assert status == 200, (status, body)
    assert body["data"]["error_detail"] == "raw TimeoutError detail"
    assert body["data"]["error_traceback"] is not None
    print("fetchers-run-detail-admin-ok")


def get_fetcher_timeline_returns_envelope():
    status, body = _request("GET", f"/api/v1/fetchers/{_FETCHER_NAME}/timeline")
    assert status == 200, (status, body)
    assert len(body["data"]["points"]) == 1
    assert body["data"]["disabled_periods"] == []
    print("fetchers-timeline-ok")


def get_fetcher_config_anonymous_returns_401():
    status, body = _request("GET", f"/api/v1/fetchers/{_FETCHER_NAME}/config")
    assert status == 401, (status, body)
    assert body["code"] == "AUTH_NOT_AUTHENTICATED"
    print("fetchers-config-anonymous-401-ok")


def get_fetcher_config_admin_shows_deregistered_snapshot(admin_token):
    status, body = _request(
        "GET", f"/api/v1/fetchers/{_FETCHER_NAME}/config", token=admin_token
    )
    assert status == 200, (status, body)
    assert body["data"]["default_schedule"] is None
    assert body["data"]["settings_schema"] is None
    assert body["data"]["effective_schedule"] == "0 5 * * *"
    assert body["data"]["custom_settings"] == {"orphaned_key": "raw_value"}
    print("fetchers-config-admin-ok")


def patch_fetcher_config_anonymous_returns_401():
    status, body = _request(
        "PATCH",
        f"/api/v1/fetchers/{_FETCHER_NAME}/config",
        body={"enabled": False},
    )
    assert status == 401, (status, body)
    assert body["code"] == "AUTH_NOT_AUTHENTICATED"
    print("fetchers-config-patch-anonymous-401-ok")


def patch_fetcher_config_admin_deregistered_returns_409(admin_token):
    # The shipped image's FETCHER_REGISTRY has no production fetcher
    # yet (see module docstring), so _FETCHER_NAME's FetcherConfig row
    # is always deregistered - the one PATCH outcome fully observable
    # without production fetcher scaffolding.
    status, body = _request(
        "PATCH",
        f"/api/v1/fetchers/{_FETCHER_NAME}/config",
        token=admin_token,
        body={"enabled": False},
    )
    assert status == 409, (status, body)
    assert body["code"] == "FETCHER_DEREGISTERED"
    print("fetchers-config-patch-deregistered-409-ok")


def trigger_fetcher_anonymous_returns_401():
    status, body = _request("POST", f"/api/v1/fetchers/{_FETCHER_NAME}/trigger")
    assert status == 401, (status, body)
    assert body["code"] == "AUTH_NOT_AUTHENTICATED"
    print("fetchers-trigger-anonymous-401-ok")


def trigger_fetcher_admin_deregistered_returns_409(admin_token):
    # Same rationale as patch_fetcher_config_admin_deregistered_returns_409:
    # the shipped image has no registered production fetcher, so the
    # deregistered guard is the one outcome fully observable here — a
    # successful trigger requires a registered fetcher and a running
    # Celery worker, already covered by the in-process e2e/service suites.
    status, body = _request(
        "POST", f"/api/v1/fetchers/{_FETCHER_NAME}/trigger", token=admin_token
    )
    assert status == 409, (status, body)
    assert body["code"] == "FETCHER_DEREGISTERED"
    print("fetchers-trigger-deregistered-409-ok")


def get_fetcher_audit_log_anonymous_returns_401():
    status, body = _request("GET", f"/api/v1/fetchers/{_FETCHER_NAME}/audit-log")
    assert status == 401, (status, body)
    assert body["code"] == "AUTH_NOT_AUTHENTICATED"
    print("fetchers-audit-log-anonymous-401-ok")


def get_fetcher_audit_log_admin_shows_event(admin_token, admin_id):
    status, body = _request(
        "GET", f"/api/v1/fetchers/{_FETCHER_NAME}/audit-log", token=admin_token
    )
    assert status == 200, (status, body)
    assert body["meta"]["total"] == 1
    assert body["data"][0]["event_type"] == "triggered"
    assert body["data"][0]["actor"]["id"] == str(admin_id)
    print("fetchers-audit-log-admin-ok")


def unauthenticated_invalid_credential_returns_401():
    status, body = _request(
        "GET", "/api/v1/fetchers", token="not-a-real-token"
    )
    assert status == 401, (status, body)
    assert body["code"] == "AUTH_NOT_AUTHENTICATED"
    print("fetchers-invalid-credential-401-ok")


def get_fetcher_run_detail_shows_queued_run(queued_run_id):
    status, body = _request(
        "GET", f"/api/v1/fetchers/{_QUEUED_FETCHER_NAME}/runs/{queued_run_id}"
    )
    assert status == 200, (status, body)
    data = body["data"]
    assert data["status"] == "queued"
    assert data["started_at"] is None
    assert data["finished_at"] is None
    assert data["duration_seconds"] is None
    assert data["created_at"] is not None
    print("fetchers-run-detail-queued-ok")


def list_fetcher_runs_filters_by_queued_status():
    status, body = _request(
        "GET", f"/api/v1/fetchers/{_QUEUED_FETCHER_NAME}/runs",
        token=None,
    )
    assert status == 200, (status, body)
    assert body["meta"]["total"] == 1, body
    assert body["data"][0]["status"] == "queued"
    print("fetchers-runs-list-queued-ok")


async def main():
    admin_id, admin_token, run_id, queued_run_id = await arrange()
    try:
        await asyncio.to_thread(list_fetchers_anonymous_shows_deregistered_fetcher)
        await asyncio.to_thread(list_fetcher_runs_returns_paginated_envelope)
        await asyncio.to_thread(list_fetcher_runs_unknown_fetcher_returns_404)
        await asyncio.to_thread(
            get_fetcher_run_anonymous_hides_raw_diagnostics, run_id
        )
        await asyncio.to_thread(
            get_fetcher_run_admin_shows_raw_diagnostics, run_id, admin_token
        )
        await asyncio.to_thread(get_fetcher_timeline_returns_envelope)
        await asyncio.to_thread(get_fetcher_config_anonymous_returns_401)
        await asyncio.to_thread(
            get_fetcher_config_admin_shows_deregistered_snapshot, admin_token
        )
        await asyncio.to_thread(patch_fetcher_config_anonymous_returns_401)
        await asyncio.to_thread(
            patch_fetcher_config_admin_deregistered_returns_409, admin_token
        )
        await asyncio.to_thread(trigger_fetcher_anonymous_returns_401)
        await asyncio.to_thread(
            trigger_fetcher_admin_deregistered_returns_409, admin_token
        )
        await asyncio.to_thread(get_fetcher_audit_log_anonymous_returns_401)
        await asyncio.to_thread(
            get_fetcher_audit_log_admin_shows_event, admin_token, admin_id
        )
        await asyncio.to_thread(unauthenticated_invalid_credential_returns_401)
        await asyncio.to_thread(
            get_fetcher_run_detail_shows_queued_run, queued_run_id
        )
        await asyncio.to_thread(list_fetcher_runs_filters_by_queued_status)
    finally:
        await cleanup(admin_id)


asyncio.run(main())
"""


@pytest.mark.image
def test_fetchers_api_read_paths_are_observable_in_built_image(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _FETCHERS_API_CHECK_SCRIPT)

    assert result.returncode == 0, (
        f"fetchers API smoke check failed "
        f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
    )
    assert "fetchers-list-anonymous-ok" in result.stdout
    assert "fetchers-runs-list-ok" in result.stdout
    assert "fetchers-runs-not-found-ok" in result.stdout
    assert "fetchers-run-detail-anonymous-ok" in result.stdout
    assert "fetchers-run-detail-admin-ok" in result.stdout
    assert "fetchers-timeline-ok" in result.stdout
    assert "fetchers-config-anonymous-401-ok" in result.stdout
    assert "fetchers-config-admin-ok" in result.stdout
    assert "fetchers-config-patch-anonymous-401-ok" in result.stdout
    assert "fetchers-config-patch-deregistered-409-ok" in result.stdout
    assert "fetchers-trigger-anonymous-401-ok" in result.stdout
    assert "fetchers-trigger-deregistered-409-ok" in result.stdout
    assert "fetchers-audit-log-anonymous-401-ok" in result.stdout
    assert "fetchers-audit-log-admin-ok" in result.stdout
    assert "fetchers-invalid-credential-401-ok" in result.stdout
    assert "fetchers-run-detail-queued-ok" in result.stdout
    assert "fetchers-runs-list-queued-ok" in result.stdout
