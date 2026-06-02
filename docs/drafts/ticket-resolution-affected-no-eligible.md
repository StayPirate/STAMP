# Draft: Auto-resolution for AFFECTED tracks with no eligible products

**Status**: Draft — open questions resolved, ready for spec integration
**Domain**: tickets / packages (ticket lifecycle gate)
**Related guardrail**: 24 (dimension orthogonality)

## Problem statement

The Analyzed → Resolved gate currently requires every active
`TicketPackageTrack` to be in a *final affectedness status* (`FIXED`,
`NOT_AFFECTED`, `WONT_FIX`). A track in `AFFECTED` is non-final and
blocks resolution.

This creates an unwanted manual burden in a legitimate scenario: a track
is genuinely `AFFECTED` (code vulnerable) but **all products under it
are ineligible** (`eligible = false` — CVSS below threshold and/or
Reactive LTSS phase). No product will ever receive a fix, so there is
nothing to wait for, yet the ticket cannot reach Resolved unless a VA
manually sets the track to `WONT_FIX`.

Two requirements:

1. The ticket must auto-resolve in this scenario — no manual `WONT_FIX`.
2. The factual state "this product is affected but will not receive a
   fix" must remain visible and communicable to customers (the track must
   stay `AFFECTED`, not be coerced to `WONT_FIX`).

## Current gate (authoritative: `tickets.md` Gate: Analyzed -> Resolved)

Resolved when ALL:

1. Every active track has a final status (`FIXED`, `NOT_AFFECTED`,
   `WONT_FIX`).
2. Every eligible product (`eligible = true`) under a `FIXED` track has
   `released_at IS NOT NULL`.

(Reachable only when the Analyzed gate also holds: at least one package,
no track in `ANALYSIS`, severity set, SUSE CVSS provided.)

## Rejected alternatives

### Auto-set the track to WONT_FIX when all products ineligible

Violates Guardrail 24 (setting affectedness as a side effect of
eligibility state) and is semantically wrong (`WONT_FIX` = discretionary
decision, not ineligibility). Also produces audit churn when eligibility
oscillates.

### New auto-managed final status (e.g., AFFECTED_NO_FIX)

Same forbidden cross-dimensional auto-mutation + oscillation churn.

### Bypass the track check entirely; check only products

Vacuous resolution when no `FIXED` track exists: a ticket with an
`AFFECTED` track and undelivered eligible products would resolve with a
pending fix.

### Single condition "every eligible product has released_at"

**Critical, pervasive deadlock**: eligibility is computed independently
of affectedness, so a product under a `NOT_AFFECTED`/`WONT_FIX` track
stays `eligible = true` (if CVSS high) but never gets a `released_at`.
The ticket can never resolve. Since most products are eligible by
default, this affects almost every ticket with a non-affected
codestream.

## Proposed solution: per-track "resolution-complete" predicate

Replace the two gate conditions with a single per-track predicate. Only
tracks that are not effectively excluded (per the Hierarchical Exclusion
Model) are considered. For each such track, only products that are not
effectively excluded are considered when evaluating product-level
conditions.

A track is **resolution-complete** when:

- (a) `status` is `NOT_AFFECTED` or `WONT_FIX`, OR
- (b) `status = FIXED` AND every non-excluded eligible product
  (`eligible = true`) under it has `released_at IS NOT NULL`, OR
- (c) `status = AFFECTED` AND it has no non-excluded eligible products
  (all non-excluded products have `eligible = false`, or no
  non-excluded products exist).

`ANALYSIS` is never resolution-complete. **Resolved** = every
non-excluded active track is resolution-complete.

This unifies the existing two conditions — (a) + (b) reproduce today's
behavior exactly — and adds (c) for the new case.

### Properties

- Auto-resolves the AFFECTED + all-ineligible case (clause c) — no
  manual `WONT_FIX` needed.
- The track stays `AFFECTED` — the "affected, no fix" fact is preserved
  and communicable to customers.
- Reversible automatically: if a product becomes eligible (CVSS recalc,
  LTSS phase change, AIMAAS threshold update), clause (c) ceases to
  hold and the ticket reverts to Analyzed, waiting for delivery.
- Guardrail 24 compliant: the predicate is **read** at the gate
  (`reconcile_ticket_status`); it does not mutate any dimension.
- No deadlock: NOT_AFFECTED/WONT_FIX tracks never require a release
  (clause a).

## Customer communication

No new API field is needed. The "affected but will not receive a fix"
state is fully derivable by consumers from existing fields:

- `track.status == "AFFECTED"` (the code is vulnerable)
- `product.eligible == false` (the product will not receive a fix)

The UI can combine these two existing fields to display an indicator
(e.g., a badge or tooltip) without requiring backend changes. This
avoids introducing a redundant computed field for a trivial conjunction
of already-exposed data.

## Trade-off: invariant change

The previous invariant — "Resolved implies every active track is in a
final affectedness status (`FIXED`, `NOT_AFFECTED`, `WONT_FIX`)" — is
replaced by:

> **New invariant**: Resolved implies every active track is
> *resolution-complete* (per the predicate above).

An `AFFECTED` track with all products ineligible is resolution-complete
and is **not** an anomaly in a Resolved ticket. This is the intended
semantic: the vulnerability is real, but no product will receive a fix
due to ineligibility — there is nothing left to wait for.

Dependents to audit during implementation:

- UI ticket/track displays and badges (do they assume Resolved implies
  all tracks final?).
- Reporting/aggregation that filters on track status for resolved
  tickets (e.g., `TrackSummary.affected > 0` on a Resolved ticket is
  now valid, not an error).
- Reverse-transition logic (already handled by reconcile re-evaluating
  after every gate-relevant change — no action needed).
- Future anomaly detection (Anomaly Observer draft) — an `AFFECTED`
  track with all-ineligible products in a Resolved ticket must NOT be
  flagged as anomalous.

Note: `package-service.md` (Architectural Test Requirement, line ~910)
and `package-model.md` (Exclusion from System Operations, lines
~737-738) are now covered by steps 3 and 6 of the implementation plan.

## Edge cases to specify

| Scenario | Predicate outcome | Correct? |
|----------|-------------------|----------|
| `AFFECTED` track with a **mix** of eligible/ineligible products | Has at least 1 eligible product → NOT resolution-complete | Yes — resolution requires one of: (1) track transitions to `FIXED` via release detection or VA action, then clause (b) applies; (2) VA sets track to `WONT_FIX`/`NOT_AFFECTED`, then clause (a) applies; (3) remaining eligible products become ineligible, then clause (c) applies |
| `AFFECTED` track with a product carrying `released_at` while track still `AFFECTED` (detection lag) | Still has eligible products → blocks | Yes — track-level cross-check preserved |
| Track with **no products at all** | Clause (c) holds (no eligible products) → resolution-complete | Safe — orphan cleanup prevents post-creation removal; creation-time SMELT mismatch starts in ANALYSIS (never resolution-complete) and requires deliberate VA action to reach AFFECTED |
| Excluded products/tracks (Hierarchical Exclusion Model) | Only non-excluded records count, as today | Unchanged |
| Eligibility oscillates (CVSS recalc, AIMAAS threshold change) | Ticket auto-transitions between Resolved and Analyzed | Correct — reversibility built in |
| `AFFECTED` track, all products ineligible, but some have `released_at IS NOT NULL` | Clause (c) holds — no eligible products → resolution-complete | Correct — `released_at` is a factual observation independent of eligibility (product release detection sets it regardless of affectedness/eligibility). The customer-facing state ("affected, no fix planned") remains derivable from `eligible = false`. The presence of `released_at` on an ineligible product means a fix was incidentally delivered via a broader maintenance update. The future Anomaly Observer may surface this for VA review, but it does not block resolution |
| Product restored under an AFFECTED track in a Resolved ticket | Restored product is eligible → clause (c) ceases to hold → ticket reverts to Analyzed | Correct — existing restore → reconcile flow handles this (`package_service.restore_ticket_package_product()` calls `reconcile_ticket_status()`) |

## Implementation plan

1. **`docs/features/tickets/tickets.md`**: rewrite the Analyzed →
   Resolved gate as the per-track resolution-complete predicate;
   update the Resolved status description in the Statuses table
   (currently says "Security updates have been released for all
   affected packages across all products"); document reverse
   transitions and edge cases.
2. **`docs/features/tickets/ticket-mutations.md`**: update the
   `reconcile_ticket_status()` contract to reflect the new predicate
   (the function evaluates "resolution-complete" per track, not "all
   tracks in final status").
3. **`docs/features/packages/package-model.md`**: update both (a) the
   Ticket Lifecycle Integration summary (lines ~1081-1083: gate
   conditions) and (b) the Exclusion from System Operations section
   (lines ~737-738) to reference the resolution-complete predicate.
4. **`docs/data-model.md`**: update the status transition summary
   (currently says "all packages in final status" for Resolved —
   also fix the imprecise "packages" to "tracks").
5. **`docs/architecture.md`**: update line ~272 ("When all packages in
   a ticket reach a final status, the ticket can transition to
   Resolved") to reflect the resolution-complete predicate.
6. **`docs/features/packages/package-service.md`**: update the
   Architectural Test Requirement section (line ~910) — the example
   "setting all tracks to final status triggers Analyzed -> Resolved"
   must mention the second resolution path introduced by clause (c)
   (AFFECTED track with all products ineligible).
7. **Reviews**: `@spec-gap-analyzer`, `@spec-coherence-reviewer`,
   `@data-model-reviewer` (likely no schema change),
   `@api-parity-reviewer`, `@docs-reviewer`,
   `@ticket-integrity-reviewer`.

## Resolved questions

1. **Is "track with no products = resolution-complete" always desired, or
   should it be flagged as a data-quality anomaly?**

   **Resolution**: no special treatment needed. The existing Orphan
   Cleanup Cascade (`package-service.md`, Invariant 1) guarantees that
   when the last active product under a track is soft-deleted, the track
   itself is automatically soft-deleted — so an active track cannot lose
   all its products and remain active. The only path to an active
   `AFFECTED` track with zero products is the SMELT mismatch scenario
   (target repositories not yet synced to local `ProductRepository`
   entries). In that case the track starts in `ANALYSIS` — which is
   never resolution-complete — and would require a deliberate VA action
   to reach `AFFECTED`. This is an intentional edge case, not a
   data-quality anomaly.

2. **Should a dedicated API field surface the "affected but no fix"
   state?**

   **Resolution**: no. The state is fully derivable from existing API
   fields (`track.status == "AFFECTED"` + `product.eligible == false`).
   The UI can combine these to display an indicator without requiring a
   new backend field. Adding a computed field for a trivial conjunction
   of already-exposed data would be redundant.

3. **Should this scenario be recorded in the audit trail when it
   triggers auto-resolution?**

   **Resolution**: no. `reconcile_ticket_status()` already creates a
   `TicketAuditEvent` of type `status_change` for every status
   transition — the auto-resolution from clause (c) is no exception.
   The reason is derivable: a Resolved ticket with an `AFFECTED` track
   and all-ineligible products is unambiguous. Adding a specific
   "reason" field or comment would be inconsistent with other
   auto-transitions (which do not record their trigger) and would
   require schema changes for marginal informational value.
