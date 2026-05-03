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

| Setting            | Type   | Default | Env var              |
|--------------------|--------|---------|----------------------|
| `jwt_secret_key`   | string | —       | `JWT_SECRET_KEY`     |
| `jwt_algorithm`    | string | `HS256` | `JWT_ALGORITHM`      |
| `jwt_expiry_hours` | int    | `168`   | `JWT_EXPIRY_HOURS`   |

`JWT_SECRET_KEY` is required. The application must refuse to start if it
is not set.

### Claims

| Claim        | Type   | Description                                   |
|--------------|--------|-----------------------------------------------|
| `sub`        | string | User UUID (primary key of the `User` row)     |
| `session_id` | string | UUID of the associated `Session` row          |
| `roles`      | array  | List of role names (e.g. `["admin"]`)         |
| `iat`        | int    | Issued-at timestamp (Unix epoch)              |
| `exp`        | int    | Expiration timestamp (Unix epoch)             |
| `iss`        | string | `"sentinel"` (constant)                       |

### Token lifecycle

- A single token is issued at login. There is no refresh token.
- Token duration defaults to 7 days (`168` hours), configurable via
  `JWT_EXPIRY_HOURS`.
- At expiration, the frontend redirects to the login page.
- A token becomes invalid immediately if its associated session is
  deactivated (see Session Management below).

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
| `expires_at` | timestamptz  | No       | When the session naturally expires  |
| `is_active`  | boolean      | No       | `false` after logout or revocation  |

### Session liveness check

On every authenticated request, the middleware checks:

```
session.is_active = true AND session.expires_at > now()
```

If either condition fails, the request is rejected with HTTP 401.

To avoid a database round-trip on every request, the session liveness
result is cached in Redis with a short TTL (30–60 seconds, configurable).
This means that after logout or deactivation, there is a window of up to
60 seconds before the token becomes effectively unusable. This tradeoff
is acceptable for an internal tool.

### Session invalidation

- **Logout**: the user's current session is marked `is_active = false`.
- **User deactivation**: all sessions for the user are marked
  `is_active = false`. This happens **before** the user is marked as
  inactive (see ordering below).

### Deactivation ordering

When a user is deactivated (via `user_service.deactivate_user()`), the
operations execute in this order:

1. Revoke all API keys for the user
2. Invalidate all active sessions for the user
3. Mark the user as inactive

This ordering ensures that if the process is interrupted at any point,
the user may still appear active but will have already lost access. The
admin can retry the deactivation without risk of leaving a deactivated
user with valid credentials.

### Session cleanup

A Celery Beat task runs **once per week** and deletes all session rows
where `expires_at < now()` or `is_active = false`. No session history is
retained — expired and invalidated sessions are deleted without trace.

This is a maintenance task, not a `BaseFetcher` subclass (it does not
fetch data from external sources).

## Middleware: `get_current_user`

The FastAPI dependency `get_current_user` extracts and validates
credentials from the incoming request. It is injected via `Depends()`
into all endpoints that require authentication.

### Credential resolution

1. Read the `Authorization` header. If absent, return HTTP 401.
2. Extract the bearer value: `Authorization: Bearer <token>`.
3. Determine credential type:
   - If the token starts with `stl_ak_`: treat as **API key**
   - Otherwise: treat as **JWT**
4. Validate according to the credential type (see below).
5. Load the `User` record. If the user is inactive (`active = false`),
   return HTTP 401.
6. Return the authenticated user.

### JWT validation

1. Decode the token using `JWT_SECRET_KEY` and `JWT_ALGORITHM`.
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

1. Hash the presented key using the same algorithm used at creation.
2. Look up the `ApiKey` record by hash.
3. Verify `revoked_at` is `NULL`.
4. If `expires_at` is set, verify it has not passed.
5. Update `last_used_at` to the current timestamp (debounced: update at
   most once per minute to reduce write pressure).
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
| `key_hash`    | string       | No       | Argon2 hash of the full key              |
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

### No hard limit

There is no enforced maximum number of API keys per user. Operators
should follow the principle of least privilege: one key per use case,
revoke unused keys regularly.

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

**Authentication**: required (JWT only — API keys have no session).

**Behavior**:

1. Extract `session_id` from the JWT claims
2. Mark the session as `is_active = false`
3. Invalidate the Redis cache entry for this session
4. Return HTTP 204

If called with an API key instead of a JWT, return HTTP 400 with
message: `"Logout is not applicable to API key authentication."`

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
- **API key secrets** are hashed before storage. The plaintext is never
  stored and cannot be recovered.
- **Session liveness check** ensures that logout and deactivation take
  effect within the cache TTL window (30–60 seconds maximum).
- **No single logout (SLO)**: logging out of `id.suse.com` does not
  invalidate the Sentinel session. This is a known limitation,
  acceptable for an internal tool. Users can log out of Sentinel
  explicitly.
- **Token in browser storage**: the JWT should be stored in an
  `HttpOnly` cookie (preferred, immune to XSS) or `localStorage` (if
  cookie-based auth adds unacceptable complexity). The choice will be
  finalized in implementation.
- **API key last_used_at debouncing**: updating `last_used_at` on every
  request would create write amplification. Updates are debounced to at
  most once per minute per key.

## Cross-references

- `docs/features/local-authentication.md` — local login endpoint and
  password management
- `docs/features/sso-authentication.md` — SSO login flow with
  id.suse.com
- `docs/features/user-lifecycle.md` — deactivation side effects
  (API key revocation, session invalidation)
- `docs/features/local-user-management.md` — creating local user
  accounts (including for AI agents)
- `docs/features/rbac.md` — role-based access control and permission
  model
