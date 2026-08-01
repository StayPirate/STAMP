"""Integration tests for the UserRole model (backend/app/models/user_role.py).

See docs/data-model.md (UserRole) and docs/features/identity/rbac.md
(Role Origins and Coexistence) for the full specification.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.models.user import User
from app.models.user_role import UserRole


@pytest.mark.integration
class TestUserRoleCreation:
    async def test_create_manual_role(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        role = UserRole(user_id=user.id, role=Role.VULNERABILITY_ANALYST.value)
        db_session.add(role)
        await db_session.flush()

        assert role.id is not None
        assert role.group_name == "_manual"
        assert role.created_at is not None

    async def test_create_externally_derived_role(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        role = UserRole(
            user_id=user.id,
            role=Role.RESTRICTED_ANALYST.value,
            group_name="SecurityTeam",
        )
        db_session.add(role)
        await db_session.flush()

        assert role.group_name == "SecurityTeam"

    async def test_create_with_assigning_user(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        admin = await user_factory()
        target = await user_factory()
        role = UserRole(
            user_id=target.id,
            role=Role.ADMIN.value,
            assigned_by=admin.id,
        )
        db_session.add(role)
        await db_session.flush()

        assert role.assigned_by == admin.id

    async def test_assigned_by_nullable_for_system_actions(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        role = UserRole(user_id=user.id, role=Role.ADMIN.value)
        db_session.add(role)
        await db_session.flush()
        assert role.assigned_by is None


@pytest.mark.integration
class TestUserRoleValidCheck:
    """chk_user_role_role_valid: only the three Role enum values allowed."""

    async def test_invalid_role_value_rejected(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        role = UserRole(user_id=user.id, role="SuperAdmin")
        db_session.add(role)
        with pytest.raises(IntegrityError, match="chk_user_role_role_valid"):
            await db_session.flush()

    @pytest.mark.parametrize("role_value", [r.value for r in Role])
    async def test_each_valid_role_value_accepted(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        role_value: str,
    ) -> None:
        user = await user_factory()
        role = UserRole(user_id=user.id, role=role_value)
        db_session.add(role)
        await db_session.flush()
        assert role.role == role_value


@pytest.mark.integration
class TestUserRoleUniqueConstraint:
    """Unique constraint: (user_id, role, group_name)."""

    async def test_duplicate_origin_rejected(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        await db_session.flush()

        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_same_role_different_origin_allowed(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        """A user can hold the same role from multiple origins
        simultaneously (Coexistence Rules, rule 1)."""
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        await db_session.flush()

        db_session.add(
            UserRole(user_id=user.id, role=Role.ADMIN.value, group_name="SecurityTeam")
        )
        # No exception raised means both origin rows coexist.
        await db_session.flush()


@pytest.mark.integration
class TestUserRoleForeignKeys:
    async def test_nonexistent_user_id_rejected(self, db_session: AsyncSession) -> None:
        role = UserRole(user_id=uuid.uuid4(), role=Role.ADMIN.value)
        db_session.add(role)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_nonexistent_assigned_by_rejected(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        role = UserRole(
            user_id=user.id, role=Role.ADMIN.value, assigned_by=uuid.uuid4()
        )
        db_session.add(role)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserRoleNotNullConstraints:
    async def test_missing_role_rejected(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_user_id_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(UserRole(role=Role.ADMIN.value))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserRoleRelationships:
    async def test_user_roles_relationship(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.VULNERABILITY_ANALYST.value))
        await db_session.flush()
        await db_session.refresh(user, attribute_names=["roles"])

        assert len(user.roles) == 1
        assert user.roles[0].role == Role.VULNERABILITY_ANALYST.value

    async def test_assigning_user_relationship(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        admin = await user_factory()
        target = await user_factory()
        role = UserRole(user_id=target.id, role=Role.ADMIN.value, assigned_by=admin.id)
        db_session.add(role)
        await db_session.flush()
        await db_session.refresh(role, attribute_names=["assigning_user"])

        assert role.assigning_user is not None
        assert role.assigning_user.id == admin.id

    async def test_deleting_user_cascades_to_roles(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        """User.roles uses cascade="all, delete-orphan": deleting a User
        via the ORM removes its UserRole rows (see user_role.py comment).
        This is a hypothetical path in production (users are deactivated,
        not deleted, per docs/data-model.md) but is a deliberate,
        documented cascade choice worth pinning down with a test.
        """
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        await db_session.flush()
        await db_session.refresh(user, attribute_names=["roles"])
        role_id = user.roles[0].id

        await db_session.delete(user)
        await db_session.flush()

        remaining = await db_session.get(UserRole, role_id)
        assert remaining is None


@pytest.mark.integration
class TestUserRoleGroupNameDefault:
    async def test_default_group_name_is_manual(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        role = UserRole(user_id=user.id, role=Role.ADMIN.value)
        db_session.add(role)
        await db_session.flush()
        await db_session.refresh(role)
        assert role.group_name == "_manual"
