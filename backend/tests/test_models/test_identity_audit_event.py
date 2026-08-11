"""Integration tests for the IdentityAuditEvent model
(backend/app/models/identity_audit_event.py).

See docs/data-model.md (IdentityAuditEvent) and
docs/features/identity/identity-audit-log.md (Data Model) for the full
specification. These tests exercise the raw persistence contract
(columns, constraints, indexes) using `identity_audit_event_factory`,
which bypasses `IdentityAuditLog.log_event()` validation on purpose —
service-layer validation is covered by
tests/test_services/test_identity_audit_log.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity_audit_event import IdentityAuditEvent
from app.models.mixins import AuditEventMixin
from app.models.user import User


@pytest.mark.integration
class TestIdentityAuditEventCreation:
    async def test_create_identity_audit_event(
        self,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        event = await identity_audit_event_factory(
            event_type="user_created",
            new_value="jdoe",
        )

        assert event.id is not None
        assert event.created_at is not None
        assert event.event_type == "user_created"
        assert event.target_user_id is not None
        assert event.user_id is None
        assert event.old_value is None
        assert event.new_value == "jdoe"
        assert event.detail is None

    async def test_detail_jsonb_round_trip(
        self,
        db_session: AsyncSession,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        detail = {"source": "external_sync", "mapping": "SecurityTeam"}
        event = await identity_audit_event_factory(
            event_type="role_added",
            new_value="admin",
            detail=detail,
        )
        await db_session.refresh(event)
        assert event.detail == detail

    async def test_actor_user_id_set(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        admin = await user_factory()
        event = await identity_audit_event_factory(
            event_type="password_reset",
            user_id=admin.id,
        )
        assert event.user_id == admin.id


@pytest.mark.unit
class TestIdentityAuditEventMetadata:
    """Structural assertions over SQLAlchemy metadata, independent of
    any database round-trip."""

    def test_no_updated_at_column(self) -> None:
        """IdentityAuditEvent has no `updated_at` column — audit event
        tables are append-only (docs/features/platform/
        audit-trail-infrastructure.md, AuditEventMixin)."""
        assert not hasattr(IdentityAuditEvent, "updated_at")

    def test_inherits_audit_event_mixin(self) -> None:
        assert issubclass(IdentityAuditEvent, AuditEventMixin)

    def test_registered_in_audit_event_mixin_subclasses(self) -> None:
        assert IdentityAuditEvent in AuditEventMixin.__subclasses__()

    def test_user_id_foreign_key_uses_ondelete_restrict(self) -> None:
        fks = [
            fk
            for fk in IdentityAuditEvent.__table__.foreign_keys
            if fk.parent.name == "user_id"
        ]
        assert len(fks) == 1
        assert fks[0].ondelete == "RESTRICT"

    def test_target_user_id_foreign_key_uses_ondelete_restrict(self) -> None:
        fks = [
            fk
            for fk in IdentityAuditEvent.__table__.foreign_keys
            if fk.parent.name == "target_user_id"
        ]
        assert len(fks) == 1
        assert fks[0].ondelete == "RESTRICT"


@pytest.mark.integration
class TestIdentityAuditEventNotNullConstraints:
    async def test_missing_event_type_rejected(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add(IdentityAuditEvent(target_user_id=user.id))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestIdentityAuditEventNullableColumns:
    async def test_user_id_nullable(
        self,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        event = await identity_audit_event_factory()
        assert event.user_id is None

    async def test_target_user_id_nullable(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        admin = await user_factory()
        event = IdentityAuditEvent(
            event_type="role_mapping_created",
            user_id=admin.id,
            new_value="SecurityTeam -> admin",
            detail={"group_name": "SecurityTeam", "role": "admin", "affected_users": 5},
        )
        db_session.add(event)
        await db_session.flush()
        assert event.target_user_id is None

    async def test_old_value_nullable(
        self,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        event = await identity_audit_event_factory()
        assert event.old_value is None

    async def test_new_value_nullable(
        self,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        event = await identity_audit_event_factory(new_value=None)
        assert event.new_value is None

    async def test_detail_nullable(
        self,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        event = await identity_audit_event_factory()
        assert event.detail is None


@pytest.mark.integration
class TestIdentityAuditEventForeignKeys:
    async def test_nonexistent_user_id_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(
            IdentityAuditEvent(event_type="password_reset", user_id=uuid.uuid4())
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_nonexistent_target_user_id_rejected(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(
            IdentityAuditEvent(event_type="password_reset", target_user_id=uuid.uuid4())
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestIdentityAuditEventNoCascadeOnUserDeletion:
    """Both actor and target FKs use `ON DELETE RESTRICT`: Sentinel only
    soft-deletes users, never hard-deletes them. A hypothetical
    `delete(user)` must fail loudly with an IntegrityError instead of
    silently destroying audit history.
    """

    async def test_deleting_actor_user_raises(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        admin = await user_factory()
        await identity_audit_event_factory(
            event_type="password_reset", user_id=admin.id
        )

        await db_session.delete(admin)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_deleting_target_user_raises(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        target = await user_factory()
        await identity_audit_event_factory(
            event_type="user_created", target_user_id=target.id, new_value="jdoe"
        )

        await db_session.delete(target)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestIdentityAuditEventRelationships:
    async def test_actor_relationship(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        admin = await user_factory()
        event = await identity_audit_event_factory(
            event_type="password_reset", user_id=admin.id
        )
        await db_session.refresh(event, attribute_names=["actor"])

        assert event.actor is not None
        assert event.actor.id == admin.id

    async def test_target_user_relationship(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        target = await user_factory()
        event = await identity_audit_event_factory(target_user_id=target.id)
        await db_session.refresh(event, attribute_names=["target_user"])

        assert event.target_user is not None
        assert event.target_user.id == target.id

    async def test_actor_disambiguated_from_target_user(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        """Two FKs to `user.id` (`user_id`, `target_user_id`) must
        resolve to distinct relationships without ambiguity errors."""
        actor = await user_factory()
        target = await user_factory()
        event = await identity_audit_event_factory(
            event_type="password_reset",
            user_id=actor.id,
            target_user_id=target.id,
        )
        await db_session.refresh(event, attribute_names=["actor", "target_user"])

        assert event.actor is not None
        assert event.target_user is not None
        assert event.actor.id == actor.id
        assert event.target_user.id == target.id
        assert event.actor.id != event.target_user.id

    async def test_actor_none_when_user_id_null(
        self,
        db_session: AsyncSession,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        event = await identity_audit_event_factory()
        await db_session.refresh(event, attribute_names=["actor"])
        assert event.actor is None


@pytest.mark.integration
class TestIdentityAuditEventTimezoneAwareTimestamps:
    async def test_created_at_is_timezone_aware(
        self,
        db_session: AsyncSession,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        event = await identity_audit_event_factory()
        await db_session.refresh(event)
        assert event.created_at.tzinfo is not None


@pytest.mark.integration
class TestIdentityAuditEventSchemaIndexes:
    """Verifies the indexes declared on `identity_audit_event` exist
    (docs/features/platform/audit-trail-infrastructure.md, Indexing)."""

    async def test_created_at_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("identity_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_identity_audit_event_created_at" in index_names

    async def test_user_id_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("identity_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_identity_audit_event_user_id" in index_names

    async def test_target_user_id_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("identity_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_identity_audit_event_target_user_id" in index_names


@pytest.mark.integration
class TestIdentityAuditEventFactoryFixture:
    """Sanity checks for the shared identity_audit_event_factory
    fixture, mirroring TestApiKeyFactoryFixture in test_api_key.py."""

    async def test_multiple_calls_do_not_collide(
        self,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        first = await identity_audit_event_factory()
        second = await identity_audit_event_factory()

        assert first.id != second.id
        assert first.target_user_id != second.target_user_id

    async def test_overrides_take_precedence(
        self,
        identity_audit_event_factory: Callable[..., Awaitable[IdentityAuditEvent]],
    ) -> None:
        event = await identity_audit_event_factory(event_type="role_added")
        assert event.event_type == "role_added"
