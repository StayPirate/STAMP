# API Key Service

## Purpose

Centralize all API key lifecycle operations (creation, revocation,
bulk revocation) in a single service module to ensure consistent
enforcement of business rules and side effects regardless of the entry
point (API self-service, admin API, CLI, or user deactivation).

Read-only operations (listing and retrieving API keys) are not
centralized in this service because they carry no business logic, side
effects, or audit trail requirements. They are implemented directly in
API endpoint handlers (see `docs/features/identity/authentication.md`).

Without this centralization, each entry point would need to independently
implement the mutation logic and, once the audit trail redesign is
applied, independently create the corresponding `IdentityAuditEvent`
records — a fragile pattern prone to inconsistency.

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

All operations accept an `acting_user_id: UUID | None` parameter:

- `UUID` — action performed by an authenticated user (self-service or
  admin). Stored as `revoked_by` on revocation, used for future audit
  events.
- `None` — system action (CLI, deactivation side effect). `revoked_by`
  is set to `NULL`.

### Transaction ownership

The service does NOT commit or manage transactions. All mutations execute
within the caller's database transaction. If the caller's transaction is
rolled back, the API key mutation is rolled back with it. This is the
same pattern used by `user_service` and `session_service`.

## Operations

### `create_key()`

Creates a new API key for a user.

**Parameters**:

| Parameter        | Type               | Required | Description                              |
|------------------|--------------------|----------|------------------------------------------|
| `session`        | `AsyncSession`     | Yes      | Database session (caller's transaction)  |
| `user_id`        | `UUID`             | Yes      | Owner of the new key                     |
| `name`           | `str`              | Yes      | Human-readable label (1-128 characters)  |
| `expires_at`     | `datetime \| None` | No       | Optional expiration (NULL = never)       |
| `acting_user_id` | `UUID \| None`     | No       | Who is performing the action             |

**Preconditions**:

- User must exist. If not found, raise `UserNotFoundError`
- User must be active. If `user.active = false`, raise
  `InactiveUserError`

**Validation**:

- `name` is normalized by trimming leading and trailing whitespace
  before any other validation. After trimming, the name must be 1-128
  characters. If empty or exceeds 128 characters, raise
  `ApiKeyNameValidationError`
- `name` must be unique among the user's non-revoked keys. If a
  non-revoked key with the same name already exists, raise
  `ApiKeyNameConflictError`
- If `expires_at` is provided, it must be in the future. If in the past,
  raise `ApiKeyInvalidExpiryError`

**Behavior**:

1. Validate preconditions and input (user existence, active status,
   name trimming and format, name uniqueness, expiry)
2. Generate a cryptographically random key: `stl_ak_` + 32 random
   alphanumeric characters (using a CSPRNG)
3. Compute `SHA-256(full_key)` as a lowercase hex digest
4. Create the `ApiKey` record:
   - `user_id` = provided user_id
   - `key_hash` = computed hash
   - `prefix` = first 12 characters of the full key
   - `name` = provided name
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
   count exceeds 20, emit a WARNING log: `"User {username} has {count}
   active API keys (anomaly threshold: 20)"`
6. Create `IdentityAuditEvent` with `event_type = api_key_created` via
   `IdentityAuditLog.log_event()` — `user_id` = acting user,
   `target_user_id` = key owner, `new_value` = key name,
   `detail` = `{"key_id": "uuid"}`
7. Return the created `ApiKey` record with the plaintext key accessible
   as a transient attribute (not persisted to the database). The caller
   is responsible for including the plaintext key in the API response

**Returns**: `ApiKey` record (with transient `key` attribute containing
the plaintext secret)

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

1. Look up the key by `key_id`. If not found, raise
   `ApiKeyNotFoundError`
2. If `revoked_at` is already set, return the key unchanged (idempotent)
3. Set `revoked_at = now()`
4. Set `revoked_by = acting_user_id` (NULL for system actions)
5. Create `IdentityAuditEvent` with `event_type = api_key_revoked` via
   `IdentityAuditLog.log_event()` — `user_id` = acting user (or NULL),
   `target_user_id` = key owner, `old_value` = key name,
   `detail` = `{"key_id": "uuid"}`
6. Return the revoked `ApiKey` record

**Returns**: `ApiKey` record

**Note**: ownership validation (does the key belong to the calling user?)
is NOT performed by the service. This is an endpoint-level concern:

- The self-revoke endpoint checks `key.user_id == current_user.id`
  before calling the service (returns 404 if mismatch)
- The admin revoke endpoint skips the ownership check
- The CLI and `deactivate_user()` use `revoke_all_user_keys()` instead

### `revoke_all_user_keys()`

Revokes all active API keys for a user. Used as a side effect of user
deactivation.

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
2. Query all active (non-revoked) API keys for the user
3. For each key, set `revoked_at = now()` and `revoked_by =
   acting_user_id`
4. For each revoked key, create `IdentityAuditEvent` with
   `event_type = api_key_revoked` via `IdentityAuditLog.log_event()` —
   `user_id` = acting user (or NULL for system), `target_user_id` = key
   owner, `old_value` = key name,
   `detail` = `{"key_id": "uuid", "reason": "user_deactivated"}`
5. Return the count of revoked keys

**Returns**: `int` — number of keys revoked (0 if no active keys
existed)

**Idempotency**: calling this function when the user has no active keys
returns 0 without error.

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
| `ApiKeyInvalidExpiryError` | 400 | `AUTH_API_KEY_INVALID_EXPIRY` | Expiry date is in the past or exceeds maximum |

† Shared exception — inherits from `ServiceError`, not from
`ApiKeyServiceError`. Handlers must catch it explicitly.

## Cross-references

- `docs/features/identity/authentication.md` — API key data model, key
  format, key visibility rules, endpoint definitions, anomaly detection
  threshold
- `docs/features/identity/user-service.md` — `deactivate_user()` calls
  `revoke_all_user_keys()` as step 1 of the deactivation side effects
- `docs/features/identity/identity-audit-log.md` — `api_key_created` and
  `api_key_revoked` event type contract
- `docs/features/platform/audit-trail-infrastructure.md` — BaseAuditLog,
  AuditEventMixin
- `docs/data-model.md` — `ApiKey` table schema
- `docs/api-spec.md` — global API conventions (error envelope, pagination)
