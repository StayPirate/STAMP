"""add identity audit event table

Revision ID: 4e3e94583e96
Revises: 81fad13a4b63
Create Date: 2026-08-08 18:35:01.970604

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4e3e94583e96"
down_revision: str | None = "81fad13a4b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_table(
        "identity_audit_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("target_user_id", sa.UUID(), nullable=True),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["target_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_identity_audit_event_created_at",
        "identity_audit_event",
        ["created_at"],
    )
    op.create_index(
        "ix_identity_audit_event_target_user_id",
        "identity_audit_event",
        ["target_user_id"],
    )
    op.create_index(
        "ix_identity_audit_event_user_id",
        "identity_audit_event",
        ["user_id"],
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index("ix_identity_audit_event_user_id", table_name="identity_audit_event")
    op.drop_index(
        "ix_identity_audit_event_target_user_id", table_name="identity_audit_event"
    )
    op.drop_index(
        "ix_identity_audit_event_created_at", table_name="identity_audit_event"
    )
    op.drop_table("identity_audit_event")
