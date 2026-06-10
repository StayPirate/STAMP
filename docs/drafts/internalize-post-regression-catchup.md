# Draft: Internalize Post-Regression Catch-Up Inside reconcile_ticket_status()

**Origin**: Finding TKM-GAP-18 — "Post-regression catch-up delegation is
caller-dependent"

**Status**: Ready for implementation

## Problem Statement

The catch-up logic that runs when a ticket transitions from an inactive status
(Resolved, Ignored, Duplicated) back to an active status (Analysis, Analyzed) is
currently duplicated across two specs with two different enforcement mechanisms:

1. **`ticket-mutations.md`**, section "Post-Regression Hook: Resolved → Active"
   — delegates responsibility to **callers** of `reconcile_ticket_status()`.
   Each caller must manually check old vs new status and enqueue catch-up tasks.
   A "Pattern for callers" code snippet is provided but not enforced.

2. **`ticket-service.md`**, section "Ticket Reactivation" — the **endpoint
   handler** must execute catch-up after `_reenter_gate_zone()` commits, for
   Ignored → Active and Duplicated → Active transitions.

Both paths execute the same two operations:
- `recalculate_cvss_chain(ticket_id)` — synchronous, same transaction
- `catch_up()` for all registered fetchers via `get_catch_up_fetchers()` —
  async Celery enqueue

The problem: a new caller of `reconcile_ticket_status()` can omit the
post-regression hook without any compile-time or runtime warning, leaving the
ticket with stale CVSS and release data after returning to active status.

**Additional gap — `delete_cvss_assessment()`**: this function calls
`reconcile_ticket_status()` without any post-regression handling. If deleting
a CVSS assessment triggers a backward transition from Resolved (e.g., removing
a SUSE assessment drops severity below a threshold, un-satisfying a gate), the
catch-up currently does NOT fire. After internalization, this case is resolved
automatically — all callers get the hook for free.

## Key Architectural Insight

All three inactive → active transitions already converge on
`reconcile_ticket_status()`:

| Transition | Trigger | Route |
|---|---|---|
| Resolved → Active | Gate-driven (automatic) | Direct call to `reconcile_ticket_status()` |
| Ignored → Active | `reopen_from_ignored()` | Via `_reenter_gate_zone()` → `reconcile_ticket_status(previous_status=Ignored)` |
| Duplicated → Active | `revert_duplicate()` | Via `_reenter_gate_zone()` → `reconcile_ticket_status(previous_status=Duplicated)` |

Since all paths already funnel through `reconcile_ticket_status()`, internalizing
the catch-up detection there eliminates the duplication and makes the hook
automatic for all current and future callers.

## Proposed Solution

Add a post-transition check inside `reconcile_ticket_status()`:

```
reconcile_ticket_status(ticket, db, previous_status=None):
  1. effective_previous = previous_status or ticket.status (before update)
  2. Evaluate gates, determine new_status
  3. If new_status ≠ current: update ticket, create audit event, handle
     inactive assignee
  4. NEW — If effective_previous ∈ {Resolved, Ignored, Duplicated}
        AND new_status ≠ effective_previous:
       → default_cvss_version = settings_service.get_default_cvss_version(db)
       → recalculate_cvss_chain(ticket_id, default_cvss_version)
       → try: enqueue catch_up() for all fetchers via get_catch_up_fetchers()
         except: log warning (catch-up deferred to periodic fetcher schedule)
  5. Return
```

**`default_cvss_version` resolution**: the settings query at step 4 executes
only when the regression condition is true. In the common case (no inactive →
active transition), no settings query is executed — the cost is zero. The
setting is guaranteed to exist at runtime by an Alembic data migration (see
"Bootstrap Prerequisite" below).

The condition at step 4 fires ONLY when a ticket actually leaves an inactive
state (inactive → different state). In the common case (no transition, or
Resolved → Resolved no-op), it is a single enum comparison with zero overhead.

### Implementation notes for step 4

1. **`new_status` is captured once** in step 2 and is not re-evaluated after
   `recalculate_cvss_chain()` returns. The nested `reconcile_ticket_status()`
   call from within the chain may update `ticket.status` independently — this
   does not affect step 4's condition, which was already evaluated before the
   chain executes.

2. **Step 4 is independent of step 3**: the condition fires based on
   `effective_previous` and `new_status`, regardless of whether step 3
   produced a status change. In the `_reenter_gate_zone()` case, the caller
   has already set the status before invoking reconcile; step 3 sees no change
   but step 4 correctly detects the inactive-state exit via `previous_status`.

3. **Catch-up enqueue is unconditional** after `recalculate_cvss_chain()`
   returns — it does not re-check ticket status. If the recalculation chain
   itself restores the ticket to Resolved (via a nested reconcile), the
   enqueued catch-up tasks are benign no-ops (idempotent by contract).

4. **Error handling**: `recalculate_cvss_chain()` propagates exceptions
   normally — if it fails (DB constraint violation, serialization failure),
   the session is already compromised and rollback is correct. The
   `catch_up()` enqueue is wrapped in try/except: a transient Redis failure
   does not roll back the DB transaction; catch-up is deferred to the next
   periodic fetcher cycle.

### Why this is safe

- `recalculate_cvss_chain()` operates within the same transaction (consistent
  with the existing `FOR UPDATE` lock pattern — it reads/writes CVSS data, not
  external services)
- `catch_up()` enqueues Celery tasks via Redis LPUSH (sub-millisecond, no
  external service call inside the locked transaction)
- **Pre-commit enqueue is safe** (OP-1 resolution): `catch_up()` does not read
  ticket status as a precondition, is idempotent by contract, and delegates
  mutations to service modules that acquire independent locks and evaluate the
  ticket's committed state. On rollback, catch-up tasks produce benign no-ops
  (external data is factually correct; `reconcile_ticket_status()` respects the
  unchanged inactive state). See OP-1 for the full analysis.
- No infinite recursion risk: fetchers triggered by `catch_up()` may eventually
  call `upsert_cvss_assessment()` → `reconcile_ticket_status()`, but the second
  call will not detect a regression (the ticket is already in an active state)
- Same-transaction re-lock: `recalculate_cvss_chain()` acquires `FOR UPDATE` on
  the Ticket row as its first step. When called from within
  `reconcile_ticket_status()`, the lock is already held by the same transaction
  — PostgreSQL treats this as a no-op (the SELECT returns the already-locked row
  immediately). This is intentional: the function remains independently callable
  from other contexts (e.g., the admin batch task for CVSS version changes)
  without modification
- **Lock-duration on the regression path**: step 4 extends the `FOR UPDATE`
  lock duration only when the regression condition is true (rare path). The
  additional work is bounded: `recalculate_cvss_chain()` performs simple numeric
  comparisons (CVSS score vs threshold) per product — not aggregate queries or
  external service calls. This satisfies the Transaction Hygiene Rules (no
  external calls, no expensive queries inside the lock)
- **Catch-up enqueue resilience**: the `catch_up()` enqueue is wrapped in
  try/except. A transient Redis failure does not roll back the DB transaction;
  catch-up is deferred to the next periodic fetcher cycle.
  `recalculate_cvss_chain()` propagates exceptions normally — if it fails, the
  transaction is already compromised (DB-level error) and rollback is the
  correct outcome

### What gets removed/simplified

| Current location | Change |
|---|---|
| `ticket-mutations.md` — "Post-Regression Hook: Resolved → Active" section | **Remove entirely** (including "Pattern for callers" code snippet and MUST clause) |
| `ticket-mutations.md` — `upsert_cvss_assessment()` step 7d note "handled internally" | **Remove** (redundant — all callers now get it automatically) |
| `ticket-mutations.md` — Callers table, "Post-regression from Resolved" row | **Rewrite** to document internal behavior |
| `ticket-mutations.md` — Callers table, "Ticket reactivation (un-ignore, un-duplicate)" row | **Rewrite** — catch-up is now internal to `reconcile_ticket_status()`; `ticket_service` endpoint handlers no longer execute it explicitly |
| `ticket-service.md` — "Ticket Reactivation" section | **Simplify** — remove the manual catch-up calls from endpoint handler responsibilities. Document that catch-up is handled internally by `reconcile_ticket_status()` |
| `ticket-mutations.md` — `reconcile_ticket_status()` behavior section | **Add** step 4 (inactive → active detection + catch-up execution) |

## Open Points

The following issues were identified during review and must be resolved
before implementation begins.

### OP-1: Post-commit enqueue requirement (High)

`fetcher-infrastructure.md` (line 692-696) states:

> "`run_catch_up` tasks MUST be enqueued after the caller's transaction
> commits. Enqueuing before commit risks catch-up tasks running against
> uncommitted data."

The proposed step 4 enqueues `catch_up()` tasks INSIDE
`reconcile_ticket_status()`, which executes within the caller's
transaction (before commit). This violates the MUST-level constraint.

**Options**:

- **(A)** Use a SQLAlchemy `after_commit` session hook to defer the
  enqueue until the transaction commits. The hook is registered during
  step 4 but fires only after the caller commits.
- **(B)** Relax the post-commit enqueue rule in `fetcher-infrastructure.md`
  with explicit justification: `catch_up()` is idempotent and
  concurrency-safe; if the transaction rolls back, the enqueued tasks
  operate on a non-regressed ticket and produce benign no-ops.

**Decision**: Option **(B)** — relax the constraint with a
narrowly-scoped exception in `fetcher-infrastructure.md`.

**Rationale**:

1. **Safety is guaranteed by the `catch_up()` contract itself**:
   - `catch_up()` does NOT read ticket status as a precondition (spec:
     "no guard on ticket status is required before executing catch_up()")
   - `catch_up()` is idempotent (MUST-level requirement in BaseFetcher)
   - Mutations produced by `catch_up()` delegate to service modules that
     acquire independent `FOR UPDATE` locks and call
     `reconcile_ticket_status()` — which evaluates the ticket's
     **committed** state at that point

2. **Timing makes the race condition benign**: catch-up tasks perform
   external HTTP calls (200-2000ms) before mutating the database. By
   the time a worker reaches its first DB mutation, the original
   transaction has long committed (it completes in single-digit
   milliseconds after the enqueue)

3. **Rollback scenario is harmless**: if the original transaction
   rolls back, the ticket was never actually reactivated. Catch-up
   tasks run, fetch factually correct external data, delegate to
   service modules → `reconcile_ticket_status()` sees the ticket in
   its unchanged inactive state → no spurious status transition.
   The externally-fetched data (CVSS scores, release status) remains
   factually correct regardless of ticket status

4. **Option A (after_commit hook) adds disproportionate complexity**:
   hook lifecycle management, closure capture, deduplication on
   multiple reconcile calls within one transaction, and a subtle
   change to `reconcile_ticket_status()`'s execution semantics — all
   for a safety property already guaranteed by the catch_up() contract

5. **`reconcile_ticket_status()` is not a pure evaluator** — it
   already has side effects (inactive assignee sanitization, audit
   events, revisit queue). Adding catch-up enqueue is incremental,
   not a paradigm shift

**Implementation note**: add a narrowly-scoped exception to
`fetcher-infrastructure.md` at the post-commit enqueue rule:

> Exception: when enqueued from within `reconcile_ticket_status()` as
> part of the internalized post-regression catch-up, the enqueue occurs
> before the caller's commit. This is safe because: (1) `catch_up()`
> does not read ticket status as a precondition, (2) `catch_up()` is
> idempotent by contract, (3) mutations produced by catch-up delegate
> to service modules that acquire independent locks and respect the
> ticket's committed state at execution time.

**Status**: Resolved — Option B selected.

### OP-2: Condition excludes inactive → Resolved transitions (Medium)

The proposed condition is:

```
effective_previous ∈ {Resolved, Ignored, Duplicated}
AND new_status ∈ {Analysis, Analyzed}
```

This misses the case where a ticket exits an inactive status and
immediately satisfies all gates → `new_status = Resolved`. In that
scenario, eligibility data may be stale (e.g., default CVSS version
changed while ticket was inactive), but catch-up does NOT fire because
`Resolved ∉ {Analysis, Analyzed}`.

The current design in `ticket-service.md` fires catch-up
unconditionally after un-ignore/un-duplicate, regardless of final status.

**Options**:

- **(A)** Broaden condition to: `effective_previous ∈ {Resolved, Ignored,
  Duplicated} AND new_status ∉ {Ignored, Duplicated}` — catch-up fires
  for any non-manual-zone target (including Resolved)
- **(B)** Make catch-up unconditional when `effective_previous` is an
  inactive status: `effective_previous ∈ {Resolved, Ignored, Duplicated}`
  — regardless of `new_status`
- **(C)** Keep current condition but accept the gap: argue that a ticket
  going directly from Inactive → Resolved has correct state (gates
  satisfied means all tracks are resolution-complete, so stale
  eligibility doesn't matter for the resolved outcome). The async
  catch-up would re-reconcile anyway when it completes.
- **(D)** Use transition detection: `effective_previous ∈ {Resolved,
  Ignored, Duplicated} AND new_status ≠ effective_previous` — catch-up
  fires whenever a ticket actually leaves an inactive state, regardless
  of target.

**Decision**: Option **(D)** — transition detection condition.

**Rationale**:

1. **Covers the Ignored/Duplicated → Resolved gap**: when a ticket is
   un-ignored/un-duplicated and all gates happen to be satisfied, the
   catch-up fires (Ignored ≠ Resolved, Duplicated ≠ Resolved). This
   matches the current `ticket-service.md` behavior that fires catch-up
   unconditionally after reactivation

2. **Excludes Resolved → Resolved (no-op)**: when a mutation on a
   Resolved ticket doesn't break the gates, `reconcile_ticket_status()`
   is called but produces no transition. With Options A or B, the
   catch-up would fire spuriously on every package mutation on a Resolved
   ticket — wasteful and incorrect. Option D correctly excludes this
   because `Resolved = Resolved` → condition FALSE

3. **Simpler than Option A**: no need to enumerate excluded target states
   — the `≠ effective_previous` check is universal and self-explanatory

4. **Complete by construction**: `reconcile_ticket_status()` can only
   produce {Analysis, Analyzed, Resolved}. It cannot produce Ignored or
   Duplicated (manual-zone states). Therefore the only case where
   `new_status = effective_previous` within the inactive set is
   `Resolved → Resolved` — exactly the no-op to exclude

**Verification matrix**:

| Transition | effective_previous | new_status | Fires? | Correct? |
|---|---|---|---|---|
| Resolved → Analysis | Resolved | Analysis | ✓ (≠) | Yes — regression |
| Resolved → Analyzed | Resolved | Analyzed | ✓ (≠) | Yes — regression |
| Resolved → Resolved | Resolved | Resolved | ✗ (=) | Yes — no-op |
| Ignored → Analysis | Ignored | Analysis | ✓ (≠) | Yes — reactivation |
| Ignored → Analyzed | Ignored | Analyzed | ✓ (≠) | Yes — reactivation |
| Ignored → Resolved | Ignored | Resolved | ✓ (≠) | Yes — reactivation |
| Duplicated → * | Duplicated | Any | ✓ (≠) | Yes — reactivation |

**Status**: Resolved — Option D selected.

### OP-3: Bootstrap coverage for Celery workers (Dismissed)

**Concern**: Celery workers may start before the FastAPI lifespan seeds
`default_cvss_version`, causing `get_default_cvss_version()` to raise
when step 4 fires from a background task.

**Resolution**: Dismissed — deployment initialization order is controlled.
Migrations run before any process starts (per `architecture.md`). The
setting is seeded by an Alembic data migration as part of the deployment
pipeline. Workers cannot reach `reconcile_ticket_status()` step 4 on a
database where migrations have not yet completed.

**Status**: Dismissed — not a real gap.

## Implementation Plan

### Bootstrap Prerequisite: `default_cvss_version` seed

**Rationale**: `reconcile_ticket_status()` reads `default_cvss_version`
from the database in step 4. The setting must always exist at runtime.
`system-settings.md` declares `Initial value: "3.1"` but does not
currently specify an enforcement mechanism.

**Mechanism**: Alembic data migration (primary) + FastAPI lifespan event
(defense-in-depth):

```sql
-- Alembic data migration (runs before any process starts):
INSERT INTO system_settings (key, value)
VALUES ('default_cvss_version', '3.1')
ON CONFLICT (key) DO NOTHING;
```

```python
# FastAPI lifespan event (defense-in-depth, self-healing):
INSERT INTO system_settings (key, value)
VALUES ('default_cvss_version', '3.1')
ON CONFLICT (key) DO NOTHING;
```

Properties:
- **Idempotent**: if the setting already exists (e.g., Admin changed it
  to `"4.0"`), the INSERT is a no-op
- **Self-healing**: if the row is accidentally deleted, the next
  application restart restores the default
- **Multi-replica safe**: `ON CONFLICT DO NOTHING` handles concurrent
  startup of multiple API server instances without race conditions
- **Process-order independent**: the Alembic migration guarantees the
  setting exists before any process (API server, Celery worker, RabbitMQ
  consumer) starts. The FastAPI lifespan seed is redundant but harmless

**Failure behavior invariant**: `get_default_cvss_version()` raises if
the setting is absent — this indicates a deployment or data integrity
error, not a recoverable condition. No hardcoded fallback is provided.
A missing setting means migrations have not been applied correctly.

**Documentation**: update `docs/features/platform/system-settings.md` to
add a "Bootstrap" section documenting this startup behavior and
referencing the `Initial value` property already declared in the settings
table.

### Phase 1: Modify `ticket-mutations.md`

**File**: `docs/features/tickets/ticket-mutations.md`

1. Update `reconcile_ticket_status()` behavior section (currently lines
   134-241) to add the post-transition catch-up step:
   - After step 3 (update status + audit event + inactive assignee handling),
     add step 4: detect inactive-state exit using `effective_previous ∈
     {Resolved, Ignored, Duplicated} AND new_status ≠ effective_previous`
     and execute `recalculate_cvss_chain()` + `catch_up()` enqueue
   - Document that `effective_previous` is resolved from the `previous_status`
     parameter if provided (reactivation cases), otherwise from the ticket's
     status before gate evaluation (regression cases)
   - Document that `default_cvss_version` is read from
     `settings_service.get_default_cvss_version(db)` inside step 4, only when
     the regression condition is true

2. Update the "Side effects" block (currently lines 144-151) to add:
   - May call `recalculate_cvss_chain()` when a inactive → active transition
     is detected (producing `severity_changed` and
     `product_eligibility_changed` audit events if derived values change)
   - May enqueue `catch_up()` Celery tasks for registered fetchers when a
     inactive → active transition is detected

3. Remove the "Post-Regression Hook: Resolved → Active" section (currently
   lines 243-280) entirely — the "Pattern for callers" and the MUST clause are
   no longer needed.

4. Update the `_reenter_gate_zone()` helper documentation to note that catch-up
   is now handled internally by `reconcile_ticket_status()` — no post-commit
   catch-up action is needed by the calling function or endpoint handler.

5. Simplify `upsert_cvss_assessment()` step 7d: replace the current text
   ("If this produces a backward transition from Resolved, the function
   invokes `recalculate_cvss_chain()` and enqueues `catch_up()` per the
   post-regression hook contract. The post-regression hook is handled
   internally — callers do not need to check for regression") with simply:
   "Call `reconcile_ticket_status()`". The catch-up logic is now internal
   to reconcile — no per-function behavioral description is needed.

6. Update the Callers table: rewrite both the "Post-regression from Resolved"
   row and the "Ticket reactivation (un-ignore, un-duplicate)" row to document
   that catch-up is now an internal responsibility of
   `reconcile_ticket_status()`, not a caller/endpoint-handler responsibility.

### Phase 2: Simplify `ticket-service.md`

**File**: `docs/features/tickets/ticket-service.md`

1. Simplify the "Ticket Reactivation" section: remove the requirement for
   endpoint handlers to call `recalculate_cvss_chain()` and enqueue `catch_up()`
   after `_reenter_gate_zone()`. Replace with a note that catch-up is handled
   internally by `reconcile_ticket_status()` when it detects a inactive → active
   transition. In particular, replace the phrase "After `_reenter_gate_zone()`
   commits the status transition, the endpoint handler executes:" with language
   that clarifies catch-up is handled internally during
   `reconcile_ticket_status()` execution — not as a post-commit endpoint handler
   responsibility.

2. The section can be retained as a conceptual explanation of WHY catch-up is
   needed (data staleness during inactive period), but the HOW is now simply
   "handled by `reconcile_ticket_status()`".

3. Ensure the term "inactive status" explicitly includes Resolved, Ignored, and
   Duplicated — aligning with the authoritative definition in `tickets.md`
   (line 696). The current text only mentions Ignored and Duplicated; this
   creates a terminology inconsistency that must be corrected.

### Phase 3: Update `cvss-scoring.md`

**File**: `docs/features/tickets/cvss-scoring.md`

1. Update the "Severity is recalculated whenever" list (lines 265-274):
   merge the two separate bullets for reactivation (lines 265-267:
   "A ticket is reactivated from Ignored or Duplicated status —
   `recalculate_cvss_chain()` is called synchronously during the
   reactivation, plus `catch_up()` tasks are enqueued for catch-up") and
   regression (lines 271-274: "called synchronously by the caller of
   `reconcile_ticket_status()` when a backward transition from Resolved is
   detected") into a single bullet that covers all inactive → active
   transitions uniformly:

   > A ticket transitions from an inactive status (Resolved, Ignored,
   > Duplicated) to an active status — `recalculate_cvss_chain()` is
   > called synchronously by `reconcile_ticket_status()` when it detects
   > the transition, plus `catch_up()` tasks are enqueued internally

2. Update the "Ticket Reactivation: CVSS Catch-Up" section (line 754):
   replace "Asynchronous (enqueued after commit)" with "Asynchronous
   (enqueued during `reconcile_ticket_status()` execution, before the
   caller's commit — safe per OP-1 resolution)".

3. Update the cross-references section (line ~766-768): the current text
   references the "post-regression hook" section of `ticket-mutations.md`
   which will be removed. Replace with a reference to the internalized
   step 4 behavior in `reconcile_ticket_status()`.

4. Normalize terminology: replace "non-active state" with "inactive status
   (Resolved, Ignored, Duplicated)" for consistency with the authoritative
   definition in `tickets.md`.

### Phase 4: Add exception to `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`

1. At the post-commit enqueue rule (line 692-696), add a narrowly-scoped
   exception:

   > Exception: when enqueued from within `reconcile_ticket_status()` as
   > part of the internalized post-regression catch-up, the enqueue
   > occurs before the caller's commit. This is safe because:
   > (1) `catch_up()` does not read ticket status as a precondition,
   > (2) `catch_up()` is idempotent by contract, (3) mutations produced
   > by catch-up delegate to service modules that acquire independent
   > locks and respect the ticket's committed state at execution time.

2. Update the "Invocation points" list (line 716-723): rewrite the three
   bullet points to reflect that all catch-up enqueue now originates
   exclusively from inside `reconcile_ticket_status()` (not from endpoint
   handlers or direct callers).

### Phase 5: Optional clarification in `package-service.md`

**File**: `docs/features/packages/package-service.md`

1. Optionally add a brief note in the module overview or architecture section
   clarifying that `package_service` functions call
   `reconcile_ticket_status()` and the post-regression catch-up (if any) is
   handled internally — no caller action needed. This is informational only;
   the behavior is already correct without this note.

### Phase 6: Resolve finding TKM-GAP-18

After the spec changes are committed:

1. Update `docs/reviews/ticket-mutations.md`: mark TKM-GAP-18 as RESOLVED
   with compact format:
   `**Status**: RESOLVED — Internalized post-regression catch-up inside reconcile_ticket_status(); caller delegation eliminated (<date>)`

2. Update `docs/reviews/.tracking.json`: decrement GAP Medium count for
   `ticket-mutations` (M: 1 → 0), increment resolved count (38 → 39).

3. Update `docs/reviews/README.md`: reflect updated counts.

### Phase 7: Post-fix review

Run the following reviewers on `ticket-mutations.md` to verify the changes
are coherent:

- **spec-gap-analyzer** — verify no new gaps introduced by the refactoring
- **spec-coherence-reviewer** — verify consistency with `ticket-service.md`,
  `package-service.md`, `cvss-scoring.md`, and other referencing specs

If the simplification of `ticket-service.md` is significant, also run:

- **spec-gap-analyzer** on `ticket-service.md`
- **spec-coherence-reviewer** on `ticket-service.md`

### Phase 8: Delete this draft

Once all changes are applied, reviewed, and the finding is resolved:

- Delete `docs/drafts/internalize-post-regression-catchup.md`

## Cross-References

- `docs/features/tickets/ticket-mutations.md` — primary target
- `docs/features/tickets/ticket-service.md` — secondary target
- `docs/features/packages/package-service.md` — optional clarification
- `docs/features/tickets/cvss-scoring.md` — context (CVSS write-path)
- `docs/features/platform/fetcher-infrastructure.md` — context (`catch_up()`,
  `get_catch_up_fetchers()`)
- `docs/reviews/ticket-mutations.md` — finding TKM-GAP-18 to resolve
