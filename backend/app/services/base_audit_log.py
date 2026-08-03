"""Shared audit trail service-layer base class.

See `docs/features/platform/audit-trail-infrastructure.md` for the full
specification: registry semantics, `log_event()` validation and
atomicity contract, date filtering convention, and actor filtering
convention.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from typing import Any, ClassVar

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import class_mapper

from app.models.mixins import AuditEventMixin
from app.models.user import User

# Global registry of all BaseAuditLog subclasses, keyed by `name`.
# Populated exclusively by __init_subclass__ — never written to
# directly. Module-level mutable cache: tests that define throwaway
# subclasses MUST snapshot/restore this dict (see
# docs/features/platform/testing-strategy.md, Test Independence).
AUDIT_LOG_REGISTRY: dict[str, type[BaseAuditLog]] = {}

_REQUIRED_ATTRS = ("name", "description", "model_class")


class BaseAuditLog:
    """Base for all audit trail implementations.

    Every audit trail in Sentinel MUST be implemented as a subclass of
    this class (`docs/conventions.md`, Audit Trail). Subclasses declare
    `name`, `description`, and `model_class` as class attributes; they
    are auto-registered in `AUDIT_LOG_REGISTRY` when the subclass is
    defined.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    model_class: ClassVar[type[AuditEventMixin]]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        missing = [attr for attr in _REQUIRED_ATTRS if getattr(cls, attr, None) is None]
        if missing:
            raise TypeError(
                f"{cls.__name__} must define {', '.join(missing)} "
                "before it can be registered as a BaseAuditLog subclass"
            )
        if cls.name in AUDIT_LOG_REGISTRY:
            existing = AUDIT_LOG_REGISTRY[cls.name]
            raise ValueError(
                f"Audit trail name '{cls.name}' is already registered by "
                f"{existing.__name__}; cannot register {cls.__name__}"
            )
        AUDIT_LOG_REGISTRY[cls.name] = cls

    @classmethod
    async def log_event(cls, session: AsyncSession, **kwargs: Any) -> None:
        """Create an audit record in the caller's transaction.

        Inserts and flushes exactly one `model_class` row using the
        given `AsyncSession`. Never commits and never catches
        exceptions independently — the caller's transaction governs
        atomicity (see the owning spec, Atomicity).

        Every kwarg MUST correspond to a mapped column of `model_class`
        (including columns inherited from `AuditEventMixin`).
        Relationships and any other non-column attribute are rejected.
        An unknown kwarg raises `ValueError` before any database
        operation is attempted — no partial mutation is possible.

        Subclasses that only record human-initiated actions MUST
        override this method to validate that `user_id` is provided
        (raising `ValueError` if it is `None`) before delegating to
        `super().log_event(...)`.
        """
        valid_keys = {attr.key for attr in class_mapper(cls.model_class).column_attrs}
        invalid_keys = set(kwargs) - valid_keys
        if invalid_keys:
            raise ValueError(
                f"{cls.model_class.__name__} has no column(s) matching: "
                f"{sorted(invalid_keys)}"
            )
        instance = cls.model_class(**kwargs)
        session.add(instance)
        await session.flush()

    @classmethod
    def apply_date_filters(
        cls,
        query: Select[Any],
        from_date: date | datetime | None = None,
        to_date: date | datetime | None = None,
    ) -> Select[Any]:
        """Apply inclusive `created_at` date range filters to a query.

        - `from_date` only: `WHERE created_at >= from_date`
        - `to_date` only: `WHERE created_at <= to_date`
        - Both: the inclusive range
        - Neither: no filter applied

        Date-only values are interpreted as the full UTC day (start of
        day for `from_date`, end of day for `to_date`). Naive datetimes
        are interpreted as UTC. Offset-aware datetimes are converted to
        UTC before comparison. See `docs/api-spec.md` (Date Range
        Interpretation).
        """
        if from_date is not None:
            query = query.where(
                cls.model_class.created_at
                >= _normalize_bound(from_date, end_of_day=False)
            )
        if to_date is not None:
            query = query.where(
                cls.model_class.created_at <= _normalize_bound(to_date, end_of_day=True)
            )
        return query

    @classmethod
    def filter_by_actor(
        cls,
        query: Select[Any],
        actor: str | None = None,
    ) -> Select[Any]:
        """Filter audit events by actor (the `user_id` column).

        - `actor` is `None`: no filter applied
        - `actor == "system"`: `WHERE user_id IS NULL`
        - `actor` is a valid UUID string: `WHERE user_id = <uuid>`
        - Otherwise: joined lookup by exact, case-sensitive
          `User.username`

        If the provided UUID or username does not match any user, the
        query yields an empty result set — this method never raises for
        an unknown actor. Operates exclusively on the `user_id` column;
        domain-specific filters on other user FK columns are the
        responsibility of the endpoint implementation.
        """
        if actor is None:
            return query
        if actor == "system":
            return query.where(cls.model_class.user_id.is_(None))
        try:
            actor_uuid = uuid.UUID(actor)
        except ValueError:
            pass
        else:
            return query.where(cls.model_class.user_id == actor_uuid)
        return query.join(User, User.id == cls.model_class.user_id).where(
            User.username == actor
        )


def _normalize_bound(value: date | datetime, *, end_of_day: bool) -> datetime:
    """Normalize a date-filter bound to a UTC `datetime`.

    A `date` value is expanded to the start (`00:00:00`) or end
    (`23:59:59.999999`) of that UTC day, per `end_of_day`. A naive
    `datetime` is interpreted as UTC. An offset-aware `datetime` is
    converted to UTC.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    bound_time = time.max if end_of_day else time.min
    return datetime.combine(value, bound_time, tzinfo=UTC)
