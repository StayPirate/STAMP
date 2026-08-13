"""add system settings tables

Revision ID: c1ad47607809
Revises: 4e3e94583e96
Create Date: 2026-08-13 10:38:26.665916

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1ad47607809"
down_revision: str | None = "4e3e94583e96"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_table(
        "system_setting",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "setting_audit_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("setting_key", sa.String(length=100), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["setting_key"], ["system_setting.key"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_setting_audit_event_created_at",
        "setting_audit_event",
        ["created_at"],
    )
    op.create_index(
        "ix_setting_audit_event_setting_key",
        "setting_audit_event",
        ["setting_key"],
    )
    op.create_index(
        "ix_setting_audit_event_user_id",
        "setting_audit_event",
        ["user_id"],
    )

    # Seed the required `default_cvss_version` baseline row. `ON CONFLICT
    # DO NOTHING` makes this idempotent — safe to re-run and a no-op if the
    # row already exists (e.g. a previous partial run). See
    # docs/features/platform/system-settings.md (Bootstrap).
    op.execute(
        sa.text(
            "INSERT INTO system_setting (key, value) "
            "VALUES ('default_cvss_version', '3.1') "
            "ON CONFLICT (key) DO NOTHING"
        )
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index("ix_setting_audit_event_user_id", table_name="setting_audit_event")
    op.drop_index(
        "ix_setting_audit_event_setting_key", table_name="setting_audit_event"
    )
    op.drop_index("ix_setting_audit_event_created_at", table_name="setting_audit_event")
    op.drop_table("setting_audit_event")
    op.drop_table("system_setting")
