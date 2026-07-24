# OP-8 Decision: Inline Atomic Repoint (Eliminate Duplicate Chains)

## Decision Summary

**Chosen option**: corrected option (b) — inline atomic repoint within
a single transaction.

**Rationale**: eliminates all chain-related complexity (recursive
resolver, cycle detection, hop limit, post-commit flattening, special
session factory) while preserving the ability to merge established
duplicate groups. The current design's primary benefit — resilience to
partial flattening — becomes unnecessary when repointing is atomic.

**What this replaces**: the current chain-tolerant model with
best-effort asynchronous flattening and mandatory `resolve_canonical_target()`
on every read path.

---

## New Design: Complete Specification

### Core Invariant

> `duplicate_of_id` ALWAYS points directly to a non-Duplicated ticket.
> No transient exceptions, no stale references. The invariant is
> maintained by the transactional protocol; violation indicates a bug.

### Database Constraints (local enforcement)

Two new CHECK constraints on the `Ticket` table enforce invariants that
are locally verifiable:

1. **Status–FK coherence**: `status = 'Duplicated'` if and only if
   `duplicate_of_id IS NOT NULL`.
   Name: `chk_ticket_duplicate_status_coherence`
2. **Self-reference prohibition**: `duplicate_of_id <> id`.
   Name: `chk_ticket_no_self_duplicate`

The cross-row invariant ("target is non-Duplicated") cannot be expressed
as a CHECK constraint and is enforced by the locking protocol below.

### Index

A non-unique index on `duplicate_of_id WHERE duplicate_of_id IS NOT NULL`
is required for the dependent lookup query.

### Concurrency: Two-Phase Locking with NOWAIT on Dependents

The current single-ticket-scope rule is relaxed for `mark_as_duplicate`
only.

#### Phase 1 — Root locks (blocking, ordered)

`mark_as_duplicate` acquires `FOR UPDATE` on the source ticket and the
target ticket in deterministic UUID order:

```sql
SELECT ... WHERE id = :min_id FOR UPDATE;
SELECT ... WHERE id = :max_id FOR UPDATE;
```

Each row is validated immediately after acquisition (source: operable;
target: non-Duplicated). If validation fails, the transaction is rolled
back before further lock acquisition.

Deterministic ordering prevents deadlocks between concurrent
`mark_as_duplicate` operations that share a source or target (e.g., A→B
and B→A serialize on their common ticket).

#### Phase 2 — Dependent locks (NOWAIT, ordered)

After both roots are locked and validated, a fresh query (benefiting
from `READ COMMITTED` snapshot refresh after the blocking wait in
Phase 1) selects and locks all current dependents:

```sql
SELECT ... FROM ticket
WHERE duplicate_of_id = :source_id
ORDER BY id
FOR UPDATE NOWAIT
```

- `NOWAIT` ensures this operation never waits. If any dependent row is
  currently locked by another transaction, PostgreSQL immediately raises
  SQLSTATE `55P03` (`lock_not_available`).
- `SKIP LOCKED` is **forbidden** — skipping a dependent would leave an
  unrepaired link violating the core invariant.

#### Conflict handling

On SQLSTATE `55P03` (or `40P01` defensively):

1. The entire transaction is rolled back — no mutations, no audit
   events persist.
2. The service raises `DuplicateConcurrentModificationError`.
3. The API maps this to `409 TICKET_DUPLICATE_CONCURRENT_MODIFICATION`.
4. The client should re-read source and target state before retrying.

#### Why NOWAIT prevents deadlocks

The deadlock scenario without NOWAIT:

```text
Initial: A → B (A is Duplicated, points to B)
UUIDs: A < B < C

T1: mark_as_duplicate(B, C) — locks B, C (Phase 1)
T2: concurrent operation holding A's lock
T1: Phase 2 queries dependents of B — finds A, waits for A's lock
T2: needs B — waits for T1
→ Circular wait: T1 holds B, waits A; T2 holds A, waits B
```

With `NOWAIT`, T1's Phase 2 fails immediately instead of waiting for A.
The transaction rolls back cleanly, and T1 can retry after T2 releases A.

#### Constraint: fresh transaction

`mark_as_duplicate` MUST execute in a transaction holding no
pre-existing Ticket row locks. This ensures Phase 1 is always the first
lock acquisition on Ticket rows in the transaction, preventing
ordering inversions with locks acquired by prior operations in the same
session.

#### All other operations

All other ticket operations retain the single-ticket-scope rule and
blocking waits unchanged.

### `mark_as_duplicate()` — New Algorithm

```python
async def mark_as_duplicate(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    duplicate_of_id: UUID,
    acting_user_id: UUID | None,
) -> Ticket:
```

**Preconditions**:

- Source ticket must exist (else `TicketNotFoundError`)
- Target ticket must exist (else `TicketNotFoundError`)
- Target ticket must be accessible to the acting user (API-layer scope
  check; confidential target without access → 404)
- Source ticket must be operable (`ensure_ticket_operable`)
- Target must not be in Duplicated status (else
  `DuplicateTargetIsDuplicatedError`)
- Source must not equal target (else `SelfDuplicateError`)

**Behavioral steps**:

1. **Phase 1 — lock and validate roots**:
   a. Determine lock order: `first = min(source_id, target_id)`,
      `second = max(source_id, target_id)`
   b. `SELECT ... WHERE id = first FOR UPDATE` — lock first root
   c. Validate the first root immediately (operable if source,
      non-Duplicated if target)
   d. `SELECT ... WHERE id = second FOR UPDATE` — lock second root
   e. Validate the second root immediately
   f. Validate source != target (else `SelfDuplicateError`)
2. **Phase 2 — lock dependents**:
   `SELECT ... WHERE duplicate_of_id = source_id ORDER BY id FOR UPDATE NOWAIT`
   On SQLSTATE `55P03`: rollback and raise
   `DuplicateConcurrentModificationError`
3. **Mutations** (only reached if all locks acquired):
   a. `auto_assign_actor(source, acting_user_id)`
   b. Set `source.status = Duplicated`,
      `source.duplicate_of_id = target_id`
   c. Create `TicketAuditEvent` (`status_change`,
      old_value = source's current status after auto_assign,
      new_value = `Duplicated`, user_id = acting_user_id)
   d. Create `TicketAuditEvent` (`duplicate_set`,
      old_value = NULL, new_value = target's `SNTL-{n}`,
      user_id = acting_user_id)
   e. For each dependent ticket:
      - Set `dependent.duplicate_of_id = target_id`
      - Create `TicketAuditEvent` (`duplicate_target_changed`,
        old_value = source's `SNTL-{n}`,
        new_value = target's `SNTL-{n}`,
        user_id = NULL,
        detail = `{"triggered_by_ticket": "SNTL-{n}"}` where the
        value is the source ticket's identifier)
4. Return updated source ticket

**Post-operation**: no post-commit work. Everything is atomic.

**Locking**: source + target (blocking, ordered) + dependents (NOWAIT,
ordered). All within a single transaction.

**reconcile_ticket_status**: NOT called — direct transition into
manual zone.

**Audit events**:
- `status_change` (on source — records the status transition)
- `duplicate_set` (on source — records the link creation)
- One `duplicate_target_changed` per dependent (system action)

**Atomicity guarantee**: if any step fails (validation, NOWAIT
conflict, or database error), the entire transaction rolls back. No
mutations or audit events persist.

### `revert_duplicate()` — Unchanged Semantics

The revert operation is unchanged in its fundamental behavior:

1. Lock the ticket with `FOR UPDATE`
2. Verify status is Duplicated
3. Clear `duplicate_of_id` (set to NULL)
4. `auto_assign_actor(ticket, acting_user_id, force=True)`
5. Create `TicketAuditEvent` (`duplicate_removed`)
6. Call `_reenter_gate_zone()`

**Non-retroactive revert**: if ticket A was repointed from B to C
(because B was marked duplicate of C), reverting B does NOT
automatically revert A back to B. A remains pointing to C. This is
correct: A's `duplicate_target_changed` audit event documents why the
repoint occurred, and the current state (A→C) is the intended result.
To change A's target, the VA must explicitly revert A and re-mark it.

### New Errors

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `DuplicateTargetIsDuplicatedError` | 409 | `TICKET_DUPLICATE_TARGET_DUPLICATED` | Target ticket is already in Duplicated status |
| `DuplicateConcurrentModificationError` | 409 | `TICKET_DUPLICATE_CONCURRENT_MODIFICATION` | A dependent ticket is locked by a concurrent operation; retry after the conflict resolves |

**`DuplicateTargetIsDuplicatedError`** uses the standard error envelope
(`code` + `detail`). The `detail` string includes the target's own
target identifier for human readability (e.g., "Target ticket SNTL-42
is already duplicated. Use its target SNTL-7 instead."). Clients that
need the programmatic redirect can `GET` the target ticket (which they
already know from the request body) and read its `duplicate_of` field.
No custom envelope fields are added.

**`DuplicateConcurrentModificationError`** uses the standard error
envelope. The `detail` string indicates a transient conflict. The
client should re-read source and target before retrying.

### API Response Serialization — Simplified

Since `duplicate_of_id` always points to a non-Duplicated ticket, API
serialization becomes a direct read:

- `duplicate_of` field: convert `duplicate_of_id` UUID to `SNTL-{n}`
  format. No resolution, no recursion.
- If `duplicate_of_id` is NULL, `duplicate_of` is null.

### Confidentiality — Unchanged Risk Profile

The accepted risk documented in `tickets.md:972-992` remains unchanged.
A non-confidential Duplicated ticket may expose the identifier of a
confidential target. The only change: the word "chain" is removed from
the explanation since chains no longer exist.

### `duplicate_target_changed` Audit Event — Retained

This event type is retained because inline repointing is still a
mutation of each dependent ticket's `duplicate_of_id`. The audit
contract requires one event per mutation. The event's semantics are
unchanged from the current spec; only the execution context changes
(same transaction as the primary operation, not a separate best-effort
transaction).

The `detail` JSONB schema must be registered in the detail JSONB Schema
Contract table in `ticket-audit-log.md`:

| Event Type | Required Keys | Optional Keys | Example |
|---|---|---|---|
| `duplicate_target_changed` | `triggered_by_ticket` (string) | — | `{"triggered_by_ticket": "SNTL-42"}` |

---

## What Gets Removed

| Item | Current location | Reason for removal |
|------|------------------|--------------------|
| `resolve_canonical_target()` function | `ticket-mutations.md:761-788` | No chains to resolve |
| `execute_duplicate_flattening()` function | `ticket-service.md:483-523` | Repointing is inline and atomic |
| `MarkAsDuplicateResult` dataclass | `ticket-service.md:414-423` | No flattening IDs to return; function returns `Ticket` directly |
| Transaction ownership exception | `ticket-service.md:60-70` | No function commits independently |
| `DuplicateCycleDetectedError` exception | `ticket-mutations.md:967`, `ticket-service.md:761` | No cycles possible |
| `DuplicateChainDepthError` exception | `ticket-mutations.md:968`, `ticket-service.md:762` | No chains exist |
| `TICKET_DUPLICATE_CYCLE_DETECTED` error code | `api-spec.md:156` | Removed with its exception |
| `TICKET_DUPLICATE_CHAIN_DEPTH` error code | `api-spec.md:156` | Removed with its exception |
| Canonical Target Resolver section | `tickets.md:513-534` | No chains to resolve |
| Best-Effort Flattening section | `tickets.md:569-581` | No flattening |
| Cycle Prevention section | `tickets.md:642-664` | No cycles possible |
| Cycle Resolution section | `tickets.md:666-679` | No cycles possible |
| Correctness Guarantee section | `tickets.md:681-693` | Replaced by new invariant |
| Revert of an Intermediate Ticket section | `tickets.md:604-617` | No intermediate tickets |
| API Response Behavior (chain resolution) | `tickets.md:619-640` | Replaced by direct read |
| "duplicate chain" terminology exception | `conventions.md:84-85` | No chains in this domain |
| Dependency on `resolve_canonical_target` in ticket-service | `ticket-service.md:93-99, 780, 787-793` | Function removed |
| Flattening-related architectural test | `ticket-service.md:829-831` | Operation no longer exists |
| Resolver in maintainer.md | `maintainer.md:113-114` | Direct read replaces chain resolution |

## What Gets Added

| Item | Target location | Description |
|------|-----------------|-------------|
| `DuplicateTargetIsDuplicatedError` | `ticket-service.md` exception table | New exception (409) |
| `DuplicateConcurrentModificationError` | `ticket-service.md` exception table | New exception (409), transient conflict |
| `TICKET_DUPLICATE_TARGET_DUPLICATED` | `api-spec.md` TICKET_* category | New error code |
| `TICKET_DUPLICATE_CONCURRENT_MODIFICATION` | `api-spec.md` TICKET_* category | New error code (transient) |
| `chk_ticket_duplicate_status_coherence` | `data-model.md` Ticket table | CHECK constraint |
| `chk_ticket_no_self_duplicate` | `data-model.md` Ticket table | CHECK constraint |
| Index on `duplicate_of_id` | `data-model.md` Indexes section | Non-unique partial index |
| Two-phase locking exception with NOWAIT | `ticket-mutations.md` Concurrency Control | Scoped relaxation of single-ticket rule |
| `duplicate_target_changed` detail schema | `ticket-audit-log.md` detail JSONB Schema Contract | Schema registration |

## Existing Bugs to Fix Alongside

These inconsistencies exist independently of OP-8 but must be resolved
as part of this change to ensure internal coherence:

### Bug 1: Missing `status_change` event in `mark_as_duplicate`

**Problem**: `tickets.md:481-487` documents that `mark_as_duplicate` on
a `New` ticket produces two `status_change` events (`New → Analysis`,
`Analysis → Duplicated`). However, `ticket-service.md:479` lists only
`duplicate_set` as the audit event — no `status_change` for
`Analysis → Duplicated` or `{previous} → Duplicated`.

**Fix**: `mark_as_duplicate` must create a `status_change` event
recording the transition from the ticket's pre-operation status to
`Duplicated`. This applies regardless of the source status. The
`auto_assign_actor` call already handles `New → Analysis` with its own
`status_change`; the function must add a second `status_change` for
`{current} → Duplicated`.

### Bug 2: Reconciliation claim on Duplicated entry

**Problem**: `tickets.md:690-693` says `mark_as_duplicate` "calls
`reconcile_ticket_status`", while `ticket-service.md:476-477` says it
does NOT (correct — manual zone entry does not need reconciliation).

**Fix**: remove the incorrect claim from `tickets.md`. The function does
not call `reconcile_ticket_status` (entering the manual zone is a direct
transition).

### Bug 3: `duplicate_target_changed` missing from detail JSONB Schema Contract

**Problem**: `ticket-audit-log.md:33` defines `duplicate_target_changed`
with a `detail` field containing `triggered_by_ticket`, but the detail
JSONB Schema Contract table (lines 93-103) does not list this event type.
Lines 79-81 and 121-125 state that undocumented detail keys/events must be
rejected.

**Fix**: add `duplicate_target_changed` to the detail JSONB Schema Contract
table with its required key `triggered_by_ticket`.

### Bug 4: Admin cannot perform cycle recovery

**Problem**: `tickets.md:666-670` says "a VA or Admin must invoke
revert-duplicate", but `rbac.md:383-384` shows revert requires
`triage_ticket`, and the Admin role lacks this capability
(`rbac.md:83-92`).

**Fix**: since cycles are eliminated by this change, the cycle resolution
section is removed entirely. No fix needed — the contradiction disappears
with the removal.

### Bug 5: Maintainer endpoint field name inconsistency

**Problem**: `maintainer.md:318-325` exposes `duplicate_of_id` and
describes it as a UUID, while the main ticket schemas (`tickets.md:1172,
1191`) use `duplicate_of` as a `SNTL-{n}` string.

**Fix**: align `maintainer.md` to use `duplicate_of` with the `SNTL-{n}`
format for consistency with all other response schemas.

### Bug 6: Audit Trail Index event count drift

**Problem**: `audit-trail-infrastructure.md:298` declares the ticket
audit trail has **31** event types. The authoritative enum in
`data-model.md:1211-1240` currently defines **28** values. The count in
the index is stale.

**Fix**: during Step 9, reconcile the count in the Audit Trail Index to
match the actual number of values in the `TicketAuditEventType` enum at
the time the changes are applied. OP-8 itself does not add or remove
event types, so the reconciled count should reflect the current enum
after all other changes.

---

## Prescriptive Action Plan

Each step specifies exactly what to modify, where, and how. Steps are
ordered to maintain spec coherence at each intermediate state (no step
introduces a contradiction that is only resolved by a later step).

### Step 1: Update `docs/data-model.md` — Ticket table

**File**: `docs/data-model.md`

**Changes**:

1. Replace the `duplicate_of_id` column description (line 1088):
   - **Remove**: reference to transient Duplicated targets, resolver, and
     hop limit
   - **New text**: `Self-referencing FK to the target ticket when status
     is Duplicated. Always references a non-Duplicated ticket (enforced
     by the transactional locking protocol in mark_as_duplicate). See
     docs/features/tickets/tickets.md (Duplicate Handling)`

2. Add two CHECK constraints to the Ticket table (after
   `is_confidential` row or in a dedicated constraints subsection):
   - `chk_ticket_duplicate_status_coherence`:
     `(status = 'Duplicated' AND duplicate_of_id IS NOT NULL) OR
     (status != 'Duplicated' AND duplicate_of_id IS NULL)`
   - `chk_ticket_no_self_duplicate`: `duplicate_of_id <> id`

3. In the Indexes section (line 1513-1515), add:
   - `ix_ticket_duplicate_of_id`: index on `duplicate_of_id` where
     `duplicate_of_id IS NOT NULL` — used by `mark_as_duplicate` to find
     dependents of the source ticket

4. In the `TicketAuditEventType` enum table (line 1217), update the
   `duplicate_target_changed` description:
   - **Remove**: reference to "flattening update" and "may be absent if
     flattening was interrupted"
   - **New text**: `Atomic repoint: the ticket's duplicate_of_id was
     updated because its previous target was marked as duplicate.
     old_value is the previous target identifier (SNTL-{n}). new_value
     is the new target identifier. user_id is NULL (system action).
     detail contains {"triggered_by_ticket": "SNTL-{n}"} identifying
     the ticket whose mark-as-duplicate operation triggered this
     repoint.`

### Step 2: Update `docs/features/tickets/ticket-audit-log.md`

**File**: `docs/features/tickets/ticket-audit-log.md`

**Changes**:

1. Update the `duplicate_target_changed` row in the Event Type Contract
   table (line 33):
   - **Remove**: "Flattening update", "may be absent if the flattening
     was interrupted — this is not an error (the canonical resolver
     handles resolution at read time)"
   - **New text**: `Atomic repoint: the ticket's duplicate_of_id was
     updated within the same transaction as the triggering
     mark-as-duplicate operation, because the ticket's previous target
     was itself marked as duplicate.`
   - Keep: `user_id = NULL`, old/new value semantics, detail schema

2. Add `duplicate_target_changed` to the detail JSONB Schema Contract
   table (after line 103):

   | Event Type | Required Keys | Optional Keys | Example |
   |---|---|---|---|
   | `duplicate_target_changed` | `triggered_by_ticket` (string) | — | `{"triggered_by_ticket": "SNTL-42"}` |

### Step 3: Update `docs/features/tickets/ticket-mutations.md`

**File**: `docs/features/tickets/ticket-mutations.md`

**Changes**:

1. **Concurrency Control — Single-ticket scope** (lines 311-318):
   Replace with:

   > `ticket_mutations` functions operate on a single ticket per
   > transaction.
   >
   > **Exception — `mark_as_duplicate` (in `ticket_service`)**: this
   > operation acquires `FOR UPDATE` on the source ticket, the target
   > ticket, and all current dependents of the source ticket in a
   > single transaction. Source and target are locked with blocking
   > waits in deterministic UUID order. Dependents are locked with
   > `FOR UPDATE NOWAIT` — if any dependent is currently locked by
   > another transaction, the operation aborts immediately
   > (`DuplicateConcurrentModificationError`) rather than waiting.
   > This two-phase protocol prevents deadlocks: Phase 1 (roots)
   > cannot form cycles due to UUID ordering; Phase 2 (dependents)
   > never waits, so it cannot participate in a wait cycle.
   >
   > All other operations retain the single-ticket-scope rule and
   > blocking waits unchanged.

2. **Blocking wait** (lines 322-325): add a note:
   > Exception: `mark_as_duplicate` Phase 2 uses `FOR UPDATE NOWAIT`
   > on dependent rows. See Single-ticket scope above.

3. **Remove `resolve_canonical_target()`** section (lines 761-788)
   entirely.

4. **Remove `DuplicateCycleDetectedError` and `DuplicateChainDepthError`**
   from the Service Exceptions table (lines 967-968).

5. **Update `revert_duplicate()`** (lines 717-757):
   - Remove the final paragraph ("The revert operation does NOT need to
     know or care about the canonical target — it simply removes the
     ticket from the duplicate chain.") and replace with:
     "The revert is non-retroactive: if other tickets were previously
     repointed away from this ticket (via `duplicate_target_changed`
     events), they are not affected by this revert — they remain
     pointing to their current target."

6. **Cross-references** (line 1004): remove
   `resolve_canonical_target()` from the imports list in the
   `ticket-service.md` entry.

### Step 4: Update `docs/features/tickets/ticket-service.md`

**File**: `docs/features/tickets/ticket-service.md`

**Changes**:

1. **Transaction ownership** (lines 50-70): remove the entire exception
   paragraph about flattening. The section becomes:

   > The module does NOT commit or roll back. All operations execute
   > within the caller's database session. Commit responsibility belongs
   > to the caller.
   >
   > This matches the `ticket_mutations`, `package_service`, and
   > `user_service` pattern — the module applies mutations and creates
   > audit events, but the transaction boundary is the caller's
   > decision.

2. **Relationship with other modules** table (line 97): remove
   `resolve_canonical_target()` from the imports list.

3. **`mark_as_duplicate()`** (lines 395-481): replace the entire
   function specification with the new algorithm defined in this draft
   (see "New Algorithm" section above). Key differences:
   - Return type: `Ticket` (not `MarkAsDuplicateResult`)
   - No post-commit orchestration
   - Two-phase locking: roots (blocking) + dependents (NOWAIT)
   - Precondition: target not Duplicated (new guard)
   - Precondition removed: chain resolution (no resolver)
   - New failure mode: `DuplicateConcurrentModificationError`
   - Audit events: `status_change` + `duplicate_set` + N ×
     `duplicate_target_changed`
   - Constraint: must execute in a fresh transaction with no
     pre-existing Ticket locks

4. **Remove `execute_duplicate_flattening()`** section entirely (lines
   483-523).

5. **Remove `MarkAsDuplicateResult`** dataclass (lines 414-423).

6. **Service Exceptions table** (lines 744-770):
   - Remove `DuplicateCycleDetectedError` and `DuplicateChainDepthError`
   - Add:

   | Exception | HTTP | Code | Raised when |
   |-----------|------|------|-------------|
   | `DuplicateTargetIsDuplicatedError` | 409 | `TICKET_DUPLICATE_TARGET_DUPLICATED` | Target ticket is already in Duplicated status |
   | `DuplicateConcurrentModificationError` | 409 | `TICKET_DUPLICATE_CONCURRENT_MODIFICATION` | NOWAIT lock on a dependent failed (concurrent operation on the duplicate group) |

7. **Dependency Summary** (lines 772-798):
   - Remove `resolve_canonical_target()` from the tree diagram
   - Update the dependency matrix: remove the `resolve_canonical_target`
     column; `mark_as_duplicate` row no longer checks it

8. **Architectural Test Requirement** (lines 829-831): replace the
   flattening test with:
   > **Mark-as-duplicate with dependents (atomic repoint)**: mark
   > ticket B as duplicate of C, where tickets A1 and A2 currently
   > point to B. Verify: (a) A1 and A2 are atomically repointed to C,
   > (b) `duplicate_target_changed` events are created for A1 and A2,
   > (c) `duplicate_set` and `status_change` events are created for B

   Add a new test:
   > **Concurrent modification conflict**: hold a lock on a dependent
   > ticket (simulating a concurrent revert). Call `mark_as_duplicate`
   > on the dependent's target. Verify: (a) the operation raises
   > `DuplicateConcurrentModificationError`, (b) the transaction is
   > rolled back (no mutations, no audit events persist), (c) retrying
   > after the lock is released succeeds normally

### Step 5: Update `docs/features/tickets/tickets.md`

**File**: `docs/features/tickets/tickets.md`

**Changes**:

1. **Duplicate Handling — Terminology** (lines 502-511):
   - Remove "canonical target" and "original ticket" definitions that
     reference chain resolution
   - Replace with: "**Target**: the non-Duplicated ticket referenced by
     `duplicate_of_id`. The system guarantees this ticket is never in
     Duplicated status (see Invariant below). **Original ticket**: the
     user-facing synonym for 'target.' Used in UI copy."

2. **Remove Canonical Target Resolver section** (lines 513-534) entirely.

3. **Mark-as-Duplicate Operation** (lines 536-567): rewrite to match
   the new algorithm:
   - Source must be operable
   - Target must not be Duplicated (new error)
   - Target must not be the source (self-duplicate)
   - Set link and status atomically
   - Repoint dependents atomically in the same transaction
   - No chain resolution, no flattening
   - Concurrent modification → 409 (transient, retryable)

4. **Remove Best-Effort Flattening section** (lines 569-581).

5. **Revert-Duplicate Operation** (lines 583-602): keep unchanged
   except add the non-retroactive clause: "If other tickets were
   repointed away from this ticket during a prior `mark_as_duplicate`
   operation, they are not affected by this revert."

6. **Remove Revert of an Intermediate Ticket section** (lines 604-617).

7. **Replace API Response Behavior section** (lines 619-640) with:
   > `duplicate_of_id` always points to a non-Duplicated ticket. The
   > API field `duplicate_of` is the `SNTL-{n}` format of the stored
   > UUID — a direct conversion with no resolution step. The raw
   > `duplicate_of_id` UUID is not exposed in the API.

8. **Remove Cycle Prevention section** (lines 642-664).

9. **Remove Cycle Resolution section** (lines 666-679).

10. **Replace Correctness Guarantee section** (lines 681-693) with:
    > **Invariant**: `duplicate_of_id` always points to a non-Duplicated
    > ticket. This is enforced by `mark_as_duplicate`, which locks the
    > target and verifies its status before writing. The CHECK constraint
    > `chk_ticket_duplicate_status_coherence` enforces the bidirectional
    > implication between status and FK at the database level. Multiple
    > tickets may reference the same target.

11. **Fix Bug 2** — Remove the incorrect statement at lines 690-693
    ("This operation modifies the `Ticket` row and calls
    `reconcile_ticket_status`..."). This text refers to
    `mark_as_duplicate` but incorrectly claims it calls reconciliation.
    The Invariant paragraph above replaces this content.

12. **API endpoint error table** for Mark Ticket as Duplicate (lines
    1509-1516):
    - Remove `TICKET_DUPLICATE_CYCLE_DETECTED` and
      `TICKET_DUPLICATE_CHAIN_DEPTH` rows
    - Update `TICKET_SELF_DUPLICATE` condition text from "Resolved
      target is the same ticket (self-reference after chain resolution)"
      to "Source and target are the same ticket"
    - Add:

    | Status | Code | Condition |
    |--------|------|-----------|
    | 409 | `TICKET_DUPLICATE_TARGET_DUPLICATED` | Target ticket is itself Duplicated (use its target instead) |
    | 409 | `TICKET_DUPLICATE_CONCURRENT_MODIFICATION` | A dependent is locked by a concurrent operation; retry |

13. **Endpoint description** (lines 1489-1491): remove references to
    chain resolution and flattening updates. Replace with: "Marks a
    ticket as a duplicate of another non-Duplicated ticket. If other
    tickets currently point to the source, they are atomically repointed
    to the target."

14. **Confidential target risk** (lines 972-992): remove the word
    "chain" and "through a chain". The risk description becomes: "A
    Duplicated ticket that is non-confidential may have a
    `duplicate_of_id` pointing to a confidential ticket."

15. **Response schemas note** (lines 1200-1203): replace with:
    > Note: the API field `duplicate_of` is the `SNTL-{n}` format of
    > the database column `duplicate_of_id` (UUID FK). No resolution is
    > needed — the stored UUID always references a non-Duplicated
    > ticket.

### Step 6: Update `docs/api-spec.md`

**File**: `docs/api-spec.md`

**Changes**:

1. **Error Code Categories — TICKET_* prefix** (line 156):
   - Remove: `TICKET_DUPLICATE_CYCLE_DETECTED`,
     `TICKET_DUPLICATE_CHAIN_DEPTH`
   - Add: `TICKET_DUPLICATE_TARGET_DUPLICATED`,
     `TICKET_DUPLICATE_CONCURRENT_MODIFICATION`

### Step 7: Update `docs/conventions.md`

**File**: `docs/conventions.md`

**Changes**:

1. **Cascade / Chain / Flattening Terminology — "flattening" row**
   (line 77): update the examples to remove `execute_duplicate_flattening()`
   since the function no longer exists. Replace with a current example or
   remove the example and leave only the concept definition. The term
   "flattening" itself remains valid as a general concept.

2. **"chain" exception list** (lines 83-88): remove the "duplicate chain"
   entry ("the `duplicate_of_id` linked-list data structure") since
   `duplicate_of_id` is no longer a linked list — it is always a direct
   pointer to a non-Duplicated ticket.

### Step 8: Update `docs/features/packages/maintainer.md`

**File**: `docs/features/packages/maintainer.md`

**Changes**:

1. **Error state table** (line 110): change
   `duplicated (includes duplicate_of_id)` to
   `duplicated (includes duplicate_of)`

2. **Line 113-114** ("Duplicated link: the `duplicate_of_id` value in
   API responses is always the resolved canonical target (a
   non-Duplicated ticket)."): replace with:
   > **Duplicated link**: the `duplicate_of` value in the error-state
   > response is the `SNTL-{n}` identifier of the target ticket (always
   > non-Duplicated).

3. **Error state response example** (lines 314-321): replace the single
   example with two examples showing both the generic case (field null)
   and the populated case:

   ```json
   {
     "data": {
       "error_state": {
         "type": "not_analyzed",
         "duplicate_of": null
       }
     }
   }
   ```

   ```json
   {
     "data": {
       "error_state": {
         "type": "duplicated",
         "duplicate_of": "SNTL-42"
       }
     }
   }
   ```

4. **Line 324-325**: update description to match the new field name and
   format: "The `duplicate_of` field is populated only for the
   `duplicated` type, containing the `SNTL-{n}` identifier of the
   target ticket."

### Step 9: Update `docs/features/platform/audit-trail-infrastructure.md`

**File**: `docs/features/platform/audit-trail-infrastructure.md`

**Changes**:

1. **Audit Trail Index** (line 298): reconcile the ticket event type
   count with the actual number of values in the
   `TicketAuditEventType` enum in `data-model.md`. The current index
   says 31 but the enum defines 28 values. Update the count to match
   reality. OP-8 does not add or remove event types, so the delta is 0.

### Step 10: Update `docs/drafts/open-points.md`

**File**: `docs/drafts/open-points.md`

**Changes**:

1. Move OP-8 from "Open — Tickets" to a "Resolved" section (or simply
   change its status in the summary table from `Open` to `Resolved`).

2. Add a resolution note below the OP-8 entry:
   > **Resolution**: adopted corrected option (b) — inline atomic
   > repoint. `mark_as_duplicate` locks source and target with blocking
   > waits in UUID order, then locks dependents with `FOR UPDATE NOWAIT`.
   > All dependents repointed atomically. Duplicated targets rejected
   > with `TICKET_DUPLICATE_TARGET_DUPLICATED`. Concurrent conflicts
   > produce `TICKET_DUPLICATE_CONCURRENT_MODIFICATION` (retryable).
   > Resolver, flattening, cycle handling, and hop limit eliminated.
   > Changes applied to all relevant specs.

### Step 11: Verify — Run Reviewers

After all specification changes are applied, run the following reviewers
to verify correctness and coherence:

1. **`@spec-gap-analyzer`** on `docs/features/tickets/tickets.md` —
   verify that the rewritten duplicate handling section is functionally
   complete (all operations, error paths, edge cases specified)

2. **`@spec-gap-analyzer`** on `docs/features/tickets/ticket-service.md` —
   verify the new `mark_as_duplicate` algorithm is complete (including
   NOWAIT failure path and fresh-transaction constraint)

3. **`@spec-gap-analyzer`** on `docs/features/tickets/ticket-mutations.md` —
   verify the concurrency control exception is fully specified

4. **`@spec-coherence-reviewer`** on `docs/features/tickets/tickets.md` —
   verify no contradictions with other specs after the rewrite

5. **`@spec-coherence-reviewer`** on `docs/features/tickets/ticket-service.md` —
   verify consistency with `ticket-mutations.md` and `tickets.md`

6. **`@spec-coherence-reviewer`** on `docs/features/tickets/ticket-audit-log.md` —
   verify the event contract is consistent with the new algorithm

7. **`@data-model-reviewer`** on `docs/data-model.md` — verify the new
   constraints and index are correctly specified

8. **`@api-convention-reviewer`** on `docs/features/tickets/tickets.md` —
   verify the new error codes and response format comply with API
   conventions

9. **`@api-convention-reviewer`** on `docs/features/packages/maintainer.md` —
   verify the field name change complies with API conventions

10. **`@ticket-integrity-reviewer`** on
    `docs/features/tickets/ticket-service.md` — verify audit event
    completeness for the new algorithm

11. **`@docs-reviewer`** on `docs/features/tickets/tickets.md` — verify
    documentation completeness after the major rewrite

### Step 12: Delete This Draft

After all reviewers pass and any issues are resolved, delete this file:

```
docs/drafts/op8-inline-atomic-repoint.md
```

The decision is recorded in OP-8's resolution note in `open-points.md`
and fully embodied in the updated specifications.

---

## Impact Summary

| Document | Nature of change |
|----------|-----------------|
| `docs/data-model.md` | Add constraints, index, update column description and enum description |
| `docs/features/tickets/tickets.md` | Major rewrite of Duplicate Handling section (~200 lines replaced) |
| `docs/features/tickets/ticket-service.md` | Rewrite `mark_as_duplicate`, remove flattening, update exceptions |
| `docs/features/tickets/ticket-mutations.md` | Remove resolver, update concurrency rules, update revert |
| `docs/features/tickets/ticket-audit-log.md` | Update event description, register detail schema |
| `docs/api-spec.md` | Remove 2 error codes, add 2 |
| `docs/conventions.md` | Update terminology examples |
| `docs/features/packages/maintainer.md` | Fix field name/format inconsistency (3 locations) |
| `docs/features/platform/audit-trail-infrastructure.md` | Reconcile event count |
| `docs/drafts/open-points.md` | Mark OP-8 as resolved |

Documents that do NOT require changes (Duplicated status unchanged,
behavior toward inactive tickets unchanged):

- `docs/features/identity/rbac.md` — endpoint permission map unchanged
- `docs/features/tickets/cve-tracking.md` — exclusion of Duplicated tickets unchanged
- `docs/features/tickets/cve-service.md` — scope filtering unchanged
- `docs/features/packages/package-service.md` — Duplicated exclusion unchanged
- `docs/features/platform/fetcher-infrastructure.md` — catch-up unchanged
- `docs/system-map.md` — derived document; may be regenerated separately
