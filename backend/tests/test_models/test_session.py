"""Integration tests for the Session model (backend/app/models/session.py).

See docs/data-model.md (Session) and
docs/features/identity/authentication.md (Session Management) for the
full specification. These tests require real PostgreSQL because they
exercise indexes and constraints that SQLite (or an in-memory backend)
would not enforce identically.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.session import Session
from app.models.user import User


@pytest.mark.integration
class TestSessionCreation:
    async def test_create_session(
        self,
        db_session: AsyncSession,
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        session = await session_factory()

        assert session.id is not None
        assert session.is_active is True
        assert session.created_at is not None
        assert session.updated_at is not None
        assert session.expires_at is not None

    async def test_default_is_active_is_true(
        self,
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        session = await session_factory()
        assert session.is_active is True

    async def test_is_active_can_be_set_false(
        self,
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        session = await session_factory(is_active=False)
        assert session.is_active is False


@pytest.mark.integration
class TestSessionNotNullConstraints:
    async def test_missing_user_id_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(Session(expires_at=datetime.now(UTC) + timedelta(days=30)))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_expires_at_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add(Session(user_id=user.id))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestSessionForeignKey:
    async def test_nonexistent_user_id_rejected(self, db_session: AsyncSession) -> None:
        session = Session(
            user_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        db_session.add(session)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestSessionNoCascadeOnUserDeletion:
    """`User.sessions` deliberately has no cascade: user deletion is not
    supported (docs/features/identity/user-service.md, User Deletion).
    A hypothetical `delete(user)` must fail loudly instead of silently
    destroying session records.
    """

    async def test_deleting_user_with_sessions_raises(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        user = await user_factory()
        await session_factory(user_id=user.id)

        await db_session.delete(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestSessionRelationships:
    async def test_session_user_relationship(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        user = await user_factory()
        session = await session_factory(user_id=user.id)
        await db_session.refresh(session, attribute_names=["user"])

        assert session.user is not None
        assert session.user.id == user.id

    async def test_user_sessions_relationship(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        user = await user_factory()
        await session_factory(user_id=user.id)
        await db_session.refresh(user, attribute_names=["sessions"])

        assert len(user.sessions) == 1
        assert user.sessions[0].user_id == user.id


@pytest.mark.integration
class TestSessionTimezoneAwareTimestamps:
    async def test_expires_at_is_timezone_aware(
        self,
        db_session: AsyncSession,
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        session = await session_factory()
        await db_session.refresh(session)
        assert session.expires_at.tzinfo is not None

    async def test_created_at_is_timezone_aware(
        self,
        db_session: AsyncSession,
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        session = await session_factory()
        await db_session.refresh(session)
        assert session.created_at.tzinfo is not None


@pytest.mark.integration
class TestSessionUpdatedAtBehavior:
    async def test_updated_at_advances_on_update(
        self,
        db_session: AsyncSession,
        session_factory: Callable[..., Awaitable[Session]],
    ) -> None:
        """`onupdate=func.now()` refreshes `updated_at` on mutation.

        The comparison deliberately backdates `updated_at` explicitly
        before mutating: PostgreSQL's `now()` is the *transaction* start
        timestamp, and the `db_session` fixture runs each test inside a
        single transaction. Comparing a pre-mutation `now()` against a
        post-mutation `now()` would therefore compare two identical
        values and pass even if `onupdate` were removed entirely. An
        explicit assignment takes precedence over `onupdate`, so
        backdating gives the subsequent mutation something to move away
        from.
        """
        session = await session_factory()
        backdated = datetime.now(UTC) - timedelta(days=7)
        session.updated_at = backdated
        await db_session.flush()
        await db_session.refresh(session)
        assert session.updated_at == backdated

        session.is_active = False
        await db_session.flush()
        await db_session.refresh(session)

        assert session.updated_at > backdated


@pytest.mark.integration
class TestSessionExpiresAtImmutability:
    """`expires_at` is calculated once at login and never recomputed from
    the current `SESSION_MAX_LIFETIME_DAYS` setting
    (docs/features/identity/authentication.md, Session Management).

    This is a persistence-level guarantee test: it verifies that a
    session's persisted `expires_at` is unaffected by a later change to
    the setting. The actual login-time calculation is out of scope for
    this model (P2-01) and belongs to a future authentication service
    (P2-03).
    """

    async def test_persisted_deadline_unaffected_by_later_setting_change(
        self,
        db_session: AsyncSession,
        session_factory: Callable[..., Awaitable[Session]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("JWT_SECRET_KEY", "a" * 32)
        monkeypatch.setenv("SESSION_MAX_LIFETIME_DAYS", "30")
        initial_settings = Settings(_env_file=None)
        first_deadline = datetime.now(UTC) + timedelta(
            days=initial_settings.session_max_lifetime_days
        )
        first_session = await session_factory(expires_at=first_deadline)
        await db_session.refresh(first_session)
        persisted_first_deadline = first_session.expires_at

        # Simulate an operator changing SESSION_MAX_LIFETIME_DAYS after
        # the first session was created.
        monkeypatch.setenv("SESSION_MAX_LIFETIME_DAYS", "60")
        updated_settings = Settings(_env_file=None)
        second_deadline = datetime.now(UTC) + timedelta(
            days=updated_settings.session_max_lifetime_days
        )
        second_session = await session_factory(expires_at=second_deadline)
        await db_session.refresh(second_session)

        # The first session's persisted deadline is untouched by the
        # setting change.
        assert first_session.expires_at == persisted_first_deadline
        # The second session used the new setting value, so its deadline
        # is later than the first one's.
        assert second_session.expires_at > first_session.expires_at


@pytest.mark.integration
class TestSessionSchemaIndexes:
    """Verifies the composite index declared on `session` exists with
    the expected columns (docs/data-model.md, Session, Indexes).
    """

    async def test_user_id_is_active_index_exists(
        self, db_session: AsyncSession
    ) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("session")
        )
        index_names = {idx["name"]: idx for idx in indexes}
        assert "ix_session_user_id_is_active" in index_names
        assert index_names["ix_session_user_id_is_active"]["column_names"] == [
            "user_id",
            "is_active",
        ]
