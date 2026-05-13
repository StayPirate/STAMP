# Role-Based Access Control (RBAC)

## Purpose

Control access to platform features based on user roles. Users can hold
zero, one, or multiple roles. Users without a role — and unauthenticated
callers — have read-only access to public data.

## Access Levels

### Public

No authentication required. Read-only access to platform data:
- View users (list and detail)
- View tickets, CVEs, and products
- View fetcher dashboard (list, detail, charts, run history, error messages)

### Authenticated

Any logged-in user, regardless of role. Includes all Public access plus:
- Logout (terminate own session)
- View own profile (`/api/v1/users/me`)
- Manage own API keys (list, create, revoke)
- View maintainer dashboard (own pending, in-progress, and completed packages)

### Vulnerability Analyst

Operates the triage and assessment workflow:
- Create tickets manually (see `docs/features/tickets/tickets.md`)
- Assign and reassign tickets
- Change ticket status (New, Analysis, Analyzed, Resolved, Ignored,
  Duplicated)
- Mark tickets as duplicate and revert duplicate status
- Associate a CVE with a ticket (see `docs/features/tickets/tickets.md`)
- Set and update severity override for tickets without CVE
- Add and remove packages from tickets
- Change track and product affectedness status
- Add, edit, and delete SUSE CVSS assessments
- Add, edit, and delete ticket references

### Admin

Administers the platform:
- Manage users
- Manage system settings
- Manage fetchers
- Remove CVE from ticket
- Soft-delete and restore tickets

Admin does NOT inherit Vulnerability Analyst permissions. A user who needs both
capabilities must hold both roles.

## Permission Matrix

### Vulnerability Analyst Operations

| Action                           | Admin | VA  | Authenticated | Public |
|----------------------------------|-------|-----|---------------|--------|
| Create ticket manually           | No    | Yes | No            | No     |
| Assign/reassign ticket           | No    | Yes | No            | No     |
| Change ticket status             | No    | Yes | No            | No     |
| Mark as duplicate / revert       | No    | Yes | No            | No     |
| Associate CVE with ticket        | No    | Yes | No            | No     |
| Set/update severity override     | No    | Yes | No            | No     |
| Add/remove packages              | No    | Yes | No            | No     |
| Change track/product status      | No    | Yes | No            | No     |
| Add/edit/delete SUSE CVSS        | No    | Yes | No            | No     |
| Add/edit/delete references       | No    | Yes | No            | No     |

> **Assignment target constraint**: the "Assign/reassign ticket" permission
> allows any VA to assign a ticket, but the target user MUST also hold the
> `vulnerability_analyst` role. Assigning to a user without this role is
> rejected with 400 Bad Request.

### Admin Operations

| Action                           | Admin | VA  | Authenticated | Public |
|----------------------------------|-------|-----|---------------|--------|
| Remove CVE from ticket           | Yes   | No  | No            | No     |
| Manage user roles                | Yes   | No  | No            | No     |
| Manage role mappings             | Yes   | No  | No            | No     |
| View/update system settings      | Yes   | No  | No            | No     |
| Soft-delete ticket               | Yes   | No  | No            | No     |
| Restore deleted ticket           | Yes   | No  | No            | No     |
| View deleted tickets             | Yes   | No  | No            | No     |
| Trigger manual fetcher run       | Yes   | No  | No            | No     |
| Enable/disable fetchers          | Yes   | No  | No            | No     |
| Modify fetcher config            | Yes   | No  | No            | No     |
| View fetcher audit log           | Yes   | No  | No            | No     |
| View fetcher error tracebacks    | Yes   | No  | No            | No     |

### Authenticated Operations

| Action                           | Admin | VA  | Authenticated | Public |
|----------------------------------|-------|-----|---------------|--------|
| Logout                           | Yes   | Yes | Yes           | No     |
| View own profile                 | Yes   | Yes | Yes           | No     |
| Manage own API keys              | Yes   | Yes | Yes           | No     |
| View maintainer dashboard        | Yes   | Yes | Yes           | No     |

### Public Operations

| Action                           | Admin | VA  | Authenticated | Public |
|----------------------------------|-------|-----|---------------|--------|
| View tickets / CVEs (active)     | Yes   | Yes | Yes           | Yes    |
| View products                    | Yes   | Yes | Yes           | Yes    |
| View ticket references           | Yes   | Yes | Yes           | Yes    |
| View fetcher dashboard           | Yes   | Yes | Yes           | Yes    |

## Endpoint Permission Map

This section is a **derived summary index** of access control rules. The
authoritative source for each endpoint's access level is the owning
specification linked in the last column. This table does NOT define
endpoint behavior, parameters, response schemas, or error codes.

When adding a new endpoint to any feature spec, add a corresponding row
here with the required access level and a link to the owning spec.

### Authentication

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| POST | `/api/v1/auth/login` | Public | [local-authentication](local-authentication.md#post-apiv1authlogin) |
| GET | `/api/v1/auth/sso/authorize` | Public | [sso-authentication](sso-authentication.md#get-apiv1authssoauthorize) |
| POST | `/api/v1/auth/sso/callback` | Public | [sso-authentication](sso-authentication.md#post-apiv1authssocallback) |
| GET | `/api/v1/auth/providers` | Public | [sso-authentication](sso-authentication.md#get-apiv1authproviders) |
| POST | `/api/v1/auth/logout` | Authenticated | [authentication](authentication.md#post-apiv1authlogout) |

### Users

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/users/me` | Authenticated | [authentication](authentication.md#get-apiv1usersme) |
| GET | `/api/v1/users` | Public | [user-management](user-management.md#get-apiv1users) |
| GET | `/api/v1/users/{user}` | Public | [user-management](user-management.md#get-apiv1usersuser) |

### API Keys

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/api-keys` | Authenticated | [authentication](authentication.md#get-apiv1api-keys) |
| POST | `/api/v1/api-keys` | Authenticated | [authentication](authentication.md#post-apiv1api-keys) |
| POST | `/api/v1/api-keys/{key_id}/revoke` | Authenticated | [authentication](authentication.md#post-apiv1api-keyskey_idrevoke) |

### Tickets

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/tickets` | Public | [tickets](../tickets/tickets.md#other-ticket-endpoints) |
| GET | `/api/v1/tickets/{ticket_id}` | Public | [tickets](../tickets/tickets.md#other-ticket-endpoints) |
| POST | `/api/v1/tickets` | Vulnerability Analyst | [tickets](../tickets/tickets.md#create-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/associate-cve` | Vulnerability Analyst | [tickets](../tickets/tickets.md#associate-cve) |
| DELETE | `/api/v1/tickets/{ticket_id}/cve` | Admin | [tickets](../tickets/tickets.md#remove-cve-from-ticket-admin-only) |
| PATCH | `/api/v1/tickets/{ticket_id}/severity` | Vulnerability Analyst | [tickets](../tickets/tickets.md#update-severity-override) |
| POST | `/api/v1/tickets/{ticket_id}/assign` | Vulnerability Analyst | [tickets](../tickets/tickets.md#other-ticket-endpoints) |
| POST | `/api/v1/tickets/{ticket_id}/ignore` | Vulnerability Analyst | [tickets](../tickets/tickets.md#other-ticket-endpoints) |
| POST | `/api/v1/tickets/{ticket_id}/duplicate` | Vulnerability Analyst | [tickets](../tickets/tickets.md#other-ticket-endpoints) |
| POST | `/api/v1/tickets/{ticket_id}/revert-duplicate` | Vulnerability Analyst | [tickets](../tickets/tickets.md#other-ticket-endpoints) |
| DELETE | `/api/v1/tickets/{ticket_id}` | Admin | [tickets](../tickets/tickets.md#other-ticket-endpoints) |
| POST | `/api/v1/tickets/{ticket_id}/restore` | Admin | [tickets](../tickets/tickets.md#other-ticket-endpoints) |

### Ticket Packages

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| POST | `/api/v1/tickets/{ticket_id}/packages` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#add-package-to-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/exclude` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#soft-delete-package-from-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/restore` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#restore-package) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/exclude` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#soft-delete-track) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/restore` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#restore-track) |
| PATCH | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#change-track-status) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}/exclude` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#soft-delete-product) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}/restore` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#restore-product) |
| PATCH | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}` | Vulnerability Analyst | [package-tracking](../packages/package-tracking.md#override-product-status) |

### Products

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/products` | Public | [product-catalog](../packages/product-catalog.md#list-products) |

### Ticket References

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/tickets/{ticket_id}/references` | Public | [references](../ui/references.md#list-references) |
| POST | `/api/v1/tickets/{ticket_id}/references` | Vulnerability Analyst | [references](../ui/references.md#add-reference) |
| PATCH | `/api/v1/tickets/{ticket_id}/references/{reference_id}` | Vulnerability Analyst | [references](../ui/references.md#update-reference) |
| DELETE | `/api/v1/tickets/{ticket_id}/references/{reference_id}` | Vulnerability Analyst | [references](../ui/references.md#delete-reference) |

### CVSS Assessments

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/tickets/{ticket_id}/cvss` | Public | [cvss-scoring](../tickets/cvss-scoring.md#get-cvss-assessments-for-a-cve) |
| POST | `/api/v1/tickets/{ticket_id}/cvss/suse` | Vulnerability Analyst | [cvss-scoring](../tickets/cvss-scoring.md#set-or-update-suse-cvss-assessment) |
| DELETE | `/api/v1/tickets/{ticket_id}/cvss/suse/{cvss_version}` | Vulnerability Analyst | [cvss-scoring](../tickets/cvss-scoring.md#delete-suse-cvss-assessment) |

### Ticket Events

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/tickets/{ticket_id}/events` | Public | [ticket-history](../tickets/ticket-history.md#list-ticket-events) |

### Submission Tracking

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/tickets/{ticket_id}/submission-requests` | Public | [ibs-submission-tracking](../packages/ibs-submission-tracking.md#get-apiv1ticketsticket_idsubmission-requests) |
| GET | `/api/v1/tickets/{ticket_id}/release-requests` | Public | [ibs-submission-tracking](../packages/ibs-submission-tracking.md#get-apiv1ticketsticket_idrelease-requests) |

### Fetchers

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/fetchers` | Public | [fetcher-dashboard](../platform/fetcher-dashboard.md#list-fetchers) |
| GET | `/api/v1/fetchers/{fetcher_name}/runs` | Public | [fetcher-dashboard](../platform/fetcher-dashboard.md#list-fetcher-runs) |
| GET | `/api/v1/fetchers/{fetcher_name}/runs/{run_id}` | Public | [fetcher-dashboard](../platform/fetcher-dashboard.md#get-fetcher-run-detail) |
| GET | `/api/v1/fetchers/{fetcher_name}/timeline` | Public | [fetcher-dashboard](../platform/fetcher-dashboard.md#get-fetcher-run-timeline-data) |
| POST | `/api/v1/fetchers/{fetcher_name}/trigger` | Admin | [fetcher-dashboard](../platform/fetcher-dashboard.md#trigger-fetcher-admin-only) |
| GET | `/api/v1/fetchers/{fetcher_name}/config` | Admin | [fetcher-dashboard](../platform/fetcher-dashboard.md#get-fetcher-config-admin-only) |
| PATCH | `/api/v1/fetchers/{fetcher_name}/config` | Admin | [fetcher-dashboard](../platform/fetcher-dashboard.md#update-fetcher-config-admin-only) |
| GET | `/api/v1/fetchers/{fetcher_name}/audit-log` | Admin | [fetcher-dashboard](../platform/fetcher-dashboard.md#get-fetcher-audit-log-admin-only) |
| GET | `/api/v1/ibs-consumer/status` | Public | [fetcher-dashboard](../platform/fetcher-dashboard.md#ibs-rabbitmq-consumer-status) |

### Maintainer Dashboard

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/my/packages/pending` | Authenticated | [maintainer-dashboard](../ui/maintainer-dashboard.md#get-apiv1mypackagespending) |
| GET | `/api/v1/my/packages/in-progress` | Authenticated | [maintainer-dashboard](../ui/maintainer-dashboard.md#get-apiv1mypackagesin-progress) |
| GET | `/api/v1/my/packages/completed` | Authenticated | [maintainer-dashboard](../ui/maintainer-dashboard.md#get-apiv1mypackagescompleted) |
| GET | `/api/v1/my/packages/ticket/{ticket_id}` | Authenticated | [maintainer-dashboard](../ui/maintainer-dashboard.md#get-apiv1mypackagesticketticket_id) |

### Administration

| Method | Endpoint | Access | Owning Spec |
|--------|----------|--------|-------------|
| GET | `/api/v1/admin/settings` | Admin | [admin](../platform/admin.md#get-system-settings) |
| PATCH | `/api/v1/admin/settings` | Admin | [admin](../platform/admin.md#update-system-settings) |
| GET | `/api/v1/admin/api-keys` | Admin | [authentication](authentication.md#get-apiv1adminapi-keys) |
| POST | `/api/v1/admin/api-keys/{key_id}/revoke` | Admin | [authentication](authentication.md#post-apiv1adminapi-keyskey_idrevoke) |
| PATCH | `/api/v1/admin/users/{user}` | Admin | [user-management](user-management.md#patch-apiv1adminusersuser) |
| POST | `/api/v1/admin/users/{user}/roles` | Admin | [user-management](user-management.md#post-apiv1adminusersuserroles) |
| POST | `/api/v1/admin/users/{user}/password` | Admin | [user-management](user-management.md#post-apiv1adminusersuserpassword) |
| POST | `/api/v1/admin/users/{user}/deactivate` | Admin | [user-management](user-management.md#post-apiv1adminusersuserdeactivate) |
| POST | `/api/v1/admin/users/{user}/reactivate` | Admin | [user-management](user-management.md#post-apiv1adminusersuserreactivate) |
| GET | `/api/v1/admin/users/{user}/deactivation-impact` | Admin | [user-management](user-management.md#get-apiv1adminusersuserdeactivation-impact) |
| POST | `/api/v1/admin/users/{user}/unlock` | Admin | [user-management](user-management.md#post-apiv1adminusersuserunlock) |
| GET | `/api/v1/admin/role-mappings` | Admin | [ad-integration](ad-integration.md#role-mapping-management) |
| POST | `/api/v1/admin/role-mappings` | Admin | [ad-integration](ad-integration.md#role-mapping-management) |
| POST | `/api/v1/admin/role-mappings/preview` | Admin | [ad-integration](ad-integration.md#role-mapping-management) |
| DELETE | `/api/v1/admin/role-mappings/{id}` | Admin | [ad-integration](ad-integration.md#role-mapping-management) |

**Notes**:
- "Public" = no authentication required
- "Authenticated" = any logged-in user regardless of role
- "Admin" = requires the `admin` role
- User creation: AD users are created by the LDAP directory sync (see
  [ad-integration](ad-integration.md#ldap-sync-fetcher)); local users are created by admins
  via CLI (see [user-management](user-management.md#cli-commands))

## Implementation Details

### Authentication Mechanism

JWT with session-backed liveness checks. See
`docs/features/identity/authentication.md` for the full design: token format,
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

Sentinel stores bcrypt password hashes (with SHA-256 pre-hash) for local
users only. AD users do not have local passwords. See
`docs/features/identity/local-authentication.md` for password policy and
hashing parameters.

## Data Model

See `docs/data-model.md`. Key tables:

- **User**: username, email, active status, AD fields (ad_object_guid,
  ad_dn, manager_id, ad_synced_at)
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
  — cannot be removed via UI or API. See `docs/features/identity/ad-integration.md`.

### Coexistence rules

1. The same role can be held by a user from multiple origins
   simultaneously. Each origin creates a distinct `UserRole` record
   (unique constraint: `user_id, role, ad_group_cn`).
2. **Manual assignment when role already exists via AD**: creates a new
   `UserRole` record with `ad_group_cn = '_manual'`. The user now holds
   the role from both sources. If the AD group is later revoked, only
    the AD-derived record (identified by its `ad_group_cn` value) is removed — the manual assignment persists.
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
(redirect to id.suse.com) and a local username/password form. The SSO
button is rendered only when SSO is configured (the frontend determines
this by calling `GET /api/v1/auth/providers`); the local form is always
visible. See `docs/features/identity/authentication.md` for the shared
framework and `docs/features/identity/sso-authentication.md` /
`docs/features/identity/local-authentication.md` for each provider's flow.

### User Management Page (Admin only)

- Table of all users with their assigned roles and role origins
- Edit user roles (add/remove manual roles; AD-derived roles shown as
  locked)
- Activate/deactivate local users (AD users have their active status
  managed exclusively by directory sync — see
  `docs/features/identity/user-service.md`, AD Active Status Ownership)
- Users are created by the LDAP directory sync (AD users) or by admins
  via CLI and admin UI (local users). See
  `docs/features/identity/ad-integration.md` and
  `docs/features/identity/user-management.md`

### User Profile

- View own profile and roles (with origin)

## Business Rules

1. An admin cannot remove their own Admin role. This is enforced by
   `user_service.update_roles()` for any entry point where
   `acting_user_id` is set. System actions (LDAP sync, CLI) pass
   `acting_user_id = None` and are exempt. See
   `docs/features/identity/user-service.md`. This guard ensures that via
   UI/API, admins cannot accidentally eliminate all admin users — the
   acting admin always retains the role. For the full "zero admins"
   scenario (possible only via CLI/system operations) and recovery
   procedure, see `docs/features/identity/user-management.md`, Business Rule 2
2. Users cannot add roles to themselves (only admins can modify other
   users' roles)
3. Users cannot deactivate their own account (enforced by
   `user_service.deactivate_user()` — see
   `docs/features/identity/user-service.md`)
4. AD users cannot be manually deactivated or reactivated — their
   active status is controlled exclusively by Active Directory via LDAP
   sync (enforced by `user_service.deactivate_user()` and
   `user_service.reactivate_user()` — see
   `docs/features/identity/user-service.md`, AD Active Status Ownership)
5. Deactivated users cannot authenticate. On deactivation, all API keys
   are revoked and all active sessions are invalidated (proactively,
   before marking the user inactive). Additionally, the middleware
   checks `User.active` on every request as a defense-in-depth measure.
   See `docs/features/identity/authentication.md` (Deactivation ordering) and
   `docs/features/identity/user-service.md`
6. All authentication events are logged (login, logout, failed attempts)
7. Session duration: JWT expires after 72 hours of inactivity (refreshed
   transparently via sliding session for active users). Maximum session
   lifetime is 30 days regardless of activity. See
   `docs/features/identity/authentication.md`
8. A user with no roles has the same access as an unauthenticated user
   (read-only on public data)
9. Admin bootstrap: run `sentinel fetcher run sync_ldap_directory` to
   populate users from AD, then
   `sentinel manage-user update --username <username> --add-role admin` to
   assign the first Admin role. See `docs/features/identity/ad-integration.md`
