"""Request/response/query schemas for the system settings read and audit
log endpoints.

See `docs/features/platform/system-settings.md` (Get System Settings,
List Settings Audit Events) for the authoritative request/response
contract these schemas implement.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import PaginationMeta, UserReference


class SystemSettingsData(BaseModel):
    """The settings object returned by `GET /api/v1/admin/settings`."""

    default_cvss_version: str


class SystemSettingsResponse(BaseModel):
    """Response body for `GET /api/v1/admin/settings`."""

    data: SystemSettingsData


class SettingAuditQuery(BaseModel):
    """Query parameters for `GET /api/v1/admin/settings/audit-log`.

    `event_type` is intentionally `list[str]`, not
    `list[SettingAuditEventType]`: an invalid value must be silently
    ignored and produce an empty result (`docs/api-spec.md`, Enum Filter
    Validation) rather than the schema-validation `422` a typed enum
    field would raise. The route handler parses each value against
    `SettingAuditEventType` itself.

    `from_date`/`to_date` are already-parsed `date`/`datetime` values by
    the time this model is constructed — the API layer's
    `app.core.dates.parse_date_range_bound()` performs the strict ISO
    8601 parsing (and its `422` rejection) before this model sees them.
    An inverted normalized range is validated separately by
    `app.core.dates.validate_date_range_order()` (`400
    DATE_RANGE_INVERTED`).
    """

    event_type: list[str] = Field(default_factory=list)
    setting_key: str | None = None
    actor: str | None = None
    from_date: date | datetime | None = None
    to_date: date | datetime | None = None
    page: int = Field(default=1, ge=1, le=2_147_483_647)
    per_page: int = Field(default=20, ge=1, le=100)


class SettingAuditEventData(BaseModel):
    """One event in the setting audit log response
    (`system-settings.md`, List Settings Audit Events). `actor` is
    always the complete current user reference — setting audit events
    require a human actor and user rows cannot be hard-deleted, so it
    is never `null`."""

    id: UUID
    event_type: str
    setting_key: str
    old_value: str | None
    new_value: str
    created_at: datetime
    actor: UserReference


class SettingAuditListResponse(BaseModel):
    """Response body for `GET /api/v1/admin/settings/audit-log`."""

    data: list[SettingAuditEventData]
    meta: PaginationMeta
