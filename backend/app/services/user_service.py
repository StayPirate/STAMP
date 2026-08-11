"""User identifier resolution, lifecycle, and role queries.

See `docs/features/identity/user-service.md` for the authoritative contract
this module implements. `create_user()`, `update_user()`, and
`reactivate_user()` are the ticket-independent core of the centralized user
lifecycle service (P2-08). `reset_password()` and `unlock_user()` (P2-09)
provide password reset and lockout-counter clearing. `update_roles()`,
`deactivate_user()`, and the bulk role-mapping operations remain out of
scope and are added when their owning work item is implemented.
`resolve_user_identifier()` and `get_user_roles()` predate this piece
(P2-06) and back the shared authentication/authorization dependencies.

Module-level defaults (`docs/conventions.md`, Function Specification
Completeness): every mutating function in this module participates in the
caller-owned transaction — it flushes when required and never commits or
rolls back (`docs/conventions.md`, Caller-Owned Service Transactions). Every
mutating function propagates the exceptions listed in its own docstring plus
any underlying database or `IdentityAuditLog.log_event()` exception. Read
functions are infallible except where documented, create no audit event, and
acquire no lock.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import UUID

import structlog
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.enums import IdentityAuditEventType, Role, SessionInvalidationReason
from app.core.exceptions import ServiceError, UserNotFoundError
from app.core.passwords import PasswordValidationError, hash_password, validate_password
from app.core.permissions import role_to_wire
from app.models.user import User
from app.models.user_role import UserRole
from app.services.identity_audit_log import IdentityAuditLog
from app.services.local_auth_service import clear_login_attempts
from app.services.session_service import invalidate_user_sessions

logger = structlog.get_logger(__name__)

# Username Format (docs/conventions.md): 1-64 characters, starts with a
# letter, lowercase letters/numbers/dots/hyphens/underscores only.
_USERNAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")

# The three UNIQUE constraints on `user` that a conflicting create/update can
# violate (docs/data-model.md, User) — verified against the actual database
# (see the PR description for the verification note).
ConflictField = Literal["username", "email", "external_id"]

_CONSTRAINT_TO_FIELD: Final[dict[str, ConflictField]] = {
    "user_username_key": "username",
    "user_email_key": "email",
    "user_external_id_key": "external_id",
}

_CONFLICT_MESSAGES: Final[dict[ConflictField, str]] = {
    "username": "Username already exists.",
    "email": "Email already exists.",
    "external_id": "External ID already exists.",
}


class UserServiceError(ServiceError):
    """Base for all `user_service`-specific exceptions."""


class UserConflictError(UserServiceError):
    """A normalized username, email, or external ID is already in use.

    `conflict_field` identifies which uniqueness constraint was violated —
    never the conflicting value itself, to avoid enabling username or email
    enumeration through the response (see
    `docs/features/identity/user-service.md`, Service Exceptions).
    """

    def __init__(self, conflict_field: ConflictField) -> None:
        self.conflict_field = conflict_field
        super().__init__(_CONFLICT_MESSAGES[conflict_field])


class UsernameFormatError(UserServiceError):
    """A candidate username does not match the Username Format rules.

    The message never includes the rejected value. Unreachable through the
    API in practice — the request schema already rejects a malformed
    username with the global 422 `VALIDATION_ERROR` response (see
    `docs/features/identity/user-service.md`, System-internal exceptions).
    """

    def __init__(self) -> None:
        super().__init__("Username format is invalid.")


class EmailFormatError(UserServiceError):
    """A candidate email fails `email-validator` format validation.

    The message never includes the rejected value. Unreachable through the
    API in practice for the same reason as `UsernameFormatError` — see
    `docs/features/identity/user-service.md`, System-internal exceptions.
    """

    def __init__(self) -> None:
        super().__init__("Email format is invalid.")


class ExternalUserPasswordError(UserServiceError):
    """A password was supplied for an external (`external_id IS NOT NULL`) user."""

    def __init__(self) -> None:
        super().__init__(
            "Cannot set password for external user. "
            "External users authenticate via SSO."
        )


class ExternalUserFieldReadOnlyError(UserServiceError):
    """An identity field managed by external sync was targeted by a human
    caller, or an external-provider-only field was targeted for a local user.

    See `docs/features/identity/user-service.md` (External User Data
    Ownership).
    """

    def __init__(self) -> None:
        super().__init__(
            "Cannot modify identity fields for external users. "
            "These fields are managed by the external identity provider."
        )


class ExternalUserStatusReadOnlyError(UserServiceError):
    """A human caller attempted to reactivate an external user.

    See `docs/features/identity/user-service.md` (External Active Status
    Ownership).
    """

    def __init__(self) -> None:
        super().__init__(
            "Cannot manually activate or deactivate an external user. "
            "Active status is managed by the external identity provider."
        )


class _MissingType:
    """Sentinel type for an omitted optional `update_user()` parameter.

    Distinguishes "field not provided" (this sentinel) from an explicit
    `None` (clear the field) — see `docs/features/identity/user-service.md`
    (`update_user()`) and `docs/conventions.md`'s reference to
    `dataclasses.MISSING`.
    """

    def __repr__(self) -> str:
        return "MISSING"


_MISSING: Final = _MissingType()


@dataclass(frozen=True)
class PasswordResetResult:
    """Data returned by `reset_password()` for the caller's post-commit phase.

    See `docs/features/identity/user-service.md` (Mutation Result Types).
    `username` is the stored normalized username needed for the post-commit
    lockout-counter cleanup (`clear_login_attempts()`); `invalidated_session_ids`
    is passed to `session_service.purge_session_cache()`.
    """

    user: User
    invalidated_session_ids: list[UUID]
    username: str


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    """Return the `User` row for `user_id`, or ``None`` if no row exists.

    A trivial single-row PK lookup used by the authentication boundary
    (`get_current_user`) to load the already-resolved `user_id` from a
    validated JWT or API key, keeping all ORM queries in the Service
    layer per `docs/architecture.md` (Backend Layer Architecture).

    Q6: propagates any underlying database exception.  Never raises
    `UserNotFoundError` — the caller decides how to react to ``None``.
    """
    return await session.get(User, user_id)


async def resolve_user_identifier(session: AsyncSession, identifier: str) -> User:
    """Resolve a UUID-or-username identifier to its `User` row.

    Q1: `identifier` is the raw path/query/body value supplied by a
    caller — see `docs/api-spec.md` (User Identifier Resolution).

    Q3: if `identifier` parses as a UUID, look up `User.id`; otherwise
    look up the exact stored `username` (case-sensitive). Returns the
    matching row without loading response-specific relationships
    (roles, manager) — callers needing those load them explicitly.
    Deterministic for a fixed database snapshot.

    Q6: raises `UserNotFoundError` when no row matches either lookup.
    Propagates any underlying database exception.
    """
    try:
        user_id = UUID(identifier)
    except ValueError:
        result = await session.execute(select(User).where(User.username == identifier))
    else:
        result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise UserNotFoundError()
    return user


async def get_user_roles(session: AsyncSession, user_id: UUID) -> list[Role]:
    """Return the distinct `Role` values currently held by `user_id`.

    Q1: `user_id` identifies the user; the caller has already resolved
    it (e.g. via `resolve_user_identifier()` or a validated JWT/API-key
    principal) — an unknown `user_id` is not an error here, it simply
    yields an empty list.

    Q3: queries every `UserRole` row for `user_id` (regardless of
    `group_name`/origin — a role held from multiple origins still
    counts once) and returns the distinct `Role` values. Used by
    `require_capability()` to union capabilities on every request, so
    role changes take effect on the immediately following request (see
    `docs/features/identity/rbac.md`, Permission Checking).

    Q6: propagates any underlying database exception. Infallible
    otherwise — never raises `UserNotFoundError` (an unknown or
    role-less user simply has zero roles).
    """
    result = await session.execute(
        select(UserRole.role).where(UserRole.user_id == user_id).distinct()
    )
    return [Role(value) for value in result.scalars().all()]


def _normalize_username(username: str) -> str:
    """Trim, lowercase, and validate `username` against the Username Format.

    Raises `UsernameFormatError` for a value that is empty after trimming,
    exceeds 64 characters, does not start with a letter, or contains any
    character outside `[a-z0-9._-]`.
    """
    normalized = username.strip().lower()
    if not _USERNAME_PATTERN.fullmatch(normalized):
        raise UsernameFormatError()
    return normalized


def _normalize_and_validate_email(email: str) -> str:
    """Trim, fully lowercase, and format-validate `email`.

    The entire string — local part and domain alike — is lowercased before
    validation and before being returned (`docs/data-model.md`, `User.email`:
    stored as lowercase). `email-validator` is used for format validation
    only (`check_deliverability=False`, no DNS lookup); its own
    `.normalized` result is intentionally discarded because it lowercases
    only the domain, preserving local-part case per RFC convention — see
    `docs/features/identity/user-service.md`, `create_user()` step 2.

    Raises `EmailFormatError` when the fully-lowercased value is not a
    syntactically valid email address.
    """
    normalized = email.strip().lower()
    try:
        validate_email(normalized, check_deliverability=False)
    except EmailNotValidError as exc:
        raise EmailFormatError() from exc
    return normalized


def _conflict_field(exc: IntegrityError) -> ConflictField | None:
    """Whether `exc` originates from one of the three UNIQUE constraints
    on `user` (username, email, external ID), and which one.

    Inspects the underlying DBAPI exception's `constraint_name` (populated
    by asyncpg for integrity constraint violations) rather than matching on
    message text, so an unrelated integrity error (e.g. a foreign key
    violation on `manager_id`) is never misclassified as a conflict.
    SQLAlchemy's asyncpg dialect wraps the raw
    `asyncpg.exceptions.PostgresError` (which carries `constraint_name`) via
    `raise ... from error`; the raw error is therefore reachable at
    `exc.orig` directly on some versions and at `exc.orig.__cause__` on
    others — both are checked (mirrors `api_key_service._is_name_conflict()`).
    """
    for candidate in (exc.orig, getattr(exc.orig, "__cause__", None)):
        constraint_name = getattr(candidate, "constraint_name", None)
        if isinstance(constraint_name, str) and constraint_name in _CONSTRAINT_TO_FIELD:
            return _CONSTRAINT_TO_FIELD[constraint_name]
    return None


async def _username_taken(
    session: AsyncSession, username: str, exclude_user_id: UUID | None
) -> bool:
    """Whether a user other than `exclude_user_id` already has `username`."""
    filters: list[ColumnElement[bool]] = [User.username == username]
    if exclude_user_id is not None:
        filters.append(User.id != exclude_user_id)
    result = await session.execute(select(User.id).where(*filters))
    return result.scalar_one_or_none() is not None


async def _email_taken(
    session: AsyncSession, email: str, exclude_user_id: UUID | None
) -> bool:
    """Whether a user other than `exclude_user_id` already has `email`."""
    filters: list[ColumnElement[bool]] = [User.email == email]
    if exclude_user_id is not None:
        filters.append(User.id != exclude_user_id)
    result = await session.execute(select(User.id).where(*filters))
    return result.scalar_one_or_none() is not None


async def _external_id_taken(
    session: AsyncSession, external_id: UUID, exclude_user_id: UUID | None
) -> bool:
    """Whether a user other than `exclude_user_id` already has `external_id`."""
    filters: list[ColumnElement[bool]] = [User.external_id == external_id]
    if exclude_user_id is not None:
        filters.append(User.id != exclude_user_id)
    result = await session.execute(select(User.id).where(*filters))
    return result.scalar_one_or_none() is not None


async def _resolve_username(session: AsyncSession, user_id: UUID | None) -> str | None:
    """Return the stored username for `user_id`, or `None` if `user_id`
    is `None`. Used to build `manager_changed`'s human-readable
    old/new values from `manager_id` UUIDs."""
    if user_id is None:
        return None
    result = await session.execute(select(User.username).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _load_user_profile(session: AsyncSession, user_id: UUID) -> User:
    """Load one `User` with `roles` and `manager` eagerly populated, for
    API profile serialization after a lifecycle mutation (see
    `docs/features/identity/user-service.md`, Mutation Result Types)."""
    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .options(selectinload(User.roles), selectinload(User.manager))
    )
    return result.scalar_one()


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    full_name: str | None = None,
    active: bool = True,
    external_id: UUID | None = None,
    manager_id: UUID | None = None,
    password: str | None = None,
    roles: list[tuple[Role, str]] | None = None,
    acting_user_id: UUID | None,
) -> User:
    """Create a new `User` with optional initial roles.

    Q1: `username`/`email` are the raw candidate values (normalized here);
    `external_id` is the external provider's stable UUID (`None` for a local
    user); `password` is the plaintext password, required for a local user
    and forbidden for an external one (hashed here, never persisted or
    logged in plaintext); `roles` is an optional list of `(role,
    group_name)` pairs for initial role assignment; `acting_user_id` is the
    audit actor (`None` for CLI/external-sync system callers).

    Q2 (guards, in this order): invalid `username` format raises
    `UsernameFormatError`; invalid `email` format raises `EmailFormatError`;
    `external_id` and `password` both provided raises
    `ExternalUserPasswordError`; `external_id` is `None` and `password` is
    `None` raises `PasswordValidationError`; `external_id` is `None` and
    `manager_id` is provided raises `ExternalUserFieldReadOnlyError`
    (`manager_id` has no source of truth for local users — see External
    User Data Ownership); a normalized `username`, `email`, or `external_id`
    already in use raises `UserConflictError` (both as a sequential
    pre-check here and, when concurrently lost, via the UNIQUE constraints
    below); an out-of-policy `password` length raises
    `PasswordValidationError`.

    Q3: normalizes `username` (trim, lowercase) and `email` (trim, fully
    lowercase — see `_normalize_and_validate_email()`). Validates the
    guards above in order. Hashes `password` via `hash_password()` off the
    event loop (`asyncio.to_thread()`) — bcrypt is CPU-bound and must never
    block the event loop (see `docs/features/identity/local-authentication.md`).
    Inserts the `User` row inside a `SAVEPOINT` (`session.begin_nested()`)
    so a concurrent same-value winner's `IntegrityError` on one of the three
    UNIQUE constraints can be translated to `UserConflictError` without
    discarding other pending work in the caller's transaction. Sets
    `synced_at = datetime.now(UTC)` when `external_id` is provided,
    otherwise `None`. For each `(role, group_name)` pair in `roles`,
    deduplicated by exact pair (only one `UserRole` per unique pair — same
    idempotency as `update_roles()`), creates the `UserRole` with
    `assigned_by = acting_user_id` and one `role_added` event
    (`detail = None` for `group_name == "_manual"`; otherwise
    `detail = {"source": "external_sync", "mapping": group_name}`). Then
    creates one `user_created` event (`new_value` = the created user's
    username; `detail = {"source": "external_sync"}` when `external_id` is
    provided, otherwise `None` — a creation with `external_id` set is
    always performed by external sync per the External User Data Ownership
    contract). Flushes every mutation and audit event, then returns the
    created `User` with `roles` and `manager` eagerly loaded.

    Q4: creates exactly one `user_created` event plus one `role_added`
    event per deduplicated initial role, all in this transaction.

    Q5: not idempotent. Repeating a successful creation with the same
    normalized username, email, or external ID raises `UserConflictError`
    and creates no additional record.

    Q6: propagates `UsernameFormatError`, `EmailFormatError`,
    `ExternalUserPasswordError`, `PasswordValidationError`,
    `ExternalUserFieldReadOnlyError`, `UserConflictError`, and any
    underlying database or audit-service exception not translated above.
    """
    normalized_username = _normalize_username(username)
    normalized_email = _normalize_and_validate_email(email)

    if external_id is not None and password is not None:
        raise ExternalUserPasswordError()
    if external_id is None and password is None:
        raise PasswordValidationError("Password is required for local users.")

    if external_id is None and manager_id is not None:
        raise ExternalUserFieldReadOnlyError()

    if await _username_taken(session, normalized_username, None):
        raise UserConflictError("username")
    if await _email_taken(session, normalized_email, None):
        raise UserConflictError("email")
    if external_id is not None and await _external_id_taken(session, external_id, None):
        raise UserConflictError("external_id")

    password_hash: str | None = None
    if password is not None:
        validate_password(password)
        password_hash = await asyncio.to_thread(hash_password, password)

    new_user = User(
        username=normalized_username,
        email=normalized_email,
        full_name=full_name,
        active=active,
        external_id=external_id,
        manager_id=manager_id,
        password_hash=password_hash,
        synced_at=datetime.now(UTC) if external_id is not None else None,
    )

    try:
        async with session.begin_nested():
            session.add(new_user)
            await session.flush()
    except IntegrityError as exc:
        field = _conflict_field(exc)
        if field is None:
            raise
        raise UserConflictError(field) from exc

    deduped_roles: dict[tuple[Role, str], None] = {}
    for role, group_name in roles or []:
        deduped_roles.setdefault((role, group_name), None)

    for role, group_name in deduped_roles:
        session.add(
            UserRole(
                user_id=new_user.id,
                role=role.value,
                group_name=group_name,
                assigned_by=acting_user_id,
            )
        )
    await session.flush()

    for role, group_name in deduped_roles:
        is_manual = group_name == "_manual"
        await IdentityAuditLog.log_event(
            session,
            event_type=IdentityAuditEventType.ROLE_ADDED,
            user_id=acting_user_id,
            target_user_id=new_user.id,
            new_value=role_to_wire(role),
            detail=None
            if is_manual
            else {"source": "external_sync", "mapping": group_name},
        )

    await IdentityAuditLog.log_event(
        session,
        event_type=IdentityAuditEventType.USER_CREATED,
        user_id=acting_user_id,
        target_user_id=new_user.id,
        new_value=new_user.username,
        detail={"source": "external_sync"} if external_id is not None else None,
    )

    return await _load_user_profile(session, new_user.id)


async def update_user(
    session: AsyncSession,
    user_id: UUID,
    *,
    acting_user_id: UUID | None,
    username: str | None = None,
    email: str | _MissingType = _MISSING,
    full_name: str | _MissingType | None = _MISSING,
    manager_id: UUID | _MissingType | None = _MISSING,
    synced_at: datetime | _MissingType | None = _MISSING,
) -> User:
    """Update mutable identity fields of an existing `User`.

    Q1: `user_id` identifies the target; `acting_user_id` is the audit actor
    (`None` for CLI/external-sync system callers). `username` is `None`
    when not provided (usernames are never cleared, so `None` unambiguously
    means "no change"). `email`, `full_name`, `manager_id`, and `synced_at`
    default to the `_MISSING` sentinel (`docs/conventions.md`,
    `dataclasses.MISSING` pattern) to distinguish "not provided" from an
    explicit `None` (clear a nullable field). `email` excludes `None` from
    its type entirely — a caller passing `email=None` violates the type
    contract and is caught by static type checking, not a runtime guard.

    Q2 (guards, in this order): missing `user_id` raises `UserNotFoundError`;
    a human caller (`acting_user_id is not None`) targeting an external user
    (`external_id IS NOT NULL`) raises `ExternalUserFieldReadOnlyError` for
    the entire operation; `manager_id` or `synced_at` provided (not
    `_MISSING`) for a local user (`external_id IS NULL`) raises
    `ExternalUserFieldReadOnlyError` (see External User Data Ownership);
    invalid `username` format raises `UsernameFormatError`; invalid `email`
    format raises `EmailFormatError`; a normalized `username` or `email`
    already used by a different user raises `UserConflictError` (both as a
    sequential pre-check and, when concurrently lost, via the UNIQUE
    constraints below).

    Q3: acquires `SELECT ... FOR UPDATE` on the target `User` as the first
    database operation (`populate_existing=True` refreshes any stale
    identity-map state after waiting for the lock). Evaluates the guards
    above against the locked row. For each of `username`/`email`/
    `full_name`/`manager_id` whose normalized requested value differs from
    the current stored value, stages the change and its audit event; a
    requested value equal to the current one is a no-op for that field (no
    write, no audit event). `synced_at`, when provided, is applied
    unconditionally with no audit event (operational metadata exclusion —
    see Inactive User Management Principle). If nothing was staged and
    `synced_at` was not provided, this is a total no-op: returns the
    unchanged user (with `roles`/`manager` eagerly loaded) without issuing
    an UPDATE. Otherwise flushes every staged field inside a `SAVEPOINT`, so
    a concurrent same-value winner's `IntegrityError` on the username or
    email UNIQUE constraint is translated to `UserConflictError`. Then
    creates one `username_changed`/`email_changed`/`full_name_changed` event
    per changed field (`detail = {"source": "external_sync"}` when
    `external_id IS NOT NULL` — every mutation reaching this point for an
    external user is by construction performed by external sync, since
    guard 2 already blocks every human caller — otherwise `detail = None`),
    and one `manager_changed` event when `manager_id` changed (`user_id =
    None` always, per the intrinsically system-only contract in
    `docs/features/identity/identity-audit-log.md`; `old_value`/`new_value`
    are the previous/new manager's username, resolved via `_resolve_username()`,
    or `None`). Returns the updated `User` with `roles` and `manager`
    eagerly loaded.

    Q4: creates one event per effectively changed field among
    `username_changed`, `email_changed`, `full_name_changed`,
    `manager_changed`; zero events when every field is a no-op or omitted.
    `synced_at` never produces an audit event.

    Q5: conditionally idempotent. A request whose normalized values already
    match the stored state (or that omits every field) is a no-op and
    creates no audit event; only effective field changes are persisted and
    audited.

    Q6: propagates `UserNotFoundError`, `ExternalUserFieldReadOnlyError`,
    `UsernameFormatError`, `EmailFormatError`, `UserConflictError`, and any
    underlying database or audit-service exception not translated above.
    """
    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise UserNotFoundError()

    is_external = user.external_id is not None

    if is_external and acting_user_id is not None:
        raise ExternalUserFieldReadOnlyError()
    if not is_external and (manager_id is not _MISSING or synced_at is not _MISSING):
        raise ExternalUserFieldReadOnlyError()

    sync_detail = {"source": "external_sync"} if is_external else None

    # Phase 1 — normalize, validate format, and pre-check uniqueness for
    # every field WITHOUT mutating `user` yet. `_username_taken()` and
    # `_email_taken()` each run a `session.execute()`, which triggers
    # SQLAlchemy autoflush; keeping `user` clean here ensures that call
    # cannot flush an earlier field's pending mutation. Actual mutation
    # is deferred to Phase 2, inside the SAVEPOINT below — `begin_nested()`
    # itself flushes any *already* pending dirty state before the SAVEPOINT
    # is established (see `SessionTransaction._take_snapshot()`), so any
    # mutation applied before entering that block would be flushed
    # unprotected by the SAVEPOINT, exactly like a mutation applied before
    # Phase 1. Only mutations applied *inside* the block are covered.
    pending_username: str | None = None
    if username is not None:
        normalized_username = _normalize_username(username)
        if normalized_username != user.username:
            if await _username_taken(session, normalized_username, user_id):
                raise UserConflictError("username")
            pending_username = normalized_username

    pending_email: str | None = None
    if not isinstance(email, _MissingType):
        normalized_email = _normalize_and_validate_email(email)
        if normalized_email != user.email:
            if await _email_taken(session, normalized_email, user_id):
                raise UserConflictError("email")
            pending_email = normalized_email

    pending_full_name: str | _MissingType | None = _MISSING
    if not isinstance(full_name, _MissingType) and full_name != user.full_name:
        pending_full_name = full_name

    pending_manager_id: UUID | _MissingType | None = _MISSING
    if not isinstance(manager_id, _MissingType) and manager_id != user.manager_id:
        pending_manager_id = manager_id

    if (
        pending_username is None
        and pending_email is None
        and isinstance(pending_full_name, _MissingType)
        and isinstance(pending_manager_id, _MissingType)
        and synced_at is _MISSING
    ):
        return await _load_user_profile(session, user.id)

    # Phase 2 — apply every staged mutation and build the audit payload
    # together, inside the SAVEPOINT-protected block, so a concurrent
    # same-value winner's `IntegrityError` is translated to
    # `UserConflictError` without leaving the caller's transaction aborted.
    changed_fields: list[
        tuple[IdentityAuditEventType, str | None, str | None, dict[str, str] | None]
    ] = []
    manager_changed = False
    old_manager_id: UUID | None = None
    new_manager_id: UUID | None = None

    try:
        async with session.begin_nested():
            if pending_username is not None:
                old_username = user.username
                user.username = pending_username
                changed_fields.append(
                    (
                        IdentityAuditEventType.USERNAME_CHANGED,
                        old_username,
                        pending_username,
                        sync_detail,
                    )
                )

            if pending_email is not None:
                old_email = user.email
                user.email = pending_email
                changed_fields.append(
                    (
                        IdentityAuditEventType.EMAIL_CHANGED,
                        old_email,
                        pending_email,
                        sync_detail,
                    )
                )

            if not isinstance(pending_full_name, _MissingType):
                old_full_name = user.full_name
                user.full_name = pending_full_name
                changed_fields.append(
                    (
                        IdentityAuditEventType.FULL_NAME_CHANGED,
                        old_full_name,
                        pending_full_name,
                        sync_detail,
                    )
                )

            if not isinstance(pending_manager_id, _MissingType):
                old_manager_id = user.manager_id
                new_manager_id = pending_manager_id
                user.manager_id = pending_manager_id
                manager_changed = True

            if not isinstance(synced_at, _MissingType):
                user.synced_at = synced_at

            await session.flush()
    except IntegrityError as exc:
        field = _conflict_field(exc)
        if field is None:
            raise
        raise UserConflictError(field) from exc

    for event_type, old_value, new_value, detail in changed_fields:
        await IdentityAuditLog.log_event(
            session,
            event_type=event_type,
            user_id=acting_user_id,
            target_user_id=user.id,
            old_value=old_value,
            new_value=new_value,
            detail=detail,
        )

    if manager_changed:
        old_manager_username = await _resolve_username(session, old_manager_id)
        new_manager_username = await _resolve_username(session, new_manager_id)
        await IdentityAuditLog.log_event(
            session,
            event_type=IdentityAuditEventType.MANAGER_CHANGED,
            user_id=None,
            target_user_id=user.id,
            old_value=old_manager_username,
            new_value=new_manager_username,
            detail=None,
        )

    return await _load_user_profile(session, user.id)


async def reactivate_user(
    session: AsyncSession,
    user_id: UUID,
    *,
    acting_user_id: UUID | None,
) -> User:
    """Reactivate a previously deactivated `User`.

    Q1: `user_id` identifies the target; `acting_user_id` is the audit actor
    (`None` for CLI/external-sync system callers).

    Q2 (guards, in this order): missing `user_id` raises
    `UserNotFoundError`; an already-active user short-circuits as a no-op
    (see Q3) before any further guard; a human caller
    (`acting_user_id is not None`) targeting an inactive external user
    (`external_id IS NOT NULL`) raises `ExternalUserStatusReadOnlyError`
    (see External Active Status Ownership).

    Q3: acquires `SELECT ... FOR UPDATE` on the target `User` as the first
    database operation. An already-active user returns unchanged (with
    `roles`/`manager` eagerly loaded) without evaluating the external-status
    guard or creating an audit event. Otherwise evaluates the external-status
    guard, sets `User.active = True`, flushes, creates one `user_reactivated`
    event (`detail = {"source": "external_sync"}` when `external_id IS NOT
    NULL` — reaching the mutation for an external user is by construction
    performed by external sync, since the guard above already blocks every
    human caller — otherwise `detail = None`), and returns the updated
    `User` with `roles` and `manager` eagerly loaded. Roles, tickets,
    sessions, and API keys are left untouched.

    Q4: creates exactly one `user_reactivated` event on the first effective
    reactivation; the idempotent no-op path creates none.

    Q5: idempotent. Once active, another call returns the unchanged user
    and creates no audit event.

    Q6: propagates `UserNotFoundError`, `ExternalUserStatusReadOnlyError`,
    and any underlying database or audit-service exception.
    """
    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise UserNotFoundError()

    if user.active:
        return await _load_user_profile(session, user.id)

    if user.external_id is not None and acting_user_id is not None:
        raise ExternalUserStatusReadOnlyError()

    user.active = True
    await session.flush()

    await IdentityAuditLog.log_event(
        session,
        event_type=IdentityAuditEventType.USER_REACTIVATED,
        user_id=acting_user_id,
        target_user_id=user.id,
        old_value="inactive",
        new_value="active",
        detail={"source": "external_sync"} if user.external_id is not None else None,
    )

    return await _load_user_profile(session, user.id)


async def reset_password(
    session: AsyncSession,
    user_id: UUID,
    new_password: str,
    *,
    acting_user_id: UUID | None,
) -> PasswordResetResult:
    """Reset the password of a local `User` and invalidate its active sessions.

    Q1: `user_id` identifies the target; `new_password` is the new plain-text
    password (validated and hashed internally, never persisted or logged as
    plaintext); `acting_user_id` is the audit actor (`None` for CLI/system
    callers).

    Q2 (guards, in this order): password length outside 16-128 characters
    raises `PasswordValidationError` — evaluated before any database
    operation, using only the input. A missing `user_id` raises
    `UserNotFoundError`; an external target (`external_id IS NOT NULL`)
    raises `ExternalUserPasswordError`: "Cannot set password for external
    user. External users authenticate via SSO." Both are evaluated after
    validation and hashing, as the first database operation.

    Q3: validates and hashes `new_password` (bcrypt, off the event loop via
    `asyncio.to_thread()`) before acquiring any lock. Then acquires
    `SELECT ... FOR UPDATE` on the target `User` as the first database
    operation, evaluates the guards above, replaces `User.password_hash`
    with the new hash, invalidates all active sessions via
    `session_service.invalidate_user_sessions()` (DB only), creates one
    `password_reset` `IdentityAuditEvent` (`old_value`, `new_value`, and
    `detail` all `None`), flushes, and returns
    `PasswordResetResult(user, invalidated_session_ids, username)`. This
    function performs only the database phase — it does not commit or
    execute Redis I/O. The caller invokes
    `session_service.purge_session_cache()` and
    `local_auth_service.clear_login_attempts()` after its own commit
    succeeds (`docs/conventions.md`, Transaction Hygiene Rules).

    Q4: creates exactly one `password_reset` event per successful
    invocation, with `user_id = acting_user_id` and
    `target_user_id = user_id`.

    Q5: not idempotent. Each successful invocation hashes and stores the
    supplied password anew, invalidates the sessions active at that
    invocation, and creates one new audit event.

    Q6: propagates `PasswordValidationError`, `UserNotFoundError`,
    `ExternalUserPasswordError`, and any underlying database or
    audit-service exception.
    """
    validate_password(new_password)
    new_password_hash = await asyncio.to_thread(hash_password, new_password)

    result = await session.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise UserNotFoundError()
    if user.external_id is not None:
        raise ExternalUserPasswordError()

    user.password_hash = new_password_hash

    invalidated_session_ids = await invalidate_user_sessions(
        session, user.id, SessionInvalidationReason.PASSWORD_RESET
    )

    await IdentityAuditLog.log_event(
        session,
        event_type=IdentityAuditEventType.PASSWORD_RESET,
        user_id=acting_user_id,
        target_user_id=user.id,
        old_value=None,
        new_value=None,
        detail=None,
    )

    await session.flush()

    return PasswordResetResult(
        user=user,
        invalidated_session_ids=invalidated_session_ids,
        username=user.username,
    )


async def unlock_user(
    session: AsyncSession,
    user_id: UUID,
    *,
    acting_user_id: UUID | None,
) -> None:
    """Clear the login lockout counter for a user.

    Q1: `user_id` identifies the target. `acting_user_id` is accepted for
    lifecycle-call signature uniformity and has no persisted effect —
    unlock creates no audit event.

    Q2: a missing `user_id` raises `UserNotFoundError`. No other guard
    applies — active, inactive, local, and external users are all eligible.

    Q3: loads the user by `user_id` (read-only, no row lock). Deletes the
    Redis key `login_attempts:{username}` (where `username` is the user's
    current stored username) via
    `local_auth_service.clear_login_attempts()`, which catches every
    `RedisError` and logs a PII-free WARNING — the counter then expires
    naturally via TTL. Logs `user_unlocked` at INFO with `user_id` (no
    username or other personal identifier, per
    `docs/features/platform/logging.md`). Performs no database mutation,
    flush, commit, rollback, session invalidation, or audit event.

    Q4: None. Lockout is transient Redis-only state, not a persistent
    identity mutation (see
    `docs/features/identity/user-service.md`, `unlock_user()`).

    Q5: idempotent. If the user is not currently locked out (Redis key
    absent or counter zero), the operation completes successfully as a
    no-op.

    Q6: propagates `UserNotFoundError` and any underlying database
    exception from the user lookup. Every `RedisError` from the lockout
    deletion is caught internally and never propagates.
    """
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise UserNotFoundError()

    await clear_login_attempts(user.username)

    logger.info("user_unlocked", user_id=str(user.id))
