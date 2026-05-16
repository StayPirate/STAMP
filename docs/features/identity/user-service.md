# User Lifecycle Service

## Purpose

Centralize all user lifecycle operations (creation, modification,
deactivation, reactivation, role management) in a single service module
to ensure consistent enforcement of business rules and side effects
regardless of the entry point (API, CLI, LDAP sync, or future
integrations).

Without this centralization, each entry point would need to independently
implement side effects (ticket unassignment, API key revocation,
TicketAuditEvent creation) and business rules (self-removal guard,
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
(`username`, `email`, `full_name`, `manager_id`,
`ad_synced_at`) are managed exclusively by the LDAP sync fetcher. No
human caller — whether via API or CLI — may modify these fields. The only
legitimate consumer of `update_user()` for AD users is the sync process
itself (`acting_user_id = None`).

Fields managed by dedicated operations have their own ownership rules:

- `active` — managed by `deactivate_user()` / `reactivate_user()`. For
  AD users, this field is managed exclusively by LDAP sync — manual
  deactivation/reactivation by admins is blocked (see AD Active Status
  Ownership below)
- Roles — managed by `update_roles()` for per-user assignment,
    `sync_role_mapping()` and `delete_role_mapping_roles()` for
    AD group mapping operations. Available to admins for manual roles
    (`ad_group_cn = '_manual'`)
- `password_hash` — managed by `reset_password()`, which independently
  blocks AD users via `ADUserPasswordError`

Conversely, for local users (`ad_object_guid IS NULL`), the
AD-specific fields (`manager_id`, `ad_synced_at`) are not
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

### Excluded fields

The `last_login_at` field is exempt from `user_service` centralization.
It is a login-path auditing timestamp managed directly by the
authentication layer (local login endpoint and SSO callback handler).
It has no business rules, no side effects, and no guards — routing it
through the service would add indirection with no value. See
`docs/features/identity/local-authentication.md` and
`docs/features/identity/sso-authentication.md` for where this field is
updated.

### Deactivation and management

Deactivation blocks login and revokes active sessions/keys, but does not
prevent administrative modifications to the account. All management
operations (`update_user`, `reset_password`, `update_roles`) remain
available on inactive users via both CLI and API. This allows admins to
prepare accounts before reactivation (e.g., assign appropriate roles, set
a new password).

## User Deletion

User deletion is not supported. Deactivation is the terminal state of the
user lifecycle. This is intentional: User records are referenced by
`TicketAuditEvent` (audit trail), `Ticket` (historical assignments), `ApiKey`
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
   For each UserRole created, also create an `IdentityAuditEvent` with
   `event_type = role_added` via `IdentityAuditLog.log_event()` —
   `user_id` = `acting_user_id`, `target_user_id` = created user,
   `new_value` = role name. This ensures initial role assignments are
   audited identically to later role changes via `update_roles()`.
8. Return the created User

**TicketAuditEvent**: none (user creation does not affect tickets)

**IdentityAuditEvent**: `user_created` — `user_id` = creating admin,
`target_user_id` = created user, `new_value` = username. Additionally,
one `role_added` event per initial role assigned (see step 7). All events
created via `IdentityAuditLog.log_event()` in the same transaction.

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
| `ad_synced_at` | `datetime \| None`          | No       | Sync timestamp                       |

**Behavior**:

1. Look up user by ID. If not found, raise `UserNotFoundError`
2. If `user.ad_object_guid IS NOT NULL` and `acting_user_id` is not None:
   raise `ADUserFieldReadOnlyError`. Identity fields of AD users are
   managed exclusively by directory sync (see AD User Data Ownership
   above). The entire `update_user()` operation is blocked for human
   callers on AD users — there is no identity field that an admin
   should modify manually.
3. If `user.ad_object_guid IS NULL` and `manager_id` or
   `ad_synced_at` is provided (not `_MISSING`): raise
   `ADUserFieldReadOnlyError`. These fields are AD-specific and have
   no source of truth for local users.
4. **Username validation** (if `username` is provided): normalize and
   validate the format per the rules in `docs/conventions.md` (section
   "Username Format"). If invalid, raise `UsernameFormatError`. Verify
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
    `manager_id`) may need to be explicitly cleared — e.g.,
   when LDAP sync discovers that an AD attribute has been removed. The
   pattern follows Python's standard sentinel convention
   (`dataclasses.MISSING`).

    If all optional parameters are `_MISSING`, this is a no-op: no UPDATE
   is issued. The User record returned is the one loaded in step 1.
7. For each changed field, create an `IdentityAuditEvent` via
   `IdentityAuditLog.log_event()`: `username_changed`, `email_changed`,
   `full_name_changed`, or `manager_changed` with `old_value` and
   `new_value`. One event per changed field, all in the same transaction.
8. Return updated User

**TicketAuditEvent**: none

**IdentityAuditEvent**: one per changed field (`username_changed`,
`email_changed`, `full_name_changed`, `manager_changed`). See
`docs/features/identity/identity-audit-log.md` for the event type
contract.

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
6. For each role added, create `IdentityAuditEvent` with `event_type =
   role_added`; for each role removed, `event_type = role_removed`. All
   events created via `IdentityAuditLog.log_event()` in the same
   transaction.
7. Return updated User with current roles

**TicketAuditEvent**: none (role changes do not directly affect tickets)

**IdentityAuditEvent**: `role_added` / `role_removed` per effective
change. See `docs/features/identity/identity-audit-log.md`.

### `sync_role_mapping()`

Synchronizes `UserRole` records for a specific role mapping against the
current set of AD group members. Creates missing records for users in
the group and removes records for users no longer in the group.

This function centralizes all bulk role operations triggered by AD group
membership. It is called by the LDAP sync fetcher (step 5) and by the
`POST /api/v1/admin/role-mappings` endpoint when a new mapping is
created.

**Parameters**:

| Parameter                 | Type            | Required | Description                          |
|---------------------------|-----------------|----------|--------------------------------------|
| `role`                    | `Role`          | Yes      | The role to sync                     |
| `ad_group_cn`             | `str`           | Yes      | The AD group CN that tags these roles |
| `current_member_user_ids` | `set[UUID]`     | Yes      | User IDs currently in the AD group   |
| `acting_user_id`          | `UUID \| None`  | No       | Who is performing the action         |

**Behavior**:

1. Query all existing `UserRole` records where `role` and `ad_group_cn`
   match the provided values. Collect their `user_id` values as
   `existing_user_ids`
2. Compute:
   - `to_add = current_member_user_ids - existing_user_ids`
   - `to_remove = existing_user_ids - current_member_user_ids`
3. **Self-admin guard**: if `acting_user_id` is not None, `role` is
   `Admin`, and `acting_user_id` is in `to_remove`: check whether the
   acting user has any other `UserRole` granting `Admin` (from a
   different `ad_group_cn` or from `_manual`). If not, reject with
   `SelfRoleRemovalError`
4. For each user in `to_add`, create `UserRole(user_id, role,
   ad_group_cn)` with `assigned_by = NULL` (AD-derived roles are
   system-assigned regardless of the initiator)
5. Delete all `UserRole` records where `user_id` is in `to_remove`,
   `role` matches, and `ad_group_cn` matches
6. For each user in `to_add`, create `IdentityAuditEvent` with
   `event_type = role_added`, `user_id = NULL` (system), `detail`
   including `{"source": "ad_sync", "mapping": "..."}`. For each user
   in `to_remove`, `event_type = role_removed` with same detail. All
   events via `IdentityAuditLog.log_event()`.
7. Return `(added_count, removed_count)`

**Idempotency**: calling this function twice with the same
`current_member_user_ids` produces the same result — the second call
finds nothing to add or remove. The UNIQUE constraint on
`(user_id, role, ad_group_cn)` prevents duplicate records.

**TicketAuditEvent**: none (role changes do not directly affect tickets)

**IdentityAuditEvent**: `role_added` / `role_removed` per effective
change, with `user_id = NULL` (AD sync, system action). See
`docs/features/identity/identity-audit-log.md`.

### `delete_role_mapping_roles()`

Removes all `UserRole` records associated with a specific role mapping.
Used when a role mapping is deleted via
`DELETE /api/v1/admin/role-mappings/{id}`.

**Parameters**:

| Parameter        | Type            | Required | Description                          |
|------------------|-----------------|----------|--------------------------------------|
| `role`           | `Role`          | Yes      | The role to remove                   |
| `ad_group_cn`    | `str`           | Yes      | The AD group CN that tags these roles |
| `acting_user_id` | `UUID \| None`  | No       | Who is performing the action         |

**Behavior**:

1. Query all `UserRole` records where `role` and `ad_group_cn` match.
   Collect their `user_id` values as `affected_user_ids`
2. **Self-admin guard**: if `acting_user_id` is not None, `role` is
   `Admin`, and `acting_user_id` is in `affected_user_ids`: check
   whether the acting user has any other `UserRole` granting `Admin`
   (from a different `ad_group_cn` or from `_manual`). If not, reject
   the entire operation with `SelfRoleRemovalError`: "Cannot delete
   this role mapping because it is the sole source of your admin role.
   Assign admin via another mapping or manually before retrying."
   No `UserRole` records are removed — the operation is atomic
3. Delete all matching `UserRole` records
4. For each removed `UserRole`, create `IdentityAuditEvent` with
   `event_type = role_removed` via `IdentityAuditLog.log_event()`
5. Return `affected_users_count`

**TicketAuditEvent**: none (role changes do not directly affect tickets)

**IdentityAuditEvent**: `role_removed` per affected user. See
`docs/features/identity/identity-audit-log.md`.

### `deactivate_user()`

Deactivates a user account and triggers all associated side effects.

**Parameters**:

| Parameter      | Type                        | Required | Description                          |
|----------------|-----------------------------|----------|--------------------------------------|
| `user_id`      | `UUID`                      | Yes      | User to deactivate                   |
| `acting_user_id` | `UUID \| None`            | No       | Who is performing the action         |
| `reason`       | `str`                       | Yes      | Human-readable reason (used in TicketAuditEvent comments) |

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

1. Revoke all API keys belonging to this user via
   `api_key_service.revoke_all_user_keys(session, user_id,
   acting_user_id=None)`. Keys are not deleted — preserves audit trail.
   See `docs/features/identity/api-key-service.md`.
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
   the TicketAuditEvent record. No attempt is made to reassign to the manager
   or any other user.
   Create a `TicketAuditEvent` of type
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

**IdentityAuditEvent**: `user_deactivated` — `user_id` = admin (or
`NULL` for AD sync), `target_user_id` = deactivated user, `detail`
includes reason. API key revocations produce individual
`api_key_revoked` events via `api_key_service`. See
`docs/features/identity/identity-audit-log.md`.

**TicketAuditEvent**: yes — one `assignment` event per unassigned ticket (see
`docs/features/tickets/ticket-audit-log.md` for the event type contract)

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
2. Create `IdentityAuditEvent` with `event_type = user_reactivated`
   via `IdentityAuditLog.log_event()`
3. Return updated User

**Explicitly NOT restored**:

- Previously unassigned tickets are NOT returned to the user
- Revoked API keys are NOT restored (user must create new ones manually)
- Role assignments are unchanged (roles are not affected by
  deactivation/reactivation)

**TicketAuditEvent**: none (reactivation is not a ticket mutation)

**IdentityAuditEvent**: `user_reactivated`. See
`docs/features/identity/identity-audit-log.md`.

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
6. Create `IdentityAuditEvent` with `event_type = password_reset` via
   `IdentityAuditLog.log_event()` — `user_id` = `acting_user_id`
   (admin), `target_user_id` = target user. Created in the same
   transaction as the password hash update.
7. Return updated User

**TicketAuditEvent**: none (password reset does not affect tickets)

**IdentityAuditEvent**: `password_reset` — `user_id` = acting admin,
`target_user_id` = target user. See
`docs/features/identity/identity-audit-log.md` for the event type
contract.

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
3. Log an INFO message: `"User '{username}' unlocked by
   {acting_user or 'system'}"`.

**Idempotency:** if the user is not currently locked out (Redis key
does not exist or counter is zero), the operation completes successfully
with no error. This is a no-op, not a failure.

**Exceptions:**

| Exception | Condition |
|-----------|-----------|
| `UserNotFoundError` | `user_id` does not match any user |

**Notes:**
- No `TicketAuditEvent` is created (not a ticket operation).
- No `IdentityAuditEvent` is created. Lockout is a transient
  Redis-only state, not a persistent identity mutation. Application
  logging (INFO level) provides sufficient operational visibility.
- No session invalidation (unlocking does not indicate compromise).
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
`update_roles`, `sync_role_mapping`, `delete_role_mapping_roles`,
`reactivate_user`) are also transactional but the
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
| `SelfRoleRemovalError` | Authenticated user attempts to remove their own Admin role — raised by `update_roles()`, `sync_role_mapping()`, and `delete_role_mapping_roles()` when the operation would leave the acting user without any Admin role source |
| `SelfDeactivationError` | Authenticated user attempts to deactivate themselves |
| `ADUserStatusReadOnlyError` | A human caller (`acting_user_id` is set) attempts to deactivate or reactivate an AD user (`ad_object_guid IS NOT NULL`) — active status is managed exclusively by directory sync |
| `ADDerivedRoleError` | Attempting to manually remove a role derived from AD group membership |
| `ADUserFieldReadOnlyError` | Raised in two cases: (1) a human caller (`acting_user_id` is set) attempts to call `update_user()` on an AD user — all identity fields are managed by directory sync; (2) any caller attempts to set AD-specific fields (`manager_id`, `ad_synced_at`) on a local user — these fields are not applicable |
| `ADUserPasswordError` | Attempting to set or reset password for an AD user |
| `PasswordValidationError` | Password does not meet length requirements (16–128 characters) |

## Relationship to Other Specifications

| Spec | Relationship |
|---|---|
| `docs/features/identity/api-key-service.md` | Centralized API key lifecycle service. `deactivate_user` calls `api_key_service.revoke_all_user_keys()` as step 1 of the deactivation side effects |
| `docs/features/identity/authentication.md` | Defines API key model, session model, and `session_service`. `deactivate_user` calls `session_service.invalidate_user_sessions()`. `reset_password` calls the same. |
| `docs/features/identity/ad-integration.md` | LDAP sync fetcher calls `create_user`, `update_user`, `sync_role_mapping`, `deactivate_user`, `reactivate_user`. Role mapping CRUD endpoints call `sync_role_mapping` and `delete_role_mapping_roles` |
| `docs/features/identity/rbac.md` | Admin API endpoints delegate to `update_roles`, `deactivate_user`, `reactivate_user` |
| `docs/features/identity/user-management.md` | CLI commands delegate to `create_user`, `update_user`, `update_roles`, `deactivate_user`, `reactivate_user` |
| `docs/features/identity/local-authentication.md` | Defines password management. `create_user` accepts an optional password. CLI `set-password` and admin endpoint delegate to `reset_password` |
| `docs/features/tickets/ticket-audit-log.md` | `deactivate_user` creates TicketAuditEvents per the `assignment` event type contract |
| `docs/features/identity/identity-audit-log.md` | All identity mutations create IdentityAuditEvents per the event type contract |
| `docs/features/platform/audit-trail-infrastructure.md` | BaseAuditLog, AuditEventMixin, naming conventions |
| `docs/data-model.md` | User and UserRole table definitions |
