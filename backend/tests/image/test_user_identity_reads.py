"""Black-box image smoke assertions for the public user directory/profile,
current-user, and identity audit log endpoints.

Verifies — over a real ASGI server (uvicorn) and a real HTTP client
(`urllib`), the only combination that can observe this — representative
public, authenticated, and `manage_users`-gated read paths against the
built image. This is one representative scenario covering all five
endpoints defined by this work item; the exhaustive contract (filtering,
sorting, pagination, actor anonymization, error mapping) is already
covered by the in-process e2e suites (`tests/test_api/test_users.py`,
`tests/test_api/test_identity_audit.py`), which run far faster and do
not need a running container.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

_USER_IDENTITY_CHECK_SCRIPT = r"""
import asyncio
import json
import urllib.error
import urllib.request

from sqlalchemy import delete

from app.core.enums import IdentityAuditEventType, Role, SessionCreationReason
from app.database import async_session_factory
from app.models.identity_audit_event import IdentityAuditEvent
from app.models.session import Session
from app.models.user import User
from app.models.user_role import UserRole
from app.services.identity_audit_log import IdentityAuditLog
from app.services.session_service import create_session

_ORDINARY_USERNAME = "imageuserreadordinary"
_ADMIN_USERNAME = "imageuserreadadmin"


async def arrange():
    async with async_session_factory() as db:
        ordinary = User(
            username=_ORDINARY_USERNAME,
            email="imageuserreadordinary@example.com",
            full_name="Ordinary Reader",
            password_hash="$2b$12$" + "a" * 53,
        )
        admin = User(
            username=_ADMIN_USERNAME,
            email="imageuserreadadmin@example.com",
            password_hash="$2b$12$" + "a" * 53,
        )
        db.add_all([ordinary, admin])
        await db.flush()
        db.add(UserRole(user_id=admin.id, role=Role.ADMIN.value))
        await IdentityAuditLog.log_event(
            db,
            event_type=IdentityAuditEventType.USER_CREATED,
            user_id=None,
            target_user_id=ordinary.id,
            new_value=_ORDINARY_USERNAME,
        )
        await db.flush()
        ordinary_session = await create_session(
            db, ordinary, SessionCreationReason.LOCAL_LOGIN
        )
        admin_session = await create_session(
            db, admin, SessionCreationReason.LOCAL_LOGIN
        )
        await db.commit()
        return ordinary.id, admin.id, ordinary_session.token, admin_session.token


async def cleanup(ordinary_id, admin_id):
    async with async_session_factory() as db:
        await db.execute(
            delete(IdentityAuditEvent).where(
                (IdentityAuditEvent.target_user_id == ordinary_id)
                | (IdentityAuditEvent.user_id == ordinary_id)
                | (IdentityAuditEvent.target_user_id == admin_id)
                | (IdentityAuditEvent.user_id == admin_id)
            )
        )
        await db.execute(
            delete(UserRole).where(UserRole.user_id.in_([ordinary_id, admin_id]))
        )
        await db.execute(
            delete(Session).where(Session.user_id.in_([ordinary_id, admin_id]))
        )
        for user_id in (ordinary_id, admin_id):
            user = await db.get(User, user_id)
            if user is not None:
                await db.delete(user)
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


def public_list_users_succeeds():
    status, body = _request("GET", "/api/v1/users")
    assert status == 200, (status, body)
    assert "data" in body and "meta" in body
    print("user-list-public-ok")


def public_get_user_succeeds():
    status, body = _request("GET", f"/api/v1/users/{_ORDINARY_USERNAME}")
    assert status == 200, (status, body)
    assert body["data"]["username"] == _ORDINARY_USERNAME
    assert "last_login_at" not in body["data"]
    print("user-get-public-ok")


def get_current_user_profile(ordinary_token):
    status, body = _request("GET", "/api/v1/users/me", token=ordinary_token)
    assert status == 200, (status, body)
    assert body["data"]["username"] == _ORDINARY_USERNAME
    print("user-me-authenticated-ok")


def self_audit_log_is_anonymized(ordinary_token):
    status, body = _request(
        "GET", "/api/v1/users/me/audit-log", token=ordinary_token
    )
    assert status == 200, (status, body)
    assert body["meta"]["total"] >= 1
    assert body["data"][0]["actor"] == "system"
    print("user-me-audit-log-anonymized-ok")


def ordinary_user_denied_admin_audit_log(ordinary_token):
    status, body = _request(
        "GET", "/api/v1/admin/identity/audit-log", token=ordinary_token
    )
    assert status == 403, (status, body)
    assert body["code"] == "AUTH_INSUFFICIENT_PERMISSION"
    print("admin-audit-log-denied-ordinary-ok")


def admin_audit_log_succeeds(admin_token):
    status, body = _request(
        "GET", "/api/v1/admin/identity/audit-log", token=admin_token
    )
    assert status == 200, (status, body)
    assert body["meta"]["total"] >= 1
    print("admin-audit-log-admin-ok")


async def main():
    ordinary_id, admin_id, ordinary_token, admin_token = await arrange()
    try:
        await asyncio.to_thread(public_list_users_succeeds)
        await asyncio.to_thread(public_get_user_succeeds)
        await asyncio.to_thread(get_current_user_profile, ordinary_token)
        await asyncio.to_thread(self_audit_log_is_anonymized, ordinary_token)
        await asyncio.to_thread(
            ordinary_user_denied_admin_audit_log, ordinary_token
        )
        await asyncio.to_thread(admin_audit_log_succeeds, admin_token)
    finally:
        await cleanup(ordinary_id, admin_id)


asyncio.run(main())
"""


@pytest.mark.image
def test_user_identity_read_paths_are_observable_in_built_image(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _USER_IDENTITY_CHECK_SCRIPT)

    assert result.returncode == 0, (
        f"user/identity-audit read smoke check failed "
        f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
    )
    assert "user-list-public-ok" in result.stdout
    assert "user-get-public-ok" in result.stdout
    assert "user-me-authenticated-ok" in result.stdout
    assert "user-me-audit-log-anonymized-ok" in result.stdout
    assert "admin-audit-log-denied-ordinary-ok" in result.stdout
    assert "admin-audit-log-admin-ok" in result.stdout
