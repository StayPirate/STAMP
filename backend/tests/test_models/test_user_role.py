"""Integration tests for the UserRole model (backend/app/models/user_role.py).

See docs/data-model.md (UserRole) and docs/features/identity/rbac.md
(Role Origins and Coexistence) for the authoritative contract.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.models.user_role import UserRole


@pytest.mark.integration
class TestUserRoleCreation:
    async def test_create_manual_role_assignment(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        role = UserRole(user_id=user.id, role=Role.VULNERABILITY_ANALYST.value)
        db_session.add(role)
        await db_session.flush()

        assert role.id is not None
        assert role.group_name == "_manual"
        assert role.assigned_by is None
        assert role.created_at is not None

    async def test_group_name_defaults_to_manual(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        role = UserRole(user_id=user.id, role=Role.ADMIN.value)
        db_session.add(role)
        await db_session.flush()
        await db_session.refresh(role)
        assert role.group_name == "_manual"

    async def test_create_externally_derived_role(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        role = UserRole(
            user_id=user.id,
            role=Role.RESTRICTED_ANALYST.value,
            group_name="SecurityTeam",
        )
        db_session.add(role)
        await db_session.flush()
        assert role.group_name == "SecurityTeam"

    async def test_assigned_by_records_acting_admin(
        self, db_session: AsyncSession, user_factory
    ):
        admin = await user_factory()
        target = await user_factory()
        role = UserRole(
            user_id=target.id,
            role=Role.VULNERABILITY_ANALYST.value,
            assigned_by=admin.id,
        )
        db_session.add(role)
        await db_session.flush()
        assert role.assigned_by == admin.id


@pytest.mark.integration
class TestUserRoleCheckConstraint:
    """chk_user_role_role_valid: only the three defined Role values."""

    async def test_valid_role_values_accepted(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        for role_value in Role:
            db_session.add(UserRole(user_id=user.id, role=role_value.value))
        await db_session.flush()

    async def test_invalid_role_value_rejected(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role="Superuser"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_role_value_is_case_sensitive(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role="admin"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_role_rejected(self, db_session: AsyncSession, user_factory):
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserRoleUniqueConstraint:
    """UNIQUE (user_id, role, group_name) — see rbac.md Coexistence Rules."""

    async def test_duplicate_origin_rejected(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        await db_session.flush()

        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_same_role_different_origin_allowed(
        self, db_session: AsyncSession, user_factory
    ):
        """A user can hold the same role from both manual and external
        origins simultaneously (rbac.md, Coexistence Rules)."""
        user = await user_factory()
        db_session.add(
            UserRole(
                user_id=user.id,
                role=Role.RESTRICTED_ANALYST.value,
                group_name="_manual",
            )
        )
        db_session.add(
            UserRole(
                user_id=user.id,
                role=Role.RESTRICTED_ANALYST.value,
                group_name="SecurityTeam",
            )
        )
        await db_session.flush()

    async def test_different_roles_same_group_allowed(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        db_session.add(
            UserRole(user_id=user.id, role=Role.ADMIN.value, group_name="_manual")
        )
        db_session.add(
            UserRole(
                user_id=user.id,
                role=Role.VULNERABILITY_ANALYST.value,
                group_name="_manual",
            )
        )
        await db_session.flush()


@pytest.mark.integration
class TestUserRoleForeignKeys:
    async def test_user_id_invalid_fk_rejected(self, db_session: AsyncSession):
        db_session.add(UserRole(user_id=uuid4(), role=Role.ADMIN.value))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_assigned_by_invalid_fk_rejected(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        db_session.add(
            UserRole(
                user_id=user.id,
                role=Role.ADMIN.value,
                assigned_by=uuid4(),
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_user_id_not_null(self, db_session: AsyncSession):
        db_session.add(UserRole(role=Role.ADMIN.value))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_deleting_user_referenced_by_role_rejected(
        self, db_session: AsyncSession, user_factory
    ):
        """docs/api-spec.md: all FKs referencing the User table use
        ON DELETE RESTRICT — users are never physically deleted while
        still referenced."""
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        await db_session.flush()

        await db_session.delete(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_deleting_user_referenced_as_assigner_rejected(
        self, db_session: AsyncSession, user_factory
    ):
        admin = await user_factory()
        target = await user_factory()
        db_session.add(
            UserRole(
                user_id=target.id,
                role=Role.VULNERABILITY_ANALYST.value,
                assigned_by=admin.id,
            )
        )
        await db_session.flush()

        await db_session.delete(admin)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserRoleRelationship:
    async def test_roles_relationship_on_user(
        self, db_session: AsyncSession, user_factory
    ):
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        db_session.add(
            UserRole(
                user_id=user.id,
                role=Role.VULNERABILITY_ANALYST.value,
                group_name="SecurityTeam",
            )
        )
        await db_session.flush()
        await db_session.refresh(user, attribute_names=["roles"])

        assert len(user.roles) == 2
        assert {role.role for role in user.roles} == {
            Role.ADMIN.value,
            Role.VULNERABILITY_ANALYST.value,
        }


@pytest.mark.unit
class TestUserRoleRepr:
    def test_repr_contains_user_id_role_and_group_name(self):
        user_id = uuid4()
        role = UserRole(user_id=user_id, role=Role.ADMIN.value, group_name="_manual")
        text = repr(role)
        assert str(user_id) in text
        assert Role.ADMIN.value in text
        assert "_manual" in text
