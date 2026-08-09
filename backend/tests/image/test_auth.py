"""Black-box image smoke assertions for the logout endpoint."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

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
        return user.id, created.token


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
    user_id, token = await arrange()
    try:
        await asyncio.to_thread(request_logout, token)
        print("logout-ok")
    finally:
        await cleanup(user_id)


asyncio.run(main())
"""


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
