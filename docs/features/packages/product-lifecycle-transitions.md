# Product Lifecycle Transitions

## Purpose

Define the automated behavior when a product transitions to the Reactive
LTSS phase or reaches End of Life (EOL) while it has non-final
`TicketPackageProduct` records in active tickets. This specification
relies on the soft-deletion mechanism and track orphan cleanup invariants
in `ticket_mutations` (defined in `docs/features/tickets/tickets.md` and
`docs/features/packages/package-tracking.md`) that ensure tracks and
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

### Background Task: `check_lifecycle_phase_transitions`

A `BaseFetcher` subclass that runs daily (recommended schedule: 04:00 UTC,
after `sync_aimaas_lifecycle` and `check_ibs_track_releases`).

**Responsibility**: find products currently in Reactive LTSS or EOL phase
that still have actionable `TicketPackageProduct` records, and enqueue
re-evaluation.

**Algorithm** (idempotent — no state, no cache):

1. Find all products currently in **Reactive LTSS** phase
   (`end_of_ltss < today < end_of_reactive_ltss`)
   - For each: query `TicketPackageProduct` records with status `AFFECTED`
     in active tickets (status New, Analysis, or Analyzed; `deleted_at IS NULL`)
   - If any exist: enqueue
     `re_evaluate_product_eligibility(product_id, reason="reactive_ltss")`
2. Find all products currently in **EOL** phase (past all applicable
   lifecycle dates)
   - For each: query `TicketPackageProduct` records with status `AFFECTED`
     or `ANALYSIS` in active tickets (status New, Analysis, or Analyzed; `deleted_at IS NULL`)
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

### Sub-task: `re_evaluate_product_eligibility`

An on-demand Celery task (NOT a `BaseFetcher` — it is a sub-operation
triggered by parent fetchers, with no independent schedule).

**Parameters**: `product_id: UUID`, `reason: str`

**Behavior by reason**:

#### Reason: `reactive_ltss`

For all `TicketPackageProduct` records referencing this product in open
tickets with status `AFFECTED`:

- Call `ticket_mutations.set_product_eligibility(record, eligible=false)`

The product status remains `AFFECTED`. Only the `eligible` flag is
changed. Records with `is_eligible_override = true` are not modified.
Records in other statuses are not modified (eligibility is meaningful
only when status is `AFFECTED`).

#### Reason: `eol`

For all `TicketPackageProduct` records referencing this product in open
tickets with non-final, non-protected status:

| Current status | Action |
|----------------|--------|
| `AFFECTED` | Soft-delete the product: call `ticket_mutations.soft_delete_product(record)` with a `TicketEvent` (`user_id = NULL`, `comment` includes `eol` reason) |
| `ANALYSIS` | Soft-delete the product: call `ticket_mutations.soft_delete_product(record)` with a `TicketEvent` (`user_id = NULL`, `comment` includes `eol` reason) |

Records in final status (`NOT_AFFECTED`, `FIXED`) or protected status
(`WONT_FIX`) are not modified.

#### Reason: `threshold_change` / `cvss_change`

Existing behavior as specified in `docs/features/packages/package-tracking.md` and
`docs/features/tickets/cvss-scoring.md`.

## Cascading Cleanup

When `re_evaluate_product_eligibility` soft-deletes a `TicketPackageProduct`
(for EOL), the track orphan cleanup invariants defined in
`docs/features/tickets/tickets.md` (Ticket Mutations Module, "Orphan Cleanup
Invariants") automatically cascade: if the parent track has zero
remaining active (non-soft-deleted) products it is soft-deleted (with
`deleted_at` set to the current timestamp), and if the parent package has zero
remaining active tracks it is soft-deleted. Each step produces
a `TicketEvent` and calls `evaluate_ticket_status`.

## TicketEvent Records

All automated transitions and soft-deletions produce `TicketEvent`
records with `user_id = NULL` (system action).

| Action | `event_type` | `old_value` | `new_value` | `comment` |
|--------|--------------|-------------|-------------|-----------|
| Product eligibility set to false (Reactive LTSS) | `product_eligibility_changed` | `true` | `false` | `package_name:product_id:reactive_ltss` |
| Product soft-deleted (AFFECTED, EOL) | `product_excluded` | `NULL` | `NULL` | `package_name:product_id:eol` |
| Product soft-deleted (ANALYSIS, EOL) | `product_excluded` | `NULL` | `NULL` | `package_name:product_id:eol` |
| Track soft-deleted (orphan) | `track_excluded` | `NULL` | `NULL` | `package_name:reference:no_products_remaining` |
| Package soft-deleted (orphan) | `package_excluded` | `NULL` | `NULL` | `package_name:no_tracks_remaining` |

**Event types used**: `product_eligibility_changed` (existing),
`product_excluded`, `track_excluded`, and `package_excluded` (existing
soft-deletion event types). For system-initiated soft-deletions,
`user_id` is `NULL` (distinguishing them from VA-initiated exclusions
where `user_id` identifies the VA). See `docs/data-model.md` for the
full enum definition and `docs/features/tickets/ticket-history.md` for
the field contract.

## Integration with Existing Tasks

### `sync_aimaas_thresholds`

Existing behavior unchanged: when a product's threshold changes, enqueue
`re_evaluate_product_eligibility(product_id, reason="threshold_change")`.

### `sync_aimaas_lifecycle`

Existing behavior unchanged: syncs lifecycle dates from AIMAAS. Does NOT
perform re-evaluation — phase detection is handled by
`check_lifecycle_phase_transitions`.

## Protected States

Consistent with the rest of the system, `WONT_FIX` is NEVER modified
by automatic transitions. Records with status `WONT_FIX` are not
soft-deleted or have their eligibility changed by lifecycle transitions.

## Security

- `check_lifecycle_phase_transitions` is a system task, no user
  authentication involved
- `re_evaluate_product_eligibility` is an internal sub-task, not exposed
  via API
- All mutations go through `ticket_mutations` which enforces TicketEvent
  creation
