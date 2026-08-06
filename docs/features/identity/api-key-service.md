# API Key Service

## Purpose

Centralize all API key operations (creation, revocation, bulk
revocation, listing, retrieval) in a single service module to ensure
consistent enforcement of business rules and side effects regardless of
the entry point (API self-service, admin API, CLI, or user deactivation).

This ensures that:

- The architecture rule "only services perform database operations" is
  satisfied uniformly (see `docs/architecture.md`, Backend Layer
  Architecture)
- Ownership checks occur at the service boundary without leaking key
  existence to unauthorized callers
- Mutation operations (create, revoke) produce audit events atomically
- Read operations apply consistent status derivation and ownership
  filtering

This service follows the same architectural pattern as `user_service`
(`docs/features/identity/user-service.md`).

## Architecture

### Module location

`backend/app/services/api_key_service.py`

### Async pattern

The service is implemented as async functions. The API (FastAPI) is the
primary consumer and calls the service directly with `await`. Entry
points that operate in a synchronous context (CLI commands) call the
service via `asyncio.run()`.

| Entry point         | Invocation pattern                                           |
|---------------------|--------------------------------------------------------------|
| API endpoint        | `await api_key_service.create_key(session, ...)`             |
| CLI command         | `asyncio.run(api_key_service.revoke_key(session, ...))`      |
| `user_service`      | `await api_key_service.revoke_all_user_keys(session, ...)`   |

### Acting user convention

Revocation operations (`revoke_key`, `revoke_all_user_keys`) accept an
`acting_user_id: UUID | None` parameter:

- `UUID` — action performed by an authenticated user (self-service or
  admin). Stored as `revoked_by` on revocation, used for audit events.
- `None` — system action (CLI, deactivation triggered by external sync).
  `revoked_by` is set to `NULL`.

`create_key()` takes no `acting_user_id` parameter — key creation is
exclusively self-service, so the actor is always the key owner by
construction. The audit event records `user_id = target_user_id =
user_id`. See `api-key-management.md` (Admin Management).

`update_last_used_at()` takes no `acting_user_id` — it is operational
metadata excluded from the audit trail.

Note: `deactivate_user()` passes its own `acting_user_id` through (the
admin who triggered deactivation, or `None` for external sync), so
deactivation-triggered revocations attribute to the correct actor.

### Transaction ownership

The service does NOT commit or manage transactions. All operations
(mutations and reads) execute within the caller's database transaction.
The service flushes as needed (e.g., to obtain generated IDs or trigger
unique constraint checks) but never commits. If the caller's transaction
is rolled back, all mutations — including audit events — are rolled back
with it. This is the same pattern used by `session_service`.

**Caller commit responsibility**: each caller owns the commit decision:

- **API endpoint handlers**: the framework commits on successful response
  (standard FastAPI/SQLAlchemy middleware pattern)
- **CLI commands**: the wrapped async flow function commits explicitly
  after the service call returns successfully (see
  `cli-infrastructure.md`, Database Session Management)
- **`user_service.deactivate_user()`**: owns its own transaction
  boundary and commits after all side effects complete (see
  `user-service.md`, Commit ownership)

## Operations

### `create_key()`

Creates a new API key for a user.

**Parameters**:

| Parameter        | Type               | Required | Description                              |
|------------------|--------------------|----------|------------------------------------------|
| `session`        | `AsyncSession`     | Yes      | Database session (caller's transaction)  |
| `user_id`        | `UUID`             | Yes      | Owner of the new key (= acting user)     |
| `name`           | `str`              | Yes      | Human-readable label (1-128 characters)  |
| `expires_at`     | `datetime \| None` | No       | Optional expiration (NULL = never)       |

Key creation is exclusively self-service: the caller is always the key
owner. The audit event records `user_id = user_id` and
`target_user_id = user_id` (actor and target are the same person).

**Preconditions**:

- User must exist and be loaded with `SELECT ... FOR UPDATE` on the
  `User` row. This serializes against concurrent `deactivate_user()`
  calls (see `api-key-management.md`, Create vs. deactivate race).
  If not found, raise `UserNotFoundError`
- User must be active. If `user.active = false`, raise
  `InactiveUserError`

**Validation**:

- `name` is normalized per
  `docs/features/identity/api-key-management.md` (API Key Name Rule):
  trim leading/trailing whitespace, then convert to lowercase. After
  normalization, validate: allowed characters `[a-z0-9._-]`, length
  1-128 characters. If empty, exceeds 128 characters, or contains
  invalid characters, raise `ApiKeyNameValidationError`
- `name` must be unique among the user's non-revoked keys (evaluated on
  the normalized value). If a non-revoked key with the same normalized
  name already exists, raise `ApiKeyNameConflictError`
- If `expires_at` is provided, it must be in the future. If in the past,
  raise `ApiKeyInvalidExpiryError`

**Behavior**:

1. Validate preconditions and input (user existence, active status,
   name trimming and format, name uniqueness, expiry)
2. Generate a cryptographically random key: `stl_ak_` + 32 random
   characters from `[A-Za-z0-9]` (62 symbols, using a CSPRNG)
3. Compute `SHA-256(full_key)` as a lowercase hex digest
4. Create the `ApiKey` record:
   - `user_id` = provided user_id
   - `key_hash` = computed hash
   - `prefix` = first 12 characters of the full key
   - `name` = normalized name (after trim + lowercase)
   - `expires_at` = provided value or NULL
   - `revoked_at` = NULL
   - `revoked_by` = NULL
   - If the INSERT raises an `IntegrityError` due to the partial unique
     index on `(user_id, name) WHERE revoked_at IS NULL` (concurrent race
     condition where two requests pass the application-level uniqueness
     check before either commits), the service must catch the exception
     and re-raise it as `ApiKeyNameConflictError`. This ensures that
     concurrent race conditions produce the same typed error as the
     sequential application-level validation in the preconditions step.
5. Check active key count for the user (non-revoked, non-expired). If
   count exceeds 20, emit a WARNING log with the `user_id` (UUID):
   `"API key anomaly: user has {count} active keys (threshold: 20)"`
6. Create `IdentityAuditEvent` with `event_type = api_key_created` via
   `IdentityAuditLog.log_event()` — `user_id` = key owner,
   `target_user_id` = key owner, `new_value` = key name,
   `detail` = `{"key_id": "uuid"}`
7. Return the created `ApiKey` record and the plaintext secret wrapped
   in `SecretStr`. The caller extracts the secret value via
   `.get_secret_value()` for inclusion in the API response. The
   plaintext must never be logged, serialized, or exposed outside the
   201 response body

**Returns**: `tuple[ApiKey, SecretStr]` — the created record and the
wrapped plaintext secret

### `revoke_key()`

Revokes a single API key.

**Parameters**:

| Parameter        | Type           | Required | Description                              |
|------------------|----------------|----------|------------------------------------------|
| `session`        | `AsyncSession` | Yes      | Database session (caller's transaction)  |
| `key_id`         | `UUID`         | Yes      | ID of the key to revoke                  |
| `acting_user_id` | `UUID \| None` | No       | Who is performing the action             |

**Preconditions**:

- Key must exist. If not found, raise `ApiKeyNotFoundError`

**Idempotency**: if the key is already revoked (`revoked_at IS NOT
NULL`), return the key unchanged without error. No `IdentityAuditEvent`
is created in the idempotent case (see `audit-trail-infrastructure.md`,
Idempotent No-ops).

**Behavior**:

1. Look up the key by `key_id` with `SELECT ... FOR UPDATE`. If not
   found, raise `ApiKeyNotFoundError`. The lock serializes concurrent
   revocations on the same key (see `api-key-management.md`, Revoke
   idempotency under concurrency)
2. If `revoked_at` is already set, return the key unchanged (idempotent)
3. Set `revoked_at = now()`
4. Set `revoked_by = acting_user_id` (NULL for system actions)
5. Create `IdentityAuditEvent` with `event_type = api_key_revoked` via
   `IdentityAuditLog.log_event()` — `user_id` = acting user (or NULL),
   `target_user_id` = key owner, `old_value` = key name,
   `detail` = `{"key_id": "uuid"}`
6. Return `(ApiKey, bool)` — the key record and a flag indicating
   whether this call performed the effective revocation (`True`) or
   the key was already revoked (`False`, idempotent no-op). Callers
   that need to distinguish the two outcomes (e.g., the CLI's
   different output messages) use the flag; callers that do not (e.g.,
   the API endpoint, which returns 200 in both cases) ignore it

**Returns**: `tuple[ApiKey, bool]` — record and `revoked_this_call` flag

**Note**: ownership validation (does the key belong to the calling user?)
is NOT performed by the service. Each caller handles authorization at its
own boundary:

- The self-revoke endpoint calls `get_key()` with `owner_user_id` to
  verify ownership before calling `revoke_key()` (returns 404 if
  mismatch)
- The admin revoke endpoint skips the ownership check (requires
  `manage_users` capability)
- `sentinel api-key revoke` operates on the key UUID directly (system
  action requiring shell access; `acting_user_id=None`)
- `deactivate_user()` does not call `revoke_key()` — it uses
  `revoke_all_user_keys()` directly (ownership is N/A; operates on all
  keys of the target user)

### `revoke_all_user_keys()`

Revokes all non-revoked API keys for a user (including expired ones).
Used as a side effect of user deactivation.

**Parameters**:

| Parameter        | Type           | Required | Description                              |
|------------------|----------------|----------|------------------------------------------|
| `session`        | `AsyncSession` | Yes      | Database session (caller's transaction)  |
| `user_id`        | `UUID`         | Yes      | User whose keys should be revoked        |
| `acting_user_id` | `UUID \| None` | No       | Who is performing the action             |

**Preconditions**:

- User must exist. If not found, raise `UserNotFoundError`

**Behavior**:

1. Validate that the user exists. If not found, raise `UserNotFoundError`
2. Query all non-revoked API keys for the user with `SELECT ... FOR
   UPDATE` (`WHERE revoked_at IS NULL`). The lock serializes against
   concurrent `revoke_key()` calls on individual keys (see
   `api-key-management.md`, Bulk revoke vs. individual revoke)
3. For each locked key whose `revoked_at` is still `NULL` (re-check
   after lock acquisition — a concurrent `revoke_key()` may have
   committed between the query snapshot and the lock grant), set
   `revoked_at = now()` and `revoked_by = acting_user_id`. Keys whose
   `revoked_at` is non-NULL after the lock are skipped — no mutation,
   no audit event
4. For each effectively revoked key (transitioned in step 3), create
   `IdentityAuditEvent` with `event_type = api_key_revoked` via
   `IdentityAuditLog.log_event()` — `user_id` = acting user (or NULL
   for system), `target_user_id` = key owner, `old_value` = key name,
   `detail` = `{"key_id": "uuid", "reason": "user_deactivated"}`
5. Return the count of keys effectively revoked

**Returns**: `int` — number of keys revoked (0 if no non-revoked keys
existed)

**Idempotency**: calling this function when the user has no non-revoked
keys returns 0 without error.

### `list_user_keys()`

Lists API keys belonging to a user with pagination. Used by the
self-service list endpoint and by the CLI `api-key list` command.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session` | `AsyncSession` | Yes | Database session (caller's transaction) |
| `user_id` | `UUID` | Yes | Owner whose keys to list |
| `page` | `int` | No | Page number (default 1) |
| `per_page` | `int` | No | Items per page (default 20, max 100) |

**Behavior**:

1. Query all `ApiKey` records where `user_id` matches (all statuses:
   active, revoked, expired)
2. Order by `created_at` descending (newest first), with deterministic
   tiebreaker per `docs/api-spec.md` (Deterministic Pagination Ordering)
3. Apply pagination (offset/limit)
4. Return paginated results with total count

**Returns**: `PaginatedResult[ApiKey]` (list of records + total count)

The CLI `api-key list` command calls `list_user_keys()` iterating over
all pages to produce the complete table output.

### `list_all_keys()`

Lists API keys across all users with filtering, sorting, and pagination.
Used by the admin list endpoint.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session` | `AsyncSession` | Yes | Database session (caller's transaction) |
| `owner_user_id` | `UUID \| None` | No | Filter by owner (resolved by caller) |
| `status` | `str \| None` | No | Filter: `active`, `revoked`, or `expired` (derived per `api-key-management.md`, Derived Status) |
| `sort_by` | `str` | No | `created_at` (default) or `last_used_at` |
| `sort_order` | `str` | No | `asc` or `desc` (default: `desc`) |
| `page` | `int` | No | Page number (default 1) |
| `per_page` | `int` | No | Items per page (default 20, max 100) |

**Behavior**:

1. Build query with optional filters:
   - `owner_user_id`: exact match on `ApiKey.user_id`
   - `status`: apply the derived status conditions from
     `api-key-management.md` (Derived Status)
2. Apply sorting. When `sort_by=last_used_at`, use deterministic NULL
   ordering per `api-key-management.md` (`last_used_at` NULL Ordering).
   Deterministic tiebreaker per `docs/api-spec.md` (Deterministic
   Pagination Ordering)
3. Apply pagination (offset/limit)
4. Return paginated results with total count

**Returns**: `PaginatedResult[ApiKey]` (list of records + total count)

### `get_key()`

Retrieves a single API key by ID, with optional ownership validation.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session` | `AsyncSession` | Yes | Database session (caller's transaction) |
| `key_id` | `UUID` | Yes | ID of the key to retrieve |
| `owner_user_id` | `UUID \| None` | No | If provided, verify the key belongs to this user |

**Behavior**:

1. Look up the key by `key_id`. If not found, raise
   `ApiKeyNotFoundError`
2. If `owner_user_id` is provided and `key.user_id != owner_user_id`,
   raise `ApiKeyNotFoundError` (same error — conceals key existence
   from non-owners)
3. Return the `ApiKey` record

**Returns**: `ApiKey` record

This function is used by:

- Self-service revoke endpoint: passes `owner_user_id=current_user.id`
  to enforce ownership without leaking existence
- Admin endpoints: passes `owner_user_id=None` to skip ownership check

### `update_last_used_at()`

Persists the debounced `last_used_at` timestamp for an API key after
successful authentication. This is the only service function that
writes `last_used_at` — satisfying the architecture rule that only
services perform database operations.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session` | `AsyncSession` | Yes | Short-lived session owned by the caller (not the request transaction) |
| `key_id` | `UUID` | Yes | ID of the authenticated key |
| `used_at` | `datetime` | Yes | Timestamp to record |

**Behavior**:

1. Execute `UPDATE api_key SET last_used_at = :used_at WHERE id = :key_id
   AND (last_used_at IS NULL OR last_used_at < :used_at)`. The
   conditional prevents concurrent instances from regressing the
   timestamp and handles the first-use case (key never used before)
2. No explicit row lock is required — the conditional UPDATE is a single
   atomic SQL statement. However, the implicit row write lock may block
   behind a concurrent `FOR UPDATE` holder (e.g., `revoke_key()` or
   `revoke_all_user_keys()`). To keep this write best-effort on the
   authentication critical path, the session MUST use a short
   `lock_timeout` (1 second). A timeout is treated identically to any
   other database error (see below)

**Error handling**: the function does NOT catch exceptions internally.
All `SQLAlchemyError` subclasses (including lock timeout, connection
errors, and constraint violations) propagate to the caller. The caller
(authentication boundary) is responsible for catching, logging WARNING,
and proceeding without updating the debounce cache. This keeps the
service function simple and testable — error policy belongs to the
caller that chose best-effort semantics.

**Returns**: None

**Audit trail**: none — `last_used_at` is classified as operational
metadata excluded from `IdentityAuditEvent` coverage (see
`identity-audit-log.md`, Operational Metadata Exclusions).

**Transaction ownership exception**: unlike all other operations in this
module, `update_last_used_at()` is called with a **short-lived session
owned by the authentication boundary**, not the request-scoped
transaction. The caller opens a brief transaction, calls this function,
commits, and discards the session. This ensures the write persists
independently of the request outcome (read-only endpoints do not
commit the request-scoped session). If the commit fails, the caller
logs a WARNING and proceeds — the debounce cache is not updated, so the
next eligible request retries. See `api-key-management.md` (Operational
Metadata: `last_used_at`) for the full debounce contract.

## Concurrency

Concurrency behavior for all operations is defined in
`docs/features/identity/api-key-management.md` (Concurrency and
Locking). Summary:

- **Create-name race**: `create_key()` acquires `FOR UPDATE` on the
  `User` row, serializing concurrent creates. The second caller re-reads
  after the lock grant and raises `ApiKeyNameConflictError` at the
  application-level uniqueness check. The partial unique index on
  `(user_id, name) WHERE revoked_at IS NULL` serves as a database-level
  backstop: `IntegrityError` is caught and re-raised as
  `ApiKeyNameConflictError`.
- **Create vs. deactivate**: `create_key()` acquires `FOR UPDATE` on the
  `User` row to serialize against concurrent `deactivate_user()` calls.
  This prevents a key from surviving deactivation.
- **Revoke idempotency**: `revoke_key()` acquires `FOR UPDATE` on the
  `ApiKey` row. Concurrent revocations serialize: exactly one performs
  the effective mutation and creates one audit event; subsequent callers
  observe `revoked_at IS NOT NULL` and return early (no-op, no event).
- **Bulk revoke vs. individual revoke**: `revoke_all_user_keys()`
  acquires `FOR UPDATE` on all non-revoked `ApiKey` rows. After lock
  acquisition, rows already revoked by a concurrent `revoke_key()` are
  skipped — no duplicate mutation, no duplicate audit event.

## Service Exceptions

All exceptions in this module inherit from `ApiKeyServiceError`.
API endpoint handlers catch `ApiKeyServiceError` subclasses and map them
to the corresponding HTTP status code and error code per `api-spec.md`.

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `UserNotFoundError` † | 404 | `USER_NOT_FOUND` | Owner user does not exist |
| `InactiveUserError` † | 409 | `USER_INACTIVE` | Owner user is inactive |
| `ApiKeyNotFoundError` | 404 | `AUTH_API_KEY_NOT_FOUND` | API key ID does not exist |
| `ApiKeyNameConflictError` | 409 | `AUTH_API_KEY_NAME_CONFLICT` | Key name already in use for this user |
| `ApiKeyNameValidationError` | 422 | `AUTH_API_KEY_NAME_INVALID` | Key name does not meet format requirements |
| `ApiKeyInvalidExpiryError` | 400 | `AUTH_API_KEY_INVALID_EXPIRY` | Expiry date is in the past |

† Shared exception — inherits from `ServiceError`, not from
`ApiKeyServiceError`. Handlers must catch it explicitly.

## Cross-references

- `docs/features/identity/api-key-management.md` — API key feature:
  data model, name rules, status derivation, endpoints, CLI commands,
  concurrency, security considerations (single source of truth)
- `docs/features/identity/authentication.md` — authentication middleware,
  API key validation sub-flow, deactivation ordering
- `docs/features/identity/user-service.md` — `deactivate_user()` calls
  `revoke_all_user_keys()` as step 1 of the deactivation side effects
- `docs/features/identity/identity-audit-log.md` — `api_key_created` and
  `api_key_revoked` event type contract
- `docs/features/platform/audit-trail-infrastructure.md` — BaseAuditLog,
  AuditEventMixin
- `docs/data-model.md` — `ApiKey` table schema
- `docs/api-spec.md` — global API conventions (error envelope, pagination)
