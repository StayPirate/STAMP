"""End-to-end tests for public user directory/profile and current-user
endpoints (`backend/app/api/v1/users.py`).

See `docs/features/identity/user-management.md` (List Users, Get User)
and `docs/features/identity/authentication.md` (Get Current User) for
the authoritative endpoint contracts under test. Filter/sort/pagination
combination coverage for the underlying query lives in
`tests/test_services/test_user_service.py` — these tests focus on the
HTTP/route contract (status codes, envelopes, error mapping,
authentication, and response field shape).
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.api.v1.users import _parse_role_filters, _parse_user_type
from app.core.enums import Role, UserType
from app.models.api_key import ApiKey
from app.models.user import User
from app.models.user_role import UserRole
from app.services import api_key_service

# ---------------------------------------------------------------------------
# Shared helpers and fixtures
# ---------------------------------------------------------------------------


def _make_api_key_credential() -> tuple[str, str]:
    """Return `(plaintext_token, sha256_hex_digest)` for a synthetic key.

    Mirrors the identical helper in `tests/test_api/test_api_keys.py`
    and `tests/test_api/test_dependencies.py`.
    """
    token = "stl_ak_" + secrets.token_hex(16)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token, digest


@pytest.fixture
def authenticated_user_and_client(
    _authenticated_user_and_client: tuple[User, AsyncClient],
) -> tuple[User, AsyncClient]:
    """Non-underscore-prefixed alias for `conftest.py`'s
    `_authenticated_user_and_client`, mirroring
    `tests/test_api/test_api_keys.py`."""
    return _authenticated_user_and_client


# ---------------------------------------------------------------------------
# Pure helpers (`app.api.v1.users`)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseUserType:
    def test_absent_is_valid_with_no_filter(self) -> None:
        assert _parse_user_type(None) == (True, None)

    def test_valid_local_is_accepted(self) -> None:
        assert _parse_user_type("local") == (True, UserType.LOCAL)

    def test_valid_external_is_accepted(self) -> None:
        assert _parse_user_type("external") == (True, UserType.EXTERNAL)

    def test_invalid_value_is_rejected(self) -> None:
        assert _parse_user_type("bogus") == (False, None)


@pytest.mark.unit
class TestParseRoleFilters:
    def test_absent_is_valid_with_no_filter(self) -> None:
        assert _parse_role_filters([]) == (True, [])

    def test_all_valid_values_are_kept(self) -> None:
        is_valid, roles = _parse_role_filters(["admin", "restricted_analyst"])
        assert is_valid is True
        assert set(roles) == {Role.ADMIN, Role.RESTRICTED_ANALYST}

    def test_mixed_valid_and_invalid_keeps_only_valid(self) -> None:
        is_valid, roles = _parse_role_filters(["admin", "not-a-real-role"])
        assert is_valid is True
        assert roles == [Role.ADMIN]

    def test_all_invalid_is_rejected(self) -> None:
        assert _parse_role_filters(["not-a-real-role"]) == (False, [])


# ---------------------------------------------------------------------------
# GET /api/v1/users
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestListUsers:
    async def test_public_access_without_credentials(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/users")
        assert response.status_code == 200

    async def test_empty_result_has_correct_envelope(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/users")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"] == {"total": 0, "page": 1, "per_page": 20}

    async def test_full_profile_item_shape(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory(username="jdoe", email="jdoe@example.com")

        response = await client.get("/api/v1/users")

        item = response.json()["data"][0]
        assert set(item.keys()) == {
            "id",
            "username",
            "email",
            "full_name",
            "active",
            "source",
            "external_id",
            "manager",
            "roles",
            "created_at",
            "updated_at",
        }

    async def test_datetimes_end_with_z(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory()

        response = await client.get("/api/v1/users")

        item = response.json()["data"][0]
        assert item["created_at"].endswith("Z")
        assert item["updated_at"].endswith("Z")

    async def test_search_below_minimum_length_returns_422(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/users", params={"search": "j"})
        assert response.status_code == 422

    async def test_invalid_active_boolean_returns_422(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/users", params={"active": "maybe"})
        assert response.status_code == 422

    async def test_page_below_one_returns_422(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/users", params={"page": 0})
        assert response.status_code == 422

    async def test_per_page_out_of_range_returns_422(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/users", params={"per_page": 101})
        assert response.status_code == 422

    async def test_invalid_sort_by_returns_422(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/users", params={"sort_by": "password"})
        assert response.status_code == 422

    async def test_invalid_sort_order_returns_422(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/users", params={"sort_order": "ascending"})
        assert response.status_code == 422

    async def test_invalid_type_filter_returns_empty_result(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory()

        response = await client.get("/api/v1/users", params={"type": "bogus"})

        assert response.status_code == 200
        assert response.json()["meta"]["total"] == 0

    async def test_all_invalid_role_values_return_empty_result(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory()

        response = await client.get(
            "/api/v1/users", params=[("role", "not-a-real-role")]
        )

        assert response.status_code == 200
        assert response.json()["meta"]["total"] == 0

    async def test_repeatable_role_filter_uses_or_semantics(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
        user_role_factory: Callable[..., Awaitable[UserRole]],
    ) -> None:
        admin_user = await user_factory(username="repeatadmin")
        await user_role_factory(user_id=admin_user.id, role=Role.ADMIN.value)
        analyst_user = await user_factory(username="repeatanalyst")
        await user_role_factory(
            user_id=analyst_user.id, role=Role.VULNERABILITY_ANALYST.value
        )
        await user_factory(username="repeatnorole")

        response = await client.get(
            "/api/v1/users",
            params=[("role", "admin"), ("role", "vulnerability_analyst")],
        )

        assert response.status_code == 200
        usernames = {item["username"] for item in response.json()["data"]}
        assert usernames == {"repeatadmin", "repeatanalyst"}

    async def test_page_beyond_result_set_returns_empty_data_with_correct_total(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory()

        response = await client.get("/api/v1/users", params={"page": 2})

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 1

    async def test_no_undocumented_fields_leak(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory()

        response = await client.get("/api/v1/users")

        item = response.json()["data"][0]
        assert "password_hash" not in item
        assert "last_login_at" not in item
        assert "synced_at" not in item


# ---------------------------------------------------------------------------
# GET /api/v1/users/{user}
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestGetUser:
    async def test_public_access_without_credentials(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory(username="publicaccess")

        response = await client.get(f"/api/v1/users/{user.username}")

        assert response.status_code == 200

    async def test_resolves_by_uuid(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory(username="byuuidtest")

        response = await client.get(f"/api/v1/users/{user.id}")

        assert response.status_code == 200
        assert response.json()["data"]["username"] == "byuuidtest"

    async def test_resolves_by_exact_username(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory(username="byusernametest")

        response = await client.get(f"/api/v1/users/{user.username}")

        assert response.status_code == 200
        assert response.json()["data"]["id"] == str(user.id)

    async def test_missing_uuid_returns_404_user_not_found(
        self, client: AsyncClient
    ) -> None:
        response = await client.get(f"/api/v1/users/{uuid4()}")
        assert response.status_code == 404
        assert response.json()["code"] == "USER_NOT_FOUND"

    async def test_missing_username_returns_404_user_not_found(
        self, client: AsyncClient
    ) -> None:
        response = await client.get("/api/v1/users/no-such-user")
        assert response.status_code == 404
        assert response.json()["code"] == "USER_NOT_FOUND"

    async def test_manager_and_roles_are_fully_rendered(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
        user_role_factory: Callable[..., Awaitable[UserRole]],
    ) -> None:
        manager = await user_factory(username="managerforprofile")
        user = await user_factory(username="profiletarget", manager_id=manager.id)
        await user_role_factory(user_id=user.id, role=Role.ADMIN.value)

        response = await client.get(f"/api/v1/users/{user.username}")

        data = response.json()["data"]
        assert data["manager"]["username"] == "managerforprofile"
        assert data["manager"]["email"] == manager.email
        assert data["roles"] == [
            {
                "role": "admin",
                "group_name": "_manual",
                "assigned_by": None,
                "created_at": data["roles"][0]["created_at"],
            }
        ]

    async def test_external_user_has_external_source(
        self, client: AsyncClient, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        external_id = uuid4()
        user = await user_factory(
            username="externaltest", external_id=external_id, password_hash=None
        )

        response = await client.get(f"/api/v1/users/{user.username}")

        data = response.json()["data"]
        assert data["source"] == "external"
        assert data["external_id"] == str(external_id)

    async def test_last_login_at_is_never_exposed(
        self, client: AsyncClient, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory(username="loginhiddentest")

        response = await client.get(f"/api/v1/users/{user.username}")

        assert "last_login_at" not in response.json()["data"]

    async def test_password_hash_is_never_exposed(
        self, client: AsyncClient, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory(username="passwordhiddentest")

        response = await client.get(f"/api/v1/users/{user.username}")

        assert "password_hash" not in response.json()["data"]


# ---------------------------------------------------------------------------
# GET /api/v1/users/me
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestGetCurrentUserProfile:
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/users/me")
        assert response.status_code == 401
        assert response.json()["code"] == "AUTH_NOT_AUTHENTICATED"

    async def test_jwt_session_returns_own_profile(
        self, authenticated_user_and_client: tuple[User, AsyncClient]
    ) -> None:
        user, client = authenticated_user_and_client

        response = await client.get("/api/v1/users/me")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["id"] == str(user.id)
        assert data["username"] == user.username

    async def test_exact_concise_schema(
        self, authenticated_user_and_client: tuple[User, AsyncClient]
    ) -> None:
        _user, client = authenticated_user_and_client

        response = await client.get("/api/v1/users/me")

        data = response.json()["data"]
        assert set(data.keys()) == {
            "id",
            "username",
            "email",
            "full_name",
            "roles",
            "active",
        }

    async def test_roles_are_distinct_and_wire_sorted(
        self,
        authenticated_user_and_client: tuple[User, AsyncClient],
        user_role_factory: Callable[..., Awaitable[UserRole]],
    ) -> None:
        user, client = authenticated_user_and_client
        await user_role_factory(
            user_id=user.id, role=Role.VULNERABILITY_ANALYST.value, group_name="_manual"
        )
        await user_role_factory(
            user_id=user.id,
            role=Role.VULNERABILITY_ANALYST.value,
            group_name="O SUSE Security",
        )
        await user_role_factory(user_id=user.id, role=Role.ADMIN.value)

        response = await client.get("/api/v1/users/me")

        assert response.json()["data"]["roles"] == ["admin", "vulnerability_analyst"]

    async def test_api_key_authentication_is_accepted(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
        api_key_factory: Callable[..., Awaitable[ApiKey]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await user_factory(username="apikeyprofile")
        token, digest = _make_api_key_credential()
        await api_key_factory(user_id=user.id, key_hash=digest)
        monkeypatch.setattr(
            api_key_service, "update_last_used_at", AsyncMock(return_value=True)
        )

        response = await client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.json()["data"]["username"] == "apikeyprofile"

    async def test_has_no_full_profile_only_fields(
        self, authenticated_user_and_client: tuple[User, AsyncClient]
    ) -> None:
        _user, client = authenticated_user_and_client

        response = await client.get("/api/v1/users/me")

        data = response.json()["data"]
        assert "source" not in data
        assert "external_id" not in data
        assert "manager" not in data

    async def test_static_route_takes_precedence_over_dynamic_username_route(
        self,
        client: AsyncClient,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """A stored user literally named `me` must not capture the
        static `/users/me` route — but the public `/users/{user}` route
        remains reachable for that username."""
        await user_factory(username="me")

        response = await client.get("/api/v1/users/me")

        assert response.status_code == 401

        response = await client.get("/api/v1/users/me", params={})
        assert response.status_code == 401
