# Package Service — Module Extraction and Read Endpoints

**Status**: Draft — all open points resolved, ready for spec creation
**Created**: 2026-05-21
**Last updated**: 2026-05-21
**Scope**: Extract package-centric mutation functions from `ticket_mutations`
into a new `package_service` module, and add read endpoints for package data.

---

## Motivation

The `ticket_mutations` module (specified in
`docs/features/tickets/ticket-mutations.md`) currently hosts 20 functions.
Analysis reveals that 11 of these are **package-centric** — their primary
concern is mutating `TicketPackage`, `TicketPackageTrack`, or
`TicketPackageProduct` records. The remaining 9 are **ticket-centric** —
they operate on the ticket entity itself (status evaluation, CVSS
assessments, severity, duplicate handling, manual-zone exits).

The package-centric functions are called exclusively by package-related
endpoints, background tasks, and consumers. No ticket-centric endpoint
calls any of them. The separation is clean at the domain level.

Additionally, there is currently **no way to query package data
independently** — the only access path is the `packages` field embedded
in `TicketDetail` (returned by `GET /api/v1/tickets/{ticket_id}`). This
prevents cross-ticket queries ("which tickets affect openssl?") and
forces clients to download full ticket metadata just to access package
information.

This draft proposes:

1. **Extracting** the 11 package-centric mutation functions into a new
   `package_service` module
2. **Adding** two read endpoints for package data (per-ticket and
   cross-ticket)
3. **Adding** the corresponding query functions to `package_service`
4. **Updating** all affected specifications, guardrails, and tooling

---

## Analysis

### Current Function Classification

| # | Function | Domain | Called by |
|---|----------|--------|----------|
| 1 | `evaluate_ticket_status()` | Ticket | Internal — called by all public mutation functions |
| 2 | `_reenter_gate_zone()` | Ticket | Internal — called by manual-zone exit functions |
| 3 | `set_track_status()` | **Package** | API (Change Track Status), IBS release detection, RabbitMQ consumer |
| 4 | `set_track_delivery_status()` | **Package** | IBS submission tracking, RequestSyncFetcher |
| 5 | `set_product_status()` | **Package** | API (Override Product Status), IBS product release detection |
| 6 | `set_product_eligibility()` | **Package** | Product lifecycle transitions (AIMAAS threshold changes) |
| 7 | `create_cvss_assessment()` | Ticket | CVSS sync fetcher, admin CVSS version change |
| 8 | `update_cvss_assessment()` | Ticket | CVSS sync fetcher, admin CVSS version change |
| 9 | `delete_cvss_assessment()` | Ticket | CVSS sync fetcher, admin CVSS version change |
| 10 | `set_severity_override()` | Ticket | API (Set Severity Override) |
| 11 | `add_package_records()` | **Package** | `add_package_to_ticket` (after SMELT resolution) |
| 12 | `soft_delete_ticket_package()` | **Package** | API (Soft-Delete Package) |
| 13 | `soft_delete_ticket_package_track()` | **Package** | API (Soft-Delete Track) |
| 14 | `soft_delete_ticket_package_product()` | **Package** | API (Soft-Delete Product), product lifecycle transitions |
| 15 | `restore_ticket_package()` | **Package** | API (Restore Package) |
| 16 | `restore_ticket_package_track()` | **Package** | API (Restore Track) |
| 17 | `restore_ticket_package_product()` | **Package** | API (Restore Product) |
| 18 | `reopen_from_ignored()` | Ticket | API (Reopen from Ignored), NVD rejection handling |
| 19 | `revert_duplicate()` | Ticket | API (Revert Duplicate) |
| 20 | `resolve_canonical_target()` | Ticket | API serialization, background tasks |

**Result**: 11 package-centric, 9 ticket-centric, 0 mixed.

### Dependency Between Modules

After extraction, `package_service` depends on `ticket_mutations` for one
critical function: `evaluate_ticket_status()`. Every package mutation must
call it after applying changes. The dependency is unidirectional:

```
package_service ──depends on──> ticket_mutations.evaluate_ticket_status()
```

`ticket_mutations` does NOT depend on `package_service`. The two modules
are cleanly separable with a single-direction dependency.

### Shared Infrastructure

Both modules share the same transactional patterns (defined in
`docs/conventions.md`, Transaction and Locking):

- **Pessimistic locking**: `SELECT ... FOR UPDATE` on the Ticket row as
  the first operation
- **Transaction ownership**: the module does not commit — the caller owns
  the transaction
- **Acting user convention**: `acting_user_id: UUID | None` parameter on
  all public functions
- **Auto-assignment rule**: unassigned tickets are assigned to the acting
  VA on any mutation

These patterns are documented in `docs/conventions.md` and
`ticket-mutations.md`. They apply identically to both modules.

### Module Invariant: I/O-then-Lock Pattern

`package_service` contains both orchestration functions that perform
external I/O (e.g., `add_package_to_ticket` queries SMELT) and mutation
functions that acquire `FOR UPDATE` locks (e.g., `add_package_records`).
The following invariant MUST be maintained:

> Functions that perform external I/O MUST NOT acquire `FOR UPDATE` locks
> themselves. External I/O happens in orchestration functions, which
> delegate record mutations to lock-acquiring functions. The lock is
> acquired only after all external data has been fetched.

This is a specialization of the Transaction Hygiene Rules
(`docs/conventions.md`), applied to the module's internal architecture.
Violation of this invariant would block concurrent mutations on the same
ticket for the duration of an external HTTP call.

### Reference Inventory

88 references to `ticket_mutations` exist across 16 files. By category:

| Category | Count |
|----------|-------|
| Package-centric references | 34 |
| Ticket-centric references | 33 |
| Both / General references | 21 |

Files with the highest impact (by reference count):

| File | References | Primary updates needed |
|------|-----------|----------------------|
| `docs/features/tickets/ticket-mutations.md` | 21 | Remove package functions, update purpose, contract, callers |
| `docs/features/packages/package-model.md` | 16 | Change `ticket_mutations` → `package_service` for package operations |
| `.opencode/agents/ticket-integrity-reviewer.md` | 16 | Update to reference both modules |
| `docs/features/tickets/cvss-scoring.md` | 8 | No change (all ticket-centric references) |
| `docs/features/tickets/tickets.md` | 6 | Minor cross-reference updates |
| `docs/features/packages/ibs-track-release-detection.md` | 5 | Change to `package_service` |
| `docs/features/packages/product-lifecycle-transitions.md` | 5 | Change to `package_service` |
| `AGENTS.md` | 2 | Update Guardrail 16 |
| `docs/conventions.md` | 1 | Update example reference |
| `docs/data-model.md` | 1 | No change (ticket-centric reference) |
| `docs/features/tickets/cve-tracking.md` | 1 | No change (ticket-centric) |
| `docs/features/tickets/ticket-audit-log.md` | 1 | No change (ticket-centric) |
| `docs/features/packages/ibs-product-release-detection.md` | 1 | Change to `package_service` |
| `docs/features/packages/ibs-submission-tracking.md` | 2 | Clarification update |
| `docs/features/integrations/ibs-rabbitmq-integration.md` | 1 | Change to `package_service` |
| `docs/features/platform/admin.md` | 1 | No change (ticket-centric) |

---

## New Elements

### 1. New Specification: `docs/features/packages/package-service.md`

A dedicated spec for the `package_service` module, following the same
pattern as `docs/features/identity/user-service.md`. Defines:

- Module purpose and location (`backend/app/services/package_service.py`)
- Async pattern (same as `ticket_mutations` — async functions, sync
  callers use `asyncio.run()`)
- Transaction ownership (same — module does not commit)
- Relationship with `ticket_mutations` (depends on
  `evaluate_ticket_status()`)
- All 11 mutation function signatures (moved from `ticket-mutations.md`,
  with `ticket_mutations` references updated to `package_service`)
- 2 new query function signatures (see below)
- Orphan cleanup invariants (moved from `ticket-mutations.md` — these
  are package-domain invariants)
- Record creation logic (moved from `ticket-mutations.md` — package
  record initial status rules)
- Auto-assignment rule reference (shared with `ticket_mutations`)
- Service exceptions (package-related exceptions)
- Callers table (package-related callers only)

### 2. New Read Endpoint: `GET /api/v1/tickets/{ticket_id}/packages`

Defined in `docs/features/packages/package-model.md` (alongside the
existing mutation endpoints).

| Aspect | Design |
|--------|--------|
| **Access** | Public (consistent with `GET /api/v1/tickets/{ticket_id}`) |
| **Guard** | `require_accessible_ticket` (404/410 for missing/confidential/soft-deleted tickets) |
| **Pagination** | No — package count per ticket is bounded (typically 1-5, rarely >20). Justified omission per `api-spec.md` |
| **Envelope** | `{"data": [...]}` (unpaginated list) |
| **Soft-deleted records** | All package/track/product records are returned (including soft-deleted), with `deleted_at` visible on each — identical to `TicketDetail.packages` behavior. **Note**: this differs from `GET /api/v1/packages` which always excludes soft-deleted packages — see D9 for rationale |
| **Response schema** | `PackageDetail[]` — reuses the existing schema defined in `tickets.md` (full tree: package → tracks → products) |
| **Sorting** | Fixed alphabetical order by `package_name`. Client-controlled sorting (`sort_by`/`sort_order`) is not supported — the dataset is small and alphabetical order is the natural presentation for package lists |
| **Delegation** | Delegates to `package_service.get_ticket_packages()` |

#### Service Function: `get_ticket_packages()`

```python
async def get_ticket_packages(
    db: AsyncSession,
    ticket_id: UUID,
) -> list[PackageDetail]:
```

Returns the complete package tree for a ticket, including soft-deleted
records (with `deleted_at` visible on each level). The same function is
called by `GET /api/v1/tickets/{ticket_id}` to populate the `packages`
field in `TicketDetail`.

**Behavior**:

1. Query all `TicketPackage` records for the ticket (including
   soft-deleted)
2. For each package, load all tracks and products (including
   soft-deleted), with `deleted_at` visible
3. Compute `delivery_relevant` for each track
4. Join bugowner data from `PackageBugowner`
5. Return assembled `PackageDetail[]`, sorted alphabetically by
   `package_name`

**No locking needed** — this is a read-only operation.
**No filtering** — the caller (endpoint handler) is responsible for
access control via `require_accessible_ticket` before invoking this
function.

#### Endpoint Errors

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `TICKET_NOT_FOUND` | Ticket does not exist |
| 410 | `TICKET_DELETED` | Ticket is soft-deleted and caller is not Admin |

(Global errors from `api-spec.md` apply but are not repeated.)

### 3. New Read Endpoint: `GET /api/v1/packages`

Defined in `docs/features/packages/package-model.md`.

| Aspect | Design |
|--------|--------|
| **Access** | Public (consistent with `GET /api/v1/tickets`) |
| **Confidentiality** | Packages belonging to confidential tickets are excluded for unauthorized callers (same filter as `GET /api/v1/tickets`) |
| **Soft-deleted packages** | Always excluded — soft-deleted `TicketPackage` records (those with `deleted_at IS NOT NULL`) are never returned in cross-ticket results. They are an operational exclusion by the VA, not relevant for cross-ticket searches. **Note**: this differs from `GET /api/v1/tickets/{ticket_id}/packages` which returns all records including soft-deleted — see D9 for rationale |
| **Soft-deleted tickets** | Controlled by `include_deleted` parameter (Admin-only), consistent with `GET /api/v1/tickets` |
| **Pagination** | Yes — `page` (default 1), `per_page` (default 20, max 100) |
| **Envelope** | `{"data": [...], "meta": {"total": N, "page": P, "per_page": PP}}` |
| **Delegation** | Delegates to `package_service.search_packages()` |

#### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `search` | string | Partial match on `package_name` (case-insensitive). Max 500 chars |
| `name` | string | Exact match on `package_name`. Max 500 chars |
| `ticket_status` | string (repeatable) | Ticket statuses to include: `new`, `analysis`, `analyzed`, `resolved`, `ignored`, `duplicated`. Repeatable — multiple values are specified as separate query parameters (e.g., `?ticket_status=new&ticket_status=analysis`). Invalid values are silently ignored per `api-spec.md` (Enum Filter Validation). If all values are invalid, an empty result set is returned. Default: no filter (all statuses) |
| `include_deleted` | string | Controls visibility of packages belonging to **soft-deleted tickets**. `true` (include packages from active and deleted tickets), `only` (return only packages from deleted tickets). Any other value (including `false`) is treated as absent. Accepted from any caller, but effective only for Admins — silently ignored for non-admin callers. Default (absent or unrecognized value): return only packages from active tickets |
| `sort_by` | string | `package_name` or `created_at` (default: `created_at`). Refers to `TicketPackage.created_at` (the date the package was added to the ticket), not `Ticket.created_at`. Secondary sort: `id` (deterministic pagination when primary key has duplicates) |
| `sort_order` | string | `asc` or `desc` (default: `desc`) |
| `page` | integer | Page number (default: 1, min: 1) |
| `per_page` | integer | Items per page (default: 20, min: 1, max: 100) |

`search` and `name` are mutually exclusive. If both are provided,
return 422 `VALIDATION_ERROR`.

Pagination constraints: `page < 1` or `per_page < 1` or `per_page > 100`
return 422 `VALIDATION_ERROR`. If `page` exceeds the total number of
pages, an empty `data` array is returned with the correct `total` in
`meta` — this is not an error.

**Naming note**: the parameter is named `ticket_status` (not `status`)
to disambiguate from package-level statuses visible in `track_summary`.
On `GET /api/v1/tickets`, `status` is unambiguous because the resource
itself is a ticket.

#### Response Schema: `PackageListItem`

Each result represents a single `TicketPackage` record — i.e., one
`(package_name, ticket)` pair. If the same source package is tracked in
multiple tickets, it appears once per ticket in the results.

```json
{
  "id": "uuid",
  "package_name": "openssl-3",
  "ticket": {
    "id": "uuid",
    "identifier": "SNTL-123",
    "status": "analysis",
    "severity": "high",
    "deleted_at": null
  },
  "track_summary": {
    "total": 5,
    "affected": 2,
    "fixed": 1,
    "not_affected": 1,
    "wont_fix": 0,
    "analysis": 1
  },
  "created_at": "2026-05-15T10:30:00Z",
  "updated_at": "2026-05-16T08:00:00Z"
}
```

**`ticket`** — lightweight ticket reference. Fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Ticket ID |
| `identifier` | string | Human-readable identifier (e.g., `SNTL-123`) |
| `status` | string | Current ticket status |
| `severity` | string \| null | Ticket severity |
| `deleted_at` | datetime \| null | Soft-deletion timestamp. Always present in the schema; `null` for active tickets or when the caller is not Admin |

**`track_summary`** — aggregated track status counts for the package
within this ticket. Counts only active tracks (`deleted_at IS NULL`).
Since soft-deleted packages are excluded from this endpoint entirely,
there is no ambiguity about hierarchical exclusion — the parent package
is always active.

| Field | Type | Description |
|-------|------|-------------|
| `total` | integer | Total active tracks |
| `affected` | integer | Tracks with status `AFFECTED` |
| `fixed` | integer | Tracks with status `FIXED` |
| `not_affected` | integer | Tracks with status `NOT_AFFECTED` |
| `wont_fix` | integer | Tracks with status `WONT_FIX` |
| `analysis` | integer | Tracks with status `ANALYSIS` |

#### Endpoint Errors

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | Both `search` and `name` provided; `per_page` > 100 |

(Global errors from `api-spec.md` apply but are not repeated.)

#### Service Function: `search_packages()`

```python
async def search_packages(
    db: AsyncSession,
    confidentiality_filter: ColumnElement,  # from confidential_ticket_filter()
    search: str | None = None,
    name: str | None = None,
    ticket_status: list[TicketStatus] | None = None,
    include_deleted: str | None = None,  # soft-deleted *tickets* visibility
    caller_is_admin: bool = False,       # for include_deleted enforcement
    sort_by: Literal["package_name", "created_at"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    page: int = 1,
    per_page: int = 20,
) -> PaginatedResult[PackageListItem]:
```

**Behavior**:

1. Build base query joining `TicketPackage` → `Ticket`
2. Exclude soft-deleted packages: filter `TicketPackage.deleted_at IS NULL`
3. Apply soft-deleted ticket filter: exclude packages belonging to
   soft-deleted tickets (`Ticket.deleted_at IS NOT NULL`) unless
   `include_deleted` is set and caller is Admin
4. Apply `confidentiality_filter` (pre-built by the endpoint handler
   via `confidential_ticket_filter()` — see D11)
5. Apply `ticket_status` filter (if provided; invalid values silently
   ignored)
6. Apply `search` (ILIKE `%term%` substring match on `package_name`) or
   `name` (exact match)
7. Apply sorting (primary: `sort_by`/`sort_order`; secondary: `id` for
   deterministic pagination)
8. Execute paginated query
9. Compute `track_summary` via SQL aggregation (`COUNT(*) FILTER (WHERE
   status = ...)`) in the same query — NOT as Python post-processing —
   to avoid N+1 query patterns. Counts only tracks with
   `deleted_at IS NULL` (active tracks)
10. Return paginated `PackageListItem[]`

**No locking needed** — this is a read-only operation.

---

## Migration Plan

### Phase 1: Create `package-service.md` spec

Create `docs/features/packages/package-service.md` with:

- Module purpose, location, architecture
- All 11 mutation function definitions (moved from `ticket-mutations.md`)
- `add_package_to_ticket` orchestration function (moved from
  `package-model.md` — now owned by `package_service`)
- 2 new query function definitions (`get_ticket_packages`,
  `search_packages`)
- Orphan cleanup invariants (moved from `ticket-mutations.md`)
- Record creation logic (moved from `ticket-mutations.md`)
- Service exceptions (package-related subset)
- Callers table (package-related callers only)
- Dependency on `ticket_mutations.evaluate_ticket_status()` (direct
  import)
- Cross-references

### Phase 2: Update `ticket-mutations.md`

Remove from the spec:

- All 11 package-centric function definitions
- Orphan cleanup invariants section (moved to `package-service.md`)
- Record creation logic section (moved to `package-service.md`)
- Package-related entries from the callers table
- Package-related service exceptions

Update in the spec:

- Purpose statement: clarify scope is now ticket-centric mutations and
  CVSS assessment management only
- Contract section: replace with the following text:

  > Every service-layer operation that modifies data relevant to ticket
  > status gates MUST go through the appropriate centralized module:
  >
  > - **Package/track/product mutations**: `package_service`
  >   (`TicketPackageTrack`, `TicketPackageProduct` status, delivery
  >   status, eligibility, soft-delete/restore, record creation)
  > - **CVSS and severity mutations**: `ticket_mutations`
  >   (`CVECVSSAssessment` records, severity override)
  > - **Ticket status evaluation**: `ticket_mutations` (called by both
  >   modules after any gate-relevant mutation)
  >
  > Direct modification of gate-relevant records outside the owning
  > module is a bug.

- Architecture section: add relationship entry for `package_service`
- Architectural test requirement: scope to ticket-centric mutations

Add to the spec:

- Cross-reference to `package-service.md`
- `auto_assign_if_needed()` helper function definition (public, called
  by all modules that modify tickets under lock)
- Update Auto-Assignment Rule section to reference the helper function
  instead of inline enforcement per-function

### Phase 3: Update `package-model.md`

- Change all `ticket_mutations` references to `package_service` (16
  references)
- Add `GET /api/v1/tickets/{ticket_id}/packages` endpoint definition
- Add `GET /api/v1/packages` endpoint definition — specify that the
  endpoint handler constructs `confidential_ticket_filter()` and passes
  it to `search_packages(confidentiality_filter=...)` (see D11)
- Add `PackageListItem` schema definition
- Add `TicketPackageRef` sub-schema definition
- Add `TrackSummary` sub-schema definition
- Update Security section to document read endpoint access rules
- Add cross-reference to `package-service.md`

### Phase 4: Update `tickets.md`

- Document that `GET /api/v1/tickets/{ticket_id}` populates the
  `packages` field via `package_service.get_ticket_packages()`
- Update "Auto-Assignment on Unassigned Tickets" section to reference
  the `auto_assign_if_needed()` helper in `ticket_mutations` (instead
  of stating the rule is enforced "within each public function")
- Add `confidential_ticket_filter()` specification to the
  Confidentiality Filtering section:
  - Function signature, location (`backend/app/core/filters.py`)
  - Behavior (the OR logic for all 4 authorization rules)
  - Consumer table (ticket list, package search, maintainer operations,
    `require_accessible_ticket`)
  - Note that `GET /api/v1/tickets` uses the same utility (its existing
    filtering logic is formalized as this shared function)
- Add cross-reference to `package-service.md`

### Phase 5: Update other referencing specs

| File | Change |
|------|--------|
| `docs/features/packages/ibs-track-release-detection.md` | `ticket_mutations` → `package_service` (5 refs) |
| `docs/features/packages/product-lifecycle-transitions.md` | `ticket_mutations` → `package_service` (5 refs) |
| `docs/features/packages/ibs-product-release-detection.md` | `ticket_mutations` → `package_service` (1 ref) |
| `docs/features/packages/ibs-submission-tracking.md` | Clarification update (2 refs) |
| `docs/features/integrations/ibs-rabbitmq-integration.md` | `ticket_mutations` → `package_service` (1 ref) |
| `docs/features/packages/maintainer.md` | Update Security section: add confidentiality filtering requirement referencing `confidential_ticket_filter()`. The `tickets.md` spec already mandates this (`GET /api/v1/my/packages/*` MUST apply confidentiality filtering) but the maintainer spec does not reflect it |
| `docs/api-spec.md` | Update `require_accessible_ticket` section: reference `confidential_ticket_filter()` for the confidentiality check (evaluation step 2). The guard reuses the same shared utility with the single-ticket column reference |

Files that need NO changes (all references are ticket-centric):

- `docs/features/tickets/cvss-scoring.md`
- `docs/features/tickets/cve-tracking.md`
- `docs/features/tickets/ticket-audit-log.md`
- `docs/features/platform/admin.md`
- `docs/data-model.md`

### Phase 6: Update guardrails and tooling

| File | Change |
|------|--------|
| `AGENTS.md` (Guardrail 16) | Replace with text below |
| `docs/conventions.md` | Add I/O-then-Lock corollary to Transaction Hygiene Rules; update example reference to mention both modules |
| `.opencode/agents/ticket-integrity-reviewer.md` | Update to verify that package mutations go through `package_service` (not `ticket_mutations`). 16 references to update |

**New Guardrail 16 text**:

> ### 16. Centralized ticket status evaluation
>
> CRITICAL: Every service-layer function that modifies data relevant to
> ticket status gates MUST go through the appropriate centralized module:
>
> - **Package/track/product mutations** (`TicketPackageTrack`,
>   `TicketPackageProduct`, package soft-delete/restore): `package_service`
>   (`backend/app/services/package_service.py`)
> - **CVSS and severity mutations** (`CVECVSSAssessment` records, severity
>   override): `ticket_mutations`
>   (`backend/app/services/ticket_mutations.py`)
>
> Both modules call `ticket_mutations.evaluate_ticket_status()` after
> every gate-relevant mutation. Direct modification of gate-relevant
> records outside the owning module is a bug.
>
> If there is no suitable function in the appropriate module for a new
> type of gate-relevant mutation, add one before proceeding with the
> implementation.

### Phase 7: Update `rbac.md`

Add to the Endpoint Permission Map:

| Method | Path | Access Level | Owning Spec |
|--------|------|-------------|-------------|
| GET | `/api/v1/tickets/{ticket_id}/packages` | Public | [package-model](../packages/package-model.md#list-ticket-packages) |
| GET | `/api/v1/packages` | Public | [package-model](../packages/package-model.md#search-packages-across-tickets) |

### Phase 8: Update `docs/reviews/`

Register the new spec and acknowledge stale reviews:

1. **`.tracking.json`** — add entry:

   ```json
   "package-service": {
     "enabled": true,
     "abbr": "PKS",
     "cache": null
   }
   ```

2. **`README.md`** — add row to the Summary Table:

   ```
   | [package-service](package-service.md) | — | — | — | — | — | 0 |  | — |
   ```

3. **Stale markers** — `ticket-mutations` and `package-model` will
   become stale after modification. This is handled automatically by
   the review tracking system (comparison between `last_review` and
   spec modification date).

### Phase 9: Post-migration reviews

Invoke the following reviewers after all spec changes are complete:

| Reviewer | Reason | Guardrail |
|----------|--------|-----------|
| `@spec-gap-analyzer` | New spec (`package-service.md`) | 17 |
| `@spec-coherence-reviewer` | New spec + modifications to `ticket-mutations.md`, `package-model.md`, `tickets.md` | 15 |
| `@api-convention-reviewer` | Two new API endpoints defined in `package-model.md` | 20 |
| `@docs-placement-reviewer` | New rules added to feature specs (I/O-then-Lock reference, confidentiality filter) | 21 |
| `@docs-reviewer` | Significant documentation changes across ~15 files | 9 |

Each reviewer is invoked independently. Address any "Needs revision"
findings before considering the migration complete.

### Phase 10: Delete draft

Once all phases are complete and reviewer findings are addressed,
delete `docs/drafts/package-service.md`. The draft has served its
purpose — all content is now in the formal spec and updated documents.

---

## Open Questions

None — all questions resolved.

---

## Open Points (from reviewer feedback)

### OP1. Confidentiality filtering — shared utility design

**Source**: Spec Gap Analyzer (High severity)

**Status**: Resolved → see D11.

---

### OP2. `auto_assign_if_needed` scope — `ticket_service` as consumer

**Source**: Spec Coherence Reviewer (Medium), Design Reviewer (Low)

**Status**: Resolved → option A. D10 scoped to the two specified modules
(`ticket_mutations`, `package_service`). `ticket_service` noted as a
potential future consumer — when its spec is created, it will declare
which of its functions call the helper.

---

### OP3. `PackageListItem.ticket.deleted_at` — field presence semantics

**Source**: Spec Gap Analyzer (Medium)

**Status**: Resolved → option A. The `deleted_at` field is **always
present** in the JSON schema (value is `null` for non-Admin callers or
when `include_deleted` is not active). Fixed schema is simpler for typed
clients and consistent with `TicketDetail.deleted_at` behavior.

---

### OP4. I/O-then-Lock pattern — promote to `conventions.md`?

**Source**: Docs Placement Reviewer (Minor)

**Status**: Resolved → option A. Add a one-sentence corollary to
`conventions.md` (Transaction Hygiene Rules): "In modules that contain
both orchestration functions (with external I/O) and mutation functions
(with `FOR UPDATE` locks), the two concerns MUST be separated into
distinct functions — orchestration functions MUST NOT acquire locks."
Then `package-service.md` references this as an application of the
general rule.

---

## Resolved Decisions

### D1. Read endpoint response format

**Decision**: the per-ticket endpoint returns the full `PackageDetail[]`
tree (reusing the existing schema). The cross-ticket endpoint returns a
flat `PackageListItem[]` with a lightweight ticket reference and
aggregated track counts.

**Rationale**: per-ticket data is small enough for the full tree; cross-
ticket results need pagination and a lighter payload.

### D2. Per-ticket endpoint pagination

**Decision**: no pagination for `GET /api/v1/tickets/{ticket_id}/packages`.

**Rationale**: the number of packages per ticket is bounded by the
number of source packages affected by a single CVE — typically 1-5,
rarely exceeding 20. Pagination adds complexity with negligible benefit.

### D3. Cross-ticket ticket status filtering

**Decision**: `ticket_status` is an optional repeatable parameter.
Default: no filter (all statuses returned).

**Rationale**: most common use case is "show active tickets with this
package", but defaulting to all is more RESTful (no hidden filters).
The client explicitly passes
`ticket_status=new&ticket_status=analysis&ticket_status=analyzed`
for active-only results. The repeatable parameter pattern is consistent
with `GET /api/v1/tickets` (which uses `?status=new&status=analysis`).

### D4. Confidentiality filtering

**Decision**: the cross-ticket endpoint excludes packages belonging to
confidential tickets for unauthorized callers, using the same filter
mechanism as `GET /api/v1/tickets`. The filtering logic is provided by
the shared `confidential_ticket_filter()` utility (see D11), which the
endpoint handler constructs and passes to the service function.

**Rationale**: consistency with the ticket list endpoint. A package
reference to a confidential ticket would leak information about the
ticket's existence.

### D5. Service layer shared function for TicketDetail

**Decision**: `GET /api/v1/tickets/{ticket_id}` will use
`package_service.get_ticket_packages()` to populate the `packages`
field in `TicketDetail`.

**Rationale**: DRY — both the per-ticket packages endpoint and
ticket-details use the same logic. The refactoring is transparent to
API consumers (no response schema change).

### D6. `add_package_to_ticket` location

**Decision**: `add_package_to_ticket` is declared as part of
`package_service`. The module owns the complete package lifecycle.

**Rationale**: the Transaction Hygiene Rules forbid external I/O
*while a `FOR UPDATE` lock is held*. `add_package_to_ticket` performs
SMELT queries *before* calling `add_package_records()`, which is the
function that acquires the lock. The flow remains identical to today:

1. `add_package_to_ticket()` calls SMELT (no lock held)
2. `add_package_records()` acquires `FOR UPDATE`, creates records, lock
   released at commit

No violation occurs. Moving the function into `package_service` gives
the module complete ownership of package lifecycle (orchestration +
mutation + query) without breaking transactional guarantees.

### D7. Module naming

**Decision**: the module is named `package_service`
(`backend/app/services/package_service.py`).

**Rationale**: the module contains mutations (11), read functions (2),
and orchestration with external I/O (1). `package_mutations` would be
a misleading name for a module that also handles queries and
orchestration. `package_service` is consistent with `user_service`
naming and accurately describes the broader responsibility.

### D8. `evaluate_ticket_status()` import path

**Decision**: direct import from `ticket_mutations`.

```python
from app.services.ticket_mutations import evaluate_ticket_status
```

**Rationale**: the dependency is real and permanent — every package
mutation must re-evaluate ticket status. The dependency graph remains
unidirectional (`package_service` → `ticket_mutations`), so no
circular import risk. Extracting into a shared module (option B) or
using dependency injection (option C) adds complexity without benefit
for a single, stable function.

### D9. Soft-deletion semantics for read endpoints

**Decision**: the two read endpoints handle soft-deletion differently
based on their scope:

- **Per-ticket** (`GET /api/v1/tickets/{ticket_id}/packages`): returns
  ALL package/track/product records including soft-deleted ones, with
  `deleted_at` visible on each level. Identical to `TicketDetail.packages`.
  No `include_deleted` parameter needed — the full tree is always returned.
- **Cross-ticket** (`GET /api/v1/packages`): soft-deleted packages
  (`TicketPackage.deleted_at IS NOT NULL`) are **always excluded**.
  `include_deleted` controls visibility of packages belonging to
  **soft-deleted tickets** (Admin-only), consistent with
  `GET /api/v1/tickets`.

**Rationale**: package/track/product soft-deletion is an operational
choice by the VA ("this record is not relevant for analysis"). It is
not a secret — the per-ticket detail shows all records to anyone with
access. However, in a cross-ticket listing, excluded records add noise
without value — a VA searching "which tickets affect openssl?" does not
want results the VA already marked as irrelevant. Ticket soft-deletion,
on the other hand, is an Admin action that hides entire tickets — this
follows the same pattern as `GET /api/v1/tickets`.

### D10. Auto-assignment enforcement via shared helper

**Decision**: auto-assignment is implemented via a shared helper function
`auto_assign_if_needed(ticket, acting_user_id, db)` located in
`ticket_mutations`. All modules that modify tickets under a `FOR UPDATE`
lock (`ticket_mutations`, `package_service`) call this helper as the
first operation after acquiring the lock. `ticket_service` is a potential
future consumer — when its spec is created, it will declare which of its
functions call the helper.

**Module-level rule for `package_service`**: auto-assignment is always
applied by the function that acquires the `FOR UPDATE` lock, never by
orchestration wrappers. For example, `add_package_to_ticket` does NOT
apply auto-assignment — it delegates to `add_package_records()`, which
calls `auto_assign_if_needed` after acquiring the lock.

**Helper signature**:

```python
async def auto_assign_if_needed(
    ticket: Ticket,
    acting_user_id: UUID | None,
    db: AsyncSession,
) -> bool:
    """Assign ticket to acting VA if unassigned.

    Returns True if assignment was applied (audit event created),
    False otherwise (ticket already assigned or system action).

    Precondition: caller MUST hold FOR UPDATE on the ticket row.
    """
```

**Behavior**:

1. If `acting_user_id is None` → return False (system action, no
   auto-assignment)
2. If `ticket.assignee_id is not None` → return False (already assigned)
3. Set `ticket.assignee_id = acting_user_id`
4. Create `TicketAuditEvent` with `event_type = assignment`
5. Return True

**Rationale**: the logic is identical across all three modules (check +
assignment + audit event). A shared helper eliminates duplication, makes
the rule testable in isolation, and prevents omissions in future
functions. Placing it in `ticket_mutations` adds no new dependencies —
`package_service` already imports `evaluate_ticket_status` from there,
and `ticket_service` already has a relationship with `ticket_mutations`.

### D11. Confidentiality filtering via shared utility

**Decision**: confidentiality filtering is implemented as a shared
stateless function `confidential_ticket_filter()` in
`backend/app/core/filters.py`. It returns a SQLAlchemy `ColumnElement`
(a WHERE clause fragment) that any query can apply. The endpoint handler
constructs the filter; the service function receives it as a parameter.

**Function signature**:

```python
# backend/app/core/filters.py

from sqlalchemy import ColumnElement

def confidential_ticket_filter(
    ticket_id_col: Column,          # e.g., Ticket.id or TicketPackage.ticket_id
    is_confidential_col: Column,    # e.g., Ticket.is_confidential
    caller_is_privileged: bool,     # True if caller has VA or Admin role
    caller_user_id: UUID | None,    # for TicketAccessGrant lookup
    caller_email: str | None,       # for bugowner matching (case-insensitive)
) -> ColumnElement:
    """Build a SQL filter expression for confidential ticket visibility.

    Returns a boolean SQL expression that evaluates to TRUE for rows
    the caller is authorized to see. Apply with query.where(...).

    Authorization rules (from tickets.md):
    - Privileged callers (VA/Admin): see everything (returns TRUE)
    - Unauthenticated (user_id and email both None): only non-confidential
    - Authenticated non-privileged: non-confidential OR any of:
        - TicketAccessGrant exists for (ticket_id, user_id)
        - PackageBugowner.bugowner_email matches caller_email (person)
        - PackageBugownerMember.email matches caller_email (group)
    """
```

**Behavior**:

```
IF caller_is_privileged:
    return literal(True)  # no filter — VA/Admin see everything

IF caller_user_id is None AND caller_email is None:
    return is_confidential_col == False  # unauthenticated

return OR(
    is_confidential_col == False,
    EXISTS(
        select(TicketAccessGrant.ticket_id)
        .where(TicketAccessGrant.ticket_id == ticket_id_col)
        .where(TicketAccessGrant.user_id == caller_user_id)
    ),
    EXISTS(
        select(PackageBugowner.id)
        .join(TicketPackage,
              TicketPackage.package_name == PackageBugowner.package_name)
        .where(TicketPackage.ticket_id == ticket_id_col)
        .where(TicketPackage.deleted_at.is_(None))
        .where(PackageBugowner.bugowner_type == 'person')
        .where(func.lower(PackageBugowner.bugowner_email)
               == caller_email.lower())
    ),
    EXISTS(
        select(PackageBugownerMember.id)
        .join(PackageBugowner,
              PackageBugowner.id == PackageBugownerMember.package_bugowner_id)
        .join(TicketPackage,
              TicketPackage.package_name == PackageBugowner.package_name)
        .where(TicketPackage.ticket_id == ticket_id_col)
        .where(TicketPackage.deleted_at.is_(None))
        .where(func.lower(PackageBugownerMember.email)
               == caller_email.lower())
    ),
)
```

**Endpoint handler usage** (example for `GET /api/v1/packages`):

```python
@router.get("/packages")
async def list_packages(
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
    ...
):
    cf = confidential_ticket_filter(
        ticket_id_col=Ticket.id,
        is_confidential_col=Ticket.is_confidential,
        caller_is_privileged=has_role(current_user, [Role.VA, Role.ADMIN]),
        caller_user_id=current_user.id if current_user else None,
        caller_email=current_user.email if current_user else None,
    )
    return await package_service.search_packages(
        db, confidentiality_filter=cf, ...
    )
```

**Design properties**:

- **Stateless**: pure function, no side effects, no database calls
- **Decoupled**: accepts column references, not model instances — works
  with any query shape (ticket list, package list, CVE details, etc.)
- **Testable**: can be tested in isolation by inspecting the generated
  SQL expression
- **Single responsibility**: the handler knows the user; the service
  knows the query; the filter knows the rules

**Consumers** (known at specification time):

| Consumer | `ticket_id_col` | Notes |
|----------|-----------------|-------|
| `GET /api/v1/tickets` | `Ticket.id` | Ticket list endpoint |
| `GET /api/v1/packages` | `Ticket.id` (via JOIN) | Cross-ticket package search |
| `GET /api/v1/my/packages/*` | `Ticket.id` (via JOIN) | Maintainer operations (`maintainer.md`) |
| `require_accessible_ticket` | `Ticket.id` | Single-ticket access guard |

**Specification location**: the function is specified in
`docs/features/tickets/tickets.md` (Confidentiality Filtering section)
as a shared utility, since the Authorization Rules that it implements
are defined there. `package-service.md` cross-references it.

**Rationale**: three consumers are already known, with a fourth planned
(maintainer operations). The logic is identical across all consumers —
only the column reference changes. Extracting it avoids 4x duplication
of complex subquery logic and ensures that adding a 5th authorization
rule (e.g., team lead) requires a single code change. The
`confidentiality_filter: ColumnElement` parameter keeps the service
function completely unaware of access rules — clean separation of
concerns.

---

## Estimated Impact

- **New files**: 1 (`docs/features/packages/package-service.md`)
- **Modified files**: ~15 (spec files, guardrails, tooling)
- **Unchanged files**: ~6 (ticket-centric specs with no package refs)
- **New API endpoints**: 2
- **New shared utilities**: 1 (`confidential_ticket_filter()` in
  `backend/app/core/filters.py`, specified in `tickets.md`)
- **New service functions**: 2 (query) + 11 (moved mutations) + 1 (moved
  orchestration: `add_package_to_ticket`) + 1 (new helper:
  `auto_assign_if_needed`) = 15
- **Functions removed from `ticket-mutations.md`**: 11
- **Functions moved from `package-model.md`**: 1 (`add_package_to_ticket`)
- **Guardrail updates**: 1 (Guardrail 16 in `AGENTS.md`)
- **Agent updates**: 1 (`ticket-integrity-reviewer.md`)
