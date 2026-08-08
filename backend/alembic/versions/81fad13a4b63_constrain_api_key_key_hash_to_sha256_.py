"""constrain api_key key_hash to sha256 hex format

Revision ID: 81fad13a4b63
Revises: 1327f6343056
Create Date: 2026-08-08 10:43:13.827498

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "81fad13a4b63"
down_revision: str | None = "1327f6343056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_check_constraint(
        "chk_api_key_hash_is_sha256_hex",
        "api_key",
        "key_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_constraint("chk_api_key_hash_is_sha256_hex", "api_key", type_="check")
