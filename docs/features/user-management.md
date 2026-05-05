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
  --password <password> \
  [--full-name <name>] \
  [--role <role>] ...
```

**Parameters**:

| Parameter      | Required | Repeatable | Description                                |
|----------------|----------|------------|--------------------------------------------|
| `--username`   | Yes      | No         | Unique username for the account             |
| `--email`      | Yes      | No         | Unique email address                        |
| `--password`   | Yes      | No         | Initial password (12-128 characters)        |
| `--full-name`  | No       | No         | Display name                                |
| `--role`       | No       | Yes        | Role to assign: `admin`, `vulnerability_analyst` |

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

**Exit codes**: 0 on success, 1 on validation error (duplicate user,
invalid role, missing flag)

### `sentinel manage-user update`

Updates an existing user account. This command works on any user (local
or LDAP-synced).

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
7. If `--reactivate` is provided: delegates to
   `user_service.reactivate_user()` with `acting_user_id = None`. If
   the user is already active, this is a no-op. See
   `docs/features/user-lifecycle.md` for reactivation semantics
8. For role changes (`--add-role`, `--remove-role`), delegates to
   `user_service.update_roles()` with `acting_user_id = None` and
   roles as `(role, '_manual')` pairs. The service handles validation
   (AD-derived role protection). Since `acting_user_id = None`, the
   self-removal guard does not apply (CLI is a system action)
9. Prints summary of changes:
   `"Updated user '{username}': {list of changes}."`

**Exit codes**: 0 on success, 1 on validation error

### `sentinel manage-user delete`

Deactivates a user account (soft delete only).

```
sentinel manage-user delete \
  --username <username>
```

**Parameters**:

| Parameter    | Required | Description                                    |
|--------------|----------|------------------------------------------------|
| `--username` | Yes      | Username of the user to deactivate              |

**Behavior**:

1. Looks up the user by `username` — if not found, exits with error:
   `"Error: User '{username}' not found."`
2. If the user is already inactive, exits with error:
   `"Error: User '{username}' is already deactivated."`
3. Delegates to `user_service.deactivate_user()` with
   `acting_user_id = None` and
   `reason = "deactivated via CLI (manage-user delete)"`. This triggers
   the deactivation sequence: revoke API keys, invalidate sessions,
   mark inactive, reassign tickets — see
   `docs/features/user-lifecycle.md` and
   `docs/features/authentication.md` (Deactivation ordering)
4. Prints: `"Deactivated user '{username}'."`

This command does not permanently remove the user record from the
database. The User record is preserved to maintain referential integrity
with TicketEvent, ticket assignments, and UserRole audit data. This is
consistent with LDAP sync deactivation behavior. For full database
cleanup in development environments, reset the database directly.

**Exit codes**: 0 on success, 1 on validation error

### `sentinel manage-user set-password`

Sets or resets the password for a local user. See
`docs/features/local-authentication.md` for full details.

```
sentinel manage-user set-password \
  --username <username> \
  --password <new_password>
```

This command is only valid for local users (`ldap_uid = NULL`). It
invalidates all active sessions after changing the password.

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
2. Delete the Redis key `login_attempts:{normalized_username}`
3. If Redis is unreachable, exit with error:
   `"Error: Could not connect to Redis. Lockout state cannot be cleared."`
   (exit code 2)
4. Print: `"Unlocked user '{username}'."`

The command is idempotent: if the counter does not exist (user was not
locked), it succeeds silently. No database lookup is required — the
command operates directly on Redis.

**Exit codes**: 0 on success, 1 on validation error (missing parameter),
2 on system error (Redis unreachable)

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
- Roles

Filters: by type (local/SSO), by status (active/inactive), by role.

### Actions available for local users

- **Create**: form with username, email, password, full name, roles
- **Edit**: change email, full name, roles
- **Reset password**: set a new password (invalidates sessions)
- **Unlock**: clear the login lockout counter (same as CLI `unlock`)
- **Deactivate**: soft delete (same as CLI `delete`)
- **Reactivate**: restore a deactivated user

### Actions available for SSO users

- **Edit roles**: add/remove roles
- **Deactivate**: soft delete
- **Reactivate**: restore

SSO users cannot have their password set or reset (they authenticate
via id.suse.com).

### Admin API endpoints

#### `POST /api/v1/admin/users/{user_id}/unlock`

**Permission**: `admin` role

**Behavior**:

1. Look up the user by `user_id` — if not found, return HTTP 404
2. Normalize the user's `username` (trim, lowercase)
3. Delete the Redis key `login_attempts:{normalized_username}`
4. Return HTTP 204 (no content)

The endpoint is idempotent: if the user is not locked, it returns 204
without error.

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
2. **No "last admin" enforcement**: neither the CLI nor the admin UI
   enforces a minimum admin count. If the last admin is removed or
   deactivated, the system will have zero active admins until an
   operator runs
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

## Security Considerations

- **CLI access requires shell access**: the `manage-user` commands
  require direct access to the host or container. There are no
  unauthenticated HTTP endpoints for user management
- **Admin UI is authenticated and role-protected**: only users with the
  `admin` role can access the user management pages
- **Password policy**: minimum 12 characters, no complexity rules.
  Length is the primary defense (see
  `docs/features/local-authentication.md`)
- **Audit trail**: user creation, role changes, and deactivation
  produce `TicketEvent` records where relevant (e.g., ticket
  reassignment on deactivation)

## Cross-references

- `docs/features/authentication.md` — authentication framework, API
  keys, session management
- `docs/features/local-authentication.md` — login endpoint, password
  hashing, rate limiting
- `docs/features/user-lifecycle.md` — service contract for create,
  update, deactivate, reactivate
- `docs/features/rbac.md` — role definitions and permission model
- `docs/features/ldap-directory.md` — LDAP sync (manages SSO users)
