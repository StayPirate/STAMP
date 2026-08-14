"""JWT issuance, validation, and sliding-refresh utilities.

See `docs/features/identity/authentication.md` (Token Format, JWT
validation contract, Token lifecycle, Token refresh) for the
authoritative contract this module implements. Pure Core-layer module:
no database or Redis access, no application imports — every function
takes an explicit `now` (where relevant) so callers can test every
temporal boundary deterministically, without wall-clock sleeps.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt.types import Options

ALGORITHM = "HS256"
ISSUER = "sentinel"

# The exact six claims a Sentinel JWT contains — no more, no less. See
# authentication.md (Claims): "Issued Sentinel JWTs contain exactly
# these six claims and no others."
REQUIRED_CLAIMS: frozenset[str] = frozenset(
    {"sub", "session_id", "iat", "exp", "session_deadline", "iss"}
)

# PyJWT decode options: signature/algorithm verification stays enabled
# (the only check PyJWT itself performs here) — `verify_signature` is
# listed explicitly alongside the other flags so this table remains the
# single, unambiguous source of truth for every verification toggle;
# every claim shape and temporal check below is applied manually so
# equality boundaries and "no clock-skew leeway" are under this
# module's exclusive control — see authentication.md (JWT validation
# contract).
_DECODE_OPTIONS: Options = {
    "verify_signature": True,
    "require": [],
    "verify_exp": False,
    "verify_iat": False,
    "verify_nbf": False,
    "verify_aud": False,
    "verify_iss": False,
}


class InvalidTokenError(Exception):
    """A Sentinel JWT failed one or more required validation checks.

    Deliberately generic: per the JWT validation contract, "malformed
    input or failure of any check produces one generic authentication
    failure; callers do not receive the failed condition." Callers
    (session_service, the logout endpoint) catch this single exception
    type and respond with the generic 401 body — they never inspect
    which specific check failed.
    """


@dataclass(frozen=True)
class JWTClaims:
    """The six required claims of a validated Sentinel JWT, decoded to
    native Python types (UUIDs, UTC datetimes)."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    issued_at: datetime
    expires_at: datetime
    session_deadline: datetime


@dataclass(frozen=True)
class IssuedToken:
    """A freshly encoded JWT and its `exp` claim as a UTC datetime.

    `token_expires_at` is distinct from `session_deadline` — see
    authentication.md (Session creation): "This is distinct from
    `Session.expires_at`, which represents the later immutable session
    deadline."
    """

    token: str
    token_expires_at: datetime


def _to_epoch(value: datetime) -> int:
    return int(value.timestamp())


def _epoch_to_datetime(value: int) -> datetime:
    return datetime.fromtimestamp(value, UTC)


def _compute_exp(
    issued_at: datetime, session_deadline: datetime, jwt_expiry_hours: int
) -> datetime:
    """`exp = min(issued_at + jwt_expiry_hours, session_deadline)`.

    Shared by `issue_token()` (initial login) and `refresh_token()`
    (sliding refresh) — see authentication.md, Token lifecycle and
    Token refresh step 3a.
    """
    return min(issued_at + timedelta(hours=jwt_expiry_hours), session_deadline)


def issue_token(
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    issued_at: datetime,
    session_deadline: datetime,
    jwt_expiry_hours: int,
    secret_key: str,
) -> IssuedToken:
    """Encode a signed HS256 JWT with exactly the six required claims.

    Q1: `user_id`/`session_id` identify the token; `issued_at` is the
    UTC `iat` snapshot (the login timestamp for initial issuance, or
    `now` for a refresh); `session_deadline` is the session's immutable
    maximum lifetime (mapped to the `session_deadline` claim);
    `jwt_expiry_hours` and `secret_key` come from the JWT configuration
    (`docs/configuration.md`).

    Q3: `exp` is capped at `session_deadline` per `_compute_exp()` —
    "capped at `session_deadline` so the advertised `expires_at` never
    exceeds the session's actual maximum lifetime." Always encodes a
    token, regardless of whether the computed `exp` is in the past —
    callers (`refresh_token()`) are responsible for deciding whether to
    call this function at all.

    Q6: propagates any exception `jwt.encode()` raises (malformed
    inputs at this layer are a programming error, not a runtime
    condition to recover from).
    """
    exp = _compute_exp(issued_at, session_deadline, jwt_expiry_hours)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "session_id": str(session_id),
        "iat": _to_epoch(issued_at),
        "exp": _to_epoch(exp),
        "session_deadline": _to_epoch(session_deadline),
        "iss": ISSUER,
    }
    token = jwt.encode(payload, secret_key, algorithm=ALGORITHM)
    return IssuedToken(token=token, token_expires_at=_epoch_to_datetime(_to_epoch(exp)))


def _decode_raw(token: str, secret_key: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token, secret_key, algorithms=[ALGORITHM], options=_DECODE_OPTIONS
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("invalid token") from exc
    # Defensive: PyJWT's own decoder already rejects a non-object JSON
    # payload with `DecodeError` (a `PyJWTError` subclass, caught above),
    # so `payload` is always a dict here. Kept as defense-in-depth against
    # a future PyJWT version relaxing that internal validation.
    if not isinstance(payload, dict):
        raise InvalidTokenError("invalid token")
    return payload


def _parse_uuid_claim(value: Any) -> uuid.UUID:
    if not isinstance(value, str):
        raise InvalidTokenError("claim must be a string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise InvalidTokenError("claim must be a canonical UUID string") from exc
    if str(parsed) != value:
        raise InvalidTokenError("claim must be a canonical UUID string")
    return parsed


def _parse_int_claim(value: Any) -> int:
    # bool is a subclass of int in Python — JSON booleans are explicitly
    # not accepted as integer timing claims (authentication.md, Claims).
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidTokenError("claim must be an integer")
    return value


def _parse_claims(
    payload: dict[str, Any],
) -> tuple[uuid.UUID, uuid.UUID, int, int, int]:
    if set(payload) != REQUIRED_CLAIMS:
        raise InvalidTokenError("unexpected claim set")
    if payload.get("iss") != ISSUER:
        raise InvalidTokenError("invalid issuer")
    user_id = _parse_uuid_claim(payload.get("sub"))
    session_id = _parse_uuid_claim(payload.get("session_id"))
    iat = _parse_int_claim(payload.get("iat"))
    exp = _parse_int_claim(payload.get("exp"))
    session_deadline = _parse_int_claim(payload.get("session_deadline"))
    return user_id, session_id, iat, exp, session_deadline


def decode_and_validate(token: str, *, secret_key: str, now: datetime) -> JWTClaims:
    """Apply the full normal JWT validation contract.

    Q1: `token` is the compact JWT string; `secret_key` is the unmasked
    `JWT_SECRET_KEY` value; `now` is one UTC timestamp snapshot used for
    every temporal check in this call.

    Q3: verifies, in order: HS256 signature: exactly the six required
    claims with the required types (UUID strings, integer timing
    claims, not booleans); `iss == "sentinel"`; and the temporal chain
    `iat <= now < exp <= session_deadline` (equality with `exp` or
    `session_deadline` is expired — no clock-skew leeway). Returns the
    decoded `JWTClaims` only when every check passes.

    Q6: raises `InvalidTokenError` — the single generic failure per the
    JWT validation contract — for any check failure (signature,
    algorithm, claim set/shape, issuer, or temporal ordering). No other
    exception escapes.
    """
    payload = _decode_raw(token, secret_key)
    user_id, session_id, iat, exp, deadline = _parse_claims(payload)
    now_epoch = _to_epoch(now)
    if not (iat <= now_epoch < exp <= deadline):
        raise InvalidTokenError("token failed temporal validation")
    return JWTClaims(
        user_id=user_id,
        session_id=session_id,
        issued_at=_epoch_to_datetime(iat),
        expires_at=_epoch_to_datetime(exp),
        session_deadline=_epoch_to_datetime(deadline),
    )


def decode_for_logout(token: str, *, secret_key: str) -> JWTClaims:
    """Apply the restricted logout decode contract.

    Q1: `token` is the compact JWT string; `secret_key` is the unmasked
    `JWT_SECRET_KEY` value.

    Q3: verifies the same signature, algorithm, exact claim set,
    issuer, claim types, and UUID parsing as `decode_and_validate()`,
    and requires `iat <= exp <= session_deadline` — but, intentionally,
    never compares `exp` or `session_deadline` against the current
    time. This lets a client log out with a temporally expired (but
    otherwise validly signed) token — see authentication.md, Logout,
    step 1.

    Q6: raises `InvalidTokenError` for any check failure. No other
    exception escapes.
    """
    payload = _decode_raw(token, secret_key)
    user_id, session_id, iat, exp, deadline = _parse_claims(payload)
    if not (iat <= exp <= deadline):
        raise InvalidTokenError("token failed temporal validation")
    return JWTClaims(
        user_id=user_id,
        session_id=session_id,
        issued_at=_epoch_to_datetime(iat),
        expires_at=_epoch_to_datetime(exp),
        session_deadline=_epoch_to_datetime(deadline),
    )


def refresh_token(
    claims: JWTClaims,
    *,
    now: datetime,
    jwt_expiry_hours: int,
    secret_key: str,
) -> IssuedToken | None:
    """Sliding-session refresh decision and issuance.

    Q1: `claims` is the already-validated `JWTClaims` of the current
    request's token; `now` is one UTC timestamp snapshot; the other
    parameters mirror `issue_token()`.

    Q3: implements authentication.md, Token refresh, steps 2-3
    verbatim:

    - if `claims.session_deadline <= now`: no refresh (`None`) — the
      session has exceeded its maximum lifetime;
    - if `token_age = now - claims.issued_at` is below 50% of
      `jwt_expiry_hours`: no refresh (`None`);
    - otherwise, compute the candidate `exp` via `_compute_exp()`
      (capped at `session_deadline`); if it would not be later than
      `now`, no refresh (`None`); otherwise issue and return a new
      token preserving `sub`, `session_id`, and `session_deadline`,
      with a new `iat = now`.

    This function performs no database write — the `Session` row is
    never touched by a refresh (authentication.md, Token refresh, "No
    database write is required for token refresh").

    Q6: propagates any exception from the underlying `issue_token()`
    call (JWT encoding failure). Otherwise infallible.
    """
    if claims.session_deadline <= now:
        return None
    threshold = timedelta(hours=jwt_expiry_hours) * 0.5
    if now - claims.issued_at < threshold:
        return None
    candidate_exp = _compute_exp(now, claims.session_deadline, jwt_expiry_hours)
    if candidate_exp <= now:
        return None
    return issue_token(
        user_id=claims.user_id,
        session_id=claims.session_id,
        issued_at=now,
        session_deadline=claims.session_deadline,
        jwt_expiry_hours=jwt_expiry_hours,
        secret_key=secret_key,
    )
