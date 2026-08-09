"""Response schemas for the standard API error envelope.

See `docs/api-spec.md` (Response Format) for the authoritative error
body contract. Used only for OpenAPI documentation (`responses={...}`)
— API handlers raise `app.core.errors.AppError`, whose exception
handler (registered in `app.main`) builds the actual JSON body; these
schemas never construct a response themselves.
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """The standard `{"code": ..., "detail": ...}` error body."""

    code: str
    detail: str
