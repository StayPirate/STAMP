"""Black-box image smoke assertions for the ticket-independent admin user
mutation endpoints (`POST /api/v1/admin/users`,
`PATCH /api/v1/admin/users/{user}`,
`POST /api/v1/admin/users/{user}/reactivate`,
`POST /api/v1/admin/users/{user}/password`,
`POST /api/v1/admin/users/{user}/unlock`).

Verifies — over a real ASGI server (uvicorn) and a real HTTP client
(`urllib`), the only combination that can observe this — one
representative scenario per endpoint class against the built image. The
exhaustive contract (validation, conflicts, external-user guards,
idempotency, audit atomicity, Redis post-commit behavior) is already
covered by the in-process e2e and integration suites
(`tests/test_api/test_users.py`, `tests/test_services/test_user_service.py`),
which run far faster and do not need a running container.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

_ADMIN_USER_MUTATIONS_CHECK_SCRIPT = r"""
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
from app.services.session_service import create_session

_ADMIN_USERNAME = "imageadminmutationadmin"
_ORDINARY_USERNAME = "imageadminmutationordinary"
_CREATED_USERNAME = "imageadminmutationcreated"


async def arrange():
    async with async_session_factory() as db:
        admin = User(
            username=_ADMIN_USERNAME,
            email="imageadminmutationadmin@example.com",
            password_hash="$2b$12$" + "a" * 53,
        )
        ordinary = User(
            username=_ORDINARY_USERNAME,
            email="imageadminmutationordinary@example.com",
            password_hash="$2b$12$" + "a" * 53,
        )
        db.add_all([admin, ordinary])
        await db.flush()
        db.add(UserRole(user_id=admin.id, role=Role.ADMIN.value))
        await db.flush()
        admin_session = await create_session(
            db, admin, SessionCreationReason.LOCAL_LOGIN
        )
        ordinary_session = await create_session(
            db, ordinary, SessionCreationReason.LOCAL_LOGIN
        )
        await db.commit()
        return admin.id, ordinary.id, admin_session.token, ordinary_session.token


async def cleanup(admin_id, ordinary_id, created_id):
    async with async_session_factory() as db:
        target_ids = [admin_id, ordinary_id]
        if created_id is not None:
            target_ids.append(created_id)
        await db.execute(
            delete(IdentityAuditEvent).where(
                IdentityAuditEvent.target_user_id.in_(target_ids)
                | IdentityAuditEvent.user_id.in_(target_ids)
            )
        )
        await db.execute(delete(UserRole).where(UserRole.user_id.in_(target_ids)))
        await db.execute(delete(Session).where(Session.user_id.in_(target_ids)))
        for user_id in target_ids:
            user = await db.get(User, user_id)
            if user is not None:
                await db.delete(user)
        await db.commit()


def _request(method, path, token=None, body=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
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


def ordinary_user_denied_create(ordinary_token):
    status, body = _request(
        "POST",
        "/api/v1/admin/users",
        token=ordinary_token,
        body={
            "username": "shouldneverexist",
            "email": "shouldneverexist@example.com",
            "password": "a-fictional-password-value",
        },
    )
    assert status == 403, (status, body)
    assert body["code"] == "AUTH_INSUFFICIENT_PERMISSION"
    print("admin-user-create-denied-ordinary-ok")


def create_user_succeeds(admin_token):
    status, body = _request(
        "POST",
        "/api/v1/admin/users",
        token=admin_token,
        body={
            "username": _CREATED_USERNAME,
            "email": f"{_CREATED_USERNAME}@example.com",
            "full_name": "Image Smoke Created User",
            "password": "a-fictional-password-value",
        },
    )
    assert status == 201, (status, body)
    assert body["data"]["username"] == _CREATED_USERNAME
    assert "password" not in body["data"]
    assert "password_hash" not in body["data"]
    print("admin-user-create-ok")
    return body["data"]["id"]


def update_user_succeeds(admin_token, created_id):
    status, body = _request(
        "PATCH",
        f"/api/v1/admin/users/{created_id}",
        token=admin_token,
        body={"full_name": "Updated Image Smoke Name"},
    )
    assert status == 200, (status, body)
    assert body["data"]["full_name"] == "Updated Image Smoke Name"
    print("admin-user-update-ok")


def reactivate_user_succeeds(admin_token, created_id):
    status, body = _request(
        "POST", f"/api/v1/admin/users/{created_id}/reactivate", token=admin_token
    )
    assert status == 200, (status, body)
    assert body["data"]["active"] is True
    print("admin-user-reactivate-ok")


def reset_password_succeeds(admin_token, created_id):
    status, body = _request(
        "POST",
        f"/api/v1/admin/users/{created_id}/password",
        token=admin_token,
        body={"password": "a-new-fictional-password"},
    )
    assert status == 200, (status, body)
    assert body["data"] == {
        "detail": "Password updated. All active sessions have been invalidated."
    }
    print("admin-user-password-ok")


def unlock_user_succeeds(admin_token, created_id):
    status, body = _request(
        "POST", f"/api/v1/admin/users/{created_id}/unlock", token=admin_token
    )
    assert status == 200, (status, body)
    assert body["data"] == {"detail": "Account unlocked successfully."}
    print("admin-user-unlock-ok")


async def main():
    admin_id, ordinary_id, admin_token, ordinary_token = await arrange()
    created_id = None
    try:
        await asyncio.to_thread(ordinary_user_denied_create, ordinary_token)
        created_id = await asyncio.to_thread(create_user_succeeds, admin_token)
        await asyncio.to_thread(update_user_succeeds, admin_token, created_id)
        await asyncio.to_thread(reactivate_user_succeeds, admin_token, created_id)
        await asyncio.to_thread(reset_password_succeeds, admin_token, created_id)
        await asyncio.to_thread(unlock_user_succeeds, admin_token, created_id)
    finally:
        await cleanup(admin_id, ordinary_id, created_id)


asyncio.run(main())
"""


@pytest.mark.image
def test_admin_user_mutation_paths_are_observable_in_built_image(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _ADMIN_USER_MUTATIONS_CHECK_SCRIPT)

    assert result.returncode == 0, (
        f"admin user mutation smoke check failed "
        f"(stdout={result.stdout!r}, stderr={result.stderr!r})"
    )
    assert "admin-user-create-denied-ordinary-ok" in result.stdout
    assert "admin-user-create-ok" in result.stdout
    assert "admin-user-update-ok" in result.stdout
    assert "admin-user-reactivate-ok" in result.stdout
    assert "admin-user-password-ok" in result.stdout
    assert "admin-user-unlock-ok" in result.stdout
