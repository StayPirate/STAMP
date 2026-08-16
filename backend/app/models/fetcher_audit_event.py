"""FetcherAuditEvent model — audit trail for administrative actions on
fetchers.

See `docs/data-model.md` (FetcherAuditEvent) and
`docs/features/platform/fetcher-infrastructure.md` (Data Model —
FetcherAuditEvent, Event Field Values) for the full specification.
This model implements the persistence root; `FetcherAuditLog.log_event()`
(in `app.services.fetcher_audit_log`) handles validation and write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mixins import AuditEventMixin

if TYPE_CHECKING:
    from app.models.fetcher_config import FetcherConfig
    from app.models.user import User


class FetcherAuditEvent(Base, AuditEventMixin):
    """Audit trail for administrative modifications to a `FetcherConfig`.

    Inherits `id`, `created_at`, and the actor `user_id` from
    `AuditEventMixin`. Every fetcher audit event has a human actor —
    there are no system-initiated fetcher audit events — enforced by
    `FetcherAuditLog.log_event()` at the service layer (the `user_id`
    column remains nullable at the database level, consistent with
    every other audit event table).
    """

    __tablename__ = "fetcher_audit_event"
    __table_args__ = (Index("ix_fetcher_audit_event_fetcher_name", "fetcher_name"),)

    fetcher_name: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("fetcher_config.fetcher_name", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    config: Mapped[FetcherConfig] = relationship(
        "FetcherConfig", back_populates="audit_events"
    )
    # Read-only convenience relationship for API consumers. Deliberately
    # unidirectional and NOT back-populated: User gets no reverse
    # collection, mirroring SettingAuditEvent.actor and
    # IdentityAuditEvent.actor. `viewonly=True` because this model is
    # append-only and this relationship must never be used to persist
    # changes through ORM cascade.
    actor: Mapped[User | None] = relationship(
        "User", foreign_keys="FetcherAuditEvent.user_id", viewonly=True
    )
