# User Lifecycle Service

## Purpose

Centralize all user lifecycle operations (creation, modification,
deactivation, reactivation, role management) in a single service module
to ensure consistent enforcement of business rules and side effects
regardless of the entry point (API, CLI, LDAP sync, or future
integrations).

Without this centralization, each entry point would need to independently
implement side effects (ticket reassignment, API key revocation,
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

## Operations

### `create_user()`

Creates a new User record with optional initial roles.

**Parameters**:

| Parameter      | Type                        | Required | Description                          |
|----------------|-----------------------------|----------|--------------------------------------|
| `username`     | `str`                       | Yes      | Unique username                      |
| `email`        | `str`                       | Yes      | Unique email address                 |
| `full_name`    | `str \| None`               | No       | Display name                         |
| `active`       | `bool`                      | No       | Default: `True`                      |
| `ldap_uid`     | `str \| None`               | No       | AD sAMAccountName. NULL for local users |
| `ldap_dn`      | `str \| None`               | No       | Full AD distinguished name           |
| `manager_uid`  | `str \| None`               | No       | ldap_uid of the direct line manager  |
| `password`     | `str \| None`               | No       | Plain-text password (hashed before storage). Required for local users, must be NULL for LDAP users |
| `roles`        | `list[tuple[Role, str]]`    | No       | List of (role, ad_group_cn) pairs    |
| `acting_user_id` | `UUID \| None`            | No       | Who is performing the action         |

**Behavior**:

1. Validate uniqueness of `username` and `email` across all users
   (including inactive). If violated, raise `UserConflictError`
2. If `password` is provided, hash it with Argon2id (see
   `docs/features/local-authentication.md` for hashing parameters)
3. Create User record with provided fields,
   `password_hash` set to the hash (or NULL if no password), and
   `ldap_synced_at = now()` if `ldap_uid` is set
4. For each role in `roles`, create UserRole with specified `ad_group_cn`
   and `assigned_by = acting_user_id`
5. Return the created User

**TicketEvent**: none (user creation does not affect tickets)

### `update_user()`

Updates mutable user identity fields. This operation does NOT cover role
changes or active status changes — those have dedicated operations with
their own business rules.

**Parameters**:

| Parameter      | Type                        | Required | Description                          |
|----------------|-----------------------------|----------|--------------------------------------|
| `user_id`      | `UUID`                      | Yes      | User to update                       |
| `acting_user_id` | `UUID \| None`            | No       | Who is performing the action         |
| `email`        | `str \| None`               | No       | New email (uniqueness validated)     |
| `full_name`    | `str \| None`               | No       | New display name                     |
| `manager_uid`  | `str \| None`               | No       | New manager ldap_uid                 |
| `ldap_dn`      | `str \| None`               | No       | Updated AD distinguished name        |
| `ldap_synced_at` | `datetime \| None`        | No       | Sync timestamp                       |

**Behavior**:

1. Look up user by ID. If not found, raise `UserNotFoundError`
2. If `email` is provided, validate uniqueness. If violated, raise
   `UserConflictError`
3. Apply provided field updates (only non-None parameters are applied)
4. Return updated User

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
   `acting_user_id == user_id` AND `Admin` is in the `remove` list,
   reject the operation with `SelfRoleRemovalError`. This prevents any
   authenticated user from removing their own Admin role, regardless of
   the entry point. System actions (`acting_user_id = None`) are exempt
2. **AD-derived role protection**: when `acting_user_id` is set (user
   action), cannot remove roles with `ad_group_cn != '_manual'`. Raise
   `ADDerivedRoleError`. System actions are exempt (LDAP sync must be
   able to remove AD-derived roles when group membership changes)
3. **Idempotency**: adding a role already present for the same
   (user_id, role, ad_group_cn) combination is a no-op. Removing a role
   not present is a no-op

**Behavior**:

1. Look up user by ID. If not found, raise `UserNotFoundError`
2. Validate business rules (self-removal guard, AD-derived protection)
3. For each entry in `add`, create UserRole if not already present, with
   `assigned_by = acting_user_id`
4. For each entry in `remove`, delete matching UserRole record
5. Return updated User with current roles

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
- **Self-deactivation guard**: if `acting_user_id` is not None AND
  `acting_user_id == user_id`, reject with `SelfDeactivationError`

**Side effects** (executed atomically in the same database transaction,
in this specific order):

1. Revoke all API keys belonging to this user: set `revoked_at = now()`
   and `revoked_by = NULL` (system action) on all active keys. Keys are
   not deleted — preserves audit trail. See
   `docs/features/authentication.md` (API Keys) for the data model.
2. Invalidate all active sessions for this user via
   `session_service.invalidate_user_sessions(db, user_id)`. This sets
   `Session.is_active = false` in the database AND deletes the
   corresponding Redis cache entries. See
   `docs/features/authentication.md` (Session invalidation) for the
   session service contract.
3. Set `User.active = false`
4. Reassign open tickets: for each ticket where `assignee_id` points to
   the deactivated user:
   - Attempt reassignment to the user identified by `manager_uid`
   - The manager is eligible if ALL conditions are met:
     - `manager_uid` is not NULL
     - Manager user exists and is active
     - Manager holds the Vulnerability Analyst role
   - If the manager is not eligible, set `assignee_id = NULL`
     (unassigned)
   - Create a `TicketEvent` of type `assignment` with:
     - `user_id = NULL` (system action)
     - `old_value` = deactivated user's username
     - `new_value` = manager's username (if reassigned) or `NULL`
       (if unassigned)
     - `comment` = `"Reassigned from {old} to {new}: {reason}"` or
       `"Unassigned from {old}: {reason}, no eligible manager"`

**Ordering rationale**: API keys and sessions are revoked BEFORE the
user is marked as inactive. This ensures that if the process is
interrupted at any point, a user who still appears active will have
already lost access. The admin can safely retry the deactivation
without risk of leaving a deactivated user with valid credentials.

**TicketEvent**: yes — one `assignment` event per reassigned ticket (see
`docs/features/ticket-history.md` for the event type contract)

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

**Behavior**:

1. Set `User.active = true`
2. Return updated User

**Explicitly NOT restored**:

- Previously reassigned tickets are NOT returned to the user
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
- User must be a local user (`ldap_uid IS NULL`). If `ldap_uid` is set,
  raise `SSOUserPasswordError`: "Cannot set password for SSO user. SSO
  users authenticate via id.suse.com."

**Behavior**:

1. Validate password length (12–128 characters). If invalid, raise
   `PasswordValidationError`
2. Hash the password with Argon2id (see
   `docs/features/local-authentication.md` for hashing parameters)
3. Update `User.password_hash` with the new hash
4. Invalidate all active sessions via
   `session_service.invalidate_user_sessions(db, user_id)` — this
   forces re-login with the new password
5. Return updated User

**TicketEvent**: none (password reset does not affect tickets)

## Transactionality

All operations that produce side effects (particularly `deactivate_user`)
MUST execute within a single database transaction. If any step fails, the
entire operation is rolled back. This ensures that a user is never left in
a partially-deactivated state (e.g., marked inactive but tickets not
reassigned).

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

### Batch deactivation cascading (LDAP sync)

When multiple users are deactivated in the same LDAP sync run and one
user's manager is also in the `newly_deactivated` list, the following
may occur: user A's tickets are reassigned to manager B, then manager B
is deactivated and B's tickets (including those just received from A) are
reassigned to B's manager or set to unassigned.

This produces a correct final state but generates intermediate
TicketEvents. This is acceptable — the audit trail accurately reflects
the sequence of operations. Implementers SHOULD NOT attempt to optimize
this case by pre-computing the deactivation graph, as the added
complexity is not justified for the rare scenario (~20 users max per
threshold).

### Role modification during deactivation

`update_roles()` does not check whether the target user is active. Adding
roles to an inactive user is permitted — it has no immediate effect but
prepares the user for reactivation. This is intentional: an admin may
want to adjust a user's roles before reactivating them.

## Error Handling

| Error                    | Condition                                    | API mapping | CLI mapping          |
|--------------------------|----------------------------------------------|-------------|----------------------|
| `UserNotFoundError`      | User ID does not exist                       | 404         | Exit 1, stderr       |
| `UserConflictError`      | Duplicate username or email                  | 409         | Exit 1, stderr       |
| `SelfRoleRemovalError`   | User attempting to remove own Admin role      | 409         | Exit 1, stderr       |
| `SelfDeactivationError`  | User attempting to deactivate themselves       | 409         | Exit 1, stderr       |
| `ADDerivedRoleError`     | Attempting to remove AD-derived role via user action | 400   | Exit 1, stderr       |
| `SSOUserPasswordError`   | Attempting to set password for an SSO user    | 400         | Exit 1, stderr       |
| `PasswordValidationError`| Password does not meet length requirements (12–128) | 400  | Exit 1, stderr       |

## Relationship to Other Specifications

| Spec | Relationship |
|---|---|
| `docs/features/authentication.md` | Defines API key model, session model, and `session_service`. `deactivate_user` revokes keys and calls `session_service.invalidate_user_sessions()`. `reset_password` calls the same. |
| `docs/features/ldap-directory.md` | LDAP sync fetcher calls `create_user`, `update_user`, `update_roles`, `deactivate_user`, `reactivate_user` for each synced employee |
| `docs/features/rbac.md` | Admin API endpoints delegate to `update_roles`, `deactivate_user`, `reactivate_user` |
| `docs/features/local-user-management.md` | CLI commands delegate to `create_user`, `update_user`, `update_roles`, `deactivate_user`, `reactivate_user` |
| `docs/features/local-authentication.md` | Defines password management. `create_user` accepts an optional password. CLI `set-password` and admin endpoint delegate to `reset_password` |
| `docs/features/ticket-history.md` | `deactivate_user` creates TicketEvents per the `assignment` event type contract |
| `docs/data-model.md` | User and UserRole table definitions |
