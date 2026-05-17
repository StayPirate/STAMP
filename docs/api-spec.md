# API Specification

## General Conventions

### Base URL

All API endpoints are prefixed with `/api/v1/`.

### Authentication

All endpoints require authentication unless explicitly marked as public.
Authentication uses JWT tokens in HttpOnly cookies (browser sessions) or
API keys (programmatic access). See `docs/features/identity/authentication.md`,
`docs/features/identity/sso-authentication.md`, and
`docs/features/identity/local-authentication.md` for full details.

### Authorization

Every endpoint definition in a feature specification MUST declare its
access level using one of:

- **Public** — no authentication required
- **Authenticated** — any logged-in user regardless of role
- **Vulnerability Analyst** — requires the `vulnerability_analyst` role
- **Admin** — requires the `admin` role

The access level declared in the owning feature specification is the
**authoritative source**. `docs/features/identity/rbac.md` maintains a
derived summary index (Endpoint Permission Map) for cross-referencing —
it is not the source of truth.

### Response Format

All responses use JSON.

**Paginated list endpoints** return:

```json
{
  "data": [ ... ],
  "meta": {
    "total": 100,
    "page": 1,
    "per_page": 20
  }
}
```

**Single-resource and unpaginated list endpoints** return:

```json
{
  "data": { ... }
}
```

The `meta` object is present **only** on paginated list endpoints.
Unpaginated endpoints return the full dataset in `data` (clients can derive
the count from the array length). Endpoints that intentionally omit
pagination must state the justification (e.g., bounded dataset size).

Error responses follow this structure:

```json
{
  "code": "TICKET_NOT_FOUND",
  "detail": "Ticket with ID 'abc-123' does not exist",
  "errors": [
    {
      "field": "field_name",
      "message": "Validation error message"
    }
  ]
}
```

Fields:

- `code` (string, required): a stable machine-readable error identifier in
  UPPER_SNAKE_CASE. Clients MUST use this field (not `detail`) for
  programmatic error handling
- `detail` (string, required): a human-readable description of the error.
  May change without notice — do not match against this string
- `errors` (array, optional): field-level validation errors. Present only
  for `VALIDATION_ERROR` responses

#### Error Code Categories

Error codes are grouped by prefix:

| Prefix | Domain | Examples |
|--------|--------|----------|
| `VALIDATION_*` | Input validation | `VALIDATION_ERROR`, `VALIDATION_FIELD_REQUIRED` |
| `AUTH_*` | Authentication and authorization | `AUTH_NOT_AUTHENTICATED`, `AUTH_INSUFFICIENT_ROLE`, `AUTH_API_KEY_INVALID`, `AUTH_SSO_FAILED`, `AUTH_SSO_USER_NOT_FOUND`, `AUTH_SSO_USER_INACTIVE` |
| `TICKET_*` | Ticket operations | `TICKET_NOT_FOUND`, `TICKET_ALREADY_RESOLVED`, `TICKET_INVALID_TRANSITION`, `TICKET_DELETED`, `TICKET_ALREADY_DELETED`, `TICKET_NOT_DELETED` |
| `CVE_*` | CVE operations | `CVE_NOT_FOUND`, `CVE_FETCH_FAILED` |
| `RESOURCE_*` | Generic resource errors | `RESOURCE_NOT_FOUND`, `RESOURCE_CONFLICT`, `RESOURCE_GONE` |
| `PACKAGE_*` | Package operations | `PACKAGE_NOT_FOUND_IN_SMELT`, `PACKAGE_ALREADY_EXCLUDED`, `PACKAGE_NOT_EXCLUDED`, `PACKAGE_RESTORE_BLOCKED` |
| `ROLE_MAPPING_*` | Role mapping operations | `ROLE_MAPPING_GROUP_NOT_FOUND`, `ROLE_MAPPING_INVALID_GROUP_CN` |
| `FETCHER_*` | Fetcher operations | `FETCHER_NOT_FOUND`, `FETCHER_ALREADY_RUNNING`, `FETCHER_DEREGISTERED`, `FETCHER_DISABLED` |
| `USER_*` | User operations | `USER_NOT_FOUND`, `USER_ALREADY_EXISTS`, `USER_INACTIVE`, `USER_ALREADY_INACTIVE`, `USER_AD_STATUS_READONLY`, `USER_AD_FIELD_READONLY`, `USER_AD_PASSWORD_FORBIDDEN`, `USER_AD_ROLE_PROTECTED`, `USER_AD_LOCKOUT`, `USER_SELF_ROLE_REMOVAL`, `USER_SELF_DEACTIVATION` |

Rules:

- Every new error introduced in the codebase MUST have a corresponding code
  with the appropriate prefix
- Codes are defined as a Python enum in the backend (`app/core/errors.py`)
  and are part of the API contract — removing or renaming a code is a
  breaking change
- When an error does not fit an existing category, use the `RESOURCE_*`
  prefix for generic cases or introduce a new prefix if a distinct domain
  emerges

#### Infrastructure Dependency Errors (HTTP 503)

When an endpoint fails because an external dependency is unreachable,
use a domain-specific error code that identifies the unavailable service.
Do not use a generic code — the client and operator need to know *which*
dependency failed.

Pattern: `<DEPENDENCY>_UNAVAILABLE` with HTTP 503.

Examples:

| Code | Dependency |
|------|------------|
| `REDIS_UNAVAILABLE` | Redis cache/session store |
| `AD_UNAVAILABLE` | Active Directory (via LDAP) |
| `SMELT_UNAVAILABLE` | SMELT API |
| `AUTH_SSO_UNAVAILABLE` | SSO identity provider (OIDC discovery) |

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

#### Enum Filter Validation

When a filter parameter accepts enum values (comma-separated or repeatable),
invalid values are silently ignored. If all provided values are invalid, the
endpoint returns an empty result set (not an error). This applies to all
endpoints that accept enum-based filter parameters (e.g., `event_type`,
`status`, `severity`).

#### Date Range Interpretation

When a date range filter (`from_date`, `to_date`) is applied against a
`datetime` column:

- **Date-only value** (e.g., `2025-01-15`):
  - `from_date` → interpreted as `2025-01-15T00:00:00Z` (start of day
    UTC, inclusive)
  - `to_date` → interpreted as `2025-01-15T23:59:59.999999Z` (end of day
    UTC, inclusive)
- **Full datetime value without offset** (e.g., `2025-01-15T14:30:00`):
  interpreted as UTC
- **Full datetime value with offset** (e.g., `2025-01-15T14:30:00+02:00`):
  accepted and converted to UTC before comparison (i.e.,
  `2025-01-15T12:30:00Z`)

This ensures that "inclusive bounds" means inclusive of the full day when no
time component is specified. For the full timezone policy, see
`docs/conventions.md` (Timestamps & Timezones).

### Sorting

List endpoints support sorting:

- `sort_by` (string): Field name to sort by
- `sort_order` (string): `asc` or `desc` (default: `desc`)

**Default sort order**: when a paginated list endpoint does not document
specific sorting behavior, the implicit default is `sort_by=created_at`,
`sort_order=desc` (newest first). Endpoints with a different natural
ordering (e.g., alphabetical, severity-based) must state their default
explicitly.

Endpoints that intentionally do not support client-controlled sorting must
state so with justification (e.g., "fixed chronological order for timeline
display").

### Request Tracing

Every API response includes an `X-Request-ID` header containing a UUID that
uniquely identifies the request. If the client sends an `X-Request-ID`
header, the server adopts it; otherwise the server generates one.

The request ID is propagated to all log entries produced during request
processing, enabling end-to-end debugging. Clients should log or display the
request ID when reporting errors to support staff.

### Rate Limiting

Rate limiting is not enforced at this time. When activated, the API will
communicate limits via standard headers:

- `X-RateLimit-Limit`: maximum requests allowed in the current window
- `X-RateLimit-Remaining`: requests remaining in the current window
- `X-RateLimit-Reset`: UTC epoch timestamp when the window resets

Clients that exceed the limit will receive `429 Too Many Requests`. Clients
SHOULD respect these headers proactively to avoid hitting limits.

### Global Responses

The following responses may be returned by any authenticated endpoint due to
shared dependencies (middleware). Individual endpoint error tables document
only endpoint-specific errors; global responses are not repeated.

| Status | Code                     | Condition                                      | Source                         |
|--------|--------------------------|------------------------------------------------|--------------------------------|
| 401    | `AUTH_NOT_AUTHENTICATED` | Missing, malformed, or invalid credentials     | `get_current_user` dependency  |
| 403    | `AUTH_INSUFFICIENT_ROLE` | User authenticated but lacks required role     | `require_role` dependency      |
| 422    | `VALIDATION_ERROR`       | Request body/query/path fails schema validation | FastAPI automatic (Pydantic)   |
| 500    | `INTERNAL_ERROR`         | Unhandled server error                         | Framework                      |

Notes:

- **Public endpoints** (explicitly marked) are exempt from 401
- The 401 response body is always `{"code": "AUTH_NOT_AUTHENTICATED",
  "detail": "Authentication required"}` regardless of the specific failure
  reason — no information about the failure cause is disclosed
- The 422 response uses Pydantic's native format with the `errors` array
  populated with field-level details
- Endpoint error tables should only list responses that are specific to
  that endpoint's logic (e.g., 404, 409, 403 for role requirements)

### Scoped Responses

Some shared dependencies apply to a specific resource group rather than to
all endpoints. Like global responses, scoped responses are not repeated in
per-endpoint error tables.

#### Ticket Soft-Delete Protection

All endpoints under `/api/v1/tickets/{ticket_id}/` — including the ticket
detail endpoint itself — are subject to a centralized soft-delete check
enforced by a router-level shared dependency (`require_accessible_ticket`).

| Status | Code              | Condition                                            |
|--------|-------------------|------------------------------------------------------|
| 410    | `TICKET_DELETED`  | Ticket is soft-deleted and caller does not hold the Admin role |

When a ticket has `deleted_at IS NOT NULL`, Admin callers proceed normally
while all other callers receive 410 Gone. This applies uniformly to read
and write operations on the ticket and its sub-resources (packages, tracks,
products, CVSS assessments, references, audit log, submission requests).

**Exceptions** — the following endpoints are excluded from this check
because they manage the soft-delete lifecycle directly:

- `DELETE /api/v1/tickets/{ticket_id}` (soft-delete): returns 409
  `TICKET_ALREADY_DELETED` if the ticket is already soft-deleted
- `POST /api/v1/tickets/{ticket_id}/restore` (restore): returns 409
  `TICKET_NOT_DELETED` if the ticket is not soft-deleted

See `docs/features/tickets/tickets.md` ([Soft-Delete](docs/features/tickets/tickets.md#soft-delete))
for the full business rules (who may delete/restore, status categories,
sub-resource behavior, automated verification requirements).

### Versioning

The API uses URL-path versioning (`/api/v1/`). Only v1 exists at this time.

Rules:

- Additive changes (new fields in responses, new endpoints, new optional
  query parameters) are NOT breaking changes and are added to v1 directly
- Removal or renaming of fields, changes to response structure, changes to
  error codes, or semantic changes to existing behavior are breaking changes
- A v2 will only be considered after a stable production instance is
  confirmed — until then, all work happens on v1
- When a new version is eventually introduced, the previous version will
  include a `Sunset` response header indicating the deprecation date

### User Identifier Resolution

All parameters that identify a user — whether path parameters, query
parameters, or request body fields — accept either a UUID or a username.
Resolution is automatic:

- If the value is a valid UUID (RFC 4122 format), lookup is by primary key
  (`User.id`)
- Otherwise, lookup is by the `username` field (case-sensitive exact match)
- If no user matches either lookup, the endpoint returns 404 with error code
  `USER_NOT_FOUND`. This 404 convention applies to parameters that identify a
  **single target resource** (path parameters, request body fields). Optional
  filter parameters on list endpoints (e.g., `?actor=jdoe`) do NOT return
  404 — a non-matching value produces an empty result set instead

Response payloads always contain the user's UUID (never the username as
identifier). The database persists only UUIDs in foreign keys and
relationships.

#### User References in Responses

When a response payload includes a reference to a user (e.g., `actor`,
`assignee`, `target_user`, `created_by`), it is serialized as an object
with `id`, `username`, and `full_name` — populated via JOIN to the
**current** User record. These values reflect the user's current profile
data, not a historical snapshot at the time of the event or action.

Historical values, where relevant, are preserved in dedicated fields of
the owning entity (e.g., `old_value` / `new_value` in audit events).
The `id` (UUID) is the stable, immutable identifier; `username` and
`full_name` are display conveniences that may change over time (e.g.,
via AD sync).

Users are never physically deleted from the database — all foreign keys
referencing the User table use `ON DELETE RESTRICT`. Deactivated users
(`active=false`) are resolved normally, with all fields (`id`, `username`,
`full_name`) populated from current data. Consequently, a user reference
object is never null or partial when `user_id` is non-null — if a `user_id`
foreign key is present, the referenced user record is guaranteed to exist
and the serialized object will always be complete.

This convention applies to:

- Path parameters (e.g., `/api/v1/users/{user}`)
- Query parameters (e.g., `?assignee=jdoe`)
- Request body fields (e.g., `{"user_id": "jdoe"}`)

The special filter value `none` (used in query parameters like `assignee`)
is not subject to user resolution — it is handled as a literal keyword
before resolution is attempted.

Implementation note: a reusable FastAPI dependency
(`resolve_user_identifier`) handles the detection and lookup. See
`docs/conventions.md` (FastAPI Conventions) for the reference
implementation pattern.

### Mutation Patterns

Two patterns exist for modifying resources:

**PATCH — field update without significant side-effects:**

```
PATCH /api/v1/tickets/{id}
Body: {"severity": "high"}
```

Used when the operation is a direct attribute change with no additional
business logic beyond validation.

**POST with action verb — operation with business logic or side-effects:**

```
POST /api/v1/tickets/{id}/assign
Body: {"user_id": "..."}
```

Used when the operation triggers additional logic such as notifications,
event logging, state transitions, or cross-entity validation.

Rule of thumb: if the operation requires a dedicated service method (not
just a field setter), it is an action endpoint (POST with verb).

### Audit Trail Endpoint Naming

Every audit trail retrieval endpoint MUST use the `/audit-log` suffix.
The general pattern is `/{resource-scope}/audit-log`:

- Entity-scoped: `GET /api/v1/tickets/{ticket_id}/audit-log`
- Admin-scoped: `GET /api/v1/admin/identity/audit-log`
- Nested: `GET /api/v1/admin/settings/audit-log`
- Named resource: `GET /api/v1/fetchers/{fetcher_name}/audit-log`

See `docs/features/platform/audit-trail-infrastructure.md` for the full
audit trail specification.

## Endpoint Index

Each feature specification in `docs/features/` authoritatively defines its
own API endpoints with full request/response schemas, error codes, and
behavioral details.

For a complete cross-cutting index of all API endpoints — with HTTP
methods, paths, access levels, and links to the owning feature
specifications — see the
[Endpoint Permission Map](features/identity/rbac.md#endpoint-permission-map)
in the RBAC specification.
