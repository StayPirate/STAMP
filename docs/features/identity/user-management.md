# User Management

## Purpose

Enable administrators to manage user accounts via the CLI and the
administration panel in the web UI. This spec covers both local users
(managed directly in Sentinel's database) and AD users (synced from
SUSE Active Directory via LDAP).

Local users serve three primary use cases:

1. **Development and staging environments**: testing the full
   application workflow without depending on the SUSE internal network
2. **AI agents and automation**: dedicated accounts for AI agents or
   bots that operate as independent identities with their own audit
   trail (see `docs/features/identity/authentication.md`, Use Cases: Bots and
   AI Agents)
3. **Environments without SSO**: deployments outside the SUSE corporate
   network where `id.suse.com` is not reachable

AD users are provisioned and maintained by the LDAP sync process (see
`docs/features/identity/ad-integration.md`). Administrators can modify their
roles, but cannot deactivate/reactivate them (active status is managed
exclusively by directory sync), set passwords, or create them manually.

Local users are created directly in the database, bypassing the LDAP
sync process. They are functionally identical to AD-synced users for
the purposes of authorization, ticket assignment, and API key
management. The only difference is how they authenticate: local
credentials instead of SSO (see
`docs/features/identity/local-authentication.md`).

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
command cannot be used non-interactively — a TTY is required. If no TTY
is detected, prints to stderr `Error: This command requires an
interactive terminal (password input).` and exits with code 1.

**Behavior**:

1. Validates username format (see `docs/conventions.md`, Username Format).
   If invalid, exits with error:
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
4. Validates password per the policy in
   `docs/features/identity/local-authentication.md` § Password Validation
   (16–128 characters). If too short, exits with error:
   `"Error: Password must be at least 16 characters."` If too long,
   exits with error:
   `"Error: Password must be at most 128 characters."`
5. Delegates to `user_service.create_user()` with:
   - `ad_object_guid = None` (local user)
   - `active = True`
   - `password` = provided password (service handles hashing)
   - `roles = [(role, '_manual') for role in provided_roles]`
   - `acting_user_id = None` (CLI action)
   - See `docs/features/identity/user-service.md` for the service contract
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

Updates an existing user account. Identity field modifications
(`--email`, `--full-name`) are only permitted on local users — AD
users have their identity fields managed exclusively by directory sync
(see AD User Data Ownership in
`docs/features/identity/user-service.md`). Role changes are permitted on
both local and AD users. Reactivation (`--reactivate`) is permitted on
local users only — AD users have their active status managed
exclusively by directory sync (see AD Active Status Ownership in
`docs/features/identity/user-service.md`). The command works regardless
of whether the user is currently active or inactive (see Inactive User
Management Principle in `docs/features/identity/user-service.md`).

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

1. Normalize the username (trim whitespace, lowercase)
2. Looks up the user by normalized username — if not found, exits with
   error: `"Error: User '{username}' not found."`
3. If the user is an AD user (`ad_object_guid IS NOT NULL`) and `--email` or
   `--full-name` is provided, exits with error:
   `"Error: User '{username}' is managed by Active Directory via LDAP sync. Identity
   fields cannot be modified manually."` (exit code 1). Role changes
   are still permitted on AD users.
4. If the user is an AD user (`ad_object_guid IS NOT NULL`) and
   `--reactivate` is provided, exits with error:
   `"Error: Cannot reactivate AD users."` (exit code 1).
   Active status of AD users is managed exclusively by directory sync.
5. If no modification flags are provided (`--email`, `--full-name`,
   `--add-role`, `--remove-role`, `--reactivate` are all absent), prints:
   `"No changes specified for user '{username}'."` and exits with code 0
6. For each role value in `--add-role` and `--remove-role`, validates that
   it is a recognized role. If not, exits with error:
   `"Error: Invalid role '{value}'. Valid roles are: {list}."`
   The list of valid roles is derived from the system's role definitions
   at runtime
7. If `--email` is provided, validates email format — if not
   syntactically valid, exits with error:
   `"Error: Invalid email format '{value}'."`
8. If `--email` or `--full-name` is provided, delegates to
    `user_service.update_user()` with `acting_user_id = None`. If the
    service raises `UserConflictError` (duplicate email), exits with
    error: `"Error: A user with email '{email}' already exists."`
9. For role changes, passes `--add-role` and `--remove-role` values
    verbatim to `user_service.update_roles()` with
    `acting_user_id = None` and roles as `(role, '_manual')` pairs. The
    CLI does not pre-process or validate conflicts between add and remove
    lists — the service handles input resolution (deduplication,
    cancellation of conflicting entries) and validation (AD-derived role
    protection). The service never rejects input due to add/remove
    conflicts; it resolves them silently. Since `acting_user_id = None`,
    the self-removal guard does not apply (CLI is a system action)
10. If `--reactivate` is provided: delegates to
    `user_service.reactivate_user()` with `acting_user_id = None`. If
    the user is already active, this is a no-op. See
    `docs/features/identity/user-service.md` for reactivation semantics.
    Reactivation is intentionally the LAST mutation step so that the
    account is fully configured (correct email, roles, etc.) before
    becoming active again
11. Prints summary of changes. The summary lists only changes that were
    actually applied (not no-ops). If all requested operations resulted
    in no-ops (e.g., reactivating an already-active user, adding a role
    the user already has, removing a role the user does not have), prints:
    `"No changes applied to user '{username}'."` and exits with code 0.
    Otherwise prints:
    `"Updated user '{username}': {list of actual changes}."`
    Role changes are reported as the net difference (before → after):
    `✓ Roles updated: added 'admin'; removed 'vulnerability_analyst'`.
    If conflicting `--add-role` and `--remove-role` cancel out (no net
    change), no role line appears in the output (no-op, idempotent)

**Error handling (fail-fast)**: steps 8–10 are executed sequentially. If
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

Example output when the service rejects a role change (AD-derived
protection):

```
✓ Email updated to new@example.com
✗ Role update failed: cannot remove role 'vulnerability_analyst' (derived from AD group membership)
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
  --username <username>
```

**Parameters**:

| Parameter    | Required | Description                                    |
|--------------|----------|------------------------------------------------|
| `--username` | Yes      | Username of the user to deactivate              |

**Behavior**:

1. Normalize the username (trim whitespace, lowercase)
2. Looks up the user by normalized username — if not found, exits with
   error: `"Error: User '{username}' not found."`
3. If the user is an AD user (`ad_object_guid IS NOT NULL`), exits with
   error: `"Error: Cannot deactivate AD users."` (exit code 1).
   Active status of AD users is managed exclusively by directory sync.
4. If the user is already inactive, prints:
   `"User '{username}' is already inactive."` and exits with code 0
5. Queries the impact of deactivation:
   - Count of active API keys that will be revoked
   - Count of active sessions that will be invalidated
   - Count of open tickets assigned to the user that will be unassigned
   - Whether this user is the last active user with the Admin role
6. Displays the impact summary:
   ```
   About to deactivate user '{username}':
     - {n} active API keys will be revoked
     - {n} active sessions will be invalidated
     - {n} open tickets will be unassigned
   ```
   If the user is the last active admin, appends a warning to stderr:
   ```
   WARNING: this is the last active user with Admin role.
   After deactivation, assign Admin to another user via:
     sentinel manage-user update --username <user> --add-role admin
   ```
7. Prompts: `Proceed? [y/N]`
    - If the user answers anything other than `y` or `Y`, exits with
      code 0 without deactivating
    - If no TTY is detected, prints to stderr `Error: This command
      requires an interactive terminal (confirmation required).` and
      exits with code 1
8. Delegates to `user_service.deactivate_user()` with
   `acting_user_id = None` and
   `reason = "deactivated via CLI (manage-user deactivate)"`
9. Prints: `"Deactivated user '{username}'."`

This command does not permanently remove the user record from the
database. The User record is preserved to maintain referential integrity
with TicketEvent, ticket assignments, and UserRole audit data. This is
consistent with LDAP sync deactivation behavior. For full database
cleanup in development environments, reset the database directly.

**Inactive user management principle**: see
`docs/features/identity/user-service.md` (Inactive User Management Principle).

**Idempotency**: Idempotent. If the user is already inactive, the
command prints an informational message and exits with code 0.

**Exit codes**: 0 on success (including no-op and user-cancelled
confirmation), 1 on validation error, 2 on system error (database
unreachable).

**Output channels**: impact summary and confirmation to stdout.
`"Error: ..."` messages and `"WARNING: ..."` (last admin) to stderr.

### `sentinel manage-user set-password`

Sets or resets the password for a local user. See
`docs/features/identity/local-authentication.md` for full details on password
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
required. If no TTY is detected, prints to stderr `Error: This command
requires an interactive terminal (password input).` and exits with code 1.

This command is only valid for local users (`ad_object_guid = NULL`). The
username is normalized (trim whitespace, lowercase) before lookup. If
invoked on an AD user, exits with error:
`"Error: Cannot set password for AD user '{username}'. AD users
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
AD user, passwords don't match, password policy violation), 2 on system
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
4. If the user is an AD user (`ad_object_guid IS NOT NULL`), print a warning
   to stderr:
   `"Warning: User '{username}' is an AD user. Local login lockout
   does not apply to SSO authentication."` — then continue (do not
   abort)
5. Delegate to `asyncio.run(user_service.unlock_user(user_id,
   acting_user_id=None))` — the service handles Redis key deletion,
   logging, and idempotency (see
   `docs/features/identity/user-service.md`). `acting_user_id = None`
   because CLI is a system action.
6. Print: `"Unlocked user '{username}'."`

The command is idempotent: if the counter does not exist (user was not
locked), it succeeds silently.

**Idempotency**: Idempotent. If the user is not locked, the command
succeeds with a no-op and exits with code 0.

**Exit codes**: 0 on success (including no-op), 1 on validation error
(user not found), 2 on system error (database unreachable).

**Output channels**: confirmation to stdout. `"Warning: ..."` messages
to stderr. `"Error: ..."` messages to stderr.

### `sentinel manage-user list`

Lists all users in the system with their key attributes.

```
sentinel manage-user list \
  [--active | --inactive] \
  [--role <role>] ... \
  [--type local|ad]
```

**Parameters**:

| Parameter    | Required | Repeatable | Description                                    |
|--------------|----------|------------|------------------------------------------------|
| `--active`   | No       | No         | Show only active users                         |
| `--inactive` | No       | No         | Show only inactive users                       |
| `--role`     | No       | Yes        | Filter by role: `admin`, `vulnerability_analyst` |
| `--type`     | No       | No         | Filter by type: `local` or `ad`                |

`--active` and `--inactive` are mutually exclusive. If neither is
provided, all users are shown regardless of status.

**Behavior**:

1. Query all users matching the provided filters
2. Sort results alphabetically by username
3. Print a table to stdout with columns:

```
USERNAME        FULL NAME            EMAIL                    TYPE   STATUS    ROLES
jdoe            John Doe             jdoe@example.com         local  active    admin, vulnerability_analyst
bwilson          Bob Wilson           bob.wilson@suse.com      ad     active    vulnerability_analyst
olduser         Old User             old@example.com          local  inactive  —
```

Column alignment uses fixed-width spaces. The ROLES column shows a
comma-separated list of roles, or `—` if the user has no roles.

If no users match the filters, prints: `"No users found matching the
specified criteria."` and exits with code 0.

**Idempotency**: Idempotent. Read-only command, no state changes.

**Exit codes**: 0 on success (including empty results), 2 on system
error (database unreachable).

**Output channels**: table to stdout. `"Error: ..."` messages to stderr.

### `sentinel manage-user show`

Displays detailed information about a single user.

```
sentinel manage-user show \
  --username <username>
```

**Parameters**:

| Parameter    | Required | Description                              |
|--------------|----------|------------------------------------------|
| `--username` | Yes      | Username of the user to display          |

**Behavior**:

1. Normalize the username (trim whitespace, lowercase)
2. Look up the user by normalized username — if not found, exit with
   error: `"Error: User '{username}' not found."` (exit code 1)
3. Print detailed user information to stdout:

```
Username:     jdoe
Full name:    John Doe
Email:        jdoe@example.com
Type:         local
Status:       active
Roles:        admin (manual), vulnerability_analyst (O SUSE Security)
Created:      2025-03-15 10:30:00 UTC
Last login:   2025-06-01 14:22:00 UTC
Manager:      bwilson
```

The ROLES field shows each role with its origin in parentheses:
`manual` for roles assigned via CLI/API, or the AD group CN for roles
derived from LDAP sync. If a role has both origins, show both:
`admin (manual, O SUSE Admins)`.

If `Last login` is never, show `—`. If `Manager` is not set, show `—`.

**Idempotency**: Idempotent. Read-only command, no state changes.

**Exit codes**: 0 on success, 1 on validation error (user not found),
2 on system error (database unreachable).

**Output channels**: user detail to stdout. `"Error: ..."` messages to
stderr.

## UI

The web UI provides public user pages accessible to all authenticated
and unauthenticated users, plus administration controls accessible only
to admins.

### Users page

Accessible to all users. Displays a searchable, sortable table of all
users.

Columns:
- Full name
- Username
- Email
- Type (Local / AD)
- Roles — each role displays badge(s) indicating its origin(s): lock
  icon for AD-derived, pencil icon for manual. See
  `docs/features/identity/rbac.md` (Role Origins and Coexistence) for
  the coexistence semantics and UI representation
- Active status
- Manager name

Features:
- Search field with autocomplete (min 2 characters, searches name/email/
  username)
- Filter by type (local/AD), role, active status
- Click row to navigate to user detail page

Admin-specific actions available on each user row are described in
"Actions available for local users" and "Actions available for AD
users" below.

### User detail page

Accessible to all users (read-only for non-admins). Shows:

- **Profile section**: full name, username, email, active status, manager
  (linked to their profile), AD sync timestamp
- **Roles section** (editable by Admin only):
  - AD-derived roles: displayed with a lock icon and the source group
    name (e.g., `Vulnerability Analyst` lock icon `from "O SUSE
    Security"`). Not removable
  - Manual roles: displayed with a remove button. Removable by admin
  - "Add Role" button: dropdown to add a manual role
- **Assigned tickets section**: list of tickets currently assigned to
  this user

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
`docs/features/identity/ad-integration.md` Business Rule 1.

### Actions available for AD users

- **Edit roles**: add/remove roles

AD users cannot have their password set or reset (they authenticate
via id.suse.com). Deactivation and reactivation are not available for
AD users — their active status is managed exclusively by Active
Directory via LDAP sync.

### Deactivation confirmation dialog

When an admin clicks "Deactivate" for a local user, the frontend calls
`GET /api/v1/admin/users/{user}/deactivation-impact` and displays a
confirmation dialog with the impact summary before calling
`POST /api/v1/admin/users/{user}/deactivate`.

The dialog displays:

```
Deactivate user '{username}'?

This action will:
  - Revoke {n} active API keys
  - Invalidate {n} active sessions
  - Unassign {n} open tickets

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
- Password length must be 16–128 characters (consistent with
  `docs/features/identity/local-authentication.md`)

**On submit**: call `POST /api/v1/admin/users/{user}/password` with
the new password. On success, display a confirmation message:
"Password updated. All active sessions for this user have been
invalidated."

**Error handling**:
- HTTP 400 (invalid password): display the server error message inline
- HTTP 400 (AD user): this case should not occur since the button is
  only shown for local users, but if it does, display the error

The "Reset password" button is only visible for local users
(`ad_object_guid = NULL`). It is never shown for AD users.

### Public API endpoints

These endpoints are publicly accessible (read-only) and do not require
authentication.

#### `GET /api/v1/users`

User search and autocomplete. Returns a paginated list of users.

Query parameters:
- `search` (string, optional): searches across `username`, `email`, and
  `full_name`. Minimum 2 characters. Maximum 100 characters.
  Case-insensitive substring match (SQL `ILIKE '%query%'`)
- `type` (enum, optional): filter by authentication type. Values:
  `local`, `ad`
- `active` (boolean, optional): filter by active status
- `role` (enum, optional): filter by role (`admin`, `vulnerability_analyst`)
- `has_role` (boolean, optional): `true` to return only users with at
  least one role, `false` for users with no roles
- Standard pagination (`page`, `per_page`) and sorting (`sort_by`,
  `sort_order`). Valid `sort_by` fields: `username` (default),
  `full_name`, `email`, `created_at`

Response uses the standard paginated envelope (`data` array + `meta`
object). Each user object follows the same schema as
`GET /api/v1/users/{user}` (see below).

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | `search` parameter shorter than 2 characters or longer than 100 characters |

#### `GET /api/v1/users/{user}`

Returns full user profile. Response uses the standard single-resource
envelope:

```json
{
  "data": {
    "id": "uuid",
    "username": "string",
    "email": "string",
    "full_name": "string",
    "active": true,
    "source": "ad | local",
    "ad_object_guid": "uuid | null",
    "manager": {
      "id": "uuid",
      "username": "string",
      "full_name": "string",
      "email": "string"
    } | null,
    "roles": [
      {
        "role": "admin",
        "ad_group_cn": "O SUSE Security",
        "assigned_by": "uuid | null",
        "created_at": "ISO8601"
      }
    ],
    "created_at": "ISO8601",
    "updated_at": "ISO8601"
  }
}
```

Field notes:
- `source`: derived field — `"ad"` if `ad_object_guid IS NOT NULL`,
  otherwise `"local"`
- `ad_object_guid`: AD `objectGUID` (immutable UUID). NULL for local users
- `manager`: resolved manager object (from AD `manager` DN) or `null`
- `roles`: array of all roles from both AD group mappings and manual
  assignments. `ad_group_cn` is `'_manual'` for manually assigned roles

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `USER_NOT_FOUND` | No user found matching the given UUID or username |

### Admin API endpoints

All user mutation endpoints are defined here. This is the single source
of truth for the user management API surface. Other specs define the
business rules and service-layer contracts that these endpoints invoke.

All endpoints below require the `admin` role unless otherwise stated.

#### `PATCH /api/v1/admin/users/{user}`

Update a user's profile fields. Only local users (`ad_object_guid IS NULL`)
can be modified — AD users have their identity fields managed by
directory sync (see AD User Data Ownership in
`docs/features/identity/user-service.md`). This endpoint operates on
both active and inactive users (see Inactive User Management Principle
in `docs/features/identity/user-service.md`).

**Request body** (all fields optional, at least one required):

```json
{
  "email": "new@example.com",
  "full_name": "New Display Name"
}
```

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404 with
   code `USER_NOT_FOUND`
2. If no fields are provided in the body, return HTTP 422 with code
   `VALIDATION_ERROR`: `"At least one field must be provided."`
3. If `email` is provided, validate format — if invalid, return HTTP 422
   with code `VALIDATION_ERROR`: `"Invalid email format."`
4. If the user is an AD user (`ad_object_guid IS NOT NULL`), return HTTP 409
   with code `USER_AD_FIELD_READONLY`:
   `"Cannot modify identity fields for AD users. These fields are managed by Active Directory."`
5. Delegate to `user_service.update_user()` with
   `acting_user_id = authenticated_admin.id`
6. If the service raises `UserConflictError` (duplicate email), return
   HTTP 409 with code `USER_ALREADY_EXISTS`:
   `"A user with this email already exists."`
7. Return HTTP 200 with the updated user profile in the standard
   `{"data": ...}` envelope

**Response**: user profile in `{"data": {...}}` envelope (see
`GET /api/v1/users/{user}` in Public API endpoints above for the full
response schema).

#### `POST /api/v1/admin/users/{user}/roles`

Add or remove manual roles for a user.

**Request body**:

```json
{
  "add": ["admin"],
  "remove": ["vulnerability_analyst"]
}
```

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404 with
   code `USER_NOT_FOUND`
2. Delegate to `user_service.update_roles()` with
   `acting_user_id = authenticated_admin.id` and roles as
   `(role, '_manual')` pairs

**Validation rules**:
- Cannot remove roles with `ad_group_cn != '_manual'` — returns HTTP 400
  with code `USER_AD_ROLE_PROTECTED`:
  `"Cannot remove AD-derived role '{role}'. This role is managed by the
  AD group '{ad_group_cn}'."`
- Cannot remove your own Admin role — returns HTTP 409 with code
  `USER_SELF_ROLE_REMOVAL`:
  `"Cannot remove your own Admin role."` (enforced by
  `user_service.update_roles()` — see `docs/features/identity/user-service.md`)
- If both `add` and `remove` are empty arrays (or missing), the
  operation is a no-op — returns HTTP 200 with the unchanged user
  profile in the standard `{"data": ...}` envelope
- Adding a role that the user already has as a manual assignment is a
   no-op (idempotent)
- Removing a role the user does not have is a no-op (idempotent)
- Adding a role that the user already holds via AD derivation creates a
  separate `_manual` record — both origins coexist independently. See
  `docs/features/identity/rbac.md` (Role Origins and Coexistence) for full
  semantics
- Creates a `UserRole` record with `ad_group_cn = '_manual'` and
  `assigned_by` set to the authenticated admin's user ID for each added
  role

**Response**: HTTP 200 with updated user profile including all roles,
wrapped in the standard `{"data": ...}` envelope (see
`GET /api/v1/users/{user}` in Public API endpoints above for the full
response schema).

#### `POST /api/v1/admin/users/{user}/password`

Reset the password for a local user. This endpoint operates on both
active and inactive local users (see Inactive User Management Principle
in `docs/features/identity/user-service.md`). Setting a password on an
inactive user prepares credentials for reactivation.

**Request body**:

```json
{
  "password": "string (required, see local-authentication.md § Password Validation)"
}
```

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404 with
   code `USER_NOT_FOUND`
2. Delegate to `user_service.reset_password(user_id, password,
   acting_user_id=authenticated_admin.id)` — this handles AD user
   check, validation, hashing, and session invalidation (see
   `docs/features/identity/user-service.md`)
3. Log the operation at INFO level: admin identity (user_id, username)
   and target user (user_id, username)
4. Return HTTP 200

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `USER_AD_PASSWORD_FORBIDDEN` | Cannot set password for AD user |
| 400 | `VALIDATION_ERROR` | Password does not meet policy requirements (see `docs/features/identity/local-authentication.md` § Password Validation) |
| 404 | `USER_NOT_FOUND` | User not found |

**Response** (200):

```json
{
  "data": {
    "detail": "Password updated. All active sessions have been invalidated."
  }
}
```

#### `POST /api/v1/admin/users/{user}/deactivate`

Deactivate a user account. Triggers significant side effects (API key
revocation, session invalidation, ticket unassignment).

**Request body**: none (empty body or omitted).

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404 with
   code `USER_NOT_FOUND`
2. If the user is already inactive, return HTTP 200 with the unchanged
   user profile (idempotent no-op)
3. Delegate to `user_service.deactivate_user()` with
   `acting_user_id = authenticated_admin.id` and
   `reason = "deactivated by admin via API"`
4. Return HTTP 200 with the updated user profile in the standard
   `{"data": ...}` envelope

**Constraints**:
- Self-deactivation is rejected by the service layer — returns HTTP 409
  with code `USER_SELF_DEACTIVATION`:
  `"Cannot deactivate your own account."`
- AD user deactivation is rejected by the service layer — returns
  HTTP 409 with code `USER_AD_STATUS_READONLY`:
  `"Cannot deactivate AD users."`

See `docs/features/identity/user-service.md` for the full side effect contract
(API key revocation, session invalidation, ticket unassignment on
deactivation).

**Response**: user profile in `{"data": {...}}` envelope (see
`GET /api/v1/users/{user}` in Public API endpoints above for the full
response schema).

#### `POST /api/v1/admin/users/{user}/reactivate`

Reactivate a previously deactivated user account.

**Request body**: none (empty body or omitted).

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404 with
   code `USER_NOT_FOUND`
2. If the user is already active, return HTTP 200 with the unchanged
   user profile (idempotent no-op)
3. Delegate to `user_service.reactivate_user()` with
   `acting_user_id = authenticated_admin.id`
4. Return HTTP 200 with the updated user profile in the standard
   `{"data": ...}` envelope

**Constraints**:
- AD user reactivation is rejected by the service layer — returns
  HTTP 409 with code `USER_AD_STATUS_READONLY`:
  `"Cannot reactivate AD users."`

**Response**: user profile in `{"data": {...}}` envelope (see
`GET /api/v1/users/{user}` in Public API endpoints above for the full
response schema).

#### `GET /api/v1/admin/users/{user}/deactivation-impact`

Returns a preview of the side effects that would occur if the user were
deactivated. Used by the frontend to display a confirmation dialog before
proceeding with deactivation.

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404 with
   code `USER_NOT_FOUND`
2. If the resolved user is the requesting admin themselves, return
   HTTP 409 with code `USER_SELF_DEACTIVATION`:
   `"Cannot preview deactivation impact for your own account."`
   Rationale: the actual deactivation endpoint rejects self-deactivation
   with the same code. Rejecting the preview as well keeps the API
   consistent — if you cannot deactivate yourself, you cannot preview
   the impact either. This prevents a confusing UX where the preview
   succeeds but the subsequent action is rejected.
3. If the user is an AD user (`ad_object_guid IS NOT NULL`), return HTTP 409
   with code `USER_AD_STATUS_READONLY`:
   `"Cannot deactivate AD users."`
   Rationale: same consistency principle as self-deactivation — if the
   deactivation endpoint rejects AD users, the preview should too.
4. If the user is already inactive, return HTTP 200 with a no-impact
   response: all counts set to zero and `already_inactive` set to `true`.
   Rationale: the actual `POST .../deactivate` endpoint is idempotent and
   returns HTTP 200 for already-inactive users. The preview must not be
   stricter than the action it previews — returning 409 here while the
   action returns 200 creates an asymmetry that forces clients to
   special-case the preview error path for a condition that the action
   itself treats as a no-op.
5. Query and return the impact summary

**Response** (HTTP 200):

```json
{
  "data": {
    "already_inactive": false,
    "api_keys_count": 3,
    "sessions_count": 2,
    "tickets_count": 5
  }
}
```

When the user is already inactive, the response contains zeroed counts:

```json
{
  "data": {
    "already_inactive": true,
    "api_keys_count": 0,
    "sessions_count": 0,
    "tickets_count": 0
  }
}
```

| Field                  | Type          | Description                                      |
|------------------------|---------------|--------------------------------------------------|
| `already_inactive`     | `bool`        | `true` if the user is already inactive (no-op deactivation) |
| `api_keys_count`       | `int`         | Active API keys that will be revoked             |
| `sessions_count`       | `int`         | Active sessions that will be invalidated         |
| `tickets_count`        | `int`         | Open tickets assigned to this user that will be unassigned |

**Semantics**: this endpoint returns a point-in-time snapshot of the
user's current state. The response is purely informational — the
subsequent `POST .../deactivate` call does not verify whether the impact has
changed since the preview was fetched. Between viewing the preview and
confirming the deactivation, new resources may have been assigned to the
user (tickets, API keys, sessions). The deactivation proceeds regardless
and affects all resources at execution time, not only those shown in the
preview.

**Authorization**: requires `admin` role.

#### `POST /api/v1/admin/users/{user}/unlock`

Clear the login lockout counter for a user.

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404 with
   code `USER_NOT_FOUND`
2. Delegate to `user_service.unlock_user(user_id,
   acting_user_id=authenticated_admin.id)` — this handles Redis key
   deletion, logging, and idempotency (see
   `docs/features/identity/user-service.md`)
3. Return HTTP 200 with `{"data": {"detail": "Account unlocked successfully."}}`

The endpoint is idempotent: if the user is not locked, it returns 200
with the same response without error. The log entry is emitted
regardless (to record that an admin attempted to unlock).

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `USER_NOT_FOUND` | User not found |

## Interaction with LDAP Sync

The `sync_ldap_directory` fetcher operates exclusively on users with
`ad_object_guid IS NOT NULL`. Local users (`ad_object_guid = NULL`) are invisible to
the sync process:

- They are never deactivated by the sync
- They are never updated with AD data
- They are never assigned AD-derived roles

This separation is inherent in the existing sync algorithm — no special
handling is required.

## Business Rules

1. **Local users are identified by `ad_object_guid = NULL`**: this is the
   canonical way to distinguish local users from AD-synced users. No
   additional flag or column is needed
2. **No "last admin" enforcement**: the system does not enforce a
   minimum admin count. However, via UI/API it is practically impossible
   for administrators to accidentally eliminate all admins — the
   self-removal guard (see `docs/features/identity/rbac.md`, Business Rule 1)
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
   `docs/features/identity/ad-integration.md`, Business Rule 6)
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
- **Password policy**: minimum 16 characters, no complexity rules.
  Length is the primary defense (see
  `docs/features/identity/local-authentication.md`)
- **Audit trail**: user operations are logged at INFO level (user
   creation, role changes, password resets). Deactivation additionally
   creates `TicketEvent` records for ticket unassignment. Role changes
   do not produce `TicketEvent` records (see
   `docs/features/identity/user-service.md`)
- **Admin password reset is logged**: every admin-initiated password
  reset is logged at INFO level with the acting admin's identity and
  the target user. No rate limiting or step-up authentication is applied
  — the admin role is the highest trust level in the system, and
  additional friction would not meaningfully improve security given that
  a compromised admin already has full system access
- **No notification on admin password reset (accepted risk)**: when an
  admin resets a user's password via `POST /api/v1/admin/users/{user}/password`,
  the target user receives no notification (no email, no in-app alert).
  A compromised admin could covertly take over an account. This is
  accepted because: (1) the admin trust level already implies full system
  access; (2) the audit log (INFO-level logging of acting admin and
  target user) provides a forensic trail; (3) adding a notification
  system (SMTP infrastructure, templates, bounce handling) is
  disproportionate to the residual risk in an internal tool. If the
  threat model evolves (e.g., multi-tenant admin roles), user-facing
  notifications should be reconsidered
- **Per-username lockout DoS vector (accepted risk)**: the per-username
  lockout mechanism (5 failed attempts → account locked for 10 minutes)
  allows anyone who knows a valid username to lock out that account by
  sending 5 failed login attempts. This is a known trade-off in internal
  tools: brute-force protection at the cost of a low-effort DoS vector.
  Mitigations: (1) the lockout is temporary (auto-expires via Redis
  TTL); (2) an admin can unlock immediately via CLI or API; (3) existing
  sessions are NOT invalidated by lockout (see
  `docs/features/identity/local-authentication.md`) — a logged-in user continues
  working normally even if their account is locked. Future mitigation:
  per-IP rate limiting could be added if the threat model changes

## Cross-references

- `docs/features/identity/authentication.md` — authentication framework, API
  keys, session management
- `docs/features/identity/local-authentication.md` — login endpoint, password
  hashing, rate limiting
- `docs/features/identity/user-service.md` — service contract for create,
  update, deactivate, reactivate
- `docs/features/identity/rbac.md` — role definitions and permission model
- `docs/features/identity/ad-integration.md` — LDAP sync (manages AD users)
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
