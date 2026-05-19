# Duplicate Handling Redesign — Canonical Resolution Model

## Status

DRAFT — decisions finalized, pending integration into tickets spec

## Context

The current tickets spec (`docs/features/tickets/tickets.md`) contains a contradiction between two rules:

1. **Single-ticket scope** (line 499-504): `ticket_mutations` functions operate on a single ticket per transaction. Cascade updates of `duplicate_of_id` MUST NOT acquire `FOR UPDATE` on multiple ticket rows — each ticket is processed in an independent transaction.

2. **Invariant** (line 666-668): `duplicate_of_id` always references a ticket that is NOT in `Duplicated` status.

These two rules conflict: if the cascade processes each ticket independently and the process crashes mid-cascade, some tickets will retain a `duplicate_of_id` pointing to a ticket that is now in Duplicated status — violating the invariant.

This document records the chosen solution and its rationale.

## Decision

**Relax the invariant. Adopt canonical resolution as the correctness mechanism.**

The strict invariant ("`duplicate_of_id` always references a non-Duplicated ticket") is replaced with a weaker but crash-safe guarantee:

- `duplicate_of_id` SHOULD reference the canonical non-Duplicated ticket after normal write operations complete successfully.
- Correctness MUST NOT depend on immediate flatness of the link.
- All business logic that uses `duplicate_of_id` MUST resolve the canonical target through a centralized resolver function.
- A link to a Duplicated ticket is a valid transient state (e.g., after interrupted cascade) and MUST be handled gracefully by resolution.

## Rationale

- **Duplicate tickets are rare in Sentinel.** Chains involving more than two tickets are almost nonexistent.
- **No multi-ticket `FOR UPDATE`**: the single-ticket-per-transaction rule is an architectural constraint that prevents deadlocks across the entire `ticket_mutations` module. Introducing exceptions for duplicates would weaken this guarantee.
- **Crash safety without complexity**: rather than adding SERIALIZABLE transactions, advisory locks, or repair tasks, we make the system correct by construction — the resolver always produces the right answer regardless of whether the cascade completed.
- **Proportionality**: sophisticated concurrency mechanisms (ordered multi-row locking, serializable isolation, advisory locks) are disproportionate to the risk, given the rarity of duplicates.
- **API contract reliability**: third-party scripts that consume the API should be able to trust that `duplicate_of_id` points to a non-Duplicated ticket without implementing their own chain resolution logic. The canonical resolver guarantees this at the API boundary.

## Solution Design

### Canonical Target Resolver

A centralized public function `resolve_canonical_target` in the `ticket_mutations` module (`backend/app/services/ticket_mutations.py`).

Contract:
- Accepts a ticket ID and a database session.
- Follows the `duplicate_of_id` chain until a non-Duplicated ticket is found.
- Maintains a set of visited ticket IDs to detect cycles.
- Enforces a maximum hop limit (10).
- If a cycle is detected, raises an integrity error with code `TICKET_DUPLICATE_CYCLE_DETECTED` (409 Conflict). Indicates data corruption requiring admin intervention.
- If the hop limit is exceeded without finding a non-Duplicated ticket, raises an integrity error with code `TICKET_DUPLICATE_CHAIN_DEPTH` (409 Conflict). Indicates data corruption requiring admin intervention.
- Returns the canonical (non-Duplicated) target ticket.

All code paths that need the canonical target MUST use this function:
- `mark-as-duplicate` operation (pre-write validation)
- API response serialization (the `duplicate_of_id` field in GET responses returns the resolved canonical target)
- Any future logic that reads `duplicate_of_id` for decision-making

Direct reads of `duplicate_of_id` without resolution are only permitted for:
- Audit event recording (old_value/new_value store the raw DB value, which is the historical fact)
- Database-level queries that need the raw FK (e.g., finding all tickets whose raw `duplicate_of_id` points to a specific ticket, for cascade purposes)

### Mark-as-Duplicate Operation

1. Resolve the requested target to its canonical target using the resolver.
2. If the canonical target equals the ticket being modified, reject with 400 (cycle prevention).
3. Acquire `FOR UPDATE` on the ticket being modified (single ticket — existing rule).
4. Set `duplicate_of_id = canonical_target_id`.
5. Set `status = Duplicated`, store `previous_status`.
6. Create `TicketAuditEvent` (`duplicate_set`).
7. Commit.
8. Cascade (synchronous, same request): find all tickets whose `duplicate_of_id` points to the just-duplicated ticket. For each, in an independent transaction:
   - Acquire `FOR UPDATE` on that single ticket.
   - Update `duplicate_of_id` to the canonical target.
   - Create `TicketAuditEvent` (`duplicate_target_changed`, user_id = NULL).
   - Commit.
9. If the cascade is interrupted (crash, timeout), the system is NOT corrupted — subsequent operations and reads resolve the chain through the canonical resolver.

The cascade is synchronous (completes before the API response returns) because chains longer than two tickets are almost nonexistent, making the overhead negligible (1-2 extra DB operations in the worst case). Deferring to a background task would add complexity disproportionate to the benefit.

### Cascade as Best-Effort Flattening

The cascade is an optimization that reduces hops for future resolutions. It is NOT a correctness requirement. The system is correct with or without cascade completion because:

- All reads use the canonical resolver.
- Duplicated tickets are immutable (API returns 409 on modification attempts), so intermediate links are stable.
- The only operation that can alter an intermediate link is `revert-duplicate` on that specific ticket, which clears `duplicate_of_id` entirely (correct behavior regardless of chain state).

### API Response Behavior

The `duplicate_of_id` field in ticket API responses (GET /tickets/{id}, list endpoints, maintainer dashboard) MUST always contain the resolved canonical target, not the raw DB value. The API does NOT expose the raw DB value in a separate field. This ensures:

- UI links always point to the correct non-Duplicated ticket.
- Third-party scripts and integrations can trust that following `duplicate_of_id` always leads to a non-Duplicated ticket — no client-side chain resolution needed.
- The transient state (interrupted cascade) is invisible to API consumers.

The raw value remains accessible through the audit history (`duplicate_set` and `duplicate_target_changed` events record what was written to the DB).

### Audit Event Values

For `duplicate_target_changed` events created during cascade:
- `old_value`: the raw `duplicate_of_id` before the cascade update (the ID of the just-duplicated ticket — historical fact).
- `new_value`: the canonical target written to `duplicate_of_id` (the resolved non-Duplicated ticket — this is both the canonical target and the value actually persisted in the DB).

Both values are raw DB facts: `old_value` is what was there before, `new_value` is what was written. They happen to be "raw previous" and "canonical resolved" respectively, because the cascade always writes the canonical target to the DB.

### Revert-Duplicate Operation

When reverting ticket A from Duplicated status:
- Clears `duplicate_of_id` (set to NULL).
- Restores `previous_status`.
- Creates `TicketAuditEvent` (`duplicate_removed`).
- The audit event's `old_value` records the raw DB value of `duplicate_of_id` at the time of revert (historical fact, may be an intermediate if cascade was interrupted).
- Does NOT need to know or care about the canonical target — the operation simply removes A from the duplicate chain.

### Revert of an Intermediate Ticket

Scenario: `A → B → C` (A points to B, B points to C, cascade was interrupted so A was not flattened).

If a VA reverts B (removes B from Duplicated status):
- B.duplicate_of_id is cleared, B returns to its previous status.
- A still points to B, but B is no longer Duplicated.
- Therefore A.duplicate_of_id resolves to B directly (B is the canonical target now).
- This is correct: A is a duplicate of B, which is now a live ticket again.
- No cascade or repair needed on A.

### Cycle Prevention

Under normal sequential operations, cycles cannot form because:
1. `mark-as-duplicate` always resolves the target to a canonical non-Duplicated ticket before writing.
2. A ticket in Duplicated status cannot be the target of mark-as-duplicate (it would be resolved through to its canonical).
3. A non-Duplicated ticket being marked as duplicate has its target resolved — if the resolution leads back to itself, the operation is rejected.

Under concurrent operations (two users simultaneously marking tickets that reference each other), a cycle could theoretically form under READ COMMITTED isolation. This is accepted as a residual risk because:
- Duplicate operations are rare.
- Concurrent conflicting duplicate operations on the same chain are essentially zero probability.
- The cycle detection in the resolver catches this at read time with a clear integrity error (`TICKET_DUPLICATE_CYCLE_DETECTED`).
- Resolution requires manual admin intervention (same handling as chain depth exceeded).
- Adding `FOR UPDATE` on the target ticket would lock two tickets in the same transaction, contradicting the single-ticket-scope architectural rule — a disproportionate cost for an essentially impossible scenario.

## Cross-References

- `docs/features/tickets/tickets.md` — Duplicate Handling section (lines 645-694)
- `docs/features/tickets/tickets.md` — Concurrency Control / Single-ticket scope (lines 499-504)
- `docs/features/tickets/tickets.md` — Modifications in Inactive Statuses (lines 753-754)
- `docs/features/tickets/ticket-audit-log.md` — `duplicate_set`, `duplicate_removed`, `duplicate_target_changed` events
- `docs/data-model.md` — `duplicate_of_id` column definition, audit event types
- `docs/features/ui/maintainer-dashboard.md` — duplicate banner and link display
- Review finding: TKT-DES-01 (the finding that triggered this redesign)
- Related findings likely impacted: TKT-DES-02, TKT-GAP-02
