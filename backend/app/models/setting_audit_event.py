"""SettingAuditEvent model — audit trail for system setting modifications.

See `docs/data-model.md` (SettingAuditEvent) and
`docs/features/platform/system-settings.md` (Setting Audit Log) for the
full specification. This model implements the persistence root;
`SettingAuditLog.log_event()` (in `app.services.settings`) handles
validation and write.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import AuditEventMixin


class SettingAuditEvent(Base, AuditEventMixin):
    """Audit trail for administrative modifications to a `SystemSetting`.

    Inherits `id`, `created_at`, and the actor `user_id` from
    `AuditEventMixin`. Every setting audit event has a human actor —
    there are no system-initiated setting changes — enforced by
    `SettingAuditLog.log_event()` at the service layer (the `user_id`
    column remains nullable at the database level, consistent with
    every other audit event table).
    """

    __tablename__ = "setting_audit_event"
    __table_args__ = (Index("ix_setting_audit_event_setting_key", "setting_key"),)

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    setting_key: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("system_setting.key", ondelete="RESTRICT"),
        nullable=False,
    )
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
