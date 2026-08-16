"""Integration tests for the FetcherConfig model
(backend/app/models/fetcher_config.py).

See docs/data-model.md (FetcherConfig) and
docs/features/platform/fetcher-infrastructure.md (Data Model —
FetcherConfig) for the full specification.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import String, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base
from app.models.fetcher_config import FetcherConfig


@pytest.mark.integration
class TestFetcherConfigCreation:
    async def test_create_with_defaults(
        self,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory(fetcher_name="sync_nvd_cves")

        assert config.fetcher_name == "sync_nvd_cves"
        assert config.enabled is True
        assert config.schedule_override is None
        assert config.run_timeout == 3600
        assert config.request_delay == 0
        assert config.custom_settings == {}
        assert config.updated_at is not None

    async def test_create_with_all_fields_overridden(
        self,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory(
            fetcher_name="sync_redhat_cves",
            enabled=False,
            schedule_override="0 */4 * * *",
            run_timeout=1800,
            request_delay=2.5,
            custom_settings={"results_per_page": 500},
        )

        assert config.enabled is False
        assert config.schedule_override == "0 */4 * * *"
        assert config.run_timeout == 1800
        assert config.request_delay == 2.5
        assert config.custom_settings == {"results_per_page": 500}

    async def test_fetcher_name_is_string_primary_key(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory(fetcher_name="sync_ghsa_advisories")
        fetched = await db_session.get(FetcherConfig, "sync_ghsa_advisories")
        assert fetched is not None
        assert fetched.fetcher_name == config.fetcher_name


@pytest.mark.unit
class TestFetcherConfigMetadata:
    """Structural assertions over SQLAlchemy metadata, independent of
    any database round-trip."""

    def test_no_created_at_column(self) -> None:
        """FetcherConfig has no `created_at` — creation time is not
        tracked, only last modification (docs/data-model.md, Notes)."""
        assert not hasattr(FetcherConfig, "created_at")

    def test_fetcher_name_column_length(self) -> None:
        column_type = FetcherConfig.__table__.columns["fetcher_name"].type
        assert isinstance(column_type, String)
        assert column_type.length == 100

    def test_fetcher_name_is_the_only_primary_key_column(self) -> None:
        table = Base.metadata.tables["fetcher_config"]
        pk_columns = list(table.primary_key.columns)
        assert len(pk_columns) == 1
        assert pk_columns[0].name == "fetcher_name"


@pytest.mark.integration
class TestFetcherConfigCustomSettingsIsolation:
    """`custom_settings` uses a callable Python default (`dict`), not a
    shared mutable literal — two instances must not share the same
    underlying object."""

    async def test_two_instances_do_not_share_the_default_dict(
        self,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        first = await fetcher_config_factory()
        second = await fetcher_config_factory()

        first.custom_settings["mutated"] = True

        assert "mutated" not in second.custom_settings

    async def test_custom_settings_jsonb_round_trip(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory(
            custom_settings={"page_size": 100, "verbose": True}
        )
        await db_session.refresh(config)
        assert config.custom_settings == {"page_size": 100, "verbose": True}


@pytest.mark.integration
class TestFetcherConfigTimezoneAwareTimestamps:
    async def test_updated_at_is_timezone_aware(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory()
        await db_session.refresh(config)
        assert config.updated_at.tzinfo is not None

    async def test_updated_at_advances_on_update(
        self,
        db_session: AsyncSession,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        """See docs/features/platform/testing-strategy.md
        (`server_default=func.now()` and `onupdate=func.now()`
        Testing) — the backdating pattern is required because
        `now()` returns the same transaction timestamp for every
        evaluation within one test transaction."""
        config = await fetcher_config_factory()

        backdated = datetime.now(UTC) - timedelta(days=7)
        config.updated_at = backdated
        await db_session.flush()
        await db_session.refresh(config)
        assert config.updated_at == backdated

        config.enabled = False
        await db_session.flush()
        await db_session.refresh(config)

        assert config.updated_at > backdated


@pytest.mark.integration
class TestFetcherConfigSchemaIndexes:
    """`fetcher_config` is a small configuration table with no
    secondary query patterns beyond its primary key."""

    async def test_only_primary_key_index_exists(
        self, db_session: AsyncSession
    ) -> None:
        conn = await db_session.connection()
        indexes = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_indexes("fetcher_config")
        )
        assert indexes == []


@pytest.mark.integration
class TestFetcherConfigFactoryFixture:
    """Sanity checks for the shared fetcher_config_factory fixture,
    mirroring TestSystemSettingFactoryFixture in test_system_setting.py."""

    async def test_multiple_calls_do_not_collide(
        self,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        first = await fetcher_config_factory()
        second = await fetcher_config_factory()

        assert first.fetcher_name != second.fetcher_name

    async def test_overrides_take_precedence(
        self,
        fetcher_config_factory: Callable[..., Awaitable[FetcherConfig]],
    ) -> None:
        config = await fetcher_config_factory(run_timeout=120)
        assert config.run_timeout == 120
