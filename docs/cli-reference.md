# CLI Reference

Sentinel provides a command-line interface via the `sentinel` entry point. See
`docs/conventions.md` (CLI Conventions) for framework choices and design
guidelines.

## Commands

| Command                            | Description                              | Spec                                         |
|------------------------------------|------------------------------------------|----------------------------------------------|
| `sentinel manage-user create`      | Create a local user account              | [user-management](features/user-management.md) |
| `sentinel manage-user update`      | Update an existing user account          | [user-management](features/user-management.md) |
| `sentinel manage-user deactivate`  | Deactivate a user account                | [user-management](features/user-management.md) |
| `sentinel manage-user set-password`| Set or reset password for a local user   | [user-management](features/user-management.md) |
| `sentinel manage-user unlock`      | Clear login lockout counter for a user   | [user-management](features/user-management.md) |
| `sentinel fetcher list`            | List all fetchers with current state     | [fetcher-dashboard](features/fetcher-dashboard.md) |
| `sentinel fetcher run <name>`      | Execute a fetcher synchronously          | [fetcher-dashboard](features/fetcher-dashboard.md) |
