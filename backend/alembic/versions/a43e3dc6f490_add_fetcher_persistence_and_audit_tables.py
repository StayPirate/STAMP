"""add fetcher persistence and audit tables

Revision ID: a43e3dc6f490
Revises: c1ad47607809
Create Date: 2026-08-16 11:30:34.190224

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a43e3dc6f490"
down_revision: str | None = "c1ad47607809"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_table(
        "fetcher_config",
        sa.Column("fetcher_name", sa.String(length=100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_override", sa.String(length=50), nullable=True),
        sa.Column("run_timeout", sa.Integer(), nullable=False),
        sa.Column("request_delay", sa.Float(), nullable=False),
        sa.Column(
            "custom_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("fetcher_name"),
    )
    op.create_table(
        "fetcher_run",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("fetcher_name", sa.String(length=100), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("items_created", sa.Integer(), nullable=False),
        sa.Column("items_updated", sa.Integer(), nullable=False),
        sa.Column("items_failed", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("error_traceback", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=20), nullable=False),
        sa.Column("triggered_by_user_id", sa.UUID(), nullable=True),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failure', 'partial')",
            name="chk_fetcher_run_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["fetcher_name"], ["fetcher_config.fetcher_name"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["triggered_by_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fetcher_run_fetcher_name_started_at",
        "fetcher_run",
        ["fetcher_name", "started_at"],
    )
    op.create_table(
        "fetcher_audit_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("fetcher_name", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["fetcher_name"], ["fetcher_config.fetcher_name"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fetcher_audit_event_created_at",
        "fetcher_audit_event",
        ["created_at"],
    )
    op.create_index(
        "ix_fetcher_audit_event_fetcher_name",
        "fetcher_audit_event",
        ["fetcher_name"],
    )
    op.create_index(
        "ix_fetcher_audit_event_user_id",
        "fetcher_audit_event",
        ["user_id"],
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index("ix_fetcher_audit_event_user_id", table_name="fetcher_audit_event")
    op.drop_index(
        "ix_fetcher_audit_event_fetcher_name", table_name="fetcher_audit_event"
    )
    op.drop_index("ix_fetcher_audit_event_created_at", table_name="fetcher_audit_event")
    op.drop_table("fetcher_audit_event")
    op.drop_index("ix_fetcher_run_fetcher_name_started_at", table_name="fetcher_run")
    op.drop_table("fetcher_run")
    op.drop_table("fetcher_config")
