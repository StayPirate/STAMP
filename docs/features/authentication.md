# Authentication

## Purpose

Define the authentication framework for Sentinel: how users prove their
identity, how the system verifies that identity on every request, and how
programmatic clients (bots, AI agents, CI scripts) obtain durable
credentials without interactive login flows.

Sentinel supports two authentication providers — SSO via `id.suse.com`
(see `docs/features/sso-authentication.md`) and local credentials (see
`docs/features/local-authentication.md`). Both providers produce the
same artifact: a signed JWT that the client presents on subsequent
requests. Additionally, any authenticated user can create **API keys**
for non-interactive access.

This specification defines the shared infrastructure consumed by both
providers: token format, session management, API key lifecycle,
middleware behavior, and UI surfaces.

## Architecture

```
┌───────────────────────────────────────────────────────────────────┐
│                        Login Page                                  │
│                                                                   │
│   [ Login with SUSE SSO ]          Username: [________]           │
│                                    Password: [________]           │
│                                    [ Login ]                      │
└──────────┬────────────────────────────────────┬───────────────────┘
           │                                    │
           ▼                                    ▼
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

### Claims

| Claim              | Type   | Description                                   |
|--------------------|--------|-----------------------------------------------|
| `sub`              | string | User UUID (primary key of the `User` row)     |
| `session_id`       | string | UUID of the associated `Session` row          |
| `roles`            | array  | List of role names (e.g. `["admin"]`)         |
| `iat`              | int    | Issued-at timestamp (Unix epoch)              |
| `exp`              | int    | Expiration timestamp (Unix epoch)             |
| `session_deadline` | int    | Maximum session lifetime (Unix epoch). Set at login, never refreshed |
| `iss`              | string | `"sentinel"` (constant)                       |

### Token lifecycle

- A token is issued at login with:
  - `exp = now + JWT_EXPIRY_HOURS * 3600` (default 72 hours)
  - `session_deadline = now + 30 days` (hardcoded, never refreshed)
- The server transparently refreshes the token via **sliding session**
  (see Token refresh below). Active users never experience session
  expiration.
- An inactive user whose token expires without renewal (no requests for
  longer than `JWT_EXPIRY_HOURS`) is redirected to the login page.
- After 30 days from login (`session_deadline`), the session expires
  unconditionally — the user must re-authenticate regardless of
  activity. This provides a hard cap on session lifetime.
- A token becomes invalid immediately if its associated session is
  deactivated (see Session Management below).

### Token refresh

The authentication middleware implements a sliding session mechanism
that transparently extends the token lifetime for active users:

1. After successful JWT validation and session liveness check, compute:
   `token_age = now - iat`
   `refresh_threshold = JWT_EXPIRY_HOURS * 3600 * 0.5`
2. If `token_age >= refresh_threshold`:
   a. Verify `now + JWT_EXPIRY_HOURS * 3600` does not exceed
      `session_deadline`. If it does, set the new `exp` to
      `session_deadline` instead (final token before forced re-login)
   b. Generate a new JWT with the same `sub`, `session_id`, and
      `session_deadline`, but new `iat = now`, new `exp`, and current
      `roles` (loaded fresh from DB)
   c. Set the new JWT in the response `Set-Cookie` header (same cookie
      attributes: `HttpOnly`, `SameSite=Strict`, `Secure`, `Path=/api`)
3. If `token_age < refresh_threshold`: do nothing (normal request flow)

The refresh is completely server-side and transparent to the client. No
client-side logic or dedicated refresh endpoint is required.

**Notes**:

- The refresh threshold is a percentage (50%) of `JWT_EXPIRY_HOURS`, not
  an absolute value. If the expiry is changed, the threshold adjusts
  automatically
- The `roles` claim in the refreshed JWT reflects the user's current
  roles at refresh time
- If the `Set-Cookie` header cannot be set for any reason, the old JWT
  remains valid — the user experiences no error and the refresh is
  retried on the next eligible request
- No database write is required for token refresh (the Session record is
  not modified)

## Session Management

Every login (SSO or local) creates a **Session** record. The JWT
references this session via the `session_id` claim. On every
authenticated request, the middleware verifies that the session is still
active. This allows immediate invalidation on logout or user
deactivation, without waiting for JWT expiry.

### Data model: `Session`

| Column       | Type         | Nullable | Description                         |
|--------------|--------------|----------|-------------------------------------|
| `id`         | UUID         | No       | Primary key                         |
| `user_id`    | UUID (FK)    | No       | References `User.id`                |
| `created_at` | timestamptz  | No       | When the session was created        |
| `is_active`  | boolean      | No       | `false` after logout or revocation  |

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

**Redis unavailability**: if Redis is unreachable, the session liveness
check falls back to a direct database query. This is functionally
correct but increases database load (one extra query per authenticated
request). The Redis connection failure is logged as a WARNING on first
occurrence (not per-request, to avoid log flooding). Normal caching
resumes automatically when Redis becomes available again.

### Session invalidation

Session invalidation is handled by `session_service`
(`backend/app/services/session_service.py`), which provides two methods:

#### `invalidate_session(db, session_id)`

Invalidates a single session (used by the logout endpoint).

1. Set `Session.is_active = false` for the given `session_id`
2. Delete the Redis cache entry `session_liveness:{session_id}`
3. If Redis is unreachable, proceed — the entry expires naturally
   within the cache TTL

#### `invalidate_user_sessions(db, user_id) -> int`

Invalidates all active sessions for a user (used by deactivation and
password reset).

1. `UPDATE session SET is_active = false WHERE user_id = :user_id AND
   is_active = true` — collect the list of invalidated `session_id`s
2. For each invalidated session, delete the Redis cache entry
   `session_liveness:{session_id}`
3. If Redis is unreachable, log WARNING and proceed — entries expire
   naturally within the cache TTL (60 seconds)
4. Return the number of sessions invalidated

**Callers**:

| Caller | Context |
|--------|---------|
| Logout endpoint (`POST /api/v1/auth/logout`) | Calls `invalidate_session()` for the current session |
| `user_service.deactivate_user()` | Calls `invalidate_user_sessions()` as step 2 of deactivation |
| `user_service.reset_password()` | Calls `invalidate_user_sessions()` after updating `password_hash` |

### Deactivation ordering

When a user is deactivated (via `user_service.deactivate_user()`), the
operations execute in this order:

1. Revoke all API keys for the user
2. Invalidate all active sessions via
   `session_service.invalidate_user_sessions()` (DB + Redis)
3. Mark the user as inactive

This ordering ensures that if the process is interrupted at any point,
the user may still appear active but will have already lost access. The
admin can retry the deactivation without risk of leaving a deactivated
user with valid credentials.

### Session cleanup

A Celery Beat task runs **once per week** and deletes all session rows
where `is_active = false` or `created_at < now() - 30 days` (sessions
that have exceeded the maximum lifetime). No session history is
retained — invalidated and expired sessions are deleted without trace.

This is a maintenance task, not a `BaseFetcher` subclass (it does not
fetch data from external sources).

### Session audit logging

To compensate for session cleanup (which deletes historical records),
session lifecycle events are logged at **INFO** level:

- Session created: `"Session created for user {username} (session_id={id})"`
- Session invalidated (logout): `"Session invalidated for user {username} (session_id={id}, reason=logout)"`
- Sessions invalidated (bulk): `"Invalidated {count} sessions for user {username} (reason={deactivation|password_reset})"`

These log entries provide a permanent audit trail (retained per log
infrastructure policy) even after session rows are cleaned up.

### `last_login_at` field

The `User` table includes a `last_login_at` field (timestamptz,
nullable) that is updated to `now()` every time a session is created
(both SSO and local login). This provides a queryable answer to "when
did user X last log in?" without depending on session row retention or
log searches.

## Middleware: `get_current_user`

The FastAPI dependency `get_current_user` extracts and validates
credentials from the incoming request. It is injected via `Depends()`
into all endpoints that require authentication.

### Credential resolution

1. Check the `Authorization` header:
   - If present with scheme `Bearer`: extract the token value and go to
     step 3.
2. If the `Authorization` header is absent, check for the session cookie
   (`sentinel_session`):
   - If the cookie is present: extract its value as the token and go to
     step 3.
   - If neither the header nor the cookie is present: return HTTP 401.
3. Determine credential type:
   - If the token starts with `stl_ak_`: treat as **API key**
   - Otherwise: treat as **JWT**
4. Validate according to the credential type (see below).
5. Load the `User` record. If the user is inactive (`active = false`),
   return HTTP 401.
6. Return the authenticated user.

The dual-source approach supports both programmatic clients (which send
`Authorization: Bearer <token>`) and browser sessions (where the JWT is
stored in an `HttpOnly` cookie attached automatically by the browser).

### JWT validation

1. Decode the token using `JWT_SECRET_KEY` with the `HS256` algorithm.
2. Verify `exp` has not passed.
3. Verify `iss` equals `"sentinel"`.
4. Look up the session by `session_id` claim.
5. Verify the session passes the liveness check (active + not expired).
   Use Redis cache when available.
6. Load the user by `sub` claim.
7. Load the user's **current roles from the database** — the `roles`
   claim in the JWT is not used for authorization decisions. It exists
   for informational purposes only (e.g., frontend UI can use it for
   optimistic rendering before fetching fresh data). This ensures that
   role changes take effect immediately, without waiting for re-login.

### API key validation

1. Compute `SHA-256(presented_key)` and encode the result as a lowercase
   hex digest.
2. Look up the `ApiKey` record by matching `key_hash` to the computed
   digest. If no record is found, log a WARNING with the key prefix
   (first 12 characters) and the source IP, then return HTTP 401.
3. Verify `revoked_at` is `NULL`.
4. If `expires_at` is set, verify it has not passed.
5. Update `last_used_at` to the current timestamp (debounced: update at
   most once per minute to reduce write pressure). The debounce uses a
   per-instance in-memory cache of `key_id → last_write_timestamp`. If
   less than 60 seconds have elapsed since the last DB write for this
   key on this instance, the update is skipped. With N API server
   instances, the worst case is N writes per minute per key — acceptable
   for an internal tool.
6. Load the user by `user_id` from the `ApiKey` record.

API keys do **not** use sessions. They are validated directly against the
`ApiKey` table on every request.

## API Keys

API keys provide durable, non-interactive credentials for programmatic
access to the Sentinel API. They are suitable for bots, AI agents, CI
pipelines, and any client that cannot perform an interactive login flow.

### Data model: `ApiKey`

| Column        | Type         | Nullable | Description                              |
|---------------|--------------|----------|------------------------------------------|
| `id`          | UUID         | No       | Primary key                              |
| `user_id`     | UUID (FK)    | No       | References `User.id` (owner)             |
| `key_hash`    | string(64)   | No       | SHA-256 hex digest of the full key       |
| `prefix`      | string(12)   | No       | First 12 chars of the key (for display)  |
| `name`        | string(128)  | No       | Human-readable label (e.g. "CI prod")    |
| `created_at`  | timestamptz  | No       | When the key was created                 |
| `last_used_at`| timestamptz  | Yes      | Last time the key was used (debounced)   |
| `expires_at`  | timestamptz  | Yes      | Optional expiration (NULL = never)       |
| `revoked_at`  | timestamptz  | Yes      | When the key was revoked (NULL = active) |
| `revoked_by`  | UUID (FK)    | Yes      | Who revoked it (NULL = system/CLI)       |

### Key format

API keys follow the format:

```
stl_ak_<32 random alphanumeric characters>
```

Example: `stl_ak_<32 random alphanumeric characters>`

The `stl_ak_` prefix allows the middleware to distinguish API keys from
JWTs without attempting to decode.

### Key visibility

The full key value is returned **exactly once** — in the response to the
creation request. After that, only the `prefix` (first 12 characters,
e.g. `stl_ak_7f3a9b`) is stored and displayed. The server stores only
the hash. There is no way to recover the full key after creation.

### Self-service management

Every authenticated user can manage their own API keys:

- **Create**: generate a new key with a name and optional expiration
- **List**: view all own keys (prefix, name, created_at, last_used_at,
  expires_at, revoked_at)
- **Revoke**: mark a key as revoked (`revoked_at = now()`,
  `revoked_by = self`)

### Admin management

Users with the `admin` role can view and revoke API keys belonging to
other users from a dedicated page in the administration panel. Admins
**cannot** create keys on behalf of other users — key creation is always
a self-service action by the key owner.

When an admin revokes another user's key, `revoked_by` is set to the
admin's user ID.

### CLI commands

#### `sentinel api-key list`

```
sentinel api-key list --username <username>
```

Lists all API keys (active and revoked) for the given user. Output
includes: prefix, name, created_at, last_used_at, expires_at, status
(active/revoked/expired).

#### `sentinel api-key revoke`

```
sentinel api-key revoke --username <username> --key-id <uuid>
```

Revokes the specified key. Sets `revoked_at = now()` and
`revoked_by = NULL` (CLI action, displayed as "System" in the UI).

If the key is already revoked, prints a message and exits with code 0
(idempotent).

### Automatic revocation

When a user is deactivated (via `user_service.deactivate_user()`), all
their active API keys are revoked with `revoked_by = NULL`. This is part
of the deactivation ordering defined in the Session Management section.

### Active key limit

Each user may have at most **50** active (non-revoked, non-expired) API
keys at any given time. This prevents abuse from compromised
automations while being generous enough for any legitimate use case.
Revoked and expired keys do not count toward the limit.

### Expired and revoked key retention

Expired and revoked API keys are **retained permanently** — there is no
cleanup task. At this scale (~hundreds of users, max 50 active keys per
user), the table grows by at most a few thousand rows per year — negligible
storage. Retained records serve as audit trail (who had access, when it was
revoked). If volume becomes a concern in the future, an operational
`DELETE WHERE revoked_at < now() - interval '2 years'` can be applied.

## API Endpoints

### `GET /api/v1/users/me`

Returns the currently authenticated user's profile.

**Authentication**: required (JWT or API key).

**Response** (200):

```json
{
  "id": "uuid",
  "username": "string",
  "email": "string",
  "full_name": "string | null",
  "roles": ["string"],
  "active": true
}
```

### `POST /api/v1/auth/logout`

Invalidates the current session.

**Authentication**: this endpoint does NOT use the standard
`get_current_user` middleware (which would reject requests with an
already-invalidated session). Instead, it uses a lightweight dependency
that only verifies the JWT signature and extracts claims — it does not
check session liveness. This makes the endpoint fully idempotent:
calling it multiple times (e.g., retry or double-click) always succeeds.

If the token is not a valid JWT (invalid signature, malformed), return
HTTP 401.

If called with an API key instead of a JWT, return HTTP 400 with
message: `"Logout is not applicable to API key authentication."`

**Behavior**:

1. Verify the JWT signature (reject if invalid or expired)
2. Extract `session_id` from the JWT claims
3. Call `session_service.invalidate_session(db, session_id)` — this is
   idempotent: if the session is already inactive, no change is made
4. Return HTTP 204

### `GET /api/v1/api-keys`

Lists all API keys for the current user.

**Authentication**: required.

**Response** (200): array of API key objects (without the full secret):

```json
[
  {
    "id": "uuid",
    "prefix": "stl_ak_7f3a9b",
    "name": "CI production",
    "created_at": "ISO8601",
    "last_used_at": "ISO8601 | null",
    "expires_at": "ISO8601 | null",
    "revoked_at": "ISO8601 | null",
    "revoked_by": "uuid | null"
  }
]
```

### `POST /api/v1/api-keys`

Creates a new API key for the current user.

**Authentication**: required.

**Request body**:

```json
{
  "name": "string (required, 1-128 chars)",
  "expires_at": "ISO8601 | null (optional)"
}
```

**Validation**:

- `name` must be 1–128 characters
- If `expires_at` is provided, it must be in the future. If it is in the
  past, return HTTP 400: `"expires_at must be in the future."`
- The user must have fewer than 50 active (non-revoked, non-expired) API
  keys. If the limit is reached, return HTTP 400:
  `"Maximum number of active API keys reached (50). Revoke unused keys
  before creating new ones."`

**Response** (201):

```json
{
  "id": "uuid",
  "prefix": "stl_ak_7f3a9b",
  "name": "CI production",
  "key": "stl_ak_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "created_at": "ISO8601",
  "expires_at": "ISO8601 | null"
}
```

The `key` field contains the full secret and is returned **only** in
this response. It is never returned again by any other endpoint.

### `DELETE /api/v1/api-keys/{key_id}`

Revokes an API key belonging to the current user.

**Authentication**: required.

**Behavior**:

1. Look up the key by `key_id` — if not found or belongs to a different
   user, return HTTP 404
2. If already revoked, return HTTP 200 (idempotent)
3. Set `revoked_at = now()`, `revoked_by = current_user.id`
4. Return HTTP 200

### `GET /api/v1/admin/api-keys`

Lists API keys across all users. Admin only.

**Authentication**: required. **Permission**: `admin` role.

**Query parameters**:

| Parameter  | Type   | Description                         |
|------------|--------|-------------------------------------|
| `user_id`  | UUID   | Filter by user (optional)           |
| `status`   | string | `active`, `revoked`, `expired` (optional) |
| `page`     | int    | Page number (default 1)             |
| `per_page` | int    | Items per page (default 50)         |

**Response** (200): paginated array of API key objects with user info:

```json
{
  "items": [
    {
      "id": "uuid",
      "prefix": "stl_ak_7f3a9b",
      "name": "CI production",
      "user_id": "uuid",
      "username": "string",
      "created_at": "ISO8601",
      "last_used_at": "ISO8601 | null",
      "expires_at": "ISO8601 | null",
      "revoked_at": "ISO8601 | null",
      "revoked_by": "uuid | null"
    }
  ],
  "total": 42,
  "page": 1,
  "per_page": 50
}
```

### `DELETE /api/v1/admin/api-keys/{key_id}`

Revokes any user's API key. Admin only.

**Authentication**: required. **Permission**: `admin` role.

**Behavior**:

1. Look up the key by `key_id` — if not found, return HTTP 404
2. If already revoked, return HTTP 200 (idempotent)
3. Set `revoked_at = now()`, `revoked_by = current_user.id` (the admin)
4. Return HTTP 200

## UI Surfaces

### Personal API Keys page

Accessible to every authenticated user from their profile/settings area.

- Lists the user's own API keys (prefix, name, created, last used,
  status)
- "Create new key" action: opens a form (name + optional expiration),
  shows the full key once after creation with a copy button
- "Revoke" action per key: confirmation dialog, then revokes

### Administration: API Keys page

Accessible only to users with the `admin` role, in the administration
panel.

- Lists all API keys across all users
- Shows: prefix, name, user (username), created_at, last_used_at, status
- Filter by user, filter by status (active/revoked/expired)
- "Revoke" action per key: confirmation dialog, then revokes
- No "Create" action (admins cannot create keys for other users)

## Use Cases: Bots and AI Agents

Sentinel supports two patterns for programmatic access. The key
difference is **attribution**: whose name appears in ticket events and
audit logs.

### Pattern 1: Bot (acts as an existing user)

An existing user (SSO or local) creates an API key from their personal
page and configures a bot with that key. All operations performed by the
bot are attributed to the user who owns the key.

**Setup**:

1. User logs in to Sentinel (SSO or local credentials)
2. User navigates to their API Keys page
3. User creates a key (e.g. name: "my-automation-bot")
4. User copies the key and configures the bot

**Audit trail**: operations appear as performed by the user.

### Pattern 2: AI Agent (acts as a dedicated identity)

An admin creates a local user account dedicated to the AI agent. The
operator logs in as that user and creates an API key. Operations are
attributed to the agent's own identity.

**Setup**:

1. Admin creates a local user: `sentinel manage-user create --username
   security-scanner --role vulnerability_analyst`
2. Operator logs in to Sentinel as `security-scanner` (using the
   password set at creation)
3. Operator creates an API key from the personal API Keys page
4. Operator copies the key and configures the AI agent

**Audit trail**: operations appear as performed by `security-scanner`.

### Choosing between the two patterns

| Consideration       | Bot (Pattern 1)            | AI Agent (Pattern 2)         |
|---------------------|----------------------------|------------------------------|
| Attribution         | User's name                | Agent's own name             |
| Accountability      | User is responsible        | Agent is independently tracked |
| Permissions         | Inherits user's roles      | Has its own roles            |
| Key management      | User manages their own key | Operator manages agent's key |
| Recommended for     | Personal automations       | Shared/organizational tools  |

## Open Points

- **Admin API Keys page layout**: how should keys be displayed? Options
  include: flat table with all keys (sortable by user, last_used_at),
  search/filter by username, or grouped by user. The exact UX will be
  defined during implementation based on the expected number of keys in
  production.

## Security Considerations

- **JWT_SECRET_KEY** must be a cryptographically random string of at
  least 32 characters. It must never be committed to the repository or
  logged.
- **API key hashing uses plain SHA-256**, not a slow hash like Argon2
  and not a keyed HMAC. API keys have ~190 bits of entropy (32
  alphanumeric characters generated server-side by a CSPRNG) and are
  not vulnerable to offline brute-force — the search space is
  computationally infeasible regardless of hash speed. A plain hash
  avoids the operational burden of a server-side secret: there is no
  key to rotate and no risk of permanently invalidating all API keys
  through a configuration change. Using a slow hash for high-entropy
  tokens would create unnecessary CPU/memory pressure — at 100
  requests/second with Argon2 (64 MiB/hash), the server would consume
  ~6.4 GB of RAM solely for key validation, creating a
  denial-of-service vector.
- **API key secrets** are hashed before storage. The plaintext is never
  stored and cannot be recovered.
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
  Operators should plan key rotation during low-traffic windows and
  communicate the expected impact. There is no graceful dual-key
  transition mechanism; this simplicity is acceptable for an internal
  tool where mass re-login is a minor inconvenience.
- **API key last_used_at debouncing**: updating `last_used_at` on every
  request would create write amplification. Updates are debounced to at
  most once per minute per key.
- **No mandatory API key expiration**: `expires_at` is optional — API keys
  can live indefinitely without rotation. For an internal tool, credential
  hygiene is the user's responsibility. Mitigation mechanisms exist: users
  can revoke keys at any time, admin deactivation revokes all keys, and the
  50-key limit prevents unbounded accumulation.
- **No minimum API key duration**: the only validation on `expires_at` is
  that it must be in the future. Keys with very short durations (seconds or
  minutes) are valid use cases for testing. If a key expires before the
  client uses it, the user simply creates a new one.

## Cross-references

- `docs/features/local-authentication.md` — local login endpoint and
  password management
- `docs/features/sso-authentication.md` — SSO login flow with
  id.suse.com
- `docs/features/user-lifecycle.md` — deactivation side effects
  (API key revocation, session invalidation)
- `docs/features/user-management.md` — creating local user
  accounts (including for AI agents)
- `docs/features/rbac.md` — role-based access control and permission
  model
