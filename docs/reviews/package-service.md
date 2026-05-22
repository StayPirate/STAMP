# Review: package-service

**Spec**: `docs/features/packages/package-service.md`
**Last reviewed**: 2026-05-22
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### PKS-GAP-01 — set_track_status() does not specify WONT_FIX protection enforcement (High)

**Status**: RESOLVED — Final-status protection added to set_track_status(): system callers rejected with warning log when track is in final state (2026-05-22)

### PKS-GAP-02 — set_track_delivery_status() does not specify delivery status transition validation (High)

**Status**: RESOLVED — Delivery status transition validation added to set_track_delivery_status(); caller error handling documented in ibs-submission-tracking (2026-05-22)

### PKS-GAP-03 — set_product_status() does not distinguish VA override from propagation (High)

**Status**: RESOLVED — set_product_status() removed; product-level affectedness eliminated. Products no longer have status or is_status_override columns. (2026-05-22)

### PKS-GAP-04 — add_package_to_ticket() behavior when SMELT returns zero tracks (Medium)

**Status**: RESOLVED — Spec updated: add_package_to_ticket() now documents SMELT validation gate (zero tracks → 422, unavailable → 503) before any DB writes (2026-05-22)

### PKS-GAP-05 — Product-to-ProductRepository lookup location unspecified (Medium)

**Category**: Error paths
**Status**: OPEN

The `package-model.md` spec states: "If no matching product is found for a target, log a warning but do not fail." The spec does not clarify who is responsible for the product-to-`ProductRepository` lookup — `add_package_to_ticket()` (before the lock, consistent with I/O-then-Lock) or `add_package_records()` (inside the lock). The I/O-then-Lock invariant suggests the lookup must happen before the lock, but this is not stated.

### PKS-GAP-06 — Bugowner resolution and submission discovery failure behavior unspecified (Medium)

**Category**: Error paths
**Status**: OPEN

Steps 5 and 6 of `add_package_to_ticket()` involve external I/O (IBS API for bugowner, task enqueue for submission discovery). The spec does not specify what happens if bugowner resolution fails or if the task enqueue fails. It is unclear whether these failures cause the entire operation to fail and rollback the record creation from step 4, or whether they are best-effort.

### PKS-GAP-07 — add_package_records() with empty tracks list creates orphan (Medium)

**Category**: Boundary conditions
**Status**: OPEN

If `tracks` is an empty list, the function would create/skip a `TicketPackage` record, create a `package_added` audit event, and call `evaluate_ticket_status()`, but create zero track/product records. This produces a package with no tracks — which is immediately an orphan. The orphan cleanup invariant only triggers "on soft-deletion events," not on creation with zero children.

### PKS-GAP-08 — Restore pre-checks from package-model.md not reflected in module spec (Medium)

**Category**: Boundary conditions
**Status**: OPEN

The `package-model.md` spec defines detailed restore pre-checks: restoring a package requires at least one track with `deleted_at IS NULL` that has at least one product with `deleted_at IS NULL`; restoring a track requires at least one product with `deleted_at IS NULL`. These pre-checks (returning `PACKAGE_RESTORE_BLOCKED`) are absent from the `package_service.md` restore function specs.

### PKS-GAP-09 — TicketPackage creation in step 1 of add_package_to_ticket outside FOR UPDATE lock (Medium)

**Category**: Data lifecycle
**Status**: OPEN

Step 1 of `add_package_to_ticket()` creates a `TicketPackage` record before the lock is acquired in step 4. Two concurrent calls for the same ticket and package could both attempt to create the record, causing a unique constraint violation. The spec should clarify whether step 1's creation is moved inside `add_package_records()` or whether the constraint violation should be caught as a no-op.

### PKS-GAP-10 — TrackData type not defined (Medium)

**Category**: Data lifecycle
**Status**: OPEN

The `TrackData` type is referenced in the `add_package_records()` parameter table but never defined. What fields does it contain? The I/O-then-Lock pattern implies all external resolution happens before `add_package_records()`, so `TrackData` must contain fully resolved data — but this is not stated. Without a definition, implementers must reverse-engineer the structure.

### PKS-GAP-11 — Mutations on effectively-excluded records not explicitly permitted or denied (Low)

**Category**: Boundary conditions
**Status**: OPEN

The preconditions for `set_track_status()` check that the track itself is not soft-deleted and the ticket is not soft-deleted, but do not check whether the parent `TicketPackage` is soft-deleted. Per the hierarchical exclusion model, a track whose parent package is soft-deleted is "effectively excluded." The spec should be explicit about whether mutations on effectively-excluded records are permitted.

### PKS-GAP-12 — Delivery status regression audit event omitted (Low)

**Category**: State machine completeness
**Status**: OPEN

When delivery regresses from `IN_PROGRESS` to `PENDING` (all SRs revoked/declined), no `TicketAuditEvent` is created. The spec only documents that `RELEASED` generates an event and "intermediate" transitions do not. A regression signals a failed delivery attempt worth a VA's attention but produces no ticket-level audit trail.

### PKS-GAP-13 — Eligibility calculation I/O location within lock not specified (Low)

**Category**: Configuration and defaults
**Status**: OPEN

When `add_package_records()` creates `TicketPackageProduct` records, it must calculate eligibility. This requires resolving the CVSS score and looking up product lifecycle data. The spec says the module "delegates CVSS resolution and eligibility calculation to pure functions in `cvss.py`" but does not specify whether these database reads happen inside the `FOR UPDATE` lock.

---

## Coherence

### PKS-COH-01 — set_product_status() does not specify setting is_status_override = true (High)

**Status**: RESOLVED — set_product_status() removed; no product-level is_status_override to set. Products no longer have affectedness status. (2026-05-22)

### PKS-COH-02 — set_product_eligibility() does not specify setting is_eligible_override = true (High)

**Status**: RESOLVED — Spec updated: set_product_eligibility() now documents is_eligible_override management for both override (bool) and reset (None) cases (2026-05-22)

### PKS-COH-03 — Propagation to soft-deleted products contradicts 'active only' rule (Medium)

**Status**: RESOLVED — Status propagation (track → product) removed entirely. Products no longer have affectedness status; contradiction no longer exists. (2026-05-22)

### PKS-COH-04 — set_track_status() propagation mechanism for child products unspecified vs set_product_status() (Medium)

**Status**: RESOLVED — Propagation step removed from set_track_status(); no child product status changes. Products no longer have affectedness status. (2026-05-22)

### PKS-COH-05 — IBS product release detection caller uses wrong operation (Medium)

**Status**: RESOLVED — Callers table corrected: set_product_released_at() replaces set_product_status() for IBS product release detection. (2026-05-22)

### PKS-COH-06 — track_released audit event user_id conflict between package-service and audit-log (Low)

**Category**: Contradictory definitions
**Status**: OPEN

ticket-audit-log.md defines track_released with user_id = NULL (system action). package-service.md set_track_delivery_status() creates track_released when delivery_status transitions to RELEASED. Since delivery status is system-managed per package-model.md, acting_user_id should always be None for this function. However, the function signature accepts acting_user_id: UUID | None, and the general pattern calls auto_assign_if_needed() — implying it could be called with a user context.

---

## Design

### PKS-DES-01 — Orphan cascade calls evaluate_ticket_status() multiple times per operation (Medium)

**Category**: Complexity and performance
**Status**: OPEN

The orphan cascade shows evaluate_ticket_status() being called at each level: after the product soft-delete, after the orphan-triggered track soft-delete, and after the orphan-triggered package soft-delete. In the worst case, this calls evaluate_ticket_status() three times within the same transaction. Since the function queries all active tracks/products to determine ticket status, only the final evaluation matters. Alternative: call evaluate_ticket_status() once at the end of the entire cascade.

### PKS-DES-02 — No mechanism to batch-set track statuses without repeated lock acquisition (Low)

**Category**: Scalability
**Status**: OPEN

Each mutation function independently acquires FOR UPDATE on the parent ticket. When a VA sets status on multiple tracks of the same ticket (common workflow: marking 20 tracks as NOT_AFFECTED), each call independently locks, evaluates, and releases. Alternative: add a batch variant that acquires the lock once, applies all mutations, then evaluates once. Acceptable to defer.

### PKS-DES-03 — set_track_status() lacks WONT_FIX protection specification (High)

**Status**: RESOLVED — Cross-agent duplicate of PKS-GAP-001 (2026-05-22)

### PKS-DES-04 — set_track_delivery_status() lacks transition validation (High)

**Status**: RESOLVED — Cross-agent duplicate of PKS-GAP-002 (2026-05-22)

### PKS-DES-05 — TicketPackage creation in add_package_to_ticket() is outside the FOR UPDATE lock (Medium)

**Status**: RESOLVED — Cross-agent duplicate of PKS-GAP-009 (2026-05-22)

### PKS-DES-06 — Product status propagation mechanism unspecified (internal vs set_product_status) (High)

**Status**: RESOLVED — Cross-agent duplicate of PKS-GAP-003 (2026-05-22)

---

## Security

### PKS-SEC-01 — No authorization enforcement specified at service layer for acting_user_id (Medium)

**Category**: Authorization
**Status**: OPEN

The spec defines an acting_user_id parameter convention but does not specify any validation that the provided UUID actually corresponds to the authenticated caller. If an API handler passes a different user's UUID (due to a bug or IDOR in the handler), the service would auto-assign the ticket to that user and create audit events attributing the action to them. The service layer trusts the caller completely for identity — which is acceptable if the API layer is the only entry point, but the spec should acknowledge this trust boundary explicitly.

### PKS-SEC-02 — Confidentiality filter delegation creates risk of bypass in new callers (Medium)

**Category**: Authorization
**Status**: OPEN

The search_packages() function receives a pre-built confidentiality_filter from the endpoint handler and states 'The service function is unaware of access rules.' Similarly, get_ticket_packages() relies on the caller performing require_accessible_ticket before invocation. Any new caller that forgets to apply the confidentiality check will expose confidential ticket package data. The spec does not define a defensive fallback.

### PKS-SEC-03 — No input validation specified for package_name parameter (Low)

**Category**: Input validation
**Status**: OPEN

The add_package_to_ticket() function accepts a package_name: str that is used to query SMELT and create database records. The spec does not specify any validation (length limit, allowed characters, format).

### PKS-SEC-04 — search_packages ILIKE pattern not escaped (Low)

**Category**: Input validation
**Status**: OPEN

The search_packages() function applies search as ILIKE '%term%' substring match. The spec does not mention escaping SQL LIKE metacharacters (%, _, \) in user input.

---

## API Conventions

_No findings — the spec defines service functions, not API endpoints._
