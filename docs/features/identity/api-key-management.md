# API Key Management

## Purpose

Define the complete lifecycle of API keys in Sentinel: creation,
validation rules, status derivation, listing, revocation, anomaly
detection, and retention. API keys provide durable, non-interactive
credentials for programmatic access — suitable for bots, AI agents, CI
pipelines, and any client that cannot perform an interactive login flow.

This specification is the **single source of truth** for the API key
feature. The authentication middleware's key validation flow (hash
lookup, revocation check, expiration check, `last_used_at` debounce) is
defined in `docs/features/identity/authentication.md` (API key
validation sub-flow). The service-layer implementation contract is
defined in `docs/features/identity/api-key-service.md`.

## Data Model: ApiKey

| Column        | Type         | Nullable | Description                              |
|---------------|--------------|----------|------------------------------------------|
| `id`          | UUID         | No       | Primary key                              |
| `user_id`     | UUID (FK)    | No       | References `User.id` (owner)             |
| `key_hash`    | string(64)   | No       | SHA-256 hex digest of the full key (UNIQUE) |
| `prefix`      | string(12)   | No       | First 12 chars of the key (for display)  |
| `name`        | string(128)  | No       | Human-readable label, normalized (see API Key Name Rule) |
| `created_at`  | timestamptz  | No       | When the key was created                 |
| `last_used_at`| timestamptz  | Yes      | Last time the key was used (see Operational Metadata) |
| `expires_at`  | timestamptz  | Yes      | Optional expiration (NULL = never)       |
| `revoked_at`  | timestamptz  | Yes      | When the key was revoked (NULL = active) |
| `revoked_by`  | UUID (FK)    | Yes      | Who revoked it (NULL = system/CLI)       |

**Indexes**:

- `key_hash` (unique) — authentication lookup (hash-based key validation)
- `(user_id, revoked_at)` — efficient listing of active keys per user
- `UNIQUE (user_id, name) WHERE revoked_at IS NULL` — prevents duplicate
  names among non-revoked keys for the same user. Evaluated on the
  normalized (trimmed, lowercased) value

### Key format

API keys follow the format:

```
stl_ak_<32 random characters from [A-Za-z0-9]>
```

The `stl_ak_` prefix allows the authentication middleware to distinguish
API keys from JWTs without attempting to decode.

### Key visibility

The full key value is returned **exactly once** — in the response to the
creation request. After that, only the `prefix` (first 12 characters,
e.g. `stl_ak_7f3a9`) is stored and displayed. The server stores only
the hash. There is no way to recover the full key after creation.

## API Key Name Rule

This section is the authoritative definition of the API key name
validation rule. All other specifications reference this section.

**Normalization** (applied in order before any validation):

1. Trim leading and trailing whitespace
2. Convert to lowercase

**Validation** (applied to the normalized value):

- Allowed character set: `[a-z0-9._-]`
- Length: 1–128 characters (measured after normalization)
- A value that is empty after trim is rejected

**Uniqueness**:

- Evaluated on the **normalized** value
- Scope: per user, among non-revoked keys only
- Enforced by the partial unique index `UNIQUE (user_id, name) WHERE
  revoked_at IS NULL`
- The same name may be reused after the previous key with that name is
  revoked. **Note**: expiration alone does not free the name — an
  expired-but-not-revoked key still occupies its name slot (its
  `revoked_at` is `NULL`, so it satisfies the partial index predicate).
  The user must revoke the expired key before creating a new key with
  the same name

## Derived Status

API keys have no stored `status` column. Status is derived at query time
from the timestamp columns using the following **mutually exclusive
precedence**:

| Priority | Condition | Status |
|----------|-----------|--------|
| 1 (highest) | `revoked_at IS NOT NULL` | `revoked` |
| 2 | `expires_at IS NOT NULL AND expires_at <= now()` | `expired` |
| 3 | Otherwise | `active` |

A key that is both revoked and expired reports status **`revoked`** (the
revocation takes precedence).

This derivation is used consistently by:

- API endpoint `status` filter parameter (admin list)
- API response (when status is included)
- CLI output (`sentinel api-key list`)
- Service-layer active key count (anomaly detection)
- Active key query: `WHERE revoked_at IS NULL AND (expires_at IS NULL OR
  expires_at > now())`

**Note**: bulk revocation on deactivation (`revoke_all_user_keys()`)
operates on all **non-revoked** keys (`WHERE revoked_at IS NULL`),
not only **active** keys. This ensures expired-but-not-revoked keys
are also revoked and receive audit events. See Automatic Revocation.

### `last_used_at` NULL ordering

When sorting by `last_used_at`, NULL values (keys never used) sort last
regardless of sort direction:

| Sort direction | NULL position | Rationale |
|---------------|---------------|-----------|
| Ascending | NULLS LAST | Never-used keys appear after all used keys |
| Descending | NULLS LAST | Never-used keys appear after all used keys |

This ensures that `desc` sort returns the most-recently-used keys
first — the natural query for credential hygiene auditing — while
`asc` sort surfaces the oldest-used keys first without interleaving
never-used keys.

## Expiration Constraints

- `expires_at` is optional. NULL means the key never expires.
- When provided, `expires_at` must be **in the future** at creation time.
  A value in the past is rejected with `AUTH_API_KEY_INVALID_EXPIRY`.
- There is **no maximum expiration duration**. Keys can be created with
  arbitrarily long lifetimes. Credential hygiene is the user's
  responsibility.
- There is **no minimum duration**. Keys with very short lifetimes
  (seconds or minutes) are valid use cases for testing.
- Expiration is a passive check: once `expires_at` passes, the key is
  rejected at authentication time. No background task is needed.

## Operational Metadata: `last_used_at`

`last_used_at` is classified as **operational authentication metadata**.
It records when the key was last successfully used for authentication.

**Update mechanism**:

- The authentication middleware determines when a write is needed
  (debounce decision), then delegates the actual database write to
  `api_key_service.update_last_used_at()` — satisfying the architecture
  rule that only services perform database operations
- **Debounced**: at most once per minute per key per server instance, to
  reduce write pressure. The debounce uses a per-instance in-memory
  cache of `key_id → last_write_timestamp`
- With N API server instances, worst case is N writes per minute per key
  — acceptable for an internal tool
- **Independent transaction**: the write executes in a short-lived
  session owned by the authentication boundary, not the request-scoped
  transaction. This ensures the timestamp persists regardless of the
  request outcome (read-only endpoints do not commit the request-scoped
  session)
- **Best-effort**: if the commit fails (database error), the middleware
  logs a WARNING and proceeds — the authenticated request is not
  affected. The debounce cache is updated only after a successful
  commit, so the next eligible request retries
- **Monotonic**: the service uses a conditional UPDATE
  (`last_used_at IS NULL OR last_used_at < :used_at`) to prevent
  concurrent instances from regressing the timestamp and to handle the
  first-use case (key never used before)

**Audit trail exclusion**:

- `last_used_at` updates do NOT produce `IdentityAuditEvent` records.
  This is an explicit exclusion: routine key usage would generate
  unmanageable audit event volume without meaningful security value.
  The debounced write is not a user-facing mutation — it is an internal
  operational signal.
- See `docs/features/identity/identity-audit-log.md` (Operational
  Metadata Exclusions) for the formal exclusion declaration.

## Self-Service Management

Every authenticated user can manage their own API keys:

- **Create**: generate a new key with a name and optional expiration
- **List**: view all own keys (id, prefix, name, created_at, last_used_at,
  expires_at, status)
- **Revoke**: mark a key as revoked (`revoked_at = now()`,
  `revoked_by = self`)

## Admin Management

Users with the `manage_users` capability can view and revoke API keys
belonging to other users. They **cannot** create keys on behalf of other
users — key creation is always a self-service action by the key owner.

When an admin revokes another user's key, `revoked_by` is set to the
admin's user ID.

## Automatic Revocation

When a user is deactivated (via `user_service.deactivate_user()`), all
their non-revoked API keys (including expired ones) are revoked via
`api_key_service.revoke_all_user_keys()`. The `acting_user_id` from
`deactivate_user()` is passed through, so `revoked_by` reflects the
admin who triggered deactivation (or `NULL` for system actions like
external sync). This is part of the deactivation ordering defined in
`docs/features/identity/authentication.md` (Deactivation ordering).

Revocation during deactivation is permanent. When the user is later
reactivated, API keys are NOT restored — deactivation is an irreversible
credential kill, and reactivation is not evidence that old credentials
are still trustworthy. The user must create new keys after reactivation.

## Active Key Anomaly Detection

There is no hard limit on the number of API keys a user can create.
A WARNING log is emitted when a user exceeds **20** active (non-revoked,
non-expired) keys, as an anomaly indicator. Active key count is evaluated
at query time using the active key query defined in Derived Status. This
provides detection without blocking legitimate automation use cases.

The WARNING log contains only `user_id` (UUID) and the count — no
username, email, or other PII. See
`docs/features/identity/api-key-service.md` (`create_key()`, step 5) for
the exact log format.

## Expired and Revoked Key Retention

Expired and revoked API keys are **retained permanently** — there is no
cleanup task. At this scale (~hundreds of users), the table grows by at
most a few thousand rows per year — negligible storage. Retained records
serve as audit trail (who had access, when it was revoked).

## API Endpoints

### List My API Keys

```
GET /api/v1/api-keys
```

Lists all API keys for the current user.

**`Access: Authenticated`**

**Query parameters**:

| Parameter  | Type   | Description                         |
|------------|--------|-------------------------------------|
| `page`     | int    | Page number (default 1)             |
| `per_page` | int    | Items per page (default 20, max 100)  |

**Sorting**: fixed at `created_at` descending (newest first).
Client-controlled `sort_by`/`sort_order` are not supported.
Deterministic tiebreaker per `docs/api-spec.md` (Deterministic
Pagination Ordering).

**Response** (200): paginated array of API key objects (without the full
secret):

```json
{
  "data": [
    {
      "id": "uuid",
      "prefix": "stl_ak_7f3a9",
      "name": "ci-production",
      "status": "active",
      "created_at": "2026-03-15T10:30:00Z",
      "last_used_at": "2026-08-01T14:22:00Z",
      "expires_at": null,
      "revoked_at": null,
      "revoked_by": null
    }
  ],
  "meta": {
    "total": 5,
    "page": 1,
    "per_page": 20
  }
}
```

`revoked_by` uses the same User Reference object shape as in the revoke
response (`{id, username, full_name, active}`) when non-NULL. When NULL
(system/CLI revocation), it is serialized as `null`.

### Create API Key

```
POST /api/v1/api-keys
```

Creates a new API key for the current user.

**`Access: Authenticated`**

**Authentication restriction**: API-key-authenticated requests receive
HTTP 403 with code `AUTH_SESSION_REQUIRED` and message: `"API key
creation requires session authentication."` — this prevents a
compromised API key from self-replicating by generating additional keys.

The check is implemented as a shared dependency `require_session_auth`
that depends on `get_current_user` (guaranteeing that
`request.state.auth_method` has been set at step 7 of Credential
resolution). `require_session_auth` is **fail-closed**: it proceeds only
when `request.state.auth_method == "session"`. Any other value —
including `"api_key"`, an absent attribute, or an unrecognized value —
yields HTTP 403 with code `AUTH_SESSION_REQUIRED`. The dependency
returns the authenticated `User` (the same instance returned by
`get_current_user`), so the endpoint handler uses it as the sole
authentication dependency.

**Request body**:

```json
{
  "name": "string (required)",
  "expires_at": "ISO8601 | null (optional)"
}
```

**Name validation**: per API Key Name Rule above.

**Behavior**:

1. Verify the request is authenticated via JWT session (not API key)
   using `require_session_auth`. If not, return HTTP 403 with code
   `AUTH_SESSION_REQUIRED`
2. Delegate to `api_key_service.create_key(session, user_id=current_user.id,
   name=name, expires_at=expires_at)`.
   See `docs/features/identity/api-key-service.md`
3. Return HTTP 201 with the created key (including the plaintext secret)

The service performs all validation (name format, name uniqueness,
expiry) and raises typed exceptions that the handler maps to HTTP
responses. See `docs/features/identity/api-key-service.md`
(Service Exceptions).

**Response** (201):

```json
{
  "data": {
    "id": "uuid",
    "prefix": "stl_ak_7f3a9",
    "name": "ci-production",
    "key": "stl_ak_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "created_at": "2026-03-15T10:30:00Z",
    "expires_at": null
  }
}
```

The `key` field contains the full secret and is returned **only** in
this response. It is never returned again by any other endpoint.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_SESSION_REQUIRED` | Request authenticated via API key instead of session |
| 409 | `AUTH_API_KEY_NAME_CONFLICT` | Non-revoked key with the same normalized name exists |
| 409 | `USER_INACTIVE` | Owner was deactivated concurrently with key creation |
| 422 | `AUTH_API_KEY_NAME_INVALID` | Key name contains invalid characters or exceeds length limits |
| 400 | `AUTH_API_KEY_INVALID_EXPIRY` | `expires_at` is in the past |

### Revoke My API Key

```
POST /api/v1/api-keys/{key_id}/revoke
```

Revokes an API key belonging to the current user. The key record is
preserved in the database (not deleted) and remains visible in list
endpoints with `revoked_at` populated.

**`Access: Authenticated`**

**Behavior**:

1. Look up the key by `key_id` via
   `api_key_service.get_key(session, key_id, owner_user_id=current_user.id)`.
   If not found or belongs to a different user, return HTTP 404 with code
   `AUTH_API_KEY_NOT_FOUND`. The ownership check is performed by the
   service (returns `ApiKeyNotFoundError` on mismatch to conceal key
   existence)
2. Delegate to `api_key_service.revoke_key(session, key_id,
   acting_user_id=current_user.id)`. The service handles idempotency
   (already-revoked keys are returned unchanged). See
   `docs/features/identity/api-key-service.md`
3. Return HTTP 200

**Response** (200):

```json
{
  "data": {
    "id": "uuid",
    "prefix": "stl_ak_7f3a9",
    "name": "ci-production",
    "status": "revoked",
    "created_at": "2026-03-15T10:30:00Z",
    "last_used_at": "2026-08-01T14:22:00Z",
    "expires_at": null,
    "revoked_at": "2026-08-05T09:00:00Z",
    "revoked_by": {
      "id": "uuid",
      "username": "jdoe",
      "full_name": "John Doe",
      "active": true
    }
  }
}
```

When `revoked_by` is `NULL` (system/CLI revocation), it is serialized as
`null`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `AUTH_API_KEY_NOT_FOUND` | Key not found or belongs to a different user |

### List All API Keys (Admin)

```
GET /api/v1/admin/api-keys
```

Lists API keys across all users.

**`Capability: manage_users`**

**Query parameters**:

| Parameter  | Type   | Description                         |
|------------|--------|-------------------------------------|
| `owner`    | string | Filter by key owner; accepts UUID or username. If the identifier does not resolve to a user, return an empty page without querying the service (optional) |
| `status`   | string | `active`, `revoked`, or `expired` — single value only. Derived per Derived Status above (optional) |
| `page`     | int    | Page number (default 1)             |
| `per_page` | int    | Items per page (default 20, max 100)  |
| `sort_by`  | string | Field to sort by: `created_at`, `last_used_at` (default: `created_at`) |
| `sort_order`| string | `asc` or `desc` (default: `desc`)  |

**`last_used_at` NULL ordering**: when `sort_by=last_used_at`, NULL
values are ordered per the `last_used_at` NULL Ordering rule above.
Deterministic tiebreaker per `docs/api-spec.md` (Deterministic
Pagination Ordering).

**Response** (200): paginated array of API key objects with owner info:

```json
{
  "data": [
    {
      "id": "uuid",
      "prefix": "stl_ak_7f3a9",
      "name": "ci-production",
      "status": "active",
      "owner": {
        "id": "uuid",
        "username": "jdoe",
        "full_name": "John Doe",
        "active": true
      },
      "created_at": "2026-03-15T10:30:00Z",
      "last_used_at": "2026-08-01T14:22:00Z",
      "expires_at": null,
      "revoked_at": null,
      "revoked_by": null
    }
  ],
  "meta": {
    "total": 42,
    "page": 1,
    "per_page": 20
  }
}
```

### Revoke API Key (Admin)

```
POST /api/v1/admin/api-keys/{key_id}/revoke
```

Revokes any user's API key. The key record is preserved in the database
(not deleted) and remains visible in list endpoints with `revoked_at`
populated.

**`Capability: manage_users`**

**Behavior**:

1. Delegate to `api_key_service.revoke_key(session, key_id,
   acting_user_id=current_user.id)`. The service handles idempotency
   (already-revoked keys are returned unchanged) and raises
   `ApiKeyNotFoundError` if the key does not exist. See
   `docs/features/identity/api-key-service.md`
2. No ownership check — admins can revoke any key
3. Return HTTP 200

**Self-revocation**: an admin may revoke the API key authenticating the
current request. This succeeds because authentication validation occurs
before handler execution. The key is revoked and all subsequent requests
using it are rejected.

**Response** (200):

```json
{
  "data": {
    "id": "uuid",
    "prefix": "stl_ak_7f3a9",
    "name": "ci-production",
    "status": "revoked",
    "owner": {
      "id": "uuid",
      "username": "jdoe",
      "full_name": "John Doe",
      "active": true
    },
    "created_at": "2026-03-15T10:30:00Z",
    "last_used_at": "2026-08-01T14:22:00Z",
    "expires_at": null,
    "revoked_at": "2026-08-05T09:00:00Z",
    "revoked_by": {
      "id": "uuid",
      "username": "asmith",
      "full_name": "Alice Smith",
      "active": true
    }
  }
}
```

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `AUTH_API_KEY_NOT_FOUND` | Key not found |

## CLI Commands

### `sentinel api-key list`

```
sentinel api-key list --username <username>
```

Lists all API keys (active, revoked, and expired) for the given user.
Output includes: id, prefix, name, created_at, last_used_at, expires_at,
status (derived per Derived Status above). The `id` column provides the
UUID required by `sentinel api-key revoke --key-id`.

**Output**: table with fixed-width columns on stdout.

**Exit codes**:

| Code | Condition |
|------|-----------|
| 0 | Success (including empty result when user has no keys) |
| 1 | User error: `Error: User '<username>' not found.` (stderr) |
| 2 | System error: database unreachable |

**Idempotency**: Idempotent (read-only query).

### `sentinel api-key revoke`

```
sentinel api-key revoke --key-id <uuid>
```

Revokes the specified API key. Delegates to
`api_key_service.revoke_key(session, key_id, acting_user_id=None)`. See
`docs/features/identity/api-key-service.md`.

`acting_user_id` is `None` (CLI/system action), so `revoked_by` is set
to `NULL` (displayed as "System" in the UI).

The `--key-id` UUID is globally unique and sufficient to identify the
key. No `--username` flag is needed because the UUID unambiguously
identifies the target key regardless of owner. The operator obtains the
UUID from `sentinel api-key list --username <username>`.

**Output**: confirmation message on stdout. If the key was actively
revoked: `Revoked API key '<prefix>...' (<name>).` If the key was
already revoked (idempotent no-op): `API key '<prefix>...' (<name>) is
already revoked.` Errors on stderr with `Error:` prefix.

**Exit codes**:

| Code | Condition |
|------|-----------|
| 0 | Success (including already-revoked key = idempotent no-op) |
| 1 | User error: `Error: API key '<key_id>' not found.` (stderr) |
| 2 | System error: database unreachable |

**Idempotency**: Idempotent (no-op if already revoked — handled by the
service's idempotency guarantee).

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
   security-scanner --role restricted_analyst` (or `--role
   vulnerability_analyst` if confidential ticket access is needed)
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

## Concurrency and Locking

### Create-name race

Two concurrent `create_key()` calls with the same normalized name for
the same user: since both acquire `SELECT ... FOR UPDATE` on the same
`User` row as their first operation (see Create vs. deactivate race),
they serialize at the database level. The second caller re-reads after
the lock grant, sees the committed key, and raises
`ApiKeyNameConflictError` at the application-level uniqueness check.

The partial unique index `UNIQUE (user_id, name) WHERE revoked_at IS
NULL` serves as a database-level backstop: if a future code path inserts
without holding the `User` lock, the index produces an `IntegrityError`
that the service catches and re-raises as `ApiKeyNameConflictError` —
the same typed error as the application-level validation.

### Create vs. deactivate race

A concurrent `create_key()` and `deactivate_user()` for the same user:
`create_key()` acquires `SELECT ... FOR UPDATE` on the `User` row as
its first operation. Since `deactivate_user()` also holds `FOR UPDATE`
on the same row, the two transactions serialize at the database level.

- If deactivation commits first: `create_key()` observes
  `user.active = false` and rejects with `InactiveUserError`
- If creation commits first: `deactivate_user()` observes the new key
  in `revoke_all_user_keys()` and revokes it

Without this lock, under `READ COMMITTED` a concurrent `create_key()`
could observe `active = true` from the pre-deactivation snapshot, insert
a key after `revoke_all_user_keys()` has already queried, and produce a
non-revoked key on an inactive user — violating the permanence guarantee.

### Revoke idempotency under concurrency

Concurrent `revoke_key()` calls for the same key: `revoke_key()`
acquires `SELECT ... FOR UPDATE` on the `ApiKey` row as its first
operation, serializing concurrent revocations at the database level.

- The first transaction to acquire the lock sets `revoked_at` and
  creates exactly one `api_key_revoked` audit event
- Subsequent transactions (blocked until the first commits) observe
  `revoked_at IS NOT NULL` and return the key unchanged — no mutation,
  no audit event

This ensures that concurrent revocations produce exactly one effective
mutation and exactly one audit event, consistent with the pessimistic
locking pattern in `docs/conventions.md` (Transaction and Locking).

### Bulk revoke vs. individual revoke

Concurrent `revoke_all_user_keys()` (via `deactivate_user()`) and
`revoke_key()` (via self-service, admin, or CLI) for the same key:
`revoke_all_user_keys()` acquires `SELECT ... FOR UPDATE` on all
non-revoked `ApiKey` rows for the user. After lock acquisition, each
row is re-checked:

- If `revoked_at` is still `NULL`: the bulk operation sets `revoked_at`
  and creates one `api_key_revoked` audit event
- If `revoked_at` is non-NULL (set by a concurrent `revoke_key()` that
  committed while the bulk operation was waiting for the lock): the row
  is skipped — no mutation, no duplicate audit event

This ensures the same "exactly one effective mutation and exactly one
audit event per key" guarantee as the individual revoke case.

**Lock ordering**: `deactivate_user()` acquires `FOR UPDATE` on the
`User` row first (see `user-service.md`, Concurrent deactivation),
then `revoke_all_user_keys()` locks the `ApiKey` rows.
`revoke_key()` locks only the `ApiKey` row (never the `User` row),
so no deadlock cycle exists.

### Create vs. revoke (name reuse)

A user revokes a key and immediately creates a new key with the same
name (standard credential rotation). These two operations do NOT
serialize with each other: `create_key()` locks the `User` row while
`revoke_key()` locks the `ApiKey` row. If the operations overlap:

- If the revoke commits before create's uniqueness check: creation
  succeeds (name slot is free)
- If the revoke has not yet committed when create checks uniqueness:
  creation raises `ApiKeyNameConflictError` (409) — a transient error

**Client expectation**: credential rotation MUST sequence
revoke-then-create (wait for the revoke response before issuing the
create request). Parallelizing the two operations may produce a
transient 409 that resolves on retry.

## Security Considerations

- **API key hashing uses plain SHA-256**, not a slow hash like bcrypt
  and not a keyed HMAC. API keys have ~190 bits of entropy (32
  characters from `[A-Za-z0-9]` = 62 symbols, generated server-side by
  a CSPRNG); the stored `prefix` exposes the first 5 random characters
  (~30 bits), leaving ~161 bits of residual entropy — still
  computationally infeasible to brute-force regardless of hash speed.
  A plain hash
  avoids the operational burden of a server-side secret: there is no
  key to rotate and no risk of permanently invalidating all API keys
  through a configuration change. Using a slow hash for high-entropy
  tokens would create unnecessary CPU pressure — at 100
  requests/second with bcrypt (cost 12, ~300ms/op), the server would
  need 30 CPU-seconds per second solely for key validation, creating a
  denial-of-service vector.
- **API key secrets** are hashed before storage. The plaintext is never
  stored and cannot be recovered.
- **No mandatory API key expiration**: `expires_at` is optional — API keys
  can live indefinitely without rotation. For an internal tool, credential
  hygiene is the user's responsibility. Mitigation mechanisms exist: users
  can revoke keys at any time, and admin deactivation revokes all keys.
- **No minimum API key duration**: the only validation on `expires_at` is
  that it must be in the future. Keys with very short durations (seconds or
  minutes) are valid use cases for testing. If a key expires before the
  client uses it, the user revokes the expired key and creates a new one
  (expiration does not free the name — see API Key Name Rule).
- **Session-only creation**: API keys cannot be used to create new API
  keys. This prevents a compromised key from self-replicating.
- **Ownership concealment**: the self-service revoke endpoint returns 404
  (not 403) when a key belongs to a different user, preventing
  enumeration of other users' key IDs.
- **API key last_used_at debouncing**: updating `last_used_at` on every
  request would create write amplification. Updates are debounced to at
  most once per minute per key.

## Cross-references

- `docs/api-spec.md` — API conventions, global responses, error codes
- `docs/features/identity/api-key-service.md` — centralized API key
  lifecycle service (create, revoke, bulk-revoke, list, get)
- `docs/features/identity/authentication.md` — authentication middleware,
  API key validation sub-flow, session management, deactivation ordering
- `docs/features/identity/identity-audit-log.md` — `api_key_created` and
  `api_key_revoked` event type contract, operational metadata exclusion
- `docs/features/identity/user-service.md` — deactivation side effects
  (API key revocation, session invalidation)
- `docs/features/identity/user-management.md` — creating local user
  accounts (including for AI agents)
- `docs/features/identity/rbac.md` — role-based access control, Endpoint
  Permission Map
- `docs/features/platform/cli-infrastructure.md` — CLI entry point,
  session management, error handling
- `docs/data-model.md` — `ApiKey` table schema
