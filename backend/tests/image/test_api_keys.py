"""Black-box image smoke assertions for the API key management endpoints.

Verifies — over a real ASGI server (uvicorn) and a real HTTP client
(`urllib`), the only combination that can observe this — that the
create and revoke endpoints' database transaction commits before the
HTTP response is transmitted to the client. This is the guarantee
`scope="function"` provides for the `DatabaseSession` dependency (see
`docs/conventions.md`, API Transaction Dependency Scope): the script
queries the mutated rows on a separate database connection
*immediately* after receiving each response, with no polling or wait,
and asserts they are already visible/committed.

This is a single representative scenario covering all five endpoints
end-to-end inside the built image — the exhaustive contract (pagination,
filtering, sorting, error mapping) is already covered by the in-process
e2e suite (`tests/test_api/test_api_keys.py`), which runs far faster and
does not need a running container.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

_API_KEY_CHECK_SCRIPT = r"""
import asyncio
import json
import urllib.error
import urllib.request

from sqlalchemy import delete, select

from app.core.enums import Role, SessionCreationReason
from app.database import async_session_factory
from app.models.api_key import ApiKey
from app.models.identity_audit_event import IdentityAuditEvent
from app.models.session import Session
from app.models.user import User
from app.models.user_role import UserRole
from app.services.session_service import create_session

_OWNER_USERNAME = "imageapikeyowner"
_ADMIN_USERNAME = "imageapikeyadmin"


async def arrange():
    async with async_session_factory() as db:
        owner = User(
            username=_OWNER_USERNAME,
            email="imageapikeyowner@example.com",
            password_hash="$2b$12$" + "a" * 53,
        )
        admin = User(
            username=_ADMIN_USERNAME,
            email="imageapikeyadmin@example.com",
            password_hash="$2b$12$" + "a" * 53,
        )
        db.add_all([owner, admin])
        await db.flush()
        db.add(UserRole(user_id=admin.id, role=Role.ADMIN.value))
        await db.flush()
        owner_session = await create_session(
            db, owner, SessionCreationReason.LOCAL_LOGIN
        )
        admin_session = await create_session(
            db, admin, SessionCreationReason.LOCAL_LOGIN
        )
        await db.commit()
        return owner.id, admin.id, owner_session.token, admin_session.token


async def assert_key_immediately_visible(key_id):
    # No wait, no retry: the create response has already been received
    # by `create_key()` at this point, so `scope="function"` guarantees
    # the row is already committed and visible on this separate
    # connection.
    async with async_session_factory() as db:
        row = await db.get(ApiKey, key_id)
        assert row is not None, (
            "API key row not visible immediately after the create response "
            "— the transaction dependency did not commit before the "
            "response was transmitted."
        )


async def assert_revoked_immediately(key_id):
    async with async_session_factory() as db:
        row = await db.get(ApiKey, key_id)
        assert row is not None and row.revoked_at is not None, (
            "API key not revoked immediately after the revoke response "
            "— the transaction dependency did not commit before the "
            "response was transmitted."
        )


async def assert_audit_events_committed(owner_id):
    async with async_session_factory() as db:
        result = await db.execute(
            select(IdentityAuditEvent).where(
                IdentityAuditEvent.target_user_id == owner_id
            )
        )
        events = {e.event_type for e in result.scalars().all()}
        assert "api_key_created" in events
        assert "api_key_revoked" in events


async def cleanup(owner_id, admin_id):
    async with async_session_factory() as db:
        await db.execute(
            delete(IdentityAuditEvent).where(
                (IdentityAuditEvent.target_user_id == owner_id)
                | (IdentityAuditEvent.user_id == owner_id)
                | (IdentityAuditEvent.target_user_id == admin_id)
                | (IdentityAuditEvent.user_id == admin_id)
            )
        )
        await db.execute(
            delete(ApiKey).where(
                (ApiKey.user_id == owner_id)
                | (ApiKey.user_id == admin_id)
                | (ApiKey.revoked_by == owner_id)
                | (ApiKey.revoked_by == admin_id)
            )
        )
        await db.execute(
            delete(UserRole).where(UserRole.user_id.in_([owner_id, admin_id]))
        )
        await db.execute(
            delete(Session).where(Session.user_id.in_([owner_id, admin_id]))
        )
        for user_id in (owner_id, admin_id):
            user = await db.get(User, user_id)
            if user is not None:
                await db.delete(user)
        await db.commit()


def _request(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"http://localhost:8000{path}", method=method, data=data, headers=headers
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def self_service_list_starts_empty(owner_token):
    status, body = _request("GET", "/api/v1/api-keys", token=owner_token)
    assert status == 200, (status, body)
    assert body == {"data": [], "meta": {"total": 0, "page": 1, "per_page": 20}}
    print("api-key-list-empty-ok")


def create_key(owner_token):
    status, body = _request(
        "POST",
        "/api/v1/api-keys",
        token=owner_token,
        body={"name": "image-smoke-key"},
    )
    assert status == 201, (status, body)
    data = body["data"]
    assert data["key"].startswith("stl_ak_")
    assert data["name"] == "image-smoke-key"
    print("api-key-create-ok")
    return data["id"]


def self_service_revoke(owner_token, key_id):
    status, body = _request(
        "POST", f"/api/v1/api-keys/{key_id}/revoke", token=owner_token
    )
    assert status == 200, (status, body)
    assert body["data"]["status"] == "revoked"
    assert "key" not in body["data"]
    assert "key_hash" not in body["data"]
    print("api-key-self-revoke-ok")


def admin_list_with_owner_filter(admin_token, owner_id, owner_key_id):
    status, body = _request(
        "GET",
        f"/api/v1/admin/api-keys?owner={owner_id}",
        token=admin_token,
    )
    assert status == 200, (status, body)
    assert body["meta"]["total"] == 1
    item = body["data"][0]
    assert item["id"] == owner_key_id
    assert item["owner"]["id"] == owner_id
    assert "key" not in item
    assert "key_hash" not in item
    print("api-key-admin-list-owner-filter-ok")


def admin_creates_no_key_endpoint_but_can_revoke(admin_token, owner_token):
    # Create a second key for the admin revoke scenario.
    status, body = _request(
        "POST",
        "/api/v1/api-keys",
        token=owner_token,
        body={"name": "image-smoke-key-2"},
    )
    assert status == 201, (status, body)
    second_key_id = body["data"]["id"]

    status, body = _request(
        "POST", f"/api/v1/admin/api-keys/{second_key_id}/revoke", token=admin_token
    )
    assert status == 200, (status, body)
    assert body["data"]["status"] == "revoked"
    assert body["data"]["revoked_by"] is not None
    print("api-key-admin-revoke-ok")
    return second_key_id


async def main():
    owner_id, admin_id, owner_token, admin_token = await arrange()
    try:
        await asyncio.to_thread(self_service_list_starts_empty, owner_token)
        key_id = await asyncio.to_thread(create_key, owner_token)
        await assert_key_immediately_visible(key_id)
        print("api-key-create-immediately-visible-ok")
        await asyncio.to_thread(self_service_revoke, owner_token, key_id)
        await assert_revoked_immediately(key_id)
        print("api-key-revoke-immediately-visible-ok")
        await asyncio.to_thread(
            admin_list_with_owner_filter, admin_token, str(owner_id), key_id
        )
        await asyncio.to_thread(
            admin_creates_no_key_endpoint_but_can_revoke, admin_token, owner_token
        )
        await assert_audit_events_committed(owner_id)
        print("api-key-audit-events-committed-ok")
    finally:
        await cleanup(owner_id, admin_id)


asyncio.run(main())
"""


@pytest.mark.image
def test_api_key_lifecycle_is_observable_in_built_image(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _API_KEY_CHECK_SCRIPT)

    assert result.returncode == 0, (
        f"api-key smoke check failed (stdout={result.stdout!r}, "
        f"stderr={result.stderr!r})"
    )
    assert "api-key-list-empty-ok" in result.stdout
    assert "api-key-create-ok" in result.stdout
    assert "api-key-create-immediately-visible-ok" in result.stdout
    assert "api-key-self-revoke-ok" in result.stdout
    assert "api-key-revoke-immediately-visible-ok" in result.stdout
    assert "api-key-admin-list-owner-filter-ok" in result.stdout
    assert "api-key-admin-revoke-ok" in result.stdout
    assert "api-key-audit-events-committed-ok" in result.stdout
