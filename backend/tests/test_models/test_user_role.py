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


@pytest.mark.integration
class TestUserRoleNoCascadeOnUserDeletion:
    """`User.roles` deliberately has no cascade: user deletion is not
    supported (docs/features/identity/user-service.md, User Deletion).
    A hypothetical `delete(user)` must fail loudly instead of silently
    destroying role grant records.
    """

    async def test_deleting_user_with_roles_raises(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        await db_session.flush()

        await db_session.delete(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_deleting_assigning_user_raises(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        """`assigned_by` is a second, independent no-cascade FK: deleting
        the admin who assigned a role must never delete or alter the role
        grant. Covered separately from the owner FK because the two are
        configured differently (`assigning_user` is unidirectional, with
        no back-populated collection on `User`).
        """
        target = await user_factory()
        admin = await user_factory()
        db_session.add(
            UserRole(user_id=target.id, role=Role.ADMIN.value, assigned_by=admin.id)
        )
        await db_session.flush()

        await db_session.delete(admin)
        with pytest.raises(IntegrityError):
            await db_session.flush()


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
