# Product Lifecycle Transitions

## Purpose

Define the automated behavior when a product transitions to the Reactive
LTSS phase or reaches End of Life (EOL) while it has non-final
`TicketPackageProduct` records in active tickets. This specification
relies on the soft-deletion mechanism and track orphan cleanup invariants
in `package_service` (defined in `docs/features/packages/package-service.md`
and `docs/features/packages/package-model.md`) that ensure tracks and
packages are automatically soft-deleted when they no longer have active
children.

## Terminology

| Term | Definition |
|------|------------|
| **Lifecycle phase transition** | A product crossing a lifecycle date boundary (e.g., `today` exceeds `end_of_ltss`, moving from LTSS to Reactive LTSS) |
| **EOL** | End of Life — a product that has passed all applicable lifecycle dates. Determined exclusively by AIMAAS lifecycle dates, NOT by the `active` flag from SMELT |
| **Orphan track** | A `TicketPackageTrack` with zero remaining active (non-soft-deleted) `TicketPackageProduct` records |
| **Orphan package** | A `TicketPackage` with zero remaining active (non-soft-deleted) `TicketPackageTrack` records |

## EOL Determination

The authoritative criterion for whether a product is EOL is **exclusively
the lifecycle dates synced from AIMAAS**. A product is EOL when `today`
has surpassed all applicable (non-null) lifecycle end dates (`end_of_gs`,
`end_of_ltss`, `end_of_espos`, `end_of_reactive_ltss`).

The `active = false` flag (set by `sync_smelt_products` when a product is
no longer reported by SMELT) does NOT trigger EOL handling. That flag may
reflect temporary SMELT data issues and is not a reliable EOL signal.

## Lifecycle Phase Detection

### Fetcher: `evaluate_lifecycle_transitions`

| Property | Value |
|----------|-------|
| Fetcher name | `evaluate_lifecycle_transitions` |
| Class name | `EvaluateLifecycleTransitions` |
| Schedule | Daily at 04:00 UTC (`0 4 * * *`) |
| Source | Local (no external source) |
| Scope | Products in Reactive LTSS or EOL phase with actionable `TicketPackageProduct` records in active tickets |
| Auth | N/A |
| Custom settings | No |

Recommended to run after `sync_aimaas_lifecycle` and
`detect_ibs_track_releases`.

**Algorithm** (idempotent — no state, no cache):

1. Find all products currently in **Reactive LTSS** phase
   (`end_of_ltss < today < end_of_reactive_ltss`)
   - For each: query `TicketPackageProduct` records with `eligible = true`
      and `is_eligible_override = false` in active tickets (status New,
      Analysis, or Analyzed)
   - If any exist: enqueue
     `re_evaluate_product_eligibility(product_id, reason="reactive_ltss")`
2. Find all products currently in **EOL** phase (past all applicable
   lifecycle dates)
   - For each: query `TicketPackageProduct` records whose parent
      `TicketPackageTrack` has status `AFFECTED` or `ANALYSIS` in active
      tickets (status New, Analysis, or Analyzed)
   - If any exist: enqueue
     `re_evaluate_product_eligibility(product_id, reason="eol")`
3. If no actionable records found for a product, no sub-task is enqueued

**Idempotency**: if the task runs multiple times, subsequent executions
find no actionable records (already transitioned/removed) and enqueue
nothing.

**Metrics reported**:

| Metric | Meaning |
|--------|---------|
| `record_updated` | Number of sub-tasks enqueued (products with records to process) |
| `record_failed` | Number of products where enqueue failed |

**Schedule**: `0 4 * * *` (daily at 04:00 UTC)

#### Catch-Up

`EvaluateLifecycleTransitions` implements `catch_up()` as a custom
override. See
[fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
contract.

**Scope**: extracts the ticket's `TicketPackageProduct` records and
re-evaluates lifecycle phase and eligibility for each product. While
the ticket was inactive, products may have transitioned between
lifecycle phases (e.g., entered LTSS or reached end-of-life),
affecting eligibility thresholds.

**Detailed specification**: to be defined during implementation.

### Sub-task: `re_evaluate_product_eligibility`

An on-demand Celery task (NOT a `BaseFetcher` — it is a sub-operation
triggered by parent fetchers, with no independent schedule).

**Parameters**: `product_id: UUID`, `reason: str`

**Behavior by reason**:

#### Reason: `reactive_ltss`

For all `TicketPackageProduct` records referencing this product in open
tickets with `eligible = true` and `is_eligible_override = false`:

- Call `package_service.set_product_eligibility(record, eligible=false)`

Only the `eligible` flag is changed. Records with
`is_eligible_override = true` are not modified (already filtered out by
the query).

#### Reason: `eol`

For all `TicketPackageProduct` records referencing this product in open
tickets whose parent `TicketPackageTrack` has a non-final status
(`AFFECTED` or `ANALYSIS`):

- Soft-delete the product: call
  `package_service.soft_delete_ticket_package_product(record)` with a
  `TicketAuditEvent` (`user_id = NULL`, `comment` includes `eol` reason)

Products under tracks with a final status (`NOT_AFFECTED`, `FIXED`,
`WONT_FIX`) are not modified.

#### Reason: `threshold_change`

For all `TicketPackageProduct` records referencing this product in open
tickets: re-evaluate eligibility based on the new threshold value.
Existing behavior as specified in `docs/features/packages/package-model.md`.

**Note — CVSS-triggered eligibility recalculation**: changes to CVSS
assessments or the default CVSS version do NOT use this sub-task.
CVSS-triggered eligibility recalculation is executed synchronously and
inline by `ticket_mutations` within the CVSS mutation transaction. See
[`cvss-scoring.md`](../tickets/cvss-scoring.md) (Recalculation Chain,
step 2) for details. This sub-task handles exclusively
lifecycle-triggered re-evaluation (`reactive_ltss`, `threshold_change`,
`eol`).

## Orphan Cleanup

When `re_evaluate_product_eligibility` soft-deletes a `TicketPackageProduct`
(for EOL), the orphan cleanup invariants defined in
`docs/features/packages/package-service.md` (Orphan Cleanup Invariants)
apply upward: if the parent track has zero remaining products
with `deleted_at IS NULL` (not directly excluded), the track itself is
soft-deleted (`deleted_at` set on the track record only), and if the parent
package has zero remaining tracks with `deleted_at IS NULL`, the package
itself is soft-deleted. Each step produces a `TicketAuditEvent` (with
`user_id = NULL`) and calls `reconcile_ticket_status`.

This is an upward chain only — child records are never modified. Each
soft-deletion sets `deleted_at` on the targeted record; descendants become
effectively excluded via the hierarchical exclusion model (see
`docs/features/packages/package-model.md`).

## TicketAuditEvent Records

All automated transitions and soft-deletions produce `TicketAuditEvent`
records with `user_id = NULL` (system action).

| Action | `event_type` | `old_value` | `new_value` | `comment` |
|--------|--------------|-------------|-------------|-----------|
| Product eligibility set to false (Reactive LTSS) | `product_eligibility_changed` | `true` | `false` | `track_name package_name product_id reactive_ltss` |
| Product soft-deleted (AFFECTED, EOL) | `product_excluded` | Product display name | `NULL` | `track_name package_name product_id eol` |
| Product soft-deleted (ANALYSIS, EOL) | `product_excluded` | Product display name | `NULL` | `track_name package_name product_id eol` |
| Track soft-deleted (orphan) | `track_excluded` | Track reference | `NULL` | `track_name package_name no_products_remaining` |
| Package soft-deleted (orphan) | `package_excluded` | Package name | `NULL` | `package_name no_tracks_remaining` |

**Event types used**: `product_eligibility_changed` (existing),
`product_excluded`, `track_excluded`, and `package_excluded` (existing
soft-deletion event types). For system-initiated soft-deletions,
`user_id` is `NULL` (distinguishing them from VA-initiated exclusions
where `user_id` identifies the VA). See `docs/data-model.md` for the
full enum definition and `docs/features/tickets/ticket-audit-log.md` for
the field contract.

## Integration with Existing Tasks

### `sync_aimaas_thresholds`

Existing behavior unchanged: when a product's threshold changes, enqueue
`re_evaluate_product_eligibility(product_id, reason="threshold_change")`.

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
