"""add uuidv7 server default to primary keys

Revision ID: 4b4f7187b541
Revises: a43e3dc6f490
Create Date: 2026-08-18 16:43:03.239328

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b4f7187b541"
down_revision: str | None = "a43e3dc6f490"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Every table with a UUID primary key (`docs/data-model.md`, Notes).
# Written by hand: at the time this migration was authored,
# `env.py` had not yet enabled `compare_server_default=True`, so
# Alembic's autogenerate did not detect this `server_default` change
# (`compare_server_default` defaults to `False`). `env.py` now enables
# it, closing the gap for future `server_default` changes.
_UUID_PK_TABLES: tuple[str, ...] = (
    "user",
    "api_key",
    "user_role",
    "session",
    "fetcher_run",
    "fetcher_audit_event",
    "identity_audit_event",
    "setting_audit_event",
)


def upgrade() -> None:
    """Upgrade database schema."""
    for table_name in _UUID_PK_TABLES:
        op.alter_column(
            table_name,
            "id",
            server_default=sa.text("uuidv7()"),
        )


def downgrade() -> None:
    """Downgrade database schema."""
    for table_name in _UUID_PK_TABLES:
        op.alter_column(
            table_name,
            "id",
            server_default=None,
        )
