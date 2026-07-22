# CLI Reference

Sentinel provides a command-line interface via the `sentinel` entry point. See
`docs/conventions.md` (CLI Conventions) for framework choices, design
guidelines, and the CLI Output Contract, and
`docs/features/platform/cli-infrastructure.md` for the shared
implementation mechanism (entry point, session management, error
handling).

## Commands

| Command                            | Description                              | Idempotent | Spec                                         |
|------------------------------------|------------------------------------------|------------|----------------------------------------------|
| `sentinel manage-user create`      | Create a local user account              | No (interactive) | [user-management](features/identity/user-management.md) |
| `sentinel manage-user update`      | Update an existing user account          | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user deactivate`  | Deactivate a user account                | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user set-password`| Set or reset password for a local user   | No (interactive) | [user-management](features/identity/user-management.md) |
| `sentinel manage-user unlock`      | Clear login lockout counter for a user   | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user list`        | List users with filters                  | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user show`        | Show detailed info for a single user     | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel fetcher list`            | List all fetchers with current state     | Yes        | [fetcher-operations](features/platform/fetcher-operations.md) |
| `sentinel fetcher config <name>`   | Display fetcher configuration            | Yes        | [fetcher-operations](features/platform/fetcher-operations.md) |
| `sentinel api-key list`            | List API keys for a user                 | Yes        | [authentication](features/identity/authentication.md) |
| `sentinel api-key revoke`          | Revoke a specific API key                | Yes        | [authentication](features/identity/authentication.md) |
