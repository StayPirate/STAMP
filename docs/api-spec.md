# API Specification

## General Conventions

### Base URL

All API endpoints are prefixed with `/api/v1/`.

### Authentication

All endpoints require authentication unless explicitly marked as public.
Authentication mechanism TBD (JWT or session-based).

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

- `POST /api/v1/cves/sync` — Trigger manual CVE sync (Incident Manager role).
  CVE data is accessed through the ticket endpoints below.
### Tickets

See `docs/features/pages.md` for ticket lifecycle and page specifications.
See `docs/features/package-tracking.md` for package management endpoints.

- `GET /api/v1/tickets` — List tickets with filters
- `GET /api/v1/tickets/{ticket_id}` — Get ticket details
- `POST /api/v1/tickets/{ticket_id}/assign` — Assign or reassign a ticket
- `POST /api/v1/tickets/{ticket_id}/ignore` — Mark ticket as ignored
- `POST /api/v1/tickets/{ticket_id}/duplicate` — Mark ticket as duplicate
- `POST /api/v1/tickets/{ticket_id}/revert-duplicate` — Revert duplicate status
- `POST /api/v1/tickets/{ticket_id}/packages` — Add a package to a ticket
- `DELETE /api/v1/tickets/{ticket_id}/packages/{package_name}` — Remove a package
- `PATCH /api/v1/tickets/{ticket_id}/packages/{package_name}/codestreams/{codestream_name}`
  — Change codestream affectedness status (Incident Manager role)
- `PATCH /api/v1/tickets/{ticket_id}/packages/{package_name}/products/{product_id}`
  — Override product affectedness status (Incident Manager role)

### Products

See `docs/features/package-tracking.md` for detailed specifications.

- `GET /api/v1/products` — List products (synced from SMELT)

### Ticket References

See `docs/features/references.md` for detailed endpoint specifications.

- `GET /api/v1/tickets/{ticket_id}/references` — List references (public,
  filterable by `source`)
- `POST /api/v1/tickets/{ticket_id}/references` — Add a manual reference
  (Incident Manager role)
- `PUT /api/v1/tickets/{ticket_id}/references/{reference_id}` — Update a
  reference (Incident Manager role)
- `DELETE /api/v1/tickets/{ticket_id}/references/{reference_id}` — Delete
  a reference (Incident Manager role)

### Ticket Events

See `docs/features/ticket-history.md` for detailed endpoint specification.

- `GET /api/v1/tickets/{ticket_id}/events` — List ticket events with filters
  (event type, actor, text search) and pagination

### CVSS Assessments

See `docs/features/cvss-scoring.md` for detailed endpoint specifications.

- `GET /api/v1/tickets/{ticket_id}/cvss` — Get all CVSS assessments for a
  ticket's CVE, grouped by version, including resolved score/severity
- `POST /api/v1/tickets/{ticket_id}/cvss/suse` — Set or update SUSE CVSS
  assessment (upsert by version). Requires the Incident Manager role.
- `DELETE /api/v1/tickets/{ticket_id}/cvss/suse/{cvss_version}` — Remove
  SUSE CVSS assessment. Requires the Incident Manager role.

### Administration

See `docs/features/admin.md` for detailed endpoint specifications.

- `GET /api/v1/admin/settings` — Get system settings (Admin only)
- `PATCH /api/v1/admin/settings` — Update system settings (Admin only).
  Changing `default_cvss_version` triggers recalculation for all active
  tickets.

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

See `docs/features/rbac.md` for detailed endpoint specifications.

- `POST /api/v1/auth/login` — Authenticate
- `POST /api/v1/auth/logout` — End session
- `GET /api/v1/users/me` — Get current user
- `GET /api/v1/users` — List users (admin only)
- `POST /api/v1/users` — Create a new user (admin only)
- `PUT /api/v1/users/{id}` — Update user details and roles (admin only)
- `DELETE /api/v1/users/{id}` — Deactivate a user (admin only)
