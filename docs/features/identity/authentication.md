# Authentication

## Purpose

Define the authentication framework for Sentinel: how users prove their
identity and how the system verifies that identity on every request.

Sentinel supports two authentication providers — SSO via `id.suse.com`
(see `docs/features/identity/sso-authentication.md`) and local credentials (see
`docs/features/identity/local-authentication.md`). Both providers produce the
same artifact: a signed JWT that the client presents on subsequent
requests. Programmatic clients may instead present API keys managed under
`docs/features/identity/api-key-management.md`.

This specification defines the shared infrastructure consumed by both
providers: token format, session management, credential validation,
middleware behavior, and session UI surfaces.

## Architecture

```
   SSO Provider                        Local Provider
   (OIDC flow)                     (POST /api/v1/auth/login)
           │                                    │
           └──────────┐            ┌────────────┘
                      ▼            ▼
              ┌──────────────────────────┐
              │   Session + JWT issued   │
              └────────────┬─────────────┘
                           │
                           ▼
              ┌──────────────────────────┐
              │   get_current_user()     │◄─── API Key (no session)
              │   middleware             │
              └──────────────────────────┘
```

The middleware accepts two credential types on every request:

- **JWT** (session-backed): validated by signature + session liveness
- **API key**: validated by hash lookup + revocation check

## Token Format (JWT)

Sentinel issues JSON Web Tokens signed with a symmetric key.

### Configuration

| Setting               | Type   | Default | Env var               |
|-----------------------|--------|---------|-----------------------|
| `jwt_secret_key`      | string | —       | `JWT_SECRET_KEY`      |
| `jwt_expiry_hours`    | int    | `72`    | `JWT_EXPIRY_HOURS`    |
| `session_max_lifetime_days` | int | `30` | `SESSION_MAX_LIFETIME_DAYS` |

`JWT_SECRET_KEY` is required. The application must refuse to start if it
is not set.

### Configuration bounds

- `JWT_EXPIRY_HOURS` must be >= 1. Values of 0 or negative cause the
  application to refuse to start with error:
  `"Invalid JWT_EXPIRY_HOURS: must be >= 1 (got: {value})"`
- `JWT_EXPIRY_HOURS` values above 720 (30 days) are accepted but log a
  WARNING at startup:
  `"JWT_EXPIRY_HOURS is set to {value} (>720 hours). Long-lived tokens
  increase the window of exposure if a token is compromised."`
- `JWT_SECRET_KEY` must be at least 32 characters. If the value is shorter,
  the application refuses to start with error:
  `"Invalid JWT_SECRET_KEY: must be at least 32 characters (got: {length})"`
- `SESSION_MAX_LIFETIME_DAYS` must be >= 1. Values of 0 or negative cause
  the application to refuse to start with error:
  `"Invalid SESSION_MAX_LIFETIME_DAYS: must be >= 1 (got: {value})"`
- `SESSION_MAX_LIFETIME_DAYS` values above 365 are accepted but log a
  WARNING at startup:
  `"SESSION_MAX_LIFETIME_DAYS is set to {value} (>365 days). Long-lived
  sessions increase exposure if a session is compromised."`

### Claims

| Claim              | Type   | Description                                   |
|--------------------|--------|-----------------------------------------------|
| `sub`              | string | User UUID (primary key of the `User` row)     |
| `session_id`       | string | UUID of the associated `Session` row          |
| `iat`              | int    | Issued-at timestamp (Unix epoch)              |
| `exp`              | int    | Expiration timestamp (Unix epoch)             |
| `session_deadline` | int    | Maximum session lifetime (Unix epoch). Set at login per `SESSION_MAX_LIFETIME_DAYS`, never refreshed |
| `iss`              | string | `"sentinel"` (constant)                       |

### Token lifecycle

- A token is issued at login with:
  - `session_deadline = now + SESSION_MAX_LIFETIME_DAYS * 86400` (default 30 days, never refreshed)
  - `exp = min(now + JWT_EXPIRY_HOURS * 3600, session_deadline)` — capped
    at `session_deadline` so the advertised `expires_at` never exceeds
    the session's actual maximum lifetime (same rule as token refresh)
- A change to `SESSION_MAX_LIFETIME_DAYS` applies only to sessions
  created by subsequent successful logins. Each session's
  `session_deadline` is calculated and persisted at login time and
  remains fixed for the lifetime of that session — it is never
  recomputed from the current setting.
- The server transparently refreshes the token via **sliding session**
  (see Token refresh below). Active users never experience session
  expiration.
- An inactive user whose token expires without renewal (no requests for
  longer than `JWT_EXPIRY_HOURS`) is redirected to the login page.
- After `SESSION_MAX_LIFETIME_DAYS` from login (`session_deadline`), the
  session expires unconditionally — the user must re-authenticate
  regardless of activity. This provides a hard cap on session lifetime.
- A token becomes invalid immediately if its associated session is
  deactivated (see Session Management below).

### Token refresh

The authentication middleware implements a sliding session mechanism
that transparently extends the token lifetime for active users:

1. After successful JWT validation and session liveness check, compute:
   `token_age = now - iat`
   `refresh_threshold = JWT_EXPIRY_HOURS * 3600 * 0.5`
2. If `session_deadline < now`: do not refresh. The session has exceeded
   its maximum lifetime — the current token remains valid until its `exp`
   but no new token is issued. (In practice, step 4 of JWT validation
   catches this, but the explicit guard here prevents issuing a token
   with `exp` in the past if reached via edge cases like clock skew.)
3. If `token_age >= refresh_threshold`:
   a. Verify `now + JWT_EXPIRY_HOURS * 3600` does not exceed
      `session_deadline`. If it does, set the new `exp` to
      `session_deadline` instead (final token before forced re-login)
   b. Generate a new JWT with the same `sub`, `session_id`, and
      `session_deadline`, but new `iat = now` and new `exp`
   c. Set the new JWT in the response `Set-Cookie` header (same cookie
      attributes: `HttpOnly`, `SameSite=Strict`, `Secure`, `Path=/api`)
4. If `token_age < refresh_threshold`: do nothing (normal request flow)

The refresh is completely server-side and transparent to the client. No
client-side logic or dedicated refresh endpoint is required.

**Notes**:

- The refresh threshold is a percentage (50%) of `JWT_EXPIRY_HOURS`, not
  an absolute value. If the expiry is changed, the threshold adjusts
  automatically
- If the `Set-Cookie` header cannot be set for any reason, the old JWT
  remains valid — the user experiences no error and the refresh is
  retried on the next eligible request
- If the database query for loading current roles fails during refresh,
  the refresh is silently skipped: the old JWT remains valid and a
  WARNING is logged. The next request will re-attempt the refresh
- No database write is required for token refresh (the Session record is
  not modified)
- When multiple requests arrive simultaneously after the refresh
  threshold, each independently issues a new JWT. This is intentionally
  accepted: all resulting tokens reference the same valid session, no
  database write is involved, and the browser naturally adopts the last
  `Set-Cookie` received. No serialization or deduplication mechanism is
  required

## Session Management

Every login (SSO or local) creates a **Session** record. The JWT
references this session via the `session_id` claim. On every
authenticated request, the middleware verifies that the session is still
active. This allows immediate invalidation on logout or user
deactivation, without waiting for JWT expiry.

### Data model: `Session`

| Column             | Type         | Nullable | Description                         |
|--------------------|--------------|----------|-------------------------------------|
| `id`               | UUID         | No       | Primary key                         |
| `user_id`          | UUID (FK)    | No       | References `User.id`                |
| `created_at`       | timestamptz  | No       | When the session was created        |
| `updated_at`       | timestamptz  | No       | Last modification timestamp         |
| `expires_at`       | timestamptz  | No       | Immutable maximum lifetime, calculated at login as `now() + SESSION_MAX_LIFETIME_DAYS * 86400`. Maps to the JWT `session_deadline` claim. |
| `is_active`        | boolean      | No       | `false` after logout or revocation  |

### Session liveness check

On every authenticated request, the middleware checks:

```
session.is_active = true
```

If the session is inactive, the request is rejected with HTTP 401.
The `session_deadline` claim in the JWT provides the maximum lifetime
check (verified during JWT validation, before the liveness check).

To avoid a database round-trip on every request, the session liveness
result is cached in Redis with a TTL of 60 seconds. This means that
after logout or deactivation, there is a window of up to 60 seconds
before the token becomes effectively unusable (in practice, the explicit
cache purge on logout/deactivation makes this near-instantaneous — the
TTL is only a safety net for edge cases). This tradeoff is acceptable
for an internal tool.

**Cache value contract**: the Redis key `session_liveness:{session_id}` stores
the string `"1"` to represent an active session. Inactive sessions are never
cached. The lookup semantics are:

- **Cache hit** (key exists with value `"1"`): the session is active — no
  database query is needed. Proceed to user loading.
- **Cache miss** (key does not exist): query the database for the `Session`
  record and check `is_active`:
  - If `is_active = true`: write `"1"` to Redis with key
    `session_liveness:{session_id}` and TTL 60 seconds, then proceed.
  - If `is_active = false`: do NOT write to cache, reject the request
    (HTTP 401).
  - If no `Session` row exists (deleted by `cleanup_sessions`): do NOT
    write to cache, reject the request (HTTP 401).

This ensures that only positive (active) state is ever cached, a cache miss
always triggers a database verification, and a revoked session never pollutes
the cache.

**Redis unavailability**: if Redis is unreachable (any `RedisError` —
including connection failures and OOM rejections), the session liveness
check falls back to a direct database query. This is functionally
correct but increases database load (one extra query per authenticated
request). The Redis connection failure is logged as a WARNING on first
occurrence (not per-request, to avoid log flooding). Normal caching
resumes automatically when Redis becomes available again.

### Session invalidation

Session invalidation is handled by `session_service`
(`backend/app/services/session_service.py`), which provides two methods.
Each method separates database mutations (transactional) from Redis
cache cleanup (post-commit, best-effort) per `docs/conventions.md`
(Transaction Hygiene Rules).

Every database method accepts a caller-supplied `AsyncSession`, flushes when
required, and never commits or rolls back. The API transaction dependency or
complete CLI/task workflow owns transaction completion. Redis-only
`purge_session_cache()` has no database transaction.

#### `invalidate_session(db, session_id) -> UUID`

Invalidates a single session (used by the logout endpoint).

**Database phase** (executes within the caller's transaction):

1. Set `Session.is_active = false` and `Session.updated_at = now()` for
   the given `session_id`. If no row exists for the `session_id`
   (already deleted by `cleanup_sessions`), this is a no-op — no
   exception is raised.
2. Return the `session_id` (for post-commit cache purge)

**Post-commit phase** (best-effort, caller executes after commit via
`purge_session_cache([session_id])`):

3. Delete the Redis cache entry `session_liveness:{session_id}`
4. If Redis is unreachable, log WARNING and proceed — the entry expires
   naturally within the cache TTL (60 seconds)

#### `invalidate_user_sessions(db, user_id) -> list[UUID]`

Invalidates all active sessions for a user (used by deactivation and
password reset).

**Database phase** (executes within the caller's transaction):

1. `UPDATE session SET is_active = false, updated_at = now() WHERE
   user_id = :user_id AND is_active = true` — collect the list of
   invalidated `session_id`s
2. Return the list of invalidated `session_id`s (for post-commit cache
   purge)

**Post-commit phase** (best-effort, caller executes after commit via
`purge_session_cache(session_ids)`):

3. For each invalidated session, delete the Redis cache entry
   `session_liveness:{session_id}`
4. If Redis is unreachable, log WARNING and proceed — entries expire
   naturally within the cache TTL (60 seconds)

**Caller contract**: the caller is responsible for executing the
post-commit phase after its transaction commits. If the post-commit
phase is omitted (e.g., due to process crash between commit and cache
purge), the cache entries self-heal via TTL expiry. The database is
always the authoritative source for session validity — Redis is a
performance optimization.

**Database-phase callers**:

| Caller | Context |
|--------|---------|
| Logout endpoint (`POST /api/v1/auth/logout`) | Calls `invalidate_session()` for the current session |
| `user_service.deactivate_user()` | Calls `invalidate_user_sessions()` as part of deactivation side effects |
| `user_service.reset_password()` | Calls `invalidate_user_sessions()` after updating `password_hash` |

#### `purge_session_cache(session_ids: list[UUID]) -> None`

Executes the post-commit cache purge for previously invalidated
sessions. This is the named helper that callers invoke after their
transaction commits — it encapsulates the Redis key format and error
handling so that callers do not restate them.

1. For each `session_id` in the list, delete
   `session_liveness:{session_id}`
2. If Redis is unreachable (`RedisError`), log WARNING and proceed —
   entries expire naturally within the cache TTL (60 seconds)

This function has no database dependency; it operates exclusively on
Redis. It is safe to call multiple times with the same input
(idempotent — deleting a non-existent key is a no-op).

### Concurrent sessions

Multiple concurrent sessions per user are allowed by design. A new login
(SSO or local) creates a new `Session` record without invalidating
existing ones. This supports legitimate multi-device usage (e.g., desktop
and laptop).

Sessions are only invalidated explicitly by:
- Logout (current session only)
- User deactivation (all sessions)
- Password reset (all sessions)

Orphaned sessions (e.g., from a browser where the user never explicitly
logged out) are cleaned up by the weekly session cleanup task. All
sessions expire unconditionally when their `session_deadline` is reached,
regardless of activity.

### Deactivation ordering

When a user is deactivated (via `user_service.deactivate_user()`), the
database-phase operations execute atomically in this order:

1. Revoke all API keys for the user via
   `api_key_service.revoke_all_user_keys(session, user_id,
   acting_user_id=acting_user_id)`. Authenticated API deactivation preserves
   the admin actor; CLI and external-sync workflows pass NULL. See
   `docs/features/identity/api-key-service.md`
2. Invalidate all active sessions via
   `session_service.invalidate_user_sessions()` (DB only — cache purge
   is post-commit; see Session invalidation above)
3. Mark the user as inactive

After the transaction commits, the post-commit phase purges the session
liveness cache entries (best-effort). See
`docs/features/identity/user-service.md` (`deactivate_user()`) for the
full two-phase specification.

### Session cleanup

A Celery Beat task (`cleanup_sessions`) runs every **Sunday at 03:00 UTC**
(fixed schedule, not configurable) and deletes session rows matching either
of these conditions:

- `is_active = false` — invalidated sessions. A request in flight that
  encounters a missing row receives HTTP 401 (see Session liveness
  check, cache-miss branch), which is the correct outcome for an
  already-invalidated session.
- `expires_at < now()` — sessions whose immutable deadline has
  passed, regardless of active status. Because the `Session.expires_at`
  column and the JWT `session_deadline` claim originate from the same
  login operation, no clock-skew buffer is needed.

No session history is retained — invalidated and expired sessions are
deleted without trace.

This is a maintenance task, not a `BaseFetcher` subclass (it does not
fetch data from external sources). It is registered as a static
`beat_schedule` entry; the Beat registration mechanism is fully
specified in `docs/features/platform/fetcher-infrastructure.md`
("Non-Fetcher Periodic Tasks").

### Session operational logging

Session lifecycle events are logged at **INFO** level for operational
visibility. Log messages follow the PII discipline in
`docs/features/platform/logging.md` — they use `user_id` (UUID) as a
pseudonymous correlation identifier, never usernames or session IDs:

- Session created: structured event with `user_id` and login reason
- Session invalidated (logout): structured event with `user_id` and
  reason
- Sessions invalidated (bulk): structured event with `user_id`, count,
  and reason (deactivation or password reset)

These are **operational diagnostic logs**, not a persistent audit trail.
Session lifecycle events do not produce `IdentityAuditEvent` records —
sessions are excluded from the identity audit trail scope (see
`docs/features/identity/identity-audit-log.md`). The `last_login_at`
field on the `User` table provides the queryable answer to "when did
this user last log in?" without depending on log retention.

### `last_login_at` field

The `User` table includes a `last_login_at` field (timestamptz,
nullable) that is updated to `now()` every time a session is created
(both SSO and local login). This provides a queryable answer to "when
did user X last log in?" without depending on session row retention or
log searches.

`last_login_at` is operational authentication metadata, not a lifecycle audit
field. Session creation and the matching `last_login_at` update use the same
caller-owned database transaction and commit together; failure rolls both
back. Neither write creates an `IdentityAuditEvent`. Any Redis cleanup or cache
population remains outside that transaction.

### Frontend session behavior

This section defines the frontend behavior shared by both login providers
(SSO and local). Provider-specific frontend flows are documented in their
respective specs.

#### Post-login redirect

After a successful login (regardless of provider):

1. The backend sets the session cookie (`sentinel_session`, HttpOnly) —
   the frontend does not handle the token directly
2. The frontend checks `sessionStorage` for `sentinel_return_url`:
   - If present: redirect to that URL and remove the key
   - If absent: redirect to the dashboard
3. Each provider is responsible for saving `sentinel_return_url` to
   `sessionStorage` before initiating the login flow (to preserve the
   user's intended destination across redirects)

#### Session expiration handling

When any API call returns HTTP 401 and the user previously had an active
session (the browser was sending the `sentinel_session` cookie):

1. Redirect to the login page
2. Display an informational message: "Your session has expired. Please
   log in again."

This applies regardless of the expiration cause (token expired, session
deadline reached, session invalidated by admin).

## Middleware: `get_current_user`

The FastAPI dependency `get_current_user` extracts and validates
credentials from the incoming request. It is injected via `Depends()`
into all endpoints that require authentication.

### Credential resolution

1. Check the `Authorization` header:
   - If present with scheme `Bearer`: extract the token value. If the
     extracted value is empty or whitespace-only, treat as header absent
     and proceed to step 2. Otherwise, go to step 3.
2. If the `Authorization` header is absent, check for the session cookie
   (`sentinel_session`):
   - If the cookie is present: extract its value as the token and go to
     step 3.
   - If neither the header nor the cookie is present: return HTTP 401.
3. Determine credential type:
   - If the token starts with `stl_ak_`: treat as **API key**
   - Otherwise: treat as **JWT**
4. Validate according to the credential type (see below). Any validation
   failure in a credential sub-flow results in HTTP 401 with the standard
   generic body (described below). Only the success path is described in
   the sub-flows.
5. Load the `User` record by the `user_id` returned from the credential
   sub-flow. If the user is inactive (`active = false`), return HTTP 401.
6. Return the `User` model instance (the record loaded in step 5).

All HTTP 401 responses return a generic body `{"code":
"AUTH_NOT_AUTHENTICATED", "detail": "Authentication required"}` regardless
of the specific failure reason (expired token, invalidated session, session
deadline reached, inactive user). No information about the failure cause is
disclosed. See also `docs/api-spec.md`, "Global Responses".

The dual-source approach supports both programmatic clients (which send
`Authorization: Bearer <token>`) and browser sessions (where the JWT is
stored in an `HttpOnly` cookie attached automatically by the browser).

### JWT validation

1. Decode the token using `JWT_SECRET_KEY` with the `HS256` algorithm.
2. Verify `exp` has not passed.
3. Verify `iss` equals `"sentinel"`.
4. Verify `session_deadline` has not passed.
5. Look up the session by `session_id` claim.
6. Verify the session passes the liveness check: the `Session` row must
   exist **and** have `is_active = true`. A missing row or
   `is_active = false` rejects the request (HTTP 401). Use Redis cache
   when available (see Session liveness check).
7. On success, return the `user_id` from the `sub` claim.

### API key validation

1. Compute `SHA-256(presented_key)` and encode the result as a lowercase
   hex digest.
2. Call `api_key_service.get_key_by_hash()` with the computed digest. If no
   record is found, emit the rate-limited WARNING described below and fail.
   The authentication boundary performs no direct `ApiKey` query. The log
   MUST NOT include the key prefix, key name, presented key, computed hash,
   username, or other credential material.

   **PII exception**: the source IP is included in this log message as a
   documented exception to the PII discipline in
   `docs/features/platform/logging.md`. The IP identifies an attack
   source (not a legitimate authenticated user), the log serves an
   active defense purpose (brute-force detection and response), and
   `request_id` correlation alone is insufficient because the rate
   limiter aggregates hundreds of requests into a single log message.

   **Log rate limiting**: the WARNING emission is rate-limited to prevent
   log flooding from brute-force attacks. The HTTP 401 response is
   ALWAYS returned regardless of rate limiting — only the log emission
   is suppressed. Details:

   - **Granularity**: per source IP. An attacker trying 1000 different
     keys from the same IP generates a single WARNING per period.
   - **Rate**: at most 1 WARNING every 60 seconds per source IP per
     server instance.
   - **Aggregated log record**: `event="api_key_validation_failed"`,
     `source_ip=<full source IP>`, and `suppressed_count=<integer>`.
     `suppressed_count` is zero for an unsuppressed first failure and the
     number of additional failures suppressed in the previous 60 seconds
     for the next emitted record. No value derived from the presented
     credential is emitted.
   - **Storage**: per-instance in-memory dictionary (no Redis). Same
     philosophy as the `last_used_at` debounce — no coordination
     between instances is needed.
   - **Eviction**: entries expire after 5 minutes of inactivity (no
     failed attempt from that IP). Maximum 10,000 entries; if the limit
     is reached, the least recently used entry is evicted (LRU). This
     prevents memory growth from IP spoofing or large botnets.
   - **Multi-instance**: with N instances, the worst case is N WARNINGs
     per minute per IP (one per instance) — acceptable for an internal
     tool.
3. Verify `revoked_at` is `NULL`.
4. If `expires_at` is set, reject when `expires_at <= now`.
5. Update `last_used_at` to the current timestamp through
   `api_key_service.update_last_used_at()` (debounced: update at
   most once per minute **per server instance** to reduce write
   pressure). The debounce uses a per-instance in-memory cache of
   `key_id → last_write_timestamp`. If less than 60 seconds have elapsed
   since the last DB write for this key on this instance, the update is
   skipped. With N API server instances, the worst case is N writes per
   minute per key — acceptable for an internal tool. The authentication
   boundary owns a transaction dedicated to this best-effort operational
   write: commit before recording the debounce timestamp; on failure, roll
   back, leave the debounce timestamp unchanged, and continue authentication
   with the otherwise valid credential. This metadata creates no
   `IdentityAuditEvent`; see `docs/features/identity/identity-audit-log.md`.
6. On success, return the `user_id` from the `ApiKey` record.

API keys do **not** use sessions. They are validated through
`api_key_service` against the `ApiKey` table on every request.

## API Key Management Boundary

API key lifecycle, status, retention, REST endpoints, CLI commands, and
consumer use cases are defined in
`docs/features/identity/api-key-management.md`. This specification owns
only use of an API key as an authentication credential.

## API Endpoints

### Get Current User

Returns the currently authenticated user's profile.

**`Access: Authenticated`**

**Response** (200):

```json
{
  "data": {
    "id": "uuid",
    "username": "string",
    "email": "string",
    "full_name": "string | null",
    "roles": ["string"],
    "active": true
  }
}
```

### Logout

Invalidates the current session.

Global responses per `api-spec.md` do not apply (custom authentication handling — see below).

**Authentication**: this endpoint does NOT use the standard
`get_current_user` middleware (which would reject requests with an
already-invalidated session). Instead, it uses a lightweight dependency
that only verifies the JWT signature and extracts claims — it does not
check session liveness. This makes the endpoint fully idempotent:
calling it multiple times (e.g., retry or double-click) always succeeds.

If the token is not a valid JWT (invalid signature, malformed), return
HTTP 401 with code `AUTH_NOT_AUTHENTICATED` and body
`{"code": "AUTH_NOT_AUTHENTICATED", "detail": "Authentication required"}`.
This endpoint does not use `get_current_user` and therefore handles
authentication failure directly (same response format for client
consistency).

If called with an API key instead of a JWT, return HTTP 400 with
code `AUTH_LOGOUT_NOT_APPLICABLE` and message:
`"Logout is not applicable to API key authentication."`

**Behavior**:

1. Verify the JWT signature (reject if signature is invalid or token is
   malformed). The `exp` claim is NOT checked — a token with a valid
   signature but past expiration is accepted. This allows users to
   explicitly log out even after their token has expired (e.g., returning
   to a stale tab after 73+ hours). The security impact is negligible:
   the token is bound to a specific `session_id`, so an attacker with a
   stolen expired token can only invalidate that one session (a single
   forced re-login)
2. Extract `session_id` from the JWT claims
3. Call `session_service.invalidate_session(db, session_id)` — this is
   idempotent: if the session is already inactive, no change is made
4. After the transaction commits, execute the post-commit phase:
   `session_service.purge_session_cache([session_id])` (best-effort
   cache purge — see Session invalidation above)
5. Set a `Set-Cookie` header that clears the `sentinel_session` cookie:
   `Set-Cookie: sentinel_session=; Path=/api; Max-Age=0; HttpOnly; Secure; SameSite=Strict`
6. Return HTTP 204

## Security Considerations

- **JWT_SECRET_KEY** must be a cryptographically random string of at
  least 32 characters. It must never be committed to the repository or
  logged.
- **API key hashing uses plain SHA-256**, not a slow hash like bcrypt
  and not a keyed HMAC. API keys have ~190 bits of entropy (32
  alphanumeric characters generated server-side by a CSPRNG) and are
  not vulnerable to offline brute-force — the search space is
  computationally infeasible regardless of hash speed. A plain hash
  avoids the operational burden of a server-side secret: there is no
  key to rotate and no risk of permanently invalidating all API keys
  through a configuration change. Using a slow hash for high-entropy
  tokens would create unnecessary CPU pressure — at 100
  requests/second with bcrypt (cost 12, ~300ms/op), the server would
  need 30 CPU-seconds per second solely for key validation, creating a
  denial-of-service vector.
- **API key lifecycle security** (secret visibility, expiration, and
  revocation) is defined in `api-key-management.md`.
- **Session liveness check** ensures that logout and deactivation take
  effect within the cache TTL window (60 seconds maximum).
- **No single logout (SLO)**: logging out of `id.suse.com` does not
  invalidate the Sentinel session. This is a known limitation,
  acceptable for an internal tool. Users can log out of Sentinel
  explicitly.
- **Token in browser storage**: the JWT is stored in an `HttpOnly`
  cookie named `sentinel_session` with `SameSite=Strict`, `Secure`
  (HTTPS only), and `Path=/api`. This makes the token immune to XSS
  attacks (JavaScript cannot access HttpOnly cookies). The frontend
  does not handle the token directly — the browser attaches it
  automatically to every request to the same origin. `SameSite=Strict`
  prevents the cookie from being sent on cross-origin requests,
  eliminating the need for a separate CSRF token mechanism.
- **No concurrent session limit**: there is no enforced maximum number
  of active sessions per user. Users may have sessions on multiple
  devices simultaneously. This is a deliberate choice for an internal
  tool — a limit would create friction without meaningful security gain.
  If a user's sessions need to be terminated, an admin can deactivate
  the user (which invalidates all sessions).
- **Key rotation**: rotating `JWT_SECRET_KEY` immediately invalidates
  all existing JWTs (the signature verification will fail). This
  effectively triggers a mass logout — all users must re-authenticate.
  Additionally, any in-flight SSO flows (state parameter signed with the
  old key) will fail at callback — max 10 minutes of disruption (see
  `docs/features/identity/sso-authentication.md`, Operational note).
  Operators should plan key rotation during low-traffic windows and
  communicate the expected impact. There is no graceful dual-key
  transition mechanism; this simplicity is acceptable for an internal
  tool where mass re-login is a minor inconvenience.
- **API key last_used_at debouncing**: updating `last_used_at` on every
  request would create write amplification. Updates are debounced to at
  most once per minute per key.
- **No client fingerprint binding on session cookies**: the session cookie
  is not bound to any client fingerprint (IP address, User-Agent, or TLS
  channel binding). A stolen cookie can be replayed from any network
  location without server-side detection. This is an **accepted risk** for
  the following reasons:
  - Binding to IP would break usability for users on VPNs, roaming
    networks, or dynamic IPs — common in an internal enterprise
    environment where employees switch between office, home, and travel
    networks
  - Binding to User-Agent provides negligible security (trivially spoofed)
  - TLS channel binding (RFC 5929) is not widely supported by browsers
  - The tool operates on a trusted internal network, reducing the attack
    surface for cookie theft
  - Existing compensating controls: `Secure` flag (HTTPS only), `HttpOnly`
    (no XSS access), `SameSite=Strict` (no cross-origin leakage),
    maximum session lifetime (`SESSION_MAX_LIFETIME_DAYS`), admin
    deactivation (invalidates all sessions immediately)
  - Detection of session theft is not a goal for this tool; prevention
    via the cookie flags above is the primary defense
- **OIDC state parameter not single-use (accepted risk)**: the OIDC
  `state` parameter is HMAC-signed with a timestamp for CSRF protection,
  but it is not stored server-side or tracked as consumed. Replay
  protection relies on the IdP's single-use authorization code. If the
  IdP has a code-replay vulnerability, Sentinel has no independent
  defense. This is accepted given enterprise IdP reliability (id.suse.com
  / Azure AD) and the operational cost of maintaining a consumed-states
  cache for a low-probability attack vector.

## Cross-references

- `docs/api-spec.md` — API conventions, global responses, scoped responses
- `docs/features/identity/api-key-management.md` — API key lifecycle,
  endpoints, CLI commands, status, and retention
- `docs/features/identity/api-key-service.md` — centralized API key
  persistence service, including the `last_used_at` operational touch
- `docs/features/identity/local-authentication.md` — local login endpoint and
  password management
- `docs/features/identity/sso-authentication.md` — SSO login flow with
  id.suse.com
- `docs/features/identity/user-service.md` — deactivation side effects
  (API key revocation, session invalidation)
- `docs/features/identity/user-management.md` — creating local user
  accounts (including for AI agents)
- `docs/features/identity/rbac.md` — role-based access control and permission
  model
