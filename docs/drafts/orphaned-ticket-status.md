# Draft: Orphaned Ticket Status — Decoupling Assignment from the Analysis Gate

**Status**: Draft — design complete, ready for implementation  
**Started**: 2026-06-03  
**Updated**: 2026-06-04 — design review and gap analysis incorporated; open points 5–9 added and resolved  
**Affects**: `ticket-mutations.md`, `tickets.md`, `ticket-service.md`,
`user-service.md`, `package-model.md`, `cve-tracking.md`, `data-model.md`,
`system-map.md`

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

The same trade-off applies to **system-initiated reopens via NVD rejection
revert**: a ticket that was `New → Ignored (NVD rejection) → Reopen (NVD
un-rejection)` re-enters at `Analysis` with `assignee_id = NULL`. This is
also deliberate — the system has reassessed the CVE as valid and the ticket
requires prompt attention. Surfacing it in the unassigned queue at `Analysis`
is semantically correct. Under the old model, these tickets would return to
`New` and be indistinguishable from fresh ingestion; under the new model,
they are visible as a distinct "needs owner" population.

### `New → Analysis` transition — ownership and audit events

**Where the promotion logic lives**: the explicit `New → Analysis` status set
lives in exactly two functions:

- **`auto_assign_actor()`** — for implicit assignment triggered by any
  modifying operation by a VA on an unassigned ticket. When
  `ticket.status == New`, `auto_assign_actor` sets `status = Analysis` and
  creates a `status_change` audit event (`New → Analysis`) in addition to the
  `assignment` event.
- **`assign_ticket()`** — for explicit assignment via the API endpoint. This
  function does not call `auto_assign_actor`, so it must independently check
  `if ticket.status == New: ticket.status = Analysis` and create the
  `status_change` audit event before calling `reconcile_ticket_status`.

**`create_ticket()` with a VA actor** already sets `status = Analysis` and
`assignee_id` directly (ticket-service.md, lines 191–192). This existing
behaviour is compatible with the new model and requires no change. It is a
third, pre-existing trigger point for the `New → Analysis` transition.

**Interaction with explicit-status operations (`ignore_ticket`,
`mark_as_duplicate`)**: when a VA performs `ignore_ticket` or
`mark_as_duplicate` on a `New` ticket, `auto_assign_actor` fires first and
sets `status = Analysis`. The caller then immediately sets the explicit status
(`Ignored` or `Duplicated`). The audit trail records two status events:
`New → Analysis` and `Analysis → Ignored` (or `Analysis → Duplicated`).

This is **correct and intentional behaviour**: the VA claimed the ticket as
part of their action, placing it momentarily in the analysis queue, before
choosing to act on it explicitly. The two-event audit trail accurately reflects
this sequence. The existing text in `tickets.md` (lines 472–475) — "the
assignee gate does not override explicit transitions" — was written against the
old gate-evaluation model and must be updated to describe the two-event
sequence instead.

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
> - When `assignee_id` is `NULL` on a ticket in `Analysis` or later, the
>   ticket's audit trail MUST contain an `assignment` event documenting the
>   unassignment. An orphaned ticket without a corresponding unassignment audit
>   event indicates a bug in the mutation path that cleared `assignee_id`.
>
> Tickets created directly by a VA (`create_ticket()` with a VA actor) start at
> `Analysis` and bypass `New` entirely — no `New → Analysis` transition occurs
> and no corresponding `status_change` audit event is expected on these tickets.

---

## Open Points

1. ~~**Auto-added packages and the New queue**~~: **Resolved.** Tickets where
   CPE mapping finds matches stay in `New` until a VA first claims them. The
   `New` queue contains only tickets that have never been touched by a VA —
   genuinely undifferentiated. This is the intended behavior: auto-added
   packages are pre-computed context data, not analyst work. The `New → Analysis`
   transition is triggered exclusively by the first assignment.

2. ~~**Orphaned ticket queue naming**~~: **Resolved.** No shorthand filter is
   needed. The existing `?assignee=none` filter combined with status filters
   (`?assignee=none&status=analysis`, `?assignee=none&status=analyzed`) is
   sufficient and already specified. A dedicated UI view ("Needs Owner" or
   similar) is out of scope for this change and can be evaluated independently
   as a UI feature.

3. ~~**Impact on affected specs**~~: **Resolved** (amended after reviewer
   analysis to add missed references). All references must be updated when the
   change is applied:
   - `docs/features/tickets/tickets.md` — status transitions table (line 285:
     update trigger/mode for `New → Analysis`), Automatic Status Evaluation
     section (lines 389–392: remove the Analysis gate tier and "Otherwise →
     New" fallthrough; replace with 3-tier evaluation and `Analysis` as
     unconditional floor), auto-assignment section (lines 466–475: remove
     "assignee gate" language; describe the `New → Analysis` explicit
     transition in `auto_assign_actor` and the two-event behaviour for
     ignore/duplicate callers), `_reenter_gate_zone` description (lines
     706–708: update "typically Analysis if an assignee is present" — the
     ticket enters at `Analysis` unconditionally, not conditional on assignee
     presence), add architectural invariant, PATCH assignee endpoint note
     (lines 1440–1448: remove "since the Analysis gate requires
     `assignee_id IS NOT NULL`, unassignment causes the ticket to regress to
     `New`" — under the new model unassignment does not change status)
   - `docs/features/tickets/ticket-mutations.md` — `reconcile_ticket_status`
     gate conditions (add `New` guard, remove Analysis gate tier entirely,
     hardcode floor to `Analysis`; spec must reflect 3-tier evaluation, not 4),
     `_reenter_gate_zone` (set `Analysis` not `New`; update the inline
     description at lines 577–582 in `reopen_from_ignored` and lines 615–621 in
     `revert_duplicate` — the outcome for a non-VA actor changes from `New` to
     `Analysis`, and "Non-VA actor (unassigned): New (assignee gate not met)" at
     line 621 must be updated to `Analysis`), replace **Consequence** block
     (lines 201–208) with reference to the invariant in `tickets.md`, update
     inactive-assignee sanitization (remove step 4, add warning log requirement),
     Auto-Assignment Rule section (lines 664–685): remove "the assignee gate
     (`assignee_id IS NOT NULL`) promotes the ticket to `Analysis` automatically"
     — replace with description of the explicit `New → Analysis` transition in
     `auto_assign_actor` and the two-event behaviour for ignore/duplicate callers,
     Contract section (lines 770–773): remove "assignee gate satisfaction"
     phrasing; update to reflect that `assign_ticket` calls
     `reconcile_ticket_status` to evaluate promotion from `Analysis` upward (not
     to satisfy a gate), update callers table (line 836): update or remove the
     `deactivate_user` entry — `_unassign_active_tickets` no longer calls
     `reconcile_ticket_status`
   - `docs/features/tickets/ticket-service.md` — `assign_ticket` (lines
     369–380): add an explicit `if ticket.status == New: ticket.status =
     Analysis` step and a `status_change` audit event creation before the
     `reconcile_ticket_status` call; update the rationale for calling
     `reconcile_ticket_status` (purpose changes from "satisfies the Analysis
     gate" to "evaluates further promotion from Analysis upward"), update
     architectural test requirement (lines 818–819): the test remains valid
     but the mechanism changes from gate evaluation to explicit transition
   - `docs/features/identity/user-service.md` — `_unassign_active_tickets`:
     remove step 5 (the `reconcile_ticket_status` call and the parenthetical
     explaining the Analysis gate regression); status is no longer changed by
     unassignment
   - `docs/features/packages/package-model.md` — Analysis gate references
   - `docs/features/tickets/cve-tracking.md` — gate references in rejection
     revert flow
   - `docs/data-model.md` — `_reenter_gate_zone` description
    - `docs/system-map.md` — Mermaid diagram: `_reenter_gate_zone` edges from
      `DUPLICATED`/`IGNORED` point to `ANALYSIS` (not `NEW`); the
      `NEW -.->|"evaluate promotes"| ANALYSIS` edge is replaced by an explicit
      assignment-triggered transition

   Additional updates identified during design review and gap analysis:
   - `docs/features/tickets/tickets.md` — ASCII Status Transition Diagram (near
     line 275): update `Ignored → New (system reopen)` to `Ignored → Analysis`
   - `docs/features/tickets/tickets.md` — PATCH assignee endpoint note (lines
     1440–1448): replace the regression-to-New explanation with "system-initiated
     unassignment clears the assignee but does not change ticket status — the
     ticket remains in its current gate-zone status and is visible in the orphaned
     ticket queue (`?assignee=none`)"
   - `docs/features/tickets/ticket-mutations.md` — `auto_assign_actor`
     responsibility note (lines 732–735): update "performs assignment only" to
     also describe the conditional `New → Analysis` promotion and `status_change`
     event creation (see Implementation Notes)
   - `docs/features/tickets/ticket-mutations.md` — `revert_duplicate` non-VA
     outcome (line 621): replace "New (assignee gate not met)" with "Analysis,
     Analyzed, or Resolved based on gates"
   - `docs/features/tickets/ticket-mutations.md` — Callers table (line 836):
     **remove** (not merely update) the `deactivate_user` row —
     `_unassign_active_tickets` no longer calls `reconcile_ticket_status` after
     this change
   - `docs/features/tickets/ticket-mutations.md` — Contract section (lines
     770–773): replace "assignee gate satisfaction" with "promotion evaluation
     after assignment — `assign_ticket` calls `reconcile_ticket_status` to
     evaluate whether the ticket's existing data satisfies gates above `Analysis`"
   - `docs/features/identity/user-service.md` — Transactionality section: remove
     "status reconciliation" from the atomic transaction description for VA role
     removal; unassignment no longer triggers a status change
   - `docs/features/identity/user-service.md` — `_unassign_active_tickets`: add
     explicit per-ticket `SELECT ... FOR UPDATE` before clearing `assignee_id`
     (see Proposed Changes and Implementation Notes)

4. ~~**Inactive assignee sanitization**~~: **Resolved.** Steps 1-3 of the
   sanitization (detect inactive assignee → clear `assignee_id` → create audit
   event) remain in `reconcile_ticket_status` as a catch-up safety net. Step 4
   ("Re-evaluate the gates") is removed — clearing the assignee no longer
   affects ticket status. The **Consequence** block is removed. A warning log
   requirement is added: when the sanitization detects an inactive assignee, it
   MUST emit a warning-level log entry (e.g., `"Inactive assignee {user_id}
   detected on ticket {ticket_id} during reconciliation — this should have been
   handled by _unassign_active_tickets"`). This ensures that bugs in the primary
   unassignment path (`_unassign_active_tickets`) are not silently masked by the
   catch-up mechanism.

5. ~~**`New → Analysis` promotion duplication**~~: **Resolved.** The promotion
   logic is intentionally duplicated across `auto_assign_actor()` and
   `assign_ticket()` because the two functions carry distinct semantics that
   must not be merged. `auto_assign_actor` is a system-initiated implicit action
   (the VA modifies the ticket; the system assigns them as a side effect);
   `assign_ticket` is an explicit user-initiated action. Merging them via
   `auto_assign_actor(force=True)` would obscure this distinction and produce
   incorrect audit event attribution: `assignment` events from `auto_assign_actor`
   use `user_id = NULL` (system); those from `assign_ticket` use the acting
   user's ID. The duplication risk is mitigated by an architectural test
   requirement (see Proposed Changes).

6. ~~**`create_ticket()` invariant exception**~~: **Resolved.** Tickets created
   directly by a VA (`create_ticket()` with a VA actor) start at `Analysis`
   without passing through `New`. The Architectural Invariant is updated with an
   explicit exception sentence to prevent audit trail consumers from interpreting
   the absence of a `New → Analysis` audit event as a bug.

7. ~~**Two-event behavior for `assign_ticket()`**~~: **Resolved.** When
   `assign_ticket()` is called on a `New` ticket, it produces two `status_change`
   events if gates are satisfied: first `New → Analysis` (created explicitly by
   `assign_ticket` before calling `reconcile_ticket_status`), then `Analysis →
   Analyzed` or `Analysis → Resolved` (from `reconcile_ticket_status` if gate
   conditions are already met). This mirrors the documented two-event pattern for
   `auto_assign_actor` callers (Discussion Summary, lines 190–203). Specified in
   Proposed Changes.

8. ~~**`status_change` audit event fields for `New → Analysis`**~~:
   **Resolved.** Both `auto_assign_actor()` and `assign_ticket()` use
   `user_id = NULL` for the `status_change` event when promoting `New →
   Analysis`. The promotion is an automatic system consequence of the assignment
   action, not a deliberate user choice — consistent with the project convention
   that "all automatic transitions create a `TicketAuditEvent` with `user_id =
   NULL`" (`tickets.md` line 399). Full field spec in Proposed Changes.

9. ~~**`New + assignee_id IS NOT NULL` invariant violation detection**~~:
   **Resolved.** A warning log is added to `reconcile_ticket_status` to detect
   the invariant violation `ticket.status == New AND ticket.assignee_id IS NOT
   NULL`. Under the new model this combination cannot exist under correct
   operation — it indicates a bug in an assignment code path that set `assignee_id`
   without transitioning status to `Analysis`. The warning is emitted inside the
   `New` guard clause before returning. Specified in Implementation Notes.

---

## Proposed Changes (high-level)

### `reconcile_ticket_status` — behavior change (no signature change)

No new parameters. The only changes are:

1. Add a guard clause at the top: if `ticket.status == New`, return immediately.
   `New` is a pre-state outside the gate zone.
2. Remove the Analysis gate tier entirely. The Analysis gate (`assignee_id IS
   NOT NULL`) no longer exists as a conditional tier — `Analysis` is the
   unconditional floor.
3. The fallthrough case is hardcoded to `Analysis` (was `New`).

After this change, the evaluation collapses from four tiers to three:

```
reconcile_ticket_status(ticket, db, previous_status=None):
  # New is a pre-state — assignment logic handles New → Analysis explicitly
  if ticket.status == New:
    return

  # Three-tier evaluation (Analysis gate tier removed entirely)
  if resolved_gates AND analyzed_gates → Resolved
  if analyzed_gates                    → Analyzed
  otherwise                           → Analysis   # Unconditional floor
```

The spec updates to `tickets.md` (Automatic Status Evaluation section,
lines 389–392) and `ticket-mutations.md` must reflect this as an elimination
of the Analysis gate tier, not merely a change to its condition. A reader
looking at the updated spec should find three tiers, not four.

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
to an **explicit one-way event**. The promotion logic (`if ticket.status == New:
ticket.status = Analysis`) lives in:

- **`auto_assign_actor()`** — for implicit assignment triggered by any modifying
  operation by a VA on an unassigned ticket. Sets `status = Analysis` and
  creates a `status_change` audit event before returning to the caller.
- **`assign_ticket()`** — for explicit assignment via the API endpoint (does not
  call `auto_assign_actor`). Must independently set `status = Analysis` and
  create the `status_change` audit event before calling `reconcile_ticket_status`.

`create_ticket()` with a VA actor is a pre-existing third trigger that already
sets `status = Analysis` directly; no change is needed there.

After either function sets `status = Analysis`, `reconcile_ticket_status` is
called and evaluates from `Analysis` upward, potentially promoting to `Analyzed`
or `Resolved` if gates are already met. The `status_change` audit event for
`New → Analysis` is the responsibility of the function that performs the set,
not of `reconcile_ticket_status`.

For operations that call `auto_assign_actor` and then immediately set an
explicit status (`ignore_ticket` → `Ignored`, `mark_as_duplicate` →
`Duplicated`): `auto_assign_actor` sets `Analysis`, the caller then sets the
explicit status. The audit trail records two `status_change` events —
`New → Analysis` and `Analysis → Ignored` (or `Duplicated`). This is correct;
see the "Interaction with explicit-status operations" subsection in the
Discussion Summary.

**`status_change` audit event fields** for all `New → Analysis` promotions
(both `auto_assign_actor` and `assign_ticket`):

| Field | Value |
|-------|-------|
| `event_type` | `status_change` |
| `user_id` | `NULL` (system action — automatic consequence of assignment) |
| `old_value` | `"New"` |
| `new_value` | `"Analysis"` |
| `comment` | `NULL` |

`user_id = NULL` applies in both cases because the status promotion is an
automatic system consequence, not a deliberate user choice — consistent with
the project convention that "all automatic transitions create a
`TicketAuditEvent` with `user_id = NULL`" (`tickets.md` line 399).

**Two-event sequence for `assign_ticket()`** on a `New` ticket: `assign_ticket`
first sets `status = Analysis` and creates the `New → Analysis` `status_change`
event, then calls `reconcile_ticket_status`. If gates are already satisfied,
`reconcile_ticket_status` produces a second `status_change` event (`Analysis →
Analyzed` or `Analysis → Resolved`). This mirrors the two-event pattern already
documented for `auto_assign_actor` callers.

**Architectural test requirement** (guards against future duplication risk): a
parametrized integration test MUST verify that every known code path that sets
`assignee_id` on a `New` ticket also produces a `status_change` event with
`old_value = "New"` and `new_value = "Analysis"`. Paths to cover:
`auto_assign_actor()` (triggered via any mutation function on an unassigned
ticket) and `assign_ticket()`. The test requirement is added to the
`ticket-service.md` spec update alongside the existing architectural test
requirements.

### `_unassign_active_tickets` — simplification

The per-ticket call to `reconcile_ticket_status` is **removed**. Status is no
longer affected by unassignment. The function only clears `assignee_id` and
creates the audit events.

**`FOR UPDATE` must be made explicit**: the current spec implicitly relied on
the `reconcile_ticket_status` lock assumption (that function asserts it is
called under `FOR UPDATE`). With that call removed, the per-ticket
`SELECT ... FOR UPDATE` lock must be explicitly stated in the
`_unassign_active_tickets` contract. Without it, a concurrent `assign_ticket`
transaction could commit between the read of the current assignee username and
the write of `assignee_id = NULL`, producing a stale `old_value` in the
`assignment` audit event. The spec update must change the bulk fetch to a
per-ticket iteration with explicit `SELECT ... FOR UPDATE`.

### Inactive assignee sanitization — simplification

Steps 1-3 of the sanitization remain unchanged. Step 4 ("Re-evaluate the gates
— since the Analysis gate `assignee_id IS NOT NULL` is no longer satisfied, the
ticket regresses") is **removed**. The assignee is cleared, the audit event is
created, no status change occurs.

A **warning log** requirement is added to step 3: after creating the audit
event, the implementation MUST emit a warning-level log entry in the form:

```
"Inactive assignee {user_id} detected on ticket {ticket_id} during
reconciliation — this should have been handled by _unassign_active_tickets"
```

This ensures that bugs in the primary unassignment path are not silently masked
by the catch-up mechanism.

### Invariant — replacement

The **Consequence** block in `ticket-mutations.md` (lines 201-208) is removed.
A reference to the architectural invariant defined in `tickets.md` is added in
its place. The invariant text is defined in the "Architectural Invariant"
section above.

### `docs/system-map.md` — diagram update

The Mermaid state diagram must be updated:

- `DUPLICATED -->|"revert: _reenter_gate_zone"| NEW` → points to `ANALYSIS`
- `IGNORED -->|"reopen: _reenter_gate_zone"| NEW` → points to `ANALYSIS`
- `NEW -.->|"evaluate promotes"| ANALYSIS` (implicit gate edge) → replaced by
  an explicit edge representing the assignment-triggered transition:
  `NEW -->|"assign (first)"| ANALYSIS`

---

## Implementation Notes

Notes discovered during pre-implementation analysis that refine the proposed
changes above.

### `docs/system-map.md` — solid edge on line 553 already correct

The Mermaid diagram already has a solid edge
`NEW -->|"assignment or<br/>any modifying operation"| ANALYSIS` (line 553)
representing the assignment-triggered transition. The changes needed are:

- Remove the dashed edge `NEW -.->|"evaluate promotes"| ANALYSIS` (line 573) —
  this is the implicit gate edge being eliminated
- Change the targets of the two `_reenter_gate_zone` edges (lines 570-571)
  from `NEW` to `ANALYSIS`

Do **not** add a second solid edge — line 553 already covers the explicit
transition.

### `docs/features/packages/package-model.md` — imprecise gate terminology

The three references to "Analysis gate" in the soft-deletion sections
(lines 1218-1219, 1297-1298, 1399-1400) were already terminologically
incorrect before this change — they describe the set of active records
considered by ticket gates, but the Analysis gate (`assignee_id IS NOT NULL`)
was never about record counts; that behavior belongs to the Analyzed gate.
Correct all three to "Resolved gate and Analyzed gate" while applying this
change.

### `docs/features/tickets/cve-tracking.md` — minor clarification only

The rejection revert flow description (line 322) describes
`_reenter_gate_zone` generically as "determines the target status based on
current gate conditions." This remains accurate at a high level. Add a brief
clarifying note that the minimum re-entry status is `Analysis` (never `New`),
but no structural change is needed.

### `_unassign_active_tickets` — query scope includes `New`

The current query in `_unassign_active_tickets` (user-service.md, line 170)
selects tickets where `assignee_id = user_id AND status IN (New, Analysis,
Analyzed)`. Under the new invariant, a `New` ticket should never have an
assignee — the moment a VA is assigned, the ticket becomes `Analysis`.
Including `New` in the query is therefore a harmless no-op under correct
operation.

The query scope does not need to be changed: removing `New` would be a
premature optimisation and would silently hide any future bugs that produce
`New + assigned` tickets. Leaving it in means such tickets would be correctly
cleared by the unassignment path and surface via the warning log in the
inactive assignee sanitisation catch-up.

### Review files — RESOLVED findings with outdated resolution notes

Two already-RESOLVED findings in `docs/reviews/ticket-mutations.md` have
resolution notes that become outdated after this change is applied. The
findings remain resolved — they must not be reopened — but their resolution
notes must be updated to reflect the new mechanism:

- **TKM-DES-05** (RESOLVED): "deactivate_user now calls
  `reconcile_ticket_status()` per-ticket after bulk unassignment." After this
  change, `_unassign_active_tickets` no longer calls `reconcile_ticket_status`.
  The finding remains resolved (the decision is intentional, not a bypass), but
  the resolution note must be amended to reflect the new mechanism:
  unassignment no longer affects ticket status, so calling
  `reconcile_ticket_status` is not required.

- **TKM-GAP-13** (RESOLVED): "Analysis gate now defined inline at line 185-186
   ('the Analysis gate (`assignee_id IS NOT NULL`)')." After this change, the
   Analysis gate no longer exists. The finding remains resolved (trivially —
   nothing to define), but the resolution note must be amended to document that
   the gate has been removed entirely by this change.

### `reconcile_ticket_status` — `New + assignee_id IS NOT NULL` detection

When the `New` guard clause fires, an additional check must detect the invariant
violation `ticket.status == New AND ticket.assignee_id IS NOT NULL`. Under the
new model this combination cannot exist under correct operation — it indicates a
bug in an assignment code path that set `assignee_id` without transitioning
status to `Analysis`. The check is non-blocking: if the condition is true, emit
a warning-level log entry in the form `"Ticket {ticket_id} in New status with
assignee {assignee_id} — assignment code path bug: assignee was set without
transitioning status to Analysis"`, then return as normal. The guard clause thus
becomes: check `status == New`; if `assignee_id IS NOT NULL`, log warning; then
return. The spec update to `ticket-mutations.md` must include this warning in
the `reconcile_ticket_status` guard clause description.

### `auto_assign_actor` — responsibility note update

The current caller responsibility note in `ticket-mutations.md` (lines 732–735)
states: "This function performs assignment only. It does not call
`reconcile_ticket_status()`. Callers MUST call `reconcile_ticket_status()` after
completing all mutations."

After this change, `auto_assign_actor` also conditionally promotes the ticket
from `New` to `Analysis` and creates a `status_change` audit event. The spec
update must change this note to: "This function performs assignment and, if the
ticket is in `New` status, promotes it to `Analysis` and creates a
`status_change` audit event (`user_id = NULL`). It does not call
`reconcile_ticket_status()`. Callers MUST call `reconcile_ticket_status()` after
completing all mutations."

---

## Post-Implementation Steps

After all affected specification files have been updated:

1. **Invoke `@spec-coherence-reviewer`** — this change touches the status gate
   definitions in multiple interconnected specs (`tickets.md`,
   `ticket-mutations.md`, `ticket-service.md`, `user-service.md`). Verify no
   contradictions or incompatible flows were introduced across specs.

2. **Invoke `@docs-reviewer`** — behavioral changes are documented across
   multiple files; verify the documentation is accurate, complete, and
   internally consistent.

3. **Audit open findings in `docs/reviews/`** — scan the review files for
   findings that may auto-resolve, become invalid, or require a resolution note
   amendment as a result of this change. Prioritize:
   - `docs/reviews/ticket-mutations.md` — two RESOLVED findings have outdated
     resolution notes (TKM-DES-05 and TKM-GAP-13; see Implementation Notes
     above). The two OPEN findings (TKM-COH-04 and TKM-DES-10) are not
     affected by this change.
   - `docs/reviews/tickets.md`
   - `docs/reviews/ticket-service.md`
   - `docs/reviews/user-service.md`
   - `docs/reviews/package-model.md`
   - `docs/reviews/package-service.md`
