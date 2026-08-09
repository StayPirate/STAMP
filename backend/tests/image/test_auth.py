"""Black-box image smoke assertions for the login and logout endpoints.

Both scripts below verify — over a real ASGI server (uvicorn) and a
real HTTP client (`urllib`), the only combination that can observe this
— that the login/logout endpoints' database transaction commits before
the HTTP response is transmitted to the client. This is the guarantee
`scope="function"` provides for the `DatabaseSession` dependency (see
`docs/conventions.md`, API Transaction Dependency Scope): each script
queries the mutated row on a separate database connection *immediately*
after receiving the response, with no polling or wait, and asserts it
is already visible/updated.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

_LOGIN_CHECK_SCRIPT = r"""
import asyncio
import json
import urllib.error
import urllib.request

import redis.asyncio as redis_asyncio
from sqlalchemy import delete, select

from app.config import settings
from app.core.passwords import hash_password
from app.database import async_session_factory
from app.models.session import Session
from app.models.user import User

_USERNAME = "imageloginuser"
_PASSWORD = "image-smoke-login-password-1"


async def arrange():
    async with async_session_factory() as db:
        user = User(
            username=_USERNAME,
            email="imageloginuser@example.com",
            password_hash=hash_password(_PASSWORD),
        )
        db.add(user)
        await db.flush()
        await db.commit()
        return user.id


async def assert_session_immediately_visible(user_id):
    # No wait, no retry: the login response has already been received
    # by `happy_path()` at this point, so `scope="function"` guarantees
    # the session row is already committed and visible on this separate
    # connection.
    async with async_session_factory() as db:
        result = await db.execute(select(Session).where(Session.user_id == user_id))
        assert result.scalars().first() is not None, (
            "Session row not visible immediately after the login response "
            "— the transaction dependency did not commit before the "
            "response was transmitted."
        )


async def cleanup(user_id):
    async with async_session_factory() as db:
        await db.execute(delete(Session).where(Session.user_id == user_id))
        user = await db.get(User, user_id)
        if user is not None:
            await db.delete(user)
        await db.commit()

    client = redis_asyncio.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.delete(f"login_attempts:{_USERNAME}")
    finally:
        await client.aclose()


def _post_login(username, password):
    request = urllib.request.Request(
        "http://localhost:8000/api/v1/auth/login",
        method="POST",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            # `response.headers` (an `email.message.Message`) supports
            # case-insensitive lookup directly — converting to a plain
            # `dict` would lose that and break lookups against
            # lowercase header names as commonly emitted by ASGI
            # servers (e.g. `set-cookie` instead of `Set-Cookie`).
            return response.status, response.headers, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, json.loads(exc.read())


def happy_path():
    status, headers, body = _post_login(_USERNAME, _PASSWORD)
    assert status == 200, (status, body)
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["access_token"]
    cookie = headers["Set-Cookie"]
    assert cookie.startswith(f"sentinel_session={body['data']['access_token']}; ")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    print("login-happy-path-ok")


def generic_401():
    status, _headers, body = _post_login(_USERNAME, "definitely-the-wrong-password")
    assert status == 401, (status, body)
    assert body == {
        "code": "AUTH_INVALID_CREDENTIALS",
        "detail": "Invalid username or password.",
    }
    print("login-generic-401-ok")


def lockout():
    last_status, last_headers, last_body = None, None, None
    for _ in range(settings.login_max_attempts + 1):
        last_status, last_headers, last_body = _post_login(
            _USERNAME, "definitely-the-wrong-password"
        )
    assert last_status == 429, (last_status, last_body)
    assert last_body["code"] == "AUTH_ACCOUNT_LOCKED"
    assert int(last_headers["Retry-After"]) >= 1
    print("login-lockout-ok")


async def main():
    user_id = await arrange()
    try:
        await asyncio.to_thread(happy_path)
        await assert_session_immediately_visible(user_id)
        print("login-session-immediately-visible-ok")
        await asyncio.to_thread(generic_401)
        await asyncio.to_thread(lockout)
    finally:
        await cleanup(user_id)


asyncio.run(main())
"""

_LOGOUT_CHECK_SCRIPT = r"""
import asyncio
import urllib.request

from sqlalchemy import delete

from app.core.enums import SessionCreationReason
from app.database import async_session_factory
from app.models.session import Session
from app.models.user import User
from app.services.session_service import create_session


async def arrange():
    async with async_session_factory() as db:
        user = User(
            username="imageuser",
            email="imageuser@example.com",
            password_hash="$2b$12$" + "a" * 53,
        )
        db.add(user)
        await db.flush()
        created = await create_session(db, user, SessionCreationReason.LOCAL_LOGIN)
        await db.commit()
        return user.id, created.session.id, created.token


async def assert_session_immediately_inactive(session_id):
    # No wait, no retry: `request_logout()` has already received the
    # 204 response at this point, so `scope="function"` guarantees the
    # session row is already committed inactive and visible as such on
    # this separate connection.
    async with async_session_factory() as db:
        session = await db.get(Session, session_id)
        assert session is not None
        assert session.is_active is False, (
            "Session still active immediately after the logout response "
            "— the transaction dependency did not commit before the "
            "response was transmitted."
        )


async def cleanup(user_id):
    async with async_session_factory() as db:
        await db.execute(delete(Session).where(Session.user_id == user_id))
        user = await db.get(User, user_id)
        if user is not None:
            await db.delete(user)
        await db.commit()


def request_logout(token):
    request = urllib.request.Request(
        "http://localhost:8000/api/v1/auth/logout",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request) as response:
        assert response.status == 204, response.status
        assert response.read() == b""
        assert response.headers["Set-Cookie"] == (
            "sentinel_session=; Path=/api; Max-Age=0; "
            "HttpOnly; Secure; SameSite=Strict"
        )


async def main():
    user_id, session_id, token = await arrange()
    try:
        await asyncio.to_thread(request_logout, token)
        print("logout-ok")
        await assert_session_immediately_inactive(session_id)
        print("logout-session-immediately-inactive-ok")
    finally:
        await cleanup(user_id)


asyncio.run(main())
"""


@pytest.mark.image
def test_login_happy_path_generic_401_and_lockout_are_observable_in_built_image(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _LOGIN_CHECK_SCRIPT)

    assert result.returncode == 0, (
        f"login smoke check failed (stdout={result.stdout!r}, stderr={result.stderr!r})"
    )
    assert "login-happy-path-ok" in result.stdout
    assert "login-session-immediately-visible-ok" in result.stdout
    assert "login-generic-401-ok" in result.stdout
    assert "login-lockout-ok" in result.stdout


@pytest.mark.image
def test_logout_route_is_observable_in_built_image(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _LOGOUT_CHECK_SCRIPT)

    assert result.returncode == 0, (
        f"logout smoke check failed (stdout={result.stdout!r}, "
        f"stderr={result.stderr!r})"
    )
    assert "logout-ok" in result.stdout
    assert "logout-session-immediately-inactive-ok" in result.stdout
