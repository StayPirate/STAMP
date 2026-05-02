# Local User Management

## Purpose

Enable administrators to create, update, and delete user accounts directly
via the CLI in environments where SUSE Active Directory (`pan.suse.de`) is
not reachable — typically local development and staging deployments. This
allows testing the full application workflow without depending on the SUSE
internal network.

Local users are created directly in the database, bypassing the LDAP sync
process. They are functionally identical to LDAP-synced users for the
purposes of authentication, authorization, and ticket assignment.

## Configuration

### `ALLOW_LOCAL_USERS`

| Setting              | Type    | Default | Env var              |
|----------------------|---------|---------|----------------------|
| `allow_local_users`  | boolean | `false` | `ALLOW_LOCAL_USERS`  |

The `create` and `delete` subcommands check this flag before executing.
If the flag is `false` or absent, the command exits immediately with exit
code 1 and the message:

```
Error: Local user management is disabled. Set ALLOW_LOCAL_USERS=true to
enable this feature. This setting should only be used in development and
staging environments.
```

The `update` subcommand does **not** check this flag. It operates on
existing users regardless of environment, because it is needed in
production to assign roles during initial bootstrap (e.g., promoting the
first admin after LDAP sync). Since `update` cannot create new users, the
risk of misuse is minimal — it is functionally equivalent to the
`PUT /api/v1/users/{id}/roles` admin API endpoint.

## CLI Commands

All commands are subcommands of the `sentinel manage-user` group. See
`docs/cli-reference.md` for the full command index and
`docs/conventions.md` (CLI Conventions) for general CLI design guidelines.

### `sentinel manage-user create`

Creates a new local user account.

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

**Behavior**:

1. Checks `ALLOW_LOCAL_USERS` — exits with error if disabled
2. Validates username format: must be 1-64 characters, start with a
   letter, and contain only lowercase letters, numbers, dots, hyphens,
   and underscores (`[a-z0-9._-]`). If invalid, exits with error:
   `"Error: Invalid username '{value}'. Username must be 1-64 characters,
   start with a letter, and contain only lowercase letters, numbers, dots,
   hyphens, and underscores."`
3. For each `--role` provided, validates that it is a recognized role. If
   not, exits with error:
   `"Error: Invalid role '{value}'. Valid roles are: {list}."`
   The list of valid roles is derived from the system's role definitions
   at runtime
4. Validates email format — if the provided email is not syntactically
   valid, exits with error:
   `"Error: Invalid email format '{value}'."`
5. Validates that no user exists with the same `username` or `email`
   (including inactive/deactivated users) — if a conflict is found, exits
   with error:
   `"Error: A user with username '{username}' already exists."` or
   `"Error: A user with email '{email}' already exists."`
6. Creates a `User` record with:
   - `username` = provided value
   - `email` = provided value
   - `full_name` = provided value or `NULL`
   - `active` = `true`
   - `ldap_uid` = `NULL` (marks this as a local, non-LDAP user)
   - `ldap_dn` = `NULL`
   - `manager_uid` = `NULL`
   - `ldap_synced_at` = `NULL`
7. For each `--role` provided, creates a `UserRole` record with
   `ad_group_cn = '_manual'` and `assigned_by = NULL` (CLI action)
8. Prints confirmation:
   `"Created user '{username}' ({email}) with roles: {roles}."`
   or `"Created user '{username}' ({email}) with no roles."` if no roles
   were specified

**Exit codes**: 0 on success, 1 on validation error (duplicate user,
invalid role, missing flag)

### `sentinel manage-user update`

Updates an existing user account. This command works on any user (local
or LDAP-synced) and does not require the `ALLOW_LOCAL_USERS` flag.

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
   Then validates uniqueness and updates
6. If `--full-name` is provided, updates
7. If `--reactivate` is provided: if the user is already active, this is
   a no-op. If the user is inactive, sets `User.active = true`
8. For each `--add-role`, creates a `UserRole` record with
   `ad_group_cn = '_manual'` and `assigned_by = NULL` if not already
   present for that user/role/`_manual` combination. Adding a role the
   user already has as a manual assignment is a no-op
9. For each `--remove-role`:
   - If the role has `ad_group_cn != '_manual'` (AD-derived), exits with
     error:
     `"Error: Cannot remove AD-derived role '{role}'. This role is
     managed by Active Directory group membership."`
   - Otherwise, removes the `UserRole` record where
     `ad_group_cn = '_manual'`. If the user does not have the role as a
     manual assignment, this is a no-op (idempotent behavior, consistent
     with `--add-role`)
10. Prints summary of changes:
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

1. Checks `ALLOW_LOCAL_USERS` — exits with error if disabled
2. Looks up the user by `username` — if not found, exits with error:
   `"Error: User '{username}' not found."`
3. If the user is already inactive, exits with error:
   `"Error: User '{username}' is already deactivated."`
4. Sets `User.active = false`
5. Prints: `"Deactivated user '{username}'."`

This command does not permanently remove the user record from the
database. The User record is preserved to maintain referential integrity
with TicketEvent, ticket assignments, and UserRole audit data. This is
consistent with LDAP sync deactivation behavior. For full database
cleanup in development environments, reset the database directly.

**Exit codes**: 0 on success, 1 on validation error

## Interaction with LDAP Sync

The `sync_ldap_directory` fetcher operates exclusively on users with
`ldap_uid IS NOT NULL`. Local users (`ldap_uid = NULL`) are invisible to
the sync process:

- They are never deactivated by the sync
- They are never updated with AD data
- They are never assigned AD-derived roles

This separation is inherent in the existing sync algorithm — no special
handling is required.

## Authentication Dependency

Local user management creates user accounts in the database but does not
provide an authentication mechanism. To log in as a local user, a
compatible authentication method must be available for environments
without access to the SUSE SSO (`id.suse.com`).

This dependency will be addressed in the future
`docs/features/sso-authentication.md` specification, which must define a
fallback authentication mechanism for environments where SSO is not
reachable.

Until an authentication mechanism is available, local users can only be
used for:

- Backend testing (via test fixtures and direct service calls)
- Database-level verification of user-related features

## Business Rules

1. **CLI-only**: there are no API endpoints for local user management.
   Users are created, updated, and deleted exclusively via the `sentinel
   manage-user` CLI commands
2. **Configuration guard**: the `create` and `delete` commands require
   `ALLOW_LOCAL_USERS=true`. The `update` command does not require this
   flag because it operates on existing users and is needed for
   production bootstrap. See the Configuration section for details
3. **No "last admin" enforcement**: the CLI does not enforce a minimum
   admin count. If the last admin is removed or deactivated via CLI, the
   system will have zero active admins until an operator runs
   `sentinel manage-user update --username <user> --add-role admin`.
   This is consistent with the LDAP sync behavior (see
   `docs/features/ldap-directory.md`, Business Rule 6)
4. **No duplicate usernames or emails**: the `create` command enforces
   uniqueness. The `update` command enforces uniqueness when changing the
   email
5. **Local users are marked by `ldap_uid = NULL`**: this is the canonical
   way to distinguish local users from LDAP-synced users. No additional
   flag or column is needed
6. **Role origin is `_manual`**: all roles assigned via `manage-user`
   commands have `ad_group_cn = '_manual'` and `assigned_by = NULL`

## Security Considerations

- **No network attack surface**: the `manage-user` commands are CLI-only,
  requiring shell access to the host or container. There are no HTTP
  endpoints exposed
- **Configuration guard**: the `ALLOW_LOCAL_USERS` flag provides an
  explicit opt-in mechanism. The default is `false`, preventing accidental
  use in production
- **Production environments**: production deployments should never set
  `ALLOW_LOCAL_USERS=true`. Users in production are managed exclusively
  via LDAP sync. The configuration guard error message explicitly warns
  that this setting is intended for development and staging only
