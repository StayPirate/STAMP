# Role-Based Access Control (RBAC)

## Purpose

Control access to platform features based on user roles. Users can hold
zero, one, or multiple roles. An unauthenticated user or an authenticated
user with no roles has read-only access to public data.

## Access Levels

### Unauthenticated / No Roles

Read-only access to public data:
- View tickets, CVEs, and products
- View fetcher dashboard (list, detail, charts, run history, error messages)

### Vulnerability Analyst

Operates the triage and assessment workflow:
- Create tickets manually (see `docs/features/tickets.md`)
- Assign and reassign tickets
- Change ticket status (New, Analysis, Analyzed, Resolved, Ignored,
  Duplicated)
- Mark tickets as duplicate and revert duplicate status
- Associate a CVE with a ticket (see `docs/features/tickets.md`)
- Set and update severity override for tickets without CVE
- Add and remove packages from tickets
- Change codestream and product affectedness status
- Add, edit, and delete SUSE CVSS assessments
- Add, edit, and delete ticket references

### Admin

Administers the platform:
- Manage users (update roles, deactivate)
- View and update system settings (e.g., default CVSS version)
- Remove CVE from ticket
- Soft-delete and restore tickets
- View soft-deleted tickets
- Trigger manual fetcher runs
- Enable and disable fetchers
- Modify fetcher configuration (schedule, parameters)
- View fetcher audit log
- View fetcher error tracebacks

Admin does NOT inherit Vulnerability Analyst permissions. A user who needs both
capabilities must hold both roles.

## Permission Matrix

### Vulnerability Analyst Operations

| Action                           | Admin | VA  | Unauth |
|----------------------------------|-------|-----|--------|
| Create ticket manually           | No    | Yes | No     |
| Assign/reassign ticket           | No    | Yes | No     |
| Change ticket status             | No    | Yes | No     |
| Mark as duplicate / revert       | No    | Yes | No     |
| Associate CVE with ticket        | No    | Yes | No     |
| Set/update severity override     | No    | Yes | No     |
| Add/remove packages              | No    | Yes | No     |
| Change codestream/product status | No    | Yes | No     |
| Add/edit/delete SUSE CVSS        | No    | Yes | No     |
| Add/edit/delete references       | No    | Yes | No     |

### Admin Operations

| Action                           | Admin | VA  | Unauth |
|----------------------------------|-------|-----|--------|
| Remove CVE from ticket           | Yes   | No  | No     |
| Manage user roles                | Yes   | No  | No     |
| View/update system settings      | Yes   | No  | No     |
| Soft-delete ticket               | Yes   | No  | No     |
| Restore deleted ticket           | Yes   | No  | No     |
| View deleted tickets             | Yes   | No  | No     |
| Trigger manual fetcher run       | Yes   | No  | No     |
| Enable/disable fetchers          | Yes   | No  | No     |
| Modify fetcher config            | Yes   | No  | No     |
| View fetcher audit log           | Yes   | No  | No     |
| View fetcher error tracebacks    | Yes   | No  | No     |

### Public Operations

| Action                           | Admin | VA  | Unauth |
|----------------------------------|-------|-----|--------|
| View tickets / CVEs (active)     | Yes   | Yes | Yes    |
| View products                    | Yes   | Yes | Yes    |
| View ticket references           | Yes   | Yes | Yes    |
| View fetcher dashboard           | Yes   | Yes | Yes    |

## API Endpoints

### Authentication

Authentication is handled via two providers: SSO for LDAP-synced users
(see `docs/features/sso-authentication.md`) and local credentials for
local users (see `docs/features/local-authentication.md`). API keys
provide non-interactive access for both user types (see
`docs/features/authentication.md`).

### Current User

```
GET /api/v1/users/me
```

Response: current user profile and roles (with `ad_group_cn`). Requires
authentication.

### User Management (Admin only)

```
GET /api/v1/users
```

List/search all users. Public endpoint (read-only). Supports `search`,
`active`, `role`, `has_role` query parameters. See
`docs/features/ldap-directory.md` for details.

```
GET /api/v1/users/{user}
```

Get user detail including roles (with `ad_group_cn`) and resolved manager.
Public endpoint (read-only).

```
POST /api/v1/admin/users/{user}/roles
```

Add or remove manual roles for a user. Admin only. AD-derived roles are
never affected by this endpoint. The full endpoint specification is
defined in `docs/features/user-management.md` (Admin API endpoints).

Semantics: delegates to `user_service.update_roles()` with
`acting_user_id` set to the authenticated admin's user ID and roles as
`(role, '_manual')` pairs. See `docs/features/user-lifecycle.md` for the
full service contract.

See `docs/features/ldap-directory.md` for details on AD-derived roles.

User creation for SSO users is handled by the LDAP directory sync (see
`docs/features/ldap-directory.md`). Local users can be created by admins
via CLI (see `docs/features/user-management.md`).

### Role Mappings (Admin only)

See `docs/features/ldap-directory.md` for detailed endpoint
specifications. Admins configure mappings from AD groups to Sentinel roles
via `GET/POST/DELETE /api/v1/admin/role-mappings`.

### User Activation (Admin only)

```
PATCH /api/v1/admin/users/{user}/active
```

Set the active status of a user. Admin only. The full endpoint
specification (request/response schema, error codes, constraints) is
defined in `docs/features/user-management.md` (Admin API endpoints).

Semantics: delegates to `user_service.deactivate_user()` (when
`active: false`) or `user_service.reactivate_user()` (when
`active: true`). See `docs/features/user-lifecycle.md` for the full
side effect contract.

## Implementation Details

### Authentication Mechanism

JWT with session-backed liveness checks. See
`docs/features/authentication.md` for the full design: token format,
session management, API keys, and middleware behavior.

### Permission Checking

- Permissions are checked at the API endpoint level using FastAPI
  dependencies
- A `require_role()` dependency factory returns a dependency that checks
  whether the current user holds the required role
- Example: `Depends(require_role(Role.VULNERABILITY_ANALYST))`
- Public endpoints (ticket list, CVE list, product list, fetcher dashboard)
  do not require authentication
- The `require_role()` check queries the `UserRole` junction table
- Role changes take effect on the user's next request. A request already
  in progress continues with the permissions loaded at its start. This is
  the expected behavior and requires no additional synchronization

### Password Security

Sentinel stores Argon2id password hashes for local users only. SSO users
do not have local passwords. See
`docs/features/local-authentication.md` for password policy and hashing
parameters.

## Data Model

See `docs/data-model.md`. Key tables:

- **User**: username, email, active status, LDAP fields (ldap_uid,
  ldap_dn, manager_uid, ldap_synced_at)
- **UserRole**: junction table linking users to roles with `ad_group_cn`
  (AD group name or `_manual` for manual assignments)
- **RoleMapping**: maps AD group names to Sentinel roles
- **Role** enum: `Admin`, `Vulnerability Analyst`

## Role Origins and Coexistence

A user can acquire a role from two independent sources (origins):

- **Manual** (`ad_group_cn = '_manual'`): assigned by an admin via CLI
  or API. Can be removed by an admin at any time.
- **AD-derived** (`ad_group_cn = <group CN>`): derived from AD group
  membership during LDAP sync. Managed exclusively by the sync process
  — cannot be removed via UI or API. See `docs/features/ldap-directory.md`.

### Coexistence rules

1. The same role can be held by a user from multiple origins
   simultaneously. Each origin creates a distinct `UserRole` record
   (unique constraint: `user_id, role, ad_group_cn`).
2. **Manual assignment when role already exists via AD**: creates a new
   `UserRole` record with `ad_group_cn = '_manual'`. The user now holds
   the role from both sources. If the AD group is later revoked, only
   the `ldap_sync` record is removed — the manual assignment persists.
3. **AD derivation when role already exists manually**: the LDAP sync
   creates a new `UserRole` record with the AD group's `ad_group_cn`.
   The user now holds the role from both sources. If the admin later
   removes the manual assignment, only the `_manual` record is removed
   — the AD-derived assignment persists.
4. A role is effectively held as long as **at least one** `UserRole`
   record exists for that `(user_id, role)` pair, regardless of origin.
5. Removing a manual role never affects AD-derived records; removing an
   AD-derived role (via sync) never affects manual records. The two
   lifecycles are fully independent.

### UI representation

In the Admin UI (user detail page and user management page), each role
displays badge(s) indicating its active origin(s):

- A role held only manually shows a "Manual" badge
- A role held only via AD shows a badge with the AD group name (locked,
  not removable by the admin)
- A role held from both sources shows both badges — the admin can remove
  the manual assignment but the AD badge remains (locked)

This gives admins full visibility into why a user has a given role and
what would happen if they remove the manual assignment.

## UI Requirements

### Login Page

The login page displays both authentication options: an SSO button
(redirect to id.suse.com) and a local username/password form. Both are
always visible. See `docs/features/authentication.md` for the shared
framework and `docs/features/sso-authentication.md` /
`docs/features/local-authentication.md` for each provider's flow.

### User Management Page (Admin only)

- Table of all users with their assigned roles and role origins
- Edit user roles (add/remove manual roles; AD-derived roles shown as
  locked)
- Activate/deactivate user (note: deactivation is normally automatic via
  LDAP sync)
- Users are created by the LDAP directory sync (SSO users) or by admins
  via CLI and admin UI (local users). See
  `docs/features/ldap-directory.md` and
  `docs/features/user-management.md`

### User Profile

- View own profile and roles (with origin)

## Business Rules

1. An admin cannot remove their own Admin role. This is enforced by
   `user_service.update_roles()` for any entry point where
   `acting_user_id` is set. System actions (LDAP sync, CLI) pass
   `acting_user_id = None` and are exempt. See
   `docs/features/user-lifecycle.md`. This guard ensures that via
   UI/API, admins cannot accidentally eliminate all admin users — the
   acting admin always retains the role. For the full "zero admins"
   scenario (possible only via CLI/system operations) and recovery
   procedure, see `docs/features/user-management.md`, Business Rule 2
2. Users cannot add roles to themselves (only admins can modify other
   users' roles)
3. Users cannot deactivate their own account (enforced by
   `user_service.deactivate_user()` — see
   `docs/features/user-lifecycle.md`)
4. Deactivated users cannot authenticate. On deactivation, all API keys
   are revoked and all active sessions are invalidated (proactively,
   before marking the user inactive). Additionally, the middleware
   checks `User.active` on every request as a defense-in-depth measure.
   See `docs/features/authentication.md` (Deactivation ordering) and
   `docs/features/user-lifecycle.md`
5. All authentication events are logged (login, logout, failed attempts)
6. Session duration: JWT expires after 72 hours of inactivity (refreshed
   transparently via sliding session for active users). Maximum session
   lifetime is 30 days regardless of activity. See
   `docs/features/authentication.md`
7. A user with no roles has the same access as an unauthenticated user
   (read-only on public data)
8. Admin bootstrap: run `sentinel fetcher run sync_ldap_directory` to
   populate users from AD, then
   `sentinel manage-user update --username <username> --add-role admin` to
   assign the first Admin role. See `docs/features/ldap-directory.md`
