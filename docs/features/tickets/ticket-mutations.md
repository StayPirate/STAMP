# Ticket Mutations Service

## Purpose

Centralize all operations that modify data relevant to ticket status
gates in a single service module (`ticket_mutations`). This ensures that
`evaluate_ticket_status` is always called after gate-relevant changes,
preventing tickets from remaining in inconsistent states regardless of
the entry point (API endpoint, Celery task, IBS RabbitMQ consumer, or
future integrations).

Without this centralization, each caller would need to independently:

- Acquire the correct row-level lock
- Apply the data mutation
- Call `evaluate_ticket_status`
- Enforce orphan cleanup invariants
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

| Entry point               | Invocation pattern                                            |
|---------------------------|---------------------------------------------------------------|
| API endpoint              | `await ticket_mutations.set_track_status(session, ...)`       |
| Celery task (release det.)| `asyncio.run(ticket_mutations.set_track_status(session, ...))` |
| IBS RabbitMQ consumer     | `asyncio.run(ticket_mutations.set_track_status(session, ...))` |

### Transaction ownership

The module does NOT commit or roll back. All operations execute within
the caller's database session. Commit responsibility belongs to the
caller.

This matches the `user_service` pattern — the module applies mutations
and creates audit events, but the transaction boundary is the caller's
decision. This enables callers to compose multiple operations within a
single transaction when needed (e.g., `add_package_to_ticket` creates
records then calls orphan checks).

### Acting user convention

All operations accept an `acting_user_id: UUID | None` parameter:

- `UUID` — action performed by an authenticated VA (enables
  auto-assignment on unassigned tickets)
- `None` — system action (release detection, CVSS sync, product
  lifecycle transitions). Auto-assignment does not apply

**API handler rule**: API endpoint handlers MUST always pass the UUID of
the authenticated user as `acting_user_id`. Passing `None` from an API
handler is a bug — it would silently bypass auto-assignment. `None` is
reserved exclusively for system entry points.

### Relationship with other modules

| Module | Relationship |
|--------|-------------|
| `services/cvss.py` | `ticket_mutations` delegates CVSS resolution and severity calculation to pure functions in `cvss.py`. The resolution cascade logic is never reimplemented inside `ticket_mutations` |
| `add_package_to_ticket` | Handles SMELT resolution and external I/O. Delegates the actual creation of `TicketPackage`, `TicketPackageTrack`, and `TicketPackageProduct` records to `ticket_mutations.add_package_records()`. The SMELT query logic does not belong in `ticket_mutations` — only the record mutations do |
| `services/ticket_service.py` | Handles non-gate operations (assignment, CVE association/dissociation, soft-delete/restore, mark-as-duplicate, set-confidentiality). These operations use the same FOR UPDATE pattern but are NOT routed through `ticket_mutations` |

## State Machine Zones

The ticket state machine has two zones that determine which operations
are valid:

### Gate zone (New, Analysis, Analyzed, Resolved)

Status is determined automatically by `evaluate_ticket_status` based on
gate conditions. The `ticket_mutations` module operates exclusively on
tickets in this zone (with the exception of manual-zone exit functions).

### Manual zone (Ignored, Duplicated)

Status is set by explicit user actions or specific system events.
`evaluate_ticket_status` never operates on tickets in the manual zone.
Gate-relevant mutations are blocked at the API level
(`require_ticket_mutable` returns 409 `TICKET_NOT_MUTABLE`).

### `_reenter_gate_zone()` (private helper)

To exit the manual zone, an explicit operation must call the private
helper `_reenter_gate_zone()`:

1. Saves the ticket's current status (Ignored or Duplicated) as
   `original_status`
2. Sets `status = New` (entering the gate zone at the lowest rung)
3. Calls `evaluate_ticket_status(previous_status=original_status)`

This produces a single `TicketAuditEvent` with the real transition
(e.g., `old_value = Ignored, new_value = Analysis`). The intermediate
`New` state is an internal implementation detail — never visible in the
audit trail.

Only the two manual-zone exit functions (`reopen_from_ignored`,
`revert_duplicate`) call this helper. It is never called directly by
external code.

## `evaluate_ticket_status()`

The sole authority for determining a ticket's status based on its
current data. This function is internal to the module — external code
interacts with it indirectly through the public mutation functions.

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
before calling `evaluate_ticket_status`, the ticket's current status
field is `New`. But the real transition for the audit trail is
`Ignored → Analysis` (not `New → Analysis`). Passing
`previous_status = Ignored` records the correct semantic transition.

### Multiple invocations within a transaction

`evaluate_ticket_status` may be called multiple times in a single
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
`deleted_at`) or that calls `evaluate_ticket_status` MUST acquire
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

### `evaluate_ticket_status` does not acquire the lock

The function assumes the caller has already acquired `FOR UPDATE` on the
ticket. This is always the case because every public function in the
module acquires the lock as its first operation, and
`evaluate_ticket_status` is only called from within those functions.

## Gate-Relevant Mutation Operations

Each function below follows the same pattern:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Apply the mutation
4. Create `TicketAuditEvent`
5. Call `evaluate_ticket_status()`
6. Return the updated record

### `set_track_status()`

Sets the affectedness status of a `TicketPackageTrack` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `track_id` | `UUID` | Yes | TicketPackageTrack to modify |
| `status` | `PackageStatus` | Yes | New status value |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Track must exist and have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone (not Ignored or Duplicated)
- Status must be a valid `PackageStatus` value

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. If status unchanged, return (no-op)
4. Update `TicketPackageTrack.status`
5. Propagate status to child products per the rules in
   `docs/features/packages/package-tracking.md` (Status Propagation)
6. Create `TicketAuditEvent` (`track_status_changed`)
7. Call `evaluate_ticket_status()`
8. Return updated track

**TicketAuditEvent**: `track_status_changed`

**Idempotency**: no-op if status is unchanged.

---

### `set_track_delivery_status()`

Sets the delivery status of a `TicketPackageTrack` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `track_id` | `UUID` | Yes | TicketPackageTrack to modify |
| `delivery_status` | `DeliveryStatus` | Yes | New delivery status value |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Track must exist and have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. If delivery_status unchanged, return (no-op)
4. Update `TicketPackageTrack.delivery_status`
5. If new delivery_status is `RELEASED`: create `TicketAuditEvent`
   (`track_released`)
6. Call `evaluate_ticket_status()`
7. Return updated track

**TicketAuditEvent**: `track_released` (only when transitioning to
`RELEASED`). Intermediate delivery status transitions (`PENDING` →
`IN_PROGRESS`) do not generate ticket audit events — they are tracked
through the submission tracking system (see
`docs/features/packages/ibs-submission-tracking.md`).

**Idempotency**: no-op if delivery_status is unchanged.

---

### `set_product_status()`

Sets the status of a `TicketPackageProduct` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `product_id` | `UUID` | Yes | TicketPackageProduct to modify |
| `status` | `PackageStatus` | Yes | New status value |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Product must exist and have `deleted_at IS NULL`
- Parent track must have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. If status unchanged, return (no-op)
4. Update `TicketPackageProduct.status`
5. Create `TicketAuditEvent` (`product_status_overridden`)
6. Call `evaluate_ticket_status()`
7. Return updated product

**TicketAuditEvent**: `product_status_overridden`

**Idempotency**: no-op if status is unchanged.

---

### `set_product_eligibility()`

Sets the eligibility flag of a `TicketPackageProduct` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `product_id` | `UUID` | Yes | TicketPackageProduct to modify |
| `eligible` | `bool` | Yes | New eligibility value |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Product must exist and have `deleted_at IS NULL`
- Parent track must have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. If eligibility unchanged, return (no-op)
4. Update `TicketPackageProduct.eligible`
5. Create `TicketAuditEvent` (`product_eligibility_changed`)
6. Call `evaluate_ticket_status()`
7. Return updated product

**TicketAuditEvent**: `product_eligibility_changed`

**Idempotency**: no-op if eligibility is unchanged.

---

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

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Validate preconditions
3. Create `CVECVSSAssessment` record
4. Recalculate ticket severity via `cvss.py` resolution cascade
5. Create `TicketAuditEvent` (`cvss_assessment_changed`,
   `old_value = NULL`, `new_value = "provider vX.Y score"`)
6. Call `evaluate_ticket_status()`
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

- Assessment must exist
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
7. Call `evaluate_ticket_status()`
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

- Assessment must exist
- Parent ticket must have `deleted_at IS NULL`
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Delete the assessment record
4. Recalculate ticket severity via `cvss.py` resolution cascade
5. Create `TicketAuditEvent` (`cvss_assessment_changed`,
   `old_value = "provider vX.Y score"`, `new_value = NULL`)
6. Call `evaluate_ticket_status()`

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

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Validate preconditions
3. If severity unchanged, return (no-op)
4. Update `ticket.severity_override`
5. Create `TicketAuditEvent` (`severity_changed`)
6. Call `evaluate_ticket_status()`
7. Return updated ticket

**Conditional gate relevance**:

- When `cve_id IS NULL`: setting `severity_override` affects the
  ticket's resolved severity, which is gate-relevant (Analyzed gate #4
  requires severity). `evaluate_ticket_status()` is always called
- When `cve_id IS NOT NULL`: severity is derived from CVSS, and
  `severity_override` is ignored. The API endpoint rejects the
  operation with `TICKET_SEVERITY_DERIVED` (400) — this check is at the
  API layer (endpoint handler), not in the module function itself

The module function always calls `evaluate_ticket_status()` regardless
(it is cheap and maintains the invariant).

**TicketAuditEvent**: `severity_changed`

**Idempotency**: no-op if severity is unchanged.

---

### `add_package_records()`

Creates `TicketPackage`, `TicketPackageTrack`, and
`TicketPackageProduct` records for a package being added to a ticket.
Called by `add_package_to_ticket` after SMELT resolution completes.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `ticket_id` | `UUID` | Yes | Target ticket |
| `package_name` | `str` | Yes | Source package name |
| `tracks` | `list[TrackData]` | Yes | Track/product data from SMELT resolution |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Ticket must exist and have `deleted_at IS NULL`
- Ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Validate preconditions
3. Create or skip `TicketPackage` (idempotent — skip if exists)
4. For each track in `tracks`:
   - Create or skip `TicketPackageTrack` (idempotent — skip if exists,
     including soft-deleted records)
   - Initial status: `ANALYSIS`, delivery_status: `PENDING`
   - For each product under the track:
     - Create or skip `TicketPackageProduct` (idempotent — skip if
       exists, including soft-deleted records)
     - Initial status: inherited from parent track (see Record Creation
       Logic below)
5. Create `TicketAuditEvent` (`package_added`)
6. Call `evaluate_ticket_status()`
7. Return created records

**TicketAuditEvent**: `package_added`

**Idempotency**: if a `TicketPackageTrack` or `TicketPackageProduct`
record already exists for the given combination (including soft-deleted
records), it is skipped without modification. Only missing records are
created. This ensures re-running `add_package_to_ticket` after a
partial failure does not produce duplicate records.

---

### `soft_delete_ticket_package()`

Soft-deletes a `TicketPackage` record (sets `deleted_at`).

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `package_id` | `UUID` | Yes | TicketPackage to soft-delete |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Package must exist and have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Set `package.deleted_at = now()`
4. Create `TicketAuditEvent` (`package_excluded`)
5. Call `evaluate_ticket_status()`
6. Return updated package

Note: child tracks and products are NOT modified (hierarchical exclusion
model — only the directly targeted record receives `deleted_at`).

**TicketAuditEvent**: `package_excluded`

---

### `soft_delete_ticket_package_track()`

Soft-deletes a `TicketPackageTrack` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `track_id` | `UUID` | Yes | TicketPackageTrack to soft-delete |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Track must exist and have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Set `track.deleted_at = now()`
4. Create `TicketAuditEvent` (`track_excluded`)
5. Enforce package orphan rule (see Orphan Cleanup Invariants)
6. Call `evaluate_ticket_status()`
7. Return updated track

Note: child products are NOT modified (hierarchical exclusion model).

**TicketAuditEvent**: `track_excluded`

---

### `soft_delete_ticket_package_product()`

Soft-deletes a `TicketPackageProduct` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `product_id` | `UUID` | Yes | TicketPackageProduct to soft-delete |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Product must exist and have `deleted_at IS NULL`
- Parent track must have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Set `product.deleted_at = now()`
4. Create `TicketAuditEvent` (`product_excluded`)
5. Enforce track orphan rule (see Orphan Cleanup Invariants)
6. Call `evaluate_ticket_status()`
7. Return updated product

**TicketAuditEvent**: `product_excluded`

---

### `restore_ticket_package()`

Restores a soft-deleted `TicketPackage` record (clears `deleted_at`).

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `package_id` | `UUID` | Yes | TicketPackage to restore |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Package must exist and have `deleted_at IS NOT NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Clear `package.deleted_at`
4. Create `TicketAuditEvent` (`package_restored`)
5. Call `evaluate_ticket_status()`
6. Return updated package

**TicketAuditEvent**: `package_restored`

---

### `restore_ticket_package_track()`

Restores a soft-deleted `TicketPackageTrack` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `track_id` | `UUID` | Yes | TicketPackageTrack to restore |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Track must exist and have `deleted_at IS NOT NULL`
- Parent package must have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Clear `track.deleted_at`
4. Create `TicketAuditEvent` (`track_restored`)
5. Call `evaluate_ticket_status()`
6. Return updated track

**TicketAuditEvent**: `track_restored`

---

### `restore_ticket_package_product()`

Restores a soft-deleted `TicketPackageProduct` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `product_id` | `UUID` | Yes | TicketPackageProduct to restore |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Product must exist and have `deleted_at IS NOT NULL`
- Parent track must have `deleted_at IS NULL`
- Parent ticket must not be soft-deleted
- Parent ticket must be in the gate zone

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Validate preconditions
3. Clear `product.deleted_at`
4. Create `TicketAuditEvent` (`product_restored`)
5. Call `evaluate_ticket_status()`
6. Return updated product

**TicketAuditEvent**: `product_restored`

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
| `assignee_id` | `UUID \| None` | No | VA to assign (for manual reopen) or `None` (for system reopen — restores last assignee or leaves unassigned) |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Ticket must exist and have `deleted_at IS NULL`
- Ticket must be in `Ignored` status

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Verify current status is Ignored
3. Set assignee (if applicable):
   - Manual reopen: `assignee_id` is the acting VA
   - System reopen: restore the last active assignee, or leave
     unassigned if none exists or previous assignee is deactivated
4. Call `_reenter_gate_zone()`:
   - Saves `original_status = Ignored`
   - Sets `status = New`
   - Calls `evaluate_ticket_status(previous_status=Ignored)`
   - Produces `status_change` event with
     `old_value = Ignored, new_value = (evaluated target)` — typically
     Analysis if an assignee is present
5. Create `TicketAuditEvent` (`assignment` if assignee was set)

**TicketAuditEvent**: `status_change` (via `evaluate_ticket_status`) +
optionally `assignment`

---

### `revert_duplicate()`

Reverts a ticket from Duplicated status.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `ticket_id` | `UUID` | Yes | Ticket to revert |
| `acting_user_id` | `UUID` | Yes | VA performing the revert (becomes new assignee) |

**Preconditions**:

- Ticket must exist and have `deleted_at IS NULL`
- Ticket must be in `Duplicated` status

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Verify current status is Duplicated
3. Clear `duplicate_of_id` (set to NULL)
4. Reassign the ticket to the acting VA
5. Call `_reenter_gate_zone()`:
   - Saves `original_status = Duplicated`
   - Sets `status = New`
   - Calls `evaluate_ticket_status(previous_status=Duplicated)`
   - Since the revert assigns a new VA (satisfying the assignee gate),
     the typical outcomes are:
     - All Resolved gates met → Resolved
     - All Analyzed gates met → Analyzed
     - Assignee present but not all Analyzed gates met → Analysis
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

When a VA performs any modifying operation on a ticket with
`assignee_id = NULL`, the ticket is automatically assigned to the
acting VA. A `TicketAuditEvent` with `event_type = assignment` is
created atomically in the same transaction as the modifying operation.

After the assignment, `evaluate_ticket_status` is called within the
same transaction. If the ticket was in `New` status and the operation
does not include an explicit status change (e.g., marking as duplicate
or ignored), the assignee gate (`assignee_id IS NOT NULL`) promotes
the ticket to `Analysis` automatically.

This rule is enforced within each public function of the module: before
applying the main mutation, check `ticket.assignee_id`. If `None` and
`acting_user_id` is a UUID (not `None`), set
`ticket.assignee_id = acting_user_id` and create the assignment audit
event.

This rule does not apply to system operations (`acting_user_id = None`).
Only VA-initiated actions trigger auto-assignment.

## Orphan Cleanup Invariants

The module enforces automatic cleanup of empty parent records. These are
generic rules that apply regardless of the trigger — any current or
future feature that soft-deletes a product or track automatically
benefits from these invariants. The orphan rule triggers **only on
soft-deletion events**, not on restore or other mutations.

### Invariant 1 — Track orphan rule

After every product soft-deletion, check whether the parent
`TicketPackageTrack` has zero remaining products with
`deleted_at IS NULL` (direct check). If zero directly-active products
remain, the track receives its own `deleted_at` (direct soft-deletion).
Products under the track are NOT modified — they already have their own
`deleted_at`.

### Invariant 2 — Package orphan rule

After every track soft-deletion, check whether the parent
`TicketPackage` has zero remaining tracks with `deleted_at IS NULL`
(direct check). If zero directly-active tracks remain, the package
receives its own `deleted_at`. Tracks and products under the package
are NOT modified.

### Cascading composition

The invariants compose naturally. Soft-deleting a product may trigger
the track orphan rule, which may trigger the package orphan rule:

```
soft_delete_ticket_package_product(record, user)
  → TicketAuditEvent (product_excluded)
  → evaluate_ticket_status()
  → _enforce_track_orphan_rule()
      → if 0 directly-active products:
          set track.deleted_at (direct)
          → TicketAuditEvent (track_excluded, user_id=NULL)
          → evaluate_ticket_status()
          → _enforce_package_orphan_rule()
              → if 0 directly-active tracks:
                  set package.deleted_at (direct)
                  → TicketAuditEvent (package_excluded, user_id=NULL)
                  → evaluate_ticket_status()
```

Orphan-triggered soft-deletions create `TicketAuditEvent` records with
`user_id = NULL` (system action), distinguishing them from VA-initiated
exclusions. Each orphan soft-deletion sets `deleted_at` only on the
parent — no cascade to children (per the hierarchical exclusion model).

## Record Creation Logic

When `ticket_mutations` creates a new `TicketPackageTrack` record, the
initial status is always `ANALYSIS` and `delivery_status` is `PENDING`.

When it creates a new `TicketPackageProduct` record, it determines the
initial status by inheriting from the parent `TicketPackageTrack`:

- Parent in `ANALYSIS` → `ANALYSIS`
- Parent in `AFFECTED` → status is set to `AFFECTED`; eligibility is
  calculated separately (CVSS threshold, Reactive LTSS override) and
  stored in the `eligible` boolean
- Parent in any other status (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`) →
  inherit the same status

This logic is internal to `ticket_mutations` — callers (including
`add_package_to_ticket`) do not specify the initial status.

## Related Operations

Operations that follow the module's concurrency pattern (`FOR UPDATE` on
the Ticket row + `evaluate_ticket_status` call where applicable) but are
NOT routed through the `ticket_mutations` module. Documented here for
implementers to have a single reference for the full `FOR UPDATE`
landscape on the Ticket entity.

### CVE dissociation

Removes the CVE association from a ticket. Modifies `Ticket.cve_id` (not
gate-relevant by itself), but the side effect on severity triggers
`evaluate_ticket_status`. Full behavioral steps in
[tickets.md](tickets.md#cve-dissociation).

### Ticket soft-delete and restore

Admin operations that set/clear `Ticket.deleted_at`. Restore calls
`evaluate_ticket_status` to reconcile status with current gate
conditions. Full behavioral steps in
[tickets.md](tickets.md#soft-delete).

### Mark-as-duplicate

Sets `Ticket.status = Duplicated` and `Ticket.duplicate_of_id`. Enters
the manual zone directly — does NOT call `evaluate_ticket_status`. Full
behavioral steps in
[tickets.md](tickets.md#mark-as-duplicate-operation).

### Set-confidentiality

Modifies `Ticket.is_confidential`. Not gate-relevant, does not call
`evaluate_ticket_status`. Requires `FOR UPDATE` because it modifies the
Ticket row. Full behavioral steps in
[tickets.md](tickets.md#set-confidentiality).

## Contract

Every service-layer operation that modifies data relevant to ticket
status gates MUST go through the `ticket_mutations` module. Direct
modification of `TicketPackageTrack`, `TicketPackageProduct`, or
`CVECVSSAssessment` records outside this module is a bug — it bypasses
status re-evaluation and may leave the ticket in an inconsistent state.

Relevant data includes:

- `TicketPackageTrack` records (creation, soft-deletion/restore, status
  change, delivery status change)
- `TicketPackageProduct` records (creation, soft-deletion/restore,
  status change, eligibility change)
- `CVECVSSAssessment` records (creation, update, deletion)
- Ticket severity (`severity_override` or CVSS-derived severity)
- Package addition or soft-deletion/restore

Operations that do NOT modify gate-relevant data (assignment, duplicate
set/remove, CVE association/removal, ticket-level soft-delete/restore)
are NOT required to go through this module — they create
`TicketAuditEvent` records in their own services.

## Architectural Test Requirement

A parametrized integration test MUST be implemented to verify that the
`ticket_mutations` module produces the correct ticket status after every
type of relevant mutation. The test must cover:

- **Forward transitions**: each gate condition being satisfied one by
  one until the ticket advances (Analysis → Analyzed → Resolved)
- **Backward transitions**: each gate condition being broken after the
  ticket has advanced (Analyzed → Analysis, Resolved → Analyzed,
  Resolved → Analysis)
- **No-op cases**: mutations that do not affect gate conditions
- **Edge cases**: ticket with no packages, ticket without CVE (no SUSE
  CVSS gate), all tracks in final status but severity not set

This test serves as a permanent architectural fitness function: if a
new service operation modifies gate-relevant data without going through
the `ticket_mutations` module, the test will fail because the ticket
status will not match the expected state.

## Service Exceptions

| Exception | Raised when |
|-----------|-------------|
| `TicketNotFoundError` | `FOR UPDATE` returns no row |
| `TicketNotMutableError` | Ticket is in manual zone (defense in depth — API layer catches first) |
| `TicketSoftDeletedError` | Ticket has `deleted_at IS NOT NULL` |
| `TrackNotFoundError` | Track ID does not exist or is soft-deleted |
| `ProductNotFoundError` | Product ID does not exist or is soft-deleted |
| `PackageNotFoundError` | Package ID does not exist or is soft-deleted |
| `DuplicateCycleDetectedError` | Resolver detects a cycle in the chain |
| `DuplicateChainDepthError` | Resolver exceeds 50-hop limit |

## Callers

The callers table is scoped to operation categories rather than
individual endpoints. Maintaining an exhaustive per-endpoint table is
unrealistic at this scale and would rot.

| Caller Category | Operations Used | Context |
|-----------------|-----------------|---------|
| Ticket API mutation endpoints | All gate-relevant + manual-zone exits | VA-initiated operations |
| CVSS sync fetcher | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | Background CVSS ingestion |
| IBS track release detection | `set_track_status()`, `set_track_delivery_status()` | Automated track release |
| IBS product release detection | `set_product_status()` (released_at) | Automated product release |
| `add_package_to_ticket` | `add_package_records()` | Package addition flow |
| Product lifecycle transitions | `set_product_eligibility()`, `soft_delete_ticket_package_product()` | AIMAAS threshold changes |
| NVD rejection handling | `reopen_from_ignored()` | CVE rejection revert |
| Admin: default CVSS version change | `create_cvss_assessment()`, `update_cvss_assessment()`, `delete_cvss_assessment()` | Re-evaluation triggered by config change |
| User deactivation side effects | (none — deactivation unassigns via direct query, not through ticket_mutations) | Clarification: NOT a caller |

## Cross-references

- `docs/features/tickets/tickets.md` — ticket lifecycle, gate
  conditions, API endpoints
- `docs/features/tickets/ticket-audit-log.md` — event type contract
- `docs/features/tickets/cvss-scoring.md` — CVSS resolution cascade,
  severity calculation
- `docs/features/packages/package-tracking.md` — track/product concepts,
  status propagation, hierarchical exclusion model
- `docs/features/packages/product-lifecycle-transitions.md` — AIMAAS
  threshold changes triggering eligibility mutations
- `docs/features/identity/user-service.md` — `deactivate_user` bulk
  unassignment (complementary to inactive assignee sanitization)
- `docs/conventions.md` — Transaction and Locking (generic pessimistic
  locking pattern)
- `docs/api-spec.md` — general API conventions
