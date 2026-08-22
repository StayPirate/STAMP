"""add hard time limit to fetcher run

Revision ID: 2972274112d2
Revises: a4f22788de31
Create Date: 2026-08-22 16:48:03.172321

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2972274112d2"
down_revision: str | None = "a4f22788de31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema.

    See `docs/features/platform/fetcher-infrastructure.md` (Per-Run Hard
    Time Limit) for the full specification. Adds the nullable
    `hard_time_limit_seconds` column. No backfill is performed: existing
    rows (`queued`, `running`, or terminal) are left `NULL`. Stale
    evaluation for `running` rows with a `NULL` value falls back to the
    fetcher's current `FetcherConfig.run_timeout` at read time (see
    "Stale Run Detection", Running Stale Threshold).
    """
    op.add_column(
        "fetcher_run",
        sa.Column("hard_time_limit_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_column("fetcher_run", "hard_time_limit_seconds")
