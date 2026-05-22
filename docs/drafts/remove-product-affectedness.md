# Remove Product-Level Affectedness

## Summary

Remove the `status` and `is_status_override` columns from
`TicketPackageProduct`. Affectedness is tracked exclusively at the track
level. Products retain only eligibility (`eligible`, `is_eligible_override`)
and delivery confirmation (`released_at`).

## Rationale

Affectedness is a property of the **source code** relative to a CVE. A
track represents a single codestream (or git branch) — all products under
that track share the same source package. If the code is vulnerable, it is
vulnerable for every product served by the track. Tracking affectedness
per-product is a semantic duplication with no real informational gain.

The current model already acknowledges this: products inherit status from
their parent track, and overrides are the exception. In practice, the
override mechanism has no legitimate use case that cannot be covered by:

- `eligible = false` (override) — product should not receive the fix
- Soft-delete — product is spurious and should not exist under this track

### Benefits

1. **Simpler data model**: 2 fewer columns on `TicketPackageProduct`
2. **No propagation logic**: eliminates the track → product status
   propagation code and its edge cases
3. **Cleaner orthogonality**: each dimension lives at exactly one level:
   - Track: affectedness + delivery status
   - Product: eligibility + release confirmation
4. **Simpler Resolved gate**: condition becomes "every active track has a
   final status AND every eligible product under a FIXED track has
   `released_at IS NOT NULL`" — which is already how it reads today, but
   without needing to check product-level status
5. **Reduced audit event surface**: no more `product_status_overridden`
   events

### Resolved: No Real Edge Case

The question "is there a scenario where a VA needs per-product
affectedness?" was evaluated and the answer is **no**:

- All products under a track share the same source package from the same
  codestream. If the code is vulnerable, it is vulnerable for all products.
- The scenario where a product ships a pre-existing fix is captured by
  `released_at` (delivery confirmation), not by a status override.
- The scenario where a product should not receive the fix is captured by
  `eligible = false` (override).
- The scenario where a product is spurious is captured by soft-delete.

No legitimate case requires per-product affectedness that is not already
covered by eligibility or soft-delete.

### Migration Impact

The system is in specification/development phase — no production data
exists. The migration is a simple column drop with no data preservation
concerns.

### API Breaking Change

This change removes `status` and `is_status_override` from all
`ProductDetail` response schemas and simplifies the product override
endpoint to eligibility-only. Since the system is in
specification/development phase with no external API consumers, no
deprecation period or API versioning is needed.

---

## Current State (before change)

### TicketPackageProduct columns (affectedness-related)

| Column | Type | Purpose |
|--------|------|---------|
| `status` | PackageStatus | Effective affectedness (inherited or overridden) |
| `is_status_override` | BOOLEAN | True if VA manually set the product status |

### Logic that uses product-level status

1. **Status propagation** (`package_service`): when VA sets track status,
   propagate to products where `is_status_override = false`
2. **VA product override** (API): VA can set status on individual products
3. **Resolved gate**: checks that tracks have final status (product status
   is not directly checked in the gate condition)
4. **Anomaly detection** (future): could theoretically flag product-level
   anomalies, but no spec exists for this
5. **API responses**: product status is returned in track/product listings

### Logic that uses product-level `ANALYSIS` status

6. **Analyzed gate** (`tickets.md` condition #3): checks that no active
   product has `ANALYSIS` status — this condition is removed (redundant
   with the track-level check since products always inherit track status)

### Specifications that reference product-level status

- `docs/features/packages/package-model.md` — defines the column, propagation
  rules, override model, automatic transitions, table description text
  (~line 262), Package Management Constraints (~line 970), Design Rationale
  (~line 68), Workflow-Agnostic section (~line 1117), and the "Override
  Product Status" endpoint spec (~line 1585)
- `docs/features/tickets/tickets.md` — Analyzed gate condition #3, Resolved
  gate conditions, reverse transition description, `ProductDetail` response
  sub-schema (~line 1109: includes `status` and `is_status_override` fields)
- `docs/features/tickets/ticket-audit-log.md` — audit event types for
  product status changes (`product_status_overridden`)
- `docs/features/tickets/ticket-mutations.md` — lists `set_product_status`
  in module boundary and caller table, contract section (~line 624:
  "TicketPackageProduct status"), relationship summary (~line 74)
- `docs/features/packages/package-service.md` — service functions for
  status propagation and product override, Record Creation Logic section
  (~line 741: new products inherit status from parent track), callers table
  entry for IBS product release detection (~line 800: uses
  `set_product_status()` for `released_at`)
- `docs/features/identity/rbac.md` — permission matrix references
  "Change track/product status", endpoint permission map entry
- `docs/features/packages/product-lifecycle-transitions.md` — EOL handling
  table (~line 98) scopes actions by product `status` (`AFFECTED` or
  `ANALYSIS`); eligibility-only reference (~line 91)
- `docs/data-model.md` — schema definition, Mermaid ER diagram (~line 170:
  shows `status` and `is_status_override` on TicketPackageProduct), Ticket
  table status transitions summary (~line 762: "no track or product records
  in ANALYSIS"), `PackageStatus` enum description (~line 559: "used by both
  TicketPackageTrack and TicketPackageProduct"), `TicketAuditEventType` enum
  (`product_status_overridden` value)
- `docs/architecture.md` — Package Affectedness Flow (~line 230: "Sentinel
  propagates track status to products", "VA can override individual product
  statuses when needed")
- `docs/system-map.md` — Mermaid ER diagram (~line 154: `TicketPackageProduct`
  with `ENUM status` and `BOOLEAN is_status_override`), Status Propagation
  subgraph (~line 369: "Products inherit codestream status", "VA overrides
  product status")
- `docs/features/packages/ibs-submission-tracking.md` — references
  PackageStatus being "shared between track and product levels" (~line 102)

---

## Target State (after change)

### TicketPackageProduct columns (simplified)

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID | PK |
| `ticket_package_track_id` | UUID | FK to parent track |
| `product_id` | UUID | FK to product |
| `eligible` | BOOLEAN | Effective eligibility |
| `is_eligible_override` | BOOLEAN | True if VA manually set eligibility |
| `released_at` | TIMESTAMPTZ | When fix was detected in product repo |
| `deleted_at` | TIMESTAMPTZ | Soft-deletion |
| `created_at` | TIMESTAMPTZ | Record creation |
| `updated_at` | TIMESTAMPTZ | Record update |

### Analyzed gate (simplified)

Current condition #3 ("All product affectedness decided: no active
`TicketPackageProduct` records in `ANALYSIS` status") is **removed**.
Since products no longer have a status column, they cannot be "undecided."
Affectedness analysis completeness is determined exclusively by tracks.

After the change, the Analyzed gate conditions are:

1. At least one active `TicketPackageTrack` exists
2. All active tracks have a non-`ANALYSIS` status
3. Severity is set
4. SUSE CVSS provided (for tickets with CVE)

This is a simplification, not a semantic change: under the current model,
products always inherit the track status on propagation — a product can
only be in `ANALYSIS` if its parent track is also in `ANALYSIS` (unless
the VA overrides it, which we are removing). So condition #3 was already
redundant with condition #2 for all non-override cases.

### Resolved gate (unchanged semantics)

1. Every active `TicketPackageTrack` has a final status
2. Every eligible product (`eligible = true`) under a `FIXED` track has
   `released_at IS NOT NULL`

### What products represent after the change

A `TicketPackageProduct` answers two questions only:

1. **Eligibility**: does this product meet the criteria to receive the
   fix? (CVSS threshold + lifecycle phase)
2. **Delivery confirmation**: has the fix actually arrived in the product's
   update repository?

The affectedness question ("is the code vulnerable?") is answered
exclusively at the track level, because that is where the source code
lives.

`released_at` is irreversible once set — it records a factual observation
(advisory present in `updateinfo.xml`) that cannot be undone. If an
advisory is misidentified, the correct resolution is to soft-delete the
product record.

### Edge cases and confirmations

1. **Analyzed gate redundancy**: condition #3 ("no active
   `TicketPackageProduct` records in `ANALYSIS` status") is not redundant
   in the current model — it is made redundant by the simultaneous
   removal of the product status override mechanism. Both changes
   (condition removal + override removal) MUST be applied together.

2. **EOL handling join**: the EOL action (soft-delete) is unchanged —
   only the query filter changes. Products whose parent track has status
   `AFFECTED` or `ANALYSIS` are soft-deleted. Products under
   final-status tracks are not affected (the filter naturally excludes
   them).

3. **All-ineligible products under FIXED track**: a `FIXED` track with
   zero eligible products satisfies the Resolved gate vacuously. This is
   correct — there is nothing to wait for.

4. **New product record creation**: new `TicketPackageProduct` records
   are created with `released_at = NULL`, eligibility calculated per
   standard rules, and no status field. No status inheritance from parent
   track.

5. **Audit trail coverage**: all remaining product mutation paths have
   corresponding audit event types: eligibility override
   (`product_eligibility_changed`), release confirmation
   (`product_released`), soft-delete (`product_excluded`), restore
   (`product_restored`).

6. **Concurrency model**: unchanged — both remaining product mutation
   paths (`set_product_eligibility`, `set_product_released_at`) follow
   the existing `FOR UPDATE` locking pattern on the parent Ticket row.

### Override Product endpoint (renamed)

The current "Override Product Status" endpoint
(`PATCH .../products/{product_id}`) is **renamed** to "Override Product
Eligibility". The URL path is unchanged (it is already generic). Changes:

- Section header: "Override Product Status" → "Override Product Eligibility"
- Anchor: `#override-product-status` → `#override-product-eligibility`
- Request body: only `eligible` field (remove `status`)
- Validation: `eligible` is required (no longer "at least one of status
  or eligible")
- Reset behavior: remove `status: null` case; only `eligible: null` remains
- Response: remove `status` and `is_status_override` fields
- `rbac.md` endpoint permission map: update anchor link to
  `#override-product-eligibility`

### `set_product_released_at()` function (new)

After removing `set_product_status()`, its current caller for
`released_at` updates (IBS product release detection) needs a replacement.
A dedicated `set_product_released_at()` function must be defined in
`package_service` to handle product release confirmation. Full contract:

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `product_id` | `UUID` | Yes | TicketPackageProduct to modify |
| `released_at` | `datetime` | Yes | Advisory issued date (UTC) |
| `advisory_id` | `str` | Yes | Advisory identifier (e.g., `SUSE-SU-2025:1234-1`) |

**Preconditions**: none — release detection applies regardless of
soft-deletion status or parent track status (it is a factual observation).

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Load the product record (no `deleted_at` filter — soft-deleted
   products are included)
3. If `released_at` is already set, return (no-op — release confirmation
   is irreversible; see Irreversibility below)
4. Set `TicketPackageProduct.released_at` to the provided value
5. Create `TicketAuditEvent` (`product_released`, `user_id = NULL`)
   with detail: `{"track": "...", "package": "...", "product_id": "...",
   "advisory_id": "..."}`
6. Call `evaluate_ticket_status()`
7. Return updated product

**TicketAuditEvent**: `product_released`

**Idempotency**: no-op if `released_at` is already set (step 3).

**Irreversibility**: once set, `released_at` cannot be cleared or
modified. An advisory present in `updateinfo.xml` is a factual
observation — it cannot be "un-published". If an advisory is
misidentified (wrong source package match), the correct resolution is
to soft-delete the product record, not to clear `released_at`.

**Callers**: IBS product release detection tasks only
(`acting_user_id` is always `None`; auto-assignment does not apply).

---

## Implementation Plan

### Phase 1: Specification Updates

1. **`docs/features/packages/package-model.md`**:
   - Remove `status` and `is_status_override` from TicketPackageProduct
     table definition
   - Update table description text (~line 262): remove "Status is inherited
     from the parent TicketPackageTrack" language
   - Update Design Rationale (~line 68): remove "status propagation
     unidirectional (track -> product)" reference
   - Remove "VA Overrides a Product Status" section
   - Simplify "VA Sets a Status on a Track" (remove propagation to
     products and eligibility recalculation — eligibility inputs are
     independent of track status; see GAP-12 resolution below)
   - Update "Three Orthogonal Dimensions" — Axis 1 is per-track only;
     update status classification text that says "no further work is
     expected on the track **or product**"
   - Update automatic transitions table (remove product propagation row)
   - Update soft-deletion section: remove "Status propagation (track ->
     product)" from Continued Updates list (~line 738) and
     Workflow-Agnostic section (~line 1117)
   - Update "Status Behavior" section throughout
   - Update Override Model table (remove product status rows)
   - Update Package Management Constraints (~line 970): "override the
     status and eligibility" → "override the eligibility"
   - Rename "Override Product Status" endpoint to "Override Product
     Eligibility" (anchor: `#override-product-eligibility`); simplify
     request/response schemas to eligibility-only
   - Update Change Track Status response (~line 1546): remove `status`
     and `is_status_override` from product objects in response

2. **`docs/features/tickets/tickets.md`**:
   - Remove Analyzed gate condition #3 ("All product affectedness decided")
   - Update reverse transition description (remove "products in ANALYSIS"
     reference)
   - Update `ProductDetail` response sub-schema (~line 1109): remove
     `status` and `is_status_override` fields

3. **`docs/features/tickets/ticket-audit-log.md`**:
   - Remove `product_status_overridden` event type
   - Remove associated detail JSONB schema entry

4. **`docs/features/tickets/ticket-mutations.md`**:
   - Remove `set_product_status` from module boundary table
   - Remove from caller table and cross-references
   - Update contract section (~line 624): remove "TicketPackageProduct
     status" from the package_service scope description
   - Update relationship summary (~line 74): remove "product status"
     from the list of package-centric mutations — update to "track
     status, delivery status, eligibility"
   - Update narrative text (~line 250): remove `set_product_status` from
     the list of functions moved to `package_service`

5. **`docs/features/packages/package-service.md`**:
   - Remove `set_product_status()` function definition
   - Add `set_product_released_at()` function definition (for IBS product
     release detection to set `released_at`)
   - Simplify `set_track_status()` step 7: remove the entire step
     (status propagation AND eligibility recalculation). Eligibility
     depends on CVSS score, product CVSS threshold, and product lifecycle
     phase — none of which change when a track status changes. Eligibility
     has its own dedicated triggers (CVSS assessment changes, AIMAAS
     threshold changes, product lifecycle transitions). Retaining a
     provably no-op recalculation would suggest a cross-dimensional
     dependency that does not exist (Guardrail 24)
   - Update Record Creation Logic (~line 741): remove product status
     inheritance from parent track at creation time
   - Update callers table (~line 796): replace `set_product_status()` with
     `set_product_released_at()` for IBS product release detection entry
   - Update function inventory and caller tables

6. **`docs/features/identity/rbac.md`**:
   - Update permission matrix: "Change track/product status" becomes
     "Change track status"
   - Update VA prose (~line 37): "Change track and product affectedness
     status" becomes "Change track affectedness status"
   - Update endpoint permission map: keep the PATCH product endpoint row
     but update the anchor link from `#override-product-status` to
     `#override-product-eligibility`

7. **`docs/features/packages/product-lifecycle-transitions.md`**:
   - Rewrite EOL handling table (~line 98): replace product `status`
     checks (`AFFECTED` or `ANALYSIS`) with parent **track** status
     checks. The logic changes from "query TicketPackageProduct records
     with status AFFECTED or ANALYSIS" to "query TicketPackageProduct
     records whose parent TicketPackageTrack has status AFFECTED or
     ANALYSIS"
   - Update eligibility-only reference (~line 91): remove "the product's
     affectedness status is not modified" (no longer applicable)

8. **`docs/data-model.md`**:
   - Remove `status` and `is_status_override` from TicketPackageProduct
     table definition
   - Update Mermaid ER diagram (~line 170): remove `ENUM status` and
     `BOOLEAN is_status_override` from TicketPackageProduct entity
   - Update Ticket table status transitions summary (~line 762): remove
     "or product records" from "no track or product records in ANALYSIS"
   - Update `PackageStatus` enum description (~line 559): change "used by
     both TicketPackageTrack and TicketPackageProduct" to "used by
     TicketPackageTrack"
   - Remove `product_status_overridden` from `TicketAuditEventType` enum

9. **`docs/architecture.md`**:
   - Update Package Affectedness Flow (~line 230): remove "Sentinel
     propagates track status to products. Products inherit the track
     status directly" and "VA can override individual product statuses
     when needed"
   - Replace with simplified description: products track only eligibility
     and delivery confirmation

10. **`docs/system-map.md`**:
    - Update Mermaid ER diagram (~line 154): remove `ENUM status` and
      `BOOLEAN is_status_override` from TicketPackageProduct
    - Update Status Propagation subgraph (~line 369): remove "Products
      inherit codestream status" and "VA overrides product status"

11. **`docs/features/packages/ibs-submission-tracking.md`**:
    - Update reference (~line 102): remove "shared between track and
      product levels" language about PackageStatus

12. **`docs/api-spec.md`** (if affected):
    - Update TicketPackageProduct response schema (if defined here)

13. **`docs/features/packages/ibs-track-release-detection.md`**:
    - Verify no references to product-level status transitions

14. **`docs/features/packages/ibs-product-release-detection.md`**:
    - Remove "The product's affectedness status is NOT changed" (~line 64)
      — no longer applicable since products have no status

15. **`docs/features/packages/README.md`**:
    - Update Relationships section (~line 34): "track/product status,
      delivery, eligibility" becomes "track status, delivery, product
      eligibility"

### Phase 2: Implementation

16. **SQLAlchemy model** (`backend/app/models/`):
    - Remove `status` and `is_status_override` from TicketPackageProduct

17. **Alembic migration**:
    - Drop columns `status` and `is_status_override` from
      `ticket_package_product` table
    - Remove `product_status_overridden` from `TicketAuditEventType` enum

18. **Pydantic schemas** (`backend/app/schemas/`):
    - Remove `status` and `is_status_override` from product response/update
      schemas

19. **Service layer** (`backend/app/services/package_service.py`):
    - Remove product status propagation from `set_track_status`
    - Remove `set_product_status` function
    - Add `set_product_released_at` function
    - Remove product status inheritance from record creation logic
    - Simplify related helpers

20. **API endpoints** (`backend/app/api/v1/`):
    - Simplify product override endpoint to eligibility-only
    - Update response serialization (remove status fields from products)

21. **Celery tasks** (`backend/app/tasks/`):
    - Update IBS product release detection to use
      `set_product_released_at()` instead of `set_product_status()`
    - Verify no other task sets product-level status directly

22. **Tests** (`backend/tests/`):
    - Remove tests for product status propagation
    - Remove tests for product status override
    - Add tests for `set_product_released_at`
    - Update Resolved gate tests (should pass unchanged)
    - Update Analyzed gate tests (condition #3 removed)

### Phase 3: Frontend

23. **UI components**:
    - Remove product status display/badge from product rows
    - Remove product status override controls
    - Update product listing to show only eligibility + released_at

### Phase 4: Finalization

24. **Run reviewers** on updated specifications:
    - `@spec-coherence-reviewer` on modified specs
    - `@data-model-reviewer` on data model changes
    - `@spec-gap-analyzer` on `package-model.md`
    - `@docs-reviewer` for documentation completeness
    - `@api-convention-reviewer` if API endpoints are modified
    - `@api-parity-reviewer` for API-UI consistency

25. **Cleanup**:
    - Review findings in `docs/reviews/package-service.md` that reference
      `set_product_status()` or product status propagation (findings
      PKS-GAP-03, PKS-COH-01, PKS-COH-04, PKS-COH-05, PKS-DES-06) —
      mark as resolved or remove
    - Delete this draft file once all changes are applied and reviewed
