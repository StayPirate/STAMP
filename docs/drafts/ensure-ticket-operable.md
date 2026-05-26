# Refactoring: `ensure_ticket_operable` — Centralized Ticket Guard

**Status**: Draft
**Created**: 2026-05-26
**Scope**: `ticket-mutations`, `ticket-service`, `package-service`, `tickets`, `api-spec`, `rbac`

## Summary

Introduce `ensure_ticket_operable()` as a shared helper function in
`ticket_mutations` that consolidates two guards currently repeated
inline (~20+ times) across three service modules:

1. **Soft-delete guard**: `deleted_at IS NOT NULL` → `TicketSoftDeletedError`
2. **Immutability guard**: `status ∈ {Ignored, Duplicated}` → `TicketNotMutableError`

This eliminates redundancy in the service layer and makes the API-layer
`require_ticket_mutable` dependency redundant for all mutation endpoints,
enabling its removal and simplification of endpoint handler signatures.

## Motivation

### Current state

Three modules independently repeat the same two-step guard after
acquiring `FOR UPDATE` on the ticket row:

| Module | Soft-delete guard | Immutability guard | Pattern |
|--------|-------------------|--------------------|---------|
| `ticket_service` | `TicketAlreadyDeletedError` | `TicketNotMutableError` | inline per function |
| `ticket_mutations` | `TicketSoftDeletedError` | `TicketNotMutableError` | inline per function |
| `package_service` | `TicketSoftDeletedError` | `TicketNotMutableError` | inline per function |

Additionally, the API layer applies `require_ticket_mutable` (a FastAPI
dependency) on mutation endpoints, which performs the same immutability
check — resulting in a redundant `SELECT` before the service function's
`SELECT ... FOR UPDATE`.

### Problems

1. **Repetition**: ~20+ inline guard implementations across three modules
2. **Inconsistent naming**: `TicketAlreadyDeletedError` (ticket-service)
   vs `TicketSoftDeletedError` (ticket-mutations, package-service)
3. **Redundant DB query with TOCTOU window**: `require_ticket_mutable`
   checks status without holding a lock, then the service function loads
   the ticket again with `FOR UPDATE`. Between these two checks, a
   concurrent transaction can change the ticket's status — making the
   API-layer check an unreliable opportunistic optimization, not a
   guarantee. The true enforcement has always been in the service layer
   under `FOR UPDATE`
4. **Asymmetry**: `mark_as_duplicate` uses a "gate-zone check" that
   rejects the same statuses (Ignored, Duplicated) but raises
   `InvalidTransitionError` instead of `TicketNotMutableError`, creating
   an inconsistency with `tickets.md` which documents `TICKET_NOT_MUTABLE`
5. **Missing guard**: `set_product_released_at` has no ticket-level
   guards (soft-delete, immutability), relying entirely on the caller
   to scope correctly — no defense-in-depth

## Design

### Helper function

```python
# in ticket_mutations
def ensure_ticket_operable(ticket: Ticket) -> None:
    """Reject mutations on soft-deleted or manually-closed tickets.

    Call after acquiring FOR UPDATE on the ticket row.
    Raises TicketSoftDeletedError if deleted_at is set.
    Raises TicketNotMutableError if status is Ignored or Duplicated.

    Precedence: when a ticket is both soft-deleted AND in Ignored/Duplicated
    status, TicketSoftDeletedError wins (soft-delete is checked first).
    This ordering is intentional and contractual.
    """
    if ticket.deleted_at is not None:
        raise TicketSoftDeletedError(ticket.id)
    if ticket.status in (TicketStatus.Ignored, TicketStatus.Duplicated):
        raise TicketNotMutableError(ticket.id)
```

This function performs no database operations. It validates invariants
on an already-loaded `Ticket` object. The caller is responsible for
loading the ticket with `SELECT ... FOR UPDATE` before invoking this
function.

### Location

`ticket_mutations` — already imported by both `ticket_service` and
`package_service`. No new import relationships needed.

```
ticket_mutations  (shared infrastructure)
    ├── reconcile_ticket_status()
    ├── auto_assign_actor()
    ├── resolve_canonical_target()
    └── ensure_ticket_operable()    ← NEW
         ▲                ▲
         │                │
  ticket_service    package_service
```

### Exception naming unification

`TicketAlreadyDeletedError` is removed entirely. All soft-delete
detection uses `TicketSoftDeletedError` (already used by 2 of 3
modules). `ticket_service` renames its sole divergent exception class
`TicketAlreadyDeletedError` → `TicketSoftDeletedError`.

The `TICKET_ALREADY_DELETED` error code is retired from the catalog.

### API error code mapping

`TicketSoftDeletedError` maps to HTTP 410 with code `TICKET_DELETED`.
This is semantically consistent: 410 Gone means "the resource existed
but has been removed", which is exactly what a soft-deleted ticket is.

The mapping is coherent with `require_accessible_ticket`, which already
returns `410 TICKET_DELETED` for non-admin callers on soft-deleted
tickets. With this unification, the 410 response for soft-deleted
tickets has a single meaning regardless of which layer produces it:

- **API layer** (`require_accessible_ticket`): non-admin callers are
  blocked before reaching the service layer → 410 `TICKET_DELETED`
- **Service layer** (`ensure_ticket_operable` or `soft_delete_ticket`
  inline check): admin callers pass the API check but are blocked from
  mutating a soft-deleted ticket → 410 `TICKET_DELETED`

Admin callers retain **read access** to soft-deleted tickets (GET
endpoints pass `require_accessible_ticket`). Only **mutations** are
rejected by the service-layer guard.

**Impact on `soft_delete_ticket`**: this function retains its own inline
soft-delete check (opt-out from `ensure_ticket_operable`), but the
exception it raises changes from `TicketAlreadyDeletedError` →
`TicketSoftDeletedError`. The API error code for
`DELETE /api/v1/tickets/{ticket_id}` when the ticket is already
soft-deleted becomes `410 TICKET_DELETED` (previously `409
TICKET_ALREADY_DELETED`). The `TICKET_ALREADY_DELETED` code is removed
from the error catalog in `api-spec.md`.

### Terminology unification

This refactoring retires **"immutability guard"** as a standalone term.
The existing terminology is consolidated as follows:

- **"Gate zone"** remains as the domain concept: the set of statuses
  (New, Analysis, Analyzed, Resolved) where gate evaluation applies.
  This term is well-established in `ticket-mutations.md`,
  `package-service.md`, and `tickets.md`
- **"Immutability guard"** (used in `ticket-service.md`) is replaced by
  a direct reference to `ensure_ticket_operable` in preconditions
- In spec prose, functions that previously documented "ticket must be in
  the gate zone" or "immutability guard" now state: "Call
  `ensure_ticket_operable(ticket)`" — which implicitly verifies both
  soft-delete and gate-zone membership

## Adoption per module

### `ticket_mutations`

| Function | Action |
|----------|--------|
| `create_cvss_assessment` | Replace inline guards with `ensure_ticket_operable` |
| `update_cvss_assessment` | Replace inline guards with `ensure_ticket_operable` |
| `delete_cvss_assessment` | Replace inline guards with `ensure_ticket_operable` |
| `set_severity_override` | Replace inline guards with `ensure_ticket_operable` |
| `reopen_from_ignored` | **Opt-out** — must operate on Ignored tickets; retains only the soft-delete guard inline (`TicketSoftDeletedError`) |
| `revert_duplicate` | **Opt-out** — must operate on Duplicated tickets; retains only the soft-delete guard inline (`TicketSoftDeletedError`) |
| `reconcile_ticket_status` | No guard needed — shared helper called by functions that already hold the guard |
| `auto_assign_actor` | No guard needed — shared helper called within guarded functions |
| `resolve_canonical_target` | No guard needed — read-only utility |

### `ticket_service`

| Function | Action |
|----------|--------|
| `create_ticket` | Does not load existing ticket — no guard needed |
| `associate_cve` | Replace inline guards with `ensure_ticket_operable` |
| `dissociate_cve` | Replace inline guards with `ensure_ticket_operable` |
| `assign_ticket` | Replace inline guards with `ensure_ticket_operable` |
| `ignore_ticket` | Call `ensure_ticket_operable` (catches Ignored/Duplicated/soft-deleted), then own status check (New/Analysis required). **Ordering constraint**: `ensure_ticket_operable` MUST execute before the function's own status check — this is contractual, not incidental. Execution order: `ensure_ticket_operable` fires first for Ignored/Duplicated → `TICKET_NOT_MUTABLE`; own check fires for Analyzed/Resolved → `TICKET_INVALID_TRANSITION`. No change to API contract |
| `mark_as_duplicate` | Replace gate-zone check with `ensure_ticket_operable`. Error changes from `InvalidTransitionError` to `TicketNotMutableError`. This is a **corrective API change** (bug fix): `tickets.md` already documents `TICKET_NOT_MUTABLE` for this endpoint, but the service was diverging by raising `TICKET_INVALID_TRANSITION` — the spec was correct, the implementation was wrong |
| `set_confidentiality` | Replace inline guards with `ensure_ticket_operable` |
| `grant_access` | Replace inline guards with `ensure_ticket_operable` |
| `revoke_access` | Replace inline guards with `ensure_ticket_operable` |
| `soft_delete_ticket` | **Opt-out** — operates on any status |
| `restore_ticket` | **Opt-out** — operates on soft-deleted tickets |

### `package_service`

| Function | Action |
|----------|--------|
| `set_track_status` | Replace inline guards with `ensure_ticket_operable` |
| `set_track_delivery_status` | Replace inline guards with `ensure_ticket_operable` |
| `set_product_eligibility` | Replace inline guards with `ensure_ticket_operable` |
| `set_product_released_at` | **Add** `ensure_ticket_operable` on the parent ticket. Currently has no ticket-level guards — the caller (`check_product_releases`) scopes to active tickets, but the function itself has no defense-in-depth |
| Other mutation functions | Replace inline guards with `ensure_ticket_operable` |

**Clarification needed in `package-service.md`**: the current spec
confuses ticket-level soft-delete with package/track/product-level
soft-delete. The intended semantics are:

- **Non-operable tickets** (soft-deleted, Ignored, or Duplicated) MUST
  NOT receive any mutations — `ensure_ticket_operable` enforces this.
  Note: this is distinct from the "inactive tickets" concept in
  `tickets.md` (which includes Resolved)
- **Operable tickets with soft-deleted packages/tracks/products** DO
  receive mutations on those soft-deleted child records (release
  detection, status updates) — these are factual observations that keep
  soft-deleted records current with reality

The `set_product_released_at` spec currently states "Preconditions:
none" and "release detection applies regardless of soft-deletion status".
This must be rewritten to clarify that:

1. The "regardless of soft-deletion" clause applies to **package/track/
   product-level** `deleted_at` only (child records on active tickets)
2. **Ticket-level** operability is now enforced via
   `ensure_ticket_operable` — release detection does NOT apply to
   non-operable tickets (soft-deleted, Ignored, or Duplicated)

This same clarification applies to the "Soft-Deleted Records and
Mutations" section of `package-service.md` (currently lines 846-856),
which must explicitly distinguish the two levels.

### Caller error handling for new guard exceptions

Adding `ensure_ticket_operable` to `set_product_released_at` introduces
`TicketSoftDeletedError` and `TicketNotMutableError` as new possible
exceptions from a function that previously had "Preconditions: none".

Automated callers (`check_product_releases`, `check_ibs_track_releases`,
`IBSEventConsumer`) already scope their queries to operable tickets at
query time (active status + `deleted_at IS NULL`). The guard fires only
in race conditions (ticket status changed between query and mutation).

**Required caller behavior**: catch `TicketSoftDeletedError` and
`TicketNotMutableError`, log a WARNING with the ticket ID and skipped
product/track ID, and continue processing the next item. Do NOT
propagate the exception or abort the fetcher run.

This is consistent with the established error handling pattern in
`check_ibs_track_releases` (skip codestream on error, continue with
remaining).

## API layer simplification

### Removal of `require_ticket_mutable`

With `ensure_ticket_operable` in the service layer, the API-layer
`require_ticket_mutable` dependency becomes redundant for all mutation
endpoints. It can be removed from these 9 endpoints:

1. `POST .../associate-cve`
2. `DELETE .../cve`
3. `PATCH .../severity`
4. `PATCH .../assignee`
5. `POST .../ignore`
6. `POST .../duplicate`
7. `PATCH .../confidentiality`
8. `POST .../access` (grant)
9. `DELETE .../access/{user}` (revoke)

**Behavioral equivalence**: for each endpoint, the service-layer
`ensure_ticket_operable` produces the identical 409 `TICKET_NOT_MUTABLE`
response. For `ignore_ticket`, `ensure_ticket_operable` fires before the
function's own status check, so Ignored/Duplicated tickets still receive
`TICKET_NOT_MUTABLE` (not `TICKET_INVALID_TRANSITION`). No API contract
change.

**Endpoints NOT affected** (no `require_ticket_mutable` today):
- `POST .../reopen` — exit from Ignored
- `POST .../revert-duplicate` — exit from Duplicated
- `DELETE /api/v1/tickets/{ticket_id}` — admin-only soft-delete
- `POST .../restore` — admin-only restore
- All GET endpoints

### `require_accessible_ticket` — NOT removed

`require_accessible_ticket` remains at the API layer. It serves a
distinct security purpose:
- Confidentiality filtering (404 for unauthorized callers on
  confidential tickets — hides ticket existence)
- Soft-delete visibility (410 `TICKET_DELETED` for non-admin callers)

The service-layer soft-delete guard (`TicketSoftDeletedError`) is
defense-in-depth for non-API callers, not a replacement.

### Unified soft-delete error across all mutations

With `TicketAlreadyDeletedError` removed, all mutation functions in
`ticket_service` that previously raised `TicketAlreadyDeletedError` →
`409 TICKET_ALREADY_DELETED` now raise `TicketSoftDeletedError` → `410
TICKET_DELETED` via `ensure_ticket_operable`. This is a natural
consequence of the exception unification and requires no additional
work beyond the changes already listed.

For API callers, this is transparent: non-admin callers are blocked by
`require_accessible_ticket` (same 410 `TICKET_DELETED`) before reaching
the service layer. Admin callers who pass the accessibility check
receive 410 from the service layer — semantically identical.

## Spec changes required

| Spec | Changes |
|------|---------|
| `ticket-mutations.md` | Add `ensure_ticket_operable` definition and contract; add to exported functions list; update behavioral steps of existing functions to reference the helper |
| `ticket-service.md` | Replace inline guards with `ensure_ticket_operable` in all functions (except opt-outs); `mark_as_duplicate` switches from `InvalidTransitionError` to `TicketNotMutableError` (corrective change); remove `TicketAlreadyDeletedError` entirely, replace with `TicketSoftDeletedError`; update Service Exceptions table (remove `TICKET_ALREADY_DELETED` row, add `TicketSoftDeletedError` → `TICKET_DELETED` HTTP 410); retire "immutability guard" term in favor of `ensure_ticket_operable` references; update Module Invariant section |
| `package-service.md` | Replace inline guards with `ensure_ticket_operable`; `set_product_released_at` adds `ensure_ticket_operable` on parent ticket; rewrite "Preconditions: none" and "Soft-Deleted Records and Mutations" section to distinguish ticket-level operability from package/track/product-level soft-delete |
| `tickets.md` | Remove `require_ticket_mutable` from 9 endpoint handler signatures; simplify "Mutability Guard" section (lines 755-782) to a brief note; keep `TICKET_NOT_MUTABLE` in `ignore_ticket` error table (now produced by `ensure_ticket_operable` for Ignored/Duplicated, while `TICKET_INVALID_TRANSITION` covers Analyzed/Resolved); update `soft_delete_ticket` error from `TICKET_ALREADY_DELETED` (409) to `TICKET_DELETED` (410); update ~6 inline references to `require_ticket_mutable`; clean up mark-as-duplicate prose |
| `api-spec.md` | Update "Manual-Zone Mutability Guard" section (lines 359-378); document that immutability is enforced at the service layer via `ensure_ticket_operable`; update auth chain description (line 57) to remove `require_ticket_mutable` step; remove `TICKET_ALREADY_DELETED` from the error code catalog (line 151); update Ticket Accessibility Check exemption paragraph (line 350-351) from `409 TICKET_ALREADY_DELETED` to `410 TICKET_DELETED` |
| `rbac.md` | Update authorization chain (line 299) to remove `require_ticket_mutable` as a named step; replace "final status" with "manual-zone status"; note that mutability is now enforced at the service layer |
| `data-model.md` | Add brief clarifying note near the hierarchical exclusion model: "Ticket-level `deleted_at` blocks all mutations on the ticket and its children via `ensure_ticket_operable`. Package/track/product-level `deleted_at` does not block mutations on those children — see `package-service.md` for the full semantics" |

## Implementation plan

### Phase 1: Spec updates

1. Update `ticket-mutations.md` — add `ensure_ticket_operable` contract
2. Update `ticket-service.md` — adopt helper, fix naming, update
   `mark_as_duplicate`, retire "immutability guard" term
3. Update `package-service.md` — adopt helper, fix
   `set_product_released_at`, rewrite "Soft-Deleted Records and
   Mutations" section to distinguish ticket-level from package-level
4. Update `tickets.md` — remove `require_ticket_mutable`, simplify
   Mutability Guard section, update `soft_delete_ticket` error code,
   clean up error tables and inline references
5. Update `api-spec.md` — update Manual-Zone Mutability Guard section,
   auth chain, Ticket Accessibility Check exemption paragraph,
   remove `TICKET_ALREADY_DELETED` from error catalog
6. Update `rbac.md` — update authorization chain description, replace
   "final status" with "manual-zone status"
7. Update `data-model.md` — add clarifying note on ticket-level vs
   child-level soft-delete semantics

### Phase 2: Post-change reviews

Run the following reviewers on each modified spec to detect regressions
or new issues:

| Spec | Reviewers |
|------|-----------|
| `ticket-mutations` | `spec-gap-analyzer`, `spec-coherence-reviewer` |
| `ticket-service` | `spec-gap-analyzer`, `spec-coherence-reviewer`, `api-convention-reviewer` |
| `package-service` | `spec-gap-analyzer`, `spec-coherence-reviewer` |
| `tickets` | `spec-gap-analyzer`, `spec-coherence-reviewer`, `api-convention-reviewer`, `security-reviewer` |
| `api-spec` | `spec-coherence-reviewer` |
| `rbac` | `spec-coherence-reviewer` |
| `data-model` | `data-model-reviewer` |

Additionally, run `docs-placement-reviewer` on `ticket-mutations.md`
(new pattern/rule added).

### Phase 3: Cleanup

Delete this draft file (`docs/drafts/ensure-ticket-operable.md`) after
all spec changes are applied and reviews are clean.
