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

All commands are subcommands of the `stamp manage-user` group. See
`docs/cli-reference.md` for the full command index and
`docs/conventions.md` (CLI Conventions) for general CLI design guidelines.

### `stamp manage-user create`

Creates a new local user account.

```
stamp manage-user create \
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
2. Validates that no user exists with the same `username` or `email` — if
   a conflict is found, exits with error:
   `"Error: A user with username '{username}' already exists."` or
   `"Error: A user with email '{email}' already exists."`
3. Creates a `User` record with:
   - `username` = provided value
   - `email` = provided value
   - `full_name` = provided value or `NULL`
   - `active` = `true`
   - `ldap_uid` = `NULL` (marks this as a local, non-LDAP user)
   - `ldap_dn` = `NULL`
   - `manager_uid` = `NULL`
   - `ldap_synced_at` = `NULL`
4. For each `--role` provided, creates a `UserRole` record with
   `source = manual`
5. Prints confirmation:
   `"Created user '{username}' ({email}) with roles: {roles}."`
   or `"Created user '{username}' ({email}) with no roles."` if no roles
   were specified

**Exit codes**: 0 on success, 1 on validation error (duplicate user,
invalid role, missing flag)

### `stamp manage-user update`

Updates an existing user account. This command works on any user (local
or LDAP-synced) and does not require the `ALLOW_LOCAL_USERS` flag.

```
stamp manage-user update \
  --username <username> \
  [--email <new_email>] \
  [--full-name <new_name>] \
  [--add-role <role>] ... \
  [--remove-role <role>] ...
```

**Parameters**:

| Parameter        | Required | Repeatable | Description                                |
|------------------|----------|------------|--------------------------------------------|
| `--username`     | Yes      | No         | Username of the user to update (identifier) |
| `--email`        | No       | No         | New email address                           |
| `--full-name`    | No       | No         | New display name                            |
| `--add-role`     | No       | Yes        | Role to add: `admin`, `vulnerability_analyst` |
| `--remove-role`  | No       | Yes        | Role to remove: `admin`, `vulnerability_analyst` |

**Behavior**:

1. Looks up the user by `username` — if not found, exits with error:
   `"Error: User '{username}' not found."`
2. If `--email` is provided, validates uniqueness and updates
3. If `--full-name` is provided, updates
4. For each `--add-role`, creates a `UserRole` record with
   `source = manual` if not already present. Adding a role the user
   already has (regardless of source) is a no-op
5. For each `--remove-role`:
   - If the role has `source = ad_group`, exits with error:
     `"Error: Cannot remove AD-derived role '{role}'. This role is
     managed by Active Directory group membership."`
   - If removing the `admin` role would leave the system with zero active
     admins, exits with error:
     `"Error: Cannot remove the last Admin role. At least one admin must
     exist."`
   - Otherwise, removes the `UserRole` record
6. Prints summary of changes:
   `"Updated user '{username}': {list of changes}."`

**Exit codes**: 0 on success, 1 on validation error

### `stamp manage-user delete`

Deactivates or permanently removes a user account.

```
stamp manage-user delete \
  --username <username> \
  [--hard]
```

**Parameters**:

| Parameter    | Required | Description                                    |
|--------------|----------|------------------------------------------------|
| `--username` | Yes      | Username of the user to delete                  |
| `--hard`     | No       | Permanently remove the record from the database |

**Behavior**:

1. Checks `ALLOW_LOCAL_USERS` — exits with error if disabled
2. Looks up the user by `username` — if not found, exits with error:
   `"Error: User '{username}' not found."`
3. If the user is the last active admin, exits with error:
   `"Error: Cannot delete the last Admin. At least one admin must
   exist."`
4. **Without `--hard`** (soft delete):
   - Sets `User.active = false`
   - Prints: `"Deactivated user '{username}'."`
5. **With `--hard`** (hard delete):
   - Removes all associated `UserRole` records
   - Removes the `User` record from the database
   - Prints: `"Permanently deleted user '{username}'."`

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
   Users are created, updated, and deleted exclusively via the `stamp
   manage-user` CLI commands
2. **Configuration guard**: the `create` and `delete` commands require
   `ALLOW_LOCAL_USERS=true`. The `update` command does not require this
   flag because it operates on existing users and is needed for
   production bootstrap. See the Configuration section for details
3. **At least one admin**: the system must always have at least one active
   user with the Admin role. The `update` and `delete` commands enforce
   this constraint before removing an admin role or deactivating/deleting
   an admin user
4. **No duplicate usernames or emails**: the `create` command enforces
   uniqueness. The `update` command enforces uniqueness when changing the
   email
5. **Local users are marked by `ldap_uid = NULL`**: this is the canonical
   way to distinguish local users from LDAP-synced users. No additional
   flag or column is needed
6. **Role source is `manual`**: all roles assigned via `manage-user`
   commands have `source = manual`

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
