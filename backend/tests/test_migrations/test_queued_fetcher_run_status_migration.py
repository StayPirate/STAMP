"""Integration tests for the queued fetcher run status Alembic migration
(backend/alembic/versions/a4f22788de31_add_queued_fetcher_run_status.py).

Verifies the migration cycle mandated by
`docs/features/platform/fetcher-infrastructure.md` (Concurrency
Control, FetcherRunStatus Enum): upgrade from the previous head makes
`started_at` nullable and the CHECK constraint accept `queued`, a
`queued` row survives the round trip data-normalized on downgrade
(converted to `failure` with a synthetic `started_at`/`finished_at`),
and pre-existing rows with a populated `started_at` are left untouched.

See `tests/test_migrations/conftest.py` for the shared Alembic test
infrastructure (dedicated database fixture, isolated config helper)
used by every migration test suite in this package.
"""

from __future__ import annotations

from typing import Any, TypedDict

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.config import settings
from tests.test_migrations.conftest import isolated_alembic_config, run_sync

_PREVIOUS_HEAD = "4b4f7187b541"


class _RunRow(TypedDict):
    status: str
    started_at: Any
    finished_at: Any
    duration_seconds: Any
    error_message: Any


async def _seed_pre_migration_rows(database_url: str) -> None:
    """Insert a `FetcherConfig` row and two ordinary `FetcherRun` rows
    (a completed `success` and an in-progress `running`, both with a
    populated `started_at`) using only columns/values valid under the
    pre-migration schema."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO fetcher_config "
                    "(fetcher_name, enabled, run_timeout, request_delay, "
                    "custom_settings) "
                    "VALUES ('migration_test_fetcher', true, 3600, 0, '{}')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO fetcher_run "
                    "(fetcher_name, started_at, finished_at, duration_seconds, "
                    "status, items_created, items_updated, items_failed, triggered_by) "
                    "VALUES ('migration_test_fetcher', now() - interval '1 hour', "
                    "now() - interval '55 minutes', 300, 'success', 1, 0, 0, "
                    "'schedule')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO fetcher_run "
                    "(fetcher_name, started_at, status, items_created, "
                    "items_updated, items_failed, triggered_by) "
                    "VALUES ('migration_test_fetcher', now(), 'running', "
                    "0, 0, 0, 'schedule')"
                )
            )
    finally:
        await engine.dispose()


async def _seed_queued_row(database_url: str) -> None:
    """Insert a `queued` manual run — only valid after the migration
    under test has run."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO fetcher_run "
                    "(fetcher_name, status, items_created, items_updated, "
                    "items_failed, triggered_by) "
                    "VALUES ('migration_test_fetcher', 'queued', 0, 0, 0, 'manual')"
                )
            )
    finally:
        await engine.dispose()


async def _fetch_rows(database_url: str) -> list[_RunRow]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT status, started_at, finished_at, duration_seconds, "
                    "error_message FROM fetcher_run "
                    "WHERE fetcher_name = 'migration_test_fetcher' "
                    "ORDER BY created_at ASC"
                )
            )
            return [
                {
                    "status": row.status,
                    "started_at": row.started_at,
                    "finished_at": row.finished_at,
                    "duration_seconds": row.duration_seconds,
                    "error_message": row.error_message,
                }
                for row in result
            ]
    finally:
        await engine.dispose()


def _fetch(database_url: str) -> list[_RunRow]:
    return run_sync(_fetch_rows(database_url))


async def _inspect_fetcher_run(
    database_url: str,
) -> tuple[set[str], dict[str, dict[str, Any]], bool]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:

            def _sync_inspect(
                sync_conn: Any,
            ) -> tuple[set[str], dict[str, dict[str, Any]], bool]:
                insp = inspect(sync_conn)
                checks = {c["name"] for c in insp.get_check_constraints("fetcher_run")}
                indexes = {idx["name"]: idx for idx in insp.get_indexes("fetcher_run")}
                columns = {col["name"]: col for col in insp.get_columns("fetcher_run")}
                started_at_nullable = bool(columns["started_at"]["nullable"])
                return checks, indexes, started_at_nullable

            return await conn.run_sync(_sync_inspect)
    finally:
        await engine.dispose()


def _inspect(database_url: str) -> tuple[set[str], dict[str, dict[str, Any]], bool]:
    return run_sync(_inspect_fetcher_run(database_url))


@pytest.mark.integration
class TestQueuedFetcherRunStatusMigration:
    def test_upgrade_downgrade_reupgrade_cycle(
        self,
        alembic_test_database_url: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "database_url", alembic_test_database_url)
        cfg = isolated_alembic_config()

        # 1. Upgrade to the previous head — schema state before this
        # migration: `started_at NOT NULL`, no `queued` status.
        command.upgrade(cfg, _PREVIOUS_HEAD)
        before_checks, before_indexes, before_nullable = _inspect(
            alembic_test_database_url
        )
        assert "chk_fetcher_run_status_valid" in before_checks
        assert "ix_fetcher_run_fetcher_name_created_at" not in before_indexes
        assert before_nullable is False

        run_sync(_seed_pre_migration_rows(alembic_test_database_url))
        seeded_rows = _fetch(alembic_test_database_url)
        assert len(seeded_rows) == 2
        assert all(row["started_at"] is not None for row in seeded_rows)

        # 2. Upgrade to head — `started_at` becomes nullable, the CHECK
        # constraint accepts `queued`, and the new composite index
        # exists.
        command.upgrade(cfg, "head")
        after_checks, after_indexes, after_nullable = _inspect(
            alembic_test_database_url
        )
        assert "chk_fetcher_run_status_valid" in after_checks
        assert "ix_fetcher_run_fetcher_name_created_at" in after_indexes
        assert after_indexes["ix_fetcher_run_fetcher_name_created_at"][
            "column_names"
        ] == ["fetcher_name", "created_at"]
        assert after_nullable is True

        # Pre-existing rows are untouched by the upgrade.
        rows_after_upgrade = _fetch(alembic_test_database_url)
        assert len(rows_after_upgrade) == 2
        assert {row["status"] for row in rows_after_upgrade} == {"success", "running"}
        assert all(row["started_at"] is not None for row in rows_after_upgrade)

        # A `queued` row (no `started_at`) is now valid.
        run_sync(_seed_queued_row(alembic_test_database_url))
        rows_with_queued = _fetch(alembic_test_database_url)
        assert len(rows_with_queued) == 3
        queued_row = next(r for r in rows_with_queued if r["status"] == "queued")
        assert queued_row["started_at"] is None
        assert queued_row["finished_at"] is None
        assert queued_row["duration_seconds"] is None

        # 3. Downgrade back to the previous head — the `queued` row is
        # normalized to `failure` with a synthetic `started_at`/
        # `finished_at` and `duration_seconds = 0`; the two
        # pre-existing rows (already non-NULL `started_at`) are
        # untouched.
        command.downgrade(cfg, _PREVIOUS_HEAD)
        _downgraded_checks, downgraded_indexes, downgraded_nullable = _inspect(
            alembic_test_database_url
        )
        assert "ix_fetcher_run_fetcher_name_created_at" not in downgraded_indexes
        assert downgraded_nullable is False

        downgraded_rows = _fetch(alembic_test_database_url)
        assert len(downgraded_rows) == 3
        statuses = [row["status"] for row in downgraded_rows]
        assert statuses.count("failure") == 1
        assert statuses.count("success") == 1
        assert statuses.count("running") == 1
        assert all(row["started_at"] is not None for row in downgraded_rows)
        normalized_row = next(
            r
            for r in downgraded_rows
            if r["status"] == "failure" and r["duration_seconds"] == 0
        )
        assert normalized_row["started_at"] == normalized_row["finished_at"]
        assert normalized_row["error_message"] == (
            "Reverted to a schema version that does not support the queued status"
        )

        # 4. Re-upgrade to head — idempotent: no errors, schema facts
        # match step 2 again.
        command.upgrade(cfg, "head")
        _reupgraded_checks, reupgraded_indexes, reupgraded_nullable = _inspect(
            alembic_test_database_url
        )
        assert "ix_fetcher_run_fetcher_name_created_at" in reupgraded_indexes
        assert reupgraded_nullable is True
