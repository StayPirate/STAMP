# CLI Reference

Sentinel provides a command-line interface via the `sentinel` entry
point, registered as a console script in `pyproject.toml`. All commands
require direct shell access to the host or container running the backend.

For the CLI design contract (output format, channel separation,
idempotency rules), see `docs/conventions.md` (CLI Conventions). For the
shared implementation mechanism (entry point, session management, error
handling, signal handling), see
`docs/features/platform/cli-infrastructure.md`.

## Global Options

```
sentinel --version    # Print version and exit
sentinel --help       # Show top-level help
sentinel <group> --help   # Show help for a command group
```

## Exit Codes

All commands use the following exit code scheme (defined in
`docs/conventions.md`, Exit Codes):

| Code | Meaning |
|------|---------|
| 0    | Success (includes idempotent no-ops and user-cancelled confirmations) |
| 1    | User error (bad input, validation failure, unknown resource) |
| 2    | System error (database unreachable, Redis unreachable, unhandled exception) |
| 130  | Interrupted by SIGINT (Ctrl+C) |
| 143  | Interrupted by SIGTERM |

Individual commands may use a subset of these codes. See the owning
feature spec for per-command details.

## `sentinel manage-user`

User lifecycle management. These commands create, modify, and
deactivate local user accounts. Requires direct shell access; there
are no unauthenticated HTTP endpoints for user management.

Full specification:
[user-management](features/identity/user-management.md#cli-commands)

### `sentinel manage-user create`

Creates a new local user account with a password.

```
sentinel manage-user create \
  --username <username> \
  --email <email> \
  [--full-name <name>] \
  [--role <role>] ...
```

Password is collected interactively (hidden prompt, requires TTY).

**Idempotency**: Not idempotent (interactive).

### `sentinel manage-user update`

Updates an existing user account. Identity fields (`--email`,
`--full-name`) are only permitted on local users. Role changes and
reactivation are separate steps executed sequentially with fail-fast
semantics.

```
sentinel manage-user update \
  --username <username> \
  [--email <new_email>] \
  [--full-name <new_name>] \
  [--add-role <role>] ... \
  [--remove-role <role>] ... \
  [--reactivate]
```

**Idempotency**: Idempotent (no-op if state already reached).

### `sentinel manage-user deactivate`

Deactivates a user account (soft delete). Shows an impact summary
(API keys revoked, sessions invalidated, tickets unassigned) and
prompts for confirmation.

```
sentinel manage-user deactivate \
  --username <username>
```

**Idempotency**: Idempotent (no-op if already inactive).

### `sentinel manage-user set-password`

Sets or resets the password for a local user. Invalidates all active
sessions after changing the password.

```
sentinel manage-user set-password \
  --username <username>
```

Password is collected interactively (hidden prompt, requires TTY).
Not valid for external users.

**Idempotency**: Not idempotent (interactive).

### `sentinel manage-user unlock`

Clears the login lockout counter for a user, allowing immediate login
without waiting for the TTL to expire.

```
sentinel manage-user unlock \
  --username <username>
```

**Idempotency**: Idempotent (no-op if user is not locked).

### `sentinel manage-user list`

Lists all users with their key attributes, with optional filters.

```
sentinel manage-user list \
  [--active | --inactive] \
  [--role <role>] ... \
  [--type local|external]
```

`--active` and `--inactive` are mutually exclusive.

**Idempotency**: Idempotent (read-only).

### `sentinel manage-user show`

Displays detailed information about a single user, including roles
with their origin (manual or external group name).

```
sentinel manage-user show \
  --username <username>
```

**Idempotency**: Idempotent (read-only).

## `sentinel fetcher`

Read-only diagnostic access to the fetcher infrastructure. Designed
for troubleshooting and quick status checks. All mutations (trigger,
enable/disable, configuration changes) are done exclusively through
the API.

Full specification:
[fetcher-operations](features/platform/fetcher-operations.md#cli-commands)

### `sentinel fetcher list`

Lists all fetchers (registered and deregistered) with their current
state: enabled status, last run time, run status, and custom settings
count.

```
sentinel fetcher list
```

**Idempotency**: Idempotent (read-only).

### `sentinel fetcher config`

Displays the full configuration of a fetcher, including custom
settings with current values, defaults, and descriptions. Works for
both registered and deregistered fetchers (with reduced output for
the latter).

```
sentinel fetcher config <name>
```

**Idempotency**: Idempotent (read-only).

## `sentinel api-key`

API key management for user accounts.

Full specification:
[authentication](features/identity/authentication.md#cli-commands)

### `sentinel api-key list`

Lists all API keys (active, revoked, and expired) for a user.

```
sentinel api-key list --username <username>
```

**Idempotency**: Idempotent (read-only).

### `sentinel api-key revoke`

Revokes a specific API key for a user.

```
sentinel api-key revoke --username <username> --key-id <uuid>
```

**Idempotency**: Idempotent (no-op if already revoked).
