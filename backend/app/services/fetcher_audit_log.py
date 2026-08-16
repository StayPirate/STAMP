"""Fetcher audit trail service.

See `docs/features/platform/fetcher-infrastructure.md` (Audit Trail,
FetcherAuditLog Service, Event Field Values) for the full
specification: the `FetcherAuditEventType` contract (human-actor
requirement and `old_value`/`new_value`/`detail` per event type) and
the deterministic validation order implemented by
`FetcherAuditLog.log_event()`.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import FetcherAuditEventType
from app.models.fetcher_audit_event import FetcherAuditEvent
from app.services.base_audit_log import BaseAuditLog

# Standard `config_changed` fields whose `detail` payload is exactly
# `{"field": "<field_name>"}` — no `key`.
_STANDARD_FIELDS: Final = frozenset(
    {"schedule_override", "run_timeout", "request_delay"}
)

# The single custom-setting field name, whose `detail` payload MUST
# additionally carry a `key` naming the specific setting.
_CUSTOM_SETTINGS_FIELD: Final = "custom_settings"

_ALLOWED_DETAIL_KEYS: Final = frozenset({"field", "key"})


def _validate_config_changed_detail(detail: Mapping[str, str] | None) -> None:
    """Validate the `detail` payload for a `CONFIG_CHANGED` event.

    Raises `ValueError` for: a missing payload, a non-mapping payload,
    an unknown key, a missing `field` key, an unrecognized `field`
    value, a `key` present for a standard field, or a missing `key` for
    the `custom_settings` field.
    """
    if detail is None:
        raise ValueError("detail is required for event type 'config_changed'")
    if not isinstance(detail, Mapping):
        raise ValueError("detail must be a JSON object")

    unknown_keys = set(detail) - _ALLOWED_DETAIL_KEYS
    if unknown_keys:
        raise ValueError(
            f"detail contains unsupported keys for event type "
            f"'config_changed': {sorted(unknown_keys)}"
        )

    field = detail.get("field")
    if field is None:
        raise ValueError("detail.field is required for event type 'config_changed'")

    if field in _STANDARD_FIELDS:
        if "key" in detail:
            raise ValueError(
                f"detail.key must not be provided for standard field '{field}'"
            )
    elif field == _CUSTOM_SETTINGS_FIELD:
        if "key" not in detail:
            raise ValueError(
                "detail.key is required when detail.field is 'custom_settings'"
            )
    else:
        raise ValueError(f"detail.field has an unrecognized value: {field!r}")


def _validate_no_payload(
    event_type: FetcherAuditEventType,
    old_value: str | None,
    new_value: str | None,
    detail: Mapping[str, str] | None,
) -> None:
    """Validate that `old_value`, `new_value`, and `detail` are all
    `None` — the contract for `disabled`, `enabled`, and `triggered`."""
    if old_value is not None:
        raise ValueError(f"old_value must be None for event type '{event_type}'")
    if new_value is not None:
        raise ValueError(f"new_value must be None for event type '{event_type}'")
    if detail is not None:
        raise ValueError(f"detail must be None for event type '{event_type}'")


class FetcherAuditLog(BaseAuditLog):
    """Audit trail for administrative actions on fetchers.

    See `docs/features/platform/fetcher-infrastructure.md` (Audit
    Trail).
    """

    name = "fetcher"
    description = "Administrative actions on fetchers"
    model_class = FetcherAuditEvent

    @classmethod
    async def log_event(  # type: ignore[override]
        cls,
        session: AsyncSession,
        *,
        event_type: FetcherAuditEventType,
        fetcher_name: str,
        user_id: uuid.UUID | None,
        old_value: str | None = None,
        new_value: str | None = None,
        detail: Mapping[str, str] | None = None,
    ) -> None:
        """Create one `FetcherAuditEvent` in the caller's transaction.

        Validates that `event_type` is a `FetcherAuditEventType` member,
        that `user_id` is provided (every fetcher admin action is
        human-initiated), and the event-type-specific `old_value`/
        `new_value`/`detail` combination (see
        `docs/features/platform/fetcher-infrastructure.md`, Event Field
        Values) before any database operation. Every violation raises
        `ValueError`; no event is inserted.

        Never commits or rolls back — flushes the pending insert before
        returning, per `BaseAuditLog.log_event()`. Each invocation
        creates a new event and is therefore not idempotent; callers
        MUST invoke it only when a mutation actually occurs.
        """
        if not isinstance(event_type, FetcherAuditEventType):
            raise ValueError(
                f"event_type must be a FetcherAuditEventType member, got {event_type!r}"
            )
        if user_id is None:
            raise ValueError("user_id is required for fetcher audit events")

        if event_type is FetcherAuditEventType.CONFIG_CHANGED:
            _validate_config_changed_detail(detail)
        else:
            _validate_no_payload(event_type, old_value, new_value, detail)

        await super().log_event(
            session,
            event_type=event_type.value,
            fetcher_name=fetcher_name,
            user_id=user_id,
            old_value=old_value,
            new_value=new_value,
            detail=dict(detail) if detail is not None else None,
        )
