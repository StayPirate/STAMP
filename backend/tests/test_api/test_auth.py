"""End-to-end tests for ``POST /api/v1/auth/logout``."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import redis.asyncio as redis_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.enums import SessionCreationReason
from app.core.jwt import ALGORITHM, ISSUER
from app.database import get_db
from app.main import app
from app.models.session import Session
from app.models.user import User
from app.services.session_service import create_session


@pytest.fixture
async def logout_client(
    db_session: AsyncSession,
    redis_client: redis_asyncio.Redis,
) -> AsyncGenerator[AsyncClient]:
    """Exercise the production commit-before-callback ordering.

    The shared client fixture intentionally keeps one rollback-owned test
    transaction and therefore does not execute ``get_db`` post-yield logic.
    Logout specifically needs that lifecycle to verify its cache purge.
    """

    async def _override_get_db() -> AsyncGenerator[AsyncSession]:
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise
        else:
            callbacks = db_session.info.pop("post_commit_callbacks", [])
            for callback in callbacks:
                await callback()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


async def _created_session(
    db_session: AsyncSession,
    user_factory: Callable[..., Awaitable[User]],
) -> tuple[Session, str]:
    user = await user_factory()
    created = await create_session(db_session, user, SessionCreationReason.LOCAL_LOGIN)
    return created.session, created.token


def _expired_token(session: Session) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(session.user_id),
        "session_id": str(session.id),
        "iat": int((now - timedelta(days=3)).timestamp()),
        "exp": int((now - timedelta(days=2)).timestamp()),
        "session_deadline": int((now - timedelta(days=1)).timestamp()),
        "iss": ISSUER,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=ALGORITHM,
    )


@pytest.mark.e2e
class TestLogout:
    async def test_cookie_without_authorization_header_logs_out(
        self,
        logout_client: AsyncClient,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        _session, token = await _created_session(db_session, user_factory)
        logout_client.cookies.set("sentinel_session", token)

        response = await logout_client.post("/api/v1/auth/logout")

        assert response.status_code == 204

    async def test_valid_bearer_invalidates_session_purges_cache_and_clears_cookie(
        self,
        logout_client: AsyncClient,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        redis_client: redis_asyncio.Redis,
    ) -> None:
        session, token = await _created_session(db_session, user_factory)
        key = f"session_liveness:{session.id}"
        await redis_client.set(key, "1", ex=60)

        response = await logout_client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["set-cookie"] == (
            "sentinel_session=; Path=/api; Max-Age=0; HttpOnly; Secure; SameSite=Strict"
        )
        await db_session.refresh(session)
        assert session.is_active is False
        assert await redis_client.get(key) is None

    async def test_repeated_logout_and_missing_session_are_idempotent(
        self,
        logout_client: AsyncClient,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        session, token = await _created_session(db_session, user_factory)

        first = await logout_client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )
        second = await logout_client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )
        await db_session.delete(session)
        await db_session.flush()
        missing = await logout_client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )

        assert first.status_code == second.status_code == missing.status_code == 204

    async def test_temporally_expired_signed_token_is_accepted(
        self,
        logout_client: AsyncClient,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        session, _ = await _created_session(db_session, user_factory)

        response = await logout_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {_expired_token(session)}"},
        )

        assert response.status_code == 204

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer malformed-token"},
            {"Authorization": "Bearer "},
        ],
    )
    async def test_missing_or_invalid_jwt_returns_generic_401(
        self, logout_client: AsyncClient, headers: dict[str, str]
    ) -> None:
        response = await logout_client.post("/api/v1/auth/logout", headers=headers)

        assert response.status_code == 401
        assert response.json() == {
            "code": "AUTH_NOT_AUTHENTICATED",
            "detail": "Authentication required",
        }

    async def test_api_key_returns_logout_not_applicable(
        self, logout_client: AsyncClient
    ) -> None:
        response = await logout_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "Bearer stl_ak_fictional-key"},
        )

        assert response.status_code == 400
        assert response.json() == {
            "code": "AUTH_LOGOUT_NOT_APPLICABLE",
            "detail": "Logout is not applicable to API key authentication.",
        }

    @pytest.mark.parametrize(
        "authorization",
        ["Bearer", "Bearer   ", "Basic fictional", "scheme-less"],
    )
    async def test_empty_or_non_bearer_header_falls_back_to_cookie(
        self,
        logout_client: AsyncClient,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        authorization: str,
    ) -> None:
        _session, token = await _created_session(db_session, user_factory)
        logout_client.cookies.set("sentinel_session", token)

        response = await logout_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": authorization},
        )

        assert response.status_code == 204

    async def test_bearer_is_case_insensitive_and_takes_precedence_over_cookie(
        self,
        logout_client: AsyncClient,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        _session, cookie_token = await _created_session(db_session, user_factory)
        logout_client.cookies.set("sentinel_session", cookie_token)

        response = await logout_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": "bEaReR malformed-token"},
        )

        assert response.status_code == 401
