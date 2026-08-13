"""Image smoke assertions for the identity and system-settings schema
migrations.

Verifies container-observable outcomes of the one-shot `migrate` service
in docker-compose.smoke.yml, which runs `alembic upgrade head` as the
image's non-root user before the test suite starts (`compose up --wait`
already blocks until `migrate` exits cleanly — see the comment on that
service).

The non-root identity is verified directly against the `migrate`
service's own definition via `compose run` (a fresh, throwaway
container reusing that exact service's image/user/environment — see
the `compose_run` fixture), since the one-shot `migrate` container
itself has already exited by the time pytest runs and cannot be
`compose exec`'d into.

The remaining tests exercise the *result* of the migration through the
`api` container, which shares the same PostgreSQL database
(`DATABASE_URL`), already migrated by `migrate` by the time these tests
run.

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

from sqlalchemy import inspect, text

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
            session_indexes = {
                idx["name"]: idx for idx in insp.get_indexes("session")
            }
            api_key_indexes = {
                idx["name"]: idx for idx in insp.get_indexes("api_key")
            }
            api_key_checks = {
                c["name"] for c in insp.get_check_constraints("api_key")
            }
            identity_audit_event_indexes = {
                idx["name"]: idx
                for idx in insp.get_indexes("identity_audit_event")
            }
            setting_audit_event_indexes = {
                idx["name"]: idx
                for idx in insp.get_indexes("setting_audit_event")
            }
            setting_audit_event_fks = insp.get_foreign_keys("setting_audit_event")
            return (
                tables,
                user_checks,
                user_role_checks,
                session_indexes,
                api_key_indexes,
                api_key_checks,
                identity_audit_event_indexes,
                setting_audit_event_indexes,
                setting_audit_event_fks,
            )

        (
            tables,
            user_checks,
            user_role_checks,
            session_indexes,
            api_key_indexes,
            api_key_checks,
            identity_audit_event_indexes,
            setting_audit_event_indexes,
            setting_audit_event_fks,
        ) = await conn.run_sync(_inspect)

        seed_result = await conn.execute(
            text(
                "SELECT value FROM system_setting "
                "WHERE key = 'default_cvss_version'"
            )
        )
        seed_value = seed_result.scalar_one_or_none()

    assert "user" in tables, f"'user' table missing: {tables}"
    assert "user_role" in tables, f"'user_role' table missing: {tables}"
    assert "session" in tables, f"'session' table missing: {tables}"
    assert "api_key" in tables, f"'api_key' table missing: {tables}"
    assert "identity_audit_event" in tables, (
        f"'identity_audit_event' table missing: {tables}"
    )
    assert "system_setting" in tables, f"'system_setting' table missing: {tables}"
    assert "setting_audit_event" in tables, (
        f"'setting_audit_event' table missing: {tables}"
    )
    assert "chk_user_auth_exclusive" in user_checks, user_checks
    assert "chk_user_role_role_valid" in user_role_checks, user_role_checks
    assert "ix_session_user_id_is_active" in session_indexes, session_indexes
    assert "ix_api_key_user_id_revoked_at" in api_key_indexes, api_key_indexes
    assert "uq_api_key_user_id_name_active" in api_key_indexes, api_key_indexes
    partial_index = api_key_indexes["uq_api_key_user_id_name_active"]
    assert partial_index["unique"] is True, partial_index
    assert (
        "revoked_at"
        in partial_index["dialect_options"]["postgresql_where"]
    ), partial_index
    assert "chk_api_key_hash_is_sha256_hex" in api_key_checks, api_key_checks
    assert (
        "ix_identity_audit_event_created_at" in identity_audit_event_indexes
    ), identity_audit_event_indexes
    assert (
        "ix_identity_audit_event_user_id" in identity_audit_event_indexes
    ), identity_audit_event_indexes
    assert (
        "ix_identity_audit_event_target_user_id" in identity_audit_event_indexes
    ), identity_audit_event_indexes
    assert (
        "ix_setting_audit_event_created_at" in setting_audit_event_indexes
    ), setting_audit_event_indexes
    assert (
        "ix_setting_audit_event_user_id" in setting_audit_event_indexes
    ), setting_audit_event_indexes
    assert (
        "ix_setting_audit_event_setting_key" in setting_audit_event_indexes
    ), setting_audit_event_indexes
    setting_audit_event_fk_tables = {
        fk["referred_table"] for fk in setting_audit_event_fks
    }
    assert setting_audit_event_fk_tables == {"system_setting", "user"}, (
        setting_audit_event_fks
    )
    assert seed_value == "3.1", (
        f"default_cvss_version seed value mismatch: {seed_value!r}"
    )
    print("SCHEMA-OK")
    await engine.dispose()


asyncio.run(main())
"""


@pytest.mark.image
def test_migration_ran_as_non_root(
    compose_run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """The migration step runs as the image's non-root `appuser`.

    Verified directly against the `migrate` service's own definition via
    `compose run --rm --no-deps migrate id -u` (a fresh, throwaway
    container built from that exact service configuration — image,
    user, environment — with its default command overridden), rather
    than inferred from the long-running `api` service that happens to
    share the same image and no `user:` override. This also exercises
    the actual command `migrate` runs *as* — the same non-root identity
    that ran `alembic upgrade head` moments earlier in the primary
    stack.
    """
    result = compose_run("migrate", "id", "-u")
    assert result.returncode == 0, (
        f"expected exit 0 for id -u (stdout={result.stdout!r}, "
        f"stderr={result.stderr!r})"
    )
    assert result.stdout.strip() != "0", "migrate service runs as root"


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
def test_identity_and_settings_schema_tables_and_constraints_exist(
    compose_exec: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """`user`/`user_role`/`session`/`api_key`/`system_setting`/
    `setting_audit_event` tables, their named CHECK constraints, the
    `session`/`api_key`/`setting_audit_event` indexes (including the
    partial unique predicate), the `setting_audit_event` foreign keys,
    and the seeded `default_cvss_version = "3.1"` row all exist in the
    migrated database (see docs/features/platform/system-settings.md,
    Bootstrap).
    """
    result = compose_exec("api", "python", "-c", _SCHEMA_CHECK_SCRIPT)
    assert result.returncode == 0, (
        f"schema check failed (rc={result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "SCHEMA-OK" in result.stdout
