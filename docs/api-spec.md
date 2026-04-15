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

### Distributions

See `docs/features/distro-management.md` for detailed endpoint specifications.

- `GET /api/v1/distributions` — List distributions
- `POST /api/v1/distributions` — Create distribution
- `GET /api/v1/distributions/{id}` — Get distribution details
- `PUT /api/v1/distributions/{id}` — Update distribution
- `GET /api/v1/distributions/{id}/packages` — List packages in distribution

### Packages

- `GET /api/v1/packages` — List packages
- `GET /api/v1/packages/{id}` — Get package details
- `GET /api/v1/packages/{id}/cves` — List CVEs affecting a package

### Security Updates

See `docs/features/update-coordination.md` for detailed endpoint specifications.

- `GET /api/v1/updates` — List security updates
- `POST /api/v1/updates` — Create security update
- `GET /api/v1/updates/{id}` — Get update details
- `PUT /api/v1/updates/{id}` — Update security update
- `POST /api/v1/updates/{id}/release` — Release an update

### Users and Auth

See `docs/features/rbac.md` for detailed endpoint specifications.

- `POST /api/v1/auth/login` — Authenticate
- `POST /api/v1/auth/logout` — End session
- `GET /api/v1/users/me` — Get current user
- `GET /api/v1/users` — List users (admin only)
