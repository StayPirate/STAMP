# CLI Reference

STAMP provides a command-line interface via the `stamp` entry point. See
`docs/conventions.md` (CLI Conventions) for framework choices and design
guidelines.

## Commands

| Command                     | Description                        | Guard                | Spec                                              |
|-----------------------------|------------------------------------|----------------------|----------------------------------------------------|
| `stamp manage-user create`  | Create a local user account        | `ALLOW_LOCAL_USERS`  | [local-user-management](features/local-user-management.md) |
| `stamp manage-user update`  | Update an existing user account    | —                    | [local-user-management](features/local-user-management.md) |
| `stamp manage-user delete`  | Delete a local user account        | `ALLOW_LOCAL_USERS`  | [local-user-management](features/local-user-management.md) |
| `stamp ldap-sync`           | Trigger immediate LDAP directory sync | —               | [ldap-directory](features/ldap-directory.md)       |
