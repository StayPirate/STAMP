# Ticket Service

## Purpose

Centralize non-gate ticket lifecycle operations — creation, CVE
association, assignment, manual-zone entries, and confidentiality
management — in a single service module
(`ticket_service`). This ensures that:

- `FOR UPDATE` locking is consistently applied on the Ticket row
- `TicketAuditEvent` records are always created atomically
- `reconcile_ticket_status()` is called when operations have side effects
  on gate conditions (severity changes, status reconciliation)
- `auto_assign_actor()` is applied uniformly for unassigned tickets
- `ensure_ticket_operable()` enforces mutability regardless of entry
  point
- Business rules (idempotency) are enforced regardless of entry point

Gate-relevant mutations (CVSS assessments, severity overrides,
manual-zone exits) are handled by `ticket_mutations`
(`docs/features/tickets/ticket-mutations.md`). Package-centric mutations
are handled by `package_service`
(`docs/features/packages/package-service.md`).

## Architecture

### Module location

`backend/app/services/ticket_service.py`

### Async pattern

The service is implemented as async functions. The API (FastAPI) is the
primary consumer and calls the service directly with `await`. Entry
points that operate in a synchronous context (Celery tasks) call the
service via `asyncio.run()`.

| Entry point            | Invocation pattern                                          |
|------------------------|-------------------------------------------------------------|
| API endpoint           | `await ticket_service.assign_ticket(session, ...)`          |
| cve_service (async)    | `await ticket_service.create_ticket(session, ...)`          |
| Celery task (release detection) | `asyncio.run(ticket_service.create_ticket(session, ...))` |

### Transaction ownership

The module does NOT commit or roll back. All operations execute within
the caller's database session. Commit responsibility belongs to the
caller.

This matches the `ticket_mutations`, `package_service`, and
`user_service` pattern — the module applies mutations and creates audit
events, but the transaction boundary is the caller's decision.

**Exception — flattening operations**: `mark_as_duplicate` has a flattening
phase that updates other tickets' `duplicate_of_id`. The flattening is
executed by a dedicated service function `execute_duplicate_flattening`
which accepts a `db_session_factory` and opens/commits independent
sessions internally. This is the only function in the module that
commits transactions — it is an explicit, limited exception to the
"module does not commit" rule, justified by the requirement for
independent per-ticket transactions (see `tickets.md` and
`ticket_mutations.md` single-ticket scope rule). The caller invokes
`execute_duplicate_flattening` after committing the primary transaction.
See `mark_as_duplicate` and `execute_duplicate_flattening` for details.

### Acting user convention

All mutation operations accept an `acting_user_id: UUID | None`
parameter:

- `UUID` — action performed by an authenticated user. Enables
  auto-assignment on unassigned tickets if the user holds the
  `vulnerability_analyst` role
- `None` — system action (CVE ingestion, release detection). Auto-
  assignment does not apply

**API handler rule**: API endpoint handlers MUST always pass the UUID of
the authenticated user as `acting_user_id`. Passing `None` from an API
handler is a bug — it would silently bypass auto-assignment. `None` is
reserved exclusively for system entry points.

**Exception**: `grant_access` and `revoke_access` accept
`acting_user_id: UUID` (non-optional) because these operations are
inherently user-initiated — there is no system scenario for granting or
revoking explicit access.

### Relationship with other modules

| Module | Relationship |
|--------|-------------|
| `services/ticket_mutations.py` | `ticket_service` imports `reconcile_ticket_status()`, `recalculate_cvss_chain()`, `auto_assign_actor()`, `ensure_ticket_operable()`, and `resolve_canonical_target()` from `ticket_mutations`. The dependency is unidirectional: `ticket_service` → `ticket_mutations`. Neither module imports from the other in the reverse direction |
| `services/package_service.py` | No direct dependency. Both modules independently depend on `ticket_mutations` for status evaluation |
| `services/cvss.py` | No direct dependency. CVSS resolution is triggered indirectly via `reconcile_ticket_status()` |

## Scope Boundary

The following ticket mutation endpoints route to `ticket_mutations`,
not `ticket_service`, because they are gate-relevant or manual-zone
exit operations:

| Endpoint | Function | Reason |
|----------|----------|--------|
| `PATCH .../severity` | `set_severity_override()` | Gate-relevant (severity affects Analyzed gate #3) |
| `POST .../reopen` | `reopen_from_ignored()` | Manual-zone exit (re-enters gate zone via `_reenter_gate_zone()`) |
| `POST .../revert-duplicate` | `revert_duplicate()` | Manual-zone exit (re-enters gate zone via `_reenter_gate_zone()`) |

See [ticket-mutations.md](ticket-mutations.md) for these operations'
contracts.

### Operability guard

Operations that modify the Ticket row (all mutation functions except
`create_ticket`) call `ensure_ticket_operable(ticket)` from
`ticket_mutations` after acquiring `FOR UPDATE`. This checks:

1. **Mutability guard**: status ∈ {Ignored, Duplicated} →
   `TicketNotMutableError`

Explicit opt-outs (functions that do NOT call `ensure_ticket_operable`):

- `ignore_ticket` — calls `ensure_ticket_operable` (which catches
  Ignored/Duplicated), then applies its own status check (New/Analysis
  required). See ordering constraint below

**Ordering constraint for `ignore_ticket`**:
`ensure_ticket_operable` executes first. For Ignored/Duplicated tickets
it raises `TicketNotMutableError` before the function's own status check
fires. For other non-valid statuses (Analyzed, Resolved), the function's
own check raises `InvalidTransitionError`. This ordering is contractual.

### Concurrency control

All mutation operations that modify the Ticket row follow the
pessimistic locking pattern defined in `docs/conventions.md`
(Transaction and Locking) and extended by `ticket-mutations.md`
(Concurrency Control). Every such operation acquires `FOR UPDATE` on the
Ticket row as its first database operation.

The single exception is `create_ticket()`, which performs an INSERT —
no row-level lock is needed. The CVE uniqueness constraint is enforced
at the database level; concurrent INSERTs for the same CVE are handled
via `IntegrityError` catch-and-map (see `create_ticket` behavioral
steps).

## Ticket Lifecycle Operations

### create_ticket

Creates a new ticket. Optionally associates a CVE and sets initial
status based on the creating user's role.

```python
async def create_ticket(
    db: AsyncSession,
    *,
    acting_user_id: UUID | None,
    cve_id: str | None = None,
    severity_override: Severity | None = None,
    is_confidential: bool = False,
    source: TicketCreationSource,
) -> Ticket:
```

`TicketCreationSource` is a service-layer-only Python enum (not a
database column — it is never persisted). Values: `manual`,
`cve_ingestion`, `release_detection`. It determines the audit event
comment (e.g., "Ticket created manually", "CVE ingested from NVD",
"CVE fix detected in codestream"). Defined in
`backend/app/services/ticket_service.py`.

**Preconditions**:

- If `cve_id` is provided: CVE Resolution Behavior applies (on-demand
  fetch if unknown, conflict check if already associated with another
  ticket)
- If `is_confidential` is True: the acting user must hold
  `manage_confidentiality` capability (enforced at the API layer)
- If both `cve_id` and `severity_override` are provided: the service
  raises `SeverityDerivedError`. When a CVE is associated, severity is
  derived exclusively from CVSS assessments — manual override is not
  applicable. UI implementations should disable the severity field when
  a CVE is provided at creation time

**Behavioral steps**:

1. If `cve_id` provided: resolve CVE via CVE Resolution Behavior
2. INSERT new Ticket row with initial fields (all unspecified columns
   use database defaults: `duplicate_of_id = NULL`,
   `updated_at = now(UTC)`, etc.)
3. Determine initial status:
   - If `acting_user_id` is not None AND user holds VA role:
     `status = Analysis`, `assignee_id = acting_user_id`
   - Otherwise: `status = New`
4. Create `TicketAuditEvent` (`ticket_created`, comment from `source`)
5. If `severity_override` provided: create `TicketAuditEvent`
   (`severity_changed`, `old_value = NULL`, `new_value = <override>`)
6. If assigned (step 3): create `TicketAuditEvent` (`assignment`)
7. If CVE associated: create `TicketAuditEvent` (`cve_associated`)
8. Return the created Ticket

**Concurrency — CVE uniqueness**: If the INSERT raises an
`IntegrityError` due to the UNIQUE constraint on `Ticket.cve_id` (race
between concurrent creation for the same CVE), the service catches the
exception and raises `TicketCVEConflictError`. The API handler maps this
to `409 TICKET_CVE_CONFLICT`.

**Locking**: None (INSERT).

**reconcile_ticket_status**: Not called — initial status is determined by
fixed rules, and the ticket cannot have packages at creation time.

**Audit events**: Up to 4 (`ticket_created`, `severity_changed`,
`assignment`, `cve_associated`).

### associate_cve

Associates a CVE with a ticket that does not yet have one.

```python
async def associate_cve(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    cve_id: str,
    acting_user_id: UUID | None,
) -> Ticket:
```

**Preconditions**:

- Ticket must be operable (`ensure_ticket_operable`)
- Ticket must have `cve_id IS NULL` (else `TicketCVEAlreadySetError`)
- CVE Resolution Behavior applies (on-demand fetch, conflict check)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Verify `ticket.cve_id IS NULL` (else `TicketCVEAlreadySetError`)
4. Resolve CVE via CVE Resolution Behavior (may raise
    `TicketCVEConflictError` if CVE is already associated with another
    ticket)
5. `auto_assign_actor(ticket, acting_user_id)`
6. Set `ticket.cve_id`
7. Create `TicketAuditEvent` (`cve_associated`)
8. Call `recalculate_cvss_chain(ticket_id,
    acting_user_id=acting_user_id)` — reads `default_cvss_version`
    internally, recalculates severity (switching from
    `severity_override` to CVSS-cascade-derived) and product eligibility
    using the CVE's existing assessments, then calls
    `reconcile_ticket_status()` internally. Gate #3 (severity set) and
    gate #4 (SUSE CVSS provided) may now fail, causing regression to
    Analysis
9. Return updated Ticket

**Locking**: FOR UPDATE on Ticket row. Step 4 (CVE Resolution Behavior)
executes entirely within the locked transaction but involves only local
database operations: a `SELECT` on the CVE table and possibly an `INSERT`
of a minimal CVE record via `ensure_cve_exists()`. No synchronous
external HTTP calls or Redis/Celery operations occur while the lock is
held. `recalculate_cvss_chain()` (step 8) re-acquires `FOR UPDATE` on
the same row within the same transaction (PostgreSQL same-transaction
re-lock, no-op). Task dispatch via `trigger_on_demand_fetch()` is the
endpoint handler's responsibility and MUST occur after `db.commit()`,
outside the locked transaction.

**recalculate_cvss_chain**: YES — associating a CVE changes the severity
resolution source. `recalculate_cvss_chain()` recalculates severity via
`resolve_severity_score()` (5-step cascade using the CVE's existing
assessments) and product eligibility via `resolve_eligibility_score()`
(SUSE-only, 2-step). If the CVE has no assessments (e.g., MITRE-sourced
CVE with no CVSS data), severity resolves to `null` (gate #3 fails) and
eligibility remains at the 10.0 conservative fallback — the chain is
effectively a no-op for eligibility in this case. The final
`reconcile_ticket_status()` call (chain step 7) evaluates gates and may
regress the ticket to Analysis.

**Note on pre-existing CVSS assessments**: If the CVE being associated
already has `CVECVSSAssessment` records (e.g., from a prior NVD sync), these
assessments are immediately available to the CVSS resolution cascade.
`recalculate_cvss_chain()` uses them to derive severity and recalculate
product eligibility without requiring a fresh NVD fetch.

**Audit events**: `cve_associated`. Possibly `assignment` (from
auto-assign). Possibly `severity_changed`, `product_eligibility_changed`
(from recalculate chain). Possibly `status_change` (from reconcile).

### assign_ticket

Assigns or reassigns a ticket to a user.

```python
async def assign_ticket(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    assignee_id: UUID,
    acting_user_id: UUID | None,
) -> Ticket:
```

**Preconditions**:

- Ticket must be operable (`ensure_ticket_operable`)
- Target user must be active (else `AssigneeInactiveError`) and hold
  the `vulnerability_analyst` role (else `AssigneeNotVAError`)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Validate target user (active — else `AssigneeInactiveError`;
    holds VA role — else `AssigneeNotVAError`)
4. **Idempotency check**: if `ticket.assignee_id == assignee_id`, return
    ticket unchanged (no audit event, no status evaluation)
5. Set `ticket.assignee_id = assignee_id`
6. Create `TicketAuditEvent` (`assignment`)
7. If `ticket.status == New`: set `ticket.status = Analysis`, create
    `TicketAuditEvent` (`status_change`, `user_id = NULL`,
    `old_value = "New"`, `new_value = "Analysis"`) — this is the explicit
    `New → Analysis` transition (see Architectural Invariant in
    `tickets.md`); the `status_change` event is created here, not by
    `reconcile_ticket_status`
8. Call `reconcile_ticket_status(ticket)` — evaluates further promotion
    from `Analysis` upward; may produce a second `status_change` event
    if `Analyzed` or `Resolved` gate conditions are already satisfied
9. Return updated Ticket

**Locking**: FOR UPDATE on Ticket row.

**reconcile_ticket_status**: YES — evaluates whether the ticket's
existing data satisfies gates above `Analysis` (Analyzed or Resolved).
While `ticket-mutations.md` classifies assignment as "not gate-relevant"
in the sense that it does not modify CVSS/severity/package data, the
explicit `New → Analysis` transition in step 7 means the ticket is now
in the gate zone and `reconcile_ticket_status` can promote it further
if conditions are met.

**Audit events**: `assignment` (only if assignee actually changes).
Possibly `status_change` (explicit `New → Analysis` in step 7 and/or
further promotion from `reconcile_ticket_status`).

**auto_assign_actor**: Not called — this operation performs an explicit
assignment to a specified user, which supersedes implicit
auto-assignment of the acting user.

### ignore_ticket

Transitions a ticket to Ignored status (manual-zone entry).

```python
async def ignore_ticket(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    acting_user_id: UUID | None,
) -> Ticket:
```

**Preconditions**:

- Ticket must be operable (`ensure_ticket_operable`) — catches
  Ignored and Duplicated tickets before the status check
- Ticket must be in New or Analysis status (only valid source states;
  else `InvalidTransitionError`)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Call `ensure_ticket_operable(ticket)` — rejects Ignored or Duplicated
   (`TicketNotMutableError`) tickets
3. Verify status is New or Analysis (else `InvalidTransitionError` —
   this catches Analyzed and Resolved, which pass `ensure_ticket_operable`
   but are not valid source states for ignore)
4. `auto_assign_actor(ticket, acting_user_id)`
5. Set `ticket.status = Ignored`
6. Create `TicketAuditEvent` (`status_change`)
7. Return updated Ticket

**Locking**: FOR UPDATE on Ticket row.

**reconcile_ticket_status**: NOT called — this is a direct transition
into the manual zone. `reconcile_ticket_status` never operates on
Ignored tickets.

**Audit events**: `status_change`. Possibly `assignment` (from
auto-assign).

### mark_as_duplicate

Marks a ticket as a duplicate of another ticket.

```python
async def mark_as_duplicate(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    duplicate_of_id: UUID,
    acting_user_id: UUID | None,
) -> MarkAsDuplicateResult:
```

Returns a `MarkAsDuplicateResult` containing the updated ticket and
a list of ticket IDs requiring flattening updates. The caller passes
`flattening_ticket_ids` to `execute_duplicate_flattening` after committing
the primary transaction (see below).

```python
@dataclass
class MarkAsDuplicateResult:
    ticket: Ticket
    flattening_ticket_ids: list[UUID]
```

This is a service-layer-only dataclass defined in
`backend/app/services/ticket_service.py` (not a Pydantic schema or
database model).

**Preconditions**:

- Ticket must be operable (`ensure_ticket_operable`) — rejects
  Ignored and Duplicated tickets
- Target ticket must exist (else
  `TicketNotFoundError`)
- Target ticket must be accessible to the acting user (API-layer scope
  check). If the target ticket is confidential and the acting user's
  scope is `non_confidential`, the endpoint returns `404
  TicketNotFoundError` (to avoid confirming the existence of the
  confidential ticket). This is an API-layer constraint — the service
  function itself does not enforce scope filtering
- Target is resolved through `ticket_mutations.resolve_canonical_target()`
  to follow the duplicate chain to its end (may raise
  `DuplicateChainDepthError` if chain exceeds 50 hops)
- Circular reference check: resolved target must not be the ticket itself
  (else `SelfDuplicateError`)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row (single ticket scope)
2. Call `ensure_ticket_operable(ticket)`
3. Resolve canonical target via
   `ticket_mutations.resolve_canonical_target()`
4. Verify no circular reference (else `SelfDuplicateError`)
5. `auto_assign_actor(ticket, acting_user_id)`
6. Set `ticket.status = Duplicated`, `ticket.duplicate_of_id = resolved_target`
7. Create `TicketAuditEvent` (`duplicate_set`)
8. Query tickets where `duplicate_of_id` = this ticket — return their
    IDs as `flattening_ticket_ids`
9. Return `MarkAsDuplicateResult(ticket=ticket, flattening_ticket_ids=...)`

**Flattening orchestration** — handled by `execute_duplicate_flattening`
(see below). The caller's only responsibility is to invoke
`execute_duplicate_flattening(db_session_factory, flattening_ticket_ids,
canonical_target_id, acting_user_id)` after committing the primary
transaction. The endpoint handler pattern is:

```python
result = await mark_as_duplicate(db, ticket_id=..., ...)
await db.commit()
await execute_duplicate_flattening(
    session_factory, result.flattening_ticket_ids, resolved_target, acting_user_id
)
return result.ticket
```

**Locking**: FOR UPDATE on the source Ticket row only. Flattening
operations each acquire their own FOR UPDATE in independent transactions
managed by `execute_duplicate_flattening`.

**reconcile_ticket_status**: NOT called — this is a direct transition
into the manual zone (Duplicated).

**Audit events**: `duplicate_set`. Flattening produces
`duplicate_target_changed` events (in separate transactions managed by
`execute_duplicate_flattening`).

### execute_duplicate_flattening

Executes the flattening phase of `mark_as_duplicate`: updates
`duplicate_of_id` for all tickets that pointed to the just-duplicated
ticket.

```python
async def execute_duplicate_flattening(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    flattening_ticket_ids: list[UUID],
    canonical_target_id: UUID,
    acting_user_id: UUID | None,
) -> None:
```

This function opens and commits independent database sessions — it is
the sole exception to the "module does not commit" invariant (see
Transaction ownership above).

**Behavioral steps** (for each ticket ID in `flattening_ticket_ids`):

1. Open a new session via `db_session_factory()`
2. Acquire `FOR UPDATE` on the flattening ticket
3. Verify the ticket is still in Duplicated status and still points to
   the original ticket. If reverted concurrently, the function logs an
   informational message (ticket ID + observed state) and skips to the
   next ticket
4. Set `duplicate_of_id = canonical_target_id`
5. Create `TicketAuditEvent` (`duplicate_target_changed`)
6. Commit the session

**Best-effort semantics**: if an individual flattening step fails (e.g.,
database error on one ticket), the function logs the failure and
continues with the remaining tickets. This matches the "flattening is not
a correctness requirement" invariant from `tickets.md` — the canonical
resolver handles unflattened chains.

**Locking**: each flattening ticket is locked independently in its own
transaction. No multi-ticket locks are held simultaneously (as required
by `ticket_mutations.md` single-ticket scope rule).

## Ticket Reactivation

When a ticket transitions from an inactive status (Resolved, Ignored, or
Duplicated) back to an active status, the system executes two catch-up
mechanisms to reconcile the ticket's state with data that may have
changed during the inactive period:

1. **Synchronous — CVSS chain recalculation**: reconciles CVSS-derived
   data (severity, product eligibility) with the current
   `default_cvss_version` and any `CVECVSSAssessment` updates that
   occurred while the ticket was inactive. Automated CVSS sync scopes to
   active tickets — inactive tickets are excluded, so per-ticket CVSS
   data may be stale.

2. **Asynchronous — per-ticket fetcher catch-up**: catches up on
   external data not fetched during the inactive period (e.g., Red Hat
   CVSS updates — the `sync_redhat_cves` fetcher scopes to active
   tickets and skips inactive ones). See
   [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
   ("Per-Ticket Catch-Up: `catch_up()` Method") for the method contract.

Both mechanisms are handled internally by `reconcile_ticket_status()`
(step 4) when it detects an inactive-state exit. No post-commit action
is needed by endpoint handlers or callers. This applies to all three
inactive → active paths:

- `reopen_from_ignored()` — Ignored → active (via
  [ticket-mutations.md](ticket-mutations.md), `_reenter_gate_zone()`)
- `revert_duplicate()` — Duplicated → active (via
  [ticket-mutations.md](ticket-mutations.md), `_reenter_gate_zone()`)
- Gate-driven regression — Resolved → active (automatic, via any
  mutation that unsatisfies a gate)

### Convergence behavior

The ticket may transition rapidly as async tasks complete. For example,
if a release was detected while the ticket was inactive, the IBS
catch-up may set tracks to FIXED and products to released, causing the
ticket to reach Resolved shortly after reactivation. This is expected
behavior — the system converges to the accurate state.

### Cross-references

- [cvss-scoring.md](cvss-scoring.md) — CVSS resolution cascade,
  recalculation trigger rationale
- [ticket-mutations.md](ticket-mutations.md) —
  `reconcile_ticket_status()` step 4, `recalculate_cvss_chain()`
  contract, `_reenter_gate_zone()` helper
- [fetcher-infrastructure.md](../platform/fetcher-infrastructure.md) —
  `catch_up()` method contract

## Confidentiality Management

### set_confidentiality

Toggles the `is_confidential` flag on a ticket.

```python
async def set_confidentiality(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    is_confidential: bool,
    acting_user_id: UUID | None,
) -> Ticket:
```

**Preconditions**:

- Ticket must be operable (`ensure_ticket_operable`)
- Requires `manage_confidentiality` capability (enforced at API layer)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. **Idempotency check**: if `ticket.is_confidential == is_confidential`,
    return ticket unchanged (no audit event)
4. Set `ticket.is_confidential = is_confidential`
5. Create `TicketAuditEvent` (`confidentiality_changed`)
6. Return updated Ticket

**Note on access grants**: When setting `is_confidential = false`,
existing `TicketAccessGrant` records are NOT deleted immediately. They
become inert (the confidentiality filter no longer restricts access).
Stale grants are cleaned up by a periodic task (weekly, 14-day delay).
See `tickets.md` (Stale Access Grant Cleanup) for details.

**Locking**: FOR UPDATE on Ticket row.

**reconcile_ticket_status**: NOT called — confidentiality is not
gate-relevant.

**Audit events**: `confidentiality_changed` (only if value actually
changes).

### grant_access

Grants explicit access to a user on a confidential ticket.

```python
async def grant_access(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    target_user_id: UUID,
    acting_user_id: UUID,
) -> TicketAccessGrant:
```

**Preconditions**:

- Ticket must be operable (`ensure_ticket_operable`)
- Ticket must be confidential (`is_confidential = true`; else
  `TicketNotConfidentialError`)
- Target user must exist (else `UserNotFoundError`)
- Target user must be active (else `InactiveUserError`). Note: this
  check does not apply to `revoke_access` — revoking a grant from an
  inactive user is a legitimate cleanup operation
- Requires `manage_confidentiality` capability (enforced at API layer)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Verify ticket is confidential (else `TicketNotConfidentialError`)
4. Verify target user is active (else `InactiveUserError`)
5. **Idempotency check**: if grant already exists for this user, return
    existing grant unchanged (no audit event)
6. INSERT `TicketAccessGrant` record (`ticket_id`, `user_id`,
    `granted_by = acting_user_id`, `granted_at = now(UTC)`)
7. Create `TicketAuditEvent` (`access_grant_added`)
8. Return the created grant

**Concurrency**: If two concurrent requests attempt to grant access to
the same user, the UNIQUE constraint on `TicketAccessGrant`
(`ticket_id`, `user_id`) prevents duplicates. The service catches the
resulting `IntegrityError` and treats it as an idempotent success
(returns the existing grant, no audit event).

**Locking**: FOR UPDATE on Ticket row (provides immutability guard
consistency with other mutation functions).

**reconcile_ticket_status**: NOT called — access grants are not
gate-relevant.

**Audit events**: `access_grant_added` (only if grant is new).

### revoke_access

Revokes explicit access from a user on a confidential ticket.

```python
async def revoke_access(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    target_user_id: UUID,
    acting_user_id: UUID,
) -> None:
```

**Preconditions**:

- Ticket must be operable (`ensure_ticket_operable`)
- Ticket must be confidential (`is_confidential = true`; else
  `TicketNotConfidentialError`)
- Target user must exist (else `UserNotFoundError`)
- Requires `manage_confidentiality` capability (enforced at API layer)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Call `ensure_ticket_operable(ticket)`
3. Verify ticket is confidential (else `TicketNotConfidentialError`)
4. **Idempotency check**: if grant does not exist for this user, return
    without side effects (no audit event)
5. Delete `TicketAccessGrant` record
6. Create `TicketAuditEvent` (`access_grant_removed`)
7. Return

**Locking**: FOR UPDATE on Ticket row (provides immutability guard
consistency with other mutation functions).

**reconcile_ticket_status**: NOT called.

**Audit events**: `access_grant_removed` (only if grant existed).

### list_access_grants

Lists all users with explicit access grants for a confidential ticket.

```python
async def list_access_grants(
    db: AsyncSession,
    *,
    ticket_id: UUID,
) -> list[TicketAccessGrant]:
```

**Preconditions**:

- Ticket must be confidential (`is_confidential = true`; else
  `TicketNotConfidentialError`)
- Requires `manage_confidentiality` capability (enforced at API layer)

**Behavioral steps**:

1. Verify ticket is confidential (else `TicketNotConfidentialError`)
2. Query `TicketAccessGrant` records for the ticket, ordered by
   `granted_at` ascending
3. Return list

**Locking**: None (read-only).

**reconcile_ticket_status**: NOT called.

**Audit events**: None (read-only).

## Service Exceptions

All exceptions in this module inherit from `TicketServiceError`.
API endpoint handlers catch `TicketServiceError` subclasses and map them
to the corresponding HTTP status code and error code per `api-spec.md`.

| Exception | HTTP | Code | Raised when |
|-----------|------|------|-------------|
| `TicketNotFoundError` † | 404 | `TICKET_NOT_FOUND` | Ticket ID does not exist |
| `TicketNotMutableError` † | 409 | `TICKET_NOT_MUTABLE` | Ticket is in manual zone (Ignored or Duplicated) |
| `InvalidTransitionError` † | 409 | `TICKET_INVALID_TRANSITION` | Requested status transition is not allowed |
| `TicketCVEAlreadySetError` | 400 | `TICKET_CVE_ALREADY_SET` | Ticket already has a CVE associated |
| `TicketCVEConflictError` | 409 | `TICKET_CVE_CONFLICT` | CVE is already associated with another ticket |
| `AssigneeNotVAError` | 400 | `TICKET_ASSIGNEE_NOT_VA` | Target user lacks the vulnerability_analyst role |
| `AssigneeInactiveError` | 409 | `TICKET_ASSIGNEE_INACTIVE` | Target user is inactive (for assignment) |
| `InactiveUserError` † | 409 | `USER_INACTIVE` | Target user is inactive (for access grant) |
| `SelfDuplicateError` | 400 | `TICKET_SELF_DUPLICATE` | Ticket cannot be marked as duplicate of itself |
| `DuplicateCycleDetectedError` † | 409 | `TICKET_DUPLICATE_CYCLE_DETECTED` | Duplicate resolution would create a cycle |
| `DuplicateChainDepthError` † | 409 | `TICKET_DUPLICATE_CHAIN_DEPTH` | Duplicate chain exceeds maximum allowed depth |
| `SeverityDerivedError` † | 409 | `TICKET_SEVERITY_DERIVED` | Cannot manually set severity when it is auto-derived |
| `TicketNotConfidentialError` | 409 | `TICKET_NOT_CONFIDENTIAL` | Operation requires a confidential ticket |
| `UserNotFoundError` † | 404 | `USER_NOT_FOUND` | Referenced user does not exist |

† Shared exception — inherits from `ServiceError`, not from
`TicketServiceError`. Handlers must catch it explicitly.

## Callers

| Caller | Operations used |
|--------|----------------|
| API endpoint handlers (`api/v1/tickets.py`) | All 9 operations + `execute_duplicate_flattening` |
| CVE service (`services/cve_service.py`) | `create_ticket` (source=`cve_ingestion`) |
| IBS track release detection (`tasks/detect_ibs_track_releases.py`) | `create_ticket` (source=`release_detection`, Case C) |

**Note — ticket endpoints that route to `ticket_mutations` directly**:

| Endpoint | Function | Why not `ticket_service` |
|----------|----------|--------------------------|
| `PATCH .../severity` | `ticket_mutations.set_severity_override()` | Gate-relevant mutation |
| `POST .../reopen` | `ticket_mutations.reopen_from_ignored()` | Manual-zone exit |
| `POST .../revert-duplicate` | `ticket_mutations.revert_duplicate()` | Manual-zone exit |

These endpoints bypass `ticket_service` entirely — their handlers call
`ticket_mutations` functions directly. See the Scope Boundary section
above for the architectural rationale.

## Dependency Summary

```
ticket_mutations (infrastructure)
    ├── reconcile_ticket_status()
    ├── recalculate_cvss_chain()
    ├── auto_assign_actor()
    ├── ensure_ticket_operable()
    └── resolve_canonical_target()
         ▲                ▲
         │                │
  ticket_service    package_service
  (non-gate ops)    (package ops)
```

| ticket_service function | ensure_ticket_operable | reconcile_ticket_status | recalculate_cvss_chain | auto_assign_actor | resolve_canonical_target |
|------------------------|:----------------------:|:----------------------:|:---------------------:|:---------------------:|:------------------------:|
| create_ticket          | —                      | —                      | —                     | —                     | —                        |
| associate_cve          | ✓                      | (via chain)            | ✓                     | ✓                     | —                        |
| assign_ticket          | ✓                      | ✓                      | —                     | —                     | —                        |
| ignore_ticket          | ✓                      | —                      | —                     | ✓                     | —                        |
| mark_as_duplicate      | ✓                      | —                      | —                     | ✓                     | ✓                        |
| execute_duplicate_flattening | —                   | —                      | —                     | —                     | —                        |
| set_confidentiality    | ✓                      | —                      | —                     | —                     | —                        |
| grant_access           | ✓                      | —                      | —                     | —                     | —                        |
| revoke_access          | ✓                      | —                      | —                     | —                     | —                        |
| list_access_grants     | —                      | —                      | —                     | —                     | —                        |

## Architectural Test Requirement

The following integration tests MUST be implemented to verify correct
behavior of `ticket_service` operations:

1. **CVE association causes status regression**: create a ticket without
   CVE, set `severity_override`, add a package with tracks in final
   status to reach Analyzed. Associate a CVE → verify ticket regresses
   to Analysis (gate #3 and #4 fail)

2. **Assignment promotes New → Analysis explicitly**: create a ticket in
   New status. Assign a VA → verify ticket promotes to Analysis and a
   `status_change` event with `old_value = "New"`, `new_value = "Analysis"`,
   `user_id = NULL` is created (not by `reconcile_ticket_status` but by
   the explicit step in `assign_ticket` before calling reconcile)

3. **Assignment idempotency**: assign a ticket to user X, then assign
   again to user X → verify no audit event is created on the second call

7. **`New → Analysis` promotion coverage** (parametrized): every code
   path that sets `assignee_id` on a `New` ticket MUST produce a
   `status_change` event with `old_value = "New"` and
   `new_value = "Analysis"`. Paths to cover: `assign_ticket()` (explicit
   assignment) and `auto_assign_actor()` (triggered via any mutation
   function on an unassigned ticket, e.g., `set_severity_override`,
   `set_track_status`, `add_package_to_ticket`). This test guards against
   future code paths that set `assignee_id` without performing the
   `New → Analysis` transition.

4. **Mark-as-duplicate flattening with concurrent revert**: mark ticket B as
   duplicate of C (with ticket A pointing to B). Verify flattening updates
   A to point to C. Then revert A → verify A is no longer Duplicated

5. **CVE uniqueness race condition**: simulate concurrent `create_ticket`
   calls for the same CVE → verify one succeeds and the other raises
   `TicketCVEConflictError`

6. **grant_access concurrent requests**: simulate concurrent
   `grant_access` calls for the same user/ticket → verify one creates
   the grant and the other returns idempotent success

## Cross-references

- `docs/features/tickets/ticket-mutations.md` — gate-relevant mutations,
  `reconcile_ticket_status` contract, `ensure_ticket_operable` contract
- `docs/features/tickets/tickets.md` — ticket lifecycle, status gates,
  API endpoint definitions
- `docs/features/tickets/ticket-audit-log.md` — audit event types and
  contract
- `docs/features/tickets/tickets.md` — CVE Resolution Behavior (section
  "CVE Resolution Behavior")
- `docs/features/tickets/cve-service.md` — On-Demand Fetch: fetch_single_cve
- `docs/features/identity/rbac.md` — capability definitions
  (`triage_ticket`, `manage_confidentiality`,
  `create_ticket`)
- `docs/conventions.md` — Transaction and Locking pattern
- `docs/api-spec.md` — general API conventions, error code categories
