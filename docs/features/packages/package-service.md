# Package Service

## Purpose

Centralize all package-centric operations — mutations on
`TicketPackage`, `TicketPackageTrack`, and `TicketPackageProduct`
records, orchestration with external systems (SMELT), and package query
functions — in a single service module (`package_service`). This
ensures that:

- `ticket_mutations.reconcile_ticket_status()` is always called after
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
| `services/ticket_mutations.py` | `package_service` imports `reconcile_ticket_status()`, `auto_assign_actor()`, and `ensure_ticket_operable()` from `ticket_mutations`. The dependency is unidirectional: `package_service` depends on `ticket_mutations`, but `ticket_mutations` does NOT depend on `package_service`. Post-transition catch-up (CVSS recalculation + fetcher enqueue) is handled internally by `reconcile_ticket_status()` — no caller action needed |
| `services/cvss.py` | `package_service` delegates eligibility calculation to `resolve_eligibility_score()` in `cvss.py` (SUSE-only, 2-step cascade — see Eligibility Score Resolution in `docs/features/tickets/cvss-scoring.md`) |
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
`ticket_mutations.auto_assign_actor()`.

**Module-level rule**: auto-assignment is always applied by the function
that acquires the `FOR UPDATE` lock, never by orchestration wrappers.
For example, `add_package_to_ticket` does NOT apply auto-assignment —
it delegates to `add_package_records()`, which calls
`auto_assign_actor` after acquiring the lock.

See `docs/features/tickets/ticket-mutations.md` for the helper's
signature and behavior.

## Package Mutation Operations

Each function below follows the same pattern (except
`set_product_released_at()`, which is system-only and omits
auto-assignment — see its section for details):

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate additional preconditions
5. Apply the mutation
6. Create `TicketAuditEvent`
7. Call `ticket_mutations.reconcile_ticket_status()`
8. Return the updated record

### `set_track_status()`

Sets the affectedness status of a `TicketPackageTrack` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `track_id` | `UUID` | Yes | TicketPackageTrack to modify |
| `status` | `PackageStatus` | Yes | New status value |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |
| `force` | `bool` | No | Admin escape hatch (default `False`) — allows setting `FIXED` when `acting_user_id` is present |

**Preconditions**:

- Parent ticket must be operable (`ensure_ticket_operable`)
- Track must exist
- Status must be a valid `PackageStatus` value

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. If status unchanged → return (no-op, no log, no audit event)
5. If `status == FIXED` and `acting_user_id is not None` and `force is
   False` → raise `TrackFixedStatusRestrictedError` (only system
   detection or admin force can set FIXED)
6. If `acting_user_id` is `None` and current status is final
   (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`) → reject: log warning
   `"Rejected automatic transition from {current_status} to {new_status}
   on track {track_id}: track is in final status"`, return track
   unchanged (no audit event)
7. Update `TicketPackageTrack.status`
8. Create `TicketAuditEvent` (`track_status_changed`)
9. Call `reconcile_ticket_status()`
10. Return updated track

**TicketAuditEvent**: `track_status_changed`

**Idempotency**: no-op if status is unchanged (step 4).

**FIXED restriction**: `FIXED` is system-managed — only system callers
(`acting_user_id = None`) or admin callers with `force=True` can set it
(step 5). The service does NOT query the RBAC system — it trusts the
caller to have verified the `admin_ticket_ops` capability before passing
`force=True`. CLI commands MUST verify `admin_ticket_ops` before passing
`force=True`. Passing `force=True` without capability verification is a
bug.

**Final-status protection**: system callers (`acting_user_id = None`)
cannot transition tracks out of final states. If the requested status
differs from the current final status, the transition is rejected with a
warning log (step 6). This enforces the invariant from
`package-model.md`
([Automatic Transitions](package-model.md#automatic-transitions)) that
final-status records are not eligible as source states for automatic
transitions. VA callers (`acting_user_id` present) can transition from
any state to any non-FIXED target — the VA has full override authority
on non-FIXED target transitions.

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

- Parent ticket must be operable (`ensure_ticket_operable`)
- Track must exist
- The transition `current_delivery_status → new_delivery_status` must be
  valid per the delivery status transition rules in
  `docs/features/packages/package-model.md`. In particular, any regression
  from `RELEASED` is illegal.

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. Validate transition: verify that `current_delivery_status →
   new_delivery_status` is a legal transition per the delivery status
   state machine defined in `package-model.md`. If the transition is
   illegal (e.g., `RELEASED → IN_PROGRESS`, `RELEASED → PENDING`), raise
   `InvalidDeliveryStatusTransition` without modifying the record.
5. If delivery_status unchanged, return (no-op)
6. Update `TicketPackageTrack.delivery_status`
7. Return updated track

**Note**: delivery status transitions do NOT generate `TicketAuditEvent`
records. The delivery progress is tracked by the submission tracking
system (see `docs/features/packages/ibs-submission-tracking.md`). The
meaningful milestones for the ticket timeline are: (1) `track_status_changed`
when the track transitions to FIXED (triggered by release detection after
the RR is accepted and code is merged into the codestream), and
(2) `product_released` when the update reaches the product repository.
Delivery status is an intermediate signal — not a customer-facing event.

**TicketAuditEvent**: none.

**Idempotency**: no-op if delivery_status is unchanged.

---

### `set_product_released_at()`

Sets the `released_at` timestamp on a `TicketPackageProduct` record when
product release detection confirms the fix in the product's update
repository.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `product_id` | `UUID` | Yes | TicketPackageProduct to modify |
| `released_at` | `datetime` | Yes | Advisory issued date (UTC) |
| `advisory_id` | `str` | Yes | Advisory identifier (e.g., `SUSE-SU-2025:1234-1`) |

**Preconditions**:

- Parent ticket must be operable (`ensure_ticket_operable`) — release
  detection does NOT apply to non-operable tickets (Ignored or
  Duplicated)
- No precondition on track or product `deleted_at` — release detection
  applies to soft-deleted child records (factual observation that keeps
  them current with reality)

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Load the product record (no `deleted_at` filter — soft-deleted
   products are included)
3. If `released_at` is already set, return (no-op — release confirmation
   is irreversible; see below)
4. Set `TicketPackageProduct.released_at` to the provided value
5. Create `TicketAuditEvent` (`product_released`, `user_id = NULL`)
   with detail: `{"track": "...", "package": "...", "product_id": "...",
   "advisory_id": "..."}`
6. Call `reconcile_ticket_status()`
7. Return updated product

**TicketAuditEvent**: `product_released`

**Idempotency**: no-op if `released_at` is already set (step 3).

**Irreversibility**: once set, `released_at` cannot be cleared or
modified. An advisory present in `updateinfo.xml` is a factual
observation — it cannot be "un-published". If an advisory is
misidentified (wrong source package match), the correct resolution is
to soft-delete the product record, not to clear `released_at`.

**Callers**: IBS product release detection tasks only
(`acting_user_id` is always `None`; auto-assignment does not apply).

---

### `set_product_eligibility()`

Sets or resets the eligibility override of a `TicketPackageProduct` record.

**Parameters**:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | `AsyncSession` | Yes | Database session |
| `product_id` | `UUID` | Yes | TicketPackageProduct to modify |
| `eligible` | `bool \| None` | Yes | New eligibility value (`true`/`false` for override, `None` to reset to automatic calculation) |
| `acting_user_id` | `UUID \| None` | No | Who is performing the action |

**Preconditions**:

- Parent ticket must be operable (`ensure_ticket_operable`)
- Product must exist

**Behavior**:

If `eligible` is `bool` (override):

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. If `TicketPackageProduct.eligible == eligible` AND `is_eligible_override == true`, return (no-op)
5. Update `TicketPackageProduct.eligible` to the given value
6. Set `TicketPackageProduct.is_eligible_override = true`
7. Create `TicketAuditEvent` (`product_eligibility_changed`)
8. Call `reconcile_ticket_status()`
9. Return updated product

If `eligible` is `None` (reset to automatic):

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. If `is_eligible_override == false`, return (no-op — already automatic)
5. Set `TicketPackageProduct.is_eligible_override = false`
6. Recalculate eligibility using all automatic rules in
   `docs/features/packages/package-model.md` (Axis 2: Eligibility), including
   the Reactive Support rule and the threshold comparison based on
   `cvss.resolve_eligibility_score()`.
7. Update `TicketPackageProduct.eligible` to the calculated value
8. Create `TicketAuditEvent` (`product_eligibility_changed`)
9. Call `reconcile_ticket_status()`
10. Return updated product

> **Note**: Eligibility recalculation delegates to
> `cvss.resolve_eligibility_score()` (SUSE assessment of the default
> version only; fallback to 10.0 if no SUSE assessment exists). Since this
> requires only single-row database reads (CVE assessments + product
> threshold), it is acceptable inside the `FOR UPDATE` lock.

**TicketAuditEvent**: `product_eligibility_changed`

**Idempotency**:

- Override (`eligible` is `bool`): no-op if `eligible` matches current value AND `is_eligible_override` is already `true`
- Reset (`eligible` is `None`): no-op if `is_eligible_override` is already `false` (the current `eligible` value is already system-managed)

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
| `audit_comment` | `str \| None` | No | System-generated context for `package_added`; `NULL` for user actions |
| `active_ticket_only` | `bool` | No | When true, skip without mutation if the locked Ticket is not active; used by Product catalog backfill |

**Preconditions**:

- When `active_ticket_only` is false, the parent Ticket must be operable
  (`ensure_ticket_operable`).
- When `active_ticket_only` is true, only an active parent Ticket is eligible;
  an inactive locked Ticket returns a no-op before the operability guard.

**Behavior**:

1. Acquire `FOR UPDATE` on the Ticket row
2. If `active_ticket_only` is true and the locked Ticket status is not `New`,
   `Analysis`, or `Analyzed`, return a no-op result before assignment,
   reconciliation, or audit creation.
3. Call `ensure_ticket_operable(ticket)`
4. Validate preconditions and determine which package, track, and Product
   records are missing under the lock.
5. If no record is missing, return a no-op result before auto-assignment,
   reconciliation, or audit creation.
6. Call `auto_assign_actor()`
7. Create or skip `TicketPackage` (idempotent — skip if exists)
8. For each track in `tracks`:
   - Create or skip `TicketPackageTrack` (idempotent — skip if exists,
     including soft-deleted records)
   - If newly created, initial status: `ANALYSIS`, delivery_status:
     `PENDING`. An existing track retains both values unchanged.
   - For each product under the track:
     - Create or skip `TicketPackageProduct` (idempotent — skip if
       exists, including soft-deleted records)
      - Calculate initial eligibility (see Record Creation Logic below)

> **Note**: Eligibility calculation inside the `FOR UPDATE` lock is
> acceptable here. `CVECVSSAssessment` records are loaded once for the
> entire product batch (same CVE for all products in the ticket —
> typically fewer than 20 records). The `cvss_threshold` per product is a
> single-row lookup from the Product table. The `cvss.py` functions
> (`resolve_eligibility_score`) are pure and do not perform database
> access on their own. Therefore total I/O volume inside the lock remains
> within "fast reads (single-row lookups)" permitted by Transaction
> Hygiene Rules, even when creating dozens of products in a single
> `add_package_records()` call.

9. Create one `TicketAuditEvent` (`package_added`) using `audit_comment`.
10. Call `reconcile_ticket_status()`
11. Return created records

**TicketAuditEvent**: `package_added`

**Idempotency**: if a `TicketPackageTrack` or `TicketPackageProduct`
record already exists for the given combination (including soft-deleted
records), it is skipped without modification. Only missing records are
created. This ensures re-running `add_package_to_ticket` after a
partial failure does not produce duplicate records. A fully no-op invocation
does not auto-assign or reconcile the Ticket and creates no audit event.

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

- Parent ticket must be operable (`ensure_ticket_operable`)
- Package must exist and have `deleted_at IS NULL`

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. Set `package.deleted_at = now()`
5. Create `TicketAuditEvent` (`package_excluded`)
6. Call `reconcile_ticket_status()`
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

- Parent ticket must be operable (`ensure_ticket_operable`)
- Track must exist and have `deleted_at IS NULL`

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. Set `track.deleted_at = now()`
5. Create `TicketAuditEvent` (`track_excluded`)
6. Enforce package orphan rule (see Orphan Cleanup Invariants)
7. Call `reconcile_ticket_status()`
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

- Parent ticket must be operable (`ensure_ticket_operable`)
- Product must exist and have `deleted_at IS NULL`
- Parent track must have `deleted_at IS NULL`

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. Set `product.deleted_at = now()`
5. Create `TicketAuditEvent` (`product_excluded`)
6. Enforce track orphan rule (see Orphan Cleanup Invariants)
7. Call `reconcile_ticket_status()`
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

- Parent ticket must be operable (`ensure_ticket_operable`)
- Package must exist and have `deleted_at IS NOT NULL`
- At least one `TicketPackageTrack` under this package must have
  `deleted_at IS NULL`, AND that track must have at least one
  `TicketPackageProduct` with `deleted_at IS NULL`. If not satisfied,
  raise application error corresponding to `422 PACKAGE_RESTORE_BLOCKED`
  (see `package-model.md`)

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. Clear `package.deleted_at`
5. Create `TicketAuditEvent` (`package_restored`)
6. Call `reconcile_ticket_status()`
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

- Parent ticket must be operable (`ensure_ticket_operable`)
- Track must exist and have `deleted_at IS NOT NULL`
- Parent package must have `deleted_at IS NULL`
- At least one `TicketPackageProduct` under this track must have
  `deleted_at IS NULL`. If not satisfied, raise application error
  corresponding to `422 PACKAGE_RESTORE_BLOCKED` (see `package-model.md`)

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. Clear `track.deleted_at`
5. Create `TicketAuditEvent` (`track_restored`)
6. Call `reconcile_ticket_status()`
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

- Parent ticket must be operable (`ensure_ticket_operable`)
- Product must exist and have `deleted_at IS NOT NULL`
- Parent track must have `deleted_at IS NULL`

No child-existence pre-check required (product is a leaf record).

**Behavior**:

1. Acquire `FOR UPDATE` on the parent Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Call `auto_assign_actor()`
4. Validate preconditions
4. Clear `product.deleted_at`
5. Create `TicketAuditEvent` (`product_restored`)
6. Call `reconcile_ticket_status()`
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
    audit_comment: str | None = None,
    active_ticket_only: bool = False,
) -> AddPackageResult:
```

**Behavior**:

1. Query the SMELT v2 maintained-package endpoint to resolve all currently
   maintained tracks and products for the given package name (external I/O —
   no lock held). After the complete response is retrieved and validated,
   verify that a complete Product catalog snapshot exists; if none exists,
   raise `ProductCatalogNotReadyError` before matching Product CPEs or
   mutating ticket data. Error precedence is defined in `product-catalog.md`
   (Catalog Readiness and Freshness). Match returned product CPEs directly
   against local `Product.cpe` before the Ticket lock is acquired. Ignore
   CPEs with no local match, apply the compose-over-channel deduplication
   rule, and determine `workflow_type` from `product_definition.type` as
   specified in `package-model.md` (SMELT Query for Package Resolution). If
   resolution is partial, emit the required structured WARNING before
   mutation.
2. If SMELT is unreachable, returns a non-200 HTTP status, or returns a
   response that fails the v2 envelope or entry validation contract,
   raise an application error corresponding to `503 SMELT_UNAVAILABLE`.
   No records are created.
3. If SMELT returns a package-not-found error (`status = "error"`), raise an
   application error corresponding to `422 PACKAGE_NOT_FOUND_IN_SMELT`.
   No records are created.
4. If SMELT returned entries but no Product CPE resolves to a local Product
   across the complete response, raise `PackageTargetsUnresolvedError`. No
   records are created.
6. Delegate all record creation to `add_package_records()` — this is where
   the `FOR UPDATE` lock is acquired.
7. If step 6 created at least one package, track, or Product record, register
   the following best-effort post-commit effects with the workflow owner:
   resolve and cache the IBS bugowner, then enqueue
   `discover_submissions_for_ticket_package()` for retroactive SR/RR
   discovery. A fully no-op or `active_ticket_only` skip registers no effects.
   Neither effect executes before the database commit succeeds.
8. Return an `AddPackageResult` with creation/skip counts.

`audit_comment` is internal system context for `package_added`. API callers
always pass `NULL`. Product catalog backfill passes
`Product catalog backfill`. Other automatic callers pass the contextual
comment defined by their owning workflow.

`active_ticket_only` is false for normal API and automatic callers. Product
catalog backfill sets it to true so a Ticket that became inactive after
batch selection is skipped under the Ticket row lock.

**Error handling**:

- **Steps 1–4 (validation gate)**: blocking. If any of these steps fails,
  the function raises without side effects (no database writes occur). The
  endpoint handler translates service-layer exceptions to the corresponding
  HTTP error codes defined in `package-model.md`.
- **Steps 5–6 (record creation)**: transactional. Record creation occurs
  under the `FOR UPDATE` lock acquired by `add_package_records()`. If any
  failure occurs during these steps, the transaction is rolled back and no
  records are persisted.
- **Step 7 (post-commit effects)**: best-effort. The API transaction
  dependency or other workflow owner executes these effects only after its
  caller-owned transaction commits. Failures do not roll back the created
  records.
  - **Bugowner resolution**: if bugowner resolution fails (IBS
    unreachable, API error, timeout), log a warning and continue. The
    package addition is not rolled back. See
    `docs/features/packages/package-bugowner.md`: "Package addition to the
    ticket MUST NOT fail due to a bugowner resolution failure."
  - **Submission discovery enqueue**: if the task enqueue fails
    (e.g., Redis unavailable), log a warning and continue. The periodic
    `SyncIbsRequests` (catch-up every 24h at 02:30 UTC) ensures
    eventual consistency.

**Auto-assignment**: applied by `add_package_records()` after it confirms that
at least one record is missing. `add_package_to_ticket()` does not apply it.

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
    sort_by: Literal["package_name", "created_at"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    page: int = 1,
    per_page: int = 20,
) -> PaginatedResult[PackageListItem]:
```

**Behavior**:

1. Build base query joining `TicketPackage` -> `Ticket`
2. Exclude soft-deleted packages: filter `TicketPackage.deleted_at IS NULL`
3. Apply `confidentiality_filter` (pre-built by the endpoint handler
   via `confidential_ticket_filter()` — see
   `docs/features/tickets/tickets.md`, Confidentiality Filtering)
4. Apply `ticket_status` filter (if provided; invalid values silently
   ignored)
5. Apply `search` (ILIKE `%term%` substring match on `package_name`) or
   `name` (exact match)
6. Apply sorting (`sort_by`/`sort_order`; deterministic tiebreaker per
   `docs/api-spec.md`, Deterministic Pagination Ordering)
7. Execute paginated query
8. Compute `track_summary` via SQL aggregation (`COUNT(*) FILTER (WHERE
   status = ...)`) in the same query — NOT as Python post-processing —
   to avoid N+1 query patterns. Counts only tracks with
   `deleted_at IS NULL` (active tracks)
9. Return paginated `PackageListItem[]`

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

### Chain composition

The invariants compose naturally. Soft-deleting a product may trigger
the track orphan rule, which may trigger the package orphan rule:

```
soft_delete_ticket_package_product(record, user)
  -> TicketAuditEvent (product_excluded)
  -> _enforce_track_orphan_rule()
      -> if 0 directly-active products:
          set track.deleted_at (direct)
          -> TicketAuditEvent (track_excluded, user_id=NULL)
          -> _enforce_package_orphan_rule()
              -> if 0 directly-active tracks:
                  set package.deleted_at (direct)
                  -> TicketAuditEvent (package_excluded, user_id=NULL)
  -> reconcile_ticket_status()   # once, after entire chain completes
```

> `reconcile_ticket_status()` is called once after the entire chain
> completes — not at each intermediate level. The function is idempotent
> and queries the current state of all active records, so only the final
> invocation after all soft-deletions have been applied produces the
> correct result. Calling it at intermediate levels would produce
> redundant evaluations whose results are immediately overwritten.

Orphan-triggered soft-deletions create `TicketAuditEvent` records with
`user_id = NULL` (system action), distinguishing them from VA-initiated
exclusions. Each orphan soft-deletion sets `deleted_at` only on the
parent — no chain to children (per the hierarchical exclusion model).

## Record Creation Logic

When `package_service` creates a new `TicketPackageTrack` record, the
initial status is always `ANALYSIS` and `delivery_status` is `PENDING`.

When it creates a new `TicketPackageProduct` record, eligibility is
calculated at creation time. See `docs/features/packages/package-model.md`
([Axis 2: Eligibility](package-model.md#axis-2-eligibility-per-product-only))
for the computation rules. Products do not have their own status — they
inherit affectedness implicitly from the parent track.

This logic is internal to `package_service` — callers (including
`add_package_to_ticket`) do not specify initial values.

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

All exceptions raised by `package_service` inherit from
`PackageServiceError`. API endpoint handlers catch
`PackageServiceError` subclasses and map them to the corresponding HTTP
status code and error code per `api-spec.md`.

### API-facing exceptions

Caught by endpoint handlers and mapped to HTTP responses:

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `TicketNotFoundError` † | 404 | `TICKET_NOT_FOUND` | `FOR UPDATE` returns no row |
| `TicketNotMutableError` † | 409 | `TICKET_NOT_MUTABLE` | Ticket is in manual zone (defense in depth — API layer catches first) |
| `TrackNotFoundError` | 404 | `RESOURCE_NOT_FOUND` | Track ID does not exist |
| `ProductNotFoundError` | 404 | `RESOURCE_NOT_FOUND` | Product ID does not exist |
| `PackageNotFoundError` | 404 | `RESOURCE_NOT_FOUND` | Package ID does not exist |
| `PackageAlreadyExcludedError` | 409 | `PACKAGE_ALREADY_EXCLUDED` | Soft-delete on record with `deleted_at IS NOT NULL` |
| `PackageNotExcludedError` | 422 | `PACKAGE_NOT_EXCLUDED` | Restore on record with `deleted_at IS NULL` |
| `PackageRestoreBlockedError` | 422 | `PACKAGE_RESTORE_BLOCKED` | Restore precondition not met (no valid child chain) |
| `SmeltUnavailableError` | 503 | `SMELT_UNAVAILABLE` | SMELT does not produce a valid successful response |
| `ProductCatalogNotReadyError` | 503 | `PRODUCT_CATALOG_NOT_READY` | No complete SMELT Product catalog snapshot has committed |
| `PackageNotFoundInSmeltError` | 422 | `PACKAGE_NOT_FOUND_IN_SMELT` | SMELT returns zero tracks |
| `PackageTargetsUnresolvedError` | 422 | `PACKAGE_TARGETS_UNRESOLVED` | SMELT returns tracks but no target resolves through the current Product catalog snapshot |
| `TrackFixedStatusRestrictedError` | 403 | `AUTH_INSUFFICIENT_PERMISSION` | VA attempts `status=FIXED` without force |

† Shared exception — inherits from `ServiceError`, not from
`PackageServiceError`. Handlers must catch it explicitly.

### System-internal exceptions

Handled by system callers directly (not mapped to HTTP responses):

| Exception | Raised when | Handling |
|-----------|-------------|----------|
| `InvalidDeliveryStatusTransition` | Illegal delivery status transition (e.g., regression from `RELEASED`) | Caller logs warning and continues (`SyncIbsRequests`) or avoids via pre-check (`IBSEventConsumer`) |

## Soft-Deleted Records and Mutations

This section distinguishes two distinct levels of soft-deletion that have
different semantics:

### Ticket-level operability

Non-operable tickets (Ignored or Duplicated) MUST NOT
receive any package mutations. `ensure_ticket_operable(ticket)` enforces
this for all mutation functions in this module (including
`set_product_released_at`). Automated callers (release detection
fetchers, IBS RabbitMQ consumer) scope their queries to active tickets
at query time (a stricter subset — excludes Resolved in addition to
non-operable statuses); the guard fires only in race conditions.
Required caller behavior: catch `TicketNotMutableError`, log a
WARNING, and continue processing the next item.

### Package/track/product-level soft-deletion

Soft-deleted packages, tracks, and products on **operable tickets**
continue to receive updates from all automated processes (release
detection, eligibility recalculation, delivery status changes). The
`deleted_at` field on child records controls only **exclusion from
decision-making** (gate evaluation, anomaly detection, resolution
logic) — not from mutations.

Mutation functions (`set_track_status`, `set_track_delivery_status`,
`set_product_eligibility`, `set_product_released_at`) do NOT require
child-record `deleted_at IS NULL` as a precondition. This ensures that
soft-deleted records remain current with reality, enabling accurate
re-evaluation if the record is later restored.

Restore functions (`restore_ticket_package_track`,
`restore_ticket_package_product`) DO require that all ancestor records
(parent package for tracks, parent package and parent track for
products) have `deleted_at IS NULL`. Restoring a record whose ancestor
is still excluded would leave it effectively excluded with no observable
effect.

## Architectural Test Requirement

A parametrized integration test MUST be implemented to verify that the
`package_service` mutation functions correctly trigger
`reconcile_ticket_status` and produce the expected ticket status
transitions. The test must cover:

- **Forward transitions**: package mutations causing ticket advancement
  (e.g., setting all tracks to final status triggers Analyzed -> Resolved;
  or an AFFECTED track becoming resolution-complete because all its
  products become ineligible also triggers Analyzed -> Resolved)
- **Backward transitions**: package mutations breaking gate conditions
  (e.g., restoring a soft-deleted track with non-final status)
- **Orphan chain**: soft-deleting the last product triggers track
  deletion, then package deletion, with correct audit events at each
  level
- **Auto-assignment**: mutations on unassigned tickets trigger assignment
  to the acting VA

## Cross-references

- `docs/features/tickets/ticket-mutations.md` — `reconcile_ticket_status()`,
  `auto_assign_actor()`, `ensure_ticket_operable()`, ticket-centric mutations
- `docs/features/tickets/tickets.md` — ticket lifecycle, gate
  conditions, confidentiality filtering (`confidential_ticket_filter()`)
- `docs/features/tickets/ticket-audit-log.md` — event type contract
- `docs/features/packages/product-catalog.md` — current repository mappings
  and Product catalog backfill
- `docs/features/tickets/cvss-scoring.md` — CVSS resolution cascade,
  eligibility threshold comparison
- `docs/features/packages/package-model.md` — track/product concepts,
  hierarchical exclusion model, API endpoints
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
