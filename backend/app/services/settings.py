"""System settings service.

See `docs/features/platform/system-settings.md` for the full
specification: `bootstrap_system_settings()`, `get_default_cvss_version()`,
and the `SettingAuditLog` audit trail.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import SettingAuditEventType
from app.core.exceptions import ServiceError
from app.models.setting_audit_event import SettingAuditEvent
from app.models.system_setting import SystemSetting
from app.services.base_audit_log import BaseAuditLog

_DEFAULT_CVSS_VERSION_KEY = "default_cvss_version"
_DEFAULT_CVSS_VERSION_INITIAL_VALUE = "3.1"


class SettingsServiceError(ServiceError):
    """Base class for all exceptions raised by the settings service."""


class RequiredSystemSettingMissingError(SettingsServiceError):
    """The `default_cvss_version` row is absent from `system_setting`.

    Propagates to the caller; API handlers do not catch it, so the
    framework returns the global `500 INTERNAL_ERROR` response (see
    `docs/features/platform/system-settings.md`, Service Exceptions).
    """

    def __init__(self) -> None:
        super().__init__(
            f"Required system setting '{_DEFAULT_CVSS_VERSION_KEY}' is missing."
        )


async def bootstrap_system_settings(session: AsyncSession) -> None:
    """Idempotently ensure the `default_cvss_version` baseline row exists.

    Inserts `default_cvss_version = "3.1"` with `ON CONFLICT (key) DO
    NOTHING`, then flushes so database errors surface at this boundary.
    Never commits — the caller owns the transaction. Returns `None`
    whether it inserted the row or found an existing row (including an
    administrator-selected `"4.0"` value, which is preserved). Creates
    no audit event: this is idempotent initialization, not an
    administrative setting change. Concurrent callers are safe: at
    most one inserts the row and every successful caller observes a
    completed insert or conflict before returning. Database
    availability, missing-table/schema, constraint, and flush failures
    propagate unchanged — this function does not catch, retry, or
    return partial success.
    """
    stmt = (
        pg_insert(SystemSetting)
        .values(
            key=_DEFAULT_CVSS_VERSION_KEY,
            value=_DEFAULT_CVSS_VERSION_INITIAL_VALUE,
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )
    await session.execute(stmt)
    await session.flush()


async def get_default_cvss_version(session: AsyncSession) -> str:
    """Return the persisted `default_cvss_version` value.

    Reads the `default_cvss_version` row from `system_setting`. Raises
    `RequiredSystemSettingMissingError` if the row is absent — never
    substitutes a hardcoded or environment-derived value. Database
    availability and schema errors propagate unchanged. Performs no
    writes and creates no audit event.
    """
    result = await session.execute(
        select(SystemSetting.value).where(
            SystemSetting.key == _DEFAULT_CVSS_VERSION_KEY
        )
    )
    value = result.scalar_one_or_none()
    if value is None:
        raise RequiredSystemSettingMissingError()
    return value


class SettingAuditLog(BaseAuditLog):
    """Audit trail for system setting modifications.

    See `docs/features/platform/system-settings.md` (Setting Audit
    Log).
    """

    name = "setting"
    description = "System setting modifications"
    model_class = SettingAuditEvent

    @classmethod
    async def log_event(  # type: ignore[override]
        cls,
        session: AsyncSession,
        *,
        event_type: SettingAuditEventType,
        setting_key: str,
        user_id: uuid.UUID | None,
        old_value: str | None,
        new_value: str,
    ) -> None:
        """Create one `SettingAuditEvent` in the caller's transaction.

        Validates that `event_type` is a `SettingAuditEventType` member
        (this classification enum has no database CHECK constraint) and
        that `user_id` is provided — every setting change is
        human-initiated, so a missing actor raises `ValueError` before
        any database operation. The foreign key on `setting_key` and
        the NOT NULL constraint on `new_value` validate the remaining
        required fields at the database level.

        Creates exactly one event and flushes it before returning.
        Never commits or rolls back. Each invocation creates a new
        event and is therefore not idempotent — callers MUST invoke it
        only when a setting value actually changes. `ValueError` and
        all database/flush exceptions propagate to the caller.
        """
        if not isinstance(event_type, SettingAuditEventType):
            raise ValueError(
                f"event_type must be a SettingAuditEventType member, got {event_type!r}"
            )
        if user_id is None:
            raise ValueError("user_id is required for setting audit events")

        await super().log_event(
            session,
            event_type=event_type.value,
            setting_key=setting_key,
            user_id=user_id,
            old_value=old_value,
            new_value=new_value,
        )
