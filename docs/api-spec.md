# API Specification

## General Conventions

### Base URL

All API endpoints are prefixed with `/api/v1/`.

### Authentication

All endpoints require authentication unless explicitly marked as public.
Authentication uses JWT tokens in HttpOnly cookies (browser sessions) or
API keys (programmatic access). See `docs/features/authentication.md`,
`docs/features/sso-authentication.md`, and
`docs/features/local-authentication.md` for full details.

### Response Format

All responses use JSON. Successful responses follow this structure:

```json
{
  "data": { ... },
  "meta": {
    "total": 100,
    "page": 1,
    "per_page": 20
  }
}
```

Error responses follow this structure:

```json
{
  "detail": "Error description",
  "errors": [
    {
      "field": "field_name",
      "message": "Validation error message"
    }
  ]
}
```

### Pagination

List endpoints support pagination via query parameters:

- `page` (int, default: 1): Page number
- `per_page` (int, default: 20, max: 100): Items per page

### Filtering

List endpoints support filtering via query parameters specific to each
resource. Common patterns:

- Exact match: `?status=active`
- Search: `?search=term` (searches relevant text fields)
- Date range: `?from_date=2024-01-01&to_date=2024-12-31`

### Sorting

List endpoints support sorting:

- `sort_by` (string): Field name to sort by
- `sort_order` (string): `asc` or `desc` (default: `desc`)

## Endpoints

Detailed endpoint specifications will be added as features are implemented.
Each feature specification in `docs/features/` defines its own API endpoints.

### CVEs

See `docs/features/cve-tracking.md` for detailed endpoint specifications.

CVE data is accessed through the ticket endpoints below. On-demand
single-CVE fetch is triggered automatically when Sentinel encounters an
unknown CVE-ID during ticket creation or CVE association (see
  `docs/features/cve-tracking.md`, "On-demand Single-CVE Fetch").

### Tickets

See `docs/features/tickets.md` for the central ticket specification
(identification, creation, lifecycle, severity resolution).
See `docs/features/pages.md` for UI page specifications.
See `docs/features/package-tracking.md` for package management endpoints.

**Dual lookup**: all endpoints that accept a `{ticket_id}` path parameter
support both UUID and `SNTL-{n}` format (e.g.,
`GET /api/v1/tickets/SNTL-42`). The backend detects the format
automatically.

- `GET /api/v1/tickets` — List tickets with filters. The `search` query
  parameter searches across `SNTL-{n}` identifier, CVE ID, CVE
  description, and package names. Supports the following query parameters:
  - `search` (string): free-text search across ticket ID, CVE ID, CVE
    description, and package names
  - `status` (string, repeatable): filter by ticket status. Accepts one or
    more values from: `new`, `analysis`, `analyzed`, `resolved`, `ignored`,
    `duplicated`. When multiple values are provided, tickets matching any
    of the specified statuses are returned
  - `assignee` (string): filter by assignee. Accepts a user UUID or the
    special value `none` to return only unassigned tickets
  - `severity` (string, repeatable): filter by severity level. Accepts one
    or more values from: `critical`, `high`, `medium`, `low`, `none`
  - `bugowner` (string): filter tickets to those containing at least one
    package whose bugowner matches the value (matches against bugowner
    email, name, or group member email/userid — see
    `docs/features/package-bugowner.md`)
  - `include_deleted` (string): `true` or `only`. The parameter is accepted
    from any caller, but soft-deleted tickets are included in the response
    only if the caller holds the Admin role. For non-admin callers the
    parameter is silently ignored. Values: `true` (include active and
    deleted tickets), `only` (return only deleted tickets). Default behavior
    (parameter absent or `false`): return only active tickets
- `GET /api/v1/tickets/{ticket_id}` — Get ticket details. If the ticket is
  soft-deleted and the caller is not an Admin, returns 410 Gone with body
  `{"detail": "This ticket has been deleted. Contact an admin if you think
  this is an error."}`. Admin callers receive the full ticket data with the
  `deleted_at` field populated. The response includes bugowner information
  for each package (type, name, email, and group members when applicable).
  See `docs/features/package-bugowner.md` for the response format.
- `POST /api/v1/tickets` — Create a ticket manually (Vulnerability Analyst
  role). The creating user is automatically assigned. Optionally accepts
  a `cve_id` to associate a CVE at creation time. If the CVE is not in
  the database, a minimal CVE record is created and on-demand fetch is
  triggered. Returns 409 if CVE is already associated with another ticket
  (response includes `existing_ticket_id`). Response includes
  `cve_data_pending: true` when CVE data is being fetched. See
  `docs/features/tickets.md` for details.
- `POST /api/v1/tickets/{ticket_id}/associate-cve` — Associate a CVE with
  a ticket that does not have one (Vulnerability Analyst role). If the CVE is
  not in the database, a minimal CVE record is created and on-demand fetch
  is triggered. Returns 400 if ticket already has a CVE, 409 if CVE
  already associated with another ticket (response includes
  `existing_ticket_id`). Response includes `cve_data_pending: true` when
  CVE data is being fetched. See `docs/features/tickets.md`.
- `DELETE /api/v1/tickets/{ticket_id}/cve` — Remove the CVE association
  from a ticket (Admin role). Sets `cve_id` to NULL. Creates a
  `cve_removed` TicketEvent. Returns 204 No Content. Returns 400 if the
  ticket has no CVE associated. Returns 404 if the ticket does not exist.
  See `docs/features/tickets.md`.
- `PATCH /api/v1/tickets/{ticket_id}/severity` — Update severity override
  for a ticket without a CVE (Vulnerability Analyst role). Returns 400 if the
  ticket has an associated CVE. See `docs/features/tickets.md`.
- `POST /api/v1/tickets/{ticket_id}/assign` — Assign or reassign a ticket
- `POST /api/v1/tickets/{ticket_id}/ignore` — Mark ticket as ignored
- `POST /api/v1/tickets/{ticket_id}/duplicate` — Mark ticket as duplicate.
  Request body: `{"duplicate_of_id": "<UUID or SNTL-{n}>"}`. The target is
  resolved following the chain if it is itself Duplicated (see
  `docs/features/tickets.md`, "Duplicate Handling"). All tickets previously
  pointing to this ticket are cascade-updated to the resolved target.
  Error responses: 400 (self-reference after resolution), 409 (chain depth
  exceeded — data corruption, logged as ERROR), 404 (target ticket not found)
- `POST /api/v1/tickets/{ticket_id}/revert-duplicate` — Revert duplicate status
- `DELETE /api/v1/tickets/{ticket_id}` — Soft-delete a ticket (Admin role
  required). Sets `deleted_at` to the current timestamp. Creates a
  `ticket_deleted` TicketEvent. Returns 204 No Content. Returns 404 if the
  ticket does not exist. Returns 409 Conflict if the ticket is already
  soft-deleted.
- `POST /api/v1/tickets/{ticket_id}/restore` — Restore a soft-deleted
  ticket (Admin role required). Clears `deleted_at`. Creates a
  `ticket_restored` TicketEvent. Returns 200 OK with the restored ticket
  data. Returns 404 if the ticket does not exist. Returns 409 Conflict if
  the ticket is not soft-deleted.
- `POST /api/v1/tickets/{ticket_id}/packages` — Add a package to a ticket
- `DELETE /api/v1/tickets/{ticket_id}/packages/{package_name}` — Remove a package
- `PATCH /api/v1/tickets/{ticket_id}/packages/{package_name}/codestreams/{codestream_name}`
  — Change codestream affectedness status (Vulnerability Analyst role)
- `PATCH /api/v1/tickets/{ticket_id}/packages/{package_name}/products/{product_id}`
  — Override product affectedness status (Vulnerability Analyst role)

**Auto-assignment**: any endpoint that modifies a ticket with no current
assignee (`assignee_id = NULL`) will automatically assign the ticket to
the authenticated VA. The response will reflect the updated
`assignee_id`. A `TicketEvent` of type `assignment` is created
atomically. If the ticket was in `New` status and the operation does not
include an explicit status change, the ticket also transitions to
`Analysis`. See `docs/features/tickets.md` (Auto-Assignment on
Unassigned Tickets) for full rules.

**Soft-delete protection on sub-resources**: all endpoints under
`/api/v1/tickets/{ticket_id}/...` (events, references, packages,
codestreams, products) return 410 Gone for non-admin callers when the
parent ticket is soft-deleted. Admin callers can access sub-resources
normally. This is enforced by a shared ticket resolution dependency
that checks `deleted_at` and the caller's role.

### Products

See `docs/features/package-tracking.md` for detailed specifications.

- `GET /api/v1/products` — List products (synced from SMELT)

### Ticket References

See `docs/features/references.md` for detailed endpoint specifications.

- `GET /api/v1/tickets/{ticket_id}/references` — List references (public,
  filterable by `source`)
- `POST /api/v1/tickets/{ticket_id}/references` — Add a manual reference
  (Vulnerability Analyst role)
- `PUT /api/v1/tickets/{ticket_id}/references/{reference_id}` — Update a
  reference (Vulnerability Analyst role)
- `DELETE /api/v1/tickets/{ticket_id}/references/{reference_id}` — Delete
  a reference (Vulnerability Analyst role)

### Ticket Events

See `docs/features/ticket-history.md` for detailed endpoint specification.

- `GET /api/v1/tickets/{ticket_id}/events` — List ticket events with filters
  (event type, actor, text search) and pagination

### CVSS Assessments

See `docs/features/cvss-scoring.md` for detailed endpoint specifications.

- `GET /api/v1/tickets/{ticket_id}/cvss` — Get all CVSS assessments for a
  ticket's CVE, grouped by version, including resolved score/severity
- `POST /api/v1/tickets/{ticket_id}/cvss/suse` — Set or update SUSE CVSS
  assessment (upsert by version). Requires the Vulnerability Analyst role.
- `DELETE /api/v1/tickets/{ticket_id}/cvss/suse/{cvss_version}` — Remove
  SUSE CVSS assessment. Requires the Vulnerability Analyst role.

### Submission Tracking

See `docs/features/submission-tracking.md` for detailed endpoint
specifications.

- `GET /api/v1/tickets/{ticket_id}/submission-requests` — List submission
  requests correlated to a ticket. Filterable by `package_name`,
  `codestream_name`, `state`. Unpaginated (small dataset).
- `GET /api/v1/tickets/{ticket_id}/release-requests` — List release
  requests associated with a ticket (derived via SR incident numbers).
  Filterable by `package_name`, `codestream_name`, `state`,
  `incident_number`. Unpaginated.

### Maintainer Dashboard

See `docs/features/maintainer-dashboard.md` for detailed endpoint
specifications.

- `GET /api/v1/my/packages/pending` — List pending fixes for the
  authenticated user (codestreams needing a fix where user is bugowner).
  Filterable by `package`. Paginated. Sortable by `severity`, `waiting`.
- `GET /api/v1/my/packages/in-progress` — List in-progress submissions
  for the authenticated user. Filterable by `package`. Paginated.
  Sortable by `since`, `package`.
- `GET /api/v1/my/packages/completed` — List completed releases for the
  authenticated user. Filterable by `package`, `days`. Paginated.
  Sortable by `released`, `package`.
- `GET /api/v1/my/packages/ticket/{ticket_id}` — Get pending, in-progress,
  and completed items for a single ticket filtered to the authenticated
  user's packages. Returns 404 if ticket does not exist, 410 Gone if
  soft-deleted. Returns an error state object (200) if ticket is not in
  `Analyzed` status or user is not a bugowner of any package in the ticket.

### Administration

See `docs/features/admin.md` for detailed endpoint specifications.

- `GET /api/v1/admin/settings` — Get system settings (Admin only)
- `PATCH /api/v1/admin/settings` — Update system settings (Admin only).
  Changing `default_cvss_version` triggers recalculation for all active
  tickets.

### IBS RabbitMQ Consumer

See `docs/features/fetcher-dashboard.md` for detailed endpoint
specification.

- `GET /api/v1/ibs-consumer/status` — Get real-time status of the IBS
  event consumer (publicly accessible)

### Fetchers

See `docs/features/fetcher-dashboard.md` for detailed endpoint
specifications.

- `GET /api/v1/fetchers` — List all registered fetchers with status and
  config
- `GET /api/v1/fetchers/{fetcher_name}/runs` — List run history
  (paginated, filterable by status and date range)
- `GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}` — Get run detail
  (admin sees error traceback)
- `GET /api/v1/fetchers/{fetcher_name}/timeline` — Time-series data for
  charts (auto-selects individual or aggregate data)
- `POST /api/v1/fetchers/{fetcher_name}/trigger` — Trigger manual run
  (admin only)
- `GET /api/v1/fetchers/{fetcher_name}/config` — Get fetcher config
  (admin only)
- `PATCH /api/v1/fetchers/{fetcher_name}/config` — Update fetcher config:
  enable/disable, schedule, timeout, rate limit (admin only)
- `GET /api/v1/fetchers/{fetcher_name}/audit-log` — Admin action history
  (admin only)

### Users and Auth

See `docs/features/rbac.md` for access control details and
`docs/features/ldap-directory.md` for LDAP integration details.

Users are populated from SUSE Active Directory via the
`sync_ldap_directory` fetcher. There is no public user self-registration
endpoint. Local users are created by admins via CLI or admin UI (see
`docs/features/local-authentication.md`). Authentication is provided via
SSO (see `docs/features/sso-authentication.md`) for directory users, or
via local password for admin-created local users (see
`docs/features/local-authentication.md`).

- `GET /api/v1/users` — List/search users (public). Supports `search`
  (min 2 chars, searches username/email/full_name), `active` (boolean),
  `role` (enum), `has_role` (boolean) query parameters. Standard
  pagination and sorting
- `GET /api/v1/users/{id}` — Get user detail including roles (with
  source) and resolved manager (public)
- `GET /api/v1/users/me` — Get current authenticated user profile
- `PUT /api/v1/admin/users/{user_id}/roles` — Add/remove manual roles (admin only).
  Cannot remove AD-derived roles. Request body: `{ "add": [...],
  "remove": [...] }`. See `docs/features/user-management.md`
- `GET /api/v1/admin/users/{user_id}/deactivation-impact` — Preview side
  effects of deactivating a user (admin only). Returns counts of API keys,
  sessions, and tickets affected, plus reassignment target. See
  `docs/features/user-management.md`
- `PATCH /api/v1/admin/users/{user_id}/active` — Deactivate or reactivate a
  user (admin only). Request body: `{ "active": bool }`. See
  `docs/features/user-management.md`
- `POST /api/v1/admin/users/{user_id}/unlock` — Clear login lockout counter
  (admin only). See `docs/features/user-management.md`

### Role Mappings

See `docs/features/ldap-directory.md` for detailed specifications.

- `GET /api/v1/admin/role-mappings` — List all AD group → role mappings
  (admin only)
- `POST /api/v1/admin/role-mappings/preview` — Preview which users would
  be affected by a proposed mapping (admin only, queries AD live)
- `POST /api/v1/admin/role-mappings` — Create a new mapping and apply
  roles immediately (admin only)
- `DELETE /api/v1/admin/role-mappings/{id}` — Delete a mapping and
  revoke AD-derived roles (admin only)
