"""Request/response schemas for local authentication endpoints.

See `docs/features/identity/local-authentication.md` (Login Endpoint)
for the authoritative request/response contract. `password` and
`username` intentionally carry no `max_length`/`min_length`
constraints: the login guards (overlong password, overlong or empty
normalized username) must produce the documented generic `401`
response rather than a `422` schema-validation error — see
`docs/features/identity/local-authentication.md` (Login, Behavior,
steps 1-3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Request body for `POST /api/v1/auth/login`."""

    username: str
    password: str = Field(repr=False)


class LoginData(BaseModel):
    """The `data` payload of a successful login response."""

    access_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class LoginResponse(BaseModel):
    """Response body for a successful `POST /api/v1/auth/login` call."""

    data: LoginData
