"""Black-box image smoke assertions for the system settings read and
audit log endpoints (`backend/app/api/v1/settings.py`).

Verifies — over a real ASGI server (uvicorn) and a real HTTP client
(`urllib`), the only combination that can observe this — representative
authorized responses from both `GET /api/v1/admin/settings` and
`GET /api/v1/admin/settings/audit-log` against the built image. This is
one representative scenario; the exhaustive contract (filtering,
sorting, pagination, actor resolution, error mapping) is already covered
by the in-process e2e suite (`tests/test_api/test_settings.py`), which
runs far faster and does not need a running container.

See docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing, Growth Rule — System Settings Growth Requirements).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

_SETTINGS_API_CHECK_SCRIPT = r"""
import asyncio
import json
import urllib.error
import urllib.request

from sqlalchemy import delete

from app.core.enums import Role, SessionCreationReason, SettingAuditEventType
from app.database import async_session_factory
from app.models.session import Session
from app.models.setting_audit_event import SettingAuditEvent
from app.models.user import User
from app.models.user_role import UserRole
from app.services.session_service import create_session
from app.services.settings import SettingAuditLog

_ADMIN_USERNAME = "imagesettingsapiadmin"


async def arrange():
    async with async_session_factory() as db:
        admin = User(
            username=_ADMIN_USERNAME,
            email="imagesettingsapiadmin@example.com",
            password_hash="$2b$12$" + "a" * 53,
        )
        db.add(admin)
        await db.flush()
        db.add(UserRole(user_id=admin.id, role=Role.ADMIN.value))
        await SettingAuditLog.log_event(
            db,
            event_type=SettingAuditEventType.SETTING_CHANGED,
            setting_key="default_cvss_version",
            user_id=admin.id,
            old_value="3.1",
            new_value="3.1",
        )
        await db.flush()
        admin_session = await create_session(
            db, admin, SessionCreationReason.LOCAL_LOGIN
        )
        await db.commit()
        return admin.id, admin_session.token


async def cleanup(admin_id):
    async with async_session_factory() as db:
        await db.execute(
            delete(SettingAuditEvent).where(SettingAuditEvent.user_id == admin_id)
        )
        await db.execute(
            delete(UserRole).where(UserRole.user_id == admin_id)
        )
        await db.execute(delete(Session).where(Session.user_id == admin_id))
        admin = await db.get(User, admin_id)
        if admin is not None:
            await db.delete(admin)
        await db.commit()


def _request(method, path, token=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://localhost:8000{path}", method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def get_system_settings_succeeds(admin_token):
    status, body = _request(
        "GET", "/api/v1/admin/settings", token=admin_token
    )
    assert status == 200, (status, body)
    assert body["data"]["default_cvss_version"] in {"3.1", "4.0"}
    print("settings-get-admin-ok")


def get_system_settings_unauthenticated_denied():
    status, body = _request("GET", "/api/v1/admin/settings")
    assert status == 401, (status, body)
    print("settings-get-unauthenticated-denied-ok")


def list_settings_audit_log_succeeds(admin_token):
    status, body = _request(
        "GET", "/api/v1/admin/settings/audit-log", token=admin_token
    )
    assert status == 200, (status, body)
    assert "data" in body and "meta" in body
    assert body["meta"]["total"] >= 1
    event = body["data"][0]
    assert event["actor"] is not None
    assert event["actor"]["username"] == _ADMIN_USERNAME
    print("settings-audit-log-admin-ok")


def settings_audit_log_unauthenticated_denied():
    status, body = _request("GET", "/api/v1/admin/settings/audit-log")
    assert status == 401, (status, body)
    print("settings-audit-log-unauthenticated-denied-ok")


async def main():
    admin_id, admin_token = await arrange()
    try:
        await asyncio.to_thread(get_system_settings_succeeds, admin_token)
        await asyncio.to_thread(get_system_settings_unauthenticated_denied)
        await asyncio.to_thread(list_settings_audit_log_succeeds, admin_token)
        await asyncio.to_thread(settings_audit_log_unauthenticated_denied)
    finally:
        await cleanup(admin_id)


asyncio.run(main())
"""


@pytest.mark.image
def test_settings_api_read_paths_are_observable_in_built_image(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _SETTINGS_API_CHECK_SCRIPT)

    assert result.returncode == 0, (
        f"settings API smoke check failed "
        f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
    )
    assert "settings-get-admin-ok" in result.stdout
    assert "settings-get-unauthenticated-denied-ok" in result.stdout
    assert "settings-audit-log-admin-ok" in result.stdout
    assert "settings-audit-log-unauthenticated-denied-ok" in result.stdout
