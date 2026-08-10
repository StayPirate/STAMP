"""Tests for user identifier resolution and role queries
(backend/app/services/user_service.py).

See docs/features/identity/user-service.md (`resolve_user_identifier()`)
and docs/features/identity/rbac.md (`require_capability()` Dependency)
for the contract under test.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.core.exceptions import UserNotFoundError
from app.models.user import User
from app.models.user_role import UserRole
from app.services.user_service import get_user_roles, resolve_user_identifier


@pytest.mark.integration
class TestResolveUserIdentifier:
    async def test_resolves_by_uuid(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()

        resolved = await resolve_user_identifier(db_session, str(user.id))

        assert resolved.id == user.id

    async def test_resolves_by_exact_username(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory(username="jdoe")

        resolved = await resolve_user_identifier(db_session, "jdoe")

        assert resolved.id == user.id

    async def test_username_lookup_is_case_sensitive(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory(username="jdoe")

        with pytest.raises(UserNotFoundError):
            await resolve_user_identifier(db_session, "JDoe")

    async def test_unknown_uuid_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(UserNotFoundError):
            await resolve_user_identifier(db_session, str(uuid.uuid4()))

    async def test_unknown_username_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(UserNotFoundError):
            await resolve_user_identifier(db_session, "nonexistent-user")

    async def test_valid_uuid_with_no_match_does_not_fall_back_to_username(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """A syntactically valid but non-existent UUID must not be
        reinterpreted as a username lookup, even if a user happens to
        have that exact string as their username."""
        unknown_id = uuid.uuid4()
        await user_factory(username=str(unknown_id))

        with pytest.raises(UserNotFoundError):
            await resolve_user_identifier(db_session, str(unknown_id))


@pytest.mark.integration
class TestGetUserRoles:
    async def test_no_roles_returns_empty_list(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()

        roles = await get_user_roles(db_session, user.id)

        assert roles == []

    async def test_returns_single_role(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        await db_session.flush()

        roles = await get_user_roles(db_session, user.id)

        assert roles == [Role.ADMIN]

    async def test_role_held_from_multiple_origins_counts_once(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add_all(
            [
                UserRole(
                    user_id=user.id,
                    role=Role.VULNERABILITY_ANALYST.value,
                    group_name="_manual",
                ),
                UserRole(
                    user_id=user.id,
                    role=Role.VULNERABILITY_ANALYST.value,
                    group_name="external-group",
                ),
            ]
        )
        await db_session.flush()

        roles = await get_user_roles(db_session, user.id)

        assert roles == [Role.VULNERABILITY_ANALYST]

    async def test_multiple_distinct_roles(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add_all(
            [
                UserRole(user_id=user.id, role=Role.ADMIN.value),
                UserRole(user_id=user.id, role=Role.VULNERABILITY_ANALYST.value),
            ]
        )
        await db_session.flush()

        roles = await get_user_roles(db_session, user.id)

        assert set(roles) == {Role.ADMIN, Role.VULNERABILITY_ANALYST}

    async def test_unknown_user_id_returns_empty_list(
        self, db_session: AsyncSession
    ) -> None:
        """An unresolved `user_id` is not an error here — the caller has
        already resolved the principal via a validated credential."""
        roles = await get_user_roles(db_session, uuid.uuid4())

        assert roles == []
