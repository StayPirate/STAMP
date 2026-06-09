# Draft: Unify CVSS Assessment Mutations into Upsert

## Status

**Draft** — pending review before applying changes to approved specs.

## Problem

The `ticket_mutations` module exposes three CVSS mutation functions with
overlapping concerns:

- `create_cvss_assessment(cve_id, provider, vector, acting_user_id)` —
  rejects duplicates with `DuplicateCVSSAssessmentError`
- `update_cvss_assessment(assessment_id, vector, acting_user_id)` —
  requires pre-resolved UUID, rejects version mismatch with
  `CVSSVersionMismatchError`
- `delete_cvss_assessment(assessment_id, acting_user_id)` — requires
  pre-resolved UUID

This creates three issues:

1. **Upsert dispatch at the wrong layer**. The SUSE POST endpoint must
   check for an existing record and dispatch to create or update — a
   concern that belongs in the service layer, not the API handler. The
   `DuplicateCVSSAssessmentError` is never surfaced to API consumers (see
   CVS-COH-13).

2. **Tension with `cve_service`**. `cve_service.upsert_cve()` calls
   `create_cvss_assessment()` during Phase 1 ingestion, but fetcher
   re-syncs can encounter existing records. The spec includes an
   `ON CONFLICT DO UPDATE` note in the child table deduplication section,
   contradicting the `DuplicateCVSSAssessmentError` precondition.

3. **Unnecessary UUID resolution**. Both `update_cvss_assessment()` and
   `delete_cvss_assessment()` take an `assessment_id` (UUID), forcing
   callers to resolve the natural key `(cve_id, provider, version)` to
   a UUID before calling the function. All callers already have the
   natural key.

## Proposed Change

### Replace create + update with upsert

Replace `create_cvss_assessment()` and `update_cvss_assessment()` with a
single `upsert_cvss_assessment()`:

```
upsert_cvss_assessment(db, cve_id, provider, vector, acting_user_id)
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `cve_id` | `UUID` | Yes | CVE that receives the assessment |
| `provider` | `str` | Yes | Assessment provider (e.g., `"SUSE"`, `"NVD"`) |
| `vector` | `str` | Yes | CVSS vector string (version and score derived from vector) |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- CVE must exist for `cve_id` — the FK constraint on
  `CVECVSSAssessment.cve_id` requires a valid CVE. API endpoints
  resolve and validate the CVE path parameter before calling this
  function; `cve_service.upsert_cve()` creates the CVE before calling
  this function. The function does not check CVE existence explicitly —
  an invalid `cve_id` produces an `IntegrityError` from the database.
- Vector must be parseable — raises `InvalidCVSSVectorError`

**Persistence mechanism**: SQL `INSERT ... ON CONFLICT DO UPDATE` on the
unique constraint `(cve_id, provider_name, cvss_version)`. This
guarantees atomicity at the database level — concurrent upserts for the
same natural key are serialized by PostgreSQL. This is consistent with
the `ON CONFLICT DO UPDATE` strategy documented in `cve-service.md`
(Child Table Deduplication) for all child tables with stable unique
constraints.

**Behavior**:

1. Parse the vector string. Derive version and score. If parsing fails,
   raise `InvalidCVSSVectorError`
2. `SELECT` existing `CVECVSSAssessment` for `(cve_id, provider,
   derived_version)`. Capture the existing record (if any) for old-value
   determination and no-op detection
3. **No-op short-circuit**: if an existing record was found and
   `existing.vector == incoming_vector`, return
   `(existing, UNCHANGED)` immediately — no database write, no lock
   acquisition, no recalculation chain, no audit event. This prevents
   unnecessary lock contention and recalculation overhead during bulk
   fetcher re-syncs where most CVSS data has not changed. Note: the
   short-circuit bypasses `auto_assign_actor()` because no mutation
   occurred — this is correct and consistent with the principle that
   side effects are triggered by state changes, not by intent to change
4. Look up the ticket associated with the CVE (if any)
5. If a ticket exists:
   a. Acquire `FOR UPDATE` on the Ticket row
   b. Call `ensure_ticket_operable(ticket)` — if the ticket is in a
      non-mutable status, the function raises `TicketNotMutableError`.
      No assessment write has occurred at this point, so no rollback of
      assessment data is needed
6. Execute `INSERT ... ON CONFLICT DO UPDATE` with the parsed vector
   and computed score. Determine action:
   - **No existing record** (step 2 returned nothing): `CREATED`
   - **Existing record with different vector**: `UPDATED`
7. If a ticket exists:
   a. Call `auto_assign_actor(ticket, acting_user_id, db)`
   b. Recalculate severity via `resolve_severity_score()` and
      eligibility via `resolve_eligibility_score()`
   c. Create `TicketAuditEvent` (`cvss_assessment_changed`). The
      `old_value` is derived from the `SELECT` in step 2: `NULL` if the
      record was created, `"provider vX.Y old_score"` if updated. The
      recalculation chain may also produce `severity_changed` and
      `product_eligibility_changed` audit events when derived values
      change
   d. Call `reconcile_ticket_status()`. If this produces a backward
      transition from Resolved, the function invokes
      `recalculate_cvss_chain()` and enqueues `catch_up()` per the
      post-regression hook contract in `ticket-mutations.md`. The
      post-regression hook is handled internally — callers do not need
      to check for regression
8. Return `(assessment, action)`

**Return type**: `tuple[CVECVSSAssessment, AssessmentUpsertAction]` —
where `AssessmentUpsertAction` is a three-valued enum:

| Value | Meaning | Metric |
|-------|---------|--------|
| `CREATED` | New record inserted | `record_created()` |
| `UPDATED` | Existing record modified (vector changed) | `record_updated()` |
| `UNCHANGED` | Existing record identical (no-op) | — (no metric) |

`AssessmentUpsertAction` is a separate type from
`cve_service.UpsertAction` despite having the same members. The
semantics differ: `cve_service.UpsertAction.unchanged` means "no global
CVE fields contributed" (child data may still have been upserted),
whereas `AssessmentUpsertAction.UNCHANGED` means "the assessment vector
is identical, no mutation occurred at all." Distinct types prevent
accidental conflation in code that handles both return values.

**Audit event values**:

| Action | `old_value` | `new_value` |
|--------|-------------|-------------|
| `CREATED` | `NULL` | `"provider vX.Y score"` |
| `UPDATED` | `"provider vX.Y old_score"` | `"provider vX.Y new_score"` |
| `UNCHANGED` | — (no audit event created) | — |

### Modify delete to use natural key

Change `delete_cvss_assessment()` signature from `(assessment_id)` to
natural key:

```
delete_cvss_assessment(db, cve_id, provider, cvss_version, acting_user_id)
```

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `cve_id` | `UUID` | Yes | CVE owning the assessment |
| `provider` | `str` | Yes | Assessment provider |
| `cvss_version` | `str` | Yes | CVSS version (`"3.1"`, `"4.0"`, etc.) |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Assessment must exist for `(cve_id, provider, cvss_version)` — raises
  `CVSSAssessmentNotFoundError`

Behavior is otherwise identical to the current `delete_cvss_assessment()`.

### Behavioral correction: `auto_assign_actor()` alignment

The current `create_cvss_assessment()` and `update_cvss_assessment()`
do NOT call `auto_assign_actor()` in their explicit behavior steps,
despite the generic gate-relevant mutation pattern
(`ticket-mutations.md`, lines 379-388) listing it as step 3. This is
an omission in the existing spec — all other gate-relevant mutation
functions follow the generic pattern.

The upsert function corrects this by explicitly including
`auto_assign_actor()` (step 7a). This is an intentional behavioral
change: when a VA submits a SUSE CVSS assessment on a ticket with no
assignee, they automatically become the assignee (and the ticket
transitions from `New` to `Analysis` if applicable). This includes
tickets in Resolved status: if a VA submits a CVSS assessment on a
Resolved ticket with no assignee, they are auto-assigned. Combined with
the recalculation chain, this may also trigger a status regression from
Resolved — both effects are intentional and consistent with the
principle that any gate-relevant mutation claims ownership of the ticket.

This aligns CVSS mutations with all other gate-relevant mutations in
the module.

Note: `set_severity_override()` has the same omission (no
`auto_assign_actor()` call in its behavior steps). Correcting that
function is out of scope for this change and should be addressed
separately.

### Exceptions removed

| Exception | Reason for removal |
|-----------|--------------------|
| `DuplicateCVSSAssessmentError` | Upsert handles existing records by updating them. No duplicate scenario exists |
| `CVSSVersionMismatchError` | Version is part of the natural key (derived from vector), not something that can mismatch |

### Exceptions retained

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `CVSSAssessmentNotFoundError` | 404 | `CVSS_ASSESSMENT_NOT_FOUND` | Delete: no assessment exists for the natural key |
| `InvalidCVSSVectorError` | 422 | `CVSS_INVALID_VECTOR` | Upsert: vector string is malformed or invalid |

### Error codes removed from `api-spec.md`

| Code | Reason |
|------|--------|
| `CVSS_DUPLICATE_ASSESSMENT` | Exception eliminated |
| `CVSS_VERSION_MISMATCH` | Exception eliminated |

Per `api-spec.md` Versioning, error code removal is a breaking change.
The project is currently in specification phase with no deployed
consumers, implemented code, or production database. Additionally,
`CVSS_DUPLICATE_ASSESSMENT` was never surfaced to API consumers (the
endpoint dispatched to update when a record existed), and
`CVSS_VERSION_MISMATCH` was only reachable via the eliminated
`update_cvss_assessment()`. The removal is safe.

## Caller Impact

### `cve_service.upsert_cve()` (Phase 1)

**Before**: calls `create_cvss_assessment()`, wraps in per-entry error
handling to tolerate `DuplicateCVSSAssessmentError` on re-sync.

**After**: calls `upsert_cvss_assessment()`. The function handles
create-or-update internally. No error handling needed for duplicates.
The `ON CONFLICT DO UPDATE` note in the child table deduplication section
becomes the natural behavior of the function rather than a workaround.

If the ticket associated with the CVE is in a non-mutable status
(Ignored, Duplicated), `upsert_cvss_assessment()` raises
`TicketNotMutableError` **before** writing the assessment (step 5b).
`cve_service.upsert_cve()` catches this via its per-entry error handling
(Key Principle 6) and continues with other child data — no assessment
write has occurred, so no session cleanup is needed. This is identical
to the current behavior of `create_cvss_assessment()`.

### SUSE POST endpoint (`POST /api/v1/cves/{cve_id}/cvss/suse`)

**Before**: handler checks for existing record, dispatches to
`create_cvss_assessment()` or `update_cvss_assessment()`.

**After**: handler calls `upsert_cvss_assessment(cve_id, "SUSE", vector,
user_id)` directly. No dispatch logic. The note in `cvss-scoring.md`
about `CVSS_DUPLICATE_ASSESSMENT` never being returned is no longer
needed — the error does not exist. The endpoint returns **201 Created**
when the `AssessmentUpsertAction` is `CREATED`, and **200 OK** when it
is `UPDATED` or `UNCHANGED`. The response body remains the assessment
object in the standard `{"data": ...}` envelope — the
`AssessmentUpsertAction` is a service-layer detail for metric reporting
and is not included in the API response. The HTTP status code carries
the create-vs-update signal; the distinction between `UPDATED` and
`UNCHANGED` is not exposed to API consumers.

### SUSE DELETE endpoint (`DELETE /api/v1/cves/{cve_id}/cvss/suse/{cvss_version}`)

**Before**: handler resolves `(cve_id, "SUSE", cvss_version)` to UUID,
calls `delete_cvss_assessment(assessment_id)`.

**After**: handler calls `delete_cvss_assessment(cve_id, "SUSE",
cvss_version)` directly. No UUID resolution.

### Callers table and async pattern table (stale reference correction)

The `ticket-mutations.md` callers table (line 965) lists a "CVSS sync
fetcher" as a direct caller of all three CVSS functions. This is a
stale reference from when `sync_redhat_cves` was a CVSS-only fetcher
that called `ticket_mutations` directly. After the Red Hat fetcher was
extended to CWE, references, and packages, it was redesigned to call
`cve_service.upsert_cve()`, which internally calls
`create_cvss_assessment()`. The "CVSS sync fetcher" row no longer
describes an actual caller — all CVE fetchers go through `cve_service`.

Similarly, the async pattern table (line 41) shows
`Celery task (CVSS sync) | asyncio.run(ticket_mutations.create_cvss_assessment(...))`,
which describes the same obsolete direct path.

**Callers table fixes**:

1. Replace the "CVSS sync fetcher" row with:

   | Caller Category | Operations Used | Context |
   |-----------------|-----------------|---------|
   | CVE fetchers (via `cve_service.upsert_cve()`) | `upsert_cvss_assessment()` | Background CVE ingestion (Phase 1) |

2. Update the "CVE API mutation endpoints" row — replace
   `create_cvss_assessment(), update_cvss_assessment(),
   delete_cvss_assessment()` with
   `upsert_cvss_assessment(), delete_cvss_assessment()`

**Async pattern table fix**: remove the Celery task row entirely. CVE
fetchers call `cve_service.upsert_cve()`, which internally calls
`upsert_cvss_assessment()` — this is an indirect call path, not a
direct entry point to `ticket_mutations`. The async pattern table
documents direct entry points to the module; the only remaining entry
is the API endpoint row.

## Review Findings Addressed

This change resolves two open review findings:

- **CVS-GAP-10** (Medium): "SUSE CVSS POST endpoint specifies upsert but
  service function rejects duplicates" — eliminated by making upsert the
  service-layer primitive
- **CVS-DES-02** (Medium): "API upsert endpoint inconsistent with service
  layer's separate create/update functions" — eliminated by aligning the
  service API with the domain operation

## Specs to Update

| File | Changes |
|------|---------|
| `docs/features/tickets/ticket-mutations.md` | Replace `create_cvss_assessment()` + `update_cvss_assessment()` definitions with `upsert_cvss_assessment()`. Update `delete_cvss_assessment()` signature. Update exception table: remove `DuplicateCVSSAssessmentError` and `CVSSVersionMismatchError`. Update callers table. Update `ensure_ticket_operable` consumers table |
| `docs/features/tickets/cvss-scoring.md` | Update POST endpoint section: remove upsert dispatch note and `CVSS_DUPLICATE_ASSESSMENT` paragraph (lines 527-531), simplify description to reference `upsert_cvss_assessment()` directly. Change response code to 201 Created / 200 OK. Update Service Architecture section references. Verify heading anchors: `rbac.md` links to `#set-or-update-suse-cvss-assessment` and `#delete-suse-cvss-assessment` — if headings change, update `rbac.md` Endpoint Permission Map accordingly |
| `docs/features/tickets/cve-service.md` | Update Phase 1 to reference `upsert_cvss_assessment()`. Update child table deduplication note (the `ON CONFLICT DO UPDATE` section covers five child tables — only the `CVECVSSAssessment` tension is resolved; the note remains). Update module relationship table |
| `docs/features/tickets/cve-tracking.md` | Update NVD sync algorithm step 3g reference |
| `docs/api-spec.md` | Remove `CVSS_DUPLICATE_ASSESSMENT` and `CVSS_VERSION_MISMATCH` from Error Code Categories table |
| `docs/data-model.md` | No changes (no schema impact — the unique constraint on `CVECVSSAssessment` remains) |
| `docs/reviews/ticket-mutations.md` | Update references to `create_cvss_assessment()` and `update_cvss_assessment()` in findings TKM-GAP-09, TKM-GAP-10, and related text. Update function names for traceability |

## Implementation Plan

### Step 1: Update `ticket-mutations.md`

1. Replace `create_cvss_assessment()` section (lines 404-456) and
   `update_cvss_assessment()` section (lines 459-510) with a single
   `upsert_cvss_assessment()` section
2. Update `delete_cvss_assessment()` section: change parameter from
   `assessment_id` to `(cve_id, provider, cvss_version)`
3. Update exception table: remove `DuplicateCVSSAssessmentError` and
   `CVSSVersionMismatchError`
4. Update callers table: replace "CVSS sync fetcher" row and update
   "CVE API mutation endpoints" row (stale reference correction — see
   Caller Impact section)
5. Update `ensure_ticket_operable` consumers table and its footnote
   (line 375: "see `create_cvss_assessment()` below" →
   `upsert_cvss_assessment()`)
6. Update async pattern table (line 41): remove the Celery task row
   entirely — CVE fetchers are indirect callers via `cve_service`,
   not direct entry points to `ticket_mutations` (stale reference
   correction — see Caller Impact section)
7. Fix pre-existing provider name casing inconsistency: change lowercase
   provider examples (`"suse"`, `"nvd"`) to uppercase (`"SUSE"`, `"NVD"`)
   to match the authoritative definitions in `cvss-scoring.md`

### Step 2: Update `cvss-scoring.md`

1. Simplify POST endpoint section: remove dispatch note about
   `CVSS_DUPLICATE_ASSESSMENT` and the paragraph explaining it is never
   returned (lines 527-531); change description to reference
   `upsert_cvss_assessment()` directly
2. Change POST response code from `200 OK` to `201 Created` when a new
   assessment is created and `200 OK` when an existing one is updated or
   unchanged
3. Update Service Architecture text that mentions separate create/update
4. Heading text `### Set or Update SUSE CVSS Assessment` is retained to
   preserve the `#set-or-update-suse-cvss-assessment` anchor used by
   `rbac.md`. The heading `### Delete SUSE CVSS Assessment` is also
   retained. If either heading is renamed for any reason, the Endpoint
   Permission Map in `rbac.md` must be updated accordingly

### Step 3: Update `cve-service.md`

1. Change Phase 1 references from `create_cvss_assessment()` to
   `upsert_cvss_assessment()`
2. Update `ON CONFLICT DO UPDATE` child table deduplication note: the
   section covers five child tables and remains as-is. The implicit
   tension between `ON CONFLICT DO UPDATE` (SQL-level) and
   `DuplicateCVSSAssessmentError` (service-level) is resolved — with
   `upsert_cvss_assessment()`, both layers are aligned. No text change
   needed unless the note explicitly mentions the old precondition
3. Update architecture diagram and module relationship table

### Step 4: Update `cve-tracking.md`

1. Update NVD sync algorithm references

### Step 5: Update `api-spec.md`

1. Remove `CVSS_DUPLICATE_ASSESSMENT` from Error Code Categories
2. Remove `CVSS_VERSION_MISMATCH` from Error Code Categories

### Step 6: Update review findings

1. Resolve CVS-GAP-10 and CVS-DES-02 in `docs/reviews/cvss-scoring.md`
2. Update references to `create_cvss_assessment()` and
   `update_cvss_assessment()` in `docs/reviews/ticket-mutations.md`
   (findings TKM-GAP-09, TKM-GAP-10, and related text)
3. Update `.tracking.json` cache
4. Update `docs/reviews/README.md`

## Design Decision: Lock Position

The upsert function acquires `FOR UPDATE` on the Ticket row before the
assessment write (step 5a), spanning the operability check, the write,
and the recalculation chain (steps 5b through 7d). The lock duration is
slightly longer than a post-write lock would be (it now includes the
assessment write), but the write itself is a fast DB operation — not
external I/O. The I/O-then-Lock corollary is satisfied.

For ticketless CVEs, no lock is acquired — the function writes the
assessment (step 6) and returns.

## Design Decision: Step Ordering

The upsert checks ticket operability (step 5b) **before** writing the
`CVECVSSAssessment` record (step 6). This preserves the fail-fast
behavior of the current `create_cvss_assessment()` and avoids a
rollback dependency:

1. **Fail-fast for inoperable tickets**: if the ticket is in a
   non-mutable status (Ignored, Duplicated), `TicketNotMutableError` is
   raised before any assessment write occurs. This is critical for
   `cve_service.upsert_cve()`, which catches `TicketNotMutableError`
   via per-entry error handling — if the write happened first, the
   assessment would be flushed to the session and would persist when the
   parent transaction commits, even though the operability check rejected
   it
2. **No-op short-circuit unaffected**: the `SELECT` (step 2) and vector
   comparison (step 3) execute before either the lock or the write. If
   the vector is unchanged, the function returns immediately without
   touching the ticket or writing the assessment
3. **Ticketless CVEs unaffected**: for CVEs without an associated
   ticket, step 4 finds no ticket, steps 5 and 7 are skipped, and step
   6 writes the assessment without any lock
4. **Assessment-level concurrency**: concurrent upserts for the same
   `(cve_id, provider, version)` are serialized by the `ON CONFLICT`
   clause at the SQL level, independent of the ticket lock

## Not In Scope

- **Renaming the module or changing the module boundary**: the functions
  remain in `ticket_mutations`
- **Changing the `CVECVSSAssessment` data model**: no schema changes
- **Adding new API endpoints**: the POST and DELETE endpoints retain
  their current paths. The POST response code changes from 200-for-all
  to 201 Created / 200 OK depending on the action (see Caller Impact)
- **Modifying the recalculation chain**: the chain behavior is unchanged

## In Scope (behavioral corrections)

- **`auto_assign_actor()` alignment**: the upsert function adds
  `auto_assign_actor()` to the CVSS mutation path, correcting an
  omission where the existing `create_cvss_assessment()` and
  `update_cvss_assessment()` deviated from the generic gate-relevant
  mutation pattern (see "Behavioral correction" section above)
- **No-op detection**: the upsert function short-circuits when the
  incoming vector is identical to the stored one, a behavior not present
  in the current create/update functions. This is consistent with
  `set_severity_override()`, which has the same no-op pattern
- **HTTP response code**: the POST endpoint changes from 200 OK for all
  cases to 201 Created when a new assessment is created and 200 OK when
  an existing one is updated or unchanged
