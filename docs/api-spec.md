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
authorization level using one of the following formats:

- **`Access: Public`** — no authentication required
- **`Access: Authenticated`** — any logged-in user regardless of role
- **`Capability: <capability_name>`** — requires the specified capability
  (e.g., `Capability: create_ticket`)

The intentional field name change between `Access` and `Capability`
serves as a visual indicator: `Access` means "authentication level
only", `Capability` means "specific authorization check required". See
`docs/features/identity/rbac.md` for the full list of capabilities and
which roles include them.

**Scope** is an orthogonal dimension applied as an implicit query filter
(not at the endpoint level). Scope controls confidential ticket
visibility — it is evaluated by `confidential_ticket_filter()` and
`require_accessible_ticket`, not by endpoint decorators. See
`docs/features/identity/rbac.md` (Scope and Confidential Ticket
Visibility) for details.

The authorization declared in the owning feature specification is the
**authoritative source**. `docs/features/identity/rbac.md` maintains a
derived summary index (Endpoint Permission Map) for cross-referencing —
it is not the source of truth.

#### Authorization Chain Evaluation Order

For ticket endpoints that are capability-protected and operate on a
specific ticket, the authorization chain evaluates in this exact order:

1. **Authentication** (`get_current_user`) — returns 401 if not
   authenticated
2. **Capability** (`require_capability`) — returns 403
   `AUTH_INSUFFICIENT_PERMISSION` if the user lacks the required
   capability. This check does not depend on the specific ticket
3. **Ticket accessibility** (`require_accessible_ticket`) — returns 404
   for non-existent or invisible tickets

For mutation endpoints, a fourth check occurs at the **service layer**
(not as an API dependency):

4. **Operability guard** (`ensure_ticket_operable`) — raises 409
   `TICKET_NOT_MUTABLE` if the ticket is in Ignored or Duplicated
   status. This check executes under the `FOR UPDATE` lock and is the
   authoritative enforcement

This ordering is security-significant: the capability check (step 2)
fires before the accessibility check (step 3), preventing ticket
existence probing via differentiated error codes.

For CVE endpoints that are capability-protected and operate on a
specific CVE, the same pattern applies:

1. **Authentication** (`get_current_user`) — returns 401
2. **Capability** (`require_capability`) — returns 403
   `AUTH_INSUFFICIENT_PERMISSION`
3. **CVE accessibility** (`require_accessible_cve`) — returns 404
   `CVE_NOT_FOUND` for non-existent or inaccessible CVEs

For `GET /cves/{cve_id}/cvss` (Public — no authentication required),
only step 3 applies.

For non-ticket, non-CVE endpoints, only steps 1 and 2 apply.

#### Conditional Capability Checks

Some endpoints are Public or Authenticated but accept optional parameters
that require a capability.

Rules:

- The capability check is performed inline in the handler (not via the
  `require_capability()` dependency) only when the parameter is present
- If the caller lacks the required capability, the parameter is
  **silently ignored** — the endpoint returns results as if the parameter
  were not provided
- The endpoint never returns 403 for a missing query parameter on a
  Public or Authenticated endpoint; 403 is reserved for
  capability-protected endpoints
- When a parameter is silently ignored due to insufficient capability,
  the backend SHOULD emit a DEBUG-level log entry recording the caller
  identity and the ignored parameter name. The log MUST NOT include the
  parameter value to avoid log injection

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
| `AUTH_*` | Authentication and authorization | `AUTH_NOT_AUTHENTICATED`, `AUTH_INSUFFICIENT_PERMISSION`, `AUTH_API_KEY_INVALID`, `AUTH_API_KEY_NOT_FOUND`, `AUTH_API_KEY_NAME_CONFLICT`, `AUTH_API_KEY_NAME_INVALID`, `AUTH_API_KEY_INVALID_EXPIRY`, `AUTH_SSO_FAILED`, `AUTH_SSO_USER_NOT_FOUND`, `AUTH_SSO_USER_INACTIVE` |
| `TICKET_*` | Ticket operations | `TICKET_NOT_FOUND`, `TICKET_ALREADY_RESOLVED`, `TICKET_INVALID_TRANSITION`, `TICKET_NOT_MUTABLE`, `TICKET_NOT_CONFIDENTIAL`, `TICKET_DUPLICATE_CYCLE_DETECTED`, `TICKET_DUPLICATE_CHAIN_DEPTH`, `TICKET_SELF_DUPLICATE`, `TICKET_CVE_CONFLICT`, `TICKET_CVE_ALREADY_SET`, `TICKET_CVE_NOT_SET`, `TICKET_SEVERITY_DERIVED`, `TICKET_ASSIGNEE_NOT_VA`, `TICKET_ASSIGNEE_INACTIVE` |
| `CVE_*` | CVE operations | `CVE_NOT_FOUND`, `CVE_FETCH_FAILED`, `CVE_INVALID_SOURCE` |
| `CVSS_*` | CVSS assessment operations | `CVSS_INVALID_VECTOR`, `CVSS_ASSESSMENT_NOT_FOUND`, `CVSS_VERSION_MISMATCH`, `CVSS_DUPLICATE_ASSESSMENT` |
| `RESOURCE_*` | Generic resource errors | `RESOURCE_NOT_FOUND`, `RESOURCE_CONFLICT`, `RESOURCE_GONE`, `RESOURCE_NOT_EDITABLE` |
| `PACKAGE_*` | Package operations | `PACKAGE_NOT_FOUND_IN_SMELT`, `PACKAGE_ALREADY_EXCLUDED`, `PACKAGE_NOT_EXCLUDED`, `PACKAGE_RESTORE_BLOCKED` |
| `ROLE_MAPPING_*` | Role mapping operations | `ROLE_MAPPING_GROUP_NOT_FOUND`, `ROLE_MAPPING_INVALID_GROUP_CN` |
| `FETCHER_*` | Fetcher operations | `FETCHER_NOT_FOUND`, `FETCHER_ALREADY_RUNNING`, `FETCHER_DEREGISTERED`, `FETCHER_DISABLED` |
| `USER_*` | User operations | `USER_NOT_FOUND`, `USER_ALREADY_EXISTS`, `USER_INACTIVE`, `USER_ALREADY_INACTIVE`, `USER_AD_STATUS_READONLY`, `USER_AD_FIELD_READONLY`, `USER_AD_PASSWORD_FORBIDDEN`, `USER_AD_ROLE_PROTECTED`, `USER_AD_LOCKOUT`, `USER_SELF_ROLE_REMOVAL`, `USER_SELF_DEACTIVATION`, `USER_PASSWORD_POLICY_VIOLATION` |

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

#### Query Parameter Length Limit

Every string query parameter has an individual maximum length of 500
characters, unless the endpoint specifies otherwise. Values exceeding the
limit return `422 VALIDATION_ERROR`.

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

**Inverted range validation**: when both `from_date` and `to_date` are
provided and `from_date` is strictly after `to_date` (after timezone
normalization), the endpoint returns **400 Bad Request** with error code
`DATE_RANGE_INVERTED`. This validation applies globally to all endpoints
that accept date range parameters.

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
| 403    | `AUTH_INSUFFICIENT_PERMISSION` | User authenticated but lacks required capability | `require_capability` dependency |
| 422    | `VALIDATION_ERROR`       | Request body/query/path fails schema validation | FastAPI automatic (Pydantic)   |
| 500    | `INTERNAL_ERROR`         | Unhandled server error                         | Framework                      |

Notes:

- **Public endpoints** (explicitly marked) are exempt from 401
- The 401 response body is always `{"code": "AUTH_NOT_AUTHENTICATED",
  "detail": "Authentication required"}` regardless of the specific failure
  reason — no information about the failure cause is disclosed
- The 422 response uses Pydantic's native format with the `errors` array
  populated with field-level details
- The 403 response detail is always `"Insufficient permissions"` — it
  MUST NOT disclose which capability was required
- Endpoint error tables should only list responses that are specific to
  that endpoint's logic (e.g., 404, 409, 403 for capability requirements)

### Scoped Responses

Some shared dependencies apply to a specific resource group rather than to
all endpoints. Like global responses, scoped responses are not repeated in
per-endpoint error tables.

#### Ticket Accessibility Check

All endpoints under `/api/v1/tickets/{ticket_id}/` — including the ticket
detail endpoint itself — are subject to a centralized accessibility check
enforced by a router-level shared dependency (`require_accessible_ticket`).

The dependency evaluates conditions in this exact order:

1. **Existence**: if the ticket does not exist, return `404 TICKET_NOT_FOUND`
2. **Confidentiality**: if the ticket is confidential
   (`is_confidential=TRUE`) and the caller does not satisfy any
    visibility rule from `docs/features/identity/rbac.md` (Scope and
    Confidential Ticket Visibility) — scope `all`, explicit
    `TicketAccessGrant`, or bugowner match — return `404
    TICKET_NOT_FOUND` — indistinguishable from a non-existent ticket.
    The confidentiality evaluation reuses the shared
    `confidential_ticket_filter()` utility (see
    `docs/features/tickets/tickets.md`, Confidentiality Filtering) with
    the single-ticket column reference

| Status | Code              | Condition                                            |
|--------|-------------------|------------------------------------------------------|
| 404    | `TICKET_NOT_FOUND`| Ticket does not exist, or is confidential and caller is not authorized |

#### CVE Accessibility Check

All endpoints under `/api/v1/cves/{cve_id}/` are subject to a
centralized accessibility check enforced by a router-level shared
dependency (`require_accessible_cve`). This dependency is applied via
`dependencies=[...]` on the `APIRouter`, mirroring the
`require_accessible_ticket` pattern on the ticket router.

The dependency evaluates conditions in this exact order:

1. **Existence**: resolve the CVE by UUID or CVE-ID string (see CVE
   Identifier Resolution below). If no CVE matches, return
   `404 CVE_NOT_FOUND`
2. **Associated ticket check**: if the CVE has an associated ticket:
   a. **Confidentiality**: if the ticket is confidential
      (`is_confidential=TRUE`) and the caller does not satisfy any
      visibility rule from `docs/features/identity/rbac.md` (Scope and
      Confidential Ticket Visibility), return `404 CVE_NOT_FOUND` —
      indistinguishable from a non-existent CVE
3. **No associated ticket**: if the CVE has no associated ticket, it is
   freely accessible — CVE data is inherently public

| Status | Code              | Condition                                           |
|--------|-------------------|-----------------------------------------------------|
| 404    | `CVE_NOT_FOUND`   | CVE does not exist, or is associated with a confidential ticket and caller is not authorized |

All denial cases from this dependency return the same
`404 CVE_NOT_FOUND` response — never `TICKET_NOT_FOUND`.

**Post-accessibility service-layer errors**: mutation endpoints under
`/api/v1/cves/{cve_id}/` may still surface `409 TICKET_NOT_MUTABLE`
from `ensure_ticket_operable()` at the service layer. See the
per-endpoint error tables in `docs/features/tickets/cvss-scoring.md`
for details

Unauthenticated callers (`current_user=None`): step 2a always denies
access when the ticket is confidential — unauthenticated users can never
satisfy any visibility rule. This is consistent with
`confidential_ticket_filter()` behavior for unauthenticated requests.

**Relationship to `require_accessible_ticket`**: this dependency applies
the same confidentiality rules as `require_accessible_ticket`, with two
differences: (1) all denial cases return `404 CVE_NOT_FOUND` instead of
differentiating error codes, and (2) CVEs without an associated ticket
are freely accessible because CVE data is inherently public.

The two dependencies are intentionally kept as separate, self-contained
implementations — no shared abstraction is introduced. The access rules
are equivalent by convention, documented with this explicit
cross-reference.

Unlike the ticket router, no endpoints need to be excluded from this
check.

**Location**: `backend/app/core/dependencies.py` (alongside
`require_accessible_ticket` and `resolve_user_identifier`).

**Note**: the `GET /api/v1/cves` list endpoint lives on the parent
`/api/v1/cves/` router, NOT on the `/api/v1/cves/{cve_id}/` sub-router.
It is not covered by this dependency — confidentiality filtering is
handled inline via `confidential_ticket_filter()`.

#### Manual-Zone Mutability Guard

Tickets in the **manual zone** (status `Ignored` or `Duplicated`) are
immutable — mutation endpoints return `409 TICKET_NOT_MUTABLE`. This is
enforced at the service layer by `ensure_ticket_operable()` (defined in
`ticket_mutations`), which is called by all mutation functions after
acquiring `FOR UPDATE` on the ticket row.

| Status | Code                  | Condition                                          |
|--------|-----------------------|----------------------------------------------------|
| 409    | `TICKET_NOT_MUTABLE`  | Ticket is in Ignored or Duplicated status          |

**Exceptions** — the following service functions are excluded from this
check because they manage the manual-zone exit lifecycle:

- `reopen_from_ignored` (exit Ignored)
- `revert_duplicate` (exit Duplicated)

Read endpoints (GET) are never subject to this guard.

See `docs/features/tickets/tickets.md` ([Mutability Guard](docs/features/tickets/tickets.md#mutability-guard))
for the full specification.

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
with `id`, `username`, `full_name`, and `active` — populated via JOIN to
the **current** User record. These values reflect the user's current
profile data, not a historical snapshot at the time of the event or
action.

Historical values, where relevant, are preserved in dedicated fields of
the owning entity (e.g., `old_value` / `new_value` in audit events).
The `id` (UUID) is the stable, immutable identifier; `username`,
`full_name`, and `active` are display conveniences that may change over
time (e.g., via AD sync or deactivation).

Users are never physically deleted from the database — all foreign keys
referencing the User table use `ON DELETE RESTRICT`. Deactivated users
(`active=false`) are resolved normally, with all fields (`id`, `username`,
`full_name`, `active`) populated from current data. Consequently, a user
reference object is never null or partial when `user_id` is non-null — if
a `user_id` foreign key is present, the referenced user record is
guaranteed to exist and the serialized object will always be complete.

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

### CVE Identifier Resolution

All parameters that identify a CVE in `/api/v1/cves/` endpoints —
primarily the `{cve_id}` path parameter — accept either a UUID or a
CVE-ID string. Resolution is automatic:

- If the value is a valid UUID (RFC 4122 format), lookup is by primary
  key (`CVE.id`)
- If the value matches the CVE-ID format (`CVE-\d{4}-\d{4,}`), lookup
  is by the `CVE.cve_id` column (UNIQUE indexed)
- Otherwise, return `404 CVE_NOT_FOUND`

The CVE-ID string is the natural identifier used across all security
tooling (NVD, MITRE, advisories). Requiring UUID-only would force API
consumers to perform a two-step lookup (search for CVE-ID in ticket
list, extract UUID, then call the CVE endpoint). The dual resolution
eliminates this friction.

This follows the dual-identifier resolution pattern established by
`resolve_user_identifier` (see User Identifier Resolution above).

Implementation note: a reusable `resolve_cve_identifier` function in
`backend/app/core/dependencies.py`, analogous to
`resolve_user_identifier`.

### Mutation Patterns

Two patterns exist for modifying resources:

**PATCH — field update on an identified resource:**

```
PATCH /api/v1/tickets/{ticket_id}/severity
Body: {"severity_override": "critical"}
```

Used when the client sets one or more fields on a resource clearly
identified by the URL and the field assumes the requested value
(predictable outcome). Side effects are permitted when they are **domain
cascading consequences** — that is, reactions intrinsic to the data model
such as:

- Status propagation to related entities
- Eligibility or threshold re-evaluation
- Audit event creation
- Notification dispatch

These side effects are a consequence of the domain model, not additional
business workflows. The operation remains a PATCH because from the
client's perspective the semantics are "update this field on this
resource."

**POST with action verb — operation or command:**

```
POST /api/v1/tickets/{ticket_id}/ignore
Body: {"reason": "..."}
```

Used when the operation has characteristics that go beyond a field
update:

- **State machine guards** that may reject the operation (the field is
  not freely settable to any value)
- **Creation or destruction of separate entities** (not just cascading
  re-evaluation of existing records)
- **Irreversible operations** where the semantic weight is "execute a
  procedure" (revoke, delete, deactivate)
- **Lifecycle transitions** with cross-entity destructive mutations
  (session invalidation, key revocation, ticket reassignment)
- **Multi-entity commands** that affect multiple independent resources
  in a single operation

Rule of thumb: if the client perceives the operation as "set this field
to this value" and the field will reliably assume that value (barring
validation errors), use PATCH. If the client perceives the operation as
"perform this action" with guards, workflows, or irreversible
consequences beyond the target field, use POST with an action verb.

### Partial Update Semantics

All PATCH endpoints follow partial update semantics inspired by RFC 7396
(JSON Merge Patch). The request body includes only the fields the client
wants to change. Three cases are distinguished:

| Payload state              | Behavior                                        |
|----------------------------|-------------------------------------------------|
| Field **omitted**          | Current value is preserved (no change)           |
| Field present with **value** | Field is updated to the provided value          |
| Field present with **`null`** | Field is set to NULL in the database            |

Sending `null` is only meaningful for fields that are nullable in the
data model. Sending `null` for a non-nullable field results in a `422
VALIDATION_ERROR`.

When all fields in a PATCH request body are optional, the endpoint MUST
reject an empty body (no fields provided) with `422 VALIDATION_ERROR`
and the message `"At least one field must be provided."`. This does not
apply to single-field PATCH endpoints where the field is required.

Individual endpoint specifications document any domain-specific
semantics that `null` may carry beyond "clear the value" (e.g.,
resetting a computed value to automatic calculation, reverting to a
system default). The partial update semantics defined here are the
baseline; domain-specific meaning is additive.

Implementation note: in Pydantic v2, distinguishing "field omitted" from
"field explicitly set to `null`" requires a sentinel pattern (e.g., an
`UNSET` constant as the field default) or inspecting `model_fields_set`
after parsing. Standard `Optional[X] = None` conflates the two cases and
MUST NOT be used for PATCH request schemas with nullable fields.

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
