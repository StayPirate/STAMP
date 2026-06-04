# Ticket Mutations Service

## Purpose

Centralize ticket-centric operations that modify data relevant to ticket
status gates — CVSS assessment management, severity overrides, and
manual-zone exits — in a single service module (`ticket_mutations`).
This module also provides the shared `reconcile_ticket_status()` function
and the `auto_assign_actor()` helper, which are called by both this
module and `package_service`.

Package-centric mutations (track status, delivery status, product
eligibility, soft-deletion/restore, record creation) are handled by
`package_service` (`docs/features/packages/package-service.md`).

Without this centralization, each caller would need to independently:

- Acquire the correct row-level lock
- Apply the data mutation
- Call `reconcile_ticket_status`
- Create the correct `TicketAuditEvent`

Leading to inconsistency, missed re-evaluations, and bugs.

## Architecture

### Module location

`backend/app/services/ticket_mutations.py`

### Async pattern

The service is implemented as async functions. The API (FastAPI) is the
primary consumer and calls the service directly with `await`. Entry
points that operate in a synchronous context (Celery tasks, IBS
RabbitMQ consumer) call the service via `asyncio.run()`.

| Entry point               | Invocation pattern                                              |
|---------------------------|-----------------------------------------------------------------|
| API endpoint              | `await ticket_mutations.set_severity_override(session, ...)`    |
| Celery task (CVSS sync)   | `asyncio.run(ticket_mutations.create_cvss_assessment(session, ...))` |

### Transaction ownership

The module does NOT commit or roll back. All operations execute within
the caller's database session. Commit responsibility belongs to the
caller.

This matches the `user_service` pattern — the module applies mutations
and creates audit events, but the transaction boundary is the caller's
decision. This enables callers to compose multiple operations within a
single transaction when needed (e.g., `revert_duplicate` clears
`duplicate_of_id` then calls `_reenter_gate_zone`).

### Acting user convention

All operations accept an `acting_user_id: UUID | None` parameter:

- `UUID` — action performed by an authenticated user (enables
  auto-assignment on unassigned tickets if the user holds the
  `vulnerability_analyst` role)
- `None` — system action (release detection, CVSS sync, product
  lifecycle transitions). Auto-assignment does not apply

**API handler rule**: API endpoint handlers MUST always pass the UUID of
the authenticated user as `acting_user_id`. Passing `None` from an API
handler is a bug — it would silently bypass auto-assignment. `None` is
reserved exclusively for system entry points.

#### Authorization responsibility

The module does NOT perform capability checks — this is by design.
API-layer callers MUST apply the appropriate `require_capability()`
dependency before invoking any module function; the module trusts that
the caller has already verified the user's permissions. System callers
(fetchers, Celery tasks) use `acting_user_id=None` and operate as
trusted internal processes — capability checks do not apply to them.
Adding a new caller that passes a non-None `acting_user_id` without
having verified the corresponding capability is a security bug.

### Relationship with other modules

| Module | Relationship |
|--------|-------------|
| `services/cvss.py` | `ticket_mutations` delegates CVSS resolution and severity calculation to pure functions in `cvss.py`. The resolution cascade logic is never reimplemented inside `ticket_mutations` |
| `services/package_service.py` | Handles all package-centric mutations (track status, delivery status, product eligibility, soft-delete/restore, record creation) and package queries. `package_service` imports `reconcile_ticket_status()`, `auto_assign_actor()`, and `ensure_ticket_operable()` from `ticket_mutations`. The dependency is unidirectional: `package_service` -> `ticket_mutations` |
| `services/ticket_service.py` | Handles non-gate operations (assignment, CVE association, mark-as-duplicate, set-confidentiality, access grants). See [ticket-service.md](ticket-service.md) for the full contract. These operations use the same FOR UPDATE pattern and import `ensure_ticket_operable()` from `ticket_mutations` |

## State Machine Zones

The ticket state machine has two zones that determine which operations
are valid:

### Gate zone (Analysis, Analyzed, Resolved)

Status is determined automatically by `reconcile_ticket_status` based on
gate conditions. The `ticket_mutations` module operates exclusively on
tickets in this zone (with the exception of manual-zone exit functions).

`New` is a pre-state, not part of the gate zone. A ticket in `New` status
has never been claimed by a VA. The `New → Analysis` transition is an
explicit one-way event triggered by assignment, not a gate evaluation.
`reconcile_ticket_status` skips tickets in `New` status entirely — the
floor of the gate zone is `Analysis`.

### Manual zone (Ignored, Duplicated)

Status is set by explicit user actions or specific system events.
`reconcile_ticket_status` never operates on tickets in the manual zone.
Gate-relevant mutations are blocked at the service layer by
`ensure_ticket_operable()` (raises `TicketNotMutableError` → 409
`TICKET_NOT_MUTABLE`).

### `_reenter_gate_zone()` (private helper)

To exit the manual zone, an explicit operation must call the private
helper `_reenter_gate_zone()`:

1. Saves the ticket's current status (Ignored or Duplicated) as
   `original_status`
2. Sets `status = Analysis` (floor of the gate zone)
3. Calls `reconcile_ticket_status(previous_status=original_status)`

This produces a single `TicketAuditEvent` with the real transition
(e.g., `old_value = Ignored, new_value = Analysis`). If the Analyzed
or Resolved gates are already satisfied, `reconcile_ticket_status`
promotes the ticket further in the same call and the audit event
reflects the final target (e.g., `old_value = Ignored,
new_value = Analyzed`).

Only the two manual-zone exit functions (`reopen_from_ignored`,
`revert_duplicate`) call this helper. It is never called directly by
external code.

## `reconcile_ticket_status()`

The sole authority for reconciling a ticket's status and assignment
state with current reality. This function is internal to the module —
external code interacts with it indirectly through the public mutation
functions.

**Purpose**: Reconciles the ticket's status and assignment state with
current reality (gate conditions + data freshness).

**Side effects** (documented, intentional):

- May transition ticket status (forward or backward) based on gate
  evaluation
- May null `assignee_id` and create an `assignment` audit event if the
  current assignee is inactive (inactive assignee sanitization)
- May add ticket to revisit queue after sanitization

Callers must be aware that invoking this function may produce mutations
beyond status changes.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ticket` | `Ticket` | Yes | The ticket instance (already loaded with FOR UPDATE by the caller) |
| `db` | `AsyncSession` | Yes | Database session |
| `previous_status` | `TicketStatus \| None` | No | If provided, used as `old_value` in the audit event instead of the ticket's current status field. Enables recording the real semantic transition (e.g., `Ignored → Analysis`) when the status has been set to an intermediate value |

### Behavior (top-down evaluation)

1. Guard clause: if `ticket.status == New`, return immediately. `New` is
   a pre-state outside the gate zone — the `New → Analysis` transition is
   handled explicitly by assignment code paths (`auto_assign_actor` and
   `assign_ticket`), never by this function. Before returning, if
   `ticket.assignee_id IS NOT NULL`, emit a warning-level log:
   `"Ticket {ticket_id} in New status with assignee {assignee_id} —
   assignment code path bug: assignee was set without transitioning
   status to Analysis"`.
2. Evaluate gate conditions from highest to lowest (two active tiers;
   `Analysis` is the unconditional floor, not a gate-evaluated tier):
   - If all "Resolved" gates are met (every non-excluded active track is
     resolution-complete — see `tickets.md`, "Gate: Analyzed → Resolved")
     AND all "Analyzed" gates are met → status is Resolved
   - If all "Analyzed" gates are met (but "Resolved" gates are not) →
     status is Analyzed
   - Otherwise → status is Analysis (unconditional floor; this function
     never produces `New`)
3. If the determined status differs from the current status:
   - Update `ticket.status`
   - Create `TicketAuditEvent` with `event_type = status_change`
   - `old_value` is taken from `previous_status` if provided; otherwise
     from the ticket's current status field
4. The function operates within the same database transaction as the
   triggering operation (atomicity guarantee)

### Inactive Assignee Sanitization

After determining the ticket's "natural" status via gate evaluation, if
the resulting status is non-final (Analysis or Analyzed) and
`assignee_id` points to an inactive user:

1. Set `assignee_id = NULL`
2. Create `TicketAuditEvent` with `event_type = assignment`
   (system-initiated, `user_id = NULL`,
   `comment = "Unassigned from {username}: employee deactivated"`)
3. Add the ticket to the revisit queue (to be defined in a future
   specification)
4. Emit a warning-level log: `"Inactive assignee {user_id} detected on
   ticket {ticket_id} during reconciliation — this should have been
   handled by _unassign_active_tickets"`

If the resulting status is final (Resolved, Ignored, Duplicated): no
assignee check is performed — the ticket is closed and does not need an
active assignee.

This mechanism complements the bulk unassignment performed by
`deactivate_user` (see
[user-service.md](../identity/user-service.md#deactivate_user)) by
catching any tickets that were missed or that entered the gate zone
after the deactivation event. Unassignment does not change the ticket's
status — the ticket remains in its current gate-zone status.

> **Invariant**: ticket status reflects work state, not staffing state.
> A ticket in `Analysis`, `Analyzed`, or `Resolved` status may have
> `assignee_id = NULL` (an orphaned ticket awaiting reassignment).
> See the Architectural Invariant in
> `docs/features/tickets/tickets.md`.

### `previous_status` parameter

The `previous_status` parameter exists to handle manual-zone exit
operations correctly. When `_reenter_gate_zone()` sets `status = Analysis`
before calling `reconcile_ticket_status`, if the function then promotes
the ticket further (to `Analyzed` or `Resolved`), the audit event must
record the real transition origin (e.g., `old_value = Ignored`) rather
than the intermediate `Analysis` value. Passing
`previous_status = Ignored` records the correct semantic transition
(e.g., `Ignored → Analyzed` rather than `Analysis → Analyzed`).

### Multiple invocations within a transaction

`reconcile_ticket_status` may be called multiple times in a single
transaction during orphan cascades. However, `package_service` calls
reconcile once after the entire orphan cascade completes (not at each
level). The function is idempotent — each call ensures consistent
state based on the ticket's current data at that point in the
transaction.

## Concurrency Control

The generic pessimistic locking pattern and transaction hygiene rules
are defined in `docs/conventions.md` (Transaction and Locking). This
section documents ticket-specific refinements only.

### Extension to non-module operations

Every operation that modifies the `Ticket` row (any column: `status`,
`assignee_id`, `cve_id`, `duplicate_of_id`, `is_confidential`)
or that calls `reconcile_ticket_status` MUST acquire
`FOR UPDATE` on the Ticket row before any modification — not just
module functions. This prevents non-gate operations (assignment,
duplicate set/revert, ignore)
from racing with gate operations on the same ticket.

### Single-ticket scope

`ticket_mutations` functions operate on a single ticket per
transaction. Code that must modify multiple tickets (e.g., the cascade
update of `duplicate_of_id` when marking a ticket as duplicate) MUST
NOT acquire `FOR UPDATE` on multiple ticket rows in the same
transaction — process each ticket in an independent transaction to
avoid deadlocks.

### Blocking wait

The default PostgreSQL behavior (blocking wait) is used. `NOWAIT` is
intentionally not specified — the transaction hygiene rules ensure
locks are held for milliseconds, making spurious failures from `NOWAIT`
more harmful than brief waits.

### Ticket-not-found handling

If the `SELECT FOR UPDATE` returns no row (ticket does not exist,
invalid ID, or stale reference from a queue message), the function MUST
raise a domain-specific exception (`TicketNotFoundError`). It MUST NOT
proceed silently or operate on `None`. Callers handle the exception as
appropriate: background tasks log and skip; API endpoints return 404.

### `reconcile_ticket_status` does not acquire the lock

The function assumes the caller has already acquired `FOR UPDATE` on the
ticket. This is always the case because every public function in the
module acquires the lock as its first operation, and
`reconcile_ticket_status` is only called from within those functions.

## `ensure_ticket_operable()`

A shared guard function that rejects mutations on non-operable tickets.
Called after acquiring `FOR UPDATE` on the ticket row by all mutation
functions in `ticket_mutations`, `ticket_service`, and `package_service`
— except for explicit opt-outs documented per function.

**Signature**:

```python
def ensure_ticket_operable(ticket: Ticket) -> None:
    """Reject mutations on manually-closed tickets.

    Call after acquiring FOR UPDATE on the ticket row.
    Raises TicketNotMutableError if status is Ignored or Duplicated.
    """
```

**Behavior**:

1. If `ticket.status ∈ {Ignored, Duplicated}` → raise
   `TicketNotMutableError`

This function performs no database operations. It validates invariants
on an already-loaded `Ticket` object. The caller is responsible for
loading the ticket with `SELECT ... FOR UPDATE` before invoking this
function.

**Opt-out cases**:

- `reopen_from_ignored` — must operate on Ignored tickets; skips
  mutability guard
- `revert_duplicate` — must operate on Duplicated tickets; skips
  mutability guard

**Consumers**:

| Module | Functions that call `ensure_ticket_operable` |
|--------|----------------------------------------------|
| `ticket_mutations` | `create_cvss_assessment`\*, `update_cvss_assessment`\*, `delete_cvss_assessment`\*, `set_severity_override` |
| `ticket_service` | `associate_cve`, `assign_ticket`, `ignore_ticket`, `mark_as_duplicate`, `set_confidentiality`, `grant_access`, `revoke_access` |
| `package_service` | `set_track_status`, `set_track_delivery_status`, `set_product_eligibility`, `set_product_released_at`, and other mutation functions |

\* CVSS functions call `ensure_ticket_operable` **conditionally** — only
when the CVE has an associated ticket. Ticketless CVEs skip this check
(see `create_cvss_assessment()` below).

## Gate-Relevant Mutation Operations

Each function below follows the same pattern:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate additional preconditions
5. Apply the mutation
6. Create `TicketAuditEvent`
7. Call `reconcile_ticket_status()`
8. Return the updated record

Package-centric mutations (`set_track_status`, `set_track_delivery_status`,
`set_product_eligibility`, `set_product_released_at`,
`add_package_records`, soft-delete/restore for packages, tracks, and
products) have been moved to `package_service` — see
`docs/features/packages/package-service.md`.

### CVSS Vector Parsing

The `cvss` Python library (PyPI: `cvss`, maintained by Red Hat Product
Security) is used for vector parsing, version detection, and score
computation. Score and version are never accepted as external inputs —
they are always derived from the vector string. Providers that supply
only a numeric score without a vector string are not imported.

### `create_cvss_assessment()`

Creates a new `CVECVSSAssessment` record for a CVE. The function accepts
a `cve_id` (not a `ticket_id`) and handles both CVEs with and without
an associated ticket.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `cve_id` | `UUID` | Yes | CVE that receives the assessment |
| `provider` | `str` | Yes | Assessment provider (e.g., `"suse"`, `"nvd"`) |
| `vector` | `str` | Yes | CVSS vector string (version and score are derived from the vector) |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Vector must be parseable — raises `InvalidCVSSVectorError` (HTTP 422
  Unprocessable Entity, `error_code: "CVSS_INVALID_VECTOR"`)
- No existing assessment for the same (CVE, provider, version) combination
  — raises `DuplicateCVSSAssessmentError` (HTTP 409 Conflict,
  `error_code: "CVSS_DUPLICATE_ASSESSMENT"`)

**Behavior**:

1. Look up the ticket associated with the CVE (if any)
2. If a ticket exists:
   a. Acquire `FOR UPDATE` on the Ticket row
   b. Call `ensure_ticket_operable(ticket)` — rejects with
       `TicketNotMutableError` if in Ignored or Duplicated status
3. Parse the vector string using the `cvss` library. Determine version from
   prefix (`CVSS:4.0/` → 4.0, `CVSS:3.1/` → 3.1, `CVSS:3.0/` → 3.0,
   no prefix → 2.0). Compute base score. If parsing fails, raise
   `InvalidCVSSVectorError`
4. Create `CVECVSSAssessment` record (with derived version and score)
5. If a ticket exists:
    a. Recalculate ticket severity via `cvss.resolve_severity_score()`
       (5-step severity cascade); re-evaluate product eligibility via
       `cvss.resolve_eligibility_score()` (2-step SUSE-only cascade,
       separate call — the eligibility score may differ from the severity
       score when SUSE has not assessed the default version)

   b. Create `TicketAuditEvent` (`cvss_assessment_changed`,
      `old_value = NULL`, `new_value = "provider vX.Y score"`)
   c. Call `reconcile_ticket_status()`
6. If no ticket exists (ticketless CVE): skip audit event and cascade —
   the CVSS assessment is stored but no side effects are triggered
7. Return created assessment

**TicketAuditEvent**: `cvss_assessment_changed` (only when the CVE has
an associated ticket)

---

### `update_cvss_assessment()`

Updates an existing `CVECVSSAssessment` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `assessment_id` | `UUID` | Yes | CVECVSSAssessment to modify |
| `vector` | `str` | Yes | New CVSS vector string (version and score are re-derived) |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Assessment must exist — raises `CVSSAssessmentNotFoundError` (HTTP 404,
  `error_code: "CVSS_ASSESSMENT_NOT_FOUND"`)
- New vector must be parseable — raises `InvalidCVSSVectorError` (HTTP 422
  Unprocessable Entity, `error_code: "CVSS_INVALID_VECTOR"`)
- Derived version must match the existing assessment's version — raises
  `CVSSVersionMismatchError` (HTTP 409 Conflict,
  `error_code: "CVSS_VERSION_MISMATCH"`)

**Behavior**:

1. Look up the ticket associated with the assessment's CVE (if any)
2. If a ticket exists:
   a. Acquire `FOR UPDATE` on the Ticket row
   b. Call `ensure_ticket_operable(ticket)`
3. Parse the new vector string using the `cvss` library. Determine version
   and compute base score. If parsing fails, raise `InvalidCVSSVectorError`.
   If the derived version differs from the existing assessment's version,
   raise `CVSSVersionMismatchError` (message suggests creating a new
   assessment for the target version instead)
4. Update assessment fields (vector and recomputed score)
5. If a ticket exists:
    a. Recalculate ticket severity via `cvss.resolve_severity_score()`
       (5-step severity cascade); re-evaluate product eligibility via
       `cvss.resolve_eligibility_score()` (2-step SUSE-only cascade,
       separate call — the eligibility score may differ from the severity
       score when SUSE has not assessed the default version)

   b. Create `TicketAuditEvent` (`cvss_assessment_changed`,
      `old_value = "provider vX.Y old_score"`,
      `new_value = "provider vX.Y new_score"`)
   c. Call `reconcile_ticket_status()`
6. If no ticket exists: skip audit event and cascade
7. Return updated assessment

**TicketAuditEvent**: `cvss_assessment_changed` (only when the CVE has
an associated ticket)

---

### `delete_cvss_assessment()`

Deletes a `CVECVSSAssessment` record (hard delete).

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `assessment_id` | `UUID` | Yes | CVECVSSAssessment to delete |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Assessment must exist — raises `CVSSAssessmentNotFoundError` (HTTP 404,
  `error_code: "CVSS_ASSESSMENT_NOT_FOUND"`)

**Behavior**:

1. Look up the ticket associated with the assessment's CVE (if any)
2. If a ticket exists:
   a. Acquire `FOR UPDATE` on the Ticket row
   b. Call `ensure_ticket_operable(ticket)`
3. Delete the assessment record
4. If a ticket exists:
    a. Recalculate ticket severity via `cvss.resolve_severity_score()`
       (5-step severity cascade); re-evaluate product eligibility via
       `cvss.resolve_eligibility_score()` (2-step SUSE-only cascade,
       separate call — the eligibility score may differ from the severity
       score when SUSE has not assessed the default version)

   b. Create `TicketAuditEvent` (`cvss_assessment_changed`,
      `old_value = "provider vX.Y score"`, `new_value = NULL`)
   c. Call `reconcile_ticket_status()`
5. If no ticket exists: skip audit event and cascade

**TicketAuditEvent**: `cvss_assessment_changed` (only when the CVE has
an associated ticket)

---

### `set_severity_override()`

Sets or clears the `severity_override` field on a ticket.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `ticket_id` | `UUID` | Yes | Ticket to modify |
| `severity` | `Severity \| None` | Yes | New severity value, or `None` to clear |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Ticket must be operable (`ensure_ticket_operable`)
- Ticket must have `cve_id IS NULL` — raises `SeverityDerivedError`
  if the ticket has an associated CVE (severity is derived from CVSS
  scores and cannot be manually overridden)

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Validate preconditions
3. If severity unchanged, return (no-op)
4. Update `ticket.severity_override`
5. Create `TicketAuditEvent` (`severity_changed`, `user_id = acting_user_id`)
6. Call `reconcile_ticket_status()`
7. Return updated ticket

**Gate relevance**: setting `severity_override` affects the ticket's
resolved severity, which is gate-relevant (Analyzed gate #3 requires
severity). This operation is only valid when `cve_id IS NULL` — when a
CVE is associated, severity is derived from CVSS scores via the
resolution cascade and `severity_override` is not applicable.

**TicketAuditEvent**: `severity_changed`

**Idempotency**: no-op if severity is unchanged.

---

## Manual-Zone Exit Operations

These operations transition tickets out of the manual zone (Ignored or
Duplicated) back into the gate zone.

### `reopen_from_ignored()`

Reopens a ticket from Ignored status.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `ticket_id` | `UUID` | Yes | Ticket to reopen |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Ticket must exist
- Ticket must be in `Ignored` status

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Verify current status is Ignored
3. Call `auto_assign_actor(ticket, acting_user_id, db, force=True)`:
   - `acting_user_id` is `None` (system): ticket retains current
     assignee; `reconcile_ticket_status` handles inactive assignees in
     the final step
   - `acting_user_id` is VA: becomes new assignee
   - `acting_user_id` is non-VA: ticket retains current assignee;
     `reconcile_ticket_status` handles inactive assignees in the final
     step
4. Call `_reenter_gate_zone()`:
   - Saves `original_status = Ignored`
   - Sets `status = Analysis` (floor of the gate zone)
   - Calls `reconcile_ticket_status(previous_status=Ignored)`
   - Produces `status_change` event with
     `old_value = Ignored, new_value = Analysis` (or `Analyzed`/`Resolved`
     if gate conditions are already satisfied)

**TicketAuditEvent**: `status_change` (via `reconcile_ticket_status`) +
optionally `assignment` (via `auto_assign_actor`)

---

### `revert_duplicate()`

Reverts a ticket from Duplicated status.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `ticket_id` | `UUID` | Yes | Ticket to revert |
| `acting_user_id` | `UUID \| None` | No | User performing the revert. Currently no system caller exists; this signature enables future system-initiated revert scenarios |

**Preconditions**:

- Ticket must exist
- Ticket must be in `Duplicated` status

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Verify current status is Duplicated
3. Clear `duplicate_of_id` (set to NULL)
4. Call `auto_assign_actor(ticket, acting_user_id, db, force=True)`:
   assigns the acting user if they hold the `vulnerability_analyst`
   role; otherwise the ticket retains its current assignee
5. Create `TicketAuditEvent` (`duplicate_removed`)
6. Call `_reenter_gate_zone()`:
   - Saves `original_status = Duplicated`
   - Sets `status = Analysis` (floor of the gate zone)
   - Calls `reconcile_ticket_status(previous_status=Duplicated)`
   - Outcome: Analysis, Analyzed, or Resolved based on current gate
     conditions (independent of assignee presence)

Produces two `TicketAuditEvent` records in the same transaction:
`duplicate_removed` (user action) followed by `status_change` with
`old_value = Duplicated, new_value = (evaluated target)`.

**TicketAuditEvent**: `duplicate_removed` + `status_change`

The revert operation does NOT need to know or care about the canonical
target — it simply removes the ticket from the duplicate chain.

## Utility Functions

### `resolve_canonical_target()`

A centralized public function that follows the `duplicate_of_id` chain
to find the non-Duplicated canonical target.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `ticket_id` | `UUID` | Yes | Starting ticket ID |

**Behavior**:

- Follows the `duplicate_of_id` chain until a non-Duplicated ticket is
  found
- Maintains a set of visited ticket IDs to detect cycles
- Enforces a maximum hop limit of 50 (corruption guard — under normal
  operation chains are 1-2 hops)
- If a cycle is detected, raises `DuplicateCycleDetectedError`
  (maps to 409 `TICKET_DUPLICATE_CYCLE_DETECTED`)
- If the hop limit is exceeded, raises `DuplicateChainDepthError`
  (maps to 409 `TICKET_DUPLICATE_CHAIN_DEPTH`)
- Returns the canonical (non-Duplicated) target ticket

The resolver does not apply confidentiality checks — it is a
service-layer utility used by both API serialization and background
tasks.

## Auto-Assignment Rule

When a user with the `vulnerability_analyst` role performs any modifying
operation on a ticket with `assignee_id = NULL`, the ticket is
automatically assigned to the acting user. A `TicketAuditEvent` with
`event_type = assignment` is created atomically in the same transaction
as the modifying operation. If the acting user does not hold the
`vulnerability_analyst` role (e.g., a `restricted_analyst`),
auto-assignment is skipped — the ticket remains unassigned.

After the assignment, if the ticket was in `New` status,
`auto_assign_actor()` explicitly sets `status = Analysis` and creates a
`status_change` audit event (`New → Analysis`, `user_id = NULL`) before
returning to the caller. The caller then calls `reconcile_ticket_status`,
which evaluates from `Analysis` upward and may promote to `Analyzed` or
`Resolved` if gate conditions are already satisfied.

For operations that call `auto_assign_actor` and then immediately set
an explicit status (e.g., `ignore_ticket` → `Ignored`,
`mark_as_duplicate` → `Duplicated`): `auto_assign_actor` sets `Analysis`,
the caller then sets the explicit status. The audit trail records two
`status_change` events — `New → Analysis` and `Analysis → Ignored` (or
`Duplicated`). This is correct and intentional: the VA claimed the ticket
before choosing to act on it explicitly.

This rule is enforced via the shared helper `auto_assign_actor()`
(see below), which is called by all modules that modify tickets under
a `FOR UPDATE` lock (`ticket_mutations`, `package_service`,
`ticket_service`).

This rule does not apply to system operations (`acting_user_id = None`)
or to users without the `vulnerability_analyst` role.

### `auto_assign_actor()`

A public helper function that implements the auto-assignment check. All
modules that modify tickets under a `FOR UPDATE` lock call this helper
as the first operation after acquiring the lock.

**Signature**:

```python
async def auto_assign_actor(
    ticket: Ticket,
    acting_user_id: UUID | None,
    db: AsyncSession,
    force: bool = False,
) -> bool:
    """Assign ticket to acting user if user holds VA role.

    When force=False (default): assigns only if ticket is currently
    unassigned. Used by all gate-relevant mutations as step 2.

    When force=True: assigns regardless of current assignee. Used by
    manual-zone exit functions (reopen_from_ignored, revert_duplicate)
    to take ownership. External callers (`package_service`,
    `ticket_service`, API handlers, background tasks) MUST NOT pass
    `force=True` — doing so is a bug.

    Returns True if assignment was applied (audit event created),
    False otherwise.

    Precondition: caller MUST hold FOR UPDATE on the ticket row.
    """
```

**Behavior**:

1. If `acting_user_id is None` → return False (system action)
2. If not `force` and `ticket.assignee_id is not None` → return False
   (already assigned)
3. Load the acting user's roles. If not VA → return False
4. If `ticket.assignee_id == acting_user_id` → return False (assignment
   unchanged, no audit event)
5. Set `ticket.assignee_id = acting_user_id`
6. Create `TicketAuditEvent` with `event_type = assignment`
7. If `ticket.status == New`: set `ticket.status = Analysis`, create
   `TicketAuditEvent` with `event_type = status_change`,
   `user_id = NULL`, `old_value = "New"`, `new_value = "Analysis"`
8. Return True

> **Caller responsibility**: this function performs assignment and, if
> the ticket is in `New` status, promotes it to `Analysis` and creates a
> `status_change` audit event (`user_id = NULL`). It does not call
> `reconcile_ticket_status()`. Callers MUST call
> `reconcile_ticket_status()` after completing all mutations to ensure
> inactive assignee sanitization and correct gate evaluation.

## Related Operations

Non-gate ticket lifecycle operations (assignment, CVE association,
mark-as-duplicate, set-confidentiality, access grant
management) live in `ticket_service` —
see [ticket-service.md](ticket-service.md) for the full service contract.

These operations use the same `FOR UPDATE` pattern documented in
[Concurrency Control](#concurrency-control) and create their own
`TicketAuditEvent` records. Some call `reconcile_ticket_status()` due
to indirect gate effects (severity source change, promotion evaluation
after assignment, status reconciliation after restore).

## Contract

Every service-layer operation that modifies data relevant to ticket
status gates MUST go through the appropriate centralized module:

- **Package/track/product mutations**: `package_service`
  (`TicketPackageTrack` status, delivery status,
  `TicketPackageProduct` eligibility, soft-delete/restore, record
  creation)
- **CVSS and severity mutations**: `ticket_mutations`
  (`CVECVSSAssessment` records, severity override)
- **Ticket status evaluation**: `ticket_mutations` (called by both
  modules after any gate-relevant mutation)

Direct modification of gate-relevant records outside the owning
module is a bug.

Non-gate ticket lifecycle operations live in `ticket_service` — see
`docs/features/tickets/ticket-service.md`. Some of these operations
(CVE association, assignment, restore) call
`reconcile_ticket_status` due to indirect gate effects: severity
source changes, promotion evaluation after assignment (evaluating
whether existing data satisfies gates above `Analysis`), or status
reconciliation after restore. The per-function documentation in `ticket-service.md`
specifies exactly which operations call `reconcile_ticket_status` and
why.

## Architectural Test Requirement

A parametrized integration test MUST be implemented to verify that the
`ticket_mutations` module produces the correct ticket status after every
type of ticket-centric mutation (CVSS assessment operations, severity
override, manual-zone exits). The test must cover:

- **Forward transitions**: CVSS and severity changes causing ticket
  advancement
- **Backward transitions**: CVSS deletion breaking gate conditions
- **No-op cases**: mutations that do not affect gate conditions
- **Edge cases**: ticket without CVE (no SUSE CVSS gate), severity
  override on CVE-less ticket
- **Manual-zone exits**: `reopen_from_ignored` and `revert_duplicate`
  producing correct status transitions

Package-centric mutation tests are specified in
`docs/features/packages/package-service.md` (Architectural Test
Requirement).

## Service Exceptions

All exceptions in this module inherit from `TicketMutationsError`.
API endpoint handlers catch `TicketMutationsError` subclasses and map
them to the corresponding HTTP status code and error code per
`api-spec.md`.

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `TicketNotFoundError` † | 404 | `TICKET_NOT_FOUND` | Ticket ID does not exist |
| `TicketNotMutableError` † | 409 | `TICKET_NOT_MUTABLE` | Ticket is in manual zone (Ignored or Duplicated) |
| `DuplicateCycleDetectedError` † | 409 | `TICKET_DUPLICATE_CYCLE_DETECTED` | Duplicate resolution would create a cycle |
| `DuplicateChainDepthError` † | 409 | `TICKET_DUPLICATE_CHAIN_DEPTH` | Duplicate chain exceeds maximum allowed depth |
| `DuplicateCVSSAssessmentError` | 409 | `CVSS_DUPLICATE_ASSESSMENT` | Assessment for this provider+version already exists |
| `CVSSAssessmentNotFoundError` | 404 | `CVSS_ASSESSMENT_NOT_FOUND` | CVSS assessment ID does not exist |
| `InvalidCVSSVectorError` | 422 | `CVSS_INVALID_VECTOR` | CVSS vector string is malformed or invalid |
| `CVSSVersionMismatchError` | 409 | `CVSS_VERSION_MISMATCH` | Vector version does not match the declared version |
| `InvalidTransitionError` † | 409 | `TICKET_INVALID_TRANSITION` | Requested status transition is not allowed |
| `SeverityDerivedError` † | 409 | `TICKET_SEVERITY_DERIVED` | Cannot manually set severity when it is auto-derived |

† Shared exception — inherits from `ServiceError`, not from
`TicketMutationsError`. Handlers must catch it explicitly.

Package-specific exceptions (`TrackNotFoundError`, `ProductNotFoundError`,
`PackageNotFoundError`) are defined in `package_service` — see
`docs/features/packages/package-service.md`.

## Callers

The callers table is scoped to operation categories rather than
individual endpoints.

| Caller Category | Operations Used | Context |
|-----------------|-----------------|---------|
| Ticket API mutation endpoints | `set_severity_override()`, manual-zone exits | VA-initiated ticket operations |
| CVE API mutation endpoints | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | VA-initiated CVSS operations via `/api/v1/cves/{cve_id}/cvss/...` |
| CVSS sync fetcher | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | Background CVSS ingestion |
| NVD rejection handling | `reopen_from_ignored()` | CVE rejection revert |
| Admin: default CVSS version change | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | Re-evaluation triggered by config change |
| `package_service` | `reconcile_ticket_status()`, `auto_assign_actor()` | Called after every package mutation |

Package-centric callers (IBS release detection, product lifecycle
transitions, `add_package_to_ticket`) now call `package_service`
directly — see `docs/features/packages/package-service.md`.

## Cross-references

- `docs/features/packages/package-service.md` — package-centric
  mutations, orchestration, and query operations (imports
  `reconcile_ticket_status()`, `auto_assign_actor()`, and
  `ensure_ticket_operable()`)
- `docs/features/tickets/tickets.md` — ticket lifecycle, gate
  conditions, API endpoints
- `docs/features/tickets/ticket-audit-log.md` — event type contract
- `docs/features/tickets/cvss-scoring.md` — CVSS resolution cascade,
  severity calculation
- `docs/features/packages/package-model.md` — track/product concepts,
  status propagation, hierarchical exclusion model
- `docs/features/packages/product-lifecycle-transitions.md` — AIMAAS
  threshold changes triggering eligibility mutations
- `docs/features/identity/user-service.md` — `deactivate_user` bulk
  unassignment (complementary to inactive assignee sanitization)
- `docs/conventions.md` — Transaction and Locking (generic pessimistic
  locking pattern)
- `docs/features/tickets/ticket-service.md` — non-gate ticket lifecycle
  operations (imports `reconcile_ticket_status()`,
  `auto_assign_actor()`, `ensure_ticket_operable()`,
  `resolve_canonical_target()`)
- `docs/api-spec.md` — general API conventions
