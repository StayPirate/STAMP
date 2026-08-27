# Product Lifecycle Transitions

## Purpose

Define the automated eligibility behavior when Product lifecycle data enters
or leaves Reactive Support, and the exclusion behavior when a Product reaches
End of Life (EOL) while it has actionable `TicketPackageProduct` records under
non-final tracks. Only records in active Tickets are processed. This
specification relies on the soft-deletion mechanism and track orphan cleanup
invariants in `package_service` (defined in
`docs/features/packages/package-service.md` and
`docs/features/packages/package-model.md`) that ensure tracks and packages are
automatically soft-deleted when they no longer have active children.

## Terminology

| Term | Definition |
|------|------------|
| **Lifecycle phase transition** | A Product moving between the lifecycle phases derived from its AIMAAS date projection |
| **EOL** | End of Life — the `eol` result of the lifecycle evaluator. Determined exclusively by AIMAAS lifecycle data, never by SMELT catalog presence |
| **Orphan track** | A `TicketPackageTrack` with zero remaining active (non-soft-deleted) `TicketPackageProduct` records |
| **Orphan package** | A `TicketPackage` with zero remaining active (non-soft-deleted) `TicketPackageTrack` records |

## EOL Determination

The authoritative criterion for whether a Product is EOL is exclusively the
lifecycle evaluator over AIMAAS-derived fields. Absence from a SMELT catalog
snapshot does not trigger EOL handling. The complete evaluator, including
inclusive date boundaries and missing or inconsistent data, is defined in
`docs/features/packages/product-catalog.md` (Lifecycle Evaluator).
An inconsistent date set produces an unavailable (`NULL`) phase and MUST NOT
produce `eol` accidentally. Other partial date sets follow the evaluator;
notably, a General Support end date can establish EOL even when FCS and later
phase dates are absent.

## Lifecycle Phase Detection

### Fetcher: `evaluate_lifecycle_transitions`

| Property | Value |
|----------|-------|
| Fetcher name | `evaluate_lifecycle_transitions` |
| Class name | `EvaluateLifecycleTransitions` |
| Schedule | Daily at 04:15 UTC (`15 4 * * *`) |
| Source | Local (no external source) |
| Scope | Products with lifecycle-driven automatic eligibility mismatches or actionable EOL records in active Tickets |
| Auth | N/A |
| `participates_in_catch_up` | `True` — participates in per-ticket catch-up on ticket reactivation |
| Custom settings | No |

The default schedule follows `sync_smelt_products` at 01:00 UTC,
`sync_aimaas_lifecycle` at 02:15 UTC, and `sync_aimaas_thresholds` at 02:45
UTC. In particular, the two-hour margin after lifecycle synchronization lets
the evaluator normally consume the latest lifecycle snapshot.

This ordering is not a hard task dependency. If a preceding fetcher fails or
runs long, lifecycle evaluation still runs against the latest committed valid
data. Calendar-driven transitions remain derivable from previously committed
dates, so a failed fresh synchronization must not suppress evaluation.

**Algorithm** (idempotent — no state, no cache):

1. Find Products with system-managed `TicketPackageProduct` records in active
   Tickets whose stored eligibility differs from the current lifecycle-driven
   eligibility result. This includes both entry into Reactive Support and a
   correction that moves a Product out of Reactive Support.
   - For each Product with a mismatch, enqueue
     `re_evaluate_product_eligibility(product_id, reason="reactive_ltss")`.
   - Candidate discovery may prefilter obvious mismatches, but the sub-task
     always recalculates from current persisted inputs under the Ticket lock.
2. Find all Products for which the lifecycle evaluator returns `eol`
   - For each: query `TicketPackageProduct` records whose parent
      `TicketPackageTrack` has status `AFFECTED` or `ANALYSIS` and
      `deleted_at IS NULL`, and whose own `deleted_at IS NULL`, in active
      tickets (status New, Analysis, or Analyzed)
   - Retain these as candidates for the separate EOL workflow. Its dispatch,
     forward mutation, and reversal contract are completed by the EOL decision
     that follows this eligibility-only checkpoint; no eligibility sub-task is
     used for EOL.
3. If no actionable records are found for a Product, no sub-task is enqueued.

Products whose evaluator result is `NULL`, `pre_release`, `general_support`,
or `extended_support` cannot enqueue EOL work. They can require eligibility
recalculation when the current threshold-derived value differs from a stored
value previously forced by Reactive Support. In particular, missing or
inconsistent dates never cause EOL handling, but a correction to `NULL` can
remove a prior Reactive Support eligibility effect.

**Idempotency**: repeated runs may enqueue duplicate work when state changes
concurrently, but the sub-task is idempotent and recalculates from current
persisted inputs. Once state has converged, later runs find no mismatch or EOL
action and enqueue nothing.

**Metrics reported**:

| Metric | Meaning |
|--------|---------|
| `record_updated` | Number of sub-tasks enqueued (products with records to process) |
| `record_failed` | Number of products where enqueue failed |

#### Catch-Up

`EvaluateLifecycleTransitions` implements `catch_up()` as a custom
override. See
[fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
contract.

**Scope**: extracts the ticket's `TicketPackageProduct` records and
applies lifecycle effects that could not run while the Ticket was inactive.
Eligibility itself does not require a second catch-up mutation:
`reconcile_ticket_status()` synchronously invokes
`ticket_mutations.recalculate_cvss_chain()` during inactive-to-active
transitions, and that chain recalculates every system-managed Product using
the current threshold and lifecycle phase before the catch-up tasks run.

The custom `catch_up()` therefore has no additional `threshold` or
`reactive_ltss` eligibility work. Its remaining responsibility is EOL
exclusion catch-up, whose per-Product mutation and reversal behavior is part of
the separate EOL decision. The complete custom `catch_up()` algorithm will be
defined with that EOL contract, not deferred to implementation.

### Sub-task: `re_evaluate_product_eligibility`

An on-demand Celery task (NOT a `BaseFetcher` — it is a sub-operation
triggered by parent fetchers, with no independent schedule).

**Parameters**:

| Parameter | Type | Meaning |
|-----------|------|---------|
| `product_id` | `UUID` | Catalog `Product.id` |
| `reason` | `Literal["threshold", "reactive_ltss"]` | Trigger recorded in changed-record audit events |

`eol` is deliberately not a valid reason. EOL processing changes exclusion
state and may trigger orphan cleanup; its forward and reversal contract is a
separate lifecycle decision rather than an eligibility recalculation.

**Orchestration**:

1. Validate `reason` before opening a database session. An unsupported value
   raises `ValueError` and performs no work.
2. In a read-only session, select the distinct IDs of active Tickets that have
   at least one `TicketPackageProduct` referencing the catalog Product.
   Candidate selection includes directly and effectively soft-deleted records;
   a Ticket containing only manual overrides may be selected and later produce
   a harmless no-op.
3. Process candidate Ticket IDs sequentially. For each ID, open a fresh
   session and transaction, call
   `package_service.recalculate_product_eligibility_for_ticket()`, and commit
   that Ticket independently. All matching occurrences of the catalog Product
   in that Ticket — including records under different packages or tracks — are
   handled together by that one Ticket transaction.
4. If a Ticket operation fails, roll back that Ticket, log a structured error
   containing the Ticket ID, Product ID, reason, and exception type, increment
   the task's in-memory failure count, and continue. Earlier successful Ticket
   transactions remain committed.
5. Log structured completion counts for candidate Tickets, successful
   transactions, inactive skips, no-ops, changed records, and failed Tickets.
   A Product with no candidate Tickets completes successfully as a no-op.

The task has no automatic Celery retry. A worker loss or per-Ticket failure is
recovered by the next complete parent lifecycle or threshold run, which
rediscovers remaining eligibility mismatches. An operator can trigger that
parent fetcher through the existing fetcher-operations API; no dedicated
endpoint for this internal task is introduced.
No Product-wide transaction, progress table, outbox, additional distributed
lock, or exactly-once delivery mechanism is introduced. Per-Ticket row locks
serialize concurrent mutations, and recomputation from current inputs makes
duplicate, delayed, and out-of-order invocations safe.

When one parent run finds multiple Products, it enqueues one independent task
per Product. Celery may execute those tasks concurrently within the existing
worker-concurrency limit; there is no Product-to-Product completion chain. If
two Product tasks touch the same Ticket, the Ticket row lock serializes their
transactions and the later transaction recomputes from current committed
state. Within each Product task, Tickets remain sequential. No Ticket chunking,
Celery group/chord, dedicated queue, or additional concurrency setting is
introduced; the per-Ticket commits and later mismatch scans provide bounded
failure isolation and convergence without extra orchestration.

**Behavior by reason**:

#### Reason: `reactive_ltss`

For each candidate Ticket, invoke
`package_service.recalculate_product_eligibility_for_ticket()` with reason
`reactive_ltss`. The service applies the complete current eligibility rules,
not an unconditional assignment to `false`: entry into Reactive Support can
change `true` to `false`, while corrected dates that move the Product out of
Reactive Support can restore the threshold-derived value. Manual overrides
remain untouched.

#### Reason: `threshold`

For each candidate Ticket, invoke the same automatic service operation with
reason `threshold`. It evaluates the Product's currently persisted threshold,
including the implicit-zero meaning of `NULL`; it does not trust the threshold
value that originally caused task dispatch. Manual overrides remain untouched.

**Note — CVSS-triggered eligibility recalculation**: changes to CVSS
assessments or the default CVSS version do NOT use this sub-task.
CVSS-triggered eligibility recalculation is executed synchronously and inline
by `ticket_mutations` within the CVSS mutation transaction. The rare
platform-wide batch after an Admin changes `default_cvss_version` also uses
`ticket_mutations.recalculate_cvss_chain()` once per Ticket; it is not a
caller of this Product-level task. See
[`cvss-scoring.md`](../tickets/cvss-scoring.md) (Recalculation Chain,
step 2) and `system-settings.md` (Impact of changing the default version).
This sub-task handles only Product-originated `reactive_ltss` and `threshold`
recalculation.

## Orphan Cleanup

When the separate EOL workflow soft-deletes a `TicketPackageProduct`, the
orphan cleanup invariants defined in
`docs/features/packages/package-service.md` (Orphan Cleanup Invariants)
apply upward: if the parent track has zero remaining products
with `deleted_at IS NULL` (not directly excluded), the track itself is
soft-deleted (`deleted_at` set on the track record only), and if the parent
package has zero remaining tracks with `deleted_at IS NULL`, the package
itself is soft-deleted. Each step produces a `TicketAuditEvent` (with
`user_id = NULL`). `reconcile_ticket_status()` is called once after the entire
orphan chain completes, as defined by `package_service`.

This is an upward chain only — child records are never modified. Each
soft-deletion sets `deleted_at` on the targeted record; descendants become
effectively excluded via the hierarchical exclusion model (see
`docs/features/packages/package-model.md`).

## TicketAuditEvent Records

All automated transitions and soft-deletions produce `TicketAuditEvent`
records with `user_id = NULL` (system action).

| Action | `event_type` | `old_value` | `new_value` | `detail.reason` |
|--------|--------------|-------------|-------------|-----------------|
| Product eligibility changed after a lifecycle recalculation | `product_eligibility_changed` | Previous boolean | Current computed boolean | `reactive_ltss` |
| Product eligibility changed after a threshold recalculation | `product_eligibility_changed` | Previous boolean | Current computed boolean | `threshold` |
| Product soft-deleted (AFFECTED, EOL) | `product_excluded` | Product display name | `NULL` | `eol` |
| Product soft-deleted (ANALYSIS, EOL) | `product_excluded` | Product display name | `NULL` | `eol` |
| Track soft-deleted (orphan) | `track_excluded` | Track reference | `NULL` | `orphan_cleanup` |
| Package soft-deleted (orphan) | `package_excluded` | Package name | `NULL` | `no_tracks_remaining` |

The `comment` field is NULL for these events. Other required keys in
`detail` follow `docs/features/tickets/ticket-audit-log.md`.

**Event types used**: `product_eligibility_changed` (existing),
`product_excluded`, `track_excluded`, and `package_excluded` (existing
soft-deletion event types). For system-initiated soft-deletions,
`user_id` is `NULL` (distinguishing them from VA-initiated exclusions
where `user_id` identifies the VA). See `docs/data-model.md` for the
full enum definition and `docs/features/tickets/ticket-audit-log.md` for
the field contract.

## Integration with Existing Tasks

### `sync_aimaas_thresholds`

After the complete threshold snapshot commits, enqueue
`re_evaluate_product_eligibility(product_id, reason="threshold")` once for
each Product whose persisted threshold changed, including clearing to `NULL`,
or whose current system-managed eligibility still differs from the complete
snapshot's result. No task is published from the open threshold transaction.
Dispatch failure is best-effort: log a structured warning containing the
Product ID and continue dispatching other Products. The committed threshold
remains authoritative; the next complete threshold run rediscovers remaining
mismatches. No durable dispatch state or dedicated recovery endpoint is added.

### `sync_aimaas_lifecycle`

Existing behavior unchanged: syncs lifecycle dates from AIMAAS. Does NOT
perform re-evaluation — phase detection is handled by
`evaluate_lifecycle_transitions`.

## Security

- `evaluate_lifecycle_transitions` is a system task, no user
  authentication involved
- `re_evaluate_product_eligibility` is an internal sub-task, not exposed
  via API
- All mutations go through `package_service` which enforces TicketAuditEvent
  creation
