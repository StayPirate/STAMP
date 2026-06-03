# Draft: Orphaned Ticket Status — Decoupling Assignment from the Analysis Gate

**Status**: Draft — work in progress  
**Started**: 2026-06-03  
**Affects**: `ticket-mutations.md`, `tickets.md`, `ticket-service.md`,
`user-service.md`, and all specs that reference the Analysis gate

---

## Problem Statement

When a ticket that has already been processed (in `Analysis` or `Analyzed`
status) loses its assignee because the VA is deactivated or loses the VA role,
the current gate system regresses the ticket back to `New`. This is caused by
the Analysis gate condition: `assignee_id IS NOT NULL`.

The consequence is that the `New` queue contains two semantically different
populations:

| Type | Example | What it needs |
|------|---------|---------------|
| **Genuinely new** | CVE just ingested from NVD, never seen | Full triage from scratch |
| **Orphaned (regressed)** | Was `Analyzed` with packages, tracks, CVSS, and affectedness data — VA was deactivated | Only reassignment |

A VA looking at the `New` queue cannot distinguish between them. A
nearly-resolved ticket with hours of work may be buried among hundreds of
fresh CVEs. This causes priority inversion and loss of operational context.

---

## Discussion Summary

### Options considered

**Option A — UI differentiation only**: keep the current model, differentiate
orphaned tickets in the frontend using the audit trail (previous status, reason
for unassignment). Requires no model change. Rejected as a cosmetic workaround:
the API does not expose the distinction natively, and the problem persists in
the data model.

**Option B — Decouple assignee from the Analysis gate**: remove
`assignee_id IS NOT NULL` from the gate conditions so that a ticket can be in
`Analysis` (or higher) without an assignee. The existing `?assignee=none` API
filter (already specified in `tickets.md`) provides the orphaned ticket queue
at no additional cost.

**Option C — New `Unassigned` status**: a dedicated status for tickets that
were in progress but lost their assignee. Rejected as over-engineering: adds a
seventh status, new transitions, and new gate logic for an event that should be
relatively rare.

**Chosen approach: Option B**, extended with the refinements described below.

### Architectural rationale for Option B

The deeper justification for Option B is **dimension orthogonality**, a
principle already established in the project (Guardrail 24). Assignment is a
staffing dimension; workflow status is a progress dimension. Coupling them
causes the workflow state to be corrupted by a staffing event (VA deactivation)
that has no bearing on the actual state of the work on the ticket.

A ticket with packages, decided tracks, CVSS assessments, and affectedness data
is factually in an analysis state — regardless of who is currently assigned to
it. The current model loses that semantic information by regressing to `New`.

### Gate redefinition discussion

**Problem**: if `assignee_id IS NOT NULL` is removed from the Analysis gate,
what distinguishes `New` from `Analysis` in `reconcile_ticket_status`?

**Candidate 1 — track status-based gate**: a ticket enters Analysis if it has
at least one active `TicketPackageTrack` with status other than `ANALYSIS`.
This would mean auto-added tracks (CPE mapping) do not trigger the gate since
they start in `ANALYSIS` status. Rejected: a VA who added SUSE CVSS data but
has not yet decided any tracks would regress to `New` upon unassignment —
losing visible work.

**Candidate 2 — `first_assigned_at` field**: add a nullable `timestamptz`
column to `Ticket`. The Analysis gate becomes `assignee_id IS NOT NULL OR
first_assigned_at IS NOT NULL`. Rejected: the field is unnecessary because the
ticket status itself is the signal. Once the ticket has been promoted to
`Analysis`, it never returns to `New` under normal operation — the status is
the historical record.

**Chosen approach — `New` as a pre-state, one-way `New → Analysis` transition**:

- `New` is not part of the gate zone. It is a pre-state meaning "this ticket
  has never been claimed by a VA."
- The transition `New → Analysis` is a **one-way, irreversible event** triggered
  by the first assignment (auto or manual). It is not evaluated by
  `reconcile_ticket_status` — it is an explicit side effect of the assignment
  operation.
- `reconcile_ticket_status` operates only on tickets already in the gate zone
  (`Analysis`, `Analyzed`, `Resolved`). Its floor is `Analysis` — it can never
  push a ticket below `Analysis`.
- Unassignment (system-initiated) clears `assignee_id` and creates the audit
  event, but does **not** change the ticket status. The ticket stays in
  `Analysis` (or whatever higher status it had reached).

### `_reenter_gate_zone` and the `floor` parameter

Tickets exiting the manual zone (`Ignored`, `Duplicated`) via reopen or
revert-duplicate must re-enter the gate zone at the correct level. The current
mechanism:

1. Sets `status = New` (lowest entry point)
2. Calls `reconcile_ticket_status()` which promotes to the correct level

This must continue to work correctly under the new model. The key insight is:

- A ticket that went `New → Ignored` (before ever being assigned) should return
  to `New` on reopen — it was never triaged.
- A ticket that went `Analysis/Analyzed → Ignored` should return to
  `Analysis`/`Analyzed` — it had work in progress.

`reconcile_ticket_status` as described above cannot return `New` (its floor is
`Analysis`). If called after `_reenter_gate_zone` sets `status = New`, it
would incorrectly promote all tickets to at least `Analysis`.

**Solution**: introduce a `floor` parameter to `reconcile_ticket_status`:

```
reconcile_ticket_status(ticket, db, previous_status=None, floor=TicketStatus.Analysis):
  if resolved_gates AND analyzed_gates met  → Resolved
  if analyzed_gates met                     → Analyzed
  otherwise                                 → floor  ← Analysis by default, New from _reenter_gate_zone
```

- All normal callers omit `floor` → default `Analysis` → ticket cannot regress
  below `Analysis`.
- `_reenter_gate_zone` passes `floor=New` → the cascade can leave the ticket
  at `New` if no gate-relevant data exists.

This means:
- Ticket `New → Ignored` → reopen → `status = New`, `reconcile(floor=New)` →
  no data → stays `New`. Correct.
- Ticket `Analysis → Ignored` (had packages and CVSS) → reopen → `status =
  New`, `reconcile(floor=New)` → Analyzed gates partially met → `Analysis`.
  Correct.
- Ticket `VA deactivated, assignee = NULL, status = Analyzed` → any gate
  mutation → `reconcile(floor=Analysis)` → floor prevents regression below
  `Analysis`. Correct.

---

## Open Points

1. **Evaluate simpler flows to avoid the `floor` parameter**: the `floor`
   parameter solves the `_reenter_gate_zone` edge case cleanly, but adds a
   parameter to a core function. It is worth evaluating whether an alternative
   approach (e.g., `_reenter_gate_zone` independently evaluating the entry
   point before calling `reconcile`, or a separate code path for gate-zone
   re-entry) could achieve the same result with less coupling. This should be
   explored before finalizing the spec.

2. **Auto-added packages and the New queue**: with the new model, tickets
   where CPE mapping finds matches will stay in `New` until a VA first claims
   them (since the `New → Analysis` transition is triggered by assignment, not
   by package addition). Tickets without CPE matches also stay in `New`. This
   means the `New` queue still contains both types — but they are now
   genuinely undifferentiated (neither has been touched by a VA). Confirm this
   is the intended behavior.

3. **Orphaned ticket queue naming**: the current `assignee=none` API filter
   combined with `status=analysis` or `status=analyzed` covers the orphaned
   ticket queue. Consider whether the UI should surface this as a named view
   (e.g., "Unassigned" or "Needs Owner") and whether the `GET /api/v1/tickets`
   spec needs any additional filtering convenience (e.g., an `unassigned=true`
   boolean shorthand).

4. **Impact on package-model.md and other specs**: the Analysis gate is
   referenced in approximately 7 specification documents across ~23 locations.
   All references must be updated. A full list of affected documents:
   - `docs/features/tickets/tickets.md` (status transitions, gate definition,
     auto-assignment section, `_reenter_gate_zone` description)
   - `docs/features/tickets/ticket-mutations.md` (gate condition in
     `reconcile_ticket_status`, inactive assignee sanitization, the
     **Consequence** invariant block — all must be rewritten)
   - `docs/features/tickets/ticket-service.md` (Analysis gate references in
     `assign_ticket` and other functions)
   - `docs/features/identity/user-service.md` (`_unassign_active_tickets`
     description — the reconcile call and regression note must be removed)
   - `docs/features/packages/package-model.md` (Analysis gate references)
   - `docs/features/tickets/cve-tracking.md` (gate references in rejection
     revert flow)
   - `docs/data-model.md` (if the Analysis gate is documented there)

5. **Inactive assignee sanitization**: the current sanitization step in
   `reconcile_ticket_status` (which detects inactive assignees and clears them)
   still makes sense as a catch-up mechanism. However, since clearing the
   assignee no longer causes status regression, the "re-evaluate gates" step 4
   of the sanitization becomes a no-op for the status (but the assignee is
   still cleared correctly). This simplification should be reflected in the
   spec.

---

## Proposed Changes (high-level)

### `reconcile_ticket_status` signature change

Add a `floor: TicketStatus = TicketStatus.Analysis` parameter. The fallthrough
case in the gate evaluation uses `floor` instead of hardcoded `New`.

### `_reenter_gate_zone` change

Pass `floor=TicketStatus.New` when calling `reconcile_ticket_status`. No other
change.

### `New → Analysis` transition

Move from a gate condition evaluated by `reconcile_ticket_status` to an
explicit one-way event triggered by:
- `auto_assign_actor()` (auto-assignment on first VA touch)
- `assign_ticket()` (explicit assignment endpoint)

Both already call `reconcile_ticket_status` after assignment. The change is
that `reconcile_ticket_status` no longer needs `assignee_id IS NOT NULL` as a
gate — the promotion to `Analysis` happens as a side effect of assignment
itself, before `reconcile` is called.

Concretely: when the ticket is in `New` and assignment sets `assignee_id`,
before calling `reconcile_ticket_status`, the assignment logic sets `status =
Analysis`. `reconcile_ticket_status` then evaluates from `Analysis` upward
(with `floor=Analysis`) and may further promote to `Analyzed` or `Resolved` if
gates are met.

### `_unassign_active_tickets` simplification

The call to `reconcile_ticket_status` per ticket can be **removed** (or
retained only for the inactive assignee sanitization cleanup, which now has no
status side effect). Status is not changed by unassignment.

### Inactive assignee sanitization simplification

Step 4 of the sanitization ("Re-evaluate the gates — since the Analysis gate
`assignee_id IS NOT NULL` is no longer satisfied, the ticket regresses") is
removed. The assignee is cleared, the audit event is created, but no gate
re-evaluation for status is needed.

### Invariant update

The **Consequence** block in `ticket-mutations.md` (lines 201-208) which states
`assignee_id IS NULL on a non-final ticket implies New status` must be
**replaced** with the new invariant:

> **Invariant**: a non-final ticket in `Analysis`, `Analyzed`, or `Resolved`
> status may have `assignee_id = NULL` (orphaned ticket). The `New` status
> implies the ticket has never been claimed by a VA. Once a ticket transitions
> from `New` to `Analysis`, it never returns to `New` except via
> `_reenter_gate_zone` (which may set it back to `New` if no gate-relevant
> data is present).
