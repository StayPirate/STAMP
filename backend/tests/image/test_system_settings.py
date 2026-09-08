"""Image smoke assertions for system-settings bootstrap and lifespan
self-healing.

Verifies container-observable outcomes of `bootstrap_system_settings()`
and the FastAPI lifespan (`docs/features/platform/system-settings.md`,
Bootstrap and FastAPI Lifespan Ordering and Failure) that only manifest
when the `api` service's own entrypoint runs again inside the real
built image: restarting `api` self-heals a deleted `default_cvss_version`
row with no `SettingAuditEvent`, restarting `api` preserves an
administrator-selected `"4.0"` value, a missing required table prevents
`api` from ever becoming healthy, and a failed `migrate` step prevents
`api` from starting at all.

See docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing, Artifact-Risk Rule).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator

import httpx
import pytest

from tests.image.conftest import IsolatedComposeStack, wait_for_status

# Inline Python snippets run inside a fresh, throwaway container built
# from the `api` service's own definition (via the `compose_run`
# fixture — see its docstring), so they work regardless of whether the
# long-running `api` container is currently healthy, unhealthy, or
# exited. All connect to the same shared Postgres the primary stack
# already migrated.

_DELETE_DEFAULT_SETTING_SCRIPT = """
import asyncio

from sqlalchemy import text

from app.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM system_setting WHERE key = 'default_cvss_version'"
            )
        )
    await engine.dispose()
    print("DELETE-OK")


asyncio.run(main())
"""

_SET_CUSTOM_VALUE_SCRIPT = """
import asyncio

from sqlalchemy import text

from app.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE system_setting SET value = '4.0' "
                "WHERE key = 'default_cvss_version'"
            )
        )
    await engine.dispose()
    print("SET-CUSTOM-OK")


asyncio.run(main())
"""

_CHECK_VALUE_AND_AUDIT_COUNT_SCRIPT = """
import asyncio

from sqlalchemy import text

from app.database import engine


async def main() -> None:
    async with engine.connect() as conn:
        value_result = await conn.execute(
            text(
                "SELECT value FROM system_setting "
                "WHERE key = 'default_cvss_version'"
            )
        )
        value = value_result.scalar_one_or_none()
        count_result = await conn.execute(
            text("SELECT COUNT(*) FROM setting_audit_event")
        )
        count = count_result.scalar_one()
    await engine.dispose()
    print(f"VALUE={value}")
    print(f"AUDIT_COUNT={count}")


asyncio.run(main())
"""

_DROP_SETTINGS_TABLES_SCRIPT = """
import asyncio

from sqlalchemy import text

from app.database import engine


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE setting_audit_event"))
        await conn.execute(text("DROP TABLE system_setting"))
    await engine.dispose()
    print("DROP-OK")


asyncio.run(main())
"""

# Recreates both tables (matching the model/migration definitions
# exactly, since it uses the real SQLAlchemy metadata) and reseeds the
# baseline row — idempotent, safe to run whether or not the tables
# already exist with the correct data.
_RESTORE_AND_RESEED_SCRIPT = """
import asyncio

from sqlalchemy import text

from app.database import Base, engine
from app.models.setting_audit_event import SettingAuditEvent
from app.models.system_setting import SystemSetting


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[SystemSetting.__table__, SettingAuditEvent.__table__],
        )
        await conn.execute(
            text(
                "INSERT INTO system_setting (key, value) "
                "VALUES ('default_cvss_version', '3.1') "
                "ON CONFLICT (key) DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "UPDATE system_setting SET value = '3.1' "
                "WHERE key = 'default_cvss_version'"
            )
        )
    await engine.dispose()
    print("RESTORE-OK")


asyncio.run(main())
"""


def _restore_default_setting(
    compose_run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_run("api", "python", "-c", _RESTORE_AND_RESEED_SCRIPT)
    assert result.returncode == 0, (
        f"restore/reseed failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.fixture
def _default_setting_lifecycle(
    compose_run: Callable[..., subprocess.CompletedProcess[str]],
    compose_restart: Callable[..., subprocess.CompletedProcess[str]],
) -> Iterator[None]:
    """Ensure `system_setting`/`setting_audit_event` exist with the
    seeded default value before the test, and restore that exact known
    state afterward — recreating the tables if the test dropped them,
    resetting the value to `"3.1"`, and restarting `api` back to a
    healthy state — so later test modules in this session observe a
    clean baseline regardless of what this module's tests did.

    Uses `compose_run` (a fresh, throwaway container from the `api`
    service's own definition) rather than `compose_exec`, since the
    restore step must work even when the long-running `api` container
    has exited (the bootstrap-failure scenario below deliberately
    crashes it).
    """
    _restore_default_setting(compose_run)
    yield
    _restore_default_setting(compose_run)
    restart_result = compose_restart("api")
    assert restart_result.returncode == 0, (
        f"api restart failed during test cleanup "
        f"(stdout={restart_result.stdout!r} stderr={restart_result.stderr!r})"
    )


@pytest.mark.image
@pytest.mark.usefixtures("_default_setting_lifecycle")
class TestBootstrapSelfHealingAndPreservation:
    """`docs/features/platform/system-settings.md` (Bootstrap): the
    FastAPI lifespan self-heals a missing `default_cvss_version` row on
    restart and preserves an administrator-selected value.
    """

    def test_deleting_row_and_restarting_restores_default_with_no_audit_event(
        self,
        compose_run: Callable[..., subprocess.CompletedProcess[str]],
        compose_restart: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        delete_result = compose_run(
            "api", "python", "-c", _DELETE_DEFAULT_SETTING_SCRIPT
        )
        assert delete_result.returncode == 0, (
            f"delete failed (rc={delete_result.returncode}): "
            f"stdout={delete_result.stdout!r} stderr={delete_result.stderr!r}"
        )

        restart_result = compose_restart("api")
        assert restart_result.returncode == 0, (
            f"api restart failed "
            f"(stdout={restart_result.stdout!r} stderr={restart_result.stderr!r})"
        )

        check_result = compose_run(
            "api", "python", "-c", _CHECK_VALUE_AND_AUDIT_COUNT_SCRIPT
        )
        assert check_result.returncode == 0, (
            f"post-restart check failed (rc={check_result.returncode}): "
            f"stdout={check_result.stdout!r} stderr={check_result.stderr!r}"
        )
        assert "VALUE=3.1" in check_result.stdout, check_result.stdout
        assert "AUDIT_COUNT=0" in check_result.stdout, check_result.stdout

    def test_restarting_preserves_an_existing_custom_value(
        self,
        compose_run: Callable[..., subprocess.CompletedProcess[str]],
        compose_restart: Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        set_result = compose_run("api", "python", "-c", _SET_CUSTOM_VALUE_SCRIPT)
        assert set_result.returncode == 0, (
            f"set custom value failed (rc={set_result.returncode}): "
            f"stdout={set_result.stdout!r} stderr={set_result.stderr!r}"
        )

        restart_result = compose_restart("api")
        assert restart_result.returncode == 0, (
            f"api restart failed "
            f"(stdout={restart_result.stdout!r} stderr={restart_result.stderr!r})"
        )

        check_result = compose_run(
            "api", "python", "-c", _CHECK_VALUE_AND_AUDIT_COUNT_SCRIPT
        )
        assert check_result.returncode == 0, (
            f"post-restart check failed (rc={check_result.returncode}): "
            f"stdout={check_result.stdout!r} stderr={check_result.stderr!r}"
        )
        assert "VALUE=4.0" in check_result.stdout, check_result.stdout
        assert "AUDIT_COUNT=0" in check_result.stdout, check_result.stdout


@pytest.mark.image
@pytest.mark.usefixtures("_default_setting_lifecycle")
class TestBootstrapFailurePreventsServing:
    """`docs/features/platform/system-settings.md` (FastAPI Lifespan
    Ordering and Failure): a bootstrap failure aborts API startup —
    the process must never become healthy or serve requests.
    """

    def test_missing_required_table_prevents_api_from_becoming_healthy(
        self,
        compose_run: Callable[..., subprocess.CompletedProcess[str]],
        compose_restart: Callable[..., subprocess.CompletedProcess[str]],
        http_client: httpx.Client,
    ) -> None:
        drop_result = compose_run("api", "python", "-c", _DROP_SETTINGS_TABLES_SCRIPT)
        assert drop_result.returncode == 0, (
            f"drop tables failed (rc={drop_result.returncode}): "
            f"stdout={drop_result.stdout!r} stderr={drop_result.stderr!r}"
        )

        # This scenario expects startup to fail, so it opts out of the
        # fixture's normal readiness wait and observes the negative result.
        restart_result = compose_restart("api", wait_for_ready=False)
        assert restart_result.returncode == 0, (
            f"api restart command failed "
            f"(stdout={restart_result.stdout!r} stderr={restart_result.stderr!r})"
        )

        assert wait_for_status(http_client, expect_healthy=False, timeout=20.0), (
            "api unexpectedly became healthy despite the missing system_setting table"
        )


@pytest.mark.image
class TestFailedMigrationBlocksApiStartup:
    """`docs/deployment.md` (Startup Ordering): the self-contained
    smoke stack's `api` service depends on `migrate` reaching
    `service_completed_successfully` — an unsuccessful migration must
    prevent the `api` container from starting at all.

    Uses an isolated, independently-named compose project (not the
    primary stack shared by every other test in this suite) so this
    scenario's deliberately-broken `migrate` step cannot affect other
    tests.
    """

    def test_failed_migration_prevents_api_from_starting(
        self, isolated_compose_stack: IsolatedComposeStack
    ) -> None:
        override = (
            "services:\n"
            "  migrate:\n"
            '    command: ["alembic", "upgrade", '
            '"nonexistent_revision_id_smoke_test"]\n'
        )

        result = isolated_compose_stack.up(override)
        assert result.returncode != 0, (
            f"expected non-zero exit when migrate fails "
            f"(stdout={result.stdout!r} stderr={result.stderr!r})"
        )

        exec_result = isolated_compose_stack.exec_check("api", "true")
        assert exec_result.returncode != 0, (
            "api container unexpectedly became reachable despite the "
            f"migration failure (stdout={exec_result.stdout!r} "
            f"stderr={exec_result.stderr!r})"
        )
