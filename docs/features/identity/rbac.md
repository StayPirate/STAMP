# Role-Based Access Control (RBAC)

## Purpose

Control access to platform features using a capability-based authorization
model. Each predefined role grants a set of **capabilities** (what operations
a user can perform) and a **scope** (what resources a user can see). Users can
hold zero, one, or multiple roles. Capabilities and scopes are resolved at
request time from the user's roles.

## Authorization Model

### Capabilities

Capabilities are static enums defined in code. Each capability covers a
cohesive set of operations that are logically granted or denied together.
Endpoints are protected by a single capability via the
`require_capability()` dependency.

#### Vulnerability Analyst Capabilities

| Capability | Operations Covered |
|---|---|
| `create_ticket` | Create ticket manually |
| `triage_ticket` | Assign/reassign ticket, change ticket status (all transitions: ignore, reopen, duplicate, revert-duplicate), associate CVE with ticket, set/update severity override |
| `manage_packages` | Add/remove packages from tickets, exclude/restore (package, track, product), change track affectedness status, override product eligibility |
| `manage_cvss` | Add/edit/delete SUSE CVSS assessments |
| `manage_references` | Add/edit/delete ticket references |
| `manage_confidentiality` | Set ticket confidentiality flag, list/grant/revoke access grants |

#### Admin Capabilities

| Capability | Operations Covered |
|---|---|
| `manage_users` | Update user fields, manage user roles, reset password, deactivate/reactivate, unlock, view deactivation impact, view/revoke all API keys, view admin-scoped identity audit log |
| `manage_role_mappings` | AD role mapping CRUD, preview role mapping |
| `manage_settings` | View/update system settings, view settings audit log |
| `manage_fetchers` | Trigger manual fetcher run, enable/disable fetchers, modify fetcher config, view fetcher audit log, view error details, view error tracebacks |
| `admin_ticket_ops` | Remove CVE from ticket, force track to FIXED status |

> **Design note — capability granularity**: capabilities are intentionally
> coarse (~11 total). The current three roles are well served by grouped
> capabilities. There is no foreseeable use case for partial grants within
> a group (e.g., "can assign but not change status"). Splitting a coarse
> capability later is a bounded, mechanical refactor (~12 endpoint
> decorators + tests for the two largest groups), not an architectural
> change. The capability groupings may be refined in the future if a new
> role requires a subset of operations within an existing group.

### Scope

Scope determines the default visibility of confidential tickets. It is
orthogonal to capabilities — scope controls *what you can see*, while
capabilities control *what you can do*.

| Scope | Meaning |
|---|---|
| `all` | Unrestricted — all tickets visible including confidential |
| `non_confidential` | Confidential tickets are invisible by default (see Scope and Confidential Ticket Visibility) |

**Scope resolution**: if any of the user's roles has scope `all`, the
effective scope is `all`. Otherwise, the effective scope is
`non_confidential`. Authenticated users with no roles have an effective
scope of `non_confidential`. Unauthenticated users have no scope (treated
as `None` — only non-confidential tickets visible, no grant/bugowner
checks).

> **Design note — scope is API-layer only**: scope is enforced at the API
> layer via `confidential_ticket_filter()` in query endpoints and
> `require_accessible_ticket` in single-ticket endpoints. It does not
> apply to the service layer or background tasks. Celery workers,
> fetchers, and event consumers process all tickets (including
> confidential ones) without scope restrictions. Scope is an
> access-control concept, not a data-partitioning concept.

### Predefined Roles

Roles are static definitions mapping to a set of capabilities and a scope.
Adding a new access profile requires a code change (PR + deploy) — there
is no runtime role management by admins beyond assigning existing roles to
users.

| Role | Capabilities | Scope |
|---|---|---|
| `admin` | `manage_users`, `manage_role_mappings`, `manage_settings`, `manage_fetchers`, `admin_ticket_ops` | `all` |
| `vulnerability_analyst` | `create_ticket`, `triage_ticket`, `manage_packages`, `manage_cvss`, `manage_references`, `manage_confidentiality` | `all` |
| `automation_agent` | `create_ticket`, `triage_ticket`, `manage_packages`, `manage_cvss`, `manage_references` | `non_confidential` |

Design notes:

- `admin` does NOT inherit VA capabilities. A user needing both must hold
  both roles (unchanged from current design)
- `automation_agent` shares all VA capabilities except
  `manage_confidentiality` — bots cannot set the confidentiality flag or
  manage access grants
- A user holding multiple roles receives the **union** of all capabilities
  and the **least restrictive** scope (i.e., if any role has `all`, the
  effective scope is `all`). The admin is responsible for role assignments
  — the system does not enforce mutual exclusivity between roles
- A user with no roles has an effective scope of `non_confidential` and no
  capabilities. They can still access specific confidential tickets via
  `TicketAccessGrant` or bugowner matching — these per-ticket mechanisms
  are independent of scope

> **Design note — VA role granularity**: the VA role intentionally bundles
> triage, assessment, and package management into a single role. Sentinel
> targets a small security team where all analysts perform the full
> triage-to-resolution workflow. Introducing sub-roles would increase
> complexity for a scenario that has no current demand. The role can be
> split by adding new roles and redistributing capabilities — no
> architectural change is required.

## Access Levels

Access levels describe authentication state, not authorization. They do
not carry capabilities or scope.

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
- View own identity audit log (`/api/v1/users/me/audit-log`)
- View maintainer dashboard (own pending, in-progress, and completed packages)

## Permission Matrix

### Capability-Protected Operations

| Action | Required Capability |
|---|---|
| Create ticket manually | `create_ticket` |
| Assign/reassign ticket | `triage_ticket` |
| Change ticket status (ignore, reopen, duplicate, revert-duplicate) | `triage_ticket` |
| Associate CVE with ticket | `triage_ticket` |
| Set/update severity override | `triage_ticket` |
| Add/remove packages from tickets | `manage_packages` |
| Exclude/restore package, track, or product | `manage_packages` |
| Change track affectedness status | `manage_packages` |
| Override product eligibility | `manage_packages` |
| Add/edit/delete SUSE CVSS assessments | `manage_cvss` |
| Add/edit/delete ticket references | `manage_references` |
| Set ticket confidentiality | `manage_confidentiality` |
| Manage access grants on confidential tickets | `manage_confidentiality` |
| Remove CVE from ticket | `admin_ticket_ops` |
| Force track to FIXED status | `admin_ticket_ops` |
| Update user fields | `manage_users` |
| Manage user roles | `manage_users` |
| Reset user password | `manage_users` |
| Deactivate/reactivate user | `manage_users` |
| Unlock user | `manage_users` |
| View deactivation impact | `manage_users` |
| View/revoke all API keys | `manage_users` |
| View admin-scoped identity audit log | `manage_users` |
| AD role mapping CRUD | `manage_role_mappings` |
| Preview role mapping | `manage_role_mappings` |
| View/update system settings | `manage_settings` |
| View settings audit log | `manage_settings` |
| Trigger manual fetcher run | `manage_fetchers` |
| Enable/disable fetchers | `manage_fetchers` |
| Modify fetcher config | `manage_fetchers` |
| View fetcher audit log | `manage_fetchers` |
| View fetcher error details | `manage_fetchers` |
| View fetcher error tracebacks | `manage_fetchers` |

> **Assignment target constraint**: the `triage_ticket` capability allows
> performing assignment operations, but the target user MUST hold the
> `vulnerability_analyst` role **and be active**. Assigning to a user
> without this role or to an inactive user is rejected with 400 Bad
> Request. This is a business rule (who can own a ticket), not a
> capability check (who can invoke the endpoint). See Business Rule 10.

### Authenticated Operations

| Action | Access |
|---|---|
| Logout | Authenticated |
| View own profile | Authenticated |
| Manage own API keys | Authenticated |
| View own identity audit log | Authenticated |
| View maintainer dashboard | Authenticated |
| View ticket audit log | Authenticated |

### Public Operations

| Action | Access |
|---|---|
| View users (list and detail) | Public |
| View tickets / CVEs (active) | Public |
| View products | Public |
| View ticket references | Public |
| View CVSS assessments | Public |
| View submission/release requests | Public |
| View fetcher dashboard | Public |

## Scope and Confidential Ticket Visibility

Scope determines the **default** visibility of confidential tickets. It
does NOT override explicit per-ticket access mechanisms.

A ticket is visible to a user if ANY of the following is true:

1. The ticket is not confidential (always visible to everyone)
2. The user's effective scope is `all`
3. The user has an explicit `TicketAccessGrant` for this ticket
4. The user's email matches a `PackageBugowner` (person) for a package
   associated with this ticket
5. The user's email matches a `PackageBugownerMember` (group member) for
   a package associated with this ticket

Email comparison for rules 4 and 5 is case-insensitive, guaranteed by
normalized lowercase storage on both sides: User.email (from AD sync)
and bugowner emails (from IBS sync). A standard equality operator (`=`)
is sufficient — no runtime ILIKE or lower() is needed.

This means a user with `manage_confidentiality` capability can grant
explicit access to a confidential ticket to a bot with
`non_confidential` scope. The grant overrides the scope restriction for
that specific ticket only. The grant provides full access (read and
write) — the bot can perform any operation its capabilities allow on the
granted ticket. There is no read-only vs read-write distinction in access
grants; this is consistent with how grants work for all users.

Note that visibility alone does not imply write access. An authenticated
user with no roles who receives a `TicketAccessGrant` can see the ticket
but cannot modify it — they have no capabilities. An `automation_agent`
with a grant can both see and modify the ticket because they have
capabilities like `triage_ticket` and `manage_packages`. The two checks
are independent:

- **Scope** (+ grant/bugowner) determines: *can you see this ticket?*
- **Capability** determines: *can you perform this operation?*

Both checks must pass for a write operation to succeed.

The `confidential_ticket_filter()` function uses `caller_scope` instead
of `caller_is_privileged`:

```python
def confidential_ticket_filter(
    ...,
    caller_scope: Scope | None,     # None for unauthenticated
    caller_user_id: UUID | None,
    caller_email: str | None,
    ...
)
```

When `caller_scope` is `None` (unauthenticated), the function
short-circuits: only non-confidential tickets are returned, and
grant/bugowner checks are skipped (no user identity to match against).

## Endpoint Authorization

### `require_capability()` Dependency

Capability-protected endpoints use the `require_capability()` dependency:

```python
@router.post("/tickets")
async def create_ticket(
    ...,
    _: User = Depends(require_capability(Capability.CREATE_TICKET)),
):
```

The dependency:

1. Extracts the current user from the session/token
2. Loads the user's roles
3. Checks if any of the user's roles includes the required capability
   (using the static role definition map)
4. If yes, allows the request; if no, returns 403 with error code
   `AUTH_INSUFFICIENT_PERMISSION` and detail `"Insufficient permissions"`

The 403 response MUST NOT disclose which capability was required. The
generic message prevents information leakage about the internal
authorization model.

**Assumption**: this dependency assumes that the request has already
passed the authentication layer, which verifies `User.active` (step 5
of the authentication middleware). `require_capability()` does not
independently verify user active status. Any future authentication
mechanism that bypasses the standard middleware MUST replicate the
active-user check before the request reaches capability-protected
endpoints.

### Authorization Chain Evaluation Order

For ticket endpoints that are capability-protected and operate on a
specific ticket, the authorization chain evaluates in this order:

1. **Authentication** (`get_current_user`) — extract the current user
   from the session/token. Returns 401 if not authenticated.
2. **Capability** (`require_capability`) — check the user has the
   required capability. Returns 403 `AUTH_INSUFFICIENT_PERMISSION` if
   not. This check is user-level (does not depend on the specific
   ticket), so it does not leak information about ticket existence.
3. **Ticket accessibility** (`require_accessible_ticket`) — check that
   the ticket exists and is visible to the caller (scope + grant +
   bugowner). Returns 404 for invisible tickets.

For mutation endpoints, a fourth check occurs at the **service layer**
(not as an API dependency):

4. **Operability guard** (`ensure_ticket_operable`) — rejects mutations
   on tickets in a manual-zone status (409 `TICKET_NOT_MUTABLE`). This
   check executes under the `FOR UPDATE` lock and is the authoritative
   enforcement.

This ordering is security-significant: the capability check (step 2)
fires before the accessibility check (step 3). A user without the
required capability receives 403 regardless of whether the ticket
exists — this prevents probing for ticket existence via differentiated
error codes.

For CVE endpoints that are capability-protected and operate on a
specific CVE, the same pattern applies with `require_accessible_cve`
as step 3 — returning `404 CVE_NOT_FOUND` for non-existent or
inaccessible CVEs (see `docs/api-spec.md`, CVE Accessibility Check).
For `GET /cves/{cve_id}/cvss` (Public), only step 3 applies.

For non-ticket, non-CVE endpoints (user management, settings,
fetchers), only steps 1 and 2 apply.

### Conditional Capability Checks

Some endpoints apply conditional capability requirements on optional
parameters or request body fields. Two distinct patterns exist:

#### Soft Conditional Check (Silent Ignore)

Applies to **query parameters** on list/read endpoints. When the caller
lacks the required capability, the parameter is silently ignored — the
request succeeds but the parameter has no effect. The endpoint never
returns 403 for a missing query parameter on a Public or Authenticated
endpoint; 403 is reserved for capability-protected endpoints where the
caller cannot access the endpoint itself.

The capability check is performed inline in the handler (not via the
`require_capability()` dependency) only when the parameter is present.

The server logs the ignored parameter at DEBUG level (caller identity +
parameter name). The response includes an `X-Ignored-Parameters` header
listing parameter names that were silently ignored (see Open Points).

When a parameter is silently ignored due to insufficient capability, the
backend SHOULD emit a DEBUG-level log entry recording the caller identity
and the ignored parameter name. The log MUST NOT include the parameter
value to avoid log injection.

| Endpoint | Parameter | Required Capability |
|----------|-----------|-------------------|
| *(none currently defined)* | | |

#### Hard Conditional Check (403 Rejection)

Applies to **request body fields** on mutation endpoints. When the caller
provides a field that requires an additional capability and lacks it, the
endpoint returns 403 (AUTH_INSUFFICIENT_PERMISSION). The field is NOT
silently ignored — this is a hard authorization failure.

| Endpoint | Field | Required Capability |
|----------|-------|-------------------|
| POST /api/v1/tickets | is_confidential: true | manage_confidentiality |

See Business Rule 13 for the full specification of this check.

## Endpoint Permission Map

This section is a **derived summary index** of access control rules. The
authoritative source for each endpoint's authorization is the owning
specification linked in the last column. This table does NOT define
endpoint behavior, parameters, response schemas, or error codes.

When adding a new endpoint to any feature spec, add a corresponding row
here with the required authorization level and a link to the owning spec.

### Authentication

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| POST | `/api/v1/auth/login` | Public | [local-authentication](local-authentication.md#post-apiv1authlogin) |
| GET | `/api/v1/auth/sso/authorize` | Public | [sso-authentication](sso-authentication.md#get-apiv1authssoauthorize) |
| POST | `/api/v1/auth/sso/callback` | Public | [sso-authentication](sso-authentication.md#post-apiv1authssocallback) |
| GET | `/api/v1/auth/providers` | Public | [sso-authentication](sso-authentication.md#get-apiv1authproviders) |
| POST | `/api/v1/auth/logout` | Authenticated | [authentication](authentication.md#post-apiv1authlogout) |

### Users

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/users/me` | Authenticated | [authentication](authentication.md#get-apiv1usersme) |
| GET | `/api/v1/users/me/audit-log` | Authenticated | [identity-audit-log](identity-audit-log.md#list-my-identity-audit-events) |
| GET | `/api/v1/users` | Public | [user-management](user-management.md#get-apiv1users) |
| GET | `/api/v1/users/{user}` | Public | [user-management](user-management.md#get-apiv1usersuser) |

### API Keys

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/api-keys` | Authenticated | [authentication](authentication.md#get-apiv1api-keys) |
| POST | `/api/v1/api-keys` | Authenticated | [authentication](authentication.md#post-apiv1api-keys) |
| POST | `/api/v1/api-keys/{key_id}/revoke` | Authenticated | [authentication](authentication.md#post-apiv1api-keyskey_idrevoke) |

### Tickets

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/tickets` | Public ‡admin_ticket_ops | [tickets](../tickets/tickets.md#list-tickets) |
| GET | `/api/v1/tickets/{ticket_id}` | Public | [tickets](../tickets/tickets.md#get-ticket) |
| POST | `/api/v1/tickets` | `create_ticket` †manage_confidentiality | [tickets](../tickets/tickets.md#create-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/associate-cve` | `triage_ticket` | [tickets](../tickets/tickets.md#associate-cve) |
| DELETE | `/api/v1/tickets/{ticket_id}/cve` | `admin_ticket_ops` | [tickets](../tickets/tickets.md#remove-cve-from-ticket-admin-only) |
| PATCH | `/api/v1/tickets/{ticket_id}/severity` | `triage_ticket` | [tickets](../tickets/tickets.md#set-severity-override) |
| PATCH | `/api/v1/tickets/{ticket_id}/assignee` | `triage_ticket` | [tickets](../tickets/tickets.md#assign-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/ignore` | `triage_ticket` | [tickets](../tickets/tickets.md#ignore-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/reopen` | `triage_ticket` | [tickets](../tickets/tickets.md#reopen-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/duplicate` | `triage_ticket` | [tickets](../tickets/tickets.md#mark-ticket-as-duplicate) |
| POST | `/api/v1/tickets/{ticket_id}/revert-duplicate` | `triage_ticket` | [tickets](../tickets/tickets.md#revert-duplicate-status) |
| PATCH | `/api/v1/tickets/{ticket_id}/confidentiality` | `manage_confidentiality` | [tickets](../tickets/tickets.md#set-confidentiality) |
| GET | `/api/v1/tickets/{ticket_id}/access` | `manage_confidentiality` | [tickets](../tickets/tickets.md#list-access-grants) |
| POST | `/api/v1/tickets/{ticket_id}/access` | `manage_confidentiality` | [tickets](../tickets/tickets.md#grant-access) |
| DELETE | `/api/v1/tickets/{ticket_id}/access/{user}` | `manage_confidentiality` | [tickets](../tickets/tickets.md#revoke-access) |

### Ticket Packages

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/packages` | Public | [package-model](../packages/package-model.md#search-packages-across-tickets) |
| GET | `/api/v1/tickets/{ticket_id}/packages` | Public | [package-model](../packages/package-model.md#list-ticket-packages) |
| POST | `/api/v1/tickets/{ticket_id}/packages` | `manage_packages` | [package-model](../packages/package-model.md#add-package-to-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/exclude` | `manage_packages` | [package-model](../packages/package-model.md#soft-delete-package-from-ticket) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/restore` | `manage_packages` | [package-model](../packages/package-model.md#restore-package) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/exclude` | `manage_packages` | [package-model](../packages/package-model.md#soft-delete-track) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/restore` | `manage_packages` | [package-model](../packages/package-model.md#restore-track) |
| PATCH | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}` | `manage_packages` †admin_ticket_ops | [package-model](../packages/package-model.md#change-track-status) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}/exclude` | `manage_packages` | [package-model](../packages/package-model.md#soft-delete-product) |
| POST | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}/restore` | `manage_packages` | [package-model](../packages/package-model.md#restore-product) |
| PATCH | `/api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}` | `manage_packages` | [package-model](../packages/package-model.md#override-product-eligibility) |

### Products

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/products` | Public | [product-catalog](../packages/product-catalog.md#list-products) |

### Ticket References

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/tickets/{ticket_id}/references` | Public | [references](../tickets/ticket-references.md#list-references) |
| POST | `/api/v1/tickets/{ticket_id}/references` | `manage_references` | [references](../tickets/ticket-references.md#add-reference) |
| PATCH | `/api/v1/tickets/{ticket_id}/references/{reference_id}` | `manage_references` | [references](../tickets/ticket-references.md#update-reference) |
| DELETE | `/api/v1/tickets/{ticket_id}/references/{reference_id}` | `manage_references` | [references](../tickets/ticket-references.md#delete-reference) |

### CVEs

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/cves` | Public ‡admin_ticket_ops | [cve-tracking](../tickets/cve-tracking.md#list-cves) |
| GET | `/api/v1/cves/{cve_id}/cvss` | Public | [cvss-scoring](../tickets/cvss-scoring.md#get-cvss-assessments-for-a-cve) |
| POST | `/api/v1/cves/{cve_id}/cvss/suse` | `manage_cvss` | [cvss-scoring](../tickets/cvss-scoring.md#set-or-update-suse-cvss-assessment) |
| DELETE | `/api/v1/cves/{cve_id}/cvss/suse/{cvss_version}` | `manage_cvss` | [cvss-scoring](../tickets/cvss-scoring.md#delete-suse-cvss-assessment) |
| POST | `/api/v1/cves/{cve_id}/refetch` | `triage_ticket` | [cve-tracking](../tickets/cve-tracking.md#post-apiv1cvescve_idrefetch) |

### Ticket Events

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/tickets/{ticket_id}/audit-log` | Authenticated | [ticket-audit-log](../tickets/ticket-audit-log.md#list-ticket-events) |

### Submission Tracking

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/tickets/{ticket_id}/submission-requests` | Public | [ibs-submission-tracking](../packages/ibs-submission-tracking.md#get-apiv1ticketsticket_idsubmission-requests) |
| GET | `/api/v1/tickets/{ticket_id}/release-requests` | Public | [ibs-submission-tracking](../packages/ibs-submission-tracking.md#get-apiv1ticketsticket_idrelease-requests) |

### Fetchers

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/fetchers` | Public | [fetcher-operations](../platform/fetcher-operations.md#list-fetchers) |
| GET | `/api/v1/fetchers/{fetcher_name}/runs` | Public | [fetcher-operations](../platform/fetcher-operations.md#list-fetcher-runs) |
| GET | `/api/v1/fetchers/{fetcher_name}/runs/{run_id}` | Public | [fetcher-operations](../platform/fetcher-operations.md#get-fetcher-run-detail) |
| GET | `/api/v1/fetchers/{fetcher_name}/timeline` | Public | [fetcher-operations](../platform/fetcher-operations.md#get-fetcher-run-timeline-data) |
| POST | `/api/v1/fetchers/{fetcher_name}/trigger` | `manage_fetchers` | [fetcher-operations](../platform/fetcher-operations.md#trigger-fetcher) |
| GET | `/api/v1/fetchers/{fetcher_name}/config` | `manage_fetchers` | [fetcher-operations](../platform/fetcher-operations.md#get-fetcher-config) |
| PATCH | `/api/v1/fetchers/{fetcher_name}/config` | `manage_fetchers` | [fetcher-operations](../platform/fetcher-operations.md#update-fetcher-config) |
| GET | `/api/v1/fetchers/{fetcher_name}/audit-log` | `manage_fetchers` | [fetcher-operations](../platform/fetcher-operations.md#get-fetcher-audit-log) |
| GET | `/api/v1/ibs-consumer/status` | Public | [fetcher-operations](../platform/fetcher-operations.md#ibs-rabbitmq-consumer-status) |

### Maintainer Operations

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/my/packages/pending` | Authenticated | [maintainer](../packages/maintainer.md#get-apiv1mypackagespending) |
| GET | `/api/v1/my/packages/in-progress` | Authenticated | [maintainer](../packages/maintainer.md#get-apiv1mypackagesin-progress) |
| GET | `/api/v1/my/packages/completed` | Authenticated | [maintainer](../packages/maintainer.md#get-apiv1mypackagescompleted) |
| GET | `/api/v1/my/packages/ticket/{ticket_id}` | Authenticated | [maintainer](../packages/maintainer.md#get-apiv1mypackagesticketticket_id) |

### Administration

| Method | Endpoint | Authorization | Owning Spec |
|--------|----------|---------------|-------------|
| GET | `/api/v1/admin/settings` | `manage_settings` | [system-settings](../platform/system-settings.md#get-system-settings) |
| PATCH | `/api/v1/admin/settings` | `manage_settings` | [system-settings](../platform/system-settings.md#update-system-settings) |
| GET | `/api/v1/admin/settings/audit-log` | `manage_settings` | [system-settings](../platform/system-settings.md#api) |
| GET | `/api/v1/admin/identity/audit-log` | `manage_users` | [identity-audit-log](identity-audit-log.md#list-identity-audit-events) |
| GET | `/api/v1/admin/api-keys` | `manage_users` | [authentication](authentication.md#get-apiv1adminapi-keys) |
| POST | `/api/v1/admin/api-keys/{key_id}/revoke` | `manage_users` | [authentication](authentication.md#post-apiv1adminapi-keyskey_idrevoke) |
| PATCH | `/api/v1/admin/users/{user}` | `manage_users` | [user-management](user-management.md#patch-apiv1adminusersuser) |
| POST | `/api/v1/admin/users/{user}/roles` | `manage_users` | [user-management](user-management.md#post-apiv1adminusersuserroles) |
| POST | `/api/v1/admin/users/{user}/password` | `manage_users` | [user-management](user-management.md#post-apiv1adminusersuserpassword) |
| POST | `/api/v1/admin/users/{user}/deactivate` | `manage_users` | [user-management](user-management.md#post-apiv1adminusersuserdeactivate) |
| POST | `/api/v1/admin/users/{user}/reactivate` | `manage_users` | [user-management](user-management.md#post-apiv1adminusersuserreactivate) |
| GET | `/api/v1/admin/users/{user}/deactivation-impact` | `manage_users` | [user-management](user-management.md#get-apiv1adminusersuserdeactivation-impact) |
| POST | `/api/v1/admin/users/{user}/unlock` | `manage_users` | [user-management](user-management.md#post-apiv1adminusersuserunlock) |
| GET | `/api/v1/admin/role-mappings` | `manage_role_mappings` | [ad-integration](ad-integration.md#list-role-mappings) |
| POST | `/api/v1/admin/role-mappings` | `manage_role_mappings` | [ad-integration](ad-integration.md#create-role-mapping) |
| POST | `/api/v1/admin/role-mappings/preview` | `manage_role_mappings` | [ad-integration](ad-integration.md#preview-role-mapping) |
| DELETE | `/api/v1/admin/role-mappings/{id}` | `manage_role_mappings` | [ad-integration](ad-integration.md#delete-role-mapping) |

**Notes**:
- "Public" = no authentication required
- "Authenticated" = any logged-in user regardless of role
- Capability names (e.g., `create_ticket`, `manage_users`) = requires the
  specified capability; see Predefined Roles for which roles include each
  capability
- User creation: AD users are created by the LDAP directory sync (see
  [ad-integration](ad-integration.md#ldap-sync-fetcher)); local users are created by admins
  via CLI (see [user-management](user-management.md#cli-commands))

† Hard conditional — returns 403 if caller lacks this capability when the triggering field is provided.
‡ Soft conditional — parameter silently ignored if caller lacks this capability.

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
2. Only users with the `manage_users` capability can manage roles. They
   can manage any user's roles, including their own (subject to the
   self-removal guard in Business Rule 1)
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
8. Authenticated users with no roles have an effective scope of
   `non_confidential` and no capabilities. They can access specific
   confidential tickets via `TicketAccessGrant` or bugowner matching —
   unlike unauthenticated users, who see only non-confidential tickets
   with no per-ticket override mechanisms
9. Admin bootstrap: create a local admin via
   `sentinel manage-user create --username bootstrap-admin --email bootstrap@localhost --role admin`,
   then use the API to trigger the LDAP sync
   (`POST /api/v1/fetchers/sync_ldap_directory/trigger`), then promote
   an AD user via
   `sentinel manage-user update --username <username> --add-role admin`.
   See `docs/features/identity/ad-integration.md`.
   For bot accounts, see Business Rule 14
10. **Assignment target constraint**: only users holding the
    `vulnerability_analyst` role can be assigned as ticket owners. The
    `triage_ticket` capability controls who can *perform* the assignment;
    the VA role controls who can *be the target*. This constraint is
    enforced at two levels:
    - **Prospective** (assignment time): attempting to assign a ticket to
      a user without the VA role, or to an inactive user, is rejected
      with 400 Bad Request
    - **Retroactive** (role removal): if a user loses the
      `vulnerability_analyst` role entirely (no remaining `UserRole`
      records from any origin), all their active ticket assignments (New,
      Analysis, Analyzed) are automatically unassigned, with
      corresponding `TicketAuditEvent` records and status reconciliation
      — identical to the behavior on user deactivation. See
      `docs/features/identity/user-service.md`,
      `_unassign_tickets_on_va_role_loss()`
11. **Auto-assignment**: when a user modifies an unassigned ticket, the
    ticket is auto-assigned to the acting user **only if** the acting user
    holds the `vulnerability_analyst` role. If the acting user holds only
    `automation_agent` (or any other non-VA role), auto-assignment is
    skipped — the bot performs the operation but the ticket remains
    unassigned for a human to claim
12. **Status transitions with embedded assignment**: the reopen,
    revert-duplicate, and mark-as-duplicate flows embed a reassignment
    step. When a user without the `vulnerability_analyst` role performs
    these operations (they have `triage_ticket` capability to do so), the
    reassignment step is skipped — the ticket retains its current assignee.
    If the ticket was unassigned, it remains unassigned. Bots can trigger
    status transitions but never become ticket owners
13. **Confidential ticket creation**: the `is_confidential` field in
    `POST /api/v1/tickets` requires the `manage_confidentiality` capability
    in addition to `create_ticket`. A user with only `create_ticket` (e.g.,
    `automation_agent`) can create tickets but cannot set
    `is_confidential: true`. If the field is present and the caller lacks
    `manage_confidentiality`, the endpoint returns 403 with error code
    `AUTH_INSUFFICIENT_PERMISSION`. This prevents bots from creating
    confidential tickets they cannot subsequently access.
    (This is a _hard conditional check_ — see Conditional Capability Checks
    above for the pattern definition.)
14. **Bot account bootstrap**: bot accounts are local users created via
    CLI with the `automation_agent` role:

    ```
    sentinel manage-user create --username mybot --email mybot@example.com --role automation_agent
    ```

    The `create` command prompts for a password interactively. After
    creation, authenticate with the bot credentials to create an API key
    via `POST /api/v1/api-keys`. Bots should use API keys exclusively for
    ongoing operations. API key rotation is the admin's responsibility

## Implementation Details

### Authentication Mechanism

JWT with session-backed liveness checks. See
`docs/features/identity/authentication.md` for the full design: token format,
session management, API keys, and middleware behavior.

### Permission Checking

- Permissions are checked at the API endpoint level using FastAPI
  dependencies
- A `require_capability()` dependency factory returns a dependency that
  checks whether the current user holds the required capability through
  any of their roles
- Example: `Depends(require_capability(Capability.CREATE_TICKET))`
- Public endpoints (ticket list, CVE list, product list, fetcher dashboard)
  do not require authentication
- The `require_capability()` check loads the user's roles from the
  `UserRole` junction table, then checks each role's static capability set
- **Scope filtering** is applied separately by
  `confidential_ticket_filter()` and `require_accessible_ticket`, not at
  the endpoint decorator level
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
  manager_id, ad_synced_at)
- **UserRole**: junction table linking users to roles with `ad_group_cn`
  (AD group name or `_manual` for manual assignments)
- **RoleMapping**: maps AD group names to Sentinel roles
- **Role** enum: `Admin`, `Vulnerability Analyst`, `Automation Agent`

The capability-to-role mapping and scope-to-role mapping are static
definitions in code (not stored in the database). No new tables are
required. The Role enum in the database is **append-only** — values are
never removed from the PostgreSQL enum type. Deprecated roles (if ever
needed) would be handled via a migration that reassigns affected users.

### Role Wire Format

All role values in API requests and responses use lowercase with
underscores:

- `admin`
- `vulnerability_analyst`
- `automation_agent`

This applies to all endpoints that accept or return role values:
`POST /api/v1/admin/users/{user}/roles`, `GET /api/v1/users/{user}`,
CLI `--role` parameter, and `RoleMapping` API payloads.

## Role Origins and Coexistence

A user can acquire a role from two independent sources (origins):

- **Manual** (`ad_group_cn = '_manual'`): assigned by an admin via CLI
  or API. Can be removed by an admin at any time.
- **AD-derived** (`ad_group_cn = <group CN>`): derived from AD group
  membership during LDAP sync. Managed exclusively by the sync process
  — cannot be removed via UI or API. See `docs/features/identity/ad-integration.md`.

AD groups can be mapped to `automation_agent` via `RoleMapping`. This is
a valid use case — for example, a team of humans who should perform
ticket operations but must not access embargoed (confidential) data. The
admin is responsible for ensuring the mapping is intentional.

### Coexistence Rules

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

## Cross-references

- `docs/features/tickets/tickets.md` — confidentiality rules, status transitions
- `docs/features/tickets/ticket-mutations.md` — service-layer mutation contracts
- `docs/features/identity/ad-integration.md` — AD sync, role mappings
- `docs/features/identity/user-service.md` — centralized user lifecycle operations
- `docs/data-model.md` — Role enum, UserRole table
- `docs/api-spec.md` — API authorization conventions
- `docs/conventions.md` — FastAPI implementation conventions
