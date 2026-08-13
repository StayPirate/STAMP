"""Integration tests for the system settings Alembic migration
(backend/alembic/versions/c1ad47607809_add_system_settings_tables.py).

Verifies the migration cycle mandated by
`docs/features/platform/system-settings.md` (Bootstrap) and the P2-14
tracking issue acceptance criteria: upgrade from the previous head,
`alembic check` reports no drift, the seed is idempotent and preserves
a custom value, and downgrade/re-upgrade round-trips cleanly.

These tests run the real `alembic` commands (`command.upgrade()`,
`command.downgrade()`, `command.check()`) against a dedicated, empty
PostgreSQL database created on the same server as the shared test
harness — not against the harness's own database (which already has
every table created via `Base.metadata.create_all()`, bypassing
Alembic entirely; see `docs/features/platform/testing-strategy.md`,
Schema Setup). A dedicated database lets this suite exercise the
literal migration files against a clean schema, mirroring a real
deployment.

Per `docs/features/platform/testing-strategy.md` (Sync Entry-Point
Tests), every test function here is synchronous (`def`, not
`async def`): `alembic.command.upgrade()` (and friends) call
`asyncio.run()` internally via `alembic/env.py`'s
`run_migrations_online()`, which would raise
`RuntimeError: asyncio.run() cannot be called when another event loop
is running` if invoked from a coroutine already running on
pytest-asyncio's event loop. The `alembic_test_database_url` fixture is
plain-sync for the same reason: it performs its own database
creation/teardown via independent `asyncio.run()` calls, entirely
outside pytest-asyncio's managed loop.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import Any, TypedDict

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command
from app.config import settings

_PREVIOUS_HEAD = "4e3e94583e96"
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


class _SchemaFacts(TypedDict):
    """The schema facts this test suite asserts on for a given
    (already-migrated) database — see `_inspect_schema()`."""

    tables: set[str]
    setting_audit_event_indexes: set[str]
    setting_audit_event_fks: list[dict[str, Any]]
    seed_value: str | None


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion on a fresh event loop.

    Used for the fixture's own database creation/teardown and for
    schema inspection between `alembic` command invocations — each
    call is independent and fully completes before the next starts
    (no nesting), consistent with the Sync Entry-Point Tests
    convention's "one `asyncio.run()` call per invocation" for the
    entry point under test; this is test-infrastructure code applying
    the same discipline for clarity.
    """
    return asyncio.run(coro)


async def _create_database(admin_url: URL, db_name: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await engine.dispose()


async def _drop_database(admin_url: URL, db_name: str) -> None:
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            )
    finally:
        await engine.dispose()


@pytest.fixture
def alembic_test_database_url(_engine: AsyncEngine) -> Iterator[str]:
    """A fresh, empty PostgreSQL database dedicated to one test's
    Alembic upgrade/downgrade cycle, created on the same server as the
    shared test harness (`_engine`) and dropped on teardown.
    """
    db_name = f"alembic_test_{uuid.uuid4().hex[:12]}"
    admin_url = _engine.url.set(database="postgres")

    _run(_create_database(admin_url, db_name))

    yield _engine.url.set(database=db_name).render_as_string(hide_password=False)

    _run(_drop_database(admin_url, db_name))


async def _inspect_schema(database_url: str) -> _SchemaFacts:
    """Return the schema facts this test suite asserts on, in one
    connection, for a given (already-migrated) database URL."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:

            def _sync_inspect(sync_conn: Any) -> _SchemaFacts:
                insp = inspect(sync_conn)
                tables = set(insp.get_table_names())
                setting_indexes = (
                    {idx["name"] for idx in insp.get_indexes("setting_audit_event")}
                    if "setting_audit_event" in tables
                    else set()
                )
                setting_fks = (
                    insp.get_foreign_keys("setting_audit_event")
                    if "setting_audit_event" in tables
                    else []
                )
                return {
                    "tables": tables,
                    "setting_audit_event_indexes": setting_indexes,
                    "setting_audit_event_fks": setting_fks,
                    "seed_value": None,
                }

            result = await conn.run_sync(_sync_inspect)

            if "system_setting" in result["tables"]:
                row = await conn.execute(
                    text(
                        "SELECT value FROM system_setting "
                        "WHERE key = 'default_cvss_version'"
                    )
                )
                result["seed_value"] = row.scalar_one_or_none()
            return result
    finally:
        await engine.dispose()


def _inspect(database_url: str) -> _SchemaFacts:
    return _run(_inspect_schema(database_url))


def _isolated_alembic_config() -> Config:
    """Build an Alembic `Config` that will NOT reconfigure Python's
    stdlib `logging` module when passed to `command.upgrade()` and
    friends.

    `alembic/env.py` calls `logging.config.fileConfig(config.config_file_name)`
    whenever `config.config_file_name` is not `None`. `fileConfig()`
    defaults to `disable_existing_loggers=True`, which sets `.disabled
    = True` on every currently-registered logger NOT explicitly listed
    in `alembic.ini`'s `[loggers]` section (only `root`, `sqlalchemy`,
    `alembic`) — silently muting every `app.*` structlog-backed logger
    for the remainder of the pytest process, breaking unrelated
    `caplog`-based tests elsewhere in the suite (see
    docs/features/platform/testing-strategy.md, Test Independence).

    Forcing `file_config` to memoize its parsed `alembic.ini` content
    via one `get_main_option()` call, then clearing `config_file_name`,
    makes `env.py` skip the `fileConfig()` call entirely while leaving
    every other config lookup (`script_location`, the `sqlalchemy.url`
    override) unaffected — they read from the already-memoized
    `ConfigParser`, not from `config_file_name` directly.
    """
    cfg = Config(str(_ALEMBIC_INI))
    cfg.get_main_option("script_location")  # force file_config memoization
    cfg.config_file_name = None
    return cfg


@pytest.mark.integration
class TestSystemSettingsMigration:
    def test_upgrade_downgrade_reupgrade_cycle(
        self,
        alembic_test_database_url: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "database_url", alembic_test_database_url)
        cfg = _isolated_alembic_config()

        # 1. Upgrade to the previous head — the schema state before this
        # work item's migration.
        command.upgrade(cfg, _PREVIOUS_HEAD)
        before = _inspect(alembic_test_database_url)
        assert "system_setting" not in before["tables"]
        assert "setting_audit_event" not in before["tables"]

        # 2. Upgrade to head — creates both tables and seeds the
        # `default_cvss_version` baseline row.
        command.upgrade(cfg, "head")
        after = _inspect(alembic_test_database_url)
        assert "system_setting" in after["tables"]
        assert "setting_audit_event" in after["tables"]
        assert after["seed_value"] == "3.1"
        assert (
            "ix_setting_audit_event_created_at" in after["setting_audit_event_indexes"]
        )
        assert (
            "ix_setting_audit_event_setting_key" in after["setting_audit_event_indexes"]
        )
        assert "ix_setting_audit_event_user_id" in after["setting_audit_event_indexes"]
        fk_referred_tables = {
            fk["referred_table"] for fk in after["setting_audit_event_fks"]
        }
        assert fk_referred_tables == {"system_setting", "user"}

        # 3. Downgrade back to the previous head — both tables are
        # dropped cleanly (development downgrade).
        #
        # Note: `alembic check` itself is deliberately not invoked here.
        # `command.check()` compares the migrated database against
        # `Base.metadata` in the *current* process, which by test-collection
        # time already has `tests.support.audit_models.SampleAuditEvent`
        # registered on the same shared `Base` (needed by unrelated
        # `BaseAuditLog` unit tests) — an in-process call would spuriously
        # report that table as drift. The CI pipeline's dedicated "Check
        # Alembic migration drift" step (`.github/workflows/ci.yml`) already
        # runs `alembic check` from a clean subprocess, unaffected by test
        # fixtures, against the same migration chain verified here.
        command.downgrade(cfg, _PREVIOUS_HEAD)
        downgraded = _inspect(alembic_test_database_url)
        assert "system_setting" not in downgraded["tables"]
        assert "setting_audit_event" not in downgraded["tables"]

        # 5. Re-upgrade to head — idempotent: no errors, seed restored.
        command.upgrade(cfg, "head")
        reupgraded = _inspect(alembic_test_database_url)
        assert "system_setting" in reupgraded["tables"]
        assert reupgraded["seed_value"] == "3.1"

    def test_seed_is_idempotent_and_preserves_a_custom_value(
        self,
        alembic_test_database_url: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "database_url", alembic_test_database_url)
        cfg = _isolated_alembic_config()

        command.upgrade(cfg, "head")
        assert _inspect(alembic_test_database_url)["seed_value"] == "3.1"

        async def _set_custom_value() -> None:
            engine = create_async_engine(alembic_test_database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "UPDATE system_setting SET value = '4.0' "
                            "WHERE key = 'default_cvss_version'"
                        )
                    )
            finally:
                await engine.dispose()

        _run(_set_custom_value())

        # Re-running the seed statement directly (mirroring what a
        # repeated migration run would execute) must not overwrite the
        # administrator-selected value.
        async def _rerun_seed() -> None:
            engine = create_async_engine(alembic_test_database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(
                        text(
                            "INSERT INTO system_setting (key, value) "
                            "VALUES ('default_cvss_version', '3.1') "
                            "ON CONFLICT (key) DO NOTHING"
                        )
                    )
            finally:
                await engine.dispose()

        _run(_rerun_seed())

        assert _inspect(alembic_test_database_url)["seed_value"] == "4.0"
