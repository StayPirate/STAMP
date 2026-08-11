"""Response schemas shared across multiple feature areas.

See `docs/api-spec.md` (Response Format, User References in Responses)
for the authoritative contracts these schemas implement.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class PaginationMeta(BaseModel):
    """The `meta` object of a paginated list response.

    See `docs/api-spec.md` (Response Format, Pagination).
    """

    total: int
    page: int
    per_page: int


class UserReference(BaseModel):
    """The standard User Reference Object embedded in response payloads
    that reference a user (e.g. `owner`, `revoked_by`).

    See `docs/api-spec.md` (User References in Responses): populated
    from the *current* `User` row via a JOIN — never a historical
    snapshot of the user's profile data at the time of the referenced
    event or action.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    full_name: str | None
    active: bool
