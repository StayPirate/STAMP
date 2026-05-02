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

Authentication is handled via SSO (see future
`docs/features/sso-authentication.md`). There are no local login/logout
endpoints. Any SUSE employee can authenticate via id.suse.com.

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
GET /api/v1/users/{id}
```

Get user detail including roles (with `ad_group_cn`) and resolved manager.
Public endpoint (read-only).

```
PUT /api/v1/users/{id}/roles
```

Replace all manual roles for a user. Admin only. AD-derived roles are
never affected by this endpoint.

Request body:
```json
{ "roles": ["admin", "vulnerability_analyst"] }
```

Semantics: the provided list becomes the complete set of manual roles for
the user. Roles present in the list but not currently assigned are added.
Manual roles currently assigned but absent from the list are removed.
AD-derived roles are unchanged regardless of whether they appear in the
list. An empty list removes all manual roles.

Response: the updated user object with all roles (manual and AD-derived).

See `docs/features/ldap-directory.md` for details on AD-derived roles.

User creation is handled by the LDAP directory sync — there is no manual
user creation endpoint. See `docs/features/ldap-directory.md`.

### Role Mappings (Admin only)

See `docs/features/ldap-directory.md` for detailed endpoint
specifications. Admins configure mappings from AD groups to Sentinel roles
via `GET/POST/DELETE /api/v1/admin/role-mappings`.

### User Activation (Admin only)

```
PATCH /api/v1/users/{id}/active
```

Set the active status of a user. Admin only.

Request body:
```json
{ "active": true }
```

Semantics: sets `User.active` to the provided value. Setting `active: false`
deactivates the user (subsequent authenticated requests return 401). Setting
`active: true` reactivates the user (restores authentication ability only —
previously reassigned tickets and revoked API keys are NOT restored).

Constraints:
- An admin cannot deactivate themselves (returns 409 Conflict)
- Setting the same value as current is a no-op (returns 200 with unchanged
  user)

Response: the updated user object.

## Implementation Details

### Authentication Mechanism

TBD -- options under consideration:
- JWT tokens (stateless, good for API clients)
- Session-based (simpler, better for SPA)

Decision will be made during implementation of this feature.

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

Not applicable — Sentinel does not store or manage passwords. Authentication
is via SSO (see future `docs/features/sso-authentication.md`).

## Data Model

See `docs/data-model.md`. Key tables:

- **User**: username, email, active status, LDAP fields (ldap_uid,
  ldap_dn, manager_uid, ldap_synced_at)
- **UserRole**: junction table linking users to roles with `ad_group_cn`
  (AD group name or `_manual` for manual assignments)
- **RoleMapping**: maps AD group names to Sentinel roles
- **Role** enum: `Admin`, `Vulnerability Analyst`

## UI Requirements

### Login Page

Login is handled via SSO redirect to id.suse.com — no local login form.
See future `docs/features/sso-authentication.md`.

### User Management Page (Admin only)

- Table of all users with their assigned roles and role origins
- Edit user roles (add/remove manual roles; AD-derived roles shown as
  locked)
- Activate/deactivate user (note: deactivation is normally automatic via
  LDAP sync)
- Users are created by the LDAP directory sync — no manual user creation
  form. In environments without AD access, local users can be created
  via CLI. See `docs/features/ldap-directory.md` and
  `docs/features/local-user-management.md`

### User Profile

- View own profile and roles (with origin)

## Business Rules

1. An admin cannot remove their own Admin role via the API. The Admin
   role can be removed from a user only by a different admin, by the CLI,
   or by system actions (LDAP sync). This protects against accidental
   self-lockout. If the system ends up with zero active admins (e.g., the
   last admin is deactivated by LDAP sync), recovery is possible via CLI:
   `sentinel manage-user update --username <user> --add-role admin`
2. Users cannot add roles to themselves (only admins can modify other
   users' roles)
3. Users cannot deactivate their own account
4. Deactivated users cannot authenticate. The `active` status is checked on
   every authenticated request. If a user is deactivated while holding a
   valid session or token, the next request to any protected endpoint
   returns 401 Unauthorized. There is no proactive token/session
   invalidation — the per-request check is sufficient
5. All authentication events are logged (login, logout, failed attempts)
6. Session timeout: TBD (configurable)
7. A user with no roles has the same access as an unauthenticated user
   (read-only on public data)
8. Admin bootstrap: run `sentinel fetcher run sync_ldap_directory` to
   populate users from AD, then
   `sentinel manage-user update --username <username> --add-role admin` to
   assign the first Admin role. See `docs/features/ldap-directory.md`
