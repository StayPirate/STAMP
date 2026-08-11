"""Request/response/query schemas for API key management endpoints.

See `docs/features/identity/api-key-management.md` (API Key Contract,
API) for the authoritative request/response contract these schemas
implement.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.core.enums import ApiKeySortField, ApiKeyStatus, SortOrder
from app.schemas.common import PaginationMeta, UserReference


class ApiKeyCreateRequest(BaseModel):
    """Request body for `POST /api/v1/api-keys`.

    `name` intentionally carries no length/charset constraint: the API
    Key Name Rule (trim, lowercase, 1-128 characters, `[a-z0-9._-]`) is
    applied and enforced by `api_key_service.create_key()`, which
    raises the domain-specific `ApiKeyNameValidationError` — a schema
    constraint here would instead produce the generic
    `422 VALIDATION_ERROR`, losing the documented
    `AUTH_API_KEY_NAME_INVALID` code.
    """

    name: str
    expires_at: datetime | None = None

    @field_validator("expires_at", mode="before")
    @classmethod
    def _normalize_expires_at(cls, value: Any) -> Any:
        """Normalize `expires_at` per `api-key-management.md` (Expiration).

        `None` passes through unchanged. Any non-string value (e.g. a
        JSON number/unix timestamp) is rejected — the endpoint accepts
        only a full ISO 8601 datetime string, not an epoch timestamp
        (which Pydantic's default `datetime` parsing would otherwise
        silently accept). A value that parses as a bare ISO 8601
        *date* (no time component) is rejected, per the documented
        "date-only value is not accepted" rule. A naive datetime
        (no offset) is interpreted as UTC; an offset-bearing datetime
        is converted to UTC. Any other malformed value raises
        `ValueError`, which Pydantic renders as the standard `422
        VALIDATION_ERROR`.
        """
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("expires_at must be an ISO 8601 datetime string.")
        try:
            date.fromisoformat(value)
        except ValueError:
            pass
        else:
            raise ValueError("expires_at must include a time component.")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("expires_at must be a valid ISO 8601 datetime.") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class ApiKeyListQuery(BaseModel):
    """Common list query parameters shared by the self-service and
    admin API key list endpoints (`api-key-management.md`, API).

    `status` is intentionally `str | None`, not `ApiKeyStatus | None`:
    an invalid value must be silently ignored and produce an empty
    result (`docs/api-spec.md`, Enum Filter Validation) rather than the
    schema-validation `422` a typed enum field would raise. The route
    handler parses it against `ApiKeyStatus` itself.
    """

    status: str | None = None
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=20, ge=1, le=100)
    sort_by: ApiKeySortField = ApiKeySortField.CREATED_AT
    sort_order: SortOrder = SortOrder.DESC


class AdminApiKeyListQuery(ApiKeyListQuery):
    """Admin list query parameters: the common set plus `owner`."""

    owner: str | None = None


class ApiKeyData(BaseModel):
    """The common API key object (`api-key-management.md`, API) —
    every stored non-secret field needed to identify and manage a key.
    Never includes the plaintext key or its hash.
    """

    id: UUID
    prefix: str
    name: str
    status: ApiKeyStatus
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by: UserReference | None


class CreatedApiKeyData(ApiKeyData):
    """The creation-response object: the common object plus the
    plaintext `key`. `key` appears only in this response — no other
    schema in this module carries it."""

    key: str = Field(repr=False)


class AdminApiKeyData(ApiKeyData):
    """The admin list/revoke object: the common object plus `owner`,
    the standard User Reference Object."""

    owner: UserReference


class ApiKeyResponse(BaseModel):
    """Response body for a single self-service API key (revoke)."""

    data: ApiKeyData


class CreatedApiKeyResponse(BaseModel):
    """Response body for `POST /api/v1/api-keys`."""

    data: CreatedApiKeyData


class AdminApiKeyResponse(BaseModel):
    """Response body for a single admin API key (revoke)."""

    data: AdminApiKeyData


class ApiKeyListResponse(BaseModel):
    """Response body for `GET /api/v1/api-keys`."""

    data: list[ApiKeyData]
    meta: PaginationMeta


class AdminApiKeyListResponse(BaseModel):
    """Response body for `GET /api/v1/admin/api-keys`."""

    data: list[AdminApiKeyData]
    meta: PaginationMeta
