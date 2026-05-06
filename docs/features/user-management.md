# User Management

## Purpose

Enable administrators to manage user accounts via the CLI and the
administration panel in the web UI. This spec covers both local users
(managed directly in Sentinel's database) and SSO users (synced from
SUSE Active Directory via LDAP).

Local users serve three primary use cases:

1. **Development and staging environments**: testing the full
   application workflow without depending on the SUSE internal network
2. **AI agents and automation**: dedicated accounts for AI agents or
   bots that operate as independent identities with their own audit
   trail (see `docs/features/authentication.md`, Use Cases: Bots and
   AI Agents)
3. **Environments without SSO**: deployments outside the SUSE corporate
   network where `id.suse.com` is not reachable

SSO users are provisioned and maintained by the LDAP sync process (see
`docs/features/ldap-directory.md`). Administrators can modify their
roles and deactivate/reactivate them, but cannot set passwords or create
them manually.

Local users are created directly in the database, bypassing the LDAP
sync process. They are functionally identical to LDAP-synced users for
the purposes of authorization, ticket assignment, and API key
management. The only difference is how they authenticate: local
credentials instead of SSO (see
`docs/features/local-authentication.md`).

## CLI Commands

All commands are subcommands of the `sentinel manage-user` group. See
`docs/conventions.md` (CLI Conventions) for general CLI design
guidelines.

These commands require direct shell access to the host or container.
There are no unauthenticated HTTP endpoints for user management.

### `sentinel manage-user create`

Creates a new local user account with a password.

```
sentinel manage-user create \
  --username <username> \
  --email <email> \
  [--full-name <name>] \
  [--role <role>] ...
```

**Parameters**:

| Parameter      | Required | Repeatable | Description                                |
|----------------|----------|------------|--------------------------------------------|
| `--username`   | Yes      | No         | Unique username for the account             |
| `--email`      | Yes      | No         | Unique email address                        |
| `--full-name`  | No       | No         | Display name                                |
| `--role`       | No       | Yes        | Role to assign: `admin`, `vulnerability_analyst` |

The password is collected interactively via a hidden prompt (input is not
echoed to the terminal, like `sudo`). The prompt asks for the password
twice for confirmation. If the two entries do not match, the command exits
with error: `"Error: Passwords do not match."` (exit code 1). This
command cannot be used non-interactively — a TTY is required.

**Behavior**:

1. Validates username format: must be 1-64 characters, start with a
   letter, and contain only lowercase letters, numbers, dots, hyphens,
   and underscores (`[a-z0-9._-]`). If invalid, exits with error:
   `"Error: Invalid username '{value}'. Username must be 1-64 characters,
   start with a letter, and contain only lowercase letters, numbers, dots,
   hyphens, and underscores."`
2. For each `--role` provided, validates that it is a recognized role. If
   not, exits with error:
   `"Error: Invalid role '{value}'. Valid roles are: {list}."`
   The list of valid roles is derived from the system's role definitions
   at runtime
3. Validates email format — if the provided email is not syntactically
   valid, exits with error:
   `"Error: Invalid email format '{value}'."`
4. Validates password: 12–128 characters. If too short, exits with
   error: `"Error: Password must be at least 12 characters."` If too
   long, exits with error:
   `"Error: Password must be at most 128 characters."`
5. Delegates to `user_service.create_user()` with:
   - `ldap_uid = None` (local user)
   - `active = True`
   - `password` = provided password (service handles hashing)
   - `roles = [(role, '_manual') for role in provided_roles]`
   - `acting_user_id = None` (CLI action)
   - See `docs/features/user-lifecycle.md` for the service contract
6. If the service raises `UserConflictError` (duplicate username or
   email), exits with error:
   `"Error: A user with username '{username}' already exists."` or
   `"Error: A user with email '{email}' already exists."`
7. Prints confirmation:
   `"Created user '{username}' ({email}) with roles: {roles}."`
   or `"Created user '{username}' ({email}) with no roles."` if no roles
   were specified

**Idempotency**: Not idempotent (interactive). Each invocation collects a
new password interactively; the operation inherently changes state.

**Exit codes**: 0 on success, 1 on validation error (duplicate user,
invalid role, missing flag), 2 on system error (database unreachable).

**Output channels**: confirmation message to stdout, all `"Error: ..."`
messages to stderr.

### `sentinel manage-user update`

Updates an existing user account. This command works on any user (local
or LDAP-synced), regardless of whether the user is currently active or
inactive. Modifications to email, full_name, and roles are permitted on
inactive users — this allows admins to prepare an account (e.g., assign
appropriate roles) before reactivating it.

```
sentinel manage-user update \
  --username <username> \
  [--email <new_email>] \
  [--full-name <new_name>] \
  [--add-role <role>] ... \
  [--remove-role <role>] ... \
  [--reactivate]
```

**Parameters**:

| Parameter        | Required | Repeatable | Description                                |
|------------------|----------|------------|--------------------------------------------|
| `--username`     | Yes      | No         | Username of the user to update (identifier) |
| `--email`        | No       | No         | New email address                           |
| `--full-name`    | No       | No         | New display name                            |
| `--add-role`     | No       | Yes        | Role to add: `admin`, `vulnerability_analyst` |
| `--remove-role`  | No       | Yes        | Role to remove: `admin`, `vulnerability_analyst` |
| `--reactivate`   | No       | No         | Reactivate a previously deactivated user    |

**Behavior**:

1. Looks up the user by `username` — if not found, exits with error:
   `"Error: User '{username}' not found."`
2. If no modification flags are provided (`--email`, `--full-name`,
   `--add-role`, `--remove-role`, `--reactivate` are all absent), prints:
   `"No changes specified for user '{username}'."` and exits with code 0
3. If any role appears in both `--add-role` and `--remove-role`, exits
   with error:
   `"Error: Role '{role}' cannot be both added and removed in the same
   invocation."`
4. For each role value in `--add-role` and `--remove-role`, validates that
   it is a recognized role. If not, exits with error:
   `"Error: Invalid role '{value}'. Valid roles are: {list}."`
   The list of valid roles is derived from the system's role definitions
   at runtime
5. If `--email` is provided, validates email format — if not
   syntactically valid, exits with error:
   `"Error: Invalid email format '{value}'."`
6. If `--email` or `--full-name` is provided, delegates to
   `user_service.update_user()` with `acting_user_id = None`. If the
   service raises `UserConflictError` (duplicate email), exits with error
7. For role changes (`--add-role`, `--remove-role`), delegates to
   `user_service.update_roles()` with `acting_user_id = None` and
   roles as `(role, '_manual')` pairs. The service handles validation
   (AD-derived role protection). Since `acting_user_id = None`, the
   self-removal guard does not apply (CLI is a system action)
8. If `--reactivate` is provided: delegates to
   `user_service.reactivate_user()` with `acting_user_id = None`. If
   the user is already active, this is a no-op. See
   `docs/features/user-lifecycle.md` for reactivation semantics.
   Reactivation is intentionally the LAST mutation step so that the
   account is fully configured (correct email, roles, etc.) before
   becoming active again
9. Prints summary of changes. The summary lists only changes that were
   actually applied (not no-ops). If all requested operations resulted
   in no-ops (e.g., reactivating an already-active user, adding a role
   the user already has, removing a role the user does not have), prints:
   `"No changes applied to user '{username}'."` and exits with code 0.
   Otherwise prints:
   `"Updated user '{username}': {list of actual changes}."`

**Error handling (fail-fast)**: steps 6–8 are executed sequentially. If
any step fails, the command exits immediately (exit code 1) WITHOUT
attempting subsequent steps. The error message MUST clearly report:

- Which operations completed successfully (prefix `✓`)
- Which operation failed and why (prefix `✗`)
- Which operations were not attempted due to the failure (prefix `—`)

Example output on partial failure:

```
✓ Email updated to new@example.com
✗ Role update failed: role 'nonexistent' does not exist
— Reactivation not attempted (aborted due to previous error)
```

This ensures the admin knows exactly what state the account is in after
a partial failure and can re-run the command with corrected arguments for
the remaining operations.

**Idempotency**: Idempotent. If all requested operations result in no-ops
(state already reached), the command prints an informational message and
exits with code 0.

**Exit codes**: 0 on success (including no-op), 1 on validation or
operational error, 2 on system error (database unreachable).

**Output channels**: structured step report (`✓`/`✗`/`—`) and success
messages to stdout. All `"Error: ..."` messages to stderr.

### `sentinel manage-user deactivate`

Deactivates a user account (soft delete only).

```
sentinel manage-user deactivate \
  --username <username> \
  [--yes]
```

**Parameters**:

| Parameter    | Required | Description                                    |
|--------------|----------|------------------------------------------------|
| `--username` | Yes      | Username of the user to deactivate              |
| `--yes`      | No       | Skip interactive confirmation prompt            |

**Behavior**:

1. Looks up the user by `username` — if not found, exits with error:
   `"Error: User '{username}' not found."`
2. If the user is already inactive, prints:
   `"User '{username}' is already inactive."` and exits with code 0
3. Queries the impact of deactivation:
   - Count of active API keys that will be revoked
   - Count of active sessions that will be invalidated
   - Count of open tickets assigned to the user, and the reassignment
     target (manager username if eligible, otherwise "unassigned")
   - Whether this user is the last active user with the Admin role
4. Displays the impact summary:
   ```
   About to deactivate user '{username}':
     - {n} active API keys will be revoked
     - {n} active sessions will be invalidated
     - {n} open tickets will be reassigned to '{manager}' (manager)
   ```
   If tickets will be unassigned (no eligible manager):
   ```
     - {n} open tickets will be unassigned (no eligible manager)
   ```
   If the user is the last active admin, appends a warning to stderr:
   ```
   WARNING: this is the last active user with Admin role.
   After deactivation, assign Admin to another user via:
     sentinel manage-user update --username <user> --add-role admin
   ```
5. Unless `--yes` is passed, prompts: `Proceed? [y/N]`
   - If the user answers anything other than `y` or `Y`, exits with
     code 0 without deactivating
6. Delegates to `user_service.deactivate_user()` with
   `acting_user_id = None` and
   `reason = "deactivated via CLI (manage-user deactivate)"`
7. Prints: `"Deactivated user '{username}'."`

This command does not permanently remove the user record from the
database. The User record is preserved to maintain referential integrity
with TicketEvent, ticket assignments, and UserRole audit data. This is
consistent with LDAP sync deactivation behavior. For full database
cleanup in development environments, reset the database directly.

**Inactive user management principle**: deactivation blocks login and
revokes active sessions/keys, but does not prevent administrative
modifications to the account. All management operations (update profile,
set password, modify roles) remain available on inactive users via both
CLI and API. This allows admins to prepare accounts before reactivation
(e.g., assign appropriate roles, set a new password).

**Idempotency**: Idempotent. If the user is already inactive, the
command prints an informational message and exits with code 0.

**Exit codes**: 0 on success (including no-op and user-cancelled
confirmation), 1 on validation error, 2 on system error (database
unreachable).

**Output channels**: impact summary and confirmation to stdout.
`"Error: ..."` messages and `"WARNING: ..."` (last admin) to stderr.

### `sentinel manage-user set-password`

Sets or resets the password for a local user. See
`docs/features/local-authentication.md` for full details on password
policy and hashing.

```
sentinel manage-user set-password \
  --username <username>
```

The new password is collected interactively via a hidden prompt (input is
not echoed to the terminal, like `sudo`). The prompt asks for the
password twice for confirmation. If the two entries do not match, the
command exits with error: `"Error: Passwords do not match."` (exit
code 1). This command cannot be used non-interactively — a TTY is
required.

This command is only valid for local users (`ldap_uid = NULL`). If
invoked on an SSO user, exits with error:
`"Error: Cannot set password for SSO user '{username}'. SSO users
authenticate via id.suse.com."` (exit code 1)

This command operates on both active and inactive local users. Setting a
password on an inactive user prepares credentials for reactivation — the
user will not be able to log in until reactivated.

It invalidates all active sessions after changing the password.

On success, prints to stdout:
`"Password updated for user '{username}'. All active sessions invalidated."`

**Idempotency**: Not idempotent (interactive). Each invocation collects a
new password interactively; the operation inherently changes state.

**Exit codes**: 0 on success, 1 on validation error (user not found,
SSO user, passwords don't match, password policy violation), 2 on system
error (database unreachable).

**Output channels**: confirmation message to stdout. All `"Error: ..."`
messages to stderr.

### `sentinel manage-user unlock`

Clears the login lockout counter for a user, allowing them to log in
immediately without waiting for the TTL to expire.

```
sentinel manage-user unlock \
  --username <username>
```

**Parameters**:

| Parameter    | Required | Description                              |
|--------------|----------|------------------------------------------|
| `--username` | Yes      | Username of the user to unlock           |

**Behavior**:

1. Normalize the username (trim whitespace, lowercase)
2. Look up the user by normalized username in the database — if not
   found, exit with error:
   `"Error: User '{username}' not found."` (exit code 1)
3. If the user is inactive, print a warning to stderr:
   `"Warning: User '{username}' is inactive. Unlock has no practical
   effect until the user is reactivated."` — then continue (do not
   abort)
4. If the user is an SSO user (`ldap_uid IS NOT NULL`), print a warning
   to stderr:
   `"Warning: User '{username}' is an SSO user. Local login lockout
   does not apply to SSO authentication."` — then continue (do not
   abort)
5. Delete the Redis key `login_attempts:{normalized_username}`
6. If Redis is unreachable, exit with error:
   `"Error: Could not connect to Redis. Lockout state cannot be cleared."`
   (exit code 2)
7. Log the action at INFO level: target username and timestamp (CLI
   operations do not have an acting user identity — shell access is the
   implicit authorization)
8. Print: `"Unlocked user '{username}'."`

The command is idempotent: if the counter does not exist (user was not
locked), it succeeds silently.

**Idempotency**: Idempotent. If the user is not locked, the command
succeeds with a no-op and exits with code 0.

**Exit codes**: 0 on success (including no-op), 1 on validation error
(user not found), 2 on system error (Redis unreachable).

**Output channels**: confirmation to stdout. `"Warning: ..."` messages
to stderr. `"Error: ..."` messages to stderr.

## Administration UI

Administrators can manage all users (local and SSO) from a dedicated
page in the web UI administration panel.

### User list page

Displays all users (local and SSO) with columns:

- Username
- Full name
- Email
- Type (Local / SSO)
- Status (Active / Inactive)
- Roles — each role displays badge(s) indicating its origin(s): "Manual",
  AD group name, or both. See `docs/features/rbac.md` (Role Origins and
  Coexistence) for the coexistence semantics and UI representation

Filters: by type (local/SSO), by status (active/inactive), by role.

### Actions available for local users

- **Edit**: change email, full name, roles
- **Reset password**: set a new password for a local user (see
  "Reset password flow" below)
- **Unlock**: clear the login lockout counter (same as CLI `unlock`)
- **Deactivate**: soft delete (same as CLI `deactivate`). Shows a
  confirmation dialog before proceeding (see "Deactivation confirmation
  dialog" below)
- **Reactivate**: restore a deactivated user

Note: local user creation is restricted to the CLI
(`sentinel manage-user create`). This ensures that user provisioning
requires shell access, which is an appropriate security barrier for the
supported use cases (development, bots, non-SSO environments). See
`docs/features/ldap-directory.md` Business Rule 1.

### Actions available for SSO users

- **Edit roles**: add/remove roles
- **Deactivate**: soft delete. Shows a confirmation dialog before
  proceeding (see "Deactivation confirmation dialog" below)
- **Reactivate**: restore

SSO users cannot have their password set or reset (they authenticate
via id.suse.com).

### Deactivation confirmation dialog

When an admin clicks "Deactivate" for any user (local or SSO), the
frontend calls `GET /api/v1/admin/users/{user_id}/deactivation-impact`
and displays a confirmation dialog with the impact summary before
calling `PATCH /api/v1/admin/users/{user_id}/active`.

The dialog displays:

```
Deactivate user '{username}'?

This action will:
  - Revoke {n} active API keys
  - Invalidate {n} active sessions
  - Reassign {n} open tickets to '{manager}'
    (or: Leave {n} open tickets unassigned — no eligible manager)

[Cancel]  [Deactivate]
```

If all counts are zero, the dialog simplifies to:

```
Deactivate user '{username}'?

No active API keys, sessions, or assigned tickets.

[Cancel]  [Deactivate]
```

The "Deactivate" button uses a destructive style (red) to signal the
severity of the action.

### Reset password flow

When an admin clicks "Reset password" on a local user, the UI presents
an inline form (or modal) with the following fields:

1. **New password** (required, masked input)
2. **Confirm password** (required, masked input)

**Client-side validation**:
- Both fields must be non-empty
- Both fields must match — if they do not, display an inline error:
  "Passwords do not match." The submit button remains disabled until
  the fields match
- Password length must be 12–128 characters (consistent with
  `docs/features/local-authentication.md`)

**On submit**: call `PUT /api/v1/admin/users/{user_id}/password` with
the new password. On success, display a confirmation message:
"Password updated. All active sessions for this user have been
invalidated."

**Error handling**:
- HTTP 400 (invalid password): display the server error message inline
- HTTP 400 (SSO user): this case should not occur since the button is
  only shown for local users, but if it does, display the error

The "Reset password" button is only visible for local users
(`ldap_uid = NULL`). It is never shown for SSO users.

### Admin API endpoints

All user mutation endpoints are defined here. This is the single source
of truth for the user management API surface. Other specs define the
business rules and service-layer contracts that these endpoints invoke.

All endpoints below require the `admin` role unless otherwise stated.

#### `PATCH /api/v1/admin/users/{user_id}`

Update a user's profile fields. This endpoint operates on both active and
inactive users (see "Inactive user management principle" above).

**Request body** (all fields optional, at least one required):

```json
{
  "email": "new@example.com",
  "full_name": "New Display Name"
}
```

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404
2. If no fields are provided in the body, return HTTP 422:
   `"At least one field must be provided."`
3. If `email` is provided, validate format — if invalid, return HTTP 422:
   `"Invalid email format."`
4. Delegate to `user_service.update_user()` with
   `acting_user_id = authenticated_admin.id`
5. If the service raises `UserConflictError` (duplicate email), return
   HTTP 409: `"A user with this email already exists."`
6. Return HTTP 200 with the updated user profile

**Response**: same schema as `GET /api/v1/users/{id}`

#### `PUT /api/v1/admin/users/{user_id}/roles`

Add or remove manual roles for a user.

**Request body**:

```json
{
  "add": ["admin"],
  "remove": ["vulnerability_analyst"]
}
```

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404
2. Delegate to `user_service.update_roles()` with
   `acting_user_id = authenticated_admin.id` and roles as
   `(role, '_manual')` pairs

**Validation rules**:
- Cannot remove roles with `ad_group_cn != '_manual'` — returns HTTP 400:
  `"Cannot remove AD-derived role '{role}'. This role is managed by the
  AD group '{ad_group_cn}'."`
- Cannot remove your own Admin role — returns HTTP 409:
  `"Cannot remove your own Admin role."` (enforced by
  `user_service.update_roles()` — see `docs/features/user-lifecycle.md`)
- Adding a role that the user already has as a manual assignment is a
   no-op (idempotent)
- Adding a role that the user already holds via AD derivation creates a
  separate `_manual` record — both origins coexist independently. See
  `docs/features/rbac.md` (Role Origins and Coexistence) for full
  semantics
- Creates a `UserRole` record with `ad_group_cn = '_manual'` and
  `assigned_by` set to the authenticated admin's user ID for each added
  role

**Response**: HTTP 200 with updated user profile including all roles

#### `PUT /api/v1/admin/users/{user_id}/password`

Reset the password for a local user. This endpoint operates on both
active and inactive local users (see "Inactive user management principle"
above). Setting a password on an inactive user prepares credentials for
reactivation.

**Request body**:

```json
{
  "password": "string (required, 12-128 chars)"
}
```

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404
2. Delegate to `user_service.reset_password(user_id, password,
   acting_user_id=authenticated_admin.id)` — this handles SSO user
   check, validation, hashing, and session invalidation (see
   `docs/features/user-lifecycle.md`)
3. Log the operation at INFO level: admin identity (user_id, username)
   and target user (user_id, username)
4. Return HTTP 200

**Error responses**:
- SSO user: HTTP 400 — `"Cannot set password for SSO user. SSO users
  authenticate via id.suse.com."`
- Invalid password: HTTP 400 — `"Password must be between 12 and 128
  characters."`

**Response** (200):

```json
{
  "detail": "Password updated. All active sessions have been invalidated."
}
```

#### `PATCH /api/v1/admin/users/{user_id}/active`

Set the active status of a user (deactivate or reactivate).

**Request body**:

```json
{
  "active": true
}
```

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404
2. If `active: false`: delegate to `user_service.deactivate_user()` with
   `acting_user_id = authenticated_admin.id` and
   `reason = "deactivated by admin via API"`
3. If `active: true`: delegate to `user_service.reactivate_user()` with
   `acting_user_id = authenticated_admin.id`
4. Return HTTP 200 with the updated user profile

**Constraints**:
- Self-deactivation is rejected by the service layer — returns HTTP 409:
  `"Cannot deactivate your own account."`
- Setting the same value as current is a no-op (returns 200 with
  unchanged user)

See `docs/features/user-lifecycle.md` for the full side effect contract
(API key revocation, session invalidation, ticket reassignment on
deactivation).

**Response**: same schema as `GET /api/v1/users/{id}`

#### `GET /api/v1/admin/users/{user_id}/deactivation-impact`

Returns a preview of the side effects that would occur if the user were
deactivated. Used by the frontend to display a confirmation dialog before
proceeding with deactivation.

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404
2. If the user is already inactive, return HTTP 409:
   `"User is already inactive."`
3. Query and return the impact summary

**Response** (HTTP 200):

```json
{
  "api_keys_count": 3,
  "sessions_count": 2,
  "tickets_count": 5,
  "reassignment_target": "luigi.verdi"
}
```

| Field                  | Type          | Description                                      |
|------------------------|---------------|--------------------------------------------------|
| `api_keys_count`       | `int`         | Active API keys that will be revoked             |
| `sessions_count`       | `int`         | Active sessions that will be invalidated         |
| `tickets_count`        | `int`         | Open tickets assigned to this user               |
| `reassignment_target`  | `str \| null` | Manager username if eligible for reassignment, otherwise `null` (tickets will be unassigned) |

**Semantics**: this endpoint returns a point-in-time snapshot of the
user's current state. The response is purely informational — the
subsequent `PATCH .../active` call does not verify whether the impact has
changed since the preview was fetched. Between viewing the preview and
confirming the deactivation, new resources may have been assigned to the
user (tickets, API keys, sessions). The deactivation proceeds regardless
and affects all resources at execution time, not only those shown in the
preview.

**Authorization**: requires `admin` role.

#### `POST /api/v1/admin/users/{user_id}/unlock`

Clear the login lockout counter for a user.

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404
2. Normalize the user's `username` (trim, lowercase)
3. Delete the Redis key `login_attempts:{normalized_username}`
4. Log the action at INFO level: admin identity (user ID and username of
   the acting admin), target user (user ID and username), and timestamp
5. Return HTTP 204 (no content)

The endpoint is idempotent: if the user is not locked, it returns 204
without error. The log entry is emitted regardless (to record that an
admin attempted to unlock).

If Redis is unreachable, return HTTP 503 with message:
`"Lockout service unavailable."`

## Interaction with LDAP Sync

The `sync_ldap_directory` fetcher operates exclusively on users with
`ldap_uid IS NOT NULL`. Local users (`ldap_uid = NULL`) are invisible to
the sync process:

- They are never deactivated by the sync
- They are never updated with AD data
- They are never assigned AD-derived roles

This separation is inherent in the existing sync algorithm — no special
handling is required.

## Business Rules

1. **Local users are identified by `ldap_uid = NULL`**: this is the
   canonical way to distinguish local users from LDAP-synced users. No
   additional flag or column is needed
2. **No "last admin" enforcement**: the system does not enforce a
   minimum admin count. However, via UI/API it is practically impossible
   for administrators to accidentally eliminate all admins — the
   self-removal guard (see `docs/features/rbac.md`, Business Rule 1)
   prevents any admin from removing their own Admin role, so at least
   the acting admin always retains the role. Via CLI or system
   operations (`acting_user_id = None`), the self-removal guard does
   not apply, and it is possible to remove or deactivate even the last
   admin. This is intentional and non-problematic: the platform
   continues to function normally without active admin users (all
   non-admin features remain operational). In these rare cases, a
   system administrator with shell access can restore admin access via:
   `sentinel manage-user update --username <user> --add-role admin`.
   This is consistent with the LDAP sync behavior (see
   `docs/features/ldap-directory.md`, Business Rule 6)
3. **No duplicate usernames or emails**: enforced at creation and when
   changing the email
4. **Role origin is `_manual`**: all roles assigned via `manage-user`
   commands or admin UI have `ad_group_cn = '_manual'` and
   `assigned_by = NULL` (CLI) or `assigned_by = admin_user_id` (UI)
5. **Password required at creation**: local users must have a password
   set at creation time. There is no passwordless local user state.
   This invariant is enforced at the database level by a CHECK
   constraint (see `docs/data-model.md`, `chk_user_auth_exclusive`)

## Security Considerations

- **CLI access requires shell access**: the `manage-user` commands
  require direct access to the host or container. There are no
  unauthenticated HTTP endpoints for user management
- **Passwords are never CLI arguments**: the `create` and `set-password`
  commands collect passwords via hidden interactive prompts. This
  prevents exposure in process listings (`ps aux`) and shell history
  files. A TTY is required — these commands cannot be scripted
- **Admin UI is authenticated and role-protected**: only users with the
  `admin` role can access the user management pages
- **Password policy**: minimum 12 characters, no complexity rules.
  Length is the primary defense (see
  `docs/features/local-authentication.md`)
- **Audit trail**: user creation, role changes, and deactivation
  produce `TicketEvent` records where relevant (e.g., ticket
  reassignment on deactivation)
- **Admin password reset is logged**: every admin-initiated password
  reset is logged at INFO level with the acting admin's identity and
  the target user. No rate limiting or step-up authentication is applied
  — the admin role is the highest trust level in the system, and
  additional friction would not meaningfully improve security given that
  a compromised admin already has full system access

## Cross-references

- `docs/features/authentication.md` — authentication framework, API
  keys, session management
- `docs/features/local-authentication.md` — login endpoint, password
  hashing, rate limiting
- `docs/features/user-lifecycle.md` — service contract for create,
  update, deactivate, reactivate
- `docs/features/rbac.md` — role definitions and permission model
- `docs/features/ldap-directory.md` — LDAP sync (manages SSO users)
