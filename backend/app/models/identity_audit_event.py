"""IdentityAuditEvent model — audit trail for identity operations.

See `docs/data-model.md` (IdentityAuditEvent) and
`docs/features/identity/identity-audit-log.md` (Data Model) for the
full specification. This model implements the persistence root;
`IdentityAuditLog.log_event()` handles validation and write.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import AuditEventMixin

if TYPE_CHECKING:
    from app.models.user import User


class IdentityAuditEvent(Base, AuditEventMixin):
    """Audit trail for user lifecycle, roles, API keys, and role
    mapping administration.

    Inherits `id`, `created_at`, and the actor `user_id` from
    `AuditEventMixin` (see that mixin's docstring for the append-only,
    no-`updated_at` rationale). `target_user_id` distinguishes "who
    acted" from "who was affected" — see
    `docs/features/identity/identity-audit-log.md` (Notes) for the
    role-mapping-event exception (`target_user_id` is NULL because the
    action affects a configuration rule, not a specific user).
    """

    __tablename__ = "identity_audit_event"
    __table_args__ = (
        Index("ix_identity_audit_event_target_user_id", "target_user_id"),
    )

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=True,
    )
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Read-only convenience relationships for API consumers. Deliberately
    # unidirectional and NOT back-populated: User gets no reverse
    # collection for either FK, mirroring ApiKey.revoking_user.
    # `viewonly=True` because this model is append-only and these
    # relationships must never be used to persist changes through ORM
    # cascade.
    actor: Mapped[User | None] = relationship(
        "User", foreign_keys="IdentityAuditEvent.user_id", viewonly=True
    )
    target_user: Mapped[User | None] = relationship(
        "User", foreign_keys=[target_user_id], viewonly=True
    )
