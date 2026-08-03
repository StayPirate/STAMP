"""Integration tests for `AuditEventMixin` (backend/app/models/mixins.py).

Uses `SampleAuditEvent` (`tests/support/audit_models.py`), the test-only
concrete subclass, since the mixin itself has no physical table. See
`docs/features/platform/audit-trail-infrastructure.md` (AuditEventMixin)
for the full specification.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy import Table
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from tests.support.audit_models import SampleAuditEvent

_TABLE = cast("Table", SampleAuditEvent.__table__)


@pytest.mark.unit
class TestMixinColumnsDeclaration:
    """Structural assertions over the mixin's contribution to the table,
    independent of any database round-trip.
    """

    def test_table_has_no_updated_at_column(self) -> None:
        assert "updated_at" not in _TABLE.c

    def test_id_created_at_user_id_columns_present(self) -> None:
        columns = _TABLE.c
        assert "id" in columns
        assert "created_at" in columns
        assert "user_id" in columns

    def test_user_id_column_is_nullable(self) -> None:
        assert _TABLE.c.user_id.nullable is True

    def test_created_at_column_is_not_nullable(self) -> None:
        assert _TABLE.c.created_at.nullable is False

    def test_user_id_foreign_key_uses_ondelete_restrict(self) -> None:
        fks = list(_TABLE.c.user_id.foreign_keys)
        assert len(fks) == 1
        assert fks[0].ondelete == "RESTRICT"

    def test_created_at_and_user_id_are_indexed(self) -> None:
        indexed_columns: set[str] = set()
        for index in _TABLE.indexes:
            for column in index.columns:
                indexed_columns.add(column.name)
        assert "created_at" in indexed_columns
        assert "user_id" in indexed_columns


@pytest.mark.integration
class TestMixinPersistence:
    """Round-trip behavior of the mixin's columns against real PostgreSQL."""

    async def test_created_at_is_populated_and_utc_aware(
        self, db_session: AsyncSession
    ) -> None:
        event = SampleAuditEvent(event_type="sample_event")
        db_session.add(event)
        await db_session.flush()
        await db_session.refresh(event)

        assert event.created_at is not None
        assert event.created_at.tzinfo is not None
        # Freshly created: within a generous bound of "now" in UTC.
        assert (datetime.now(UTC) - event.created_at).total_seconds() < 60

    async def test_user_id_defaults_to_null_for_system_events(
        self, db_session: AsyncSession
    ) -> None:
        event = SampleAuditEvent(event_type="sample_event")
        db_session.add(event)
        await db_session.flush()

        assert event.user_id is None

    async def test_user_id_accepts_a_real_user(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        event = SampleAuditEvent(event_type="sample_event", user_id=user.id)
        db_session.add(event)
        await db_session.flush()

        assert event.user_id == user.id

    async def test_deleting_referenced_user_is_restricted(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        sample_audit_event_factory: Callable[..., Awaitable[SampleAuditEvent]],
    ) -> None:
        """`ON DELETE RESTRICT` prevents hard-deleting a user referenced
        by an audit event, protecting audit history (Sentinel only
        soft-deletes users)."""
        user = await user_factory()
        await sample_audit_event_factory(user_id=user.id)

        await db_session.delete(user)
        with pytest.raises(IntegrityError):
            await db_session.flush()
