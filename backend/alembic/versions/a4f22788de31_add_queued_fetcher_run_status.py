"""add queued fetcher run status

Revision ID: a4f22788de31
Revises: 4b4f7187b541
Create Date: 2026-08-22 11:54:36.855677

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f22788de31"
down_revision: str | None = "4b4f7187b541"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema.

    See `docs/features/platform/fetcher-infrastructure.md` (Concurrency
    Control, FetcherRunStatus Enum) for the `queued` lifecycle this
    migration enables: a manual `FetcherRun` is now created as `queued`
    and only transitions to `running` once a worker atomically adopts
    it, so `started_at` must be nullable for the duration it spends
    unadopted.
    """
    op.drop_constraint("chk_fetcher_run_status_valid", "fetcher_run", type_="check")
    op.create_check_constraint(
        "chk_fetcher_run_status_valid",
        "fetcher_run",
        "status IN ('queued', 'running', 'success', 'failure', 'partial')",
    )
    op.alter_column("fetcher_run", "started_at", nullable=True)
    op.create_index(
        "ix_fetcher_run_fetcher_name_created_at",
        "fetcher_run",
        ["fetcher_name", "created_at"],
    )


def downgrade() -> None:
    """Downgrade database schema.

    Normalizes data before restoring the older, stricter schema (`status`
    without `queued`, `started_at NOT NULL`):

    1. Any row still `queued` is converted to `failure` with a synthetic
       `started_at`/`finished_at` (both set to `created_at`) and
       `duration_seconds = 0`, so it satisfies the restored `NOT NULL`
       constraint. This is a lossy but reversible-in-shape normalization
       — a `queued` run has no real `started_at` under the current
       schema.
    2. Any row still left with `started_at IS NULL` after step 1 (a
       pre-adoption `failure` finalized by the disabled/deregistered/
       stale-queued paths — see fetcher-infrastructure.md) receives the
       same synthetic `started_at = created_at` and
       `duration_seconds = 0`, matching the pre-`queued` behavior of
       `finalize_manual_run_as_failure()`.
    """
    op.execute(
        "UPDATE fetcher_run "
        "SET status = 'failure', "
        "    started_at = created_at, "
        "    finished_at = created_at, "
        "    duration_seconds = 0, "
        "    error_message = 'Reverted to a schema version that does "
        "not support the queued status' "
        "WHERE status = 'queued'"
    )
    op.execute(
        "UPDATE fetcher_run "
        "SET started_at = created_at, "
        "    duration_seconds = COALESCE(duration_seconds, 0) "
        "WHERE started_at IS NULL"
    )
    op.drop_index("ix_fetcher_run_fetcher_name_created_at", table_name="fetcher_run")
    op.drop_constraint("chk_fetcher_run_status_valid", "fetcher_run", type_="check")
    op.create_check_constraint(
        "chk_fetcher_run_status_valid",
        "fetcher_run",
        "status IN ('running', 'success', 'failure', 'partial')",
    )
    op.alter_column("fetcher_run", "started_at", nullable=False)
