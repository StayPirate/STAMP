"""Integration tests for the User model (backend/app/models/user.py).

See docs/data-model.md (User) and docs/features/identity/rbac.md for the
authoritative contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

_FICTIONAL_HASH = "$2b$12$" + "b" * 53


@pytest.mark.integration
class TestUserCreation:
    async def test_create_local_user_with_valid_data(self, db_session: AsyncSession):
        user = User(
            username="jdoe",
            email="jdoe@example.com",
            full_name="John Doe",
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(user)
        await db_session.flush()

        assert user.id is not None
        assert user.active is True
        assert user.external_id is None
        assert user.created_at is not None
        assert user.updated_at is not None

    async def test_create_external_user_with_valid_data(self, db_session: AsyncSession):
        user = User(
            username="asmith",
            email="asmith@example.com",
            full_name="Alice Smith",
            external_id=uuid4(),
            password_hash=None,
        )
        db_session.add(user)
        await db_session.flush()

        assert user.id is not None
        assert user.password_hash is None
        assert user.external_id is not None

    async def test_active_defaults_to_true(self, db_session: AsyncSession):
        user = User(
            username="bwilson",
            email="bwilson@example.com",
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)
        assert user.active is True

    async def test_full_name_is_optional(self, db_session: AsyncSession):
        user = User(
            username="noname",
            email="noname@example.com",
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        assert user.full_name is None

    async def test_missing_username_rejected(self, db_session: AsyncSession):
        user = User(email="nousername@example.com", password_hash=_FICTIONAL_HASH)
        db_session.add(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_email_rejected(self, db_session: AsyncSession):
        user = User(username="noemail", password_hash=_FICTIONAL_HASH)
        db_session.add(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserAuthExclusiveCheckConstraint:
    """chk_user_auth_exclusive: exactly one of external_id/password_hash."""

    async def test_external_user_with_password_hash_rejected(
        self, db_session: AsyncSession
    ):
        user = User(
            username="badexternal",
            email="badexternal@example.com",
            external_id=uuid4(),
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_neither_external_id_nor_password_hash_rejected(
        self, db_session: AsyncSession
    ):
        user = User(username="neither", email="neither@example.com")
        db_session.add(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserUniqueConstraints:
    async def test_duplicate_username_rejected(self, db_session: AsyncSession):
        db_session.add(
            User(
                username="dupe",
                email="dupe1@example.com",
                password_hash=_FICTIONAL_HASH,
            )
        )
        await db_session.flush()

        db_session.add(
            User(
                username="dupe",
                email="dupe2@example.com",
                password_hash=_FICTIONAL_HASH,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_duplicate_email_rejected(self, db_session: AsyncSession):
        db_session.add(
            User(
                username="uniq1",
                email="shared@example.com",
                password_hash=_FICTIONAL_HASH,
            )
        )
        await db_session.flush()

        db_session.add(
            User(
                username="uniq2",
                email="shared@example.com",
                password_hash=_FICTIONAL_HASH,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_duplicate_external_id_rejected(self, db_session: AsyncSession):
        shared_external_id = uuid4()
        db_session.add(
            User(
                username="ext1",
                email="ext1@example.com",
                external_id=shared_external_id,
            )
        )
        await db_session.flush()

        db_session.add(
            User(
                username="ext2",
                email="ext2@example.com",
                external_id=shared_external_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_multiple_local_users_can_have_null_external_id(
        self, db_session: AsyncSession
    ):
        """external_id is nullable and UNIQUE — multiple NULLs must be
        allowed (standard SQL UNIQUE semantics)."""
        db_session.add(
            User(
                username="localA",
                email="localA@example.com",
                password_hash=_FICTIONAL_HASH,
            )
        )
        db_session.add(
            User(
                username="localB",
                email="localB@example.com",
                password_hash=_FICTIONAL_HASH,
            )
        )
        await db_session.flush()


@pytest.mark.integration
class TestUserManagerRelationship:
    async def test_manager_self_referencing_fk(self, db_session: AsyncSession):
        manager = User(
            username="manager1",
            email="manager1@example.com",
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(manager)
        await db_session.flush()

        report = User(
            username="report1",
            email="report1@example.com",
            password_hash=_FICTIONAL_HASH,
            manager_id=manager.id,
        )
        db_session.add(report)
        await db_session.flush()
        await db_session.refresh(report)
        await db_session.refresh(manager, attribute_names=["direct_reports"])

        assert report.manager_id == manager.id
        assert manager.direct_reports == [report]

    async def test_manager_id_defaults_to_none(self, db_session: AsyncSession):
        user = User(
            username="orphan",
            email="orphan@example.com",
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        assert user.manager_id is None

    async def test_manager_id_invalid_fk_rejected(self, db_session: AsyncSession):
        user = User(
            username="badmanager",
            email="badmanager@example.com",
            password_hash=_FICTIONAL_HASH,
            manager_id=uuid4(),
        )
        db_session.add(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_deleting_manager_referenced_by_report_rejected(
        self, db_session: AsyncSession
    ):
        """docs/api-spec.md: all FKs referencing the User table use
        ON DELETE RESTRICT — users are never physically deleted while
        still referenced."""
        manager = User(
            username="manager2",
            email="manager2@example.com",
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(manager)
        await db_session.flush()

        db_session.add(
            User(
                username="report2",
                email="report2@example.com",
                password_hash=_FICTIONAL_HASH,
                manager_id=manager.id,
            )
        )
        await db_session.flush()

        await db_session.delete(manager)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserTimestamps:
    async def test_created_at_and_updated_at_are_set_on_insert(
        self, db_session: AsyncSession
    ):
        user = User(
            username="tsuser",
            email="tsuser@example.com",
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)

        assert user.created_at is not None
        assert user.updated_at is not None
        assert user.created_at.tzinfo is not None

    async def test_synced_at_and_last_login_at_are_nullable(
        self, db_session: AsyncSession
    ):
        user = User(
            username="neverlogged",
            email="neverlogged@example.com",
            password_hash=_FICTIONAL_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        assert user.synced_at is None
        assert user.last_login_at is None

    async def test_synced_at_accepts_timezone_aware_datetime(
        self, db_session: AsyncSession
    ):
        now = datetime.now(UTC)
        user = User(
            username="syncedone",
            email="syncedone@example.com",
            external_id=uuid4(),
            synced_at=now,
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)
        assert user.synced_at == now


@pytest.mark.integration
class TestUserQuery:
    async def test_query_user_by_username(self, db_session: AsyncSession, user_factory):
        created = await user_factory(username="findme")
        result = await db_session.scalars(select(User).where(User.username == "findme"))
        found = result.one()
        assert found.id == created.id


@pytest.mark.unit
class TestUserRepr:
    def test_repr_contains_id_and_username(self):
        user = User(username="jdoe", email="jdoe@example.com")
        user.id = uuid4()
        text = repr(user)
        assert "jdoe" in text
        assert str(user.id) in text
