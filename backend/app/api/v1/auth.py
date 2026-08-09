"""Authentication endpoints.

See `docs/features/identity/authentication.md` (Logout) for the
authoritative endpoint contract this module implements.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.credentials import API_KEY_PREFIX, extract_credential
from app.core.errors import AppError, ErrorCode
from app.core.jwt import InvalidTokenError, decode_for_logout
from app.database import get_db, register_post_commit_callback
from app.schemas.errors import ErrorResponse
from app.services.session_service import invalidate_session, purge_session_cache

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

_COOKIE_NAME = "sentinel_session"

# The exact cookie-clearing header text from authentication.md (Logout,
# step 5). Built manually rather than via `Response.set_cookie()`:
# Python's stdlib `http.cookies` unconditionally double-quotes an empty
# cookie value (`sentinel_session=""`), which would diverge from the
# documented literal header value below (`sentinel_session=`, no quotes).
_CLEAR_COOKIE_HEADER = (
    f"{_COOKIE_NAME}=; Path=/api; Max-Age=0; HttpOnly; Secure; SameSite=Strict"
)


def _unauthenticated_error() -> AppError:
    """Create a fresh generic authentication failure.

    Exception instances retain traceback state when raised. Returning a
    fresh instance prevents failed requests from accumulating traceback
    frames and request-local credential data on a module-level singleton.
    """
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code=ErrorCode.AUTH_NOT_AUTHENTICATED,
        detail="Authentication required",
    )


def _clear_session_cookie(response: Response) -> None:
    """Set the exact cookie-clearing `Set-Cookie` header — see
    `_CLEAR_COOKIE_HEADER`."""
    response.headers.append("set-cookie", _CLEAR_COOKIE_HEADER)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out",
    description=(
        "Invalidates the current session and clears the session cookie. "
        "Idempotent — safe to call multiple times, including with an "
        "already-invalidated or missing session, or a temporally expired "
        "(but validly signed) token."
    ),
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Called with an API key credential instead of a JWT.",
        },
    },
)
async def logout(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> Response:
    """Logout — see `docs/features/identity/authentication.md` (Logout)."""
    credential = extract_credential(
        request.headers.get("authorization"), request.cookies.get(_COOKIE_NAME)
    )
    if credential is None:
        raise _unauthenticated_error()
    if credential.startswith(API_KEY_PREFIX):
        raise AppError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.AUTH_LOGOUT_NOT_APPLICABLE,
            detail="Logout is not applicable to API key authentication.",
        )
    try:
        claims = decode_for_logout(
            credential, secret_key=settings.jwt_secret_key.get_secret_value()
        )
    except InvalidTokenError:
        raise _unauthenticated_error() from None

    session_id = await invalidate_session(db, claims.session_id)
    register_post_commit_callback(db, lambda: purge_session_cache([session_id]))

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_session_cookie(response)
    return response
