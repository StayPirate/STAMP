# Draft: Extract `ticket-mutations.md` from `tickets.md`

## Status

**Draft v2** — incorporates findings from design review, docs placement
review, and spec coherence review. Subject to approval before execution.

## Motivation

The `ticket_mutations` module is already recognized as architecturally
distinct (AGENTS.md Guardrail 16, referenced extensively in `tickets.md`).
However, its service-layer contract — function signatures, concurrency
rules, orphan invariants, and transaction requirements — is currently
scattered across ~550 lines within a 2,013-line monolithic feature spec.

The identity domain demonstrates a proven separation pattern:

- `user-management.md` + `rbac.md` = feature behavior (the **what**)
- `user-service.md` (725 lines) = service contract (the **how**)

The ticket domain lacks this separation. Extracting a dedicated
`ticket-mutations.md` gives implementers of
`backend/app/services/ticket_mutations.py` a single authoritative
reference — the same way `user-service.md` serves implementers of
`user_service.py`.

## Guiding Principle

- **`tickets.md`** = the **what** and **why** (feature behavior,
  lifecycle, API endpoints, authorization rules)
- **`ticket-mutations.md`** = the **how** at the module level (function
  contracts, concurrency, locking, orphan invariants, test requirements)

The gate *conditions* (what must be true for a status transition) remain
in `tickets.md` as feature-level requirements. The *evaluation logic*
(how `evaluate_ticket_status` checks those conditions) moves to
`ticket-mutations.md`.

## Scope of Extraction

### What Moves to `ticket-mutations.md`

The following sections of the current `tickets.md` contain service-layer
contract material that targets implementers of the `ticket_mutations`
module:

| Section in `tickets.md` | Lines | New Location |
|---|---|---|
| Centralized Status Evaluation (intro + Behavior) | 366-393 | `evaluate_ticket_status()` function contract |
| Inactive Assignee Sanitization | 395-421 | Sub-section of `evaluate_ticket_status()` |
| Gates (evaluation logic — how the function checks) | 424-439 | Sub-section of `evaluate_ticket_status()` |
| Scope (gate zone vs manual zone, `_reenter_gate_zone`) | 441-461 | State Machine Zones |
| Ticket Mutations Module (description, categories, relationships) | 463-543 | Purpose + Architecture + operation list |
| Concurrency Control (ticket-specific rules only) | 544-597 | Dedicated section (references `conventions.md` for generic pattern) |
| Orphan Cleanup Invariants | 599-649 | Dedicated section |
| Contract | 651-668 | Dedicated section |
| Architectural Test Requirement | 670-688 | Dedicated section |
| Auto-Assignment on Unassigned Tickets (implementation) | 717-737 | Cross-cutting module rule |
| Canonical Target Resolver (function contract only) | 754-778 | Utility function contract |
| Revert-Duplicate Operation (function steps) | 863-884 | Manual-zone exit function |
| `reopen_from_ignored()` steps | 1025-1034 | Manual-zone exit function |

**Approximate content extracted:** ~550 lines from `tickets.md`.

**`ticket-mutations.md` estimated size:** ~850-1000 lines (extracted
content + new function contracts + Related Operations section).

### What Stays in `tickets.md`

Everything else, with cross-references added to `ticket-mutations.md`:

- Ticket Identification (lines 14-52)
- CVE Association (lines 53-142)
- Ticket Creation (lines 143-204)
- Severity Resolution (lines 206-229) — feature-level concept, brief
- Ticket Lifecycle: statuses, transition table, gate **conditions**
  (lines 231-340)
- Automatic Status Re-evaluation (lines 341-364) — merged with the
  Centralized Status Evaluation summary (see Changes section below)
- Reassignment (lines 690-716)
- Duplicate Handling: terminology, mark-as-duplicate operation steps,
  cascade behavior, API response behavior, cycle prevention, correctness
  guarantee, **and** the "who must use the resolver / who is exempt"
  rules (lines 779-791) — these are feature-level architectural
  decisions about when to resolve vs. read raw `duplicate_of_id`
- Soft-Delete, Status Categories, Terminal Statuses, Mutability Guard
  (lines 962-1083)
- Tickets Without CVE (lines 1085-1104)
- Confidential Tickets (lines 1106-1253)
- API Endpoints (lines 1254-1969)
- Data Model, Security, Cross-references (lines 1971-2013)

**`tickets.md` resulting size:** ~1,350-1,450 lines (down from 2,013).

### What Does NOT Move (and why)

| Content | Reason it stays in `tickets.md` |
|---|---|
| Gate conditions (Analysis → Analyzed, Analyzed → Resolved) | Feature-level requirements — define *what* must be true |
| Mark-as-duplicate operation steps | Not a `ticket_mutations` function — directly sets `duplicate_of_id` and `status = Duplicated` without gate evaluation |
| Cascade as Best-Effort Flattening | Correctness property of the duplicate feature, not a module contract |
| Cycle Prevention | System invariant of the duplicate feature |
| Mutability Guard (`require_ticket_mutable`) | FastAPI dependency, not a module function |
| Severity Resolution rules | Feature-level concept (which value wins) vs. service logic |
| CVE Dissociation steps | Modifies `Ticket.cve_id` (not gate-relevant data); the side effect on severity triggers gates. Documented as a "Related Operation" in `ticket-mutations.md` for concurrency visibility |
| Canonical Target Resolver usage rules (lines 779-791) | Feature-level architectural decisions: "who must use the resolver" and "who is exempt from resolution" — these are API contract rules, not module internals |

### Boundary Clarification: mark-as-duplicate

The mark-as-duplicate operation is **not** a `ticket_mutations` function
because:

1. It sets `status = Duplicated` directly (entering the manual zone),
   not via gate evaluation
2. It does NOT call `evaluate_ticket_status`
3. The module's scope (per the existing contract in `tickets.md`) is
   "operations that modify data relevant to ticket status gates" —
   mark-as-duplicate bypasses the gates entirely

However, the mark-as-duplicate operation *uses* `resolve_canonical_target()`
(which IS a public function in `ticket_mutations`) and the cascade *uses*
the FOR UPDATE pattern. The feature behavior stays in `tickets.md`; the
utility function contract moves to `ticket-mutations.md`.

Similarly, `revert_duplicate()` IS a `ticket_mutations` function because
it calls `_reenter_gate_zone()` which calls `evaluate_ticket_status`.

## Proposed Structure for `ticket-mutations.md`

```markdown
# Ticket Mutations Service

## Purpose
  (Why centralization exists, what it prevents)

## Architecture
  ### Module location
  ### Async pattern
  ### Transaction ownership
  ### Relationship with other modules
    (cvss.py, add_package_to_ticket, etc.)

## State Machine Zones
  ### Gate zone (New, Analysis, Analyzed, Resolved)
  ### Manual zone (Ignored, Duplicated)
  ### _reenter_gate_zone() (private helper)

## evaluate_ticket_status()
  ### Parameters
  ### Behavior (top-down evaluation)
  ### Inactive Assignee Sanitization
  ### previous_status parameter
  ### Multiple invocations within a transaction

## Concurrency Control
  (References conventions.md for generic pattern; documents only
   ticket-specific refinements: single-ticket scope, blocking wait,
   ticket-not-found handling, evaluate_ticket_status does not acquire
   lock)

## Gate-Relevant Mutation Operations
  ### set_track_status()
  ### set_track_delivery_status()
  ### set_product_status()
  ### set_product_eligibility()
  ### create_cvss_assessment()
  ### update_cvss_assessment()
  ### delete_cvss_assessment()
  ### set_severity_override()
  ### add_package_records()
  ### soft_delete_ticket_package()
  ### soft_delete_ticket_package_track()
  ### soft_delete_ticket_package_product()
  ### restore_ticket_package()
  ### restore_ticket_package_track()
  ### restore_ticket_package_product()

  (Each with: Parameters table, Preconditions, Behavior steps,
   TicketAuditEvent, Idempotency)

## Manual-Zone Exit Operations
  ### reopen_from_ignored()
  ### revert_duplicate()

## Utility Functions
  ### resolve_canonical_target()

## Auto-Assignment Rule
  (Cross-cutting behavior: when any VA-initiated mutation targets an
   unassigned ticket, auto-assign first)

## Orphan Cleanup Invariants
  (Track orphan rule, package orphan rule, cascading composition)

## Record Creation Logic
  (Initial status inheritance for new tracks and products)

## Idempotency
  (Record creation idempotency for add_package_records)

## Related Operations
  (Operations that follow the module's concurrency pattern — FOR UPDATE
   + evaluate_ticket_status — but are not module functions:
   CVE dissociation, ticket soft-delete/restore, mark-as-duplicate,
   set-confidentiality. Brief description of each with cross-reference
   to tickets.md for full steps.)

## Contract
  (The binding rule: all gate-relevant mutations MUST go through this
   module)

## Architectural Test Requirement
  (Parametrized integration test covering forward/backward/no-op/edge)

## Service Exceptions
  (Typed exception table)

## Callers
  (By category — not per-endpoint. See rationale below.)

## Cross-references
```

### Key Structural Decisions (from review)

1. **State Machine Zones before `evaluate_ticket_status()`**: establishes
   foundational concepts (gate zone, manual zone, `_reenter_gate_zone`)
   before the function that depends on them. Follows the same top-down
   conceptual ordering that `user-service.md` uses.

2. **Related Operations section**: documents CVE dissociation, ticket
   soft-delete/restore, mark-as-duplicate, and set-confidentiality as
   operations that share the module's concurrency pattern (FOR UPDATE on
   the Ticket row + `evaluate_ticket_status` call where applicable) but
   are NOT routed through the module. This gives implementers a single
   document covering the full FOR UPDATE landscape for the Ticket entity.
   (~30 lines, cross-references `tickets.md` for full behavioral steps.)

3. **`soft_delete_ticket_package()` and `restore_ticket_package()`
   included**: the existing contract in `tickets.md` (lines 506-509)
   states that "package soft-deletion delegates record updates to the
   module." The `package_excluded` and `package_restored` audit event
   types exist in the data model. These functions are part of the module
   contract and must not be lost during extraction.

4. **Explicit transaction ownership statement**: "The module does NOT
   commit or roll back. All operations execute within the caller's
   database session. Commit responsibility belongs to the caller." —
   matching the `api-key-service.md` pattern.

5. **Multiple invocations guidance**: `evaluate_ticket_status()` may be
   called multiple times in a single transaction during orphan cascades
   (up to 3 times: product → track → package). The function is
   idempotent. Each call ensures consistent state. Implementations MUST
   NOT defer or skip intermediate calls for optimization.

## New Content (Not Just Relocation)

The extraction is not pure copy-paste. The following content will be
**newly structured** based on existing descriptions:

### 1. Function Contracts with Parameter Tables

Currently `tickets.md` describes mutation categories as a bulleted list
(lines 469-477). The new spec will expand each into a proper function
contract following the `user-service.md` pattern:

```markdown
### set_track_status()

Sets the affectedness status of a `TicketPackageTrack` record.

**Parameters**:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `track_id` | `UUID` | Yes | TicketPackageTrack to modify |
| `status` | `PackageStatus` | Yes | New status value |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:
- Track must exist and have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be mutable (not Ignored or Duplicated)
- Status must be a valid PackageStatus value

**Behavior**:
1. Acquire FOR UPDATE on the parent Ticket row
2. Validate preconditions
3. If status unchanged, return (no-op)
4. Update `TicketPackageTrack.status`
5. If new status is AFFECTED: propagate to products (see package-tracking.md)
6. Create TicketAuditEvent (track_status_changed)
7. Call evaluate_ticket_status()
8. Return updated track

**TicketAuditEvent**: `track_status_changed`
```

This pattern will be applied to all ~15 public functions (13 gate-relevant
+ 2 manual-zone exits).

### 2. Architecture Section

New section modeled after `user-service.md` lines 16-52:

- Module location: `backend/app/services/ticket_mutations.py`
- Async pattern: async functions, same as user_service
- Transaction ownership: the module does NOT commit or roll back. All
  operations execute within the caller's database session. Commit
  responsibility belongs to the caller
- Relationship with `services/cvss.py`: delegates CVSS resolution and
  severity calculation to pure functions
- Relationship with `add_package_to_ticket`: accepts delegation for
  record creation (SMELT I/O happens before the lock)

### 3. Service Exceptions Table

Currently implicit in the text. New explicit table:

| Exception | Raised when |
|---|---|
| `TicketNotFoundError` | FOR UPDATE returns no row |
| `TicketNotMutableError` | Ticket is in manual zone (defense in depth) |
| `TicketSoftDeletedError` | Ticket has `deleted_at IS NOT NULL` |
| `TrackNotFoundError` | Track ID does not exist or is soft-deleted |
| `ProductNotFoundError` | Product ID does not exist or is soft-deleted |
| `PackageNotFoundError` | Package ID does not exist or is soft-deleted |
| `DuplicateCycleDetectedError` | Resolver detects a cycle in the chain |
| `DuplicateChainDepthError` | Resolver exceeds 50-hop limit |

### 4. Callers Table (by category)

The callers table is scoped to **operation categories** rather than
individual endpoints, because the module has 15+ functions with
potentially dozens of callers. Maintaining an exhaustive per-endpoint
table is unrealistic and would rot. This follows the same principle as
`api-key-service.md` but adapted for scale.

| Caller Category | Operations Used | Context |
|---|---|---|
| Ticket API mutation endpoints | All gate-relevant + manual-zone exits | VA-initiated operations |
| CVSS sync fetcher | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | Background CVSS ingestion |
| IBS track release detection | `set_track_status()`, `set_track_delivery_status()` | Automated track release |
| IBS product release detection | `set_product_status()` (released_at) | Automated product release |
| `add_package_to_ticket` | `add_package_records()` | Package addition flow |
| Product lifecycle transitions | `set_product_eligibility()`, `soft_delete_ticket_package_product()` | AIMAAS threshold changes |
| NVD rejection handling | `reopen_from_ignored()` | CVE rejection revert |
| Admin: default CVSS version change | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | Re-evaluation triggered by config change |
| User deactivation side effects | (none — deactivation unassigns via direct query, not through ticket_mutations) | Clarification: NOT a caller |

### 5. Concurrency Control (ticket-specific only)

The generic pessimistic locking pattern and transaction hygiene rules are
already defined in `docs/conventions.md` (Transaction and Locking). The
new `ticket-mutations.md` section will:

1. **Reference** `conventions.md` for the generic pattern (FOR UPDATE as
   first DB operation, no external I/O inside lock)
2. **Document only ticket-specific refinements**:
   - Extension to non-module operations (any service modifying the Ticket
     row must acquire FOR UPDATE — not just module functions)
   - Single-ticket scope: never acquire FOR UPDATE on multiple tickets in
     the same transaction (deadlock prevention)
   - Blocking wait rationale: no NOWAIT — locks are held for milliseconds
   - Ticket-not-found handling: raise domain exception, never proceed on
     `None`
   - `evaluate_ticket_status` does not acquire the lock — always the
     caller's responsibility

This avoids duplicating `conventions.md` content while documenting the
ticket-specific decisions that don't belong in a cross-cutting document.

### 6. `set_severity_override()` — Conditional Gate Relevance

This function has conditional behavior:

- When `cve_id IS NULL`: setting `severity_override` affects the ticket's
  resolved severity, which is gate-relevant (Analyzed gate #4 requires
  severity). `evaluate_ticket_status()` is always called after the update.
- When `cve_id IS NOT NULL`: severity is derived from CVSS, and
  `severity_override` is ignored. The API endpoint rejects the operation
  with `TICKET_SEVERITY_DERIVED` (400) — this check is at the **API
  layer** (endpoint handler), not in the module function itself.

The module function always calls `evaluate_ticket_status()` regardless
(it's cheap and maintains the invariant). The conditional behavior is
documented in the function contract's preconditions/behavior.

## Changes Required in `tickets.md`

### Sections to Replace with Summaries

1. **Centralized Status Evaluation** (lines 366-688) — merged with
   "Automatic Status Re-evaluation" (lines 341-364) into a single
   section:

   ```markdown
   ### Automatic Status Evaluation

   Forward and reverse transitions between Analysis, Analyzed, and
   Resolved are governed by a single mechanism: the centralized status
   evaluation function (`evaluate_ticket_status`) in the
   `ticket_mutations` module. This function re-evaluates gate conditions
   after every relevant data change and sets the ticket to the highest
   valid status. It is the sole authority for gate-zone status.

   - If all "Resolved" AND "Analyzed" gates are met → Resolved
   - If all "Analyzed" gates are met → Analyzed
   - If the "Analysis" gate is met → Analysis
   - Otherwise → New

   Reverse transitions are not special cases — they emerge naturally
   when gate conditions are no longer met.

   All automatic transitions create a `TicketAuditEvent` with
   `user_id = NULL` (system action), even when the underlying data
   change was initiated by a VA.

   See `docs/features/tickets/ticket-mutations.md` for the full function
   contract, inactive assignee sanitization, concurrency control rules,
   orphan cleanup invariants, and architectural test requirements.
   ```

   This merges the current "Automatic Status Re-evaluation" section with
   the summary of "Centralized Status Evaluation" to avoid redundancy.
   The merged section retains the `#automatic-status-evaluation` anchor.
   A redirect comment or anchor alias for `#centralized-status-evaluation`
   should be added to avoid breaking internal links.

2. **Auto-Assignment on Unassigned Tickets** (lines 717-737) — keep the
   full behavioral description (what the user experiences), add reference:

   ```markdown
   This rule is enforced by the `ticket_mutations` module — see
   `docs/features/tickets/ticket-mutations.md` (Auto-Assignment Rule)
   for implementation details.
   ```

3. **Canonical Target Resolver** (lines 754-791) — replace the function
   contract but **retain** the usage rules:

   ```markdown
   #### Canonical Target Resolver

   A centralized public function `resolve_canonical_target` in the
   `ticket_mutations` module follows the `duplicate_of_id` chain to
   find the non-Duplicated canonical target. See
   `docs/features/tickets/ticket-mutations.md` for the full function
   contract (parameters, hop limit, cycle detection, error codes).

   All code paths that need the canonical target MUST use this function:
   - `mark-as-duplicate` operation (pre-write validation)
   - API response serialization (see API Response Behavior)
   - Any future logic that reads `duplicate_of_id` for decision-making

   Direct reads of `duplicate_of_id` without resolution are only
   permitted for:
   - Audit event recording (`old_value`/`new_value` store the raw
     `SNTL-{n}` at the time of the event)
   - Database-level queries that need the raw FK (e.g., finding all
     tickets whose `duplicate_of_id` points to a specific ticket)
   ```

   The "who must use it" and "who is exempt" lists stay in `tickets.md`
   because they are feature-level architectural decisions about the API
   contract, not module implementation details.

4. **Revert-Duplicate steps** (lines 863-884) — replace the step-by-step
   procedure with:

   ```markdown
   When reverting a ticket from Duplicated status
   (`ticket_mutations.revert_duplicate()`):

   - `duplicate_of_id` is cleared (set to NULL)
   - The ticket is reassigned to the VA who performed the revert
   - The ticket re-enters the gate zone; `evaluate_ticket_status`
     determines the correct status based on current gate conditions
   - Creates two `TicketAuditEvent` records: `duplicate_removed` (user
     action) + `status_change` (system action)

   See `docs/features/tickets/ticket-mutations.md` for the full function
   contract.
   ```

   The behavioral outcome stays; the implementation steps (`_reenter_gate_zone`
   mechanics, intermediate New state) move.

5. **`reopen_from_ignored()` steps** (lines 1025-1034) — replace with:

   ```markdown
   Both transitions go through
   `ticket_mutations.reopen_from_ignored()`:
   1. Acquires FOR UPDATE on the ticket
   2. Verifies current status is Ignored
   3. Sets assignee (if applicable)
   4. Re-enters the gate zone; `evaluate_ticket_status` determines the
      correct status (typically Analysis if an assignee is present)

   See `docs/features/tickets/ticket-mutations.md` for the full function
   contract.
   ```

   The behavioral description of *when* reopen happens (the two exit
   transitions at lines 1016-1023) stays in `tickets.md`.

6. **Concurrency Control anchor** — a stub section with the anchor
   `#concurrency-control` must remain in `tickets.md` because internal
   references from CVE dissociation (line 134), mark-as-duplicate (line
   960), and set-confidentiality (line 1852) point to it:

   ```markdown
   #### Concurrency Control

   Every operation that modifies the `Ticket` row or calls
   `evaluate_ticket_status` MUST acquire `FOR UPDATE` on the Ticket row
   before any modification. See
   `docs/features/tickets/ticket-mutations.md` (Concurrency Control) for
   the full rules. The same pattern applies to non-module operations
   (CVE dissociation, mark-as-duplicate, soft-delete/restore,
   set-confidentiality).
   ```

### Cross-references to Add

Add `docs/features/tickets/ticket-mutations.md` to the Cross-references
section at the end of `tickets.md`.

## Impact on Other Documents

| Document | Change Required | Severity |
|---|---|---|
| `docs/features/tickets/README.md` | Add `ticket-mutations.md` to spec list and relationships | Required |
| `AGENTS.md` Guardrail 16 | Add reference: "See `docs/features/tickets/ticket-mutations.md` for the full specification" | Required |
| `docs/features/packages/package-tracking.md` (lines 556, 668, 1039) | Update prose references from "see `tickets.md`, Ticket Mutations Module" to point to `ticket-mutations.md`; line 1039 references "Centralized Status Evaluation" which is being extracted | Required |
| `docs/features/tickets/cvss-scoring.md` (line 352, 510) | Update references from "tickets.md, Centralized Status Evaluation" and "tickets.md, Ticket Mutations Module" to `ticket-mutations.md` | Required |
| `docs/features/packages/product-lifecycle-transitions.md` (lines 9, 102, 120) | Update references from `tickets.md` to `ticket-mutations.md`; resolve function name to canonical form | Required |
| `docs/data-model.md` (line 779) | Update reference from "tickets.md (Centralized Status Evaluation)" to `ticket-mutations.md` | Required |
| `docs/system-map.md` (lines 554-556) | Update description of `tickets.md` hub spec to mention `ticket-mutations.md` as a separate spec for the module contract | Required |
| `docs/features/tickets/ticket-audit-log.md` (line 238) | Update reference from "tickets.md, Concurrency Control" to point to `ticket-mutations.md` (Concurrency Control). Currently survivable via stub anchor, but cleaner to point to the canonical location | Recommended |
| `docs/features/identity/user-service.md` | No change needed (references tickets.md for deactivation side effects) | None |

### Function Name Canonicalization

Three different function names exist across specs for the same operation:

| Current Usage | Location |
|---|---|
| `soft_delete_ticket_package_product(record, user)` | `tickets.md` (line 625, pseudocode) |
| `soft_delete_ticket_package_product()` | This draft |
| `ticket_mutations.soft_delete_product(record)` | `product-lifecycle-transitions.md` (line 102) |

**Resolution**: the canonical name is `soft_delete_ticket_package_product()`
(consistent with `tickets.md` and the naming pattern of sibling functions).
`product-lifecycle-transitions.md` uses a shortened form that must be
updated during Phase 3 to use the canonical name.

## Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Cross-referencing overhead | Low | Clear boundary (what vs how). Same pattern works for identity domain. |
| Duplicate handling split confusion | Medium | mark-as-duplicate stays entirely in tickets.md. Only `revert_duplicate()` and `resolve_canonical_target()` move. Boundary documented. |
| Function contracts are new content | Low | Derived from existing text — restructured into parameter tables. No new semantics invented. |
| Anchors breaking in other docs | Medium | Stub sections with original anchors remain in tickets.md. Prose references in 5 external docs updated in Phase 3. |
| Readers confused about where to look | Low | Both docs state their scope in Purpose. README.md explains the split. |
| Callers table going stale | Low | Scoped to categories, not individual endpoints. Architectural test requirement catches missing callers mechanically. |

## Execution Plan

### Phase 1: Write `ticket-mutations.md`

1. Create `docs/features/tickets/ticket-mutations.md` with the full
   structure above
2. Populate from extracted content + new function contracts
3. Include Related Operations section (~30 lines)
4. Include explicit transaction ownership statement
5. Reference `conventions.md` for generic concurrency pattern
6. Ensure all cross-references are correct

### Phase 2: Refactor `tickets.md`

1. Merge "Automatic Status Re-evaluation" + "Centralized Status
   Evaluation" into a single summary section with cross-reference
2. Replace other extracted sections with summaries (retain anchor stubs)
3. Retain Canonical Target Resolver usage rules in `tickets.md`
4. Add `ticket-mutations.md` to the Cross-references section
5. Verify all internal anchors still work (especially `#concurrency-control`
   referenced by CVE dissociation, mark-as-duplicate, set-confidentiality)

### Phase 3: Update Supporting Documents

1. Update `docs/features/tickets/README.md`
2. Update `AGENTS.md` Guardrail 16
3. Update `docs/features/packages/package-tracking.md` (3 prose refs:
   lines 556, 668, 1039)
4. Update `docs/features/tickets/cvss-scoring.md` (2 prose refs)
5. Update `docs/features/packages/product-lifecycle-transitions.md`
   (3 refs + function name canonicalization)
6. Update `docs/data-model.md` (1 prose ref)
7. Update `docs/system-map.md` (1 prose ref: hub spec description)
8. Update `docs/features/tickets/ticket-audit-log.md` (1 prose ref:
   Concurrency Control — optional but recommended)

### Phase 4: Update Review Tracking

Both `tickets` and `ticket-mutations` start reviews from zero after the
extraction. The previous `docs/reviews/tickets.md` findings file has been
deleted — the old findings are no longer applicable to the restructured
spec.

#### `docs/reviews/.tracking.json`

1. **Reset `tickets`**: set `cache` to `null` (fresh start)
2. **Add `ticket-mutations`**: new enabled entry with `cache: null`

```json
"tickets": {
  "enabled": true,
  "abbr": "TKT",
  "cache": null
},
"ticket-mutations": {
  "enabled": true,
  "abbr": "TKM",
  "cache": null
}
```

#### `docs/reviews/README.md`

1. Remove the old `tickets` row (had 16 findings: 7 GAP, 1 COH, 3 DES,
   5 SEC, 0 API — all Low severity)
2. Add `tickets` as a fresh entry (all reviewers `—`, no findings)
3. Add `ticket-mutations` as a fresh entry (all reviewers `—`, no findings)
4. Place both in alphabetical order: ticket-audit-log → ticket-mutations
   → ticket-references → tickets
5. Recalculate totals:

| | GAP | COH | DES | SEC | API | Total |
|---|---|---|---|---|---|---|
| Previous total | 32 | 7 | 3 | 5 | 5 | 52 |
| Removed (tickets) | -7 | -1 | -3 | -5 | 0 | -16 |
| **New total** | **25** | **6** | **0** | **0** | **5** | **36** |

New severity breakdown:
- GAP: 1:🔴 11:🟠 13:🟡
- COH: 4:🟠 2:🟡
- DES: (none)
- SEC: (none)
- API: 2:🟠 3:🟡

New rows in the Summary Table:

```
| [ticket-mutations](ticket-mutations.md) | — | — | — | — | — | 0 |  |  |
|  |  |  |  |  |  |  |  |  |
| [tickets](tickets.md) | — | — | — | — | — | 0 |  |  |
|  |  |  |  |  |  |  |  |  |
```

Remove `tickets` from the "Disabled specs" list (it was never there, but
verify). `ticket-mutations` is new and enabled, so it does NOT appear in
the disabled list.

### Phase 5: Review

1. Run `@spec-coherence-reviewer` on `ticket-mutations.md`
2. Run `@spec-gap-analyzer` on `ticket-mutations.md`
3. Run `@docs-placement-reviewer` on the changes
4. Run `@docs-reviewer` on the full ticket domain

## Resolved Questions

Previously open questions, now resolved based on reviewer consensus:

### 1. CVE dissociation

**Decision**: behavioral steps stay in `tickets.md`; documented as a
"Related Operation" in `ticket-mutations.md` (~5 lines with
cross-reference). This gives implementers a single document covering
the full FOR UPDATE landscape without claiming CVE dissociation is a
module function.

### 2. Soft-delete/restore of tickets

**Decision**: same as CVE dissociation — stays in `tickets.md`,
listed in "Related Operations" section of `ticket-mutations.md`.

### 3. Function granularity (track status vs delivery_status)

**Decision**: **separate functions**. Rationale:
- Different audit event types (`track_status_changed` vs
  `track_delivery_status_changed`)
- Different callers (VA sets affectedness status; release detection sets
  delivery_status)
- Different authorization context
- Follows `user-service.md` principle where `deactivate_user()` and
  `reactivate_user()` are separate despite operating on the same field

### 4. Package soft-delete scope

**Decision**: **`soft_delete_ticket_package()` must exist.** Evidence:
- `tickets.md` lines 506-509: "package soft-deletion delegates record
  updates to the module"
- The `package_excluded` audit event type exists in the data model
- The orphan package rule (Invariant 2) describes package-level
  soft-deletion as a module operation
- Similarly, `restore_ticket_package()` must exist (corresponding to
  `package_restored` audit event)

### 5. Restore functions boundary (new from review)

**Decision**: `restore_ticket_package_track()`,
`restore_ticket_package_product()`, and `restore_ticket_package()` are
**already implied** by the existing contract. `tickets.md` line 660 lists
"Package addition or soft-deletion/restore" as gate-relevant data. The
extraction makes them explicit as named functions — this is not new scope,
just making the implicit explicit.
