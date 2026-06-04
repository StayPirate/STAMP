# Draft: Orphaned Ticket Status — Decoupling Assignment from the Analysis Gate

**Status**: Draft — work in progress  
**Started**: 2026-06-03  
**Affects**: `ticket-mutations.md`, `tickets.md`, `ticket-service.md`,
`user-service.md`, and all specs that reference the Analysis gate

> **Note**: this draft is a transitional document. It captures the reasoning
> and design decisions made during this change. Once all affected specification
> files have been updated, this draft will be deleted. The architectural
> invariant described here will live permanently in `tickets.md`, not in this
> draft.

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
| **Genuinely new** | CVE just ingested from NVD, never seen by anyone | Full triage from scratch |
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

This leads to the core design principle that will be formalized as an invariant
in `tickets.md` (see "Architectural Invariant" below).

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
- `reconcile_ticket_status` skips tickets in `New` status entirely (guard
  clause). It operates only on tickets already in the gate zone (`Analysis`,
  `Analyzed`, `Resolved`). Its floor is hardcoded to `Analysis`.
- Unassignment (system-initiated) clears `assignee_id` and creates the audit
  event, but does **not** change the ticket status. The ticket stays in
  whatever gate-zone status it had reached — including `Analyzed` or `Resolved`
  if those gates are still satisfied.

### `_reenter_gate_zone` — no `floor` parameter needed

Tickets exiting the manual zone (`Ignored`, `Duplicated`) via reopen or
revert-duplicate must re-enter the gate zone at the correct level. The initial
design proposed a `floor` parameter on `reconcile_ticket_status` to allow
`_reenter_gate_zone` to pass `floor=New` and leave tickets at `New` when no
gate-relevant data exists. This was rejected as unnecessary complexity.

**Simpler solution**: `_reenter_gate_zone` sets `status = Analysis` (not
`New`) before calling `reconcile_ticket_status`. The cascade then promotes to
`Analyzed` or `Resolved` if the corresponding gates are met, or leaves the
ticket at `Analysis` if not.

```
_reenter_gate_zone(ticket, db):
  previous = ticket.status              # Ignored or Duplicated
  ticket.status = Analysis              # Re-enter at gate zone floor
  reconcile_ticket_status(ticket, db, previous_status=previous)
  # Audit event records: Ignored → Analysis/Analyzed/Resolved (not New → ...)

reconcile_ticket_status(ticket, db, previous_status=None):
  if ticket.status == New:
    return                              # Pre-state — not in the gate zone

  if resolved_gates AND analyzed_gates → Resolved
  if analyzed_gates                    → Analyzed
  otherwise                           → Analysis  # Hardcoded floor
```

**Trade-off: `New → Ignored → Reopen`**

Under this design, a ticket that was `New → Ignored` (ignored before ever being
assigned to a VA) re-enters at `Analysis` on reopen, not at `New`. This is
a deliberate and acceptable trade-off:

- Reopening a ticket is an explicit act by someone who has decided the ticket
  is relevant. Placing it in the "Unassigned" queue at `Analysis` surfaces it
  ahead of genuinely untouched tickets — which is the right priority.
- If the actor reopening holds the VA role, `auto_assign_actor` triggers and
  the ticket gets assigned immediately. `Analysis` is the correct status.
- If the actor does not hold the VA role, the ticket lands in `Analysis` with
  `assignee_id = NULL` and appears in the unassigned queue for a VA to claim.
  This is also correct — the ticket has been deliberately reopened and should
  be visible.

The alternative (re-entering at `New`) would bury a deliberately reopened
ticket in the noise of fresh CVE ingestion. The chosen behaviour is
semantically sounder.

---

## Architectural Invariant

This invariant will be added to `tickets.md` (in the "Ticket Statuses" or
"Automatic Status Evaluation" section) when the change is applied. It replaces
the current **Consequence** block in `ticket-mutations.md` (lines 201-208).

> **Ticket status reflects work state, not staffing state.** The status of a
> ticket represents the progress of the analysis work, never the assignment
> state. Assignment is an orthogonal staffing concern. Consequently:
>
> - A ticket in `Analysis`, `Analyzed`, or `Resolved` status may have
>   `assignee_id = NULL` (an orphaned ticket awaiting reassignment). This is a
>   valid and expected state.
> - `New` is a pre-state: it means the ticket has never been claimed by a VA.
>   Once a ticket transitions from `New` to `Analysis`, it never returns to
>   `New` under normal operation.
> - `reconcile_ticket_status` never pushes a ticket below `Analysis`. The
>   floor of the gate zone is `Analysis`, not `New`.

---

## Open Points

1. **Auto-added packages and the New queue**: with the new model, tickets
   where CPE mapping finds matches will stay in `New` until a VA first claims
   them (since the `New → Analysis` transition is triggered by assignment, not
   by package addition). Tickets without CPE matches also stay in `New`. The
   `New` queue therefore contains only tickets that have never been touched by
   a VA — genuinely undifferentiated. Confirm this is the intended behavior.

2. **Orphaned ticket queue naming**: the existing `assignee=none` API filter
   combined with status filters covers the orphaned ticket queue
   (`?assignee=none&status=analysis`, `?assignee=none&status=analyzed`).
   Consider whether the UI should surface this as a named view (e.g.,
   "Unassigned" or "Needs Owner") and whether the `GET /api/v1/tickets` spec
   needs any additional filtering convenience (e.g., an `unassigned=true`
   boolean shorthand).

3. **Impact on affected specs**: the Analysis gate (`assignee_id IS NOT NULL`)
   is referenced in approximately 7 specification documents across ~23
   locations. All references must be updated when the change is applied:
   - `docs/features/tickets/tickets.md` — status transitions table, gate
     definitions, auto-assignment section, `_reenter_gate_zone` description,
     add architectural invariant
   - `docs/features/tickets/ticket-mutations.md` — `reconcile_ticket_status`
     gate conditions and signature (add `New` guard, remove Analysis gate,
     hardcode floor), `_reenter_gate_zone` (set `Analysis` not `New`),
     replace **Consequence** block with reference to the invariant in
     `tickets.md`
   - `docs/features/tickets/ticket-service.md` — Analysis gate references in
     `assign_ticket` and other functions
   - `docs/features/identity/user-service.md` — `_unassign_active_tickets`:
     remove the `reconcile_ticket_status` call and regression note; status is
     no longer changed by unassignment
   - `docs/features/packages/package-model.md` — Analysis gate references
   - `docs/features/tickets/cve-tracking.md` — gate references in rejection
     revert flow
   - `docs/data-model.md` — if the Analysis gate is documented there

4. **Inactive assignee sanitization**: the sanitization step in
   `reconcile_ticket_status` (which detects inactive assignees and clears
   them) remains useful as a catch-up mechanism. However, since clearing the
   assignee no longer causes status regression, step 4 of the sanitization
   ("Re-evaluate the gates") becomes a no-op for the status and should be
   removed from the spec. The assignee is cleared and the audit event is
   created — no further gate re-evaluation is needed.

---

## Proposed Changes (high-level)

### `reconcile_ticket_status` — behavior change (no signature change)

No new parameters. The only changes are:

1. Add a guard clause at the top: if `ticket.status == New`, return immediately.
   `New` is a pre-state outside the gate zone.
2. Remove `assignee_id IS NOT NULL` from the Analysis gate condition (it no
   longer exists as a gate).
3. The fallthrough case is hardcoded to `Analysis` (was `New`).

```
reconcile_ticket_status(ticket, db, previous_status=None):
  # New is a pre-state — assignment logic handles New → Analysis explicitly
  if ticket.status == New:
    return

  if resolved_gates AND analyzed_gates → Resolved
  if analyzed_gates                    → Analyzed
  otherwise                           → Analysis   # Hardcoded floor
```

### `_reenter_gate_zone` — entry point change

Set `status = Analysis` instead of `status = New`. No other change.

```
_reenter_gate_zone(ticket, db):
  previous = ticket.status              # Ignored or Duplicated
  ticket.status = Analysis              # Floor of the gate zone (was: New)
  reconcile_ticket_status(ticket, db, previous_status=previous)
```

### `New → Analysis` transition — made explicit

The transition moves from an implicit gate evaluated by `reconcile_ticket_status`
to an **explicit one-way event** triggered by:
- `auto_assign_actor()` — auto-assignment on first VA touch
- `assign_ticket()` — explicit assignment endpoint

When the ticket is in `New` and assignment sets `assignee_id`, the assignment
logic sets `status = Analysis` directly before calling
`reconcile_ticket_status`. `reconcile_ticket_status` then evaluates from
`Analysis` upward and may further promote to `Analyzed` or `Resolved` if gates
are already met.

### `_unassign_active_tickets` — simplification

The per-ticket call to `reconcile_ticket_status` is **removed**. Status is no
longer affected by unassignment. The function only clears `assignee_id` and
creates the audit events.

### Inactive assignee sanitization — simplification

Step 4 of the sanitization ("Re-evaluate the gates — since the Analysis gate
`assignee_id IS NOT NULL` is no longer satisfied, the ticket regresses") is
removed. The assignee is cleared, the audit event is created, no status change
occurs.

### Invariant — replacement

The **Consequence** block in `ticket-mutations.md` (lines 201-208) is removed.
A reference to the architectural invariant defined in `tickets.md` is added in
its place. The invariant text is defined in the "Architectural Invariant"
section above.
