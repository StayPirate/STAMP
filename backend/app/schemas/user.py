"""Request/response/query schemas for the public user directory and
profile endpoints.

See `docs/features/identity/user-management.md` (List Users, Get User)
for the authoritative request/response contract these schemas implement.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.enums import SortOrder, UserSortField
from app.schemas.common import PaginationMeta


class UserListQuery(BaseModel):
    """Query parameters for `GET /api/v1/users` (`user-management.md`,
    List Users).

    `type` and `role` are intentionally `str | None` / `list[str]`, not
    typed enums: an invalid value must be silently ignored and produce
    an empty result (`docs/api-spec.md`, Enum Filter Validation) rather
    than the schema-validation `422` a typed enum field would raise. The
    route handler parses them against `UserType`/`Role` itself.
    """

    search: str | None = Field(default=None, min_length=2)
    type: str | None = None
    active: bool | None = None
    role: list[str] = Field(default_factory=list)
    has_role: bool | None = None
    page: int = Field(default=1, ge=1, le=2_147_483_647)
    per_page: int = Field(default=20, ge=1, le=100)
    sort_by: UserSortField = UserSortField.USERNAME
    sort_order: SortOrder = SortOrder.ASC


class UserManagerData(BaseModel):
    """The `manager` object in a full user profile — the standard User
    Reference Object shape plus `email`, per `user-management.md` (Get
    User)."""

    id: UUID
    username: str
    full_name: str | None
    active: bool
    email: str


class UserRoleAssignmentData(BaseModel):
    """One entry of a full user profile's `roles` array
    (`user-management.md`, Get User)."""

    role: str
    group_name: str
    assigned_by: UUID | None
    created_at: datetime


class UserData(BaseModel):
    """The full user profile object shared by `GET /api/v1/users` (list
    items) and `GET /api/v1/users/{user}` (`user-management.md`, Get
    User). `full_name` and `manager.full_name` are nullable — the API
    returns `null` verbatim with no fallback substitution."""

    id: UUID
    username: str
    email: str
    full_name: str | None
    active: bool
    source: str
    external_id: UUID | None
    manager: UserManagerData | None
    roles: list[UserRoleAssignmentData]
    created_at: datetime
    updated_at: datetime


class UserResponse(BaseModel):
    """Response body for `GET /api/v1/users/{user}`."""

    data: UserData


class UserListResponse(BaseModel):
    """Response body for `GET /api/v1/users`."""

    data: list[UserData]
    meta: PaginationMeta
