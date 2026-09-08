"""OCI artifact probes for the packaged Alembic migration chain.

Focused migration tests own exhaustive schema, constraint, index, downgrade,
and re-upgrade coverage. These checks verify only that the candidate's migrate
service runs as non-root, the packaged migration files reach the expected head
and seed, and the packaged models report no Alembic drift.

See ``docs/features/platform/testing-strategy.md`` (Artifact-Risk Rule).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

_HEAD_AND_SEED_CHECK_SCRIPT = """
import asyncio

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.database import engine


async def main() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    expected_head = script.get_current_head()
    async with engine.connect() as conn:
        version_result = await conn.execute(
            text("SELECT version_num FROM alembic_version")
        )
        version = version_result.scalar_one()
        seed = (
            await conn.execute(
                text(
                    "SELECT value FROM system_setting "
                    "WHERE key = 'default_cvss_version'"
                )
            )
        ).scalar_one_or_none()
    assert version == expected_head, (version, expected_head)
    assert seed == "3.1", seed
    print("MIGRATION-HEAD-AND-SEED-OK")
    await engine.dispose()


asyncio.run(main())
"""


@pytest.mark.image
def test_migration_ran_as_non_root(
    compose_run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_run("migrate", "id", "-u")
    assert result.returncode == 0, (
        f"expected exit 0 for id -u (stdout={result.stdout!r}, "
        f"stderr={result.stderr!r})"
    )
    assert result.stdout.strip() != "0", "migrate service runs as root"


@pytest.mark.image
def test_packaged_migrations_reach_head_and_seed(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "python", "-c", _HEAD_AND_SEED_CHECK_SCRIPT)
    assert result.returncode == 0, (
        f"migration head/seed check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "MIGRATION-HEAD-AND-SEED-OK" in result.stdout


@pytest.mark.image
def test_no_alembic_drift(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    result = compose_exec("api", "alembic", "check")
    assert result.returncode == 0, (
        f"alembic check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
