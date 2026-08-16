"""Integration tests for the FetcherAuditEvent model
(backend/app/models/fetcher_audit_event.py).

See docs/data-model.md (FetcherAuditEvent) and
docs/features/platform/fetcher-infrastructure.md (Data Model —
FetcherAuditEvent) for the full specification. These tests exercise
the raw persistence contract (columns, constraints, indexes) using
`fetcher_audit_event_factory`, which bypasses
`FetcherAuditLog.log_event()` validation on purpose — service-layer
validation is covered by tests/test_services/test_fetcher_audit_log.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fetcher_audit_event import FetcherAuditEvent
from app.models.fetcher_config import FetcherConfig
from app.models.mixins import AuditEventMixin
from app.models.user import User


@pytest.mark.integration
class TestFetcherAuditEventCreation:
    async def test_create_disabled_event(
        self,
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        event = await fetcher_audit_event_factory(event_type="disabled")

        assert event.id is not None
        assert event.created_at is not None
        assert event.event_type == "disabled"
        assert event.fetcher_name is not None
        assert event.user_id is not None
        assert event.old_value is None
        assert event.new_value is None
        assert event.detail is None

    async def test_create_config_changed_event_with_detail(
        self,
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        event = await fetcher_audit_event_factory(
            event_type="config_changed",
            old_value="0 */6 * * *",
            new_value="0 */4 * * *",
            detail={"field": "schedule_override"},
        )

        assert event.event_type == "config_changed"
        assert event.old_value == "0 */6 * * *"
        assert event.new_value == "0 */4 * * *"
        assert event.detail == {"field": "schedule_override"}


@pytest.mark.unit
class TestFetcherAuditEventMetadata:
    """Structural assertions over SQLAlchemy metadata, independent of
    any database round-trip."""

    def test_no_updated_at_column(self) -> None:
        """FetcherAuditEvent has no `updated_at` column — audit event
        tables are append-only (docs/features/platform/
        audit-trail-infrastructure.md, AuditEventMixin)."""
        assert not hasattr(FetcherAuditEvent, "updated_at")

    def test_inherits_audit_event_mixin(self) -> None:
        assert issubclass(FetcherAuditEvent, AuditEventMixin)

    def test_registered_in_audit_event_mixin_subclasses(self) -> None:
        assert FetcherAuditEvent in AuditEventMixin.__subclasses__()

    def test_user_id_foreign_key_uses_ondelete_restrict(self) -> None:
        fks = [
            fk
            for fk in FetcherAuditEvent.__table__.foreign_keys
            if fk.parent.name == "user_id"
        ]
        assert len(fks) == 1
        assert fks[0].ondelete == "RESTRICT"

    def test_fetcher_name_foreign_key_uses_ondelete_restrict(self) -> None:
        fks = [
            fk
            for fk in FetcherAuditEvent.__table__.foreign_keys
            if fk.parent.name == "fetcher_name"
        ]
        assert len(fks) == 1
        assert fks[0].ondelete == "RESTRICT"

    def test_old_value_and_new_value_columns_are_nullable(self) -> None:
        columns = FetcherAuditEvent.__table__.columns
        assert columns["old_value"].nullable is True
        assert columns["new_value"].nullable is True

    def test_detail_column_is_nullable(self) -> None:
        column = FetcherAuditEvent.__table__.columns["detail"]
        assert column.nullable is True

    def test_fetcher_name_column_is_not_nullable(self) -> None:
        column = FetcherAuditEvent.__table__.columns["fetcher_name"]
        assert column.nullable is False


@pytest.mark.integration
class TestFetcherAuditEventNotNullConstraints:
    async def test_missing_event_type_rejected(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory()
        db_session.add(FetcherAuditEvent(fetcher_name=config.fetcher_name))
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_missing_fetcher_name_rejected(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(FetcherAuditEvent(event_type="disabled"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestFetcherAuditEventForeignKeys:
    async def test_nonexistent_fetcher_name_rejected(
        self, db_session: AsyncSession
    ) -> None:
        db_session.add(
            FetcherAuditEvent(
                event_type="disabled",
                fetcher_name="nonexistent_fetcher",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_nonexistent_user_id_rejected(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory()
        db_session.add(
            FetcherAuditEvent(
                event_type="disabled",
                fetcher_name=config.fetcher_name,
                user_id=uuid.uuid4(),
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestFetcherAuditEventNoCascadeOnDeletion:
    """Both FKs use `ON DELETE RESTRICT`: audit history must never be
    silently destroyed by deleting the referenced fetcher config or
    user."""

    async def test_deleting_referenced_config_raises(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        config = await fetcher_config_factory()
        await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        await db_session.delete(config)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_deleting_actor_user_raises(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        admin = await user_factory()
        await fetcher_audit_event_factory(user_id=admin.id)

        await db_session.delete(admin)
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestFetcherAuditEventRelationships:
    async def test_config_relationship_resolves(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        config = await fetcher_config_factory(fetcher_name="sync_osv_advisories")
        event = await fetcher_audit_event_factory(fetcher_name=config.fetcher_name)

        await db_session.refresh(event, attribute_names=["config"])

        assert event.config is not None
        assert event.config.fetcher_name == "sync_osv_advisories"

    async def test_actor_relationship_resolves_the_user(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        admin = await user_factory(username="fetcheractoruser")
        event = await fetcher_audit_event_factory(user_id=admin.id)

        await db_session.refresh(event, attribute_names=["actor"])

        assert event.actor is not None
        assert event.actor.id == admin.id
        assert event.actor.username == "fetcheractoruser"

    def test_actor_relationship_is_viewonly(self) -> None:
        mapper = inspect(FetcherAuditEvent)
        relationship_property = mapper.relationships["actor"]
        assert relationship_property.viewonly is True


@pytest.mark.integration
class TestFetcherAuditEventTimezoneAwareTimestamps:
    async def test_created_at_is_timezone_aware(
        self,
        db_session: AsyncSession,
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        event = await fetcher_audit_event_factory()
        await db_session.refresh(event)
        assert event.created_at.tzinfo is not None


@pytest.mark.integration
class TestFetcherAuditEventSchemaIndexes:
    """Verifies the indexes declared on `fetcher_audit_event`
    (docs/features/platform/audit-trail-infrastructure.md, Indexing)."""

    async def test_created_at_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("fetcher_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_fetcher_audit_event_created_at" in index_names

    async def test_user_id_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("fetcher_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_fetcher_audit_event_user_id" in index_names

    async def test_fetcher_name_index_exists(self, db_session: AsyncSession) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("fetcher_audit_event")
        )
        index_names = {idx["name"] for idx in indexes}
        assert "ix_fetcher_audit_event_fetcher_name" in index_names

    async def test_no_event_type_index_exists(self, db_session: AsyncSession) -> None:
        """event_type has low cardinality — no dedicated index
        (docs/features/platform/audit-trail-infrastructure.md,
        Indexing)."""
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("fetcher_audit_event")
        )
        for idx in indexes:
            assert idx["column_names"] != ["event_type"]


@pytest.mark.integration
class TestFetcherAuditEventFactoryFixture:
    """Sanity checks for the shared fetcher_audit_event_factory fixture."""

    async def test_multiple_calls_do_not_collide(
        self,
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        first = await fetcher_audit_event_factory()
        second = await fetcher_audit_event_factory()

        assert first.id != second.id

    async def test_overrides_take_precedence(
        self,
        fetcher_audit_event_factory: Callable[..., Awaitable[FetcherAuditEvent]],
    ) -> None:
        event = await fetcher_audit_event_factory(event_type="enabled")
        assert event.event_type == "enabled"
