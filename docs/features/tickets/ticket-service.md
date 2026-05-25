# Ticket Service

## Purpose

Centralize non-gate ticket lifecycle operations — creation, CVE
management, assignment, manual-zone entries, soft-deletion, and
confidentiality management — in a single service module
(`ticket_service`). This ensures that:

- `FOR UPDATE` locking is consistently applied on the Ticket row
- `TicketAuditEvent` records are always created atomically
- `reconcile_ticket_status()` is called when operations have side effects
  on gate conditions (severity changes, status reconciliation)
- `auto_assign_actor()` is applied uniformly for unassigned tickets
- Business rules (immutability guards, idempotency) are enforced
  regardless of entry point

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
| Celery task (CVE sync) | `asyncio.run(ticket_service.create_ticket(session, ...))` |

### Transaction ownership

The module does NOT commit or roll back. All operations execute within
the caller's database session. Commit responsibility belongs to the
caller.

This matches the `ticket_mutations`, `package_service`, and
`user_service` pattern — the module applies mutations and creates audit
events, but the transaction boundary is the caller's decision.

**Exception — cascade operations**: `mark_as_duplicate` has a cascade
phase that updates other tickets' `duplicate_of_id`. Each cascade update
runs in its own database session (opened and committed by the caller or
endpoint handler after the primary transaction commits). The service
function returns the list of ticket IDs requiring cascade updates; the
caller is responsible for orchestrating the cascade transactions. See
`mark_as_duplicate` for details.

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
| `services/ticket_mutations.py` | `ticket_service` imports `reconcile_ticket_status()`, `auto_assign_actor()`, and `resolve_canonical_target()` from `ticket_mutations`. The dependency is unidirectional: `ticket_service` → `ticket_mutations`. Neither module imports from the other in the reverse direction |
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

### Immutability guard

Operations that modify the Ticket row (all mutation functions except
`create_ticket`) MUST reject tickets in Ignored or Duplicated status
with `TicketNotMutableError`, unless the operation is specifically
designed for those statuses (e.g., `restore_ticket` operates on any
soft-deleted ticket regardless of status; `soft_delete_ticket` operates
on any status). This guard is applied after acquiring `FOR UPDATE`.

Note: `mark_as_duplicate` does not use the immutability guard directly —
it has its own gate-zone status check that is semantically equivalent
(rejects Ignored and Duplicated tickets) but produces a more specific
error (`InvalidTransitionError`).

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
- If both `cve_id` and `severity_override` are provided:
  `severity_override` is stored but not used for severity resolution
  while the CVE is associated (severity is derived from CVSS). The
  override serves as a historical record of the VA's initial assessment
  and as a fallback if the CVE is later dissociated

**Behavioral steps**:

1. If `cve_id` provided: resolve CVE via CVE Resolution Behavior
2. INSERT new Ticket row with initial fields (all unspecified columns
   use database defaults: `duplicate_of_id = NULL`,
   `deleted_at = NULL`, `updated_at = now(UTC)`, etc.)
3. Determine initial status:
   - If `acting_user_id` is not None AND user holds VA role:
     `status = Analysis`, `assignee_id = acting_user_id`
   - Otherwise: `status = New`
4. Create `TicketAuditEvent` (`ticket_created`, comment from `source`)
5. If assigned (step 3): create `TicketAuditEvent` (`assignment`)
6. If CVE associated: create `TicketAuditEvent` (`cve_associated`)
7. Return the created Ticket

**Concurrency — CVE uniqueness**: If the INSERT raises an
`IntegrityError` due to the UNIQUE constraint on `Ticket.cve_id` (race
between concurrent creation for the same CVE), the service catches the
exception and raises `TicketCVEConflictError`. The API handler maps this
to `409 TICKET_CVE_CONFLICT`.

**Locking**: None (INSERT).

**reconcile_ticket_status**: Not called — initial status is determined by
fixed rules, and the ticket cannot have packages at creation time.

**Audit events**: Up to 3 (`ticket_created`, `assignment`,
`cve_associated`).

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

- Ticket must have `cve_id IS NULL` (else `TicketCVEAlreadySetError`)
- CVE Resolution Behavior applies (on-demand fetch, conflict check)
- Immutability guard applies

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Immutability guard check
3. Verify `ticket.cve_id IS NULL` (else `TicketCVEAlreadySetError`)
4. Resolve CVE via CVE Resolution Behavior (may raise
   `TicketCVEConflictError` if CVE is already associated with another
   ticket)
5. `auto_assign_actor(ticket, acting_user_id)`
6. Set `ticket.cve_id`
7. Create `TicketAuditEvent` (`cve_associated`)
8. Call `reconcile_ticket_status(ticket)` — severity source changes from
   `severity_override` to CVSS-derived; gate #3 (severity set) and
   gate #4 (SUSE CVSS provided) may now fail, causing regression
9. Return updated Ticket

**Locking**: FOR UPDATE on Ticket row.

**reconcile_ticket_status**: YES — associating a CVE changes the severity
resolution source. If the ticket was Analyzed without a CVE (using
`severity_override`), associating a CVE causes: (a) severity to become
CVSS-derived, which may be `None` until CVSS data arrives (gate #3
fails); (b) gate #4 (SUSE CVSS v3.1 + v4.0 required) to become
applicable and likely fail. The ticket may regress to Analysis.

**Audit events**: `cve_associated`. Possibly `assignment` (from
auto-assign). Possibly `status_change` (from evaluate).

### dissociate_cve

Removes the CVE association from a ticket.

```python
async def dissociate_cve(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    acting_user_id: UUID | None,
) -> Ticket:
```

**Preconditions**:

- Ticket must have `cve_id IS NOT NULL` (else `TicketCVENotSetError`)
- Requires `admin_ticket_ops` capability (enforced at API layer)
- Immutability guard applies

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Immutability guard check
3. Verify `ticket.cve_id IS NOT NULL` (else `TicketCVENotSetError`)
4. Delete all `CVECVSSAssessment` records where `cve_id` matches the CVE
   being dissociated
5. For each deleted assessment, create a `TicketAuditEvent`
   (`cvss_assessment_deleted`) — matching the per-record audit pattern
   used by `ticket_mutations.delete_cvss_assessment()`
6. Set `ticket.cve_id = NULL`
7. Create `TicketAuditEvent` (`cve_removed`)
8. Call `reconcile_ticket_status(ticket)` — severity falls back to
   `severity_override`; if that is also NULL, severity = None and
   gate #3 fails, causing regression. Without CVSS assessments, the
   conservative fallback (10.0) applies for eligibility threshold
   comparisons
9. Return updated Ticket

**Note on CVSS assessment cleanup**: Deleting CVSS assessments on CVE
dissociation prevents orphaned records and ensures that
`update_cvss_assessment()` / `delete_cvss_assessment()` in
`ticket_mutations` never encounter an assessment whose CVE has no parent
ticket. This deletion happens within the same transaction (inside the
`FOR UPDATE` lock) to maintain atomicity.

**Note on auto-assignment**: `auto_assign_actor` is NOT called.
CVE dissociation requires `admin_ticket_ops` capability, making it an
administrative correction rather than a triage operation. If the admin
also holds the VA role and the ticket is unassigned, the admin should
use the explicit assignment operation instead.

**Note on package records**: Existing `TicketPackageTrack` and
`TicketPackageProduct` records are preserved. Without an associated CVE,
automatic release detection ceases to function. The VA must manually
manage these records or re-associate a CVE. See `tickets.md`
(Dissociating a CVE) for full behavioral details.

**Locking**: FOR UPDATE on Ticket row.

**reconcile_ticket_status**: YES — removing a CVE changes severity
resolution. If `severity_override` is NULL, severity becomes None and
the Analyzed gate #3 fails.

**Audit events**: `cvss_assessment_deleted` (one per deleted assessment).
`cve_removed`. Possibly `status_change` (from evaluate).

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

- Target user must be active and hold the `vulnerability_analyst` role
  (else `InvalidAssigneeError`)
- Immutability guard applies

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Immutability guard check
3. Validate target user (active, holds VA role; else
   `InvalidAssigneeError`)
4. **Idempotency check**: if `ticket.assignee_id == assignee_id`, return
   ticket unchanged (no audit event, no status evaluation)
5. Set `ticket.assignee_id = assignee_id`
6. Create `TicketAuditEvent` (`assignment`)
7. Call `reconcile_ticket_status(ticket)` — assignment affects the
   Analysis gate (New → Analysis promotion when assignee is set)
8. Return updated Ticket

**Locking**: FOR UPDATE on Ticket row.

**reconcile_ticket_status**: YES — assignment satisfies a precondition
for Analysis status (a ticket in New with an assignee should promote to
Analysis). While `ticket-mutations.md` classifies assignment as "not
gate-relevant" in the sense that it does not modify CVSS/severity/
package data, it has an indirect gate effect: the Analysis gate requires
`assignee_id IS NOT NULL`.

**Audit events**: `assignment` (only if assignee actually changes).
Possibly `status_change` (from evaluate).

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

- Ticket must be in New or Analysis status (only valid source states;
  else `InvalidTransitionError`)
- Immutability guard does not apply here (by definition, Ignored tickets
  would fail it — the transition itself is the operation)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Verify status is New or Analysis (else `InvalidTransitionError`)
3. `auto_assign_actor(ticket, acting_user_id)`
4. Set `ticket.status = Ignored`
5. Create `TicketAuditEvent` (`status_change`)
6. Return updated Ticket

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
a list of ticket IDs requiring cascade updates (see Transaction
ownership exception above).

```python
@dataclass
class MarkAsDuplicateResult:
    ticket: Ticket
    cascade_ticket_ids: list[UUID]
```

This is a service-layer-only dataclass defined in
`backend/app/services/ticket_service.py` (not a Pydantic schema or
database model).

**Preconditions**:

- Ticket must be in a gate-zone status (New, Analysis, Analyzed,
  Resolved; else `InvalidTransitionError`)
- Target ticket must exist and not be soft-deleted (else
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
2. Verify ticket is in gate-zone status (else `InvalidTransitionError`)
3. Resolve canonical target via
   `ticket_mutations.resolve_canonical_target()`
4. Verify no circular reference (else `SelfDuplicateError`)
5. `auto_assign_actor(ticket, acting_user_id)`
6. Set `ticket.status = Duplicated`, `ticket.duplicate_of_id = resolved_target`
7. Create `TicketAuditEvent` (`duplicate_set`)
8. Query tickets that currently point to this ticket via
   `duplicate_of_id` — return their IDs as `cascade_ticket_ids`
9. Return `MarkAsDuplicateResult(ticket=ticket, cascade_ticket_ids=...)`

**Cascade orchestration** (caller responsibility):

After committing the primary transaction, the caller (endpoint handler)
iterates over `cascade_ticket_ids` and for each:
1. Opens a new database session
2. Acquires `FOR UPDATE` on the cascade ticket
3. Verifies the ticket is still in Duplicated status and still points to
   the original ticket (skip if reverted concurrently)
4. Sets `duplicate_of_id = resolved_target`
5. Creates `TicketAuditEvent` (`duplicate_target_changed`)
6. Commits the session

This separation preserves the "module does not commit" invariant while
allowing cascade updates in independent transactions as required by
`tickets.md`.

**Locking**: FOR UPDATE on the source Ticket row only. Cascade
operations each acquire their own FOR UPDATE in independent transactions
managed by the caller.

**reconcile_ticket_status**: NOT called — this is a direct transition
into the manual zone (Duplicated).

**Audit events**: `duplicate_set`. Cascade produces
`duplicate_target_changed` events (in separate transactions).

### soft_delete_ticket

Soft-deletes a ticket by setting `deleted_at`.

```python
async def soft_delete_ticket(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    acting_user_id: UUID | None,
) -> Ticket:
```

**Preconditions**:

- Ticket must not already be soft-deleted (else
  `TicketAlreadyDeletedError`)
- Requires `admin_ticket_ops` capability (enforced at API layer)
- Allowed from any status (immutability guard does not apply)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Verify `ticket.deleted_at IS NULL` (else `TicketAlreadyDeletedError`)
3. Set `ticket.deleted_at = now(UTC)`
4. Create `TicketAuditEvent` (`ticket_deleted`)
5. Return updated Ticket

**Note on related records**: `TicketAccessGrant` records are preserved
(the ticket remains confidential even when soft-deleted). Package records
(`TicketPackageTrack`, `TicketPackageProduct`) are also preserved. See
`tickets.md` (Soft-Delete) for full lifecycle details.

**Locking**: FOR UPDATE on Ticket row.

**reconcile_ticket_status**: NOT called — soft-deletion makes the ticket
invisible to all business logic; no gate reconciliation needed.

**Audit events**: `ticket_deleted`.

### restore_ticket

Restores a soft-deleted ticket and reconciles its status.

```python
async def restore_ticket(
    db: AsyncSession,
    *,
    ticket_id: UUID,
    acting_user_id: UUID | None,
) -> Ticket:
```

**Preconditions**:

- Ticket must be soft-deleted (`deleted_at IS NOT NULL`; else
  `TicketNotDeletedError`)
- Requires `admin_ticket_ops` capability (enforced at API layer)
- Allowed regardless of ticket status (immutability guard does not apply)

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Verify `ticket.deleted_at IS NOT NULL` (else `TicketNotDeletedError`)
3. Clear `ticket.deleted_at`
4. Create `TicketAuditEvent` (`ticket_restored`)
5. Call `reconcile_ticket_status(ticket)` — reconcile status with current
   gate conditions (data may have changed while the ticket was deleted)
6. Return updated Ticket

**Locking**: FOR UPDATE on Ticket row.

**reconcile_ticket_status**: YES — the ticket's status at deletion time
may no longer be valid given current gate conditions.

**Audit events**: `ticket_restored`. Possibly `status_change` (from
evaluate).

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

- Requires `manage_confidentiality` capability (enforced at API layer)
- Immutability guard applies

**Behavioral steps**:

1. Acquire `FOR UPDATE` on the Ticket row
2. Immutability guard check
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

- Ticket must be confidential (`is_confidential = true`; else
  `TicketNotConfidentialError`)
- Target user must exist (else `UserNotFoundError`)
- Requires `manage_confidentiality` capability (enforced at API layer)
- Immutability guard is enforced at the API layer via
  `require_ticket_mutable` dependency (not inside this function)

**Behavioral steps**:

1. Verify ticket is confidential (else `TicketNotConfidentialError`)
2. **Idempotency check**: if grant already exists for this user, return
   existing grant unchanged (no audit event)
3. INSERT `TicketAccessGrant` record (`ticket_id`, `user_id`,
   `granted_by = acting_user_id`, `granted_at = now(UTC)`)
4. Create `TicketAuditEvent` (`access_grant_added`)
5. Return the created grant

**Concurrency**: If two concurrent requests attempt to grant access to
the same user, the UNIQUE constraint on `TicketAccessGrant`
(`ticket_id`, `user_id`) prevents duplicates. The service catches the
resulting `IntegrityError` and treats it as an idempotent success
(returns the existing grant, no audit event).

**Locking**: Not required on the Ticket row — the operation does not
modify the Ticket entity. The unique constraint provides concurrency
safety.

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

- Ticket must be confidential (`is_confidential = true`; else
  `TicketNotConfidentialError`)
- Target user must exist (else `UserNotFoundError`)
- Requires `manage_confidentiality` capability (enforced at API layer)
- Immutability guard is enforced at the API layer via
  `require_ticket_mutable` dependency (not inside this function)

**Behavioral steps**:

1. Verify ticket is confidential (else `TicketNotConfidentialError`)
2. **Idempotency check**: if grant does not exist for this user, return
   without side effects (no audit event)
3. Delete `TicketAccessGrant` record
4. Create `TicketAuditEvent` (`access_grant_removed`)
5. Return

**Locking**: Not required on the Ticket row.

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

| Exception class | Mapped API error code | Raised by |
|----------------|----------------------|-----------|
| `TicketNotFoundError` | `TICKET_NOT_FOUND` | All operations (ticket lookup) |
| `TicketNotMutableError` | `TICKET_NOT_MUTABLE` | Operations with immutability guard |
| `InvalidTransitionError` | `TICKET_INVALID_TRANSITION` | `ignore_ticket`, `mark_as_duplicate` |
| `TicketCVEAlreadySetError` | `TICKET_CVE_ALREADY_SET` | `associate_cve` |
| `TicketCVENotSetError` | `TICKET_CVE_NOT_SET` | `dissociate_cve` |
| `TicketCVEConflictError` | `TICKET_CVE_CONFLICT` | `create_ticket`, `associate_cve` |
| `InvalidAssigneeError` | `TICKET_ASSIGNEE_NOT_VA` or `TICKET_ASSIGNEE_INACTIVE` | `assign_ticket` |
| `SelfDuplicateError` | `TICKET_SELF_DUPLICATE` | `mark_as_duplicate` |
| `DuplicateChainDepthError` | `TICKET_DUPLICATE_CHAIN_DEPTH` | `mark_as_duplicate` (via `resolve_canonical_target`) |
| `TicketAlreadyDeletedError` | `TICKET_ALREADY_DELETED` | `soft_delete_ticket` |
| `TicketNotDeletedError` | `TICKET_NOT_DELETED` | `restore_ticket` |
| `TicketNotConfidentialError` | `TICKET_NOT_CONFIDENTIAL` | `grant_access`, `revoke_access`, `list_access_grants` |
| `UserNotFoundError` | `USER_NOT_FOUND` | `grant_access`, `revoke_access` |

All exceptions inherit from a common `TicketServiceError` base class.
API endpoint handlers catch `TicketServiceError` subclasses and map them
to the corresponding HTTP status code and error code per `api-spec.md`.

Note: `InvalidAssigneeError` carries a `reason` attribute that
distinguishes between the two failure modes. The API handler maps
`reason="not_va"` to `TICKET_ASSIGNEE_NOT_VA` (400) and
`reason="inactive"` to `TICKET_ASSIGNEE_INACTIVE` (400).

## Callers

| Caller | Operations used |
|--------|----------------|
| API endpoint handlers (`api/v1/tickets.py`) | All 12 operations |
| CVE ingestion fetcher (`tasks/cve_sync.py`) | `create_ticket` (source=`cve_ingestion`) |
| IBS track release detection (`tasks/check_ibs_track_releases.py`) | `create_ticket` (source=`release_detection`, Case C) |

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
    ├── auto_assign_actor()
    └── resolve_canonical_target()
         ▲                ▲
         │                │
  ticket_service    package_service
  (non-gate ops)    (package ops)
```

| ticket_service function | reconcile_ticket_status | auto_assign_actor | resolve_canonical_target |
|------------------------|:----------------------:|:---------------------:|:------------------------:|
| create_ticket          | —                      | —                     | —                        |
| associate_cve          | ✓                      | ✓                     | —                        |
| dissociate_cve         | ✓                      | —                     | —                        |
| assign_ticket          | ✓                      | —                     | —                        |
| ignore_ticket          | —                      | ✓                     | —                        |
| mark_as_duplicate      | —                      | ✓                     | ✓                        |
| soft_delete_ticket     | —                      | —                     | —                        |
| restore_ticket         | ✓                      | —                     | —                        |
| set_confidentiality    | —                      | —                     | —                        |
| grant_access           | —                      | —                     | —                        |
| revoke_access          | —                      | —                     | —                        |
| list_access_grants     | —                      | —                     | —                        |

## Architectural Test Requirement

The following integration tests MUST be implemented to verify correct
behavior of `ticket_service` operations:

1. **CVE association causes status regression**: create a ticket without
   CVE, set `severity_override`, add a package with tracks in final
   status to reach Analyzed. Associate a CVE → verify ticket regresses
   to Analysis (gate #3 and #4 fail)

2. **CVE dissociation causes status regression**: create a ticket with
   CVE and CVSS data, reach Analyzed. Dissociate CVE with
   `severity_override = NULL` → verify ticket regresses to Analysis

3. **Assignment promotes New → Analysis**: create a ticket in New status.
   Assign a VA → verify ticket promotes to Analysis

4. **Assignment idempotency**: assign a ticket to user X, then assign
   again to user X → verify no audit event is created on the second call

5. **Mark-as-duplicate cascade with concurrent revert**: mark ticket B as
   duplicate of C (with ticket A pointing to B). Verify cascade updates
   A to point to C. Then revert A → verify A is no longer Duplicated

6. **CVE uniqueness race condition**: simulate concurrent `create_ticket`
   calls for the same CVE → verify one succeeds and the other raises
   `TicketCVEConflictError`

7. **grant_access concurrent requests**: simulate concurrent
   `grant_access` calls for the same user/ticket → verify one creates
   the grant and the other returns idempotent success

## Cross-references

- `docs/features/tickets/ticket-mutations.md` — gate-relevant mutations,
  `reconcile_ticket_status` contract
- `docs/features/tickets/tickets.md` — ticket lifecycle, status gates,
  API endpoint definitions
- `docs/features/tickets/ticket-audit-log.md` — audit event types and
  contract
- `docs/features/tickets/tickets.md` — CVE Resolution Behavior (section
  "CVE Resolution Behavior")
- `docs/features/tickets/cve-tracking.md` — On-demand Single-CVE Fetch
- `docs/features/identity/rbac.md` — capability definitions
  (`triage_ticket`, `admin_ticket_ops`, `manage_confidentiality`,
  `create_ticket`)
- `docs/conventions.md` — Transaction and Locking pattern
- `docs/api-spec.md` — general API conventions, error code categories
