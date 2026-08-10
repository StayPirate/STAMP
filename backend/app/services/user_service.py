"""User identifier resolution and role queries.

See `docs/features/identity/user-service.md` for the authoritative
contract this module implements. Only the pieces consumed by the
shared authentication/authorization dependencies (P2-06) are present so
far: `resolve_user_identifier()` (User Identifier Resolution) and
`get_user_roles()`, a read helper backing `require_capability()`'s
requirement to "load the user's current roles from the `UserRole`
table" (`docs/features/identity/rbac.md`, `require_capability()`
Dependency) without an API-layer ORM query. The full lifecycle
operations (`create_user`, `update_user`, `update_roles`,
`deactivate_user`, `reactivate_user`, etc.) are out of scope for this
piece and are added when their owning work item is implemented.

Module-level defaults (`docs/conventions.md`, Function Specification
Completeness): every function in this module is read-only, creates no
audit event, and propagates `UserNotFoundError` (shared,
`app.core.exceptions`) plus any underlying database exception — no
function defines a broader hierarchy than documented.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Role
from app.core.exceptions import UserNotFoundError
from app.models.user import User
from app.models.user_role import UserRole


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
