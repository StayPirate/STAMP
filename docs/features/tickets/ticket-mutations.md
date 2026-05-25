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
| `services/package_service.py` | Handles all package-centric mutations (track status, delivery status, product eligibility, soft-delete/restore, record creation) and package queries. `package_service` imports `reconcile_ticket_status()` and `auto_assign_actor()` from `ticket_mutations`. The dependency is unidirectional: `package_service` -> `ticket_mutations` |
| `services/ticket_service.py` | Handles non-gate operations (assignment, CVE association/dissociation, soft-delete/restore, mark-as-duplicate, set-confidentiality, access grants). See [ticket-service.md](ticket-service.md) for the full contract. These operations use the same FOR UPDATE pattern but are NOT routed through `ticket_mutations` |

## State Machine Zones

The ticket state machine has two zones that determine which operations
are valid:

### Gate zone (New, Analysis, Analyzed, Resolved)

Status is determined automatically by `reconcile_ticket_status` based on
gate conditions. The `ticket_mutations` module operates exclusively on
tickets in this zone (with the exception of manual-zone exit functions).

### Manual zone (Ignored, Duplicated)

Status is set by explicit user actions or specific system events.
`reconcile_ticket_status` never operates on tickets in the manual zone.
Gate-relevant mutations are blocked at the API level
(`require_ticket_mutable` returns 409 `TICKET_NOT_MUTABLE`).

### `_reenter_gate_zone()` (private helper)

To exit the manual zone, an explicit operation must call the private
helper `_reenter_gate_zone()`:

1. Saves the ticket's current status (Ignored or Duplicated) as
   `original_status`
2. Sets `status = New` (entering the gate zone at the lowest rung)
3. Calls `reconcile_ticket_status(previous_status=original_status)`

This produces a single `TicketAuditEvent` with the real transition
(e.g., `old_value = Ignored, new_value = Analysis`). The intermediate
`New` state is an internal implementation detail — never visible in the
audit trail.

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

1. Evaluate gate conditions from highest to lowest:
   - If all "Resolved" gates AND all "Analyzed" gates are met → status
     is Resolved
   - If all "Analyzed" gates are met (but "Resolved" gates are not) →
     status is Analyzed
   - If the "Analysis" gate is met (but "Analyzed" gates are not) →
     status is Analysis
   - Otherwise → status is New
2. If the determined status differs from the current status:
   - Update `ticket.status`
   - Create `TicketAuditEvent` with `event_type = status_change`
   - `old_value` is taken from `previous_status` if provided; otherwise
     from the ticket's current status field
3. The function operates within the same database transaction as the
   triggering operation (atomicity guarantee)

### Inactive Assignee Sanitization

After determining the ticket's "natural" status via gate evaluation, if
the resulting status is non-final (New, Analysis, or Analyzed) and
`assignee_id` points to an inactive user:

1. Set `assignee_id = NULL`
2. Create `TicketAuditEvent` with `event_type = assignment`
   (system-initiated, `user_id = NULL`,
   `comment = "Unassigned from {username}: employee deactivated"`)
3. Add the ticket to the revisit queue (to be defined in a future
   specification)
4. Re-evaluate the gates — since the Analysis gate
   (`assignee_id IS NOT NULL`) is no longer satisfied, the ticket
   regresses accordingly (e.g., Analysis → New, Analyzed → New)

If the resulting status is final (Resolved, Ignored, Duplicated): no
assignee check is performed — the ticket is closed and does not need an
active assignee.

This mechanism complements the bulk unassignment performed by
`deactivate_user` (see
[user-service.md](../identity/user-service.md#deactivate_user)) by
catching any tickets that were missed or that entered the gate zone
after the deactivation event.

### `previous_status` parameter

The `previous_status` parameter exists to handle manual-zone exit
operations correctly. When `_reenter_gate_zone()` sets `status = New`
before calling `reconcile_ticket_status`, the ticket's current status
field is `New`. But the real transition for the audit trail is
`Ignored → Analysis` (not `New → Analysis`). Passing
`previous_status = Ignored` records the correct semantic transition.

### Multiple invocations within a transaction

`reconcile_ticket_status` may be called multiple times in a single
transaction during orphan cascades (up to 3 times: product → track →
package). The function is idempotent — each call ensures consistent
state based on the ticket's current data at that point in the
transaction. Implementations MUST NOT defer or skip intermediate calls
for optimization.

## Concurrency Control

The generic pessimistic locking pattern and transaction hygiene rules
are defined in `docs/conventions.md` (Transaction and Locking). This
section documents ticket-specific refinements only.

### Extension to non-module operations

Every operation that modifies the `Ticket` row (any column: `status`,
`assignee_id`, `cve_id`, `duplicate_of_id`, `is_confidential`,
`deleted_at`) or that calls `reconcile_ticket_status` MUST acquire
`FOR UPDATE` on the Ticket row before any modification — not just
module functions. This prevents non-gate operations (assignment,
duplicate set/revert, CVE dissociation, soft-delete, restore, ignore)
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

## Gate-Relevant Mutation Operations

Each function below follows the same pattern:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `auto_assign_actor()`
3. Validate preconditions
4. Apply the mutation
5. Create `TicketAuditEvent`
6. Call `reconcile_ticket_status()`
7. Return the updated record

Package-centric mutations (`set_track_status`, `set_track_delivery_status`,
`set_product_eligibility`, `set_product_released_at`,
`add_package_records`, soft-delete/restore for packages, tracks, and
products) have been moved to `package_service` — see
`docs/features/packages/package-service.md`.

### `create_cvss_assessment()`

Creates a new `CVECVSSAssessment` record for a ticket's CVE.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `ticket_id` | `UUID` | Yes | Ticket whose CVE receives the assessment |
| `provider` | `str` | Yes | Assessment provider (e.g., `"suse"`, `"nvd"`) |
| `cvss_version` | `str` | Yes | CVSS version (e.g., `"3.1"`, `"4.0"`) |
| `score` | `Decimal` | Yes | CVSS score |
| `vector` | `str` | Yes | CVSS vector string |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Ticket must exist and have `deleted_at IS NULL`
- Ticket must be in the gate zone
- Ticket must have an associated CVE (`cve_id IS NOT NULL`)
- No existing assessment for the same (CVE, provider, version) combination
  — raises `DuplicateCVSSAssessmentError` (HTTP 409 Conflict,
  `error_code: "DUPLICATE_CVSS_ASSESSMENT"`)

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Validate preconditions
3. Create `CVECVSSAssessment` record
4. Recalculate ticket severity via `cvss.py` resolution cascade
5. Create `TicketAuditEvent` (`cvss_assessment_changed`,
   `old_value = NULL`, `new_value = "provider vX.Y score"`)
6. Call `reconcile_ticket_status()`
7. Return created assessment

**TicketAuditEvent**: `cvss_assessment_changed`

---

### `update_cvss_assessment()`

Updates an existing `CVECVSSAssessment` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `assessment_id` | `UUID` | Yes | CVECVSSAssessment to modify |
| `score` | `Decimal \| None` | No | New CVSS score (if changed) |
| `vector` | `str \| None` | No | New CVSS vector (if changed) |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Assessment must exist — raises `CVSSAssessmentNotFoundError` (HTTP 404,
  `error_code: "CVSS_ASSESSMENT_NOT_FOUND"`)
- Parent ticket must have `deleted_at IS NULL`
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. If no fields changed, return (no-op)
4. Update assessment fields
5. Recalculate ticket severity via `cvss.py` resolution cascade
6. Create `TicketAuditEvent` (`cvss_assessment_changed`,
   `old_value = "provider vX.Y old_score"`,
   `new_value = "provider vX.Y new_score"`)
7. Call `reconcile_ticket_status()`
8. Return updated assessment

**TicketAuditEvent**: `cvss_assessment_changed`

**Idempotency**: no-op if no fields changed.

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
- Parent ticket must have `deleted_at IS NULL`
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Delete the assessment record
4. Recalculate ticket severity via `cvss.py` resolution cascade
5. Create `TicketAuditEvent` (`cvss_assessment_changed`,
   `old_value = "provider vX.Y score"`, `new_value = NULL`)
6. Call `reconcile_ticket_status()`

**TicketAuditEvent**: `cvss_assessment_changed`

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

- Ticket must exist and have `deleted_at IS NULL`
- Ticket must be in the gate zone
- Ticket must have `cve_id IS NULL` — raises `SeverityDerivedError`
  if the ticket has an associated CVE (severity is derived from CVSS
  scores and cannot be manually overridden)

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Validate preconditions
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

- Ticket must exist and have `deleted_at IS NULL`
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
   - Sets `status = New`
   - Calls `reconcile_ticket_status(previous_status=Ignored)`
   - Produces `status_change` event with
     `old_value = Ignored, new_value = (evaluated target)` — typically
     Analysis if an assignee is present

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

- Ticket must exist and have `deleted_at IS NULL`
- Ticket must be in `Duplicated` status

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Verify current status is Duplicated
3. Clear `duplicate_of_id` (set to NULL)
4. Call `auto_assign_actor(ticket, acting_user_id, db, force=True)`:
   assigns the acting user if they hold the `vulnerability_analyst`
   role; otherwise the ticket retains its current assignee
5. Call `_reenter_gate_zone()`:
   - Saves `original_status = Duplicated`
   - Sets `status = New`
   - Calls `reconcile_ticket_status(previous_status=Duplicated)`
   - Typical outcomes depend on assignee presence:
     - VA actor (assigned): Analysis, Analyzed, or Resolved based on gates
     - Non-VA actor (unassigned): New (assignee gate not met)
6. Create `TicketAuditEvent` (`duplicate_removed`)

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
`vulnerability_analyst` role (e.g., an `automation_agent`),
auto-assignment is skipped — the ticket remains unassigned.

After the assignment, `reconcile_ticket_status` is called within the
same transaction. If the ticket was in `New` status and the operation
does not include an explicit status change (e.g., marking as duplicate
or ignored), the assignee gate (`assignee_id IS NOT NULL`) promotes
the ticket to `Analysis` automatically.

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
    to take ownership.

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
4. Set `ticket.assignee_id = acting_user_id`
5. Create `TicketAuditEvent` with `event_type = assignment`
6. Return True

> **Caller responsibility**: this function performs assignment only. It
> does not call `reconcile_ticket_status()`. Callers MUST call
> `reconcile_ticket_status()` after completing all mutations to ensure
> inactive assignee sanitization and correct gate evaluation.

## Related Operations

Non-gate ticket lifecycle operations (assignment, CVE association/
dissociation, soft-delete/restore, mark-as-duplicate, set-
confidentiality, access grant management) live in `ticket_service` —
see [ticket-service.md](ticket-service.md) for the full service contract.

These operations use the same `FOR UPDATE` pattern documented in
[Concurrency Control](#concurrency-control) and create their own
`TicketAuditEvent` records. Some call `reconcile_ticket_status()` due
to indirect gate effects (severity source change, assignee gate
satisfaction, status reconciliation after restore).

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
(CVE association/removal, assignment, restore) call
`reconcile_ticket_status` due to indirect gate effects: severity
source changes, assignee gate satisfaction, or status reconciliation
after restore. The per-function documentation in `ticket-service.md`
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

| Exception | Raised when |
|-----------|-------------|
| `TicketNotFoundError` | `FOR UPDATE` returns no row |
| `TicketNotMutableError` | Ticket is in manual zone (defense in depth — API layer catches first) |
| `TicketSoftDeletedError` | Ticket has `deleted_at IS NOT NULL` |
| `DuplicateCycleDetectedError` | Resolver detects a cycle in the chain |
| `DuplicateChainDepthError` | Resolver exceeds 50-hop limit |
| `DuplicateCVSSAssessmentError` | Assessment for the same (CVE, provider, version) combination already exists (maps to 409 `DUPLICATE_CVSS_ASSESSMENT`) |
| `CVSSAssessmentNotFoundError` | `assessment_id` does not match any existing `CVECVSSAssessment` record (maps to 404 `CVSS_ASSESSMENT_NOT_FOUND`) |
| `SeverityDerivedError` | `set_severity_override()` called on a ticket with `cve_id IS NOT NULL` — severity is derived from CVSS scores and cannot be manually overridden (maps to 409 `TICKET_SEVERITY_DERIVED`) |

Package-specific exceptions (`TrackNotFoundError`, `ProductNotFoundError`,
`PackageNotFoundError`) are defined in `package_service` — see
`docs/features/packages/package-service.md`.

## Callers

The callers table is scoped to operation categories rather than
individual endpoints.

| Caller Category | Operations Used | Context |
|-----------------|-----------------|---------|
| Ticket API mutation endpoints | CVSS operations, `set_severity_override()`, manual-zone exits | VA-initiated operations |
| CVSS sync fetcher | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | Background CVSS ingestion |
| NVD rejection handling | `reopen_from_ignored()` | CVE rejection revert |
| Admin: default CVSS version change | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | Re-evaluation triggered by config change |
| `package_service` | `reconcile_ticket_status()`, `auto_assign_actor()` | Called after every package mutation |
| `user_service.deactivate_user` | `reconcile_ticket_status()` | Calls reconcile per-ticket after bulk unassignment (indirect caller — does not use mutation functions) |

Package-centric callers (IBS release detection, product lifecycle
transitions, `add_package_to_ticket`) now call `package_service`
directly — see `docs/features/packages/package-service.md`.

## Cross-references

- `docs/features/packages/package-service.md` — package-centric
  mutations, orchestration, and query operations (imports
  `reconcile_ticket_status()` and `auto_assign_actor()`)
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
  `auto_assign_actor()`, `resolve_canonical_target()`)
- `docs/api-spec.md` — general API conventions
