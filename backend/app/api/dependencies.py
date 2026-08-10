"""Shared authentication and authorization FastAPI dependencies.

See `docs/features/identity/authentication.md` (Authenticated Principal,
Middleware: `get_current_user`, API key validation, Session-Only
Authentication Dependency) and `docs/features/identity/rbac.md`
(`require_capability()` Dependency) for the authoritative contracts
this module implements.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.core.credentials import API_KEY_PREFIX, extract_credential
from app.core.enums import Capability, CredentialKind
from app.core.errors import AppError, ErrorCode
from app.core.jwt import InvalidTokenError, decode_and_validate, refresh_token
from app.core.permissions import get_capabilities
from app.database import DatabaseSession, async_session_factory
from app.models.user import User
from app.services import api_key_service, user_service
from app.services.session_service import is_session_active

logger = structlog.get_logger(__name__)

# The session cookie name and attributes are shared by every code path
# that sets or reads it: the login endpoint (`app/api/v1/auth.py`),
# this module's sliding-refresh attachment, and the logout endpoint's
# clearing header. Centralized here so all three stay in sync.
SESSION_COOKIE_NAME = "sentinel_session"


def unauthenticated_error() -> AppError:
    """Create a fresh generic 401 authentication failure.

    Shared by every credential-resolution failure path in
    `get_current_user()` (missing credential, invalid JWT, failed
    session liveness, unknown/revoked/expired API key, missing/inactive
    user) and by the logout endpoint's lightweight JWT-only dependency
    — see `docs/features/identity/authentication.md` (Credential
    resolution): "All HTTP 401 responses return a generic body ...
    regardless of the specific failure reason." A fresh instance per
    call avoids accumulating traceback state and request-local
    credential data on a shared singleton exception.
    """
    return AppError(
        status_code=status.HTTP_401_UNAUTHORIZED,
        code=ErrorCode.AUTH_NOT_AUTHENTICATED,
        detail="Authentication required",
    )


def user_not_found_error() -> AppError:
    """Create the standard 404 for an unresolved user-identifying parameter.

    See `docs/api-spec.md` (User Identifier Resolution). Endpoints that
    resolve a UUID-or-username parameter via
    `user_service.resolve_user_identifier()` (or any other service
    raising the shared `UserNotFoundError`) catch it and raise this,
    so every such endpoint returns the identical envelope.
    """
    return AppError(
        status_code=status.HTTP_404_NOT_FOUND,
        code=ErrorCode.USER_NOT_FOUND,
        detail="User not found.",
    )


def set_session_cookie(response: Response, token: str) -> None:
    """Set the `sentinel_session` cookie with the approved secure attributes.

    Shared by the login endpoint (initial issuance) and this module's
    sliding-refresh attachment — see
    `docs/features/identity/authentication.md` (Frontend session
    behavior, Token refresh) for the attribute rationale.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/api",
    )


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """The resolved identity of an authenticated request.

    See `docs/features/identity/authentication.md` (Authenticated
    Principal): carries the active `User` and the credential mechanism
    that authenticated the request, without retaining any raw
    credential material (no JWT, cookie value, API key plaintext,
    digest, prefix, or API key name). This is the single return type of
    `get_current_user()` and the input to every downstream
    authorization dependency (`require_capability()`,
    `require_session_authentication()`).
    """

    user: User
    credential_kind: CredentialKind


# ---------------------------------------------------------------------------
# Unknown API key warning limiter
# ---------------------------------------------------------------------------
#
# See `docs/features/identity/authentication.md` (API key validation,
# step 2): a bounded, per-instance, per-ASGI-peer-address rate limiter
# for the `api_key_validation_failed` WARNING emitted on a hash lookup
# miss only. Denial (the HTTP 401 response) is never rate-limited —
# only the log emission is suppressed.

_LIMITER_WINDOW_SECONDS = 60
_LIMITER_INACTIVITY_SECONDS = 300
_LIMITER_MAX_ENTRIES = 10_000
_UNKNOWN_PEER_SENTINEL = "unknown"


@dataclass
class _LimiterEntry:
    last_emitted_at: datetime
    last_seen_at: datetime
    suppressed_count: int


class UnknownKeyWarningLimiter:
    """Per-instance, per-peer rate limiter for the unknown-API-key WARNING.

    Bounds: at most one WARNING per 60 seconds per ASGI peer address: a
    10,000-entry maximum with LRU eviction on overflow, and a 5-minute
    inactivity eviction — see `docs/features/identity/authentication.md`
    (API key validation, step 2). Not designed for cross-task
    concurrency control (a single asyncio event loop serializes calls
    to `record()` within one server process); multiple worker processes
    each maintain independent state ("with N instances, the worst case
    is N WARNINGs per minute per IP").
    """

    def __init__(self) -> None:
        # Ordered by ascending `last_seen_at`: every access moves its
        # entry to the end, so the front is always the least-recently
        # seen — both LRU eviction and inactivity eviction scan from
        # the front and stop at the first entry that is still fresh.
        self._entries: OrderedDict[str, _LimiterEntry] = OrderedDict()

    def record(self, peer: str, now: datetime) -> int | None:
        """Register one failed lookup from `peer` at `now`.

        Returns the `suppressed_count` to log when a WARNING should be
        emitted for this call, or `None` when emission is suppressed
        (within the 60-second window of a previous emission for the
        same peer).
        """
        self._evict_inactive(now)

        entry = self._entries.get(peer)
        if entry is None:
            if len(self._entries) >= _LIMITER_MAX_ENTRIES:
                self._entries.popitem(last=False)
            self._entries[peer] = _LimiterEntry(
                last_emitted_at=now, last_seen_at=now, suppressed_count=0
            )
            return 0

        entry.last_seen_at = now
        self._entries.move_to_end(peer)
        if (now - entry.last_emitted_at).total_seconds() >= _LIMITER_WINDOW_SECONDS:
            emitted = entry.suppressed_count
            entry.last_emitted_at = now
            entry.suppressed_count = 0
            return emitted
        entry.suppressed_count += 1
        return None

    def _evict_inactive(self, now: datetime) -> None:
        while self._entries:
            peer, entry = next(iter(self._entries.items()))
            if (now - entry.last_seen_at).total_seconds() < _LIMITER_INACTIVITY_SECONDS:
                break
            del self._entries[peer]


# Module-level singleton — one limiter per server process, matching the
# "per-instance in-memory dictionary (no Redis)" storage contract.
_unknown_key_limiter = UnknownKeyWarningLimiter()


def _record_unknown_key_attempt(request: Request, now: datetime) -> None:
    """Update the limiter for an API key hash lookup miss and emit the
    rate-limited WARNING when not suppressed.

    The peer key is the ASGI peer address (`request.client.host`); a
    `None` `request.client` (e.g. a Unix domain socket connection) uses
    the sentinel `"unknown"` so all such requests share one bucket. No
    forwarded header (`X-Forwarded-For`, `Forwarded`) is consulted.
    """
    peer = request.client.host if request.client is not None else _UNKNOWN_PEER_SENTINEL
    suppressed_count = _unknown_key_limiter.record(peer, now)
    if suppressed_count is not None:
        logger.warning(
            "api_key_validation_failed",
            source_ip=peer,
            suppressed_count=suppressed_count,
        )


# ---------------------------------------------------------------------------
# `last_used_at` debounce
# ---------------------------------------------------------------------------
#
# See `docs/features/identity/authentication.md` (API key validation,
# step 5): the debounced operational touch, at most once per minute per
# key per server instance, in a transaction dedicated to this
# best-effort write.

_DEBOUNCE_INTERVAL_SECONDS = 60


def get_last_used_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory for the dedicated `last_used_at` touch
    transaction.

    Performs no I/O — returns the production `async_session_factory`.
    Extracted as its own function — mirroring
    `session_service.get_session_redis_url()` — so tests can redirect it
    via `monkeypatch.setattr()` to a factory bound to the test engine,
    without needing a FastAPI dependency override.
    """
    return async_session_factory


class LastUsedDebouncer:
    """Per-instance debounce state for the `last_used_at` operational touch.

    A per-key `asyncio.Lock` serializes concurrent requests
    authenticating with the same key so they cannot both observe a
    stale "not yet written this minute" state and issue two writes.
    The lock map grows with the number of distinct keys ever
    authenticated by this process — bounded by the number of API keys
    that actually exist (unlike the peer-address limiter above, which
    must defend against unbounded IP spoofing).
    """

    def __init__(self) -> None:
        self._last_write_at: dict[UUID, datetime] = {}
        self._locks: dict[UUID, asyncio.Lock] = {}

    def _lock_for(self, key_id: UUID) -> asyncio.Lock:
        lock = self._locks.get(key_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key_id] = lock
        return lock

    async def touch(self, key_id: UUID, now: datetime) -> None:
        """Best-effort debounced write of `last_used_at` for `key_id`.

        Skips silently if less than 60 seconds have elapsed since the
        last successful write recorded for this key on this instance.
        Otherwise opens a transaction dedicated to this write — an
        orchestration boundary that owns its own session rather than
        the caller-supplied request session, per
        `docs/conventions.md` (Transaction and Locking) — commits on
        success and records the debounce timestamp only after that
        commit, or rolls back and leaves both the debounce timestamp
        and the (otherwise valid) authentication outcome unaffected on
        failure.
        """
        async with self._lock_for(key_id):
            last_write = self._last_write_at.get(key_id)
            if (
                last_write is not None
                and (now - last_write).total_seconds() < _DEBOUNCE_INTERVAL_SECONDS
            ):
                return

            async with get_last_used_session_factory()() as session:
                try:
                    await api_key_service.update_last_used_at(session, key_id, now)
                except Exception:
                    await session.rollback()
                    logger.warning(
                        "api_key_last_used_touch_failed",
                        key_id=str(key_id),
                        exc_info=True,
                    )
                    return
                await session.commit()

            self._last_write_at[key_id] = now


# Module-level singleton — one debounce cache per server process.
_last_used_debouncer = LastUsedDebouncer()


# ---------------------------------------------------------------------------
# Credential sub-flows
# ---------------------------------------------------------------------------


async def _authenticate_jwt(
    db: DatabaseSession, response: Response, token: str, now: datetime
) -> User:
    """JWT validation sub-flow — see `authentication.md`, JWT validation."""
    try:
        claims = decode_and_validate(
            token, secret_key=settings.jwt_secret_key.get_secret_value(), now=now
        )
    except InvalidTokenError:
        raise unauthenticated_error() from None

    if not await is_session_active(db, claims.session_id):
        raise unauthenticated_error()

    user = await db.get(User, claims.user_id)
    if user is None or not user.active:
        raise unauthenticated_error()

    # Sliding refresh occurs here — after the user-active check — so a
    # refreshed cookie is never emitted alongside a 401 for an inactive
    # user (authentication.md, Credential resolution step 6).
    refreshed = refresh_token(
        claims,
        now=now,
        jwt_expiry_hours=settings.jwt_expiry_hours,
        secret_key=settings.jwt_secret_key.get_secret_value(),
    )
    if refreshed is not None:
        try:
            set_session_cookie(response, refreshed.token)
        except Exception:
            # If the Set-Cookie header cannot be attached for any
            # reason, the old JWT remains valid and the request still
            # succeeds — the refresh is simply retried on the next
            # eligible request (authentication.md, Token refresh notes).
            logger.warning("session_cookie_refresh_failed", exc_info=True)

    return user


async def _authenticate_api_key(
    db: DatabaseSession, request: Request, token: str, now: datetime
) -> User:
    """API key validation sub-flow — see `authentication.md`, API key
    validation."""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    api_key = await api_key_service.get_key_by_hash(db, digest)
    if api_key is None:
        _record_unknown_key_attempt(request, now)
        raise unauthenticated_error()

    # Revoked and expired keys never authorize and never emit the
    # unknown-key warning (lookup miss only) — authentication.md, API
    # key validation, steps 3-4.
    if api_key.revoked_at is not None:
        raise unauthenticated_error()
    if api_key.expires_at is not None and api_key.expires_at <= now:
        raise unauthenticated_error()

    await _last_used_debouncer.touch(api_key.id, now)

    user = await db.get(User, api_key.user_id)
    if user is None or not user.active:
        raise unauthenticated_error()
    return user


# ---------------------------------------------------------------------------
# `get_current_user`
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request,
    response: Response,
    db: DatabaseSession,
) -> AuthenticatedPrincipal:
    """Resolve and validate the request's credential.

    See `docs/features/identity/authentication.md` (Middleware:
    `get_current_user`, Credential resolution) for the authoritative
    behavior: `Authorization: Bearer` precedence over the
    `sentinel_session` cookie, JWT-vs-API-key dispatch by the
    `stl_ak_` prefix, the generic 401 on any validation failure, and
    the JWT-only sliding refresh. Injected via `Depends()` into every
    endpoint that requires authentication.
    """
    credential = extract_credential(
        request.headers.get("authorization"),
        request.cookies.get(SESSION_COOKIE_NAME),
    )
    if credential is None:
        raise unauthenticated_error()

    now = datetime.now(UTC)
    if credential.startswith(API_KEY_PREFIX):
        user = await _authenticate_api_key(db, request, credential, now)
        credential_kind = CredentialKind.API_KEY
    else:
        user = await _authenticate_jwt(db, response, credential, now)
        credential_kind = CredentialKind.JWT

    return AuthenticatedPrincipal(user=user, credential_kind=credential_kind)


CurrentUser = Annotated[AuthenticatedPrincipal, Depends(get_current_user)]


# ---------------------------------------------------------------------------
# Authorization dependencies
# ---------------------------------------------------------------------------


def require_capability(
    capability: Capability,
) -> Callable[..., Awaitable[AuthenticatedPrincipal]]:
    """Dependency factory enforcing a single required capability.

    See `docs/features/identity/rbac.md` (`require_capability()`
    Dependency): loads the principal's current roles from the
    `UserRole` table on every request, unions their capabilities, and
    returns the unchanged principal when `capability` is included.
    Otherwise raises the generic 403 without disclosing the required or
    the caller's missing capability. Assumes the request has already
    passed `get_current_user` (which verifies `User.active`); this
    dependency does not re-verify active status.
    """

    async def _dependency(
        db: DatabaseSession, principal: CurrentUser
    ) -> AuthenticatedPrincipal:
        roles = await user_service.get_user_roles(db, principal.user.id)
        if capability not in get_capabilities(roles):
            raise AppError(
                status_code=status.HTTP_403_FORBIDDEN,
                code=ErrorCode.AUTH_INSUFFICIENT_PERMISSION,
                detail="Insufficient permissions",
            )
        return principal

    return _dependency


async def require_session_authentication(
    principal: CurrentUser,
) -> AuthenticatedPrincipal:
    """Reject API-key-authenticated requests; pass JWT sessions through.

    See `docs/features/identity/authentication.md` (Session-Only
    Authentication Dependency). Relies entirely on the credential kind
    already resolved by `get_current_user` — does not re-parse the
    request or duplicate the `stl_ak_` recognition rule.
    """
    if principal.credential_kind is CredentialKind.API_KEY:
        raise AppError(
            status_code=status.HTTP_403_FORBIDDEN,
            code=ErrorCode.AUTH_SESSION_REQUIRED,
            detail="API key creation requires session authentication.",
        )
    return principal
