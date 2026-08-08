"""Test-only concrete `AuditEventMixin` subclass.

`AuditEventMixin` has no physical table of its own (see
`docs/features/platform/audit-trail-infrastructure.md`) — it only
supplies columns to concrete audit trail models. This module provides a
minimal concrete model so tests can exercise the mixin's columns and
`BaseAuditLog`'s generic behavior independently of any specific
production audit trail (e.g., `IdentityAuditEvent`,
`backend/app/models/identity_audit_event.py`), which additionally
enforce their own domain-specific validation rules.

Not a fixture — see `docs/features/platform/testing-strategy.md`
(Directory Structure) for the distinction between `conftest.py`
(fixtures) and `support/` (importable test-only classes).
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import AuditEventMixin
from app.models.user import User


class SampleAuditEvent(Base, AuditEventMixin):
    """A minimal, test-only audit event table.

    Adds a single domain-specific column (`event_type`) plus a
    relationship over the mixin's own `user_id` column — the
    relationship exists solely so tests can verify that
    `BaseAuditLog.log_event()` rejects relationship kwargs (they are
    not mapped columns).
    """

    __tablename__ = "sample_audit_event"

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)

    actor: Mapped[User | None] = relationship(
        "User", foreign_keys="SampleAuditEvent.user_id", viewonly=True
    )
