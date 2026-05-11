# User Lifecycle Service

## Purpose

Centralize all user lifecycle operations (creation, modification,
deactivation, reactivation, role management) in a single service module
to ensure consistent enforcement of business rules and side effects
regardless of the entry point (API, CLI, LDAP sync, or future
integrations).

Without this centralization, each entry point would need to independently
implement side effects (ticket unassignment, API key revocation,
TicketEvent creation) and business rules (self-removal guard,
self-deactivation guard), leading to inconsistency and bugs.

## Architecture

### Module location

`backend/app/services/user_service.py`

### Async pattern

The service is implemented as async functions. The API (FastAPI) is the
primary consumer and calls the service directly with `await`. Entry
points that operate in a synchronous context (CLI commands, Celery tasks)
call the service via `asyncio.run()`.

| Entry point         | Invocation pattern                                      |
|---------------------|---------------------------------------------------------|
| API endpoint        | `await user_service.create_user(session, ...)`          |
| Celery task (LDAP)  | `asyncio.run(user_service.deactivate_user(session, ...))` |
| CLI command         | `asyncio.run(user_service.create_user(session, ...))`   |

### Acting user convention

All operations accept an `acting_user_id: UUID | None` parameter:

- `UUID` — action performed by an authenticated user. Enables
  self-operation guards (self-deactivation, self-role-removal)
- `None` — system action (LDAP sync, CLI, fetcher). Self-operation
  guards do not apply

This distinction allows the service to enforce invariants for interactive
users while preserving the ability of system processes to perform any
operation.

**API handler rule**: API endpoint handlers MUST always pass the UUID of
the authenticated user (obtained via `Depends()`) as `acting_user_id`.
Passing `None` from an API handler is a bug — it would silently bypass
all self-operation guards. `None` is reserved exclusively for system
entry points (LDAP sync, Celery tasks, CLI commands).

## AD User Data Ownership

For AD users (`ad_object_guid IS NOT NULL`), all identity fields
(`username`, `email`, `full_name`, `ad_dn`, `manager_id`,
`ad_synced_at`) are managed exclusively by the LDAP sync fetcher. No
human caller — whether via API or CLI — may modify these fields. The only
legitimate consumer of `update_user()` for AD users is the sync process
itself (`acting_user_id = None`).

Fields managed by dedicated operations have their own ownership rules:

- `active` — managed by `deactivate_user()` / `reactivate_user()`. For
  AD users, this field is managed exclusively by LDAP sync — manual
  deactivation/reactivation by admins is blocked (see AD Active Status
  Ownership below)
- Roles — managed by `update_roles()`, available to admins for manual
  roles (`ad_group_cn = '_manual'`)
- `password_hash` — managed by `reset_password()`, which independently
  blocks AD users via `ADUserPasswordError`

Conversely, for local users (`ad_object_guid IS NULL`), the
AD-specific fields (`ad_dn`, `manager_id`, `ad_synced_at`) are not
applicable and must not be set — they have no source of truth outside of
Active Directory.

### AD Active Status Ownership

For AD users, Active Directory `EMPLOYEESTATUS` is the sole source of
truth for the `active` field. Manual deactivation or reactivation by
admins (via API, CLI, or UI) is not permitted — these operations are
reserved for the LDAP sync fetcher.

**Rationale**: if an admin could manually deactivate an AD user, the
next sync cycle would reactivate them (because AD still reports
`EMPLOYEESTATUS = Active`). This creates a confusing loop where
irreversible side effects (API key revocation, session invalidation,
ticket unassignment) are triggered by the deactivation but never restored
by the automatic reactivation. Blocking manual deactivation eliminates
this inconsistency entirely.

**Enforcement**: `deactivate_user()` and `reactivate_user()` check
`user.ad_object_guid IS NOT NULL AND acting_user_id IS NOT NULL` and raise
`ADUserStatusReadOnlyError` when both conditions are true. Since LDAP
sync always passes `acting_user_id = None`, its calls are unaffected.
CLI commands add an additional pre-call guard for defense in depth
(CLI also uses `acting_user_id = None`).

**If an AD user must be blocked from Sentinel**: deactivate the
employee in Active Directory. The next LDAP sync cycle will propagate
the change to Sentinel with all associated side effects.

### Immutability Constraints

Once set, `ad_object_guid` cannot be modified by any operation in this
service. This field is the stable identity anchor that links a Sentinel user
to an Active Directory object. All LDAP sync operations match by
`ad_object_guid` — if it were changed, the user would lose its AD
association and historical audit trail.

## Inactive User Management Principle

Deactivation blocks login and revokes active sessions/keys, but does not
prevent administrative modifications to the account. All management
operations (`update_user`, `reset_password`, `update_roles`) remain
available on inactive users via both CLI and API. This allows admins to
prepare accounts before reactivation (e.g., assign appropriate roles, set
a new password).

## User Deletion

User deletion is not supported. Deactivation is the terminal state of the
user lifecycle. This is intentional: User records are referenced by
`TicketEvent` (audit trail), `Ticket` (historical assignments), `ApiKey`
(revocation records), `Session`, and `UserRole`. Deleting a user would
orphan these records or require complex cascade/anonymization logic.

If a future requirement arises (e.g., GDPR right-to-erasure), it will be
addressed as a separate feature with its own specification covering data
anonymization, orphan handling, and audit trail preservation.

## Operations

### Password handling

The `password` and `new_password` parameters accepted by `create_user()`
and `reset_password()` MUST NOT be logged, included in error messages, or
exposed in stack traces. Implementations must treat these fields as opaque
secrets that exist only for the duration of hashing.

### `create_user()`

Creates a new User record with optional initial roles.

**Parameters**:

| Parameter        | Type                        | Required | Description                          |
|------------------|-----------------------------|----------|--------------------------------------|
| `username`       | `str`                       | Yes      | Unique username                      |
| `email`          | `str`                       | Yes      | Unique email address                 |
| `full_name`      | `str \| None`               | No       | Display name                         |
| `active`         | `bool`                      | No       | Default: `True`                      |
| `ad_object_guid` | `UUID \| None`            | No       | AD `objectGUID` (immutable). NULL for local users |
| `ad_dn`        | `str \| None`               | No       | Full AD distinguished name           |
| `manager_id`     | `UUID \| None`              | No       | FK to user.id of the direct line manager |
| `password`       | `str \| None`               | No       | Plain-text password (hashed before storage). Required for local users, must be NULL for AD users |
| `roles`          | `list[tuple[Role, str]]`    | No       | List of (role, ad_group_cn) pairs    |
| `acting_user_id` | `UUID \| None`              | No       | Who is performing the action         |

**Behavior**:

1. Normalize `username` (trim whitespace, lowercase) and validate format
   per `docs/conventions.md` (Username Format). If invalid, raise
   `UsernameFormatError`
2. Validate password/`ad_object_guid` mutual exclusivity: if
   `ad_object_guid` is provided and `password` is also provided, raise
   `ADUserPasswordError`. If `ad_object_guid` is NULL and `password` is not
   provided, raise `PasswordValidationError`
3. Validate uniqueness of `username` and `email` across all users
   (including inactive). If `ad_object_guid` is provided, also validate
   its uniqueness — if already associated with another user, raise
   `UserConflictError`. If violated, raise `UserConflictError`

   **Note on email format**: the service validates email *uniqueness* but
   not *format*. Format validation (RFC 5321/5322 compliance, including
   `+` tag addressing) is the responsibility of the caller — Pydantic
   schemas for the API, Click validation for the CLI. All callers MUST
   use the `email-validator` library to ensure consistent acceptance
   rules across entry points.

4. If `password` is provided, validate length per the password policy in
   `docs/features/identity/local-authentication.md` § Password Validation
   (16–128 characters). If invalid, raise `PasswordValidationError`
5. If `password` is provided, hash it with bcrypt (see
   `docs/features/identity/local-authentication.md` for hashing parameters)
6. Create User record with provided fields,
   `password_hash` set to the hash (or NULL if no password), and
   `ad_synced_at = now()` if `ad_object_guid` is set
7. For each role in `roles`, create UserRole with specified `ad_group_cn`
   and `assigned_by = acting_user_id`. If the list contains duplicate
   entries (same role + same `ad_group_cn`), deduplicate silently — only
   one UserRole record is created per unique `(role, ad_group_cn)` pair.
   This is consistent with the idempotency behavior of `update_roles()`.
8. Return the created User

**TicketEvent**: none (user creation does not affect tickets)

### `update_user()`

Updates mutable user identity fields. This operation does NOT cover role
changes or active status changes — those have dedicated operations with
their own business rules.

**Parameters**:

| Parameter        | Type                        | Required | Description                          |
|------------------|-----------------------------|----------|--------------------------------------|
| `user_id`        | `UUID`                      | Yes      | User to update                       |
| `acting_user_id` | `UUID \| None`              | No       | Who is performing the action         |
| `username`       | `str \| None`               | No       | New username (updated by LDAP sync when sAMAccountName changes) |
| `email`          | `str \| None`               | No       | New email (uniqueness validated)     |
| `full_name`      | `str \| None`               | No       | New display name                     |
| `manager_id`     | `UUID \| None`              | No       | New manager (FK to user.id)          |
| `ad_dn`        | `str \| None`               | No       | Updated AD distinguished name        |
| `ad_synced_at` | `datetime \| None`          | No       | Sync timestamp                       |

**Behavior**:

1. Look up user by ID. If not found, raise `UserNotFoundError`
2. If `user.ad_object_guid IS NOT NULL` and `acting_user_id` is not None:
   raise `ADUserFieldReadOnlyError`. Identity fields of AD users are
   managed exclusively by directory sync (see AD User Data Ownership
   above). The entire `update_user()` operation is blocked for human
   callers on AD users — there is no identity field that an admin
   should modify manually.
3. If `user.ad_object_guid IS NULL` and any of `ad_dn`, `manager_id`, or
   `ad_synced_at` is provided (not `_MISSING`): raise
   `ADUserFieldReadOnlyError`. These fields are AD-specific and have
   no source of truth for local users.
4. **Username validation** (if `username` is provided): normalize and
   validate the format per the rules in `docs/conventions.md` (section
   "Username Format"). If invalid, raise `UserValidationError`. Verify
   uniqueness in the database (excluding the current user, including
   inactive users). If violated, raise `UserConflictError`. For AD
   users, this step is reached only by the sync fetcher (human callers
   are already blocked at step 2).
5. If `email` is provided, validate uniqueness. If violated, raise
   `UserConflictError`
6. Apply provided field updates. Optional parameters use a `_MISSING`
   sentinel as default to distinguish three states:
   - `_MISSING` (default): field is not modified
   - `None`: field is explicitly cleared to NULL in the database
   - Any other value: field is updated to the new value

   This is necessary because nullable fields (`full_name`,
   `manager_id`, `ad_dn`) may need to be explicitly cleared — e.g.,
   when LDAP sync discovers that an AD attribute has been removed. The
   pattern follows Python's standard sentinel convention
   (`dataclasses.MISSING`).

    If all optional parameters are `_MISSING`, this is a no-op: no UPDATE
   is issued. The User record returned is the one loaded in step 1.
7. Return updated User

**TicketEvent**: none

### `update_roles()`

Adds or removes roles for a user.

**Parameters**:

| Parameter      | Type                        | Required | Description                          |
|----------------|-----------------------------|----------|--------------------------------------|
| `user_id`      | `UUID`                      | Yes      | User to update                       |
| `add`          | `list[tuple[Role, str]]`    | No       | Roles to add as (role, ad_group_cn)  |
| `remove`       | `list[tuple[Role, str]]`    | No       | Roles to remove as (role, ad_group_cn) |
| `acting_user_id` | `UUID \| None`            | No       | Who is performing the action         |

**Business rules**:

1. **Self-removal guard**: if `acting_user_id` is not None AND
   `acting_user_id == user_id` AND `Admin` is in the effective `remove`
   list (after input resolution), reject the operation with
   `SelfRoleRemovalError`. This prevents any authenticated user from
   removing their own Admin role, regardless of the entry point. System
   actions (`acting_user_id = None`) are exempt.
   For the implications of this guard on the "zero admins" scenario and
   the CLI recovery procedure, see `docs/features/identity/user-management.md`,
   Business Rule 2
2. **AD-derived role protection**: when `acting_user_id` is set (user
   action), cannot remove roles with `ad_group_cn != '_manual'`. Raise
   `ADDerivedRoleError`. System actions are exempt (LDAP sync must be
   able to remove AD-derived roles when group membership changes)
3. **Idempotency**: adding a role already present for the same
   (user_id, role, ad_group_cn) combination is a no-op. Removing a role
   not present is a no-op

**Behavior**:

1. Look up user by ID. If not found, raise `UserNotFoundError`
2. Resolve inputs (set-based): deduplicate entries within each list
   (treat as sets — each unique `(role, ad_group_cn)` tuple appears at
   most once). Then cancel entries that appear in both lists:
   `effective_add = add − remove`, `effective_remove = remove − add`.
   If both effective lists are empty after resolution, this is a no-op:
   return the user unchanged
3. Validate business rules (self-removal guard, AD-derived protection)
   against the resolved effective lists
4. For each entry in `effective_add`, create UserRole if not already
   present, with `assigned_by = acting_user_id`
5. For each entry in `effective_remove`, delete matching UserRole record
6. Log INFO with `user_id`, `acting_user_id`, roles added, and roles
   removed (effective lists only — omit no-ops)
7. Return updated User with current roles

**TicketEvent**: none (role changes do not directly affect tickets)

### `deactivate_user()`

Deactivates a user account and triggers all associated side effects.

**Parameters**:

| Parameter      | Type                        | Required | Description                          |
|----------------|-----------------------------|----------|--------------------------------------|
| `user_id`      | `UUID`                      | Yes      | User to deactivate                   |
| `acting_user_id` | `UUID \| None`            | No       | Who is performing the action         |
| `reason`       | `str`                       | Yes      | Human-readable reason (used in TicketEvent comments) |

**Preconditions**:

- User must be currently active. If already inactive, this is a no-op
  (returns the user unchanged)
- **AD status guard**: if `user.ad_object_guid IS NOT NULL` AND
  `acting_user_id IS NOT NULL`, reject with
  `ADUserStatusReadOnlyError`. Active status of AD users is managed
  exclusively by directory sync (see AD Active Status Ownership above)
- **Self-deactivation guard**: if `acting_user_id` is not None AND
  `acting_user_id == user_id`, reject with `SelfDeactivationError`

**Side effects** (executed atomically in the same database transaction,
in this specific order):

1. Revoke all API keys belonging to this user: set `revoked_at = now()`
   and `revoked_by = NULL` (system action) on all active keys. Keys are
   not deleted — preserves audit trail. See
   `docs/features/identity/authentication.md` (API Keys) for the data model.
2. Invalidate all active sessions for this user via
   `session_service.invalidate_user_sessions(db, user_id)`. This sets
   `Session.is_active = false` in the database AND deletes the
   corresponding Redis cache entries. If Redis is unreachable during
   cache deletion, log WARNING and proceed — the database is the
   authoritative source for session validity. Auth middleware MUST
   verify session status against the database on cache miss. See
   `docs/features/identity/authentication.md` (Session invalidation) for the
   session service contract.
3. Set `User.active = false`
4. Unassign open tickets: for each ticket where `assignee_id` points to
   the deactivated user and the ticket is in active status (see
   `docs/features/tickets/tickets.md` § Status Categories: New,
   Analysis, Analyzed), set `assignee_id = NULL`. Tickets in inactive
   statuses (Resolved, Ignored, Duplicated) retain their current
   assignee. No active ticket should retain an assignee pointing to an
   inactive user — ticket history preserves the previous assignment via
   the TicketEvent record. No attempt is made to reassign to the manager
   or any other user.
   Create a `TicketEvent` of type
   `assignment` with:
   - `user_id = NULL` (system action)
   - `old_value` = deactivated user's username
   - `new_value` = `NULL`
   - `comment` = `"Unassigned from {old}: employee deactivated"`

**Ordering rationale**: API keys and sessions are revoked BEFORE the
user is marked as inactive. This ensures that if the process is
interrupted at any point, a user who still appears active will have
already lost access. The admin can safely retry the deactivation
without risk of leaving a deactivated user with valid credentials.

**Logging**: log INFO for every deactivation with `user_id`,
`acting_user_id`, and `reason`.

**TicketEvent**: yes — one `assignment` event per unassigned ticket (see
`docs/features/tickets/ticket-history.md` for the event type contract)

### `reactivate_user()`

Reactivates a previously deactivated user account.

**Parameters**:

| Parameter      | Type                        | Required | Description                          |
|----------------|-----------------------------|----------|--------------------------------------|
| `user_id`      | `UUID`                      | Yes      | User to reactivate                   |
| `acting_user_id` | `UUID \| None`            | No       | Who is performing the action         |

**Preconditions**:

- User must be currently inactive. If already active, this is a no-op
  (returns the user unchanged)
- **AD status guard**: if `user.ad_object_guid IS NOT NULL` AND
  `acting_user_id IS NOT NULL`, reject with
  `ADUserStatusReadOnlyError`. Active status of AD users is managed
  exclusively by directory sync (see AD Active Status Ownership above)

**Behavior**:

1. Set `User.active = true`
2. Log INFO for every reactivation with `user_id`, `acting_user_id`,
   and the user's current roles
3. Return updated User

**Explicitly NOT restored**:

- Previously unassigned tickets are NOT returned to the user
- Revoked API keys are NOT restored (user must create new ones manually)
- Role assignments are unchanged (roles are not affected by
  deactivation/reactivation)

**TicketEvent**: none (reactivation is not a ticket mutation)

### `reset_password()`

Resets the password for a local user and invalidates all active sessions.

**Parameters**:

| Parameter       | Type             | Required | Description                          |
|-----------------|------------------|----------|--------------------------------------|
| `user_id`       | `UUID`           | Yes      | User whose password is being reset   |
| `new_password`  | `str`            | Yes      | New plain-text password (validated and hashed internally) |
| `acting_user_id`| `UUID \| None`   | No       | Who is performing the action (admin or system) |

**Preconditions**:

- User must exist. If not found, raise `UserNotFoundError`
- User must be a local user (`ad_object_guid IS NULL`). If
  `ad_object_guid` is set, raise `ADUserPasswordError`: "Cannot set
  password for AD user. AD users authenticate via id.suse.com."

**Behavior**:

1. Validate password length (16–128 characters). If invalid, raise
   `PasswordValidationError`
2. Hash the password with bcrypt (see
   `docs/features/identity/local-authentication.md` for hashing parameters)
3. Update `User.password_hash` with the new hash
4. Invalidate all active sessions via
   `session_service.invalidate_user_sessions(db, user_id)` — this
   forces re-login with the new password
5. Clear the login lockout counter: delete the Redis key
   `login_attempts:{username}` if it exists. If Redis is unreachable,
   log WARNING and proceed — the counter will expire naturally via TTL.
   This ensures that a locked-out user regains access immediately after
   a password reset.
6. Return updated User

**TicketEvent**: none (password reset does not affect tickets)

### `unlock_user(user_id, acting_user_id)`

Clears the login lockout counter for a user, restoring their ability to
attempt local authentication.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `user_id` | `UUID` | Target user to unlock |
| `acting_user_id` | `UUID \| None` | Who is performing the action (admin or system) |

**Behavior:**

1. Load user by `user_id`. If not found, raise `UserNotFoundError`.
2. Delete the Redis key `login_attempts:{username}` (where `username`
   is the user's current username). If Redis is unreachable, log WARNING
   and proceed — the counter will expire naturally via TTL.
3. Log at INFO level: admin identity, target user, timestamp.

**Idempotency:** if the user is not currently locked out (Redis key
does not exist or counter is zero), the operation completes successfully
with no error. This is a no-op, not a failure.

**Exceptions:**

| Exception | Condition |
|-----------|-----------|
| `UserNotFoundError` | `user_id` does not match any user |

**Notes:**
- No `TicketEvent` is created (not a ticket operation).
- No session invalidation (unlocking does not indicate compromise).
- No database mutation — this operation is purely Redis-based.
- The `reset_password()` operation continues to clear the lockout
  counter as a side effect (step 5), but `unlock_user()` provides
  an independent path that does not force a password change.

## Transactionality

All operations that produce side effects (particularly `deactivate_user`)
MUST execute within a single database transaction. If any step fails, the
entire operation is rolled back. This ensures that a user is never left in
a partially-deactivated state (e.g., marked inactive but tickets not
unassigned).

Operations without side effects (`create_user`, `update_user`,
`update_roles`, `reactivate_user`) are also transactional but the
atomicity requirement is less critical since they perform a single
logical write.

## Concurrency Considerations

### Concurrent deactivation from multiple entry points

If two entry points call `deactivate_user()` for the same user
concurrently (e.g., LDAP sync and admin API), the `active` precondition
check and write MUST use row-level locking (`SELECT ... FOR UPDATE`) to
prevent duplicate side effects. The first caller acquires the lock,
performs the deactivation, and commits. The second caller acquires the
lock, finds `active = false`, and returns as a no-op.

### Role modification during deactivation

`update_roles()` does not check whether the target user is active. Adding
roles to an inactive user is permitted — it has no immediate effect but
prepares the user for reactivation. This is intentional: an admin may
want to adjust a user's roles before reactivating them.

### Concurrent role modification from multiple entry points

`update_roles()` does not require row-level locking. Each role is an
independent tuple `(user_id, role, ad_group_cn)` managed via atomic
INSERT/DELETE operations — there is no read-modify-write pattern.
Concurrency safety is guaranteed by:

1. **UNIQUE constraint** `(user_id, role, ad_group_cn)` — prevents
   duplicate records regardless of timing
2. **Disjoint key spaces** — manual actions use `ad_group_cn = '_manual'`
   while LDAP sync uses the actual AD group CN. These never operate on
   the same row
3. **Idempotency** — adding a role already present is a no-op; removing
   a role not present is a no-op. Two concurrent identical operations
   produce the same final state as one

No locking, serialization, or coordination is needed between CLI, API,
and LDAP sync entry points for role modifications.

## Service Exceptions

The service layer raises the following typed exceptions. Each consumer
(API handler, CLI command, background task) is responsible for
translating these into its own response format (HTTP status + error code,
CLI exit code + stderr message, etc.). See `docs/features/identity/user-management.md`
for the API-layer mapping.

| Exception | Raised when |
|-----------|-------------|
| `UserNotFoundError` | User lookup by ID or username finds no match |
| `UserConflictError` | Duplicate username or email (uniqueness constraint violation) |
| `UsernameFormatError` | Username does not conform to the format defined in `docs/conventions.md` (Username Format) |
| `SelfRoleRemovalError` | Authenticated user attempts to remove their own Admin role |
| `SelfDeactivationError` | Authenticated user attempts to deactivate themselves |
| `ADUserStatusReadOnlyError` | A human caller (`acting_user_id` is set) attempts to deactivate or reactivate an AD user (`ad_object_guid IS NOT NULL`) — active status is managed exclusively by directory sync |
| `ADDerivedRoleError` | Attempting to manually remove a role derived from AD group membership |
| `ADUserFieldReadOnlyError` | Raised in two cases: (1) a human caller (`acting_user_id` is set) attempts to call `update_user()` on an AD user — all identity fields are managed by directory sync; (2) any caller attempts to set AD-specific fields (`ad_dn`, `manager_id`, `ad_synced_at`) on a local user — these fields are not applicable |
| `ADUserPasswordError` | Attempting to set or reset password for an AD user |
| `PasswordValidationError` | Password does not meet length requirements (16–128 characters) |

## Relationship to Other Specifications

| Spec | Relationship |
|---|---|
| `docs/features/identity/authentication.md` | Defines API key model, session model, and `session_service`. `deactivate_user` revokes keys and calls `session_service.invalidate_user_sessions()`. `reset_password` calls the same. |
| `docs/features/identity/ad-integration.md` | LDAP sync fetcher calls `create_user`, `update_user`, `update_roles`, `deactivate_user`, `reactivate_user` for each synced employee |
| `docs/features/identity/rbac.md` | Admin API endpoints delegate to `update_roles`, `deactivate_user`, `reactivate_user` |
| `docs/features/identity/user-management.md` | CLI commands delegate to `create_user`, `update_user`, `update_roles`, `deactivate_user`, `reactivate_user` |
| `docs/features/identity/local-authentication.md` | Defines password management. `create_user` accepts an optional password. CLI `set-password` and admin endpoint delegate to `reset_password` |
| `docs/features/tickets/ticket-history.md` | `deactivate_user` creates TicketEvents per the `assignment` event type contract |
| `docs/data-model.md` | User and UserRole table definitions |
