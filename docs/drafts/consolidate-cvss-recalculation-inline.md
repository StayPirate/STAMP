# Consolidate Inline CVSS Recalculation into `recalculate_cvss_chain()`

## Summary

`upsert_cvss_assessment()` and `delete_cvss_assessment()` inline the same
severity + eligibility recalculation logic that `recalculate_cvss_chain()`
encapsulates. This draft proposes consolidating the duplicated steps by
having both functions delegate to `recalculate_cvss_chain()` instead of
reimplementing the chain inline.

## Current State

### Duplicated Pattern

Both `upsert_cvss_assessment()` (step 7b-7d) and
`delete_cvss_assessment()` (step 5a-5c) perform:

1. Read `default_cvss_version` from settings (implicit — not shown in
   parameters but required by resolution functions)
2. Call `resolve_severity_score()` → update `CVE.severity` if changed
3. Call `resolve_eligibility_score()` → re-evaluate `TicketPackageProduct.eligible`
4. Create audit events (`severity_changed`, `product_eligibility_changed`)
5. Call `reconcile_ticket_status()`

This is a 1:1 overlap with `recalculate_cvss_chain()` steps 2-7.

### `recalculate_cvss_chain()` (authoritative definition)

```
1. Acquire FOR UPDATE on the Ticket row
2. resolve_severity_score(assessments, default_cvss_version)
3. Update CVE.severity if changed
4. resolve_eligibility_score(assessments, default_cvss_version)
5. Re-evaluate TicketPackageProduct.eligible for all products
6. Create audit events (severity_changed, product_eligibility_changed)
7. reconcile_ticket_status()
```

### Comparison Table

| Aspect | upsert/delete (inline) | recalculate_cvss_chain() |
|--------|------------------------|--------------------------|
| resolve_severity_score() | Yes | Yes (step 2) |
| Update CVE.severity | Yes (implicit) | Yes (step 3) |
| resolve_eligibility_score() | Yes | Yes (step 4) |
| Re-evaluate products | Yes (implicit) | Yes (step 5) |
| Audit: severity_changed | Yes | Yes (step 6) |
| Audit: product_eligibility_changed | Yes | Yes (step 6) |
| reconcile_ticket_status() | Yes | Yes (step 7) |
| Own FOR UPDATE | No (already held) | Yes (step 1) |
| default_cvss_version source | Reads from DB internally | Reads internally (or caller provides for batch) |
| Assessment-level audit | Yes (cvss_assessment_changed) | No |

## Proposed Refactoring

### `upsert_cvss_assessment()` — New Step 7

```
7. If a ticket exists:
   a. Call auto_assign_actor(ticket, acting_user_id, db)
   b. Create TicketAuditEvent (cvss_assessment_changed)
   c. Call recalculate_cvss_chain(ticket_id, acting_user_id=acting_user_id)
      — reads default_cvss_version internally, then handles: severity
        recalculation, eligibility recalculation, derived audit events,
        and reconcile_ticket_status() (which includes step 4
        post-transition catch-up)
```

### `delete_cvss_assessment()` — New Step 5

```
5. If a ticket exists:
   a. Call auto_assign_actor(ticket, acting_user_id, db)
   b. Create TicketAuditEvent (cvss_assessment_changed,
      old_value = "provider vX.Y score", new_value = NULL)
   c. Call recalculate_cvss_chain(ticket_id, acting_user_id=acting_user_id)
      — reads default_cvss_version internally, then handles: severity
        recalculation, eligibility recalculation, derived audit events,
        and reconcile_ticket_status()
```

Note: `auto_assign_actor()` was missing from the current spec for
`delete_cvss_assessment()`. This is a pre-existing bug — the Caller
Table (line 980) documents delete as a "VA-initiated CVSS operation",
and the Auto-Assignment Rule states that "any modifying operation"
triggers auto-assignment. This draft fixes the omission.

## Affected Functions (Scope)

Only two functions require changes:

| Function | File | Current inline steps | After refactoring |
|----------|------|---------------------|-------------------|
| `upsert_cvss_assessment()` | ticket-mutations.md | Steps 7b, 7c (derived audit), 7d | Replaced by single `recalculate_cvss_chain()` call |
| `delete_cvss_assessment()` | ticket-mutations.md | Steps 5a, 5b (derived audit), 5c | Replaced by single `recalculate_cvss_chain()` call |

### Intentionally NOT in scope

| Caller | Uses | Reason for exclusion |
|--------|------|---------------------|
| `package_service.override_product_eligibility()` (reset) | Only `resolve_eligibility_score()` for a single product | Partial recalculation — only ONE product, no severity recalc. Intentionally different from the full chain |
| `re_evaluate_product_eligibility` (threshold_change) | Only `resolve_eligibility_score()` | Lifecycle-triggered, explicitly documented as different from CVSS-triggered recalculation |
| Read-only API paths | Both resolution functions | No side effects, pure computation for response fields |

## Answer to (C): How do resolution functions get `default_cvss_version`?

**Finding**: `resolve_severity_score()` and `resolve_eligibility_score()`
are **pure functions** that receive `default_cvss_version` as a parameter
(confirmed in `cvss-scoring.md` lines 609-626 and 669-674). They never
read from the database.

The `services/cvss.py` module specification explicitly states:

> "The default CVSS version is read from the SystemSetting table via a
> dedicated settings service module. `services/cvss.py` does not access
> SystemSetting directly — the caller (API endpoint or ticket_mutations
> function) resolves the default version and passes it as a parameter."

**Design decision — optional parameter**:

The current spec requires all callers to read `default_cvss_version` and
pass it explicitly. This draft changes `recalculate_cvss_chain()` to
make the parameter **optional** (`str | None = None`): if `None`, the
function reads the version internally from
`settings_service.get_default_cvss_version(db)`.

Rationale:

- `recalculate_cvss_chain()` is NOT a pure function — it already
  performs `FOR UPDATE`, reads assessments, writes `CVE.severity`,
  writes `TicketPackageProduct.eligible`, creates audit events, and
  calls `reconcile_ticket_status()`. Adding one settings read does not
  violate any purity principle
- 4 out of 5 callers mechanically repeat the same boilerplate read
  before calling the function. Internalizing the read eliminates this
  repetition and prevents future callers from forgetting to read
- The ONE caller that needs explicit version control (the batch
  recalculation Celery task) passes the version explicitly to preserve
  the read-after-lock pattern — ensuring all tickets in a batch use the
  same version even if an admin changes it mid-batch

New signature:

```python
async def recalculate_cvss_chain(
    db: AsyncSession,
    ticket_id: UUID,
    *,
    default_cvss_version: str | None = None,
    acting_user_id: UUID | None = None,
) -> None:
    """Recalculate severity and product eligibility for a ticket.

    Args:
        default_cvss_version: If provided, uses this version for
            resolution. If None (default), reads the current version
            from settings_service. The batch recalculation task provides
            this explicitly to ensure all tickets in a batch use the
            same version (read-after-lock pattern). Other callers should
            typically omit this parameter.
        acting_user_id: Who triggered the recalculation. None for
            system-initiated operations (batch, reconcile catch-up).
    """
```

Caller impact:

| Caller | Before | After |
|--------|--------|-------|
| `upsert_cvss_assessment()` | Read setting + pass | Omit (internal read) |
| `delete_cvss_assessment()` | Read setting + pass | Omit (internal read) |
| `associate_cve()` | Read setting + pass | Omit (internal read) |
| `reconcile_ticket_status()` step 4 | Read setting + pass | Omit (internal read) |
| Batch recalculation task | Read-after-lock + pass | Pass explicitly (unchanged) |

**Conclusion**: the refactoring introduces **no measurable overhead**
and simplifies all non-batch callers. The `FOR UPDATE` that
`recalculate_cvss_chain()` step 1 attempts is a **same-transaction
no-op** in PostgreSQL (the row is already locked by the upsert/delete
function's step 5a/3a).

## Open Points

### OP-1: Is the refactoring worth doing now?

**Arguments for**:

- Single source of truth: all CVSS recalculation logic lives in one
  function (`recalculate_cvss_chain()`), reducing drift risk
- `reconcile_ticket_status()` step 4 (post-transition catch-up) is
  automatically inherited — no need to ensure upsert/delete manually
  triggers catch-up
- Simpler spec: upsert and delete steps become shorter and more focused
  on their primary responsibility (assessment persistence)
- Consistency: `associate_cve()` already delegates to
  `recalculate_cvss_chain()` — upsert/delete would follow the same pattern

**Arguments against**:

- `upsert_cvss_assessment()` is the **hottest path** in the system — it is
  called for every CVSS assessment in every CVE sync (NVD, Red Hat, CNA).
  Any overhead, even minimal, is multiplied thousands of times per sync run
- The current inline approach was designed before `recalculate_cvss_chain()`
  existed as a separate function. The duplication is an artifact, not a
  deliberate architectural choice — but it works correctly and is tested
- The spec-level change is low-risk, but implementation will need careful
  testing to verify no behavioral regressions

**Recommendation**: proceed with the refactoring. The overhead analysis
(OP-2) shows zero additional DB queries and the FOR UPDATE is a no-op.
The consistency and maintainability benefits outweigh the (negligible)
performance concern.

**Decision**: proceed. No code exists yet — ideal time to consolidate at
the spec level. The delegation also closes a spec gap (inline version
does not document product evaluation scope, override guards, or Reactive
LTSS exclusion — these are explicitly specified in
`recalculate_cvss_chain()` and inherited automatically by delegation).

### OP-2: Performance impact assessment

| Operation | Current (inline) | After refactoring | Delta |
|-----------|------------------|-------------------|-------|
| `settings_service.get_default_cvss_version(db)` | 1 read (implicit) | 1 read (explicit) | 0 |
| `SELECT ... FOR UPDATE` on Ticket | 1 (from upsert/delete step) | 2 (upsert/delete + chain step 1) | +1 no-op (same-tx re-lock) |
| `resolve_severity_score()` | 1 call | 1 call | 0 |
| `resolve_eligibility_score()` | 1 call | 1 call | 0 |
| Audit event creation | Same | Same | 0 |
| `reconcile_ticket_status()` | 1 call | 1 call | 0 |

**Net overhead**: one additional `SELECT ... FOR UPDATE` that PostgreSQL
resolves immediately as a same-transaction no-op. Negligible on any
workload.

**Optimization possibility**: `recalculate_cvss_chain()` could accept an
optional `skip_lock: bool = False` parameter that skips step 1 when the
caller guarantees the lock is already held. This eliminates even the
no-op query. Whether this optimization is warranted depends on measured
performance — it should NOT be done speculatively.

**Decision**: confirmed negligible. No optimization needed.

### OP-3: Should `recalculate_cvss_chain()` gain a `skip_lock` parameter?

If profiling shows that the redundant FOR UPDATE has measurable cost at
scale (unlikely given PostgreSQL's lock manager), the function could
accept `_lock_held: bool = False` (private parameter convention):

```python
async def recalculate_cvss_chain(
    db: AsyncSession,
    ticket_id: UUID,
    *,
    default_cvss_version: str | None = None,
    acting_user_id: UUID | None = None,
    _lock_held: bool = False,  # Private: caller guarantees FOR UPDATE
) -> None:
```

**Risk**: callers that set `_lock_held=True` incorrectly introduce race
conditions. The private naming convention (`_` prefix) and documentation
make this an explicit opt-in.

**Recommendation**: defer this optimization until profiling proves it is
needed. The no-op lock has near-zero cost in PostgreSQL.

**Decision**: deferred. The no-op lock cost is negligible; adding
`_lock_held` now would be premature optimization that complicates the
interface without measurable benefit. Revisit only if profiling
demonstrates a bottleneck.

### OP-4: Spec ambiguity — implicit settings read in upsert/delete

The current spec for `upsert_cvss_assessment()` and
`delete_cvss_assessment()` calls `resolve_severity_score()` and
`resolve_eligibility_score()` without documenting where
`default_cvss_version` comes from. This is a spec gap that exists
regardless of whether the refactoring is performed.

If the refactoring is NOT performed, this gap should still be fixed by
adding an explicit step: "Read `default_cvss_version` from
`settings_service.get_default_cvss_version(db)`" before the resolution
calls.

**Decision**: resolved automatically by the refactoring. The optional
parameter design means callers no longer need to read
`default_cvss_version` at all — `recalculate_cvss_chain()` reads it
internally when not provided. The implicit step that was undocumented
in the current spec is eliminated entirely.

### OP-5: `cvss_assessment_changed` event ordering relative to derived events

In the proposed refactoring:
- `cvss_assessment_changed` is created BEFORE calling `recalculate_cvss_chain()`
- `severity_changed` and `product_eligibility_changed` are created INSIDE `recalculate_cvss_chain()`

This means the audit trail order is:
1. `cvss_assessment_changed` (cause)
2. `severity_changed` (effect)
3. `product_eligibility_changed` (effect)

This ordering is semantically correct (cause before effect) and improves
on the current spec, which is ambiguous about intra-step event ordering
(step 7c mentions all events together without explicit sequencing). No
consumer of the audit trail depends on intra-transaction event ordering.

**Decision**: confirmed correct. The cause-before-effect ordering is an
improvement, not a regression.

## Behavioral Equivalence Analysis

A detailed comparison of the current inline steps with the proposed
delegation confirms behavioral equivalence with minor improvements:

| Aspect | Verdict |
|--------|---------|
| Functions called, data mutated | Equivalent |
| Audit trail | Improved — explicit cause→effect ordering |
| Product evaluation scope | Improved — soft-deleted products, override guards, and Reactive LTSS exclusion are now explicitly inherited from `recalculate_cvss_chain()` (closes spec gap in inline version) |
| Performance | +1 same-transaction FOR UPDATE no-op (~0.03ms). Negligible |
| Recursion safety | Identical — `ensure_ticket_operable()` prevents the scenario; recursion termination guarantee applies regardless |
| `auto_assign_actor()` timing | **Improved** — called before recalculation in both upsert and delete flows. The current spec omits `auto_assign_actor()` in the delete flow (bug); this draft adds it |
| `acting_user_id` propagation | Identical — forwarded to `recalculate_cvss_chain()` |
| Double reconciliation risk | None — `reconcile_ticket_status()` called exactly once in both flows |
| Assessment already deleted (delete case) | Identical — record deleted before recalculation in both flows |

No behavioral regression identified. The refactoring is safe to apply.

### Note: `acting_user_id` in `associate_cve()`

After this refactoring, `upsert_cvss_assessment()` and
`delete_cvss_assessment()` pass `acting_user_id` to
`recalculate_cvss_chain()`, so derived audit events (`severity_changed`,
`product_eligibility_changed`) are attributed to the acting user.

The `associate_cve()` function in `ticket-service.md` previously omitted
`acting_user_id` when calling `recalculate_cvss_chain()`. This draft
fixes this inconsistency as part of the Spec Modifications Plan
(section 5): the updated step passes `acting_user_id`, aligning audit
trail attribution across all user-initiated code paths.

### Note: `auto_assign_actor()` added to `delete_cvss_assessment()`

The current spec omits `auto_assign_actor()` from the delete flow. This
is a pre-existing bug: the Caller Table (line 980) documents
`delete_cvss_assessment()` as a "VA-initiated CVSS operation via
`/api/v1/cves/{cve_id}/cvss/...`", and the Auto-Assignment Rule
(line 783) states that "any modifying operation" on a ticket triggers
auto-assignment. This draft adds `auto_assign_actor()` to the delete
flow (Proposed Refactoring step 5a, Spec Modifications Plan item 2),
bringing it into alignment with `upsert_cvss_assessment()` and the
Auto-Assignment Rule.

## Recursion Depth Correction

The current spec (lines 228-234 of `ticket-mutations.md`) claims max
recursion depth is 1 with this justification:

> "This nested call cannot re-trigger step 4 because
> `effective_previous` in the inner call is the ticket's current status
> (already set to Analysis or Analyzed by the outer call), which is
> never in `{Resolved, Ignored, Duplicated}`."

This is **incorrect**. The outer call can set the ticket to **Resolved**
(not only Analysis/Analyzed) when gates are satisfied with pre-inactivity
data. The canonical scenario:

1. Ticket in Resolved → VA marks Ignored → later reopened
2. Outer reconcile(`previous_status=Ignored`): gates (stale) pass →
   `new_status` = Resolved → step 4 triggers (Ignored→Resolved)
3. `recalculate_cvss_chain()`: `default_cvss_version` changed during
   Ignored period → severity becomes `None` → gate #3 fails
4. Inner reconcile: `effective_previous` = **Resolved** (set by outer
   step 3) → `new_status` = Analysis → step 4 **re-triggers**
   (Resolved ∈ {Resolved, Ignored, Duplicated})
5. Second `recalculate_cvss_chain()`: idempotent (same inputs) → no-op
6. Innermost reconcile: `effective_previous` = Analysis → step 4 does
   NOT trigger → terminates

**Actual max depth**: 2 (outer + inner + innermost-no-op).

**Termination guarantee**: idempotency of `recalculate_cvss_chain()` —
a second call within the same transaction operates on identical inputs
(same assessments, same `default_cvss_version`, same products) and
produces no mutations. The innermost reconcile therefore sees a stable
active status as `effective_previous`, which is not in the trigger set.

**When depth stays at 1**: if nothing changed during the Ignored period,
`recalculate_cvss_chain()` is a no-op, inner reconcile sees
Resolved→Resolved (`new_status` = `effective_previous`), and step 4
does not re-trigger.

## Cross-references

- `docs/features/tickets/ticket-mutations.md` — `upsert_cvss_assessment()`,
  `delete_cvss_assessment()`, `recalculate_cvss_chain()`
- `docs/features/tickets/cvss-scoring.md` — resolution cascades, chain
  execution model, module architecture
- `docs/features/platform/system-settings.md` — `default_cvss_version`
  setting and batch recalculation task
- `docs/features/tickets/ticket-service.md` — `associate_cve()` (reference
  implementation of delegation pattern)
- `docs/features/packages/package-service.md` — intentionally excluded
  partial recalculation cases
- `docs/features/packages/product-lifecycle-transitions.md` — intentionally
  excluded lifecycle-triggered recalculation

## Spec Modifications Plan

When this draft is applied, the following changes are required across
multiple spec files:

### 1. `ticket-mutations.md` — `upsert_cvss_assessment()`: Replace steps 7b-7d

Current steps 7b-7d:

> 7b. Recalculate severity via `resolve_severity_score()` and
>     eligibility via `resolve_eligibility_score()`
> 7c. Create `TicketAuditEvent` (`cvss_assessment_changed`). The
>     `old_value` is derived from the `SELECT` in step 2: `NULL` if the
>     record was created, `"provider vX.Y old_score"` if updated. The
>     recalculation chain may also produce `severity_changed` and
>     `product_eligibility_changed` audit events when derived values
>     change
> 7d. Call `reconcile_ticket_status()` (post-transition catch-up, if
>     triggered, is handled internally by step 4)

Replacement (3 steps → 2 steps):

> 7b. Create `TicketAuditEvent` (`cvss_assessment_changed`). The
>     `old_value` is derived from the `SELECT` in step 2: `NULL` if the
>     record was created, `"provider vX.Y old_score"` if updated
> 7c. Call `recalculate_cvss_chain(ticket_id,
>     acting_user_id=acting_user_id)` — reads `default_cvss_version`
>     internally, recalculates severity and product eligibility, creates
>     derived audit events (`severity_changed`,
>     `product_eligibility_changed`) when values change, and calls
>     `reconcile_ticket_status()` internally (post-transition catch-up,
>     if triggered, is handled by reconcile step 4)

### 2. `ticket-mutations.md` — `delete_cvss_assessment()`: Replace steps 5a-5c

Current steps 5a-5c:

> 5a. Recalculate ticket severity via `cvss.resolve_severity_score()`
>     (5-step severity cascade); re-evaluate product eligibility via
>     `cvss.resolve_eligibility_score()` (2-step SUSE-only cascade,
>     separate call — the eligibility score may differ from the severity
>     score when SUSE has not assessed the default version)
> 5b. Create `TicketAuditEvent` (`cvss_assessment_changed`,
>     `old_value = "provider vX.Y score"`, `new_value = NULL`)
> 5c. Call `reconcile_ticket_status()`

Replacement (3 steps → 3 steps):

> 5a. Call `auto_assign_actor(ticket, acting_user_id, db)`
> 5b. Create `TicketAuditEvent` (`cvss_assessment_changed`,
>     `old_value = "provider vX.Y score"`, `new_value = NULL`)
> 5c. Call `recalculate_cvss_chain(ticket_id,
>     acting_user_id=acting_user_id)` — reads `default_cvss_version`
>     internally, recalculates severity and product eligibility, creates
>     derived audit events when values change, and calls
>     `reconcile_ticket_status()` internally

Note: the addition of `auto_assign_actor()` fixes a pre-existing bug in
the current spec. The step count stays at 3, but the responsibilities
shift (inline recalculation replaced by delegation + auto-assignment
added).

### 3. `ticket-mutations.md` — `recalculate_cvss_chain()`: Update signature and callers

Current "Primary caller" note (line 621-623):

> **Primary caller**: the batch recalculation Celery task triggered by a
> default CVSS version change (see
> `docs/features/platform/system-settings.md`).

Replacement:

> **Callers**: `upsert_cvss_assessment()`, `delete_cvss_assessment()`,
> `associate_cve()` (ticket-service), `reconcile_ticket_status()` step 4
> (post-transition catch-up), and the batch recalculation Celery task
> triggered by a default CVSS version change (see
> `docs/features/platform/system-settings.md`).

Current parameter table (line 627-632):

> | `default_cvss_version` | `str` | Yes | The CVSS version to use for severity resolution and eligibility evaluation. The caller must provide this explicitly; the function does not read the default version from the database |

Replacement:

> | `default_cvss_version` | `str \| None` | No | The CVSS version to use for severity resolution and eligibility evaluation. If `None` (default), the function reads the current version from `settings_service.get_default_cvss_version(db)`. The batch recalculation task provides this explicitly to ensure all tickets in a batch use the same version (read-after-lock pattern). Other callers should typically omit this parameter |

Current behavior step 2 (line 637-638):

> 2\. Call `cvss.resolve_severity_score()` with the provided
>    `default_cvss_version` to determine the new resolved score

Replacement:

> 2\. Resolve `default_cvss_version`: if the parameter is `None`, read
>    from `settings_service.get_default_cvss_version(db)`. Call
>    `cvss.resolve_severity_score()` with the resolved version to
>    determine the new resolved score

### 4. `ticket-mutations.md` — `reconcile_ticket_status()` step 4: Simplify

Current step 4.1-4.2 (lines 206-215):

> 1\. Read `default_cvss_version` from
>    `settings_service.get_default_cvss_version(db)`. If this raises
>    (setting absent), the exception propagates — this indicates a
>    deployment error (migrations not applied). The transaction rolls
>    back and the inactive-state exit is aborted
> 2\. If `ticket.cve_id IS NOT NULL`: call
>    `recalculate_cvss_chain(ticket_id, default_cvss_version)`.
>    If `ticket.cve_id IS NULL`: skip (tickets without a CVE derive
>    severity from `severity_override`, not from CVSS assessments —
>    there is nothing to recalculate)

Replacement:

> 1\. If `ticket.cve_id IS NOT NULL`: call
>    `recalculate_cvss_chain(ticket_id)` (reads `default_cvss_version`
>    internally; if the setting is absent, the exception propagates —
>    this indicates a deployment error and the transaction rolls back).
>    If `ticket.cve_id IS NULL`: skip (tickets without a CVE derive
>    severity from `severity_override`, not from CVSS assessments —
>    there is nothing to recalculate)

Note: the step numbering within step 4 shifts (sub-step 3 "Enqueue
catch_up()" becomes sub-step 2).

### 5. `ticket-service.md` — `associate_cve()`: Simplify and fix `acting_user_id`

Current steps 8-9 (lines 246-253):

> 8\. Read `default_cvss_version` from
>    `settings_service.get_default_cvss_version(db)`
> 9\. Call `recalculate_cvss_chain(ticket_id, default_cvss_version)` —
>    recalculates severity (switching from `severity_override` to
>    CVSS-cascade-derived) and product eligibility using the CVE's
>    existing assessments, then calls `reconcile_ticket_status()`
>    internally. Gate #3 (severity set) and gate #4 (SUSE CVSS provided)
>    may now fail, causing regression to Analysis

Replacement (2 steps → 1 step):

> 8\. Call `recalculate_cvss_chain(ticket_id,
>    acting_user_id=acting_user_id)` — reads `default_cvss_version`
>    internally, recalculates severity (switching from
>    `severity_override` to CVSS-cascade-derived) and product eligibility
>    using the CVE's existing assessments, then calls
>    `reconcile_ticket_status()` internally. Gate #3 (severity set) and
>    gate #4 (SUSE CVSS provided) may now fail, causing regression to
>    Analysis

Note: this also fixes the pre-existing `acting_user_id` omission (see
"Follow-up: `acting_user_id` in `associate_cve()`" above — now resolved
within this draft's scope).

### 6. `cvss-scoring.md` — Batch task description: Minor update

Current (line 713-714):

> The task passes `default_cvss_version` as a mandatory parameter to
> every `recalculate_cvss_chain()` call.

Replacement:

> The task passes `default_cvss_version` explicitly to every
> `recalculate_cvss_chain()` call (overriding the internal read) to
> ensure all tickets in the batch use the same version.

### 7. `ticket-mutations.md` — `reconcile_ticket_status()` step 4: Fix recursion termination note

Current (lines 228-234):

> - **Recursion termination**: `recalculate_cvss_chain()` calls
>   `reconcile_ticket_status()` at its step 7. This nested call cannot
>   re-trigger step 4 because `effective_previous` in the inner call is
>   the ticket's current status (already set to Analysis or Analyzed by
>   the outer call), which is never in `{Resolved, Ignored, Duplicated}`.
>   No infinite recursion risk — the status converges (the inner call
>   either produces no change or a forward transition)

Replacement:

> - **Recursion termination**: `recalculate_cvss_chain()` calls
>   `reconcile_ticket_status()` at its step 7. This inner call may
>   re-trigger step 4 at most once: when the outer call set the ticket
>   to Resolved (gates satisfied with pre-inactivity data) and the
>   recalculation invalidates a gate, the inner call regresses the
>   ticket to Analysis with `effective_previous` = Resolved — which is
>   in the trigger set. The second `recalculate_cvss_chain()` call is
>   idempotent (same inputs within the same transaction), producing no
>   mutations. The innermost `reconcile_ticket_status()` sees an active
>   status (Analysis or Analyzed) as `effective_previous`, which is not
>   in the trigger set. Maximum recursion depth: 2 reconcile calls
>   (outer → inner → innermost no-op). No infinite recursion risk —
>   termination is guaranteed by idempotency of
>   `recalculate_cvss_chain()`

## Implementation Notes

When implementing this refactoring:

1. Apply spec modifications described in "Spec Modifications Plan" above
   to `ticket-mutations.md`, `ticket-service.md`, and `cvss-scoring.md`
2. Implement `recalculate_cvss_chain()` with optional
   `default_cvss_version` parameter in
   `backend/app/services/ticket_mutations.py`
3. Update all callers to use the new signature (omit
   `default_cvss_version` for non-batch callers; pass `acting_user_id`
   via keyword argument)
4. Verify that `recalculate_cvss_chain()` handles the case where no
   assessments exist (after delete) — severity should resolve to `None`
   and eligibility to 10.0 fallback. This is already the behavior of the
   resolution functions
5. Verify audit event ordering in tests (cause before effect)
6. Run existing test suite — all CVSS mutation tests should pass unchanged
   (behavioral equivalence)
