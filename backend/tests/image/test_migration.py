"""Image smoke assertions for the identity root migration (P1-04).

Verifies container-observable outcomes of the one-shot `migrate` service
in docker-compose.smoke.yml, which runs `alembic upgrade head` as the
image's non-root user before the test suite starts (`compose up --wait`
already blocks until `migrate` exits cleanly — see the comment on that
service). These tests exercise the *result* of that migration through
the `api` container, which:

- Shares the exact same image and non-root user as `migrate` (neither
  service overrides `user:` in docker-compose.smoke.yml), so verifying
  the acting user here also verifies the user `migrate` ran as.
- Shares the same PostgreSQL database (`DATABASE_URL`), already migrated
  by `migrate` by the time these tests run.

`migrate` itself cannot be `compose exec`'d into directly: it is a
one-shot container with `restart: "no"` that has already exited by the
time pytest runs.

See docs/features/platform/testing-strategy.md (Image / Container Smoke
Testing, Growth Rule).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest

# Inline Python snippet run inside the `api` container to inspect the
# live (already-migrated) database schema via SQLAlchemy's inspector.
# Kept as a single string (not a helper script file) because it is
# short, self-contained, and only ever executed via `compose exec`.
_SCHEMA_CHECK_SCRIPT = """
import asyncio

from sqlalchemy import inspect

from app.database import engine


async def main() -> None:
    async with engine.connect() as conn:
        def _inspect(sync_conn):
            insp = inspect(sync_conn)
            tables = set(insp.get_table_names())
            user_checks = {
                c["name"] for c in insp.get_check_constraints("user")
            }
            user_role_checks = {
                c["name"] for c in insp.get_check_constraints("user_role")
            }
            return tables, user_checks, user_role_checks

        tables, user_checks, user_role_checks = await conn.run_sync(_inspect)

    assert "user" in tables, f"'user' table missing: {tables}"
    assert "user_role" in tables, f"'user_role' table missing: {tables}"
    assert "chk_user_auth_exclusive" in user_checks, user_checks
    assert "chk_user_role_role_valid" in user_role_checks, user_role_checks
    print("SCHEMA-OK")
    await engine.dispose()


asyncio.run(main())
"""


@pytest.mark.image
def test_migration_ran_as_non_root(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """The migration step runs as the image's non-root `appuser`.

    `migrate` and `api` share the same image and no per-service `user:`
    override, so confirming the acting user for `api` confirms it for
    `migrate` too.
    """
    result = compose_exec("api", "id", "-u")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() != "0", "container is running as root"


@pytest.mark.image
def test_no_alembic_drift(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`alembic check` reports no drift between models and the migrated DB."""
    result = compose_exec("api", "alembic", "check")
    assert result.returncode == 0, (
        f"alembic check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.image
def test_user_and_user_role_tables_and_constraints_exist(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`user`/`user_role` tables and their named CHECK constraints exist
    in the migrated database.
    """
    result = compose_exec("api", "python", "-c", _SCHEMA_CHECK_SCRIPT)
    assert result.returncode == 0, (
        f"schema check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "SCHEMA-OK" in result.stdout
