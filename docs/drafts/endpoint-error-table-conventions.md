# Endpoint Error Table Convention Consolidation

**Status**: Draft — pending review before application  
**Scope**: Specification-only (no code, no migrations)  
**Impact**: ~66 error table modifications across 15 feature specs + 2
cross-cutting documents + 1 agent definition + ~30 header renames + ~29
anchor updates in rbac.md

---

## 1. Problem Statement

The project has three documented rules governing what appears in
per-endpoint error tables, and they **contradict each other**:

| Source | Line(s) | States |
|--------|---------|--------|
| `api-spec.md` | 324-325 | "Individual endpoint error tables document only endpoint-specific errors; global responses are not repeated." |
| `api-spec.md` | 344-345 | "(e.g., 404, 409, **403 for capability requirements**)" as an example of endpoint-specific content |
| `conventions.md` | 622-626 | "errors handled globally (Pydantic validation, authentication, **authorization**, etc.) ... must not be repeated per-endpoint" |
| `conventions.md` | 933-936 | "Endpoint error tables retain their own HTTP status column only for errors that do NOT originate from a service exception (e.g., **framework-level 401/403 from auth dependencies**, 404 from path parameter resolution)" — implies 401/403 SHOULD appear |

The result: **40% of specs repeat generic 403**, 60% correctly omit it.
One spec (`package-model.md`) does both within the same file.

Additionally, `api-spec.md` defines **Scoped Responses** (lines 347-460)
with the same "not repeated" rule, but ~37 instances across 7 specs
repeat `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`, and `CVE_NOT_FOUND` in
per-endpoint tables.

### Quantitative Summary

| Violation type | Count | Files |
|----------------|-------|-------|
| Generic `403 AUTH_INSUFFICIENT_PERMISSION` repeated | 15 | 4 |
| Generic `401 AUTH_NOT_AUTHENTICATED` repeated | 1 | 1 |
| `422 VALIDATION_ERROR` for Pydantic-level constraints | 13 | 8 |
| Scoped responses repeated per-endpoint | ~37 | 7 |
| **Total** | **~66** | **15** |

---

## 2. Root Cause Analysis

The contradiction at `api-spec.md:344-345` — which lists "403 for
capability requirements" as an example of endpoint-specific content — was
likely added as a parenthetical clarification without cross-checking the
Global Responses table two lines above. Subsequent spec authors and
reviewers read this line as authorization to include 403 rows.

The contradiction at `conventions.md:933-936` was introduced in the
"Endpoint error tables (post-standardization)" section, which was
written to describe the *format* of error tables but inadvertently also
restated the *content* rule — with different semantics.

---

## 3. Design Decisions

### DD1 — Single Discriminant Principle

> A row belongs in an endpoint error table **if and only if** its
> *condition* conveys information specific to that endpoint that is not
> already stated by the Global Responses or Scoped Responses definitions
> in `api-spec.md`.

The discriminant is **information content**, not the HTTP status code.
A `403` with condition "Caller does not have required capability" adds
zero information beyond the global definition. A `403` with condition
"Request authenticated via API key instead of session"
(`AUTH_SESSION_REQUIRED`) is genuinely endpoint-specific and belongs in
the table.

**Corollary**: generic `401 AUTH_NOT_AUTHENTICATED`, `403
AUTH_INSUFFICIENT_PERMISSION`, `422 VALIDATION_ERROR` (Pydantic), and
`500 INTERNAL_ERROR` rows are NEVER repeated. Scoped responses
(`TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`, `CVE_NOT_FOUND`) are NEVER
repeated as full table rows.

### DD2 — Single-Owner Principle

The "don't repeat global/scoped responses" rule is owned by
**`api-spec.md`** (co-located with the tables it governs).
`conventions.md` references it but does not re-enunciate it.

This eliminates the dual-source drift that caused the current
contradiction.

### DD3 — Reference Line Mechanism

Each endpoint section (or API section preamble) includes a standardized
**reference line** that makes global/scoped applicability explicit
without repeating full table rows.

Five canonical variants:

**Variant A — Public endpoint (no 401/403):**
```
Global responses per `api-spec.md` apply (422, 500 only — public endpoint).
```

**Variant B — Authenticated endpoint (401 + optional 403 apply):**
```
Global responses per `api-spec.md` apply.
```

**Variant C — Ticket-scoped endpoint (adds scoped responses):**
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.
```

**Variant D — CVE-scoped endpoint (adds CVE scoped response):**
```
Global responses per `api-spec.md` apply. Scoped: `CVE_NOT_FOUND`.
```

**Variant E — CVE-scoped with ticket mutability (adds both):**
```
Global responses per `api-spec.md` apply. Scoped: `CVE_NOT_FOUND`, `TICKET_NOT_MUTABLE`.
```

**Placement**: the reference line appears immediately before the error
table (or in place of a now-empty table). If a spec groups multiple
endpoints under a single API section with a shared preamble that already
states global applicability (as `system-settings.md:124-127` does), that
preamble satisfies the requirement for all endpoints in the group.

### DD4 — Conditional Authorization in Prose

Authorization conditions that are *genuinely endpoint-specific* (e.g.,
`admin_ticket_ops` required only for `status=FIXED`) are documented in
the **Behavior** prose or capability declaration, NOT as a 403 row in
the error table. The error response to the consumer is still the generic
`AUTH_INSUFFICIENT_PERMISSION` with no capability disclosure — adding a
table row provides no consumer-actionable information.

Example pattern (already used correctly in `tickets.md:1319-1322`):

> Setting `status` to `FIXED` requires the `admin_ticket_ops` capability
> (Hard Conditional Check). Users with only `manage_packages` can set any
> other status but not `FIXED`.

### DD5 — Pydantic Validation Rows Removed

Rows documenting conditions that Pydantic schema validation handles
automatically (type coercion, enum membership, string length, regex
patterns, `min_length`, cross-field exclusivity via `model_validator`)
are removed from endpoint tables. They are covered by the global `422
VALIDATION_ERROR` response.

Domain-specific validation with **dedicated error codes** (e.g.,
`CVSS_INVALID_VECTOR`, `ROLE_MAPPING_INVALID_GROUP_CN`,
`FETCHER_SETTING_INVALID`, `PACKAGE_NOT_FOUND_IN_SMELT`) remain in the
table — they represent endpoint-specific conditions distinguishable by
consumers.

**Classification of all current 422 `VALIDATION_ERROR` rows:**

| Location | Condition | Verdict |
|----------|-----------|---------|
| `ticket-references.md:660` | URL fails HttpUrl, length, blank, invalid type | Pydantic → **remove** |
| `ticket-references.md:739` | Same as above (PATCH) | Pydantic → **remove** |
| `system-settings.md:185` | Unsupported CVSS version (Literal enum) | Pydantic → **remove** |
| `fetcher-operations.md:686` | Invalid cron/timeout/delay (field_validator) | Pydantic → **remove** |
| `user-management.md:551` | `search` < 2 chars (Query min_length) | Pydantic → **remove** |
| `ad-integration.md:734` | Missing/empty `ad_group_cn`, unknown role | Pydantic → **remove** |
| `package-model.md:1196` | package_name empty/255/regex | Pydantic → **remove** |
| `package-model.md:1560` | Invalid status value (enum) | Pydantic → **remove** |
| `package-model.md:1638` | `eligible` field not provided (required) | Pydantic → **remove** |
| `package-model.md:1782` | `search`+`name` mutex, `per_page`>100 | Pydantic → **remove** |
| `product-catalog.md:211` | Non-integer page, unknown lifecycle_phase | Pydantic → **remove** |
| `ibs-submission-tracking.md:933` | Invalid `state` value (enum) | Pydantic → **remove** |
| `ibs-submission-tracking.md:992` | Invalid `state`/`incident_number` value | Pydantic → **remove** |

**New error codes needed: 0** (zero). All domain-specific codes already
exist.

### DD6 — Scoped Responses Referenced by Code

Scoped responses are listed by code in the reference line (DD3, Variants
C/D/E) rather than as full table rows. This provides discoverability
(the reader knows which scoped checks apply) without information
duplication.

**Exception**: when an endpoint's error table cites a scoped response
code whose **Condition column** conveys a semantically distinct variant
not captured by the Scoped Responses definition or by endpoint prose
(e.g., a `TICKET_NOT_FOUND` row with condition "Ticket or *target*
ticket not found" — dual-resolution semantics not covered by the
single-entity scoped check), the code remains in the table because the
condition text adds information beyond the scoped definition. The key
test: does the row's "Condition" column say something that neither the
Scoped Responses section nor the endpoint's surrounding prose already
states?

**Corollary**: if the endpoint-specific nuance is already captured in
adjacent prose (e.g., `cvss-scoring.md:598-599` documents that
`TICKET_NOT_MUTABLE` applies only when the CVE has an associated
ticket), a reference-line entry is sufficient — the table row adds no
information the reader cannot already see.

### DD7 — `per_page` Default Alignment

`authentication.md` uses `per_page` default of 50 for `GET
/api/v1/admin/api-keys`. No documented justification exists for the
deviation from the standard default of 20 (per `api-spec.md:193`).
**Align to 20.** No exception mechanism is introduced.

### DD8 — Header Normalization (Descriptive Format)

All API endpoint headers in feature specs are standardized to
**descriptive format** (e.g., `### List Tickets`, `### Create API Key`).
Path-literal format (e.g., `` ### `GET /api/v1/tickets` ``) is
eliminated.

**Rationale**:
- Anchors are short, readable, and stable (`#list-tickets` vs
  `#get-apiv1tickets`)
- The HTTP method + full path already appears in the code block
  immediately following each header
- ~60% of specs already use descriptive format (less rework)
- GitHub/CommonMark anchor generation for backtick/special-char headers
  is unpredictable across renderers

**Affected specs** (7):
- `authentication.md` (7 headers)
- `local-authentication.md` (2 headers)
- `ibs-submission-tracking.md` (2 headers)
- `cve-tracking.md` (1 header)
- `sso-authentication.md` (3 headers)
- `user-management.md` (9 headers)
- `maintainer.md` (4 headers)
- `cve-service.md` (2 headers — hybrid `## Title: \`METHOD /path\``)

**Anchor consumers**: all cross-references to these headers originate
exclusively from `rbac.md` (verified via grep — no other spec links to
these anchors).

---

## 4. Canonical Rule Text

The following text replaces the contradictory passages in `api-spec.md`
and `conventions.md`.

### 4a. New text for `api-spec.md` (replaces lines 324-345)

```markdown
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

#### What belongs in an endpoint error table

An error row belongs in a per-endpoint table **if and only if** its
condition conveys information specific to that endpoint that is not
already stated by the Global Responses table above or the Scoped
Responses section below.

**Include**: errors with endpoint-specific codes (e.g., `TICKET_CVE_CONFLICT`),
errors from service exceptions with domain semantics (e.g.,
`PACKAGE_ALREADY_EXCLUDED`), and errors with a different error code from
the global one for the same status (e.g., `AUTH_SESSION_REQUIRED` instead
of generic `AUTH_INSUFFICIENT_PERMISSION`).

**Exclude**: generic `401 AUTH_NOT_AUTHENTICATED`, generic `403
AUTH_INSUFFICIENT_PERMISSION`, generic `422 VALIDATION_ERROR` (Pydantic
schema failures), and `500 INTERNAL_ERROR`. These are global and provide
no endpoint-specific information.

**Conditional authorization**: when an endpoint has authorization logic
beyond the base `require_capability()` guard (e.g., a secondary
capability required only for specific input values), document the
condition in the **Behavior** section or capability declaration — not as
a 403 row in the error table. The HTTP response is still the generic
`AUTH_INSUFFICIENT_PERMISSION` (the consumer cannot distinguish it).

**Pydantic-level validation**: constraints enforceable via Pydantic
schema definitions (type, enum membership, string length, regex, cross-field
exclusivity, required fields) produce the global `422 VALIDATION_ERROR`
automatically. Do not add a separate row for these. Only
domain-specific validation with a **dedicated error code** (e.g.,
`CVSS_INVALID_VECTOR`, `FETCHER_SETTING_INVALID`) warrants a table row.

**Reference line**: each endpoint section should include a brief note
indicating which global and scoped responses apply. Example:
`Global responses per api-spec.md apply. Scoped: TICKET_NOT_FOUND, TICKET_NOT_MUTABLE.`
```

### 4b. New text for `conventions.md` line 622-626

Replace the current text:

```markdown
### API Cross-references

Feature specs that define API endpoints MUST include `docs/api-spec.md` in
their Cross-references section. The rules governing what appears in
per-endpoint error tables (global responses, scoped responses, Pydantic
validation) are defined in `docs/api-spec.md` (section "What belongs in
an endpoint error table") and are not restated here.
```

### 4c. New text for `conventions.md` lines 926-936

Replace the current "Endpoint error tables (post-standardization)"
section:

```markdown
#### Endpoint error tables (post-standardization)

Endpoint-level error tables in feature specs document errors specific to
the endpoint's logic. They reference the error code and condition for
traceability — the authoritative HTTP status mapping for service
exceptions lives in the owning service spec's exception table.

Global and scoped responses (defined in `api-spec.md`) are never
included as table rows — they are covered by a reference line.
```

---

## 5. Prescriptive Change Inventory

### 5.1 `docs/api-spec.md`

#### 5.1.1 Replace Global Responses notes (lines 334-345)

**Before** (lines 334-345):
```
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
```

**After**:
```
Notes:

- **Public endpoints** (explicitly marked) are exempt from 401
- The 401 response body is always `{"code": "AUTH_NOT_AUTHENTICATED",
  "detail": "Authentication required"}` regardless of the specific failure
  reason — no information about the failure cause is disclosed
- The 422 response uses Pydantic's native format with the `errors` array
  populated with field-level details
- The 403 response detail is always `"Insufficient permissions"` — it
  MUST NOT disclose which capability was required

#### What belongs in an endpoint error table

An error row belongs in a per-endpoint table **if and only if** its
condition conveys information specific to that endpoint that is not
already stated by the Global Responses table above or the Scoped
Responses section below.

**Include**: errors with endpoint-specific codes (e.g., `TICKET_CVE_CONFLICT`),
errors from service exceptions with domain semantics (e.g.,
`PACKAGE_ALREADY_EXCLUDED`), and errors with a different error code from
the global one for the same status (e.g., `AUTH_SESSION_REQUIRED` instead
of generic `AUTH_INSUFFICIENT_PERMISSION`).

**Exclude**: generic `401 AUTH_NOT_AUTHENTICATED`, generic `403
AUTH_INSUFFICIENT_PERMISSION`, generic `422 VALIDATION_ERROR` (Pydantic
schema failures), and `500 INTERNAL_ERROR`. These are global and provide
no endpoint-specific information.

**Conditional authorization**: when an endpoint has authorization logic
beyond the base `require_capability()` guard (e.g., a secondary
capability required only for specific input values), document the
condition in the **Behavior** section or capability declaration — not as
a 403 row in the error table. The HTTP response is still the generic
`AUTH_INSUFFICIENT_PERMISSION` (the consumer cannot distinguish it).

**Pydantic-level validation**: constraints enforceable via Pydantic
schema definitions (type, enum membership, string length, regex, cross-field
exclusivity, required fields) produce the global `422 VALIDATION_ERROR`
automatically. Do not add a separate row for these. Only
domain-specific validation with a **dedicated error code** (e.g.,
`CVSS_INVALID_VECTOR`, `FETCHER_SETTING_INVALID`) warrants a table row.

**Reference line**: each endpoint section should include a brief note
indicating which global and scoped responses apply. Example:
`Global responses per api-spec.md apply. Scoped: TICKET_NOT_FOUND, TICKET_NOT_MUTABLE.`
```

#### 5.1.2 Register `AUTH_LOGOUT_NOT_APPLICABLE` (line 146)

**Before** (line 146):
```
| `AUTH_*` | Authentication and authorization | `AUTH_NOT_AUTHENTICATED`, `AUTH_INSUFFICIENT_PERMISSION`, `AUTH_API_KEY_INVALID`, `AUTH_API_KEY_NOT_FOUND`, `AUTH_API_KEY_NAME_CONFLICT`, `AUTH_API_KEY_NAME_INVALID`, `AUTH_API_KEY_INVALID_EXPIRY`, `AUTH_SSO_FAILED`, `AUTH_SSO_USER_NOT_FOUND`, `AUTH_SSO_USER_INACTIVE`, `AUTH_SESSION_REQUIRED`, `AUTH_INVALID_CREDENTIALS`, `AUTH_ACCOUNT_LOCKED`, `AUTH_SSO_STATE_INVALID`, `AUTH_SSO_DISABLED` |
```

**After**:
```
| `AUTH_*` | Authentication and authorization | `AUTH_NOT_AUTHENTICATED`, `AUTH_INSUFFICIENT_PERMISSION`, `AUTH_API_KEY_INVALID`, `AUTH_API_KEY_NOT_FOUND`, `AUTH_API_KEY_NAME_CONFLICT`, `AUTH_API_KEY_NAME_INVALID`, `AUTH_API_KEY_INVALID_EXPIRY`, `AUTH_SSO_FAILED`, `AUTH_SSO_USER_NOT_FOUND`, `AUTH_SSO_USER_INACTIVE`, `AUTH_SESSION_REQUIRED`, `AUTH_INVALID_CREDENTIALS`, `AUTH_ACCOUNT_LOCKED`, `AUTH_SSO_STATE_INVALID`, `AUTH_SSO_DISABLED`, `AUTH_LOGOUT_NOT_APPLICABLE` |
```

#### 5.1.3 Fix stale cross-reference in CVE Accessibility Check (lines 406-410)

**Before** (lines 406-410):
```
Post-accessibility service-layer errors: mutation endpoints under
`/api/v1/cves/{cve_id}/` may still surface `409 TICKET_NOT_MUTABLE`
from `ensure_ticket_operable()` at the service layer. See the
per-endpoint error tables in `docs/features/tickets/cvss-scoring.md`
for details
```

**After**:
```
Post-accessibility service-layer errors: mutation endpoints under
`/api/v1/cves/{cve_id}/` may still surface `409 TICKET_NOT_MUTABLE`
from `ensure_ticket_operable()` at the service layer. This applies
only when the CVE has an associated ticket in a manual-zone status
(see Manual-Zone Mutability Guard below)
```

(The external reference to cvss-scoring.md is removed because the
error table there will no longer contain `TICKET_NOT_MUTABLE` as a
row — it is listed in the reference line as a scoped response. The
conditionality information is now self-contained in this paragraph
with an internal cross-reference to the Mutability Guard section
immediately above.)

---

### 5.2 `docs/conventions.md`

#### 5.2.1 Replace API Cross-references section (lines 620-626)

**Before**:
```
### API Cross-references

Feature specs that define API endpoints MUST include `docs/api-spec.md` in
their Cross-references section. Endpoint-specific error tables document only
errors unique to the endpoint logic; errors handled globally (Pydantic
validation, authentication, authorization, etc.) are defined in `api-spec.md`
and must not be repeated per-endpoint.
```

**After**:
```
### API Cross-references

Feature specs that define API endpoints MUST include `docs/api-spec.md` in
their Cross-references section. The rules governing what appears in
per-endpoint error tables (global responses, scoped responses, Pydantic
validation) are defined in `docs/api-spec.md` (section "What belongs in
an endpoint error table") and are not restated here.
```

#### 5.2.2 Replace "Endpoint error tables" section (lines 926-936)

**Before**:
```
#### Endpoint error tables (post-standardization)

Endpoint-level error tables in feature specs (e.g., `tickets.md`,
`user-management.md`) MUST NOT repeat the HTTP status code for service
exceptions. They reference the exception class name and error code for
traceability — the authoritative HTTP mapping lives in the service spec.

Endpoint error tables retain their own HTTP status column only for
errors that do NOT originate from a service exception (e.g.,
framework-level 401/403 from auth dependencies, 404 from path parameter
resolution).
```

**After**:
```
#### Endpoint error tables (post-standardization)

Endpoint-level error tables in feature specs document errors specific to
the endpoint's logic. They reference the error code and condition for
traceability — the authoritative HTTP status mapping for service
exceptions lives in the owning service spec's exception table.

Global and scoped responses (defined in `api-spec.md`) are never
included as table rows — they are covered by a reference line.
```

---

### 5.3 `docs/features/packages/package-model.md`

#### 5.3.1 Add Package to Ticket (lines 1189-1198)

**Before** (lines 1189-1198):
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Package exists on this ticket but is soft-deleted — use the restore endpoint |
| 422 | `VALIDATION_ERROR` | Missing or empty `package_name`, exceeds 255 characters, or contains invalid characters |
| 422 | `PACKAGE_NOT_FOUND_IN_SMELT` | SMELT returned no results for the given package name |
| 503 | `SMELT_UNAVAILABLE` | SMELT is unreachable or returned a server error |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Package exists on this ticket but is soft-deleted — use the restore endpoint |
| 422 | `PACKAGE_NOT_FOUND_IN_SMELT` | SMELT returned no results for the given package name |
| 503 | `SMELT_UNAVAILABLE` | SMELT is unreachable or returned a server error |
```

#### 5.3.2 Soft-Delete Package (lines 1236-1244)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Package is already soft-deleted |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Package is already soft-deleted |
```

#### 5.3.3 Restore Package (lines 1273-1282)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 422 | `PACKAGE_NOT_EXCLUDED` | Package is not directly soft-deleted |
| 422 | `PACKAGE_RESTORE_BLOCKED` | Package has no active tracks with active products. Restore at least one track (with active products) first. |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 422 | `PACKAGE_NOT_EXCLUDED` | Package is not directly soft-deleted |
| 422 | `PACKAGE_RESTORE_BLOCKED` | Package has no active tracks with active products. Restore at least one track (with active products) first. |
```

#### 5.3.4 Soft-Delete Track (lines 1341-1349)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Track is already soft-deleted |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Track is already soft-deleted |
```

#### 5.3.5 Restore Track (lines 1377-1386)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 422 | `PACKAGE_NOT_EXCLUDED` | Track is not directly soft-deleted |
| 422 | `PACKAGE_RESTORE_BLOCKED` | Track has no active products. Restore at least one product first. |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 422 | `PACKAGE_NOT_EXCLUDED` | Track is not directly soft-deleted |
| 422 | `PACKAGE_RESTORE_BLOCKED` | Track has no active products. Restore at least one product first. |
```

#### 5.3.6 Soft-Delete Product (lines 1447-1455)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Product is already soft-deleted |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Product is already soft-deleted |
```

#### 5.3.7 Restore Product (lines 1481-1489)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 422 | `PACKAGE_NOT_EXCLUDED` | Product is not directly soft-deleted |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 422 | `PACKAGE_NOT_EXCLUDED` | Product is not directly soft-deleted |
```

#### 5.3.8 Change Track Status (lines 1553-1561)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability, or caller attempts `status = FIXED` without `admin_ticket_ops` |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package or track not found on this ticket |
| 422 | `VALIDATION_ERROR` | Invalid status value |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package or track not found on this ticket |
```

Note: the conditional `admin_ticket_ops` requirement for `status=FIXED`
is already documented in the Behavior section (line 1513-1515) and the
capability declaration (line 1550-1551). No additional 403 row is needed.

#### 5.3.9 Override Product Eligibility (lines 1631-1639)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package or product not found on this ticket |
| 422 | `VALIDATION_ERROR` | `eligible` field not provided |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package or product not found on this ticket |
```

#### 5.3.10 List Ticket Packages (lines 1676-1682)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `TICKET_NOT_FOUND` | Ticket does not exist |

(Global errors from `api-spec.md` apply but are not repeated.)
```

**After**:
```
Global responses per `api-spec.md` apply (422, 500 only — public endpoint). Scoped: `TICKET_NOT_FOUND`.
```

(Table removed entirely — `TICKET_NOT_FOUND` is a scoped response.)

#### 5.3.11 Search Packages Across Tickets (lines 1778-1784)

**Before**:
```
#### Error Responses

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | Both `search` and `name` provided; `per_page` > 100 |

(Global errors from `api-spec.md` apply but are not repeated.)
```

**After**:
```
#### Error Responses

Global responses per `api-spec.md` apply (422, 500 only — public endpoint).
```

(Table removed — mutual exclusivity and max pagination are Pydantic-level.)

---

### 5.4 `docs/features/platform/system-settings.md`

#### 5.4.1 Section preamble (lines 124-127)

**Before**:
```
Global responses
(401, 422) apply per `api-spec.md` "Global Responses" section. 403
(`AUTH_INSUFFICIENT_PERMISSION`) is returned for authenticated users
without the required capability.
```

**After**:
```
Global responses per `api-spec.md` apply to all endpoints in this section.
```

#### 5.4.2 Get System Settings error table (lines 147-151)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
```

**After**:

Remove the entire error responses block (no endpoint-specific errors
remain). The section preamble already covers globals.

#### 5.4.3 Update System Settings error table (lines 179-186)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 409 | `RECALC_ALREADY_IN_PROGRESS` | A recalculation batch is already running (setting change blocked until current batch completes) |
| 422 | `VALIDATION_ERROR` | Invalid setting value (e.g., unsupported CVSS version) |
| 503 | `REDIS_UNAVAILABLE` | Redis broker is unreachable (setting change requires broker availability) |
```

**After**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `RECALC_ALREADY_IN_PROGRESS` | A recalculation batch is already running (setting change blocked until current batch completes) |
| 503 | `REDIS_UNAVAILABLE` | Redis broker is unreachable (setting change requires broker availability) |
```

#### 5.4.4 Trigger CVSS Recalculation error table (lines 247-254)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have `manage_settings` capability |
| 409 | `RECALC_ALREADY_IN_PROGRESS` | A recalculation batch is already running (slot occupied) |
| 503 | `REDIS_UNAVAILABLE` | Redis is unreachable (slot acquisition failed) |
| 503 | `CELERY_ENQUEUE_FAILED` | Task could not be enqueued (slot released) |
```

**After**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `RECALC_ALREADY_IN_PROGRESS` | A recalculation batch is already running (slot occupied) |
| 503 | `REDIS_UNAVAILABLE` | Redis is unreachable (slot acquisition failed) |
| 503 | `CELERY_ENQUEUE_FAILED` | Task could not be enqueued (slot released) |
```

#### 5.4.5 List Settings Audit Events error table (lines ~340-342)

The audit log endpoint currently has a 403 row. Remove it (covered by
section preamble). If no other endpoint-specific errors exist, remove
the error responses block entirely.

---

### 5.5 `docs/features/identity/identity-audit-log.md`

#### 5.5.1 List Identity Audit Events (lines 162-166)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
```

**After**:

Remove the entire error responses block (no endpoint-specific errors).
Add reference line:

```
Global responses per `api-spec.md` apply.
```

#### 5.5.2 List My Identity Audit Events (lines 244-248)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 401 | `AUTH_NOT_AUTHENTICATED` | Caller is not authenticated |
```

**After**:

Remove the entire error responses block (no endpoint-specific errors —
401 is a global response).

```
Global responses per `api-spec.md` apply.
```

---

### 5.6 `docs/features/identity/authentication.md`

#### 5.6.1 Admin Revoke API Key error table (lines 894-899)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 404 | `AUTH_API_KEY_NOT_FOUND` | Key not found |
```

**After**:
```
Global responses per `api-spec.md` apply.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `AUTH_API_KEY_NOT_FOUND` | Key not found |
```

#### 5.6.2 `per_page` default (line 822)

**Before**:
```
| `per_page` | int    | Items per page (default 50, max 100)  |
```

**After**:
```
| `per_page` | int    | Items per page (default 20, max 100)  |
```

#### 5.6.3 Response example `per_page` (line 847)

**Before**:
```
    "per_page": 50
```

**After**:
```
    "per_page": 20
```

#### 5.6.4 Authentication declaration style (line 606)

**Before**:
```
**Authentication**: required (JWT or API key).
```

**After**:
```
**`Access: Authenticated`**
```

(The "(JWT or API key)" clarification is redundant — all authenticated
endpoints accept both by default per the auth architecture.)

#### 5.6.5 `GET /api/v1/api-keys` authentication declaration (line 666)

**Before**:
```
**Authentication**: required.
```

**After**:
```
**`Access: Authenticated`**
```

#### 5.6.6 `POST /api/v1/api-keys/{key_id}/revoke` auth declaration (line 772)

**Before**:
```
**Authentication**: required.
```

**After**:
```
**`Access: Authenticated`**
```

#### 5.6.7 Add Cross-references section

`authentication.md` defines 7 API endpoints but does not include
`docs/api-spec.md` in its Cross-references section. Add it to the
existing cross-references list (lines 1040-1053).

---

### 5.7 `docs/features/tickets/tickets.md`

#### 5.7.1 Error format normalization (bullet → table)

The following endpoints use bullet-list format for errors. Convert to
table format for consistency. Additionally, remove scoped responses
(`TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`) and add reference lines.

**Get Ticket** (lines 1283-1285):

**Before**:
```
Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
```

**After**:
```
Global responses per `api-spec.md` apply (422, 500 only — public endpoint). Scoped: `TICKET_NOT_FOUND`.
```

(No endpoint-specific errors remain after removing the scoped response.)

**Create Ticket** (lines 1327-1331):

**Before**:
```
Error responses:

- 409 with code `TICKET_CVE_CONFLICT`: CVE is already associated with
  another ticket. Response body includes `existing_ticket_id` (UUID) to
  allow the frontend to link to the existing ticket
```

**After**:
```
Global responses per `api-spec.md` apply.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `TICKET_CVE_CONFLICT` | CVE is already associated with another ticket. Response includes `existing_ticket_id` (UUID) |
```

Note: The conditional `manage_confidentiality` check for
`is_confidential` (documented at lines 1319-1322) stays in the Behavior
prose — consistent with DD4.

**Associate CVE** (lines 1360-1369):

**Before**:
```
Error responses:

- 400 with code `TICKET_CVE_ALREADY_SET`: ticket already has a CVE
  associated
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_CVE_CONFLICT`: CVE is already associated with
  another ticket. Response body includes `existing_ticket_id` (UUID) to
  allow the frontend to link to the existing ticket
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `TICKET_CVE_ALREADY_SET` | Ticket already has a CVE associated |
| 409 | `TICKET_CVE_CONFLICT` | CVE is already associated with another ticket. Response includes `existing_ticket_id` (UUID) |
```

**Set Severity Override** (lines 1396-1402):

**Before**:
```
Error responses:

- 409 with code `TICKET_SEVERITY_DERIVED`: ticket has an associated CVE
  (severity is derived from CVSS, not manually settable)
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `TICKET_SEVERITY_DERIVED` | Ticket has an associated CVE (severity is derived from CVSS) |
```

**Assign Ticket** (lines 1445-1453):

**Before**:
```
Error responses:

- 400 with code `TICKET_ASSIGNEE_NOT_VA`: target user does not hold the
  Vulnerability Analyst role
- 409 with code `TICKET_ASSIGNEE_INACTIVE`: target user is inactive
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 404 with code `USER_NOT_FOUND`: target user not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status (use the dedicated reopen or revert-duplicate endpoints instead)
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `TICKET_ASSIGNEE_NOT_VA` | Target user does not hold the Vulnerability Analyst role |
| 404 | `USER_NOT_FOUND` | Target user not found |
| 409 | `TICKET_ASSIGNEE_INACTIVE` | Target user is inactive |
```

**Ignore Ticket** (lines 1474-1480):

**Before**:
```
Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status (ticket is in Ignored or Duplicated status)
- 409 with code `TICKET_INVALID_TRANSITION`: current status does not
  allow transition to Ignored (ticket is in Analyzed or Resolved status)
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `TICKET_INVALID_TRANSITION` | Current status does not allow transition to Ignored |
```

**Mark Ticket as Duplicate** (lines 1511-1521):

**Before**:
```
Error responses:

- 400 with code `TICKET_SELF_DUPLICATE`: resolved target is the same
  ticket (self-reference after chain resolution)
- 404 with code `TICKET_NOT_FOUND`: ticket or target ticket not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status
- 409 with code `TICKET_DUPLICATE_CYCLE_DETECTED`: duplicate resolution
  would create a cycle in the chain
- 409 with code `TICKET_DUPLICATE_CHAIN_DEPTH`: chain depth exceeded
  (indicates data corruption requiring manual intervention)
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 400 | `TICKET_SELF_DUPLICATE` | Resolved target is the same ticket (self-reference after chain resolution) |
| 404 | `TICKET_NOT_FOUND` | Target ticket (`duplicate_of_id`) does not exist |
| 409 | `TICKET_DUPLICATE_CYCLE_DETECTED` | Duplicate resolution would create a cycle |
| 409 | `TICKET_DUPLICATE_CHAIN_DEPTH` | Chain depth exceeded (data corruption) |
```

Note: The `404 TICKET_NOT_FOUND` row is retained per DD6 exception —
the scoped `require_accessible_ticket` only covers the source ticket
(path parameter). The target ticket resolution from the request body
is endpoint-specific (dual-resolution semantics).

**Reopen Ticket** (lines 1546-1550):

**Before**:
```
Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_INVALID_TRANSITION`: ticket is not in Ignored
  status
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `TICKET_INVALID_TRANSITION` | Ticket is not in Ignored status |
```

Note: This endpoint is NOT subject to `TICKET_NOT_MUTABLE` (it is the
dedicated exit from the Ignored manual-zone).

**Revert Duplicate Status** (lines 1575-1579):

**Before**:
```
Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_INVALID_TRANSITION`: ticket is not in Duplicated
  status
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `TICKET_INVALID_TRANSITION` | Ticket is not in Duplicated status |
```

Note: This endpoint is NOT subject to `TICKET_NOT_MUTABLE` (it is the
dedicated exit from the Duplicated manual-zone).

**Set Confidentiality** (lines 1600-1604):

This endpoint already uses a table. Remove scoped responses:

**Before**:
```
| Status | Code | Condition |
|--------|------|-----------|
| 200    | -    | Success (or already in requested state) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found |
| 409    | `TICKET_NOT_MUTABLE` | Ticket is in Ignored or Duplicated status |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.
```

(No endpoint-specific error responses remain. The 200 success row is
not an error and should not be in an error table.)

**List Access Grants** (lines 1645-1649):

**Before**:
```
| Status | Code | Condition |
|--------|------|-----------|
| 200    | -    | Success |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |
```

**Grant Access** (lines 1673-1681):

**Before**:
```
| Status | Code | Condition |
|--------|------|-----------|
| 201    | -    | Grant created |
| 200    | -    | Grant already exists (idempotent success) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 404    | `USER_NOT_FOUND` | Target user not found |
| 409    | `TICKET_NOT_MUTABLE` | Ticket is in Ignored or Duplicated status |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `USER_NOT_FOUND` | Target user not found |
| 409 | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |
```

**Revoke Access** (lines 1699-1705):

**Before**:
```
| Status | Code | Condition |
|--------|------|-----------|
| 204    | -    | Grant revoked (or did not exist — idempotent success) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 404    | `USER_NOT_FOUND` | Target user not found |
| 409    | `TICKET_NOT_MUTABLE` | Ticket is in Ignored or Duplicated status |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `USER_NOT_FOUND` | Target user not found |
| 409 | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |
```

---

### 5.8 `docs/features/tickets/cvss-scoring.md`

#### 5.8.1 Set or Update SUSE CVSS Assessment (lines 590-596)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `CVE_NOT_FOUND` | CVE not found or inaccessible (see `docs/api-spec.md`, CVE Accessibility Check) |
| 409 | `TICKET_NOT_MUTABLE` | Associated ticket is in Ignored or Duplicated status |
| 422 | `CVSS_INVALID_VECTOR` | Vector string is malformed or unparseable |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `CVE_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `CVSS_INVALID_VECTOR` | Vector string is malformed or unparseable |
```

Note: `TICKET_NOT_MUTABLE` applies only when the CVE has an associated
ticket (retained in prose at line 598-599).

#### 5.8.2 Delete SUSE CVSS Assessment (lines 619-625)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `CVE_NOT_FOUND` | CVE not found or inaccessible (see `docs/api-spec.md`, CVE Accessibility Check) |
| 404 | `CVSS_ASSESSMENT_NOT_FOUND` | No SUSE assessment exists for the specified version |
| 409 | `TICKET_NOT_MUTABLE` | Associated ticket is in Ignored or Duplicated status |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `CVE_NOT_FOUND`, `TICKET_NOT_MUTABLE`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `CVSS_ASSESSMENT_NOT_FOUND` | No SUSE assessment exists for the specified version |
```

#### 5.8.3 Fix absent-case example (lines 530-541)

**Before**:
```json
{
  "cve_id": "CVE-2025-12345",
  "assessments": [],
  "severity": null,
  "eligibility": {
    "score": 10.0,
    "source": "fallback"
  }
}
```

**After**:
```json
{
  "data": {
    "assessments": [],
    "default_cvss_version": "3.1",
    "severity": null,
    "eligibility": {
      "score": 10.0,
      "source": "fallback"
    }
  }
}
```

(Wrapped in `data` envelope consistent with the main example at lines
467-503; removed spurious `cve_id` field not present in the normal
response.)

---

### 5.9 `docs/features/tickets/ticket-references.md`

#### 5.9.1 Add Reference error table (lines 655-660)

**Before**:
```
**Error responses**:

| Status | Code                | Condition                                     |
|--------|---------------------|-----------------------------------------------|
| 409    | `RESOURCE_CONFLICT` | URL already exists for this ticket             |
| 422    | `VALIDATION_ERROR`  | URL fails RFC 3986 validation (via `HttpUrl`), exceeds length limit, blank title/description, or invalid type value |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `RESOURCE_CONFLICT` | URL already exists for this ticket |
```

#### 5.9.2 Update Reference error table (around line 739)

Remove the `422 VALIDATION_ERROR` row (line 739) — Pydantic-level
validation. Keep the three endpoint-specific rows (`RESOURCE_NOT_FOUND`,
`RESOURCE_NOT_EDITABLE`, `RESOURCE_CONFLICT`).

The file already has a reference line at line 741: `See docs/api-spec.md
for global and scoped responses.` — update it to the canonical format:

```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.
```

---

### 5.10 `docs/features/packages/product-catalog.md`

#### 5.10.1 List Products error table (lines 207-211)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | Invalid query parameter value (e.g., non-integer `page`, unknown `lifecycle_phase` value) |
```

**After**:
```
Global responses per `api-spec.md` apply (422, 500 only — public endpoint).
```

(Table removed — all conditions are Pydantic-level.)

---

### 5.11 `docs/features/packages/ibs-submission-tracking.md`

#### 5.11.1 List Submission Requests error table (lines 928-933)

**Before**:
```
**Error responses**:

| Status | Code | Condition                                              |
|--------|------|--------------------------------------------------------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found                                       |
| 422    | `VALIDATION_ERROR` | Invalid `state` value                                  |
```

**After**:
```
Global responses per `api-spec.md` apply (422, 500 only — public endpoint). Scoped: `TICKET_NOT_FOUND`.
```

(Table removed — both rows are global/scoped.)

#### 5.11.2 List Release Requests error table (lines 987-992)

**Before**:
```
**Error responses**:

| Status | Code | Condition                                              |
|--------|------|--------------------------------------------------------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found                                       |
| 422    | `VALIDATION_ERROR` | Invalid `state` or `incident_number` value             |
```

**After**:
```
Global responses per `api-spec.md` apply (422, 500 only — public endpoint). Scoped: `TICKET_NOT_FOUND`.
```

---

### 5.12 `docs/features/identity/user-management.md`

#### 5.12.1 List Users error table (lines 547-551)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | `search` parameter shorter than 2 characters |
```

**After**:
```
Global responses per `api-spec.md` apply (422, 500 only — public endpoint).
```

(Table removed — min_length is Pydantic Query validation.)

---

### 5.13 `docs/features/identity/ad-integration.md`

#### 5.13.1 Verify Role Mapping error table (lines 730-736)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | Invalid request body (missing or empty `ad_group_cn`, unrecognized `role`) |
| 422 | `ROLE_MAPPING_INVALID_GROUP_CN` | `ad_group_cn` contains characters invalid for an AD group CN |
| 503 | `AD_UNAVAILABLE` | AD is unreachable or the connection timed out (10–15 s timeout) |
```

**After**:
```
Global responses per `api-spec.md` apply.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `ROLE_MAPPING_INVALID_GROUP_CN` | `ad_group_cn` contains characters invalid for an AD group CN |
| 503 | `AD_UNAVAILABLE` | AD is unreachable or the connection timed out (10–15 s timeout) |
```

---

### 5.14 `docs/features/platform/fetcher-operations.md`

#### 5.14.1 Update Fetcher Config error table (lines 678-686)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists (not in the registry and no `FetcherConfig` record in the database) |
| 409 | `FETCHER_DEREGISTERED` | Fetcher exists in DB but is not present in the registry (code removed). Cannot be configured. |
| 422 | `FETCHER_SETTING_UNKNOWN` | Unknown key in `custom_settings` (not declared in the fetcher's schema) |
| 422 | `FETCHER_SETTING_INVALID` | Value in `custom_settings` fails type, range, or choices validation |
| 422 | `VALIDATION_ERROR` | Invalid cron expression, run_timeout, or request_delay value |
```

**After**:
```
Global responses per `api-spec.md` apply.

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists |
| 409 | `FETCHER_DEREGISTERED` | Fetcher exists in DB but is not present in the registry (code removed) |
| 422 | `FETCHER_SETTING_UNKNOWN` | Unknown key in `custom_settings` (not declared in the fetcher's schema) |
| 422 | `FETCHER_SETTING_INVALID` | Value in `custom_settings` fails type, range, or choices validation |
```

---

### 5.15 `docs/features/tickets/cve-tracking.md`

#### 5.15.1 Re-fetch CVE Data error table (lines 391-397)

The `CVE_NOT_FOUND` row is a scoped response and the `CVE_INVALID_SOURCE`
is an endpoint-specific domain error. Keep only the domain error:

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `CVE_NOT_FOUND` | CVE does not exist, or is associated with a confidential ticket and caller lacks visibility |
| 422 | `CVE_INVALID_SOURCE` | The `source` value is not a registered CVE source with `supports_fetch_single = True` |
| 503 | `CELERY_ENQUEUE_FAILED` | All source dispatches failed — task broker unreachable. No fetch processing will occur |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `CVE_NOT_FOUND`.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `CVE_INVALID_SOURCE` | The `source` value is not a registered CVE source with `supports_fetch_single = True` |
| 503 | `CELERY_ENQUEUE_FAILED` | All source dispatches failed — task broker unreachable |
```

---

### 5.16 `docs/features/tickets/ticket-audit-log.md`

#### 5.16.1 List Ticket Events error table (lines 200-204)

**Before**:
```
**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found |
```

**After**:
```
Global responses per `api-spec.md` apply. Scoped: `TICKET_NOT_FOUND`.
```

(Table removed — `TICKET_NOT_FOUND` is a scoped response from
`require_accessible_ticket`.)

---

### 5.17 `docs/features/tickets/cve-service.md`

#### 5.17.1 CVE Source Status error responses (lines 1179-1185)

**Before**:
```
### Error responses

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `CVE_NOT_FOUND` | CVE does not exist or is not accessible (scoped `require_accessible_cve` dependency) |

Global responses (422, 500) apply per `docs/api-spec.md`.
```

**After**:
```
Global responses per `api-spec.md` apply (422, 500 only — public endpoint). Scoped: `CVE_NOT_FOUND`.
```

(Table removed — `CVE_NOT_FOUND` is a scoped response. The existing
note about global responses is replaced by the standard reference line.)

---

## 6. Header Normalization Inventory (DD8)

### 6.1 `docs/features/identity/authentication.md`

| Line | Before | After |
|------|--------|-------|
| 602 | `` ### `GET /api/v1/users/me` `` | `### Get Current User` |
| 623 | `` ### `POST /api/v1/auth/logout` `` | `### Logout` |
| 662 | `` ### `GET /api/v1/api-keys` `` | `### List My API Keys` |
| 693 | `` ### `POST /api/v1/api-keys` `` | `### Create API Key` |
| 766 | `` ### `POST /api/v1/api-keys/{key_id}/revoke` `` | `### Revoke My API Key` |
| 809 | `` ### `GET /api/v1/admin/api-keys` `` | `### List All API Keys (Admin)` |
| 852 | `` ### `POST /api/v1/admin/api-keys/{key_id}/revoke` `` | `### Revoke API Key (Admin)` |

### 6.2 `docs/features/identity/local-authentication.md`

| Line | Before | After |
|------|--------|-------|
| 31 | `` ### `POST /api/v1/auth/login` `` | `### Login` |
| 205 | `` ### `POST /api/v1/admin/users/{user}/password` `` | `### Admin Password Reset` |

### 6.3 `docs/features/identity/sso-authentication.md`

| Line | Before | After |
|------|--------|-------|
| 138 | `` #### `GET /api/v1/auth/sso/authorize` `` | `#### SSO Authorize` |
| 201 | `` #### `POST /api/v1/auth/sso/callback` `` | `#### SSO Callback` |
| 443 | `` #### `GET /api/v1/auth/providers` `` | `#### List Auth Providers` |

### 6.4 `docs/features/identity/user-management.md`

| Line | Before | After |
|------|--------|-------|
| 525 | `` #### `GET /api/v1/users` `` | `#### List Users` |
| 553 | `` #### `GET /api/v1/users/{user}` `` | `#### Get User` |
| 612 | `` #### `PATCH /api/v1/admin/users/{user}` `` | `#### Update User (Admin)` |
| 653 | `` #### `POST /api/v1/admin/users/{user}/roles` `` | `#### Set User Roles` |
| 702 | `` #### `POST /api/v1/admin/users/{user}/password` `` | `#### Reset User Password` |
| 745 | `` #### `POST /api/v1/admin/users/{user}/deactivate` `` | `#### Deactivate User` |
| 780 | `` #### `POST /api/v1/admin/users/{user}/reactivate` `` | `#### Reactivate User` |
| 806 | `` #### `GET /api/v1/admin/users/{user}/deactivation-impact` `` | `#### Get Deactivation Impact` |
| 883 | `` #### `POST /api/v1/admin/users/{user}/unlock` `` | `#### Unlock User` |

### 6.5 `docs/features/packages/ibs-submission-tracking.md`

| Line | Before | After |
|------|--------|-------|
| 875 | `` ### `GET /api/v1/tickets/{ticket_id}/submission-requests` `` | `### List Submission Requests` |
| 935 | `` ### `GET /api/v1/tickets/{ticket_id}/release-requests` `` | `### List Release Requests` |

### 6.6 `docs/features/tickets/cve-tracking.md`

| Line | Before | After |
|------|--------|-------|
| 342 | `` ### `POST /api/v1/cves/{cve_id}/refetch` `` | `### Re-fetch CVE Data` |

### 6.7 `docs/features/packages/maintainer.md`

| Line | Before | After |
|------|--------|-------|
| 118 | `### GET /api/v1/my/packages/pending` | `### Pending Packages` |
| 159 | `### GET /api/v1/my/packages/in-progress` | `### In-Progress Packages` |
| 207 | `### GET /api/v1/my/packages/completed` | `### Completed Packages` |
| 253 | `### GET /api/v1/my/packages/ticket/{ticket_id}` | `### Package Details for Ticket` |

### 6.8 `docs/features/tickets/cve-service.md`

| Line | Before | After |
|------|--------|-------|
| 1004 | `` ## CVE Source Status: `GET /api/v1/cves/{cve_id}/sources` `` | `## CVE Source Status` |
| 1200 | `` ## Global CVE Source Listing: `GET /api/v1/cve-sources` `` | `## Global CVE Source Listing` |

### 6.9 Anchor Updates in `docs/features/identity/rbac.md`

All affected rows in the Endpoint Permission Map:

| Line | Before (Owning Spec link) | After |
|------|---------------------------|-------|
| 348 | `[local-authentication](local-authentication.md#post-apiv1authlogin)` | `[local-authentication](local-authentication.md#login)` |
| 349 | `[sso-authentication](sso-authentication.md#get-apiv1authssoauthorize)` | `[sso-authentication](sso-authentication.md#sso-authorize)` |
| 350 | `[sso-authentication](sso-authentication.md#post-apiv1authssocallback)` | `[sso-authentication](sso-authentication.md#sso-callback)` |
| 351 | `[sso-authentication](sso-authentication.md#get-apiv1authproviders)` | `[sso-authentication](sso-authentication.md#list-auth-providers)` |
| 352 | `[authentication](authentication.md#post-apiv1authlogout)` | `[authentication](authentication.md#logout)` |
| 358 | `[authentication](authentication.md#get-apiv1usersme)` | `[authentication](authentication.md#get-current-user)` |
| 360 | `[user-management](user-management.md#get-apiv1users)` | `[user-management](user-management.md#list-users)` |
| 361 | `[user-management](user-management.md#get-apiv1usersuser)` | `[user-management](user-management.md#get-user)` |
| 367 | `[authentication](authentication.md#get-apiv1api-keys)` | `[authentication](authentication.md#list-my-api-keys)` |
| 368 | `[authentication](authentication.md#post-apiv1api-keys)` | `[authentication](authentication.md#create-api-key)` |
| 369 | `[authentication](authentication.md#post-apiv1api-keyskey_idrevoke)` | `[authentication](authentication.md#revoke-my-api-key)` |
| 430 | `[cve-tracking](../tickets/cve-tracking.md#post-apiv1cvescve_idrefetch)` | `[cve-tracking](../tickets/cve-tracking.md#re-fetch-cve-data)` |
| 427 | `[cve-service](../tickets/cve-service.md#cve-source-status-get-apiv1cvescve_idsources)` | `[cve-service](../tickets/cve-service.md#cve-source-status)` |
| 431 | `[cve-service](../tickets/cve-service.md#global-cve-source-listing-get-apiv1cve-sources)` | `[cve-service](../tickets/cve-service.md#global-cve-source-listing)` |
| 443 | `[ibs-submission-tracking](../packages/ibs-submission-tracking.md#get-apiv1ticketsticket_idsubmission-requests)` | `[ibs-submission-tracking](../packages/ibs-submission-tracking.md#list-submission-requests)` |
| 444 | `[ibs-submission-tracking](../packages/ibs-submission-tracking.md#get-apiv1ticketsticket_idrelease-requests)` | `[ibs-submission-tracking](../packages/ibs-submission-tracking.md#list-release-requests)` |
| 464 | `[maintainer](../packages/maintainer.md#get-apiv1mypackagespending)` | `[maintainer](../packages/maintainer.md#pending-packages)` |
| 465 | `[maintainer](../packages/maintainer.md#get-apiv1mypackagesin-progress)` | `[maintainer](../packages/maintainer.md#in-progress-packages)` |
| 466 | `[maintainer](../packages/maintainer.md#get-apiv1mypackagescompleted)` | `[maintainer](../packages/maintainer.md#completed-packages)` |
| 467 | `[maintainer](../packages/maintainer.md#get-apiv1mypackagesticketticket_id)` | `[maintainer](../packages/maintainer.md#package-details-for-ticket)` |
| 478 | `[authentication](authentication.md#get-apiv1adminapi-keys)` | `[authentication](authentication.md#list-all-api-keys-admin)` |
| 479 | `[authentication](authentication.md#post-apiv1adminapi-keyskey_idrevoke)` | `[authentication](authentication.md#revoke-api-key-admin)` |
| 480 | `[user-management](user-management.md#patch-apiv1adminusersuser)` | `[user-management](user-management.md#update-user-admin)` |
| 481 | `[user-management](user-management.md#post-apiv1adminusersuserroles)` | `[user-management](user-management.md#set-user-roles)` |
| 482 | `[user-management](user-management.md#post-apiv1adminusersuserpassword)` | `[user-management](user-management.md#reset-user-password)` |
| 483 | `[user-management](user-management.md#post-apiv1adminusersuserdeactivate)` | `[user-management](user-management.md#deactivate-user)` |
| 484 | `[user-management](user-management.md#post-apiv1adminusersuserreactivate)` | `[user-management](user-management.md#reactivate-user)` |
| 485 | `[user-management](user-management.md#get-apiv1adminusersuserdeactivation-impact)` | `[user-management](user-management.md#get-deactivation-impact)` |
| 486 | `[user-management](user-management.md#post-apiv1adminusersuserunlock)` | `[user-management](user-management.md#unlock-user)` |

---

## 7. Ordered Action Plan

Execute in this sequence. Each step depends on the previous one.

### Step 1 — Update `api-spec.md` (source of truth)

Apply changes 5.1.1, 5.1.2, and 5.1.3. This establishes the canonical
rule and fixes stale cross-references before any feature spec is
modified.

### Step 2 — Update `conventions.md` (references only)

Apply changes 5.2.1 and 5.2.2. Now both cross-cutting documents are
internally consistent.

### Step 3 — Clean error tables in feature specs

Apply changes in sections 5.3 through 5.17 (all feature specs). Order
within this step does not matter — each change is independent.

### Step 4 — Fix incidentals in `authentication.md`

Apply changes 5.6.2 through 5.6.7 (per_page, auth declarations,
cross-reference).

### Step 5 — Fix `cvss-scoring.md` absent-case example

Apply change 5.8.3.

### Step 6 — Normalize endpoint headers (DD8)

Apply all header renames from section 6 (6.1 through 6.8). This changes
the actual markdown headings in the 8 affected specs.

### Step 7 — Update anchors in `rbac.md`

Apply all anchor updates from section 6.9. This MUST happen in the same
atomic operation as Step 6 (or immediately after) to avoid broken links.

### Step 8 — Update `@api-convention-reviewer` agent

Update `.opencode/agents/api-convention-reviewer.md` to add a check
under "Error handling":

```markdown
- Endpoint error tables MUST NOT include global responses (generic 401,
  403, 422, 500) or scoped responses already covered by the reference
  line. See `api-spec.md` "What belongs in an endpoint error table" for
  the exact rule
- Each endpoint section should include a reference line indicating which
  global and scoped responses apply
```

### Step 9 — Run reviewers on affected specs

Execute the following reviewers to verify correctness:

| Reviewer | Target | Rationale |
|----------|--------|-----------|
| `@api-convention-reviewer` | Each spec with modified error tables (one session per spec) | Verify error tables comply with the new rule |
| `@spec-coherence-reviewer` | `api-spec.md` | Verify no contradictions with feature specs |
| `@spec-coherence-reviewer` | `conventions.md` | Verify no contradictions with api-spec.md |
| `@docs-placement-reviewer` | `api-spec.md` | Verify the new rule is correctly placed |

Specs to review with `@api-convention-reviewer`:
- `package-model.md`
- `system-settings.md`
- `identity-audit-log.md`
- `authentication.md`
- `tickets.md`
- `cvss-scoring.md`
- `ticket-references.md`
- `product-catalog.md`
- `ibs-submission-tracking.md`
- `user-management.md`
- `ad-integration.md`
- `fetcher-operations.md`
- `cve-tracking.md`
- `ticket-audit-log.md`
- `cve-service.md`

### Step 10 — Delete this draft

Once all changes are applied and reviewers pass, delete
`docs/drafts/endpoint-error-table-conventions.md`.

---

## 8. Internal Coherence Checklist

- [ ] DD1 (information content discriminant) is consistently applied
  across all 50+ modifications in section 5
- [ ] DD2 (single-owner) is satisfied: conventions.md never re-states the
  rule, only references api-spec.md
- [ ] DD3 (reference lines) uses the correct variant (A/B/C/D/E) for
  each endpoint based on its access level and resource scope
- [ ] DD4 (conditional auth in prose) is applied to `package-model.md`
  line 1557 (the only case)
- [ ] DD5 (Pydantic rows removed) correctly identifies all 13 rows as
  Pydantic-level; no domain-specific validation is accidentally removed
- [ ] DD6 (scoped responses) correctly identifies which endpoints are
  exempt from `TICKET_NOT_MUTABLE` (reopen, revert-duplicate — they are
  the manual-zone exit operations)
- [ ] DD6 (exception) — tickets.md "Mark as Duplicate" retains
  `TICKET_NOT_FOUND` row with reworded condition for target ticket
  (dual-resolution semantics not covered by scoped dependency)
- [ ] DD6 (corollary) — cvss-scoring.md `TICKET_NOT_MUTABLE` removed
  from table because conditionality is already in prose (line 598-599);
  api-spec.md:406-410 updated to be self-contained (5.1.3)
- [ ] DD7 (per_page) change is limited to the one non-conformant endpoint
- [ ] DD8 (headers) — every renamed header has a corresponding anchor
  update in rbac.md section 6.9
- [ ] No spec loses information: all endpoint-specific error codes remain
  in their tables; only generic/scoped rows are removed
- [ ] ticket-references.md 5.9.2: existing reference line at line 741 is
  updated to canonical format (not duplicated)
- [ ] Cross-references: authentication.md gains api-spec.md in its
  cross-references
- [ ] Error code registry: AUTH_LOGOUT_NOT_APPLICABLE is added to
  api-spec.md
