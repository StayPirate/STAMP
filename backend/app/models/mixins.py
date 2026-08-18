"""Shared SQLAlchemy mixins.

See `docs/features/platform/audit-trail-infrastructure.md` for the full
specification of `AuditEventMixin`, and `docs/data-model.md` (Shared
Structures) for the column reference.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class AuditEventMixin:
    """Common columns for every audit trail table.

    Every audit event SQLAlchemy model MUST inherit from this mixin
    (`docs/conventions.md`, Audit Trail). It has no physical table of
    its own — it only supplies columns to concrete subclasses. Audit
    event tables are append-only: this mixin deliberately has no
    `updated_at` column.

    The FK on `user_id` uses `ON DELETE RESTRICT` explicitly (even
    though it is the PostgreSQL default) to make the constraint visible
    and prevent accidental data loss — Sentinel only soft-deletes
    (deactivates) users, never hard-deletes them. Both `created_at` and
    `user_id` are indexed here so every concrete audit event table
    inherits these indexes automatically (see the owning spec,
    Indexing).
    """

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid7,
        server_default=text("uuidv7()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
