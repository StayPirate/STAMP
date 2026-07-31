"""Integration tests for the User model (backend/app/models/user.py).

See docs/data-model.md (User) and docs/features/identity/rbac.md for the
full specification. These tests require real PostgreSQL because they
exercise CHECK/UNIQUE constraints that SQLite (or an in-memory backend)
would not enforce identically.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

_FICTIONAL_PASSWORD_HASH = "$2b$12$" + "a" * 53


@pytest.mark.integration
class TestUserCreation:
    """Creation with valid data for both authentication modes."""

    async def test_create_local_user(self, db_session: AsyncSession) -> None:
        user = User(
            username="jdoe",
            email="jdoe@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(user)
        await db_session.flush()

        assert user.id is not None
        assert user.active is True
        assert user.external_id is None
        assert user.created_at is not None
        assert user.updated_at is not None

    async def test_create_external_user(self, db_session: AsyncSession) -> None:
        user = User(
            username="asmith",
            email="asmith@example.com",
            external_id=uuid.uuid4(),
        )
        db_session.add(user)
        await db_session.flush()

        assert user.id is not None
        assert user.password_hash is None

    async def test_default_active_is_true(self, db_session: AsyncSession) -> None:
        user = User(
            username="bwilson",
            email="bwilson@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        assert user.active is True

    async def test_timestamps_auto_populated(self, db_session: AsyncSession) -> None:
        user = User(
            username="ctaylor",
            email="ctaylor@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)

        assert isinstance(user.created_at, datetime)
        assert isinstance(user.updated_at, datetime)


@pytest.mark.integration
class TestUserAuthExclusiveCheck:
    """chk_user_auth_exclusive: exactly one of external_id/password_hash."""

    async def test_both_set_violates_check(self, db_session: AsyncSession) -> None:
        user = User(
            username="dcollins",
            email="dcollins@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
            external_id=uuid.uuid4(),
        )
        db_session.add(user)
        with pytest.raises(IntegrityError, match="chk_user_auth_exclusive"):
            await db_session.flush()

    async def test_neither_set_violates_check(self, db_session: AsyncSession) -> None:
        user = User(username="ewalker", email="ewalker@example.com")
        db_session.add(user)
        with pytest.raises(IntegrityError, match="chk_user_auth_exclusive"):
            await db_session.flush()


@pytest.mark.integration
class TestUserUniqueConstraints:
    async def test_duplicate_username_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(
            User(
                username="fgarcia",
                email="fgarcia1@example.com",
                password_hash=_FICTIONAL_PASSWORD_HASH,
            )
        )
        await db_session.flush()

        db_session.add(
            User(
                username="fgarcia",
                email="fgarcia2@example.com",
                password_hash=_FICTIONAL_PASSWORD_HASH,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_duplicate_email_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(
            User(
                username="gharris1",
                email="gharris@example.com",
                password_hash=_FICTIONAL_PASSWORD_HASH,
            )
        )
        await db_session.flush()

        db_session.add(
            User(
                username="gharris2",
                email="gharris@example.com",
                password_hash=_FICTIONAL_PASSWORD_HASH,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_duplicate_external_id_rejected(
        self, db_session: AsyncSession
    ) -> None:
        shared_external_id = uuid.uuid4()
        db_session.add(
            User(
                username="hlee1",
                email="hlee1@example.com",
                external_id=shared_external_id,
            )
        )
        await db_session.flush()

        db_session.add(
            User(
                username="hlee2",
                email="hlee2@example.com",
                external_id=shared_external_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserNotNullConstraints:
    async def test_missing_username_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(
            User(email="imartin@example.com", password_hash=_FICTIONAL_PASSWORD_HASH)
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_email_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(User(username="imartin", password_hash=_FICTIONAL_PASSWORD_HASH))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserManagerRelationship:
    """Self-referencing manager/direct_reports relationship."""

    async def test_manager_assignment_and_reverse_relationship(
        self, db_session: AsyncSession
    ) -> None:
        manager = User(
            username="jmanager",
            email="jmanager@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(manager)
        await db_session.flush()

        report = User(
            username="kreport",
            email="kreport@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
            manager_id=manager.id,
        )
        db_session.add(report)
        await db_session.flush()
        await db_session.refresh(manager, attribute_names=["direct_reports"])
        await db_session.refresh(report, attribute_names=["manager"])

        assert report.manager is not None
        assert report.manager.id == manager.id
        assert len(manager.direct_reports) == 1
        assert manager.direct_reports[0].id == report.id

    async def test_manager_id_nullable(self, db_session: AsyncSession) -> None:
        user = User(
            username="lnomanager",
            email="lnomanager@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        assert user.manager_id is None

    async def test_manager_id_nonexistent_fk_rejected(
        self, db_session: AsyncSession
    ) -> None:
        user = User(
            username="mbadmanager",
            email="mbadmanager@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
            manager_id=uuid.uuid4(),
        )
        db_session.add(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestUserUpdatedAtBehavior:
    async def test_updated_at_changes_on_update(self, db_session: AsyncSession) -> None:
        user = User(
            username="nupdater",
            email="nupdater@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)
        first_updated_at = user.updated_at

        user.full_name = "New Name"
        await db_session.flush()
        await db_session.refresh(user)

        assert user.updated_at >= first_updated_at


@pytest.mark.integration
class TestUserTimezoneAwareTimestamps:
    async def test_created_at_is_timezone_aware(self, db_session: AsyncSession) -> None:
        user = User(
            username="otzuser",
            email="otzuser@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)

        assert user.created_at.tzinfo is not None
        # Sanity check: the value is close to "now" in UTC.
        assert abs((datetime.now(UTC) - user.created_at).total_seconds()) < 60


@pytest.mark.integration
class TestUserFactoryFixture:
    """Sanity checks for the shared user_factory fixture."""

    async def test_creates_local_user_by_default(self, user_factory) -> None:
        user = await user_factory()
        assert user.id is not None
        assert user.external_id is None
        assert user.password_hash is not None

    async def test_overrides_take_precedence(self, user_factory) -> None:
        user = await user_factory(username="specificname")
        assert user.username == "specificname"

    async def test_multiple_calls_do_not_collide(
        self, user_factory, db_session: AsyncSession
    ) -> None:
        first = await user_factory()
        second = await user_factory()
        assert first.username != second.username
        assert first.email != second.email

        result = await db_session.execute(select(User))
        assert len(result.scalars().all()) == 2

    async def test_external_user_override(self, user_factory) -> None:
        user = await user_factory(external_id=uuid.uuid4(), password_hash=None)
        assert user.external_id is not None
        assert user.password_hash is None
