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

- `GET /api/v1/cves` — List CVEs with filters
- `GET /api/v1/cves/{cve_id}` — Get CVE details
- `POST /api/v1/cves/sync` — Trigger manual CVE sync
- `GET /api/v1/cves/{cve_id}/impact` — Get impact analysis

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

### Products

See `docs/features/package-tracking.md` for detailed specifications.

- `GET /api/v1/products` — List products (synced from SMELT)

### Ticket Events

See `docs/features/ticket-history.md` for detailed endpoint specification.

- `GET /api/v1/tickets/{ticket_id}/events` — List ticket events with filters
  (event type, actor, text search) and pagination

### Users and Auth

See `docs/features/rbac.md` for detailed endpoint specifications.

- `POST /api/v1/auth/login` — Authenticate
- `POST /api/v1/auth/logout` — End session
- `GET /api/v1/users/me` — Get current user
- `GET /api/v1/users` — List users (admin only)
