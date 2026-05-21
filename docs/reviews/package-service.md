# Review: package-service

**Spec**: `docs/features/packages/package-service.md`
**Last reviewed**: 2026-05-21
**Reviewers**: Gap Analysis

---

## Gap Analysis

### PKS-GAP-001 — set_track_status() does not specify WONT_FIX protection enforcement (High)

**Category**: State machine completeness
**Status**: OPEN

The `package-model.md` spec states that `WONT_FIX` is never modified by automatic transitions. `set_track_status()` is used by both VA-initiated and system-initiated callers (IBS release detection sets `FIXED` automatically). The spec does not state whether `set_track_status()` enforces the `WONT_FIX` protection itself or delegates that to the caller. When IBS release detection calls `set_track_status(track_id, FIXED, acting_user_id=None)` on a track whose current status is `WONT_FIX`, it is unclear whether the function should silently no-op, raise an error, or apply the change. An implementer could either skip the protection (corrupting the VA's decision) or enforce it (conflicting with callers that expect the function to always succeed).

### PKS-GAP-002 — set_track_delivery_status() does not specify delivery status transition validation (High)

**Category**: State machine completeness
**Status**: OPEN

The `package-model.md` defines specific delivery status transitions (`PENDING -> IN_PROGRESS`, `IN_PROGRESS -> RELEASED`, `IN_PROGRESS -> PENDING`) and explicitly states "RELEASED is irreversible." The `set_track_delivery_status()` function accepts any `DeliveryStatus` value with no documented transition validation. A bug in the `RequestSyncFetcher` reconciliation phase could call `set_track_delivery_status(track_id, IN_PROGRESS)` on a track already at `RELEASED`. The spec does not state whether the function should reject this invalid regression, no-op, or apply it. Violating the "RELEASED is irreversible" invariant would corrupt delivery tracking data and could falsely reopen resolved tickets via `evaluate_ticket_status()`.

### PKS-GAP-003 — set_product_status() does not distinguish VA override from propagation (High)

**Category**: State machine completeness
**Status**: OPEN

The function always creates a `product_status_overridden` audit event, implying it is exclusively for VA overrides. However, `package-model.md` describes automatic product status changes via track propagation (when a VA sets a track status, products inherit). There is no separate function specified for propagation-driven product status updates (which should inherit from the parent track without setting `is_status_override = true`). When `set_track_status()` propagates to child products (step 6), it is unclear whether it calls `set_product_status()` — which would incorrectly set `is_status_override = true` and emit `product_status_overridden`. If so, every track status change would incorrectly mark all child products as VA-overridden, breaking future automatic propagation.

### PKS-GAP-004 — add_package_to_ticket() behavior when SMELT returns zero tracks (Medium)

**Category**: Error paths
**Status**: OPEN

The `package-model.md` API endpoint spec defines error `422 PACKAGE_NOT_FOUND_IN_SMELT` ("SMELT returned no results for the given package name"), but `add_package_to_ticket()` in this spec does not mention this condition. It is ambiguous what happens when SMELT returns valid responses but with zero tracks: does a `TicketPackage` get created with no tracks? Is the bugowner still resolved? Is the `package_added` audit event still emitted?

### PKS-GAP-005 — Product-to-ProductRepository lookup location unspecified (Medium)

**Category**: Error paths
**Status**: OPEN

The `package-model.md` spec states: "If no matching product is found for a target, log a warning but do not fail." This rule is in `package-model.md` but not in `package-service.md`. If some products resolve and others don't, `add_package_records()` receives a partial `tracks` list. The spec does not clarify who is responsible for the product-to-`ProductRepository` lookup — `add_package_to_ticket()` (before the lock, consistent with I/O-then-Lock) or `add_package_records()` (inside the lock). The I/O-then-Lock invariant suggests the lookup must happen before the lock, but this is not stated.

### PKS-GAP-006 — Bugowner resolution and submission discovery failure behavior unspecified (Medium)

**Category**: Error paths
**Status**: OPEN

Steps 5 and 6 of `add_package_to_ticket()` involve external I/O (IBS API for bugowner, task enqueue for submission discovery). The spec does not specify what happens if bugowner resolution fails (IBS unreachable, package has no bugowner) or if the task enqueue fails (Redis unreachable). It is unclear whether these failures cause the entire operation to fail and rollback the record creation from step 4, or whether they are best-effort. If a bugowner resolution failure rolls back the transaction (including records created by `add_package_records()`), the VA would see the operation fail even though SMELT resolution and record creation succeeded.

### PKS-GAP-007 — add_package_records() with empty tracks list creates orphan (Medium)

**Category**: Boundary conditions
**Status**: OPEN

If `tracks` is an empty list, the function would create/skip a `TicketPackage` record, create a `package_added` audit event, and call `evaluate_ticket_status()`, but create zero track/product records. This produces a package with no tracks — which is immediately an orphan. The orphan cleanup invariant only triggers "on soft-deletion events," not on creation with zero children. The result is a `TicketPackage` that exists but has no active tracks, which is an inconsistent state that could block ticket progression.

### PKS-GAP-008 — Restore pre-checks from package-model.md not reflected in module spec (Medium)

**Category**: Boundary conditions
**Status**: OPEN

The `package-model.md` spec defines detailed restore pre-checks: restoring a package requires at least one track with `deleted_at IS NULL` that has at least one product with `deleted_at IS NULL`; restoring a track requires at least one product with `deleted_at IS NULL`. These pre-checks (returning `PACKAGE_RESTORE_BLOCKED`) are absent from the `package_service.md` restore function specs. An implementer following only the module spec would skip them, allowing restoration of packages with no active children.

### PKS-GAP-009 — TicketPackage creation in step 1 of add_package_to_ticket outside FOR UPDATE lock (Medium)

**Category**: Data lifecycle
**Status**: OPEN

Step 1 of `add_package_to_ticket()` creates a `TicketPackage` record, and step 4 delegates to `add_package_records()` which acquires the `FOR UPDATE` lock. The `TicketPackage` creation in step 1 happens before the lock is acquired. Two concurrent calls for the same ticket and package could both attempt to create the record, causing a unique constraint violation on `(ticket_id, package_name)`. The spec should clarify whether step 1's creation is moved inside `add_package_records()` (inside the lock) or whether the constraint violation should be caught as a no-op.

### PKS-GAP-010 — TrackData type not defined (Medium)

**Category**: Data lifecycle
**Status**: OPEN

The `TrackData` type is referenced in the `add_package_records()` parameter table but never defined. What fields does it contain? At minimum it must include `reference`, `workflow_type`, and a list of products. But the structure is unspecified. The I/O-then-Lock pattern implies all external resolution (SMELT, ProductRepository lookups) happens before `add_package_records()`, so `TrackData` must contain resolved data — but this is not stated. Without a definition, implementers must reverse-engineer the structure from the behavioral description.

### PKS-GAP-011 — Mutations on effectively-excluded records not explicitly permitted or denied (Low)

**Category**: Boundary conditions
**Status**: OPEN

The preconditions for `set_track_status()` check that the track itself is not soft-deleted and the ticket is not soft-deleted, but do not check whether the parent `TicketPackage` is soft-deleted. Per the hierarchical exclusion model, a track whose parent package is soft-deleted is "effectively excluded." The same gap exists for product-level mutations. Soft-deleted records "continue to receive updates" per `package-model.md`, so allowing mutations on effectively-excluded records may be intentional, but the spec should be explicit.

### PKS-GAP-012 — Delivery status regression audit event omitted (Low)

**Category**: State machine completeness
**Status**: OPEN

When delivery regresses from `IN_PROGRESS` to `PENDING` (all SRs revoked/declined), no `TicketAuditEvent` is created. The spec only documents that `RELEASED` generates an event and "intermediate" transitions do not. A regression signals a failed delivery attempt worth a VA's attention but produces no ticket-level audit trail. The omission is likely intentional (submission tracking handles this), but the gap means there is no ticket-level record for delivery regressions.

### PKS-GAP-013 — Eligibility calculation I/O location within lock not specified (Low)

**Category**: Configuration and defaults
**Status**: OPEN

When `add_package_records()` creates `TicketPackageProduct` records with parent status `AFFECTED`, it must calculate eligibility. This requires resolving the CVSS score and looking up product lifecycle data. The spec says the module "delegates CVSS resolution and eligibility calculation to pure functions in `cvss.py`" but does not specify whether these database reads happen inside the `FOR UPDATE` lock. The "pure functions" characterization of `cvss.py` strongly implies no external I/O, making this a minor documentation gap.
