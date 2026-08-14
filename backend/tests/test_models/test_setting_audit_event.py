"""Integration tests for the SettingAuditEvent model
(backend/app/models/setting_audit_event.py).

See docs/data-model.md (SettingAuditEvent) and
docs/features/platform/system-settings.md (Setting Audit Log) for the
full specification. These tests exercise the raw persistence contract
(columns, constraints, indexes) using `setting_audit_event_factory`,
which bypasses `SettingAuditLog.log_event()` validation on purpose —
service-layer validation is covered by
tests/test_services/test_settings.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mixins import AuditEventMixin
from app.models.setting_audit_event import SettingAuditEvent
from app.models.system_setting import SystemSetting
from app.models.user import User


@pytest.mark.integration
class TestSettingAuditEventCreation:
    async def test_create_setting_audit_event(
        self,
        setting_audit_event_factory: Callable[..., Awaitable[SettingAuditEvent]],
    ) -> None:
        event = await setting_audit_event_factory(
            event_type="setting_changed",
            old_value="3.1",
            new_value="4.0",
        )

        assert event.id is not None
        assert event.created_at is not None
        assert event.event_type == "setting_changed"
        assert event.setting_key is not None
        assert event.user_id is not None
        assert event.old_value == "3.1"
        assert event.new_value == "4.0"


@pytest.mark.unit
class TestSettingAuditEventMetadata:
    """Structural assertions over SQLAlchemy metadata, independent of
    any database round-trip."""

    def test_no_updated_at_column(self) -> None:
        """SettingAuditEvent has no `updated_at` column — audit event
        tables are append-only (docs/features/platform/
        audit-trail-infrastructure.md, AuditEventMixin)."""
        assert not hasattr(SettingAuditEvent, "updated_at")

    def test_no_detail_column(self) -> None:
        """Unlike TicketAuditEvent/IdentityAuditEvent/FetcherAuditEvent,
        SettingAuditEvent has no `detail` JSONB column (not needed) —
        see docs/features/platform/audit-trail-infrastructure.md
        (Naming)."""
        assert not hasattr(SettingAuditEvent, "detail")

    def test_inherits_audit_event_mixin(self) -> None:
        assert issubclass(SettingAuditEvent, AuditEventMixin)

    def test_registered_in_audit_event_mixin_subclasses(self) -> None:
        assert SettingAuditEvent in AuditEventMixin.__subclasses__()

    def test_user_id_foreign_key_uses_ondelete_restrict(self) -> None:
        fks = [
            fk
            for fk in SettingAuditEvent.__table__.foreign_keys
            if fk.parent.name == "user_id"
        ]
        assert len(fks) == 1
        assert fks[0].ondelete == "RESTRICT"

    def test_setting_key_foreign_key_uses_ondelete_restrict(self) -> None:
        fks = [
            fk
            for fk in SettingAuditEvent.__table__.foreign_keys
            if fk.parent.name == "setting_key"
        ]
        assert len(fks) == 1
        assert fks[0].ondelete == "RESTRICT"

    def test_new_value_column_is_not_nullable(self) -> None:
        column = SettingAuditEvent.__table__.columns["new_value"]
        assert column.nullable is False

    def test_old_value_column_is_nullable(self) -> None:
        column = SettingAuditEvent.__table__.columns["old_value"]
        assert column.nullable is True


@pytest.mark.integration
class TestSettingAuditEventNotNullConstraints:
    async def test_missing_event_type_rejected(
        self,
        db_session: AsyncSession,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        setting = await system_setting_factory()
        db_session.add(SettingAuditEvent(setting_key=setting.key, new_value="4.0"))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_new_value_rejected(
        self,
        db_session: AsyncSession,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        setting = await system_setting_factory()
        db_session.add(
            SettingAuditEvent(event_type="setting_changed", setting_key=setting.key)
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_setting_key_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(SettingAuditEvent(event_type="setting_changed", new_value="4.0"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestSettingAuditEventForeignKeys:
    async def test_nonexistent_setting_key_rejected(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(
            SettingAuditEvent(
                event_type="setting_changed",
                setting_key="nonexistent_setting",
                new_value="4.0",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_nonexistent_user_id_rejected(
        self,
        db_session: AsyncSession,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        setting = await system_setting_factory()
        db_session.add(
            SettingAuditEvent(
                event_type="setting_changed",
                setting_key=setting.key,
                user_id=uuid.uuid4(),
                new_value="4.0",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestSettingAuditEventNoCascadeOnDeletion:
    """Both FKs use `ON DELETE RESTRICT`: audit history must never be
    silently destroyed by deleting the referenced setting or user."""

    async def test_deleting_referenced_setting_raises(
        self,
        db_session: AsyncSession,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
        setting_audit_event_factory: Callable[..., Awaitable[SettingAuditEvent]],
    ) -> None:
        setting = await system_setting_factory()
        await setting_audit_event_factory(setting_key=setting.key)

        await db_session.delete(setting)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_deleting_actor_user_raises(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        setting_audit_event_factory: Callable[..., Awaitable[SettingAuditEvent]],
    ) -> None:
        admin = await user_factory()
        await setting_audit_event_factory(user_id=admin.id)

        await db_session.delete(admin)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestSettingAuditEventActorRelationship:
    """The read-only `actor` relationship (mirrors
    `IdentityAuditEvent.actor`), used by the settings audit log
    endpoint to serialize the actor without a separate query."""

    async def test_actor_relationship_resolves_the_user(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        setting_audit_event_factory: Callable[..., Awaitable[SettingAuditEvent]],
    ) -> None:
        admin = await user_factory(username="actorrelationshipuser")
        event = await setting_audit_event_factory(user_id=admin.id)

        await db_session.refresh(event, attribute_names=["actor"])

        assert event.actor is not None
        assert event.actor.id == admin.id
        assert event.actor.username == "actorrelationshipuser"

    def test_actor_relationship_is_viewonly(self) -> None:
        mapper = inspect(SettingAuditEvent)
        relationship_property = mapper.relationships["actor"]
        assert relationship_property.viewonly is True


@pytest.mark.integration
class TestSettingAuditEventTimezoneAwareTimestamps:
    async def test_created_at_is_timezone_aware(
        self,
        db_session: AsyncSession,
        setting_audit_event_factory: Callable[..., Awaitable[SettingAuditEvent]],
    ) -> None:
        event = await setting_audit_event_factory()
        await db_session.refresh(event)
        assert event.created_at.tzinfo is not None


@pytest.mark.integration
class TestSettingAuditEventSchemaIndexes:
    """Verifies the indexes declared on `setting_audit_event` exist
    (docs/features/platform/audit-trail-infrastructure.md, Indexing)."""

    async def test_created_at_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("setting_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_setting_audit_event_created_at" in index_names

    async def test_user_id_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("setting_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_setting_audit_event_user_id" in index_names

    async def test_setting_key_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("setting_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_setting_audit_event_setting_key" in index_names


@pytest.mark.integration
class TestSettingAuditEventFactoryFixture:
    """Sanity checks for the shared setting_audit_event_factory
    fixture, mirroring TestApiKeyFactoryFixture in test_api_key.py."""

    async def test_multiple_calls_do_not_collide(
        self,
        setting_audit_event_factory: Callable[..., Awaitable[SettingAuditEvent]],
    ) -> None:
        first = await setting_audit_event_factory()
        second = await setting_audit_event_factory()

        assert first.id != second.id

    async def test_overrides_take_precedence(
        self,
        setting_audit_event_factory: Callable[..., Awaitable[SettingAuditEvent]],
    ) -> None:
        event = await setting_audit_event_factory(event_type="setting_changed")
        assert event.event_type == "setting_changed"
