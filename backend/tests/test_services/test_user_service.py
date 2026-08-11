"""Tests for user identifier resolution, lifecycle, and role queries
(backend/app/services/user_service.py).

See docs/features/identity/user-service.md (`create_user()`,
`update_user()`, `reactivate_user()`, `resolve_user_identifier()`) and
docs/features/identity/rbac.md (`require_capability()` Dependency) for the
contract under test, and docs/features/platform/testing-strategy.md (User
Lifecycle and Management) for the mandatory scenarios exercised here.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from email_validator import validate_email
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import IdentityAuditEventType, Role
from app.core.exceptions import UserNotFoundError
from app.core.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordValidationError,
    verify_password,
)
from app.core.permissions import role_to_wire
from app.models.identity_audit_event import IdentityAuditEvent
from app.models.user import User
from app.models.user_role import UserRole
from app.services import user_service
from app.services.identity_audit_log import IdentityAuditLog
from app.services.user_service import (
    EmailFormatError,
    ExternalUserFieldReadOnlyError,
    ExternalUserPasswordError,
    ExternalUserStatusReadOnlyError,
    UserConflictError,
    UsernameFormatError,
    create_user,
    get_user_by_id,
    get_user_roles,
    reactivate_user,
    resolve_user_identifier,
    update_user,
)
from tests.support.database import rollback_test_scope

# Fictional bcrypt-shaped value — never a real hash (see AGENTS.md Guardrail 23)
_FICTIONAL_PASSWORD_HASH = "$2b$12$" + "a" * 53
_VALID_PASSWORD = "a-valid-password-16"


def _service_log_text(caplog: pytest.LogCaptureFixture) -> str:
    """Join only the log records emitted by `app.services.user_service`."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "app.services.user_service"
    )


async def _audit_events_for(
    db_session: AsyncSession, target_user_id: uuid.UUID
) -> list[IdentityAuditEvent]:
    rows = await db_session.execute(
        select(IdentityAuditEvent)
        .where(IdentityAuditEvent.target_user_id == target_user_id)
        .order_by(IdentityAuditEvent.created_at, IdentityAuditEvent.id)
    )
    return list(rows.scalars().all())


async def _cleanup_committed_users(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> None:
    """Explicit cleanup for `User` rows committed through
    `db_session_factory` in a concurrency test — not covered by the
    fixture's rollback-on-teardown (see
    docs/features/platform/testing-strategy.md, Database Strategy —
    Concurrency Testing). Deletes in FK-safe order: audit events (both
    actor and target references), then roles, then the users themselves.
    """
    await session.execute(
        delete(IdentityAuditEvent).where(
            or_(
                IdentityAuditEvent.target_user_id.in_(user_ids),
                IdentityAuditEvent.user_id.in_(user_ids),
            )
        )
    )
    await session.execute(delete(UserRole).where(UserRole.user_id.in_(user_ids)))
    await session.execute(delete(User).where(User.id.in_(user_ids)))
    await session.commit()


async def _cancel_pending_task(task: asyncio.Task[Any] | None) -> None:
    """Cancel and consume a task that did not finish before test cleanup."""
    if task is None:
        return
    if task.done():
        if not task.cancelled():
            task.exception()
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _concurrency_teardown(
    task: asyncio.Task[Any] | None,
    session_a: AsyncSession,
    session_b: AsyncSession,
    cleanup_session: AsyncSession,
    user_ids: list[uuid.UUID],
) -> None:
    """Robust teardown for concurrency tests using `db_session_factory`.

    Guarantees that each step is attempted even if a previous one fails:
    cancel the pending task, roll back both sessions (releasing locks),
    then DELETE committed test data. Errors are not suppressed — the first
    failure propagates after every step has been attempted.
    """
    first_error: Exception | None = None
    for coro in (
        _cancel_pending_task(task),
        session_a.rollback(),
        session_b.rollback(),
    ):
        try:
            await coro
        except Exception as exc:
            if first_error is None:
                first_error = exc

    if user_ids:
        try:
            await _cleanup_committed_users(cleanup_session, user_ids)
        except Exception as exc:
            if first_error is None:
                first_error = exc

    if first_error is not None:
        raise first_error


class _FakeConstraintError(Exception):
    """A minimal double for asyncpg's `UniqueViolationError`, exposing
    only the `constraint_name` attribute `_conflict_field()` reads."""

    def __init__(self, constraint_name: str | None) -> None:
        super().__init__("simulated database error")
        self.constraint_name = constraint_name


# ---------------------------------------------------------------------------
# _normalize_username()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeUsername:
    def test_trims_and_lowercases(self) -> None:
        assert user_service._normalize_username("  JDoe  ") == "jdoe"

    def test_minimum_length_one_accepted(self) -> None:
        assert user_service._normalize_username("a") == "a"

    def test_maximum_length_64_accepted(self) -> None:
        name = "a" * 64
        assert user_service._normalize_username(name) == name

    def test_over_64_characters_rejected(self) -> None:
        with pytest.raises(UsernameFormatError):
            user_service._normalize_username("a" * 65)

    def test_empty_after_trim_rejected(self) -> None:
        with pytest.raises(UsernameFormatError):
            user_service._normalize_username("   ")

    def test_must_start_with_a_letter(self) -> None:
        with pytest.raises(UsernameFormatError):
            user_service._normalize_username("1jdoe")

    def test_allows_digits_dot_underscore_hyphen(self) -> None:
        assert user_service._normalize_username("j.doe-01_x") == "j.doe-01_x"

    @pytest.mark.parametrize("invalid_char", ["@", "/", "!", " ", "\t", "#"])
    def test_rejects_characters_outside_allowed_set(self, invalid_char: str) -> None:
        with pytest.raises(UsernameFormatError):
            user_service._normalize_username(f"jdoe{invalid_char}x")

    def test_error_message_never_includes_the_value(self) -> None:
        with pytest.raises(UsernameFormatError) as exc_info:
            user_service._normalize_username("1-invalid-secret-username")
        assert "1-invalid-secret-username" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# _normalize_and_validate_email()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeAndValidateEmail:
    def test_trims_and_lowercases_entire_address(self) -> None:
        """The local part is also lowercased — unlike `email-validator`'s
        own `.normalized`, which lowercases only the domain (see
        docs/features/identity/user-service.md, `create_user()` step 2)."""
        assert (
            user_service._normalize_and_validate_email("  John.DOE@Example.COM  ")
            == "john.doe@example.com"
        )

    def test_accepts_plus_tag_addressing(self) -> None:
        assert (
            user_service._normalize_and_validate_email("user+tag@example.com")
            == "user+tag@example.com"
        )

    def test_missing_at_sign_rejected(self) -> None:
        with pytest.raises(EmailFormatError):
            user_service._normalize_and_validate_email("not-an-email")

    def test_missing_domain_rejected(self) -> None:
        with pytest.raises(EmailFormatError):
            user_service._normalize_and_validate_email("user@")

    def test_missing_tld_rejected(self) -> None:
        with pytest.raises(EmailFormatError):
            user_service._normalize_and_validate_email("user@localhost")

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(EmailFormatError):
            user_service._normalize_and_validate_email("")

    def test_error_message_never_includes_the_value(self) -> None:
        with pytest.raises(EmailFormatError) as exc_info:
            user_service._normalize_and_validate_email("secret-invalid-value")
        assert "secret-invalid-value" not in str(exc_info.value)

    def test_performs_no_deliverability_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`check_deliverability=False` must be passed explicitly — a DNS
        lookup would be network I/O inside a pure validation helper."""
        calls: list[dict[str, Any]] = []

        def _spy(value: str, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return validate_email(value, **kwargs)

        monkeypatch.setattr(user_service, "validate_email", _spy)
        user_service._normalize_and_validate_email("user@example.com")
        assert calls == [{"check_deliverability": False}]


# ---------------------------------------------------------------------------
# _conflict_field()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConflictField:
    @pytest.mark.parametrize(
        ("constraint_name", "expected_field"),
        [
            ("user_username_key", "username"),
            ("user_email_key", "email"),
            ("user_external_id_key", "external_id"),
        ],
    )
    def test_matching_constraint_name_is_a_conflict(
        self, constraint_name: str, expected_field: str
    ) -> None:
        exc = IntegrityError("stmt", {}, _FakeConstraintError(constraint_name))
        assert user_service._conflict_field(exc) == expected_field

    def test_matching_constraint_name_on_cause_is_a_conflict(self) -> None:
        wrapped = Exception("wrapped")
        wrapped.__cause__ = _FakeConstraintError("user_email_key")
        exc = IntegrityError("stmt", {}, wrapped)
        assert user_service._conflict_field(exc) == "email"

    def test_different_constraint_name_is_not_a_conflict(self) -> None:
        exc = IntegrityError("stmt", {}, _FakeConstraintError("user_manager_id_fkey"))
        assert user_service._conflict_field(exc) is None

    def test_orig_without_constraint_name_attribute_is_not_a_conflict(self) -> None:
        exc = IntegrityError("stmt", {}, Exception("plain"))
        assert user_service._conflict_field(exc) is None


# ---------------------------------------------------------------------------
# create_user()
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateUser:
    async def test_creates_local_user_with_normalized_fields(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="  JDoe  ",
            email="  John.DOE@Example.COM  ",
            full_name="John Doe",
            password=_VALID_PASSWORD,
            acting_user_id=None,
        )

        assert user.username == "jdoe"
        assert user.email == "john.doe@example.com"
        assert user.full_name == "John Doe"
        assert user.active is True
        assert user.external_id is None
        assert user.synced_at is None
        assert user.password_hash is not None
        assert verify_password(_VALID_PASSWORD, user.password_hash) is True

    async def test_active_defaults_to_true_and_can_be_overridden(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="inactive-new",
            email="inactive-new@example.com",
            password=_VALID_PASSWORD,
            active=False,
            acting_user_id=None,
        )
        assert user.active is False

    async def test_creates_external_user_without_password(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        manager = await user_factory()
        external_id = uuid.uuid4()

        user = await create_user(
            db_session,
            username="ext-user",
            email="ext-user@example.com",
            external_id=external_id,
            manager_id=manager.id,
            acting_user_id=None,
        )

        assert user.external_id == external_id
        assert user.password_hash is None
        assert user.manager_id == manager.id
        assert user.synced_at is not None

    async def test_invalid_username_format_raises(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(UsernameFormatError):
            await create_user(
                db_session,
                username="1-invalid",
                email="valid@example.com",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )

    async def test_invalid_email_format_raises(self, db_session: AsyncSession) -> None:
        with pytest.raises(EmailFormatError):
            await create_user(
                db_session,
                username="valid-user",
                email="not-an-email",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )

    async def test_external_id_and_password_both_provided_raises(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(ExternalUserPasswordError):
            await create_user(
                db_session,
                username="ext-with-pw",
                email="ext-with-pw@example.com",
                external_id=uuid.uuid4(),
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )

    async def test_local_user_without_password_raises(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(PasswordValidationError):
            await create_user(
                db_session,
                username="local-no-pw",
                email="local-no-pw@example.com",
                acting_user_id=None,
            )

    async def test_manager_id_on_local_user_raises(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(ExternalUserFieldReadOnlyError):
            await create_user(
                db_session,
                username="local-with-manager",
                email="local-with-manager@example.com",
                password=_VALID_PASSWORD,
                manager_id=uuid.uuid4(),
                acting_user_id=None,
            )

    async def test_duplicate_username_raises_conflict_with_field(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory(username="taken-user")

        with pytest.raises(UserConflictError) as exc_info:
            await create_user(
                db_session,
                username="taken-user",
                email="different@example.com",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )
        assert exc_info.value.conflict_field == "username"

    async def test_duplicate_email_raises_conflict_with_field(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory(email="taken@example.com")

        with pytest.raises(UserConflictError) as exc_info:
            await create_user(
                db_session,
                username="different-user",
                email="taken@example.com",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )
        assert exc_info.value.conflict_field == "email"

    async def test_duplicate_external_id_raises_conflict_with_field(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        shared_external_id = uuid.uuid4()
        await user_factory(external_id=shared_external_id)

        with pytest.raises(UserConflictError) as exc_info:
            await create_user(
                db_session,
                username="different-ext-user",
                email="different-ext@example.com",
                external_id=shared_external_id,
                acting_user_id=None,
            )
        assert exc_info.value.conflict_field == "external_id"

    async def test_conflict_error_message_never_includes_the_value(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory(email="secret-taken@example.com")

        with pytest.raises(UserConflictError) as exc_info:
            await create_user(
                db_session,
                username="another-user",
                email="secret-taken@example.com",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )
        assert "secret-taken@example.com" not in str(exc_info.value)

    async def test_uniqueness_guard_precedes_password_length_guard(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """A duplicate username with an also-invalid password raises the
        conflict, not the password error — matching the documented guard
        order (uniqueness before password length validation)."""
        await user_factory(username="ordering-conflict")

        with pytest.raises(UserConflictError):
            await create_user(
                db_session,
                username="ordering-conflict",
                email="ordering@example.com",
                password="short",
                acting_user_id=None,
            )

    @pytest.mark.parametrize(
        "password",
        ["a" * (MIN_PASSWORD_LENGTH - 1), "a" * (MAX_PASSWORD_LENGTH + 1)],
    )
    async def test_password_outside_length_policy_raises(
        self, db_session: AsyncSession, password: str
    ) -> None:
        with pytest.raises(PasswordValidationError):
            await create_user(
                db_session,
                username="bad-password-user",
                email="bad-password@example.com",
                password=password,
                acting_user_id=None,
            )

    async def test_deduplicates_identical_role_pairs(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="role-dedupe",
            email="role-dedupe@example.com",
            password=_VALID_PASSWORD,
            roles=[
                (Role.ADMIN, "_manual"),
                (Role.ADMIN, "_manual"),
                (Role.VULNERABILITY_ANALYST, "ext-group"),
            ],
            acting_user_id=None,
        )

        rows = await db_session.execute(
            select(UserRole).where(UserRole.user_id == user.id)
        )
        pairs = {(role_row.role, role_row.group_name) for role_row in rows.scalars()}
        assert pairs == {
            (Role.ADMIN.value, "_manual"),
            (Role.VULNERABILITY_ANALYST.value, "ext-group"),
        }

    async def test_role_assigned_by_is_acting_user(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        admin = await user_factory()

        user = await create_user(
            db_session,
            username="role-assigned-by",
            email="role-assigned-by@example.com",
            password=_VALID_PASSWORD,
            roles=[(Role.ADMIN, "_manual")],
            acting_user_id=admin.id,
        )

        role_row = (
            await db_session.execute(
                select(UserRole).where(UserRole.user_id == user.id)
            )
        ).scalar_one()
        assert role_row.assigned_by == admin.id

    async def test_creates_exactly_one_user_created_and_one_role_added_per_role(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="audit-count",
            email="audit-count@example.com",
            password=_VALID_PASSWORD,
            roles=[(Role.ADMIN, "_manual"), (Role.VULNERABILITY_ANALYST, "ext-group")],
            acting_user_id=None,
        )

        events = await _audit_events_for(db_session, user.id)
        event_types = [event.event_type for event in events]
        assert event_types.count(IdentityAuditEventType.ROLE_ADDED.value) == 2
        assert event_types.count(IdentityAuditEventType.USER_CREATED.value) == 1
        assert len(events) == 3

    async def test_role_added_detail_is_none_for_manual_role(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="manual-role-detail",
            email="manual-role-detail@example.com",
            password=_VALID_PASSWORD,
            roles=[(Role.ADMIN, "_manual")],
            acting_user_id=None,
        )

        events = await _audit_events_for(db_session, user.id)
        role_event = next(
            e for e in events if e.event_type == IdentityAuditEventType.ROLE_ADDED.value
        )
        assert role_event.detail is None
        assert role_event.new_value == role_to_wire(Role.ADMIN)

    async def test_role_added_detail_includes_source_and_mapping_for_external_role(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="ext-role-detail",
            email="ext-role-detail@example.com",
            password=_VALID_PASSWORD,
            roles=[(Role.VULNERABILITY_ANALYST, "SecurityTeam")],
            acting_user_id=None,
        )

        events = await _audit_events_for(db_session, user.id)
        role_event = next(
            e for e in events if e.event_type == IdentityAuditEventType.ROLE_ADDED.value
        )
        assert role_event.detail == {
            "source": "external_sync",
            "mapping": "SecurityTeam",
        }

    async def test_user_created_detail_is_none_for_local_creation(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="local-created-detail",
            email="local-created-detail@example.com",
            password=_VALID_PASSWORD,
            acting_user_id=None,
        )
        event = (await _audit_events_for(db_session, user.id))[0]
        assert event.detail is None
        assert event.new_value == "local-created-detail"

    async def test_user_created_detail_includes_source_for_external_creation(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="ext-created-detail",
            email="ext-created-detail@example.com",
            external_id=uuid.uuid4(),
            acting_user_id=None,
        )
        event = (await _audit_events_for(db_session, user.id))[0]
        assert event.detail == {"source": "external_sync"}

    async def test_user_created_actor_is_the_authenticated_acting_user(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        """A real, authenticated admin creates a local user: the resulting
        `user_created` event's actor (`user_id`) must be the admin, not
        `None` -- proving the actor is threaded through correctly for a
        human-initiated creation, not just the system-caller (`None`) path
        exercised by every other creation test in this class."""
        admin = await user_factory()

        user = await create_user(
            db_session,
            username="actor-created",
            email="actor-created@example.com",
            password=_VALID_PASSWORD,
            acting_user_id=admin.id,
        )

        event = (await _audit_events_for(db_session, user.id))[0]
        assert event.event_type == IdentityAuditEventType.USER_CREATED.value
        assert event.user_id == admin.id
        assert event.target_user_id == user.id

    async def test_returns_user_with_roles_and_manager_eagerly_loaded(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        manager = await user_factory()
        user = await create_user(
            db_session,
            username="eager-load",
            email="eager-load@example.com",
            external_id=uuid.uuid4(),
            manager_id=manager.id,
            roles=[(Role.ADMIN, "_manual")],
            acting_user_id=None,
        )

        # Direct attribute access — an unloaded relationship would raise
        # MissingGreenlet in this async context if accessed synchronously.
        assert len(user.roles) == 1
        assert user.manager is not None
        assert user.manager.id == manager.id

    async def test_flushes_without_commit(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commit_spy = AsyncMock(side_effect=AssertionError("must not commit"))
        monkeypatch.setattr(db_session, "commit", commit_spy)
        await create_user(
            db_session,
            username="no-commit-user",
            email="no-commit-user@example.com",
            password=_VALID_PASSWORD,
            acting_user_id=None,
        )
        commit_spy.assert_not_called()

    async def test_rollback_removes_user_role_and_audit_events_together(
        self, db_session: AsyncSession
    ) -> None:
        user = await create_user(
            db_session,
            username="rollback-user",
            email="rollback-user@example.com",
            password=_VALID_PASSWORD,
            roles=[(Role.ADMIN, "_manual")],
            acting_user_id=None,
        )
        user_id = user.id

        await db_session.rollback()

        assert await db_session.get(User, user_id) is None
        rows = await db_session.execute(
            select(UserRole).where(UserRole.user_id == user_id)
        )
        assert rows.scalars().all() == []
        assert await _audit_events_for(db_session, user_id) == []

    async def test_audit_failure_rolls_back_the_pending_user_too(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(*args: object, **kwargs: object) -> None:
            raise ValueError("simulated audit failure")

        monkeypatch.setattr(IdentityAuditLog, "log_event", _boom)

        with pytest.raises(ValueError, match="simulated audit failure"):
            async with rollback_test_scope(db_session):
                await create_user(
                    db_session,
                    username="audit-fail-user",
                    email="audit-fail-user@example.com",
                    password=_VALID_PASSWORD,
                    acting_user_id=None,
                )

        rows = await db_session.execute(
            select(User).where(User.username == "audit-fail-user")
        )
        assert rows.scalars().all() == []

    async def test_unrelated_integrity_error_propagates_unchanged(
        self, db_session: AsyncSession
    ) -> None:
        """A foreign-key violation on `manager_id` (unrelated to the three
        UNIQUE constraints) must propagate unchanged rather than being
        mistranslated to `UserConflictError`."""
        with pytest.raises(IntegrityError):
            await create_user(
                db_session,
                username="fk-violation-user",
                email="fk-violation-user@example.com",
                external_id=uuid.uuid4(),
                manager_id=uuid.uuid4(),
                acting_user_id=None,
            )

    async def test_precheck_miss_still_translates_email_conflict_via_savepoint(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Simulates the race the sequential pre-check is documented not to
        guarantee against: a conflicting email already exists, but
        `_email_taken()` (monkeypatched) misses it — so the insert
        genuinely violates the UNIQUE constraint, and the SAVEPOINT
        correctly translates that real `IntegrityError` to
        `UserConflictError`. Also verifies unrelated pending work survives
        the SAVEPOINT rollback."""
        await user_factory(email="precheck-miss@example.com")

        bystander = User(
            username="precheck-bystander",
            email="precheck-bystander@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(bystander)
        await db_session.flush()
        bystander_id = bystander.id

        async def _no_conflict(*args: object, **kwargs: object) -> bool:
            return False

        monkeypatch.setattr(user_service, "_email_taken", _no_conflict)

        with pytest.raises(UserConflictError) as exc_info:
            await create_user(
                db_session,
                username="precheck-miss-user",
                email="precheck-miss@example.com",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )
        assert exc_info.value.conflict_field == "email"

        result = await db_session.execute(select(User).where(User.id == bystander_id))
        recovered = result.scalar_one()
        assert recovered.username == "precheck-bystander"

    async def test_precheck_miss_still_translates_external_id_conflict(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        shared_external_id = uuid.uuid4()
        await user_factory(external_id=shared_external_id)

        async def _no_conflict(*args: object, **kwargs: object) -> bool:
            return False

        monkeypatch.setattr(user_service, "_external_id_taken", _no_conflict)

        with pytest.raises(UserConflictError) as exc_info:
            await create_user(
                db_session,
                username="precheck-miss-ext-user",
                email="precheck-miss-ext@example.com",
                external_id=shared_external_id,
                acting_user_id=None,
            )
        assert exc_info.value.conflict_field == "external_id"

    async def test_two_concurrent_creates_same_username_serialize_via_unique_index(
        self,
        db_session_factory: Callable[[], Awaitable[AsyncSession]],
    ) -> None:
        session_a = await db_session_factory()
        session_b = await db_session_factory()
        user_ids: list[uuid.UUID] = []
        task_b: asyncio.Task[Any] | None = None

        try:
            user_a = await create_user(
                session_a,
                username="conc-same-username",
                email="conc-same-username-a@example.com",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )

            task_b = asyncio.create_task(
                create_user(
                    session_b,
                    username="conc-same-username",
                    email="conc-same-username-b@example.com",
                    password=_VALID_PASSWORD,
                    acting_user_id=None,
                )
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task_b), timeout=0.3)

            await session_a.commit()
            user_ids.append(user_a.id)

            with pytest.raises(UserConflictError) as exc_info:
                await asyncio.wait_for(task_b, timeout=5)
            assert exc_info.value.conflict_field == "username"
            await session_b.rollback()

            rows = await session_a.execute(
                select(User).where(User.username == "conc-same-username")
            )
            assert [row.id for row in rows.scalars().all()] == [user_a.id]
        finally:
            await _concurrency_teardown(
                task_b, session_a, session_b, session_a, user_ids
            )

    async def test_two_concurrent_creates_same_email_serialize_via_unique_index(
        self,
        db_session_factory: Callable[[], Awaitable[AsyncSession]],
    ) -> None:
        session_a = await db_session_factory()
        session_b = await db_session_factory()
        user_ids: list[uuid.UUID] = []
        task_b: asyncio.Task[Any] | None = None

        try:
            user_a = await create_user(
                session_a,
                username="conc-same-email-a",
                email="conc-same-email@example.com",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )

            task_b = asyncio.create_task(
                create_user(
                    session_b,
                    username="conc-same-email-b",
                    email="conc-same-email@example.com",
                    password=_VALID_PASSWORD,
                    acting_user_id=None,
                )
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task_b), timeout=0.3)

            await session_a.commit()
            user_ids.append(user_a.id)

            with pytest.raises(UserConflictError) as exc_info:
                await asyncio.wait_for(task_b, timeout=5)
            assert exc_info.value.conflict_field == "email"
            await session_b.rollback()

            rows = await session_a.execute(
                select(User).where(User.email == "conc-same-email@example.com")
            )
            assert [row.id for row in rows.scalars().all()] == [user_a.id]
        finally:
            await _concurrency_teardown(
                task_b, session_a, session_b, session_a, user_ids
            )


# ---------------------------------------------------------------------------
# update_user()
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateUser:
    async def test_missing_user_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(UserNotFoundError):
            await update_user(
                db_session, uuid.uuid4(), acting_user_id=None, full_name="New Name"
            )

    async def test_human_caller_on_external_user_raises(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(external_id=uuid.uuid4())
        admin = await user_factory()

        with pytest.raises(ExternalUserFieldReadOnlyError):
            await update_user(
                db_session,
                target.id,
                acting_user_id=admin.id,
                full_name="New Name",
            )

    async def test_manager_id_on_local_user_raises(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory()
        with pytest.raises(ExternalUserFieldReadOnlyError):
            await update_user(
                db_session, target.id, acting_user_id=None, manager_id=uuid.uuid4()
            )

    async def test_synced_at_on_local_user_raises(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory()
        with pytest.raises(ExternalUserFieldReadOnlyError):
            await update_user(
                db_session,
                target.id,
                acting_user_id=None,
                synced_at=datetime.now(UTC),
            )

    async def test_updates_username_and_creates_audit_event(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(username="old-name")

        updated = await update_user(
            db_session, target.id, acting_user_id=None, username="new-name"
        )

        assert updated.username == "new-name"
        events = await _audit_events_for(db_session, target.id)
        assert len(events) == 1
        assert events[0].event_type == IdentityAuditEventType.USERNAME_CHANGED.value
        assert events[0].old_value == "old-name"
        assert events[0].new_value == "new-name"

    async def test_username_changed_actor_is_the_authenticated_acting_user(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        """A real, authenticated admin renames a local user: the resulting
        `username_changed` event's actor (`user_id`) must be the admin, not
        `None` -- proving the actor is threaded through correctly for a
        human-initiated update, not just the system-caller (`None`) path
        exercised by every other update test in this class."""
        admin = await user_factory()
        target = await user_factory(username="pre-actor-rename")

        await update_user(
            db_session, target.id, acting_user_id=admin.id, username="post-actor-rename"
        )

        events = await _audit_events_for(db_session, target.id)
        assert len(events) == 1
        assert events[0].user_id == admin.id
        assert events[0].target_user_id == target.id

    async def test_username_is_normalized_before_comparison(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(username="already-set")

        updated = await update_user(
            db_session, target.id, acting_user_id=None, username="  Already-Set  "
        )

        assert updated.username == "already-set"
        assert await _audit_events_for(db_session, target.id) == []

    async def test_invalid_username_format_raises(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory()
        with pytest.raises(UsernameFormatError):
            await update_user(
                db_session, target.id, acting_user_id=None, username="1-invalid"
            )

    async def test_duplicate_username_raises_conflict(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        await user_factory(username="already-taken")
        target = await user_factory(username="other-user")

        with pytest.raises(UserConflictError) as exc_info:
            await update_user(
                db_session, target.id, acting_user_id=None, username="already-taken"
            )
        assert exc_info.value.conflict_field == "username"

    async def test_updates_email_fully_lowercased(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory()

        updated = await update_user(
            db_session,
            target.id,
            acting_user_id=None,
            email="  New.EMAIL@Example.COM  ",
        )

        assert updated.email == "new.email@example.com"

    async def test_email_no_op_when_normalized_value_already_stored(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(email="already@example.com")

        await update_user(
            db_session, target.id, acting_user_id=None, email="Already@Example.com"
        )

        assert await _audit_events_for(db_session, target.id) == []

    async def test_invalid_email_format_raises(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory()
        with pytest.raises(EmailFormatError):
            await update_user(
                db_session, target.id, acting_user_id=None, email="not-an-email"
            )

    async def test_duplicate_email_raises_conflict_excluding_self(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        other = await user_factory(email="other@example.com")
        target = await user_factory(email="mine@example.com")

        # Updating to its own current email must NOT conflict (self-exclusion).
        await update_user(
            db_session, target.id, acting_user_id=None, email="mine@example.com"
        )

        with pytest.raises(UserConflictError) as exc_info:
            await update_user(
                db_session, target.id, acting_user_id=None, email=other.email
            )
        assert exc_info.value.conflict_field == "email"

    async def test_full_name_explicit_none_clears_it(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(full_name="Original Name")

        updated = await update_user(
            db_session, target.id, acting_user_id=None, full_name=None
        )

        assert updated.full_name is None
        events = await _audit_events_for(db_session, target.id)
        assert len(events) == 1
        assert events[0].event_type == IdentityAuditEventType.FULL_NAME_CHANGED.value
        assert events[0].old_value == "Original Name"
        assert events[0].new_value is None

    async def test_full_name_omitted_leaves_it_unchanged(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(full_name="Keep Me")

        updated = await update_user(
            db_session, target.id, acting_user_id=None, email="x@example.com"
        )

        assert updated.full_name == "Keep Me"

    async def test_full_name_same_value_is_a_no_op(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(full_name="Same Name")

        await update_user(
            db_session, target.id, acting_user_id=None, full_name="Same Name"
        )

        assert await _audit_events_for(db_session, target.id) == []

    async def test_manager_id_change_audits_manager_usernames(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        manager_one = await user_factory(username="manager-one")
        manager_two = await user_factory(username="manager-two")
        target = await user_factory(external_id=uuid.uuid4(), manager_id=manager_one.id)

        await update_user(
            db_session, target.id, acting_user_id=None, manager_id=manager_two.id
        )

        events = await _audit_events_for(db_session, target.id)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == IdentityAuditEventType.MANAGER_CHANGED.value
        assert event.user_id is None
        assert event.old_value == "manager-one"
        assert event.new_value == "manager-two"
        assert event.detail is None

    async def test_manager_id_explicit_none_clears_it_and_audits(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        manager = await user_factory(username="the-manager")
        target = await user_factory(external_id=uuid.uuid4(), manager_id=manager.id)

        updated = await update_user(
            db_session, target.id, acting_user_id=None, manager_id=None
        )

        assert updated.manager_id is None
        events = await _audit_events_for(db_session, target.id)
        assert events[0].old_value == "the-manager"
        assert events[0].new_value is None

    async def test_synced_at_applies_without_audit_event(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(external_id=uuid.uuid4())
        new_synced_at = datetime.now(UTC)

        updated = await update_user(
            db_session, target.id, acting_user_id=None, synced_at=new_synced_at
        )

        assert updated.synced_at == new_synced_at
        assert await _audit_events_for(db_session, target.id) == []

    async def test_username_changed_detail_includes_source_for_external_user(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(external_id=uuid.uuid4(), username="ext-old-name")

        await update_user(
            db_session, target.id, acting_user_id=None, username="ext-new-name"
        )

        events = await _audit_events_for(db_session, target.id)
        assert events[0].detail == {"source": "external_sync"}

    async def test_all_omitted_fields_is_a_total_no_op(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(full_name="Untouched")

        updated = await update_user(db_session, target.id, acting_user_id=None)

        assert updated.full_name == "Untouched"
        assert await _audit_events_for(db_session, target.id) == []

    async def test_multi_field_update_creates_one_event_per_field(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(username="multi-old", full_name="Old Name")

        await update_user(
            db_session,
            target.id,
            acting_user_id=None,
            username="multi-new",
            email="multi-new@example.com",
            full_name="New Name",
        )

        events = await _audit_events_for(db_session, target.id)
        event_types = {event.event_type for event in events}
        assert event_types == {
            IdentityAuditEventType.USERNAME_CHANGED.value,
            IdentityAuditEventType.EMAIL_CHANGED.value,
            IdentityAuditEventType.FULL_NAME_CHANGED.value,
        }
        assert len(events) == 3

    async def test_returns_user_with_roles_and_manager_eagerly_loaded(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        user_role_factory: Callable[..., Awaitable[UserRole]],
    ) -> None:
        manager = await user_factory()
        target = await user_factory(external_id=uuid.uuid4(), manager_id=manager.id)
        await user_role_factory(user_id=target.id, role=Role.ADMIN.value)

        updated = await update_user(
            db_session, target.id, acting_user_id=None, synced_at=datetime.now(UTC)
        )

        assert len(updated.roles) == 1
        assert updated.manager is not None
        assert updated.manager.id == manager.id

    async def test_flushes_without_commit(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = await user_factory()
        commit_spy = AsyncMock(side_effect=AssertionError("must not commit"))
        monkeypatch.setattr(db_session, "commit", commit_spy)
        await update_user(db_session, target.id, acting_user_id=None, full_name="X")
        commit_spy.assert_not_called()

    async def test_rollback_removes_mutation_and_audit_event_together(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(full_name="Before")
        target_id = target.id

        async with rollback_test_scope(db_session):
            await update_user(
                db_session, target_id, acting_user_id=None, full_name="After"
            )

        reloaded = await db_session.get(User, target_id)
        assert reloaded is not None
        assert reloaded.full_name == "Before"
        assert await _audit_events_for(db_session, target_id) == []

    async def test_audit_failure_rolls_back_the_pending_update_too(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = await user_factory(full_name="Before")
        target_id = target.id

        async def _boom(*args: object, **kwargs: object) -> None:
            raise ValueError("simulated audit failure")

        monkeypatch.setattr(IdentityAuditLog, "log_event", _boom)

        with pytest.raises(ValueError, match="simulated audit failure"):
            async with rollback_test_scope(db_session):
                await update_user(
                    db_session, target_id, acting_user_id=None, full_name="After"
                )

        reloaded = await db_session.get(User, target_id)
        assert reloaded is not None
        assert reloaded.full_name == "Before"

    async def test_unrelated_integrity_error_propagates_unchanged(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        target = await user_factory(external_id=uuid.uuid4())
        with pytest.raises(IntegrityError):
            await update_user(
                db_session, target.id, acting_user_id=None, manager_id=uuid.uuid4()
            )

    async def test_precheck_miss_still_translates_conflict_keeps_transaction_usable(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression test for a SAVEPOINT-ordering bug: `update_user()`
        used to assign `user.username`/`user.email` directly onto the ORM
        object, one field at a time, each *before* the next field's own
        uniqueness pre-check query. Since a pre-check query
        (`_email_taken()`) triggers SQLAlchemy autoflush, a prior field's
        pending mutation could be flushed *before* `begin_nested()`
        established its SAVEPOINT protection -- leaving the whole parent
        transaction aborted instead of just the nested one. This simulates
        the race the sequential pre-check is documented not to guarantee
        against (`_email_taken()` monkeypatched to miss a real conflict)
        while changing BOTH `username` and `email` in the same call --
        the exact combination that triggered the bug -- and verifies the
        transaction remains usable for unrelated work after the translated
        `UserConflictError`."""
        await user_factory(email="update-precheck-miss@example.com")
        target = await user_factory(username="update-precheck-target")
        target_id = target.id

        bystander = User(
            username="update-precheck-bystander",
            email="update-precheck-bystander@example.com",
            password_hash=_FICTIONAL_PASSWORD_HASH,
        )
        db_session.add(bystander)
        await db_session.flush()
        bystander_id = bystander.id

        async def _no_conflict(*args: object, **kwargs: object) -> bool:
            return False

        monkeypatch.setattr(user_service, "_email_taken", _no_conflict)

        with pytest.raises(UserConflictError) as exc_info:
            await update_user(
                db_session,
                target_id,
                acting_user_id=None,
                username="update-precheck-new-name",
                email="update-precheck-miss@example.com",
            )
        assert exc_info.value.conflict_field == "email"

        # The transaction must still be usable: an unrelated read succeeds,
        # proving the parent transaction was not left in PostgreSQL's
        # aborted state by the failed flush.
        result = await db_session.execute(select(User).where(User.id == bystander_id))
        recovered = result.scalar_one()
        assert recovered.username == "update-precheck-bystander"

        # The whole staged mutation set rolled back together: the
        # username change must not have been persisted either.
        reloaded = await db_session.get(User, target_id)
        assert reloaded is not None
        assert reloaded.username == "update-precheck-target"

    async def test_two_concurrent_updates_same_user_serialize_via_row_lock(
        self,
        db_session_factory: Callable[[], Awaitable[AsyncSession]],
    ) -> None:
        session_a = await db_session_factory()
        session_b = await db_session_factory()
        user_ids: list[uuid.UUID] = []
        task_b: asyncio.Task[Any] | None = None

        try:
            user = User(
                username="conc-update-target",
                email="conc-update-target@example.com",
                password_hash=_FICTIONAL_PASSWORD_HASH,
            )
            session_a.add(user)
            await session_a.commit()
            user_id = user.id
            user_ids.append(user_id)

            updated_a = await update_user(
                session_a,
                user_id,
                acting_user_id=None,
                email="first-update@example.com",
            )

            task_b = asyncio.create_task(
                update_user(
                    session_b,
                    user_id,
                    acting_user_id=None,
                    email="second-update@example.com",
                )
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task_b), timeout=0.3)

            await session_a.commit()

            updated_b = await asyncio.wait_for(task_b, timeout=5)
            await session_b.commit()

            assert updated_a.email == "first-update@example.com"
            assert updated_b.email == "second-update@example.com"

            # session_b must have refreshed post-commit state: its own
            # email_changed old_value reflects session_a's committed
            # email, not the pre-session_a original.
            events = await _audit_events_for(session_a, user_id)
            email_events = [
                e
                for e in events
                if e.event_type == IdentityAuditEventType.EMAIL_CHANGED.value
            ]
            assert len(email_events) == 2
            assert email_events[1].old_value == "first-update@example.com"
            assert email_events[1].new_value == "second-update@example.com"
        finally:
            await _concurrency_teardown(
                task_b, session_a, session_b, session_a, user_ids
            )

    async def test_two_concurrent_updates_different_users_same_email_serialize(
        self,
        db_session_factory: Callable[[], Awaitable[AsyncSession]],
    ) -> None:
        session_a = await db_session_factory()
        session_b = await db_session_factory()
        user_ids: list[uuid.UUID] = []
        task_b: asyncio.Task[Any] | None = None

        try:
            user_a = User(
                username="conc-cross-user-a",
                email="conc-cross-user-a@example.com",
                password_hash=_FICTIONAL_PASSWORD_HASH,
            )
            user_b = User(
                username="conc-cross-user-b",
                email="conc-cross-user-b@example.com",
                password_hash=_FICTIONAL_PASSWORD_HASH,
            )
            session_a.add_all([user_a, user_b])
            await session_a.commit()
            user_ids.extend([user_a.id, user_b.id])

            updated_a = await update_user(
                session_a,
                user_a.id,
                acting_user_id=None,
                email="conc-cross-target@example.com",
            )

            task_b = asyncio.create_task(
                update_user(
                    session_b,
                    user_b.id,
                    acting_user_id=None,
                    email="conc-cross-target@example.com",
                )
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task_b), timeout=0.3)

            await session_a.commit()

            with pytest.raises(UserConflictError) as exc_info:
                await asyncio.wait_for(task_b, timeout=5)
            assert exc_info.value.conflict_field == "email"
            await session_b.rollback()

            assert updated_a.email == "conc-cross-target@example.com"
        finally:
            await _concurrency_teardown(
                task_b, session_a, session_b, session_a, user_ids
            )


# ---------------------------------------------------------------------------
# reactivate_user()
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReactivateUser:
    async def test_missing_user_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(UserNotFoundError):
            await reactivate_user(db_session, uuid.uuid4(), acting_user_id=None)

    async def test_already_active_is_a_no_op(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(active=True)

        updated = await reactivate_user(db_session, target.id, acting_user_id=None)

        assert updated.active is True
        assert await _audit_events_for(db_session, target.id) == []

    async def test_already_active_external_user_with_human_caller_is_a_no_op(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """The already-active short-circuit runs BEFORE the external-status
        guard, so a human caller reactivating an already-active external
        user does not raise."""
        admin = await user_factory()
        target = await user_factory(external_id=uuid.uuid4(), active=True)

        updated = await reactivate_user(db_session, target.id, acting_user_id=admin.id)

        assert updated.active is True
        assert await _audit_events_for(db_session, target.id) == []

    async def test_reactivates_inactive_local_user(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(active=False)

        updated = await reactivate_user(db_session, target.id, acting_user_id=None)

        assert updated.active is True
        events = await _audit_events_for(db_session, target.id)
        assert len(events) == 1
        assert events[0].event_type == IdentityAuditEventType.USER_REACTIVATED.value
        assert events[0].old_value == "inactive"
        assert events[0].new_value == "active"
        assert events[0].detail is None

    async def test_reactivated_actor_is_the_authenticated_acting_user(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        """A real, authenticated admin reactivates a local user: the
        resulting `user_reactivated` event's actor (`user_id`) must be the
        admin, not `None` -- proving the actor is threaded through
        correctly for a human-initiated reactivation, not just the
        system-caller (`None`) path exercised by every other reactivation
        test in this class."""
        admin = await user_factory()
        target = await user_factory(active=False)

        await reactivate_user(db_session, target.id, acting_user_id=admin.id)

        events = await _audit_events_for(db_session, target.id)
        assert len(events) == 1
        assert events[0].user_id == admin.id
        assert events[0].target_user_id == target.id

    async def test_reactivates_inactive_external_user_via_system_caller(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(external_id=uuid.uuid4(), active=False)

        updated = await reactivate_user(db_session, target.id, acting_user_id=None)

        assert updated.active is True
        events = await _audit_events_for(db_session, target.id)
        assert events[0].detail == {"source": "external_sync"}

    async def test_human_caller_reactivating_inactive_external_user_raises(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        admin = await user_factory()
        target = await user_factory(external_id=uuid.uuid4(), active=False)

        with pytest.raises(ExternalUserStatusReadOnlyError):
            await reactivate_user(db_session, target.id, acting_user_id=admin.id)

        reloaded = await db_session.get(User, target.id)
        assert reloaded is not None
        assert reloaded.active is False
        assert await _audit_events_for(db_session, target.id) == []

    async def test_returns_user_with_roles_and_manager_eagerly_loaded(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        user_role_factory: Callable[..., Awaitable[UserRole]],
    ) -> None:
        manager = await user_factory()
        target = await user_factory(
            active=False, external_id=uuid.uuid4(), manager_id=manager.id
        )
        await user_role_factory(user_id=target.id, role=Role.ADMIN.value)

        updated = await reactivate_user(db_session, target.id, acting_user_id=None)

        assert len(updated.roles) == 1
        assert updated.manager is not None
        assert updated.manager.id == manager.id

    async def test_flushes_without_commit(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = await user_factory(active=False)
        commit_spy = AsyncMock(side_effect=AssertionError("must not commit"))
        monkeypatch.setattr(db_session, "commit", commit_spy)
        await reactivate_user(db_session, target.id, acting_user_id=None)
        commit_spy.assert_not_called()

    async def test_rollback_restores_inactive_state_and_removes_audit_event(
        self, db_session: AsyncSession, user_factory: Callable[..., Awaitable[User]]
    ) -> None:
        target = await user_factory(active=False)
        target_id = target.id

        async with rollback_test_scope(db_session):
            await reactivate_user(db_session, target_id, acting_user_id=None)

        reloaded = await db_session.get(User, target_id)
        assert reloaded is not None
        assert reloaded.active is False
        assert await _audit_events_for(db_session, target_id) == []

    async def test_audit_failure_rolls_back_the_pending_reactivation_too(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = await user_factory(active=False)
        target_id = target.id

        async def _boom(*args: object, **kwargs: object) -> None:
            raise ValueError("simulated audit failure")

        monkeypatch.setattr(IdentityAuditLog, "log_event", _boom)

        with pytest.raises(ValueError, match="simulated audit failure"):
            async with rollback_test_scope(db_session):
                await reactivate_user(db_session, target_id, acting_user_id=None)

        reloaded = await db_session.get(User, target_id)
        assert reloaded is not None
        assert reloaded.active is False
        assert await _audit_events_for(db_session, target_id) == []

    async def test_two_concurrent_reactivations_produce_one_mutation_and_event(
        self,
        db_session_factory: Callable[[], Awaitable[AsyncSession]],
    ) -> None:
        session_a = await db_session_factory()
        session_b = await db_session_factory()
        user_ids: list[uuid.UUID] = []
        task_b: asyncio.Task[Any] | None = None

        try:
            user = User(
                username="conc-reactivate-target",
                email="conc-reactivate-target@example.com",
                password_hash=_FICTIONAL_PASSWORD_HASH,
                active=False,
            )
            session_a.add(user)
            await session_a.commit()
            user_id = user.id
            user_ids.append(user_id)

            updated_a = await reactivate_user(session_a, user_id, acting_user_id=None)

            task_b = asyncio.create_task(
                reactivate_user(session_b, user_id, acting_user_id=None)
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task_b), timeout=0.3)

            await session_a.commit()

            updated_b = await asyncio.wait_for(task_b, timeout=5)

            assert updated_a.active is True
            assert updated_b.active is True

            events = await _audit_events_for(session_a, user_id)
            reactivated_events = [
                e
                for e in events
                if e.event_type == IdentityAuditEventType.USER_REACTIVATED.value
            ]
            assert len(reactivated_events) == 1
        finally:
            await _concurrency_teardown(
                task_b, session_a, session_b, session_a, user_ids
            )


# ---------------------------------------------------------------------------
# Pre-existing read functions (regression coverage — unchanged behavior)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetUserById:
    async def test_returns_user_when_exists(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()

        result = await get_user_by_id(db_session, user.id)

        assert result is not None
        assert result.id == user.id

    async def test_returns_none_when_not_exists(self, db_session: AsyncSession) -> None:
        result = await get_user_by_id(db_session, uuid.uuid4())

        assert result is None


@pytest.mark.integration
class TestResolveUserIdentifier:
    async def test_resolves_by_uuid(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()

        resolved = await resolve_user_identifier(db_session, str(user.id))

        assert resolved.id == user.id

    async def test_resolves_by_exact_username(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory(username="jdoe")

        resolved = await resolve_user_identifier(db_session, "jdoe")

        assert resolved.id == user.id

    async def test_username_lookup_is_case_sensitive(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory(username="jdoe")

        with pytest.raises(UserNotFoundError):
            await resolve_user_identifier(db_session, "JDoe")

    async def test_unknown_uuid_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(UserNotFoundError):
            await resolve_user_identifier(db_session, str(uuid.uuid4()))

    async def test_unknown_username_raises_not_found(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(UserNotFoundError):
            await resolve_user_identifier(db_session, "nonexistent-user")

    async def test_valid_uuid_with_no_match_does_not_fall_back_to_username(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        """A syntactically valid but non-existent UUID must not be
        reinterpreted as a username lookup, even if a user happens to
        have that exact string as their username."""
        unknown_id = uuid.uuid4()
        await user_factory(username=str(unknown_id))

        with pytest.raises(UserNotFoundError):
            await resolve_user_identifier(db_session, str(unknown_id))


@pytest.mark.integration
class TestGetUserRoles:
    async def test_no_roles_returns_empty_list(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()

        roles = await get_user_roles(db_session, user.id)

        assert roles == []

    async def test_returns_single_role(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add(UserRole(user_id=user.id, role=Role.ADMIN.value))
        await db_session.flush()

        roles = await get_user_roles(db_session, user.id)

        assert roles == [Role.ADMIN]

    async def test_role_held_from_multiple_origins_counts_once(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add_all(
            [
                UserRole(
                    user_id=user.id,
                    role=Role.VULNERABILITY_ANALYST.value,
                    group_name="_manual",
                ),
                UserRole(
                    user_id=user.id,
                    role=Role.VULNERABILITY_ANALYST.value,
                    group_name="external-group",
                ),
            ]
        )
        await db_session.flush()

        roles = await get_user_roles(db_session, user.id)

        assert roles == [Role.VULNERABILITY_ANALYST]

    async def test_multiple_distinct_roles(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        user = await user_factory()
        db_session.add_all(
            [
                UserRole(user_id=user.id, role=Role.ADMIN.value),
                UserRole(user_id=user.id, role=Role.VULNERABILITY_ANALYST.value),
            ]
        )
        await db_session.flush()

        roles = await get_user_roles(db_session, user.id)

        assert set(roles) == {Role.ADMIN, Role.VULNERABILITY_ANALYST}

    async def test_unknown_user_id_returns_empty_list(
        self, db_session: AsyncSession
    ) -> None:
        """An unresolved `user_id` is not an error here — the caller has
        already resolved the principal via a validated credential."""
        roles = await get_user_roles(db_session, uuid.uuid4())

        assert roles == []


# ---------------------------------------------------------------------------
# PII / secret safety across create_user / update_user / reactivate_user
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPiiAndSecretSafety:
    async def test_create_user_logs_contain_no_pii_or_secret(
        self, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("DEBUG"):
            await create_user(
                db_session,
                username="pii-safe-user",
                email="pii-safe-user@example.com",
                full_name="Pii Safe",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )
        text = _service_log_text(caplog)
        assert "pii-safe-user" not in text
        assert _VALID_PASSWORD not in text

    async def test_conflict_error_str_contains_no_secret_value(
        self,
        db_session: AsyncSession,
        user_factory: Callable[..., Awaitable[User]],
    ) -> None:
        await user_factory(username="conflict-secret-user")

        with pytest.raises(UserConflictError) as exc_info:
            await create_user(
                db_session,
                username="conflict-secret-user",
                email="another@example.com",
                password=_VALID_PASSWORD,
                acting_user_id=None,
            )
        assert _VALID_PASSWORD not in str(exc_info.value)
