# Package Service

## Purpose

Centralize all package-centric operations — mutations on
`TicketPackage`, `TicketPackageTrack`, and `TicketPackageProduct`
records, orchestration with external systems (SMELT), and package query
functions — in a single service module (`package_service`). This
ensures that:

- `ticket_mutations.evaluate_ticket_status()` is always called after
  gate-relevant package changes
- Orphan cleanup invariants are consistently enforced
- Record creation logic (initial status, eligibility) is centralized
- The complete package lifecycle (orchestration + mutation + query) is
  owned by a single module

Without this centralization, package-centric logic would be scattered
across `ticket_mutations` (mutations), `package-model.md` endpoint
handlers (orchestration), and ad-hoc query code, leading to
inconsistency and missed re-evaluations.

## Architecture

### Module location

`backend/app/services/package_service.py`

### Async pattern

The service is implemented as async functions. The API (FastAPI) is the
primary consumer and calls the service directly with `await`. Entry
points that operate in a synchronous context (Celery tasks, IBS
RabbitMQ consumer) call the service via `asyncio.run()`.

| Entry point               | Invocation pattern                                             |
|---------------------------|----------------------------------------------------------------|
| API endpoint              | `await package_service.set_track_status(session, ...)`         |
| Celery task (release det.)| `asyncio.run(package_service.set_track_status(session, ...))` |
| IBS RabbitMQ consumer     | `asyncio.run(package_service.set_track_status(session, ...))` |

### Transaction ownership

The module does NOT commit or roll back. All operations execute within
the caller's database session. Commit responsibility belongs to the
caller.

This matches the `ticket_mutations` and `user_service` pattern — the
module applies mutations and creates audit events, but the transaction
boundary is the caller's decision. This enables callers to compose
multiple operations within a single transaction when needed (e.g.,
`add_package_to_ticket` creates records then calls orphan checks).

### Acting user convention

All mutation operations accept an `acting_user_id: UUID | None`
parameter:

- `UUID` — action performed by an authenticated VA (enables
  auto-assignment on unassigned tickets)
- `None` — system action (release detection, product lifecycle
  transitions). Auto-assignment does not apply

**API handler rule**: API endpoint handlers MUST always pass the UUID of
the authenticated user as `acting_user_id`. Passing `None` from an API
handler is a bug — it would silently bypass auto-assignment. `None` is
reserved exclusively for system entry points.

### Relationship with other modules

| Module | Relationship |
|--------|-------------|
| `services/ticket_mutations.py` | `package_service` imports `evaluate_ticket_status()` and `auto_assign_if_needed()` from `ticket_mutations`. The dependency is unidirectional: `package_service` depends on `ticket_mutations`, but `ticket_mutations` does NOT depend on `package_service` |
| `services/cvss.py` | `package_service` delegates CVSS resolution and eligibility calculation to pure functions in `cvss.py` (for record creation and eligibility evaluation) |
| `core/filters.py` | `search_packages()` receives a `confidentiality_filter` (a SQLAlchemy `ColumnElement`) built by the endpoint handler via `confidential_ticket_filter()`. The service function is unaware of access rules |

### Module invariant: I/O-then-Lock pattern

`package_service` contains both orchestration functions that perform
external I/O (e.g., `add_package_to_ticket` queries SMELT) and mutation
functions that acquire `FOR UPDATE` locks (e.g., `add_package_records`).
The following invariant MUST be maintained:

> Functions that perform external I/O MUST NOT acquire `FOR UPDATE`
> locks themselves. External I/O happens in orchestration functions,
> which delegate record mutations to lock-acquiring functions. The lock
> is acquired only after all external data has been fetched.

This is an application of the I/O-then-Lock corollary defined in
`docs/conventions.md` (Transaction Hygiene Rules). Violation of this
invariant would block concurrent mutations on the same ticket for the
duration of an external HTTP call.

## Auto-Assignment Rule

When a VA performs any modifying operation on a ticket with
`assignee_id = NULL`, the ticket is automatically assigned to the
acting VA. This is enforced via the shared helper
`ticket_mutations.auto_assign_if_needed()`.

**Module-level rule**: auto-assignment is always applied by the function
that acquires the `FOR UPDATE` lock, never by orchestration wrappers.
For example, `add_package_to_ticket` does NOT apply auto-assignment —
it delegates to `add_package_records()`, which calls
`auto_assign_if_needed` after acquiring the lock.

See `docs/features/tickets/ticket-mutations.md` for the helper's
signature and behavior.

## Package Mutation Operations

Each function below follows the same pattern:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Apply the mutation
5. Create `TicketAuditEvent`
6. Call `ticket_mutations.evaluate_ticket_status()`
7. Return the updated record

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. If status unchanged → return (no-op, no log, no audit event)
5. If `acting_user_id` is `None` and current status is final
   (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`) → reject: log warning
   `"Rejected automatic transition from {current_status} to {new_status}
   on track {track_id}: track is in final status"`, return track
   unchanged (no audit event)
6. Update `TicketPackageTrack.status`
7. Propagate status to child products and recalculate eligibility per the
   rules in `docs/features/packages/package-model.md`
   ([VA Sets a Status on a Track](package-model.md#va-sets-a-status-on-a-track)).
   For each product whose `eligible` value actually changes during
   recalculation, create a `TicketAuditEvent`
   (`product_eligibility_changed`, `user_id = NULL`). Products whose
   eligibility is unchanged produce no event
8. Create `TicketAuditEvent` (`track_status_changed`)
9. Call `evaluate_ticket_status()`
10. Return updated track

**TicketAuditEvent**: `track_status_changed`

**Idempotency**: no-op if status is unchanged (step 4).

**Final-status protection**: system callers (`acting_user_id = None`)
cannot transition tracks out of final states. If the requested status
differs from the current final status, the transition is rejected with a
warning log (step 5). This enforces the invariant from
`package-model.md`
([Automatic Transitions](package-model.md#automatic-transitions)) that
final-status records are not eligible as source states for automatic
transitions. VA callers (`acting_user_id` present) can transition from
any state — the VA has full override authority.

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
- The transition `current_delivery_status → new_delivery_status` must be
  valid per the delivery status transition rules in
  `docs/features/packages/package-model.md`. In particular, any regression
  from `RELEASED` is illegal.

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Validate transition: verify that `current_delivery_status →
   new_delivery_status` is a legal transition per the delivery status
   state machine defined in `package-model.md`. If the transition is
   illegal (e.g., `RELEASED → IN_PROGRESS`, `RELEASED → PENDING`), raise
   `InvalidDeliveryStatusTransition` without modifying the record.
5. If delivery_status unchanged, return (no-op)
6. Update `TicketPackageTrack.delivery_status`
6. If new delivery_status is `RELEASED`: create `TicketAuditEvent`
   (`track_released`)
7. Return updated track

**Note**: this function does not trigger ticket status re-evaluation
because `delivery_status` is not part of any gate condition. Only
`released_at` on products (set by product-level release detection)
participates in the Resolved gate. If `delivery_status` ever enters a
gate condition in the future, the `evaluate_ticket_status()` call can be
reintroduced at that time.

**TicketAuditEvent**: `track_released` (only when transitioning to
`RELEASED`). Intermediate delivery status transitions (`PENDING` ->
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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. If status unchanged, return (no-op)
5. Update `TicketPackageProduct.status`
6. Recalculate eligibility if `is_eligible_override = false` (products
   with eligibility override are not recalculated). See
   `docs/features/packages/package-model.md`
   ([Axis 2: Eligibility](package-model.md#axis-2-eligibility-per-product-only))
   for the computation rules. If eligibility recalculation changes the
   `eligible` value, create a `TicketAuditEvent`
   (`product_eligibility_changed`) in the same transaction
7. Create `TicketAuditEvent` (`product_status_overridden`)
8. Call `evaluate_ticket_status()`
9. Return updated product

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. If eligibility unchanged, return (no-op)
5. Update `TicketPackageProduct.eligible`
6. Create `TicketAuditEvent` (`product_eligibility_changed`)
7. Call `evaluate_ticket_status()`
8. Return updated product

**TicketAuditEvent**: `product_eligibility_changed`

**Idempotency**: no-op if eligibility is unchanged.

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Create or skip `TicketPackage` (idempotent — skip if exists)
5. For each track in `tracks`:
   - Create or skip `TicketPackageTrack` (idempotent — skip if exists,
     including soft-deleted records)
   - Initial status: `ANALYSIS`, delivery_status: `PENDING`
   - For each product under the track:
     - Create or skip `TicketPackageProduct` (idempotent — skip if
       exists, including soft-deleted records)
     - Initial status: inherited from parent track (see Record Creation
       Logic below)
6. Create `TicketAuditEvent` (`package_added`)
7. Call `evaluate_ticket_status()`
8. Return created records

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Set `package.deleted_at = now()`
5. Create `TicketAuditEvent` (`package_excluded`)
6. Call `evaluate_ticket_status()`
7. Return updated package

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Set `track.deleted_at = now()`
5. Create `TicketAuditEvent` (`track_excluded`)
6. Enforce package orphan rule (see Orphan Cleanup Invariants)
7. Call `evaluate_ticket_status()`
8. Return updated track

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Set `product.deleted_at = now()`
5. Create `TicketAuditEvent` (`product_excluded`)
6. Enforce track orphan rule (see Orphan Cleanup Invariants)
7. Call `evaluate_ticket_status()`
8. Return updated product

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Clear `package.deleted_at`
5. Create `TicketAuditEvent` (`package_restored`)
6. Call `evaluate_ticket_status()`
7. Return updated package

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Clear `track.deleted_at`
5. Create `TicketAuditEvent` (`track_restored`)
6. Call `evaluate_ticket_status()`
7. Return updated track

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
2. Call `auto_assign_if_needed()`
3. Validate preconditions
4. Clear `product.deleted_at`
5. Create `TicketAuditEvent` (`product_restored`)
6. Call `evaluate_ticket_status()`
7. Return updated product

**TicketAuditEvent**: `product_restored`

## Orchestration Operations

### `add_package_to_ticket()`

Orchestrates the full package addition flow: queries SMELT for track and
product resolution, then delegates record creation to
`add_package_records()`. This function performs external I/O and MUST NOT
acquire `FOR UPDATE` locks itself (I/O-then-Lock invariant).

See `docs/features/packages/package-model.md` (Adding Packages to a
Ticket) for the full behavioral specification, triggers, and SMELT query
details.

**Signature** (conceptual):

```python
async def add_package_to_ticket(
    db: AsyncSession,
    ticket_id: UUID,
    package_name: str,
    acting_user_id: UUID | None = None,
) -> AddPackageResult:
```

**Behavior**:

1. Create a `TicketPackage` record for the package if one does not
   already exist. If a record already exists (active or soft-deleted),
   skip creation and proceed to step 2.
2. Query SMELT to resolve all currently maintained tracks and products
   for the given package.
3. Infer `workflow_type` for each resolved track.
4. Delegate record creation to `add_package_records()` — this is where
   the `FOR UPDATE` lock is acquired.
5. Resolve and cache the IBS bugowner for the package.
6. Enqueue `discover_submissions_for_ticket_package()` for retroactive
   SR/RR discovery.
7. Return an `AddPackageResult` with creation/skip counts.

**Auto-assignment**: applied by `add_package_records()` (the function
that acquires the lock), NOT by `add_package_to_ticket()`.

## Query Operations

### `get_ticket_packages()`

Returns the complete package tree for a ticket, including soft-deleted
records (with `deleted_at` visible on each level).

```python
async def get_ticket_packages(
    db: AsyncSession,
    ticket_id: UUID,
) -> list[PackageDetail]:
```

**Behavior**:

1. Query all `TicketPackage` records for the ticket (including
   soft-deleted)
2. For each package, load all tracks and products (including
   soft-deleted), with `deleted_at` visible
3. Compute `delivery_relevant` for each track
4. Join bugowner data from `PackageBugowner`
5. Return assembled `PackageDetail[]`, sorted alphabetically by
   `package_name`

**No locking needed** — this is a read-only operation.
**No filtering** — the caller (endpoint handler) is responsible for
access control via `require_accessible_ticket` before invoking this
function.

This function is called by both `GET /api/v1/tickets/{ticket_id}/packages`
and `GET /api/v1/tickets/{ticket_id}` (to populate the `packages` field
in `TicketDetail`).

### `search_packages()`

Searches packages across all tickets with filtering, pagination, and
confidentiality enforcement.

```python
async def search_packages(
    db: AsyncSession,
    confidentiality_filter: ColumnElement,  # from confidential_ticket_filter()
    search: str | None = None,
    name: str | None = None,
    ticket_status: list[TicketStatus] | None = None,
    include_deleted: str | None = None,  # soft-deleted *tickets* visibility
    caller_is_admin: bool = False,       # for include_deleted enforcement
    sort_by: Literal["package_name", "created_at"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    page: int = 1,
    per_page: int = 20,
) -> PaginatedResult[PackageListItem]:
```

**Behavior**:

1. Build base query joining `TicketPackage` -> `Ticket`
2. Exclude soft-deleted packages: filter `TicketPackage.deleted_at IS NULL`
3. Apply soft-deleted ticket filter: exclude packages belonging to
   soft-deleted tickets (`Ticket.deleted_at IS NOT NULL`) unless
   `include_deleted` is set and caller is Admin
4. Apply `confidentiality_filter` (pre-built by the endpoint handler
   via `confidential_ticket_filter()` — see
   `docs/features/tickets/tickets.md`, Confidentiality Filtering)
5. Apply `ticket_status` filter (if provided; invalid values silently
   ignored)
6. Apply `search` (ILIKE `%term%` substring match on `package_name`) or
   `name` (exact match)
7. Apply sorting (primary: `sort_by`/`sort_order`; secondary: `id` for
   deterministic pagination)
8. Execute paginated query
9. Compute `track_summary` via SQL aggregation (`COUNT(*) FILTER (WHERE
   status = ...)`) in the same query — NOT as Python post-processing —
   to avoid N+1 query patterns. Counts only tracks with
   `deleted_at IS NULL` (active tracks)
10. Return paginated `PackageListItem[]`

**No locking needed** — this is a read-only operation.

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
  -> TicketAuditEvent (product_excluded)
  -> evaluate_ticket_status()
  -> _enforce_track_orphan_rule()
      -> if 0 directly-active products:
          set track.deleted_at (direct)
          -> TicketAuditEvent (track_excluded, user_id=NULL)
          -> evaluate_ticket_status()
          -> _enforce_package_orphan_rule()
              -> if 0 directly-active tracks:
                  set package.deleted_at (direct)
                  -> TicketAuditEvent (package_excluded, user_id=NULL)
                  -> evaluate_ticket_status()
```

Orphan-triggered soft-deletions create `TicketAuditEvent` records with
`user_id = NULL` (system action), distinguishing them from VA-initiated
exclusions. Each orphan soft-deletion sets `deleted_at` only on the
parent — no cascade to children (per the hierarchical exclusion model).

## Record Creation Logic

When `package_service` creates a new `TicketPackageTrack` record, the
initial status is always `ANALYSIS` and `delivery_status` is `PENDING`.

When it creates a new `TicketPackageProduct` record, it determines the
initial status by inheriting from the parent `TicketPackageTrack`:

- Parent in `ANALYSIS` -> `ANALYSIS`
- Parent in `AFFECTED` -> `AFFECTED`
- Parent in any other status (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`) ->
  inherit the same status

Eligibility is always calculated at creation time regardless of the
inherited status. See `docs/features/packages/package-model.md`
([Axis 2: Eligibility](package-model.md#axis-2-eligibility-per-product-only))
for the computation rules.

This logic is internal to `package_service` — callers (including
`add_package_to_ticket`) do not specify the initial status.

## Concurrency Control

The generic pessimistic locking pattern and transaction hygiene rules
are defined in `docs/conventions.md` (Transaction and Locking). This
section documents package-specific refinements only.

All mutation functions in this module acquire `FOR UPDATE` on the parent
Ticket row as the first operation. This serializes concurrent package
mutations on the same ticket at the database level. The lock is released
automatically when the transaction commits or rolls back.

The I/O-then-Lock invariant (see Architecture section) is an additional
constraint specific to this module: orchestration functions that perform
external I/O MUST NOT acquire `FOR UPDATE` locks.

## Service Exceptions

| Exception | Raised when |
|-----------|-------------|
| `TicketNotFoundError` | `FOR UPDATE` returns no row |
| `TicketNotMutableError` | Ticket is in manual zone (defense in depth — API layer catches first) |
| `TicketSoftDeletedError` | Ticket has `deleted_at IS NOT NULL` |
| `TrackNotFoundError` | Track ID does not exist or is soft-deleted |
| `ProductNotFoundError` | Product ID does not exist or is soft-deleted |
| `PackageNotFoundError` | Package ID does not exist or is soft-deleted |
| `InvalidDeliveryStatusTransition` | Requested delivery status transition is illegal (e.g., regression from `RELEASED`) |

## Callers

The callers table is scoped to operation categories rather than
individual endpoints.

| Caller Category | Operations Used | Context |
|-----------------|-----------------|---------|
| Package API mutation endpoints | `set_track_status()`, `set_product_status()`, soft-delete/restore functions | VA-initiated operations |
| Package API read endpoints | `get_ticket_packages()`, `search_packages()` | Public read access |
| Ticket detail endpoint | `get_ticket_packages()` | Populates `packages` field in `TicketDetail` |
| IBS track release detection | `set_track_status()` | Automated track release (sets FIXED) |
| IBS product release detection | `set_product_status()` (released_at) | Automated product release |
| `add_package_to_ticket` | `add_package_records()` | Package addition flow (internal) |
| Product lifecycle transitions | `set_product_eligibility()`, `soft_delete_ticket_package_product()` | AIMAAS threshold changes |
| IBS RabbitMQ consumer (`package.commit`) | `set_track_status()` | Real-time track release detection (sets FIXED) |
| IBS submission tracking | `set_track_delivery_status()` | SR/RR state changes (sets delivery_status) |

## Architectural Test Requirement

A parametrized integration test MUST be implemented to verify that the
`package_service` mutation functions correctly trigger
`evaluate_ticket_status` and produce the expected ticket status
transitions. The test must cover:

- **Forward transitions**: package mutations causing ticket advancement
  (e.g., setting all tracks to final status triggers Analyzed -> Resolved)
- **Backward transitions**: package mutations breaking gate conditions
  (e.g., restoring a soft-deleted track with non-final status)
- **Orphan cascade**: soft-deleting the last product triggers track
  deletion, then package deletion, with correct audit events at each
  level
- **Auto-assignment**: mutations on unassigned tickets trigger assignment
  to the acting VA

## Cross-references

- `docs/features/tickets/ticket-mutations.md` — `evaluate_ticket_status()`,
  `auto_assign_if_needed()`, ticket-centric mutations
- `docs/features/tickets/tickets.md` — ticket lifecycle, gate
  conditions, confidentiality filtering (`confidential_ticket_filter()`)
- `docs/features/tickets/ticket-audit-log.md` — event type contract
- `docs/features/tickets/cvss-scoring.md` — CVSS resolution cascade,
  eligibility threshold comparison
- `docs/features/packages/package-model.md` — track/product concepts,
  status propagation, hierarchical exclusion model, API endpoints
- `docs/features/packages/product-lifecycle-transitions.md` — AIMAAS
  threshold changes triggering eligibility mutations
- `docs/features/packages/ibs-track-release-detection.md` — IBS
  track-level release detection
- `docs/features/packages/ibs-product-release-detection.md` — IBS
  product-level release detection
- `docs/features/packages/ibs-submission-tracking.md` — SR/RR tracking,
  delivery pipeline
- `docs/features/integrations/ibs-rabbitmq-integration.md` — real-time
  IBS event consumption
- `docs/features/packages/package-bugowner.md` — bugowner resolution
- `docs/conventions.md` — Transaction and Locking (pessimistic locking,
  I/O-then-Lock corollary)
- `docs/api-spec.md` — general API conventions
