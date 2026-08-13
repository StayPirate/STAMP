"""Integration tests for the SystemSetting model
(backend/app/models/system_setting.py).

See docs/data-model.md (SystemSetting) and
docs/features/platform/system-settings.md for the full specification.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import String, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from app.models.system_setting import SystemSetting


@pytest.mark.integration
class TestSystemSettingCreation:
    async def test_create_system_setting(
        self,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        setting = await system_setting_factory(key="default_cvss_version", value="3.1")

        assert setting.key == "default_cvss_version"
        assert setting.value == "3.1"
        assert setting.updated_at is not None

    async def test_key_is_string_primary_key(
        self,
        db_session: AsyncSession,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        setting = await system_setting_factory(key="some_setting", value="x")
        fetched = await db_session.get(SystemSetting, "some_setting")
        assert fetched is not None
        assert fetched.key == setting.key


@pytest.mark.unit
class TestSystemSettingMetadata:
    """Structural assertions over SQLAlchemy metadata, independent of
    any database round-trip."""

    def test_no_created_at_column(self) -> None:
        """SystemSetting has no `created_at` — creation time is not
        tracked, only last modification (docs/data-model.md, Notes)."""
        assert not hasattr(SystemSetting, "created_at")

    def test_key_column_length(self) -> None:
        column_type = SystemSetting.__table__.columns["key"].type
        assert isinstance(column_type, String)
        assert column_type.length == 100

    def test_value_column_length(self) -> None:
        column_type = SystemSetting.__table__.columns["value"].type
        assert isinstance(column_type, String)
        assert column_type.length == 255

    def test_key_is_the_only_primary_key_column(self) -> None:
        table = Base.metadata.tables["system_setting"]
        pk_columns = list(table.primary_key.columns)
        assert len(pk_columns) == 1
        assert pk_columns[0].name == "key"


@pytest.mark.integration
class TestSystemSettingNotNullConstraints:
    async def test_missing_value_rejected(self, db_session: AsyncSession) -> None:
        db_session.add(SystemSetting(key="missing_value_setting"))
        with pytest.raises(IntegrityError):
            await db_session.flush()


@pytest.mark.integration
class TestSystemSettingTimezoneAwareTimestamps:
    async def test_updated_at_is_timezone_aware(
        self,
        db_session: AsyncSession,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        setting = await system_setting_factory()
        await db_session.refresh(setting)
        assert setting.updated_at.tzinfo is not None

    async def test_updated_at_advances_on_update(
        self,
        db_session: AsyncSession,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        """See docs/features/platform/testing-strategy.md
        (`server_default=func.now()` and `onupdate=func.now()`
        Testing) — the backdating pattern is required because
        `now()` returns the same transaction timestamp for every
        evaluation within one test transaction."""
        setting = await system_setting_factory()

        backdated = datetime.now(UTC) - timedelta(days=7)
        setting.updated_at = backdated
        await db_session.flush()
        await db_session.refresh(setting)
        assert setting.updated_at == backdated

        setting.value = "changed-value"
        await db_session.flush()
        await db_session.refresh(setting)

        assert setting.updated_at > backdated


@pytest.mark.integration
class TestSystemSettingSchemaIndexes:
    """Verifies `system_setting` has no unexpected secondary indexes
    beyond its primary key (it is a small key-value table with no
    additional query patterns)."""

    async def test_only_primary_key_index_exists(
        self, db_session: AsyncSession
    ) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("system_setting")
        )
        assert indexes == []


@pytest.mark.integration
class TestSystemSettingFactoryFixture:
    """Sanity checks for the shared system_setting_factory fixture,
    mirroring TestApiKeyFactoryFixture in test_api_key.py."""

    async def test_multiple_calls_do_not_collide(
        self,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        first = await system_setting_factory()
        second = await system_setting_factory()

        assert first.key != second.key

    async def test_overrides_take_precedence(
        self,
        system_setting_factory: Callable[..., Awaitable[SystemSetting]],
    ) -> None:
        setting = await system_setting_factory(value="custom-value")
        assert setting.value == "custom-value"
