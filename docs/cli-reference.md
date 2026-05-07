# CLI Reference

Sentinel provides a command-line interface via the `sentinel` entry point. See
`docs/conventions.md` (CLI Conventions) for framework choices, design
guidelines, and the CLI Output Contract.

## Commands

| Command                            | Description                              | Idempotent | Spec                                         |
|------------------------------------|------------------------------------------|------------|----------------------------------------------|
| `sentinel manage-user create`      | Create a local user account              | No (interactive) | [user-management](features/identity/user-management.md) |
| `sentinel manage-user update`      | Update an existing user account          | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user deactivate`  | Deactivate a user account                | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel manage-user set-password`| Set or reset password for a local user   | No (interactive) | [user-management](features/identity/user-management.md) |
| `sentinel manage-user unlock`      | Clear login lockout counter for a user   | Yes        | [user-management](features/identity/user-management.md) |
| `sentinel fetcher list`            | List all fetchers with current state     | Yes        | [fetcher-dashboard](features/platform/fetcher-dashboard.md) |
| `sentinel fetcher run <name>`      | Execute a fetcher synchronously          | No (by design) | [fetcher-dashboard](features/platform/fetcher-dashboard.md) |
