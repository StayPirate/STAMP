"""Public user directory, profile, and current-user endpoints.

See `docs/features/identity/user-management.md` (List Users, Get User)
and `docs/features/identity/authentication.md` (Get Current User) for
the authoritative endpoint contracts this module implements. Handlers
stay thin: they validate, delegate to `user_service`
(`docs/features/identity/user-service.md`), and map the result or a
typed service exception to the documented response — no business logic
or database query lives here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import CurrentUser, user_not_found_error
from app.core.enums import Role, SortOrder, UserSortField, UserType
from app.core.exceptions import UserNotFoundError
from app.core.permissions import role_from_wire, role_to_wire
from app.database import DatabaseSession
from app.models.user import User
from app.schemas.auth import CurrentUserData, CurrentUserResponse
from app.schemas.common import PaginationMeta
from app.schemas.errors import ErrorResponse
from app.schemas.user import (
    UserData,
    UserListQuery,
    UserListResponse,
    UserManagerData,
    UserResponse,
    UserRoleAssignmentData,
)
from app.services import user_service

router = APIRouter(prefix="/api/v1", tags=["Users"])


# ---------------------------------------------------------------------------
# Query parameter builder
#
# Declared as individual `Query()`-annotated parameters (rather than a
# single `Annotated[Model, Query()]` query-parameter-model field) so
# each field is visible individually to the shared query-length-limit
# dependency's route-dependant walk (`app.core.query_limits`), which
# inspects `Dependant.query_params` at every nesting depth. Mirrors
# `app/api/v1/api_keys.py`.
# ---------------------------------------------------------------------------


def _user_list_query(
    *,
    search: Annotated[
        str | None,
        Query(
            min_length=2,
            description=(
                "Case-insensitive substring across username, email, and full name."
            ),
        ),
    ] = None,
    type_filter: Annotated[
        str | None,
        Query(alias="type", description="One of 'local' or 'external'."),
    ] = None,
    active: Annotated[
        bool | None, Query(description="Filter by active status.")
    ] = None,
    role: Annotated[
        list[str],
        Query(
            default_factory=list,
            description=(
                "One or more of 'admin', 'vulnerability_analyst', "
                "'restricted_analyst'. Repeatable; OR semantics."
            ),
        ),
    ],
    has_role: Annotated[
        bool | None,
        Query(description="true for users with at least one role, false for none."),
    ] = None,
    page: Annotated[int, Query(ge=1, le=2_147_483_647, description="Page number.")] = 1,
    per_page: Annotated[
        int, Query(ge=1, le=100, description="Items per page; maximum 100.")
    ] = 20,
    sort_by: Annotated[
        UserSortField,
        Query(
            description="'username' (default), 'full_name', 'email', or 'created_at'."
        ),
    ] = UserSortField.USERNAME,
    sort_order: Annotated[SortOrder, Query(description="'asc' or 'desc'.")] = (
        SortOrder.ASC
    ),
) -> UserListQuery:
    return UserListQuery(
        search=search,
        type=type_filter,
        active=active,
        role=role,
        has_role=has_role,
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_order=sort_order,
    )


def _parse_user_type(raw: str | None) -> tuple[bool, UserType | None]:
    """Resolve the raw `type` query value to a typed filter.

    Returns `(True, None)` when `raw` is absent, `(True, <type>)` when
    it matches a valid `UserType` member, and `(False, None)` when
    present but invalid — the caller must then render an empty page
    without querying the service, per `docs/api-spec.md` (Enum Filter
    Validation): an invalid single-value enum filter yields an empty
    result, not an error.
    """
    if raw is None:
        return True, None
    try:
        return True, UserType(raw)
    except ValueError:
        return False, None


def _parse_role_filters(raw: list[str]) -> tuple[bool, list[Role]]:
    """Resolve the raw repeatable `role` query values to typed filters.

    Returns `(True, [])` when `raw` is empty (no filter applied),
    `(True, [...])` with the valid subset when at least one value is
    valid, and `(False, [])` when `raw` is non-empty but every value is
    invalid — the caller must then render an empty page without
    querying the service, per `docs/api-spec.md` (Enum Filter
    Validation): if all provided values are invalid, the result is
    empty, not unfiltered.
    """
    if not raw:
        return True, []
    valid: list[Role] = []
    for value in raw:
        try:
            valid.append(role_from_wire(value))
        except ValueError:
            continue
    if not valid:
        return False, []
    return True, valid


# ---------------------------------------------------------------------------
# Response serialization
# ---------------------------------------------------------------------------


def _serialize_user(user: User) -> UserData:
    """Build the full user profile response object.

    `source` is derived (never stored) from `external_id`. `roles` is
    ordered alphabetically by wire-format role value, then `group_name`,
    then `id` — deterministic regardless of assignment order or
    database physical order (`docs/features/identity/rbac.md`,
    Deterministic ordering).
    """
    manager = None
    if user.manager is not None:
        manager = UserManagerData(
            id=user.manager.id,
            username=user.manager.username,
            full_name=user.manager.full_name,
            active=user.manager.active,
            email=user.manager.email,
        )

    sorted_roles = sorted(
        user.roles,
        key=lambda user_role: (
            role_to_wire(Role(user_role.role)),
            user_role.group_name,
            user_role.id,
        ),
    )
    roles = [
        UserRoleAssignmentData(
            role=role_to_wire(Role(user_role.role)),
            group_name=user_role.group_name,
            assigned_by=user_role.assigned_by,
            created_at=user_role.created_at,
        )
        for user_role in sorted_roles
    ]

    return UserData(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        active=user.active,
        source="external" if user.external_id is not None else "local",
        external_id=user.external_id,
        manager=manager,
        roles=roles,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
#
# `/users/me` MUST be registered before `/users/{user}`: Starlette
# matches routes in declaration order, and `/users/{user}` would
# otherwise capture the literal path segment `me` as a username.
# ---------------------------------------------------------------------------


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="List users",
    description=(
        "Returns a paginated public directory of users. Supports search "
        "across username, email, and full name; local/external type; "
        "active status; repeatable role (OR semantics); and has_role "
        "filters, plus standard pagination and sorting. Public endpoint."
    ),
)
async def list_users(
    db: DatabaseSession,
    query: Annotated[UserListQuery, Depends(_user_list_query)],
) -> UserListResponse:
    """List users — see
    `docs/features/identity/user-management.md` (List Users)."""
    type_valid, user_type = _parse_user_type(query.type)
    roles_valid, roles = _parse_role_filters(query.role)
    if not type_valid or not roles_valid:
        return UserListResponse(
            data=[],
            meta=PaginationMeta(total=0, page=query.page, per_page=query.per_page),
        )

    page = await user_service.list_users(
        db,
        search=query.search,
        user_type=user_type,
        active=query.active,
        roles=roles,
        has_role=query.has_role,
        page=query.page,
        per_page=query.per_page,
        sort_by=query.sort_by,
        sort_order=query.sort_order,
    )
    return UserListResponse(
        data=[_serialize_user(user) for user in page.items],
        meta=PaginationMeta(total=page.total, page=page.page, per_page=page.per_page),
    )


@router.get(
    "/users/me",
    response_model=CurrentUserResponse,
    summary="Get my profile",
    description=(
        "Returns the concise profile of the currently authenticated user, "
        "including current role values. Requires authentication (JWT "
        "session or API key)."
    ),
)
async def get_current_user_profile(
    principal: CurrentUser, db: DatabaseSession
) -> CurrentUserResponse:
    """Get current user — see
    `docs/features/identity/authentication.md` (Get Current User)."""
    roles = await user_service.get_user_roles(db, principal.user.id)
    return CurrentUserResponse(
        data=CurrentUserData(
            id=principal.user.id,
            username=principal.user.username,
            email=principal.user.email,
            full_name=principal.user.full_name,
            roles=sorted(role_to_wire(role) for role in roles),
            active=principal.user.active,
        )
    )


@router.get(
    "/users/{user}",
    response_model=UserResponse,
    summary="Get user",
    description=(
        "Returns the full profile of one user, resolved by UUID or exact, "
        "case-sensitive username. Public endpoint."
    ),
    responses={
        404: {
            "model": ErrorResponse,
            "description": "No user found matching the given UUID or username.",
        },
    },
)
async def get_user(user: str, db: DatabaseSession) -> UserResponse:
    """Get user — see
    `docs/features/identity/user-management.md` (Get User)."""
    try:
        resolved = await user_service.get_user(db, user)
    except UserNotFoundError:
        raise user_not_found_error() from None
    return UserResponse(data=_serialize_user(resolved))
