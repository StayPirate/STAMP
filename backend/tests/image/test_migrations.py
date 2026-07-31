"""Image smoke assertions for the identity root migration.

Verifies that the one-shot ``migrate`` service (docker-compose.smoke.yml)
actually applies the Alembic migration that creates the ``user`` and
``user_role`` tables — including their named CHECK/UNIQUE constraints —
against a real, non-root, containerized run of the built image. See
docs/features/platform/testing-strategy.md (Growth Rule) and
docs/data-model.md (User, UserRole).

These tests query PostgreSQL directly (via ``compose_exec`` on the
``postgres`` service) rather than the ``api`` service, because the
tables/constraints are database-level artifacts with no HTTP-visible
surface at this phase (no endpoints exist yet).
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable

import pytest


def _psql(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]], query: str
) -> subprocess.CompletedProcess[str]:
    return compose_exec(
        "postgres",
        "psql",
        "-U",
        "sentinel",
        "-d",
        "sentinel",
        "-tAc",
        query,
    )


@pytest.mark.image
class TestMigrationAppliesIdentityRootSchema:
    """The `migrate` one-shot service applies the identity root migration."""

    def test_alembic_version_is_populated(
        self, compose_exec: Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """A non-empty `alembic_version` row proves the one-shot `migrate`
        service ran `alembic upgrade head` to completion — migrations are
        never applied by the long-running `api` service itself."""
        result = _psql(compose_exec, "SELECT version_num FROM alembic_version")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() != ""

    def test_user_table_has_named_check_constraint(
        self, compose_exec: Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        result = _psql(
            compose_exec,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'public.\"user\"'::regclass "
            "AND conname = 'chk_user_auth_exclusive'",
        )
        assert result.returncode == 0, result.stderr
        assert "chk_user_auth_exclusive" in result.stdout

    def test_user_role_table_has_named_constraints(
        self, compose_exec: Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        result = _psql(
            compose_exec,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'public.user_role'::regclass "
            "AND conname IN ('chk_user_role_role_valid', "
            "'uq_user_role_user_id_role_group_name') ORDER BY conname",
        )
        assert result.returncode == 0, result.stderr
        assert "chk_user_role_role_valid" in result.stdout
        assert "uq_user_role_user_id_role_group_name" in result.stdout
