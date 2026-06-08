# Draft: Per-Ticket Catch-Up Architecture

## Status

**Draft** — agreed in principle, pending application.

## Dependencies

- **`docs/drafts/redhat-cvss-fetcher-spec-changes.md` (Change #7)**:
  the Fetcher Inventory assumes `SyncRedhatCves` implements
  `fetch_single(cve_id)`, which enables the default `catch_up()`. This
  depends on the Red Hat fetcher draft being applied first (or
  concurrently). Without it, `SyncRedhatCves` has no `fetch_single()`
  and the default `catch_up()` would be a no-op for that fetcher.

## Problem

The current spec in `fetcher-infrastructure.md` defines two mechanisms
with confusingly similar names that serve different use cases:

### Mechanism A: `fetch_single(cve_id)` — On-demand Single-Item Fetch

- **Defined at**: `fetcher-infrastructure.md` lines 199-310
- **What it is**: a method on `BaseFetcher` subclasses
- **Parameter**: `cve_id: str`
- **Purpose**: fetch data for a single CVE from an external source
  (discovery or enrichment)
- **Auto-discovered**: via `get_fetch_single_fetchers()` registry
  accessor
- **Invoked by**: `trigger_on_demand_fetch()` in `cve-service.md` —
  when Sentinel encounters an unknown CVE-ID during ticket creation or
  CVE association
- **Required for**: all CVE fetchers with `cve_source_type`
- **Implemented by**: `SyncNvdCves` (canonical example), `SyncMitreCves`,
  `SyncKernelCves`
- **Not implemented by**: `SyncRedhatCves` (currently presented as
  "enrichment fetcher (no `fetch_single`)")

### Mechanism B: `fetch_single_<fetcher_name>(ticket_id)` — Per-Ticket Catch-Up

- **Defined at**: `fetcher-infrastructure.md` lines 435-516
- **What it is**: a standalone Celery task (not a method on
  `BaseFetcher`)
- **Parameter**: `ticket_id: str`
- **Purpose**: catch up on data missed during ticket inactivity
  (Ignored, Duplicated, or Resolved → active)
- **Registration**: "class attribute or registry entry (to be defined
  during implementation)" — never finalized
- **Invoked by**: ticket reactivation hooks in `ticket-service.md`
  (un-ignore, un-duplicate) and `ticket-mutations.md` (regression from
  Resolved)
- **Applicable to**: any fetcher whose scope is filtered by ticket
  status
- **Only explicit definition**: `fetch_single_redhat(ticket_id)` in
  `cvss-scoring.md` line 772 — marked "template, to be finalized"

### Issues

1. **Naming confusion**: both are called `fetch_single` but have
   different parameters (`cve_id` vs `ticket_id`), different scopes,
   and different invocation points
2. **Mechanism B registration is undefined**: the spec says "to be
   defined during implementation" — never finalized
3. **Mechanism B is specified for only one fetcher**: only
   `fetch_single_redhat` is explicitly defined, and it is a template.
   IBS release detection and submission tracking are mentioned
   aspirationally in `cvss-scoring.md` line 708 but never specified
4. **Mechanism B as standalone Celery tasks** creates per-fetcher
   boilerplate — each fetcher needs its own task definition, whereas
   mechanism A uses a generic wrapper (`fetch_single_cve`)
5. **Misplaced documentation**: `cvss-scoring.md` lines 704-711
   describes the catch-up mechanism for all fetchers (including IBS
   release detection, submission tracking) in a CVSS-specific spec

## Solution

Replace mechanism B with a `catch_up(ticket_id)` method on
`BaseFetcher`, auto-discoverable via registry, with a default
implementation for CVE fetchers.

### Three methods on `BaseFetcher`

```python
class BaseFetcher:
    async def execute(self, session: AsyncSession) -> None:
        """Scheduled batch — processes the full scope."""
        ...  # abstract, required

    async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:
        """On-demand single-CVE fetch (discovery/enrichment).

        Required for CVE fetchers. Not applicable to non-CVE fetchers.
        The system discovers fetchers implementing this method via
        get_fetch_single_fetchers() and invokes them in parallel for
        on-demand CVE lookup.
        """
        ...  # optional, required for CVE fetchers

    async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
        """Per-ticket catch-up after reactivation.

        Called when a ticket transitions from an inactive status
        (Ignored, Duplicated, Resolved) to an active status. The
        fetcher retrieves data that was missed during the inactive
        period.

        Optional. Only applicable to fetchers whose execute() scope
        is filtered by ticket status. Global fetchers (product catalog,
        AD sync, etc.) do not implement this.
        """
        ...  # optional
```

### Default `catch_up` for CVE fetchers

`BaseFetcher` provides a default implementation of `catch_up()` that
delegates to `fetch_single()` for CVE fetchers:

```python
class BaseFetcher:
    async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
        """Default: extract cve_id from ticket, call fetch_single().

        CVE fetchers that implement fetch_single() inherit this
        default catch_up() automatically. Non-CVE fetchers override
        with custom logic.
        """
        if 'fetch_single' not in type(self).__dict__:
            return  # no fetch_single, no default catch_up
        ticket = await session.get(Ticket, UUID(ticket_id))
        if ticket and ticket.cve_id:
            await self.fetch_single(str(ticket.cve_id), session)
```

This means CVE fetchers only need to implement `fetch_single(cve_id)`:
- `execute()` calls `self.fetch_single()` in a loop over active CVEs
- `catch_up()` is derived automatically

Non-CVE fetchers override `catch_up()` with custom logic specific to
their data domain.

### Registry accessor: `get_catch_up_fetchers()`

```python
def get_catch_up_fetchers() -> dict[str, type[BaseFetcher]]:
    """Return fetchers implementing catch_up(), keyed by fetcher name.

    A fetcher "implements catch_up" if:
    - It defines catch_up() in its own __dict__ (explicit override), OR
    - It defines fetch_single() in its own __dict__ (inherits the
      default catch_up from BaseFetcher)
    """
    ...
```

The detection predicate combines two checks:

1. `'catch_up' in cls.__dict__` — fetcher explicitly overrides
   `catch_up()` (non-CVE fetchers like `DetectIbsTrackReleases`)
2. `'fetch_single' in cls.__dict__` — fetcher implements
   `fetch_single()`, which means it inherits the default `catch_up()`
   (CVE fetchers like `SyncRedhatCves`, `SyncNvdCves`)

Fetchers that match neither condition (global fetchers) are excluded.

### Celery task wrapper

A single generic Celery task wraps all `catch_up()` invocations:

```python
@celery_app.task
def run_catch_up(fetcher_name: str, ticket_id: str) -> None:
    """Generic catch-up task — replaces per-fetcher tasks."""
    fetcher_cls = FETCHER_REGISTRY.get(fetcher_name)
    if fetcher_cls is None:
        logger.error("run_catch_up: unknown fetcher %s — skipping", fetcher_name)
        return  # non-retryable — fetcher was removed between enqueue and execution
    fetcher = fetcher_cls()
    async def _run():
        async with get_async_session() as session:
            await fetcher.catch_up(ticket_id, session)
    async_run(_run())
```

This replaces the per-fetcher `fetch_single_<name>(ticket_id)` Celery
tasks. A single task definition handles all fetchers. If
`fetcher_name` is not found in the registry (e.g., a deployment
removed the fetcher between enqueue and execution), the task logs an
error and returns without retry.

### Interface contract for `catch_up()`

- **Parameter**: `ticket_id` (UUID as string)
- **Idempotent**: if external data is unchanged, no side effects
- **Mutation path**: when changed data is found, persists through the
  normal mutation path (service modules), which triggers the standard
  chain (audit events, reconciliation)
- **No direct ticket mutations**: MUST NOT acquire `FOR UPDATE` locks
  on the Ticket row — delegates to the appropriate service module
- **No `FetcherRun` record**: `catch_up()` is a sub-operation, not a
  full fetcher execution
- **Session management**: the `run_catch_up` Celery task wrapper
  creates and manages the `AsyncSession`, following the same pattern
  as `fetch_single_cve`. The session is passed to `catch_up()` as a
  parameter. Transaction boundaries depend on the implementation:
  - **Default `catch_up()`** (CVE fetchers): single transaction —
    reads the ticket, calls `fetch_single()`, commits on return. Same
    semantics as `fetch_single_cve`
  - **Custom `catch_up()` overrides** (non-CVE fetchers): the method
    receives the session for read-only queries (ticket lookup, item
    enumeration). Mutations on each item are delegated to the
    appropriate service module, which manages its own transaction
    lifecycle. Each item MUST be committed independently so that a
    failure on item N does not roll back items 1..N-1
- **Error handling**:
  - **Retry policy**: the `run_catch_up` Celery task wrapper applies
    the same retry policy as `fetch_single_cve` (3 retries with
    exponential backoff). This applies uniformly to all fetchers.
    The retry is reserved for **infrastructure failure** (external
    service completely unreachable)
  - **CVE fetchers** (default `catch_up()`): the `fetch_single`
    signaling convention applies (CVENotInSource → no-op, transient
    errors → retry)
  - **Non-CVE fetchers** (custom `catch_up()` override): MUST use
    per-item error handling — if one item (track, product, package)
    fails, continue with the remaining items rather than aborting the
    entire catch-up. Detailed error categorization is defined in each
    fetcher's own specification
  - **Raise/return contract for non-CVE overrides**: custom
    `catch_up()` overrides MUST catch per-item exceptions internally.
    The method MUST only propagate an exception when all items have
    failed, indicating infrastructure failure (e.g., the external
    service is completely unreachable). Partial failure (some items
    succeed, some fail) MUST result in a normal return — the failed
    items are logged per-item and will be recovered by the next
    periodic `execute()` run. This ensures the Celery-level retry is
    reserved for cases where retrying has a realistic chance of
    success
- **Post-commit enqueue**: `run_catch_up` tasks MUST be enqueued
  after the caller's transaction commits, consistent with the
  post-commit enqueue pattern used by `trigger_on_demand_fetch()`.
  Enqueuing before commit risks catch-up tasks running against
  uncommitted data (e.g., a newly added package that triggered a
  regression from Resolved)
- **Concurrency safety**: no guard on ticket status is required before
  executing `catch_up()`. If a ticket is re-deactivated (re-ignored,
  re-duplicated) after catch-up tasks are enqueued but before they
  execute, the tasks run to completion. This is safe by design:
  mutations produced by `catch_up()` are factually correct
  (the external data is real regardless of ticket status), and
  `reconcile_ticket_status()` respects the current ticket status —
  it will not transition an Ignored or Duplicated ticket. Duplicate
  enqueuing (e.g., two rapid reactivations) is also safe because
  `catch_up()` is idempotent

### Invocation points

`catch_up()` is enqueued exclusively by **ticket reactivation** hooks:

- `ticket_service.reopen_from_ignored()`: after un-ignore
- `ticket_service.revert_duplicate()`: after un-duplicate
- `ticket_mutations.reconcile_ticket_status()`: after regression from
  Resolved to an active status

At each invocation point, the system calls
`get_catch_up_fetchers()` and enqueues a `run_catch_up` Celery task
for each registered fetcher.

**Removed invocation point**: the current spec
(`fetcher-infrastructure.md` lines 574-580) also lists `cve_service`
endpoint handlers (after CVE association or ticket creation with CVE)
as an invocation point for the per-ticket `fetch_single`. This
invocation point is **not carried over** to `catch_up()` because:

1. CVE fetchers at CVE association time are already dispatched by
   `trigger_on_demand_fetch()` via `fetch_single(cve_id)` (Mechanism
   A). Once `SyncRedhatCves` implements `fetch_single(cve_id)` (per
   the Red Hat fetcher draft), all CVE fetchers are covered by
   `trigger_on_demand_fetch()` — the separate enrichment dispatch
   becomes redundant.
2. Non-CVE fetchers (IBS release detection, submission tracking,
   bugowner) have nothing to catch up on at CVE association time —
   packages have not been added to the ticket yet.

This makes `catch_up()` semantically clean: it handles exclusively
the recovery of missed data after a period of ticket inactivity.

**Note**: the rationale above (why the `cve_service` invocation point
was removed) is decision context for this draft. It MUST NOT be
carried into `fetcher-infrastructure.md` — the final spec should
document the invocation points as they are, not explain which
invocation points were considered and rejected.

## Fetcher Inventory

### Fetchers that need `catch_up()` (scope filtered by ticket status)

| Fetcher | `execute()` scope filter | `catch_up()` type | What catch-up does |
|---|---|---|---|
| `sync_redhat_cves` | CVEs with active tickets | **Default** (via `fetch_single`) `*` | Extract `cve_id` → call Red Hat API → upsert CVSS/CWE/refs/packages |
| `sync_nvd_cves` | All CVEs (global) — but has `fetch_single` | **Default** (via `fetch_single`) | Already has `fetch_single` for on-demand CVE discovery; catch-up is free |
| `sync_mitre_cves` | All CVEs (global) — but has `fetch_single` | **Default** (via `fetch_single`) | Same as NVD |
| `sync_kernel_cves` | All CVEs (global) — but has `fetch_single` | **Default** (via `fetch_single`) | Same as NVD |
| `detect_ibs_track_releases` | Tracks in active tickets | **Custom override** | Extract ticket's `TicketPackageTrack` records → check IBS for releases on each codestream |
| `detect_ibs_product_releases` | Products in active tickets | **Custom override** | Extract ticket's `TicketPackageProduct` records → check `updateinfo.xml` for advisories |
| `sync_ibs_requests` | Codestreams in active tickets | **Custom override** | Extract ticket's codestream names → query IBS Request Search API → correlate SRs/RRs |
| `evaluate_lifecycle_transitions` | Products in active tickets | **Custom override** | Extract ticket's products → re-evaluate lifecycle phase and eligibility |
| `sync_ibs_bugowners` | Packages in active tickets | **Custom override** | Extract ticket's package names → refresh bugowner cache for each |

`*` `sync_redhat_cves` does not currently implement
`fetch_single(cve_id)`. The default `catch_up()` depends on the Red
Hat fetcher draft (`redhat-cvss-fetcher-spec-changes.md`, Change #7)
being applied. Without it, this fetcher has no `fetch_single()` and
the default `catch_up()` would be a no-op.

Note: for NVD, MITRE, and kernel CVE fetchers, `execute()` is global
(not filtered by ticket status), but they still benefit from
`catch_up()` because their `fetch_single()` method already exists for
on-demand discovery. The default `catch_up()` gives them ticket
reactivation support for free.

### Fetchers that do NOT need `catch_up()` (global scope)

| Fetcher | Why no catch-up needed |
|---|---|
| `sync_smelt_products` | Syncs entire product catalog regardless of ticket state |
| `sync_aimaas_lifecycle` | Syncs all product lifecycle dates |
| `sync_aimaas_thresholds` | Syncs all CVSS thresholds |
| `sync_ldap_directory` | Syncs all employee records |
| `sync_cisa_kev` | Syncs entire KEV catalog |
| `sync_epss_scores` | Syncs all EPSS scores |
| `sync_ghsa_advisories` | Syncs all GHSA advisories |
| `sync_osv_advisories` | Syncs all OSV advisories |
| `aggregate_fetcher_runs` | Maintenance — no external data, no ticket scope |

## Spec Changes Required

### 1. `docs/features/platform/fetcher-infrastructure.md`

**Section "Per-Ticket Catch-Up: `fetch_single` Capability" (lines
435-516)**: rewrite entirely:

- Rename to "Per-Ticket Catch-Up: `catch_up()` Method"
- Replace the standalone Celery task interface (`fetch_single_<fetcher_name>(ticket_id)`) with the `catch_up()` method on `BaseFetcher`
- Document the default implementation for CVE fetchers
- Document `get_catch_up_fetchers()` registry accessor
- Document the generic `run_catch_up` Celery task wrapper
- Update the interface contract (same constraints, new method name)
- Update invocation points (unchanged, just reference new method name)
- Update Applicability section to reference the fetcher inventory

**Section "On-demand Single-Item Fetch" (lines 199-310)**: unchanged.
`fetch_single(cve_id)` remains as-is. Only add a note that CVE
fetchers implementing `fetch_single()` automatically get `catch_up()`
via the default implementation.

**Sub-operation contract consolidation**: the `catch_up()` and
`fetch_single` interface contracts share several rules (idempotency,
mutation path, no direct ticket mutations, no `FetcherRun`, sub-operation
classification). When rewriting the "Per-Ticket Catch-Up" section,
consider extracting the shared rules into a common "Sub-Operation
Common Rules" sub-section that both `fetch_single` and `catch_up()`
reference, to avoid maintaining two near-identical contracts.

**Import-time validation rules**: add a rule for `catch_up()`:
- If a fetcher defines `catch_up()` in its `__dict__`, it must accept
  `(self, ticket_id: str, session: AsyncSession) -> None`
- If a non-CVE fetcher needs catch-up, it MUST define `catch_up()`
  explicitly (the default only works for fetchers with `fetch_single`)

### 2. `docs/features/tickets/cvss-scoring.md`

**Section "Ticket Reactivation: CVSS Catch-Up" (lines 695-722)**:

Remove the cross-cutting description of the catch-up mechanism for all
fetchers (lines 704-711 mentioning IBS release detection and
submission tracking). Replace with:

1. Keep the synchronous part (lines 697-702) — `recalculate_cvss_chain()`
   within the reactivation transaction
2. For the asynchronous part: replace with a cross-reference to
   `fetcher-infrastructure.md` ("Per-Ticket Catch-Up: `catch_up()`
   Method") and `ticket-service.md` (reactivation hooks). Do not list
   individual fetchers — that belongs in `fetcher-infrastructure.md`

This fixes the misplaced information: the catch-up mechanism is a
cross-cutting concern owned by `fetcher-infrastructure.md`, not by
`cvss-scoring.md`.

### 3. `docs/features/tickets/cvss-scoring.md`

**Section "Sub-operation: `fetch_single_redhat`" (lines 772-801)**:
remove entirely (already planned in the Red Hat fetcher draft, change
0). Replaced by `fetch_single()` method on `SyncRedhatCves` in
`cve-tracking.md`, plus automatic `catch_up()` from base class.

### 4. `docs/features/tickets/ticket-service.md`

**Lines 536-544**: update the reactivation hook description to
reference `catch_up()` instead of `fetch_single`:

Current:
> enqueue `fetch_single` for every registered fetcher that exposes the
> capability, passing the `ticket_id`

Updated:
> enqueue `catch_up()` for every registered fetcher via
> `get_catch_up_fetchers()`, passing the `ticket_id`

### 5. `docs/features/tickets/ticket-mutations.md`

**Line 258**: update the post-regression hook to reference `catch_up()`
instead of `fetch_single`.

### 6. `docs/features/tickets/cve-service.md`

**Lines 906-925**: remove the "enrichment catch-up" step (step 3 of
the post-dispatch sequence) from the CVE association and ticket
creation flows. This step currently enqueues `fetch_single` for all
capable fetchers, passing `ticket_id`. It is no longer needed because:

1. CVE-level enrichment is fully covered by
   `trigger_on_demand_fetch()` (step 1), which dispatches all CVE
   fetchers implementing `fetch_single(cve_id)` — including
   `SyncRedhatCves` once the Red Hat fetcher draft is applied
2. Non-CVE fetchers have no data to fetch at CVE association time
   (packages are not yet on the ticket)

The two remaining steps are unchanged:

1. `trigger_on_demand_fetch(cve_id)` — CVE-level discovery and
   enrichment (Mechanism A, unchanged)
2. `recalculate_cvss_chain(ticket_id)` — derived data from existing
   CVSS assessments

The "Relationship between the three dispatch steps" explanation
(lines 916-925) is updated to reflect two steps instead of three.

Note: `trigger_on_demand_fetch()` (lines 666-830) is **unchanged** —
it uses `fetch_single(cve_id)` via `get_fetch_single_fetchers()`, which
is Mechanism A and not affected by this refactoring.

### 7. `docs/features/tickets/cve-tracking.md`

**No changes required.** All `fetch_single` references in this spec
are Mechanism A (`fetch_single(cve_id)` method on `BaseFetcher`) and
remain unchanged. The rename of Mechanism B to `catch_up()` makes
`fetch_single` unambiguous in this spec — it always means the
per-CVE on-demand fetch method.

### 8. Feature specs for non-CVE fetchers

The following specs will need a new "Catch-Up" subsection in their
fetcher definition, documenting their custom `catch_up()` override:

- `docs/features/packages/ibs-track-release-detection.md` — what
  `catch_up()` does for `DetectIbsTrackReleases`
- `docs/features/packages/ibs-product-release-detection.md` — what
  `catch_up()` does for `DetectIbsProductReleases`
- `docs/features/packages/ibs-submission-tracking.md` — what
  `catch_up()` does for `SyncIbsRequests`
- `docs/features/packages/product-lifecycle-transitions.md` — what
  `catch_up()` does for `EvaluateLifecycleTransitions`
- `docs/features/packages/package-bugowner.md` — what `catch_up()`
  does for `SyncIbsBugowners`

These are new sections to be added — the catch-up logic for these
fetchers has not been specified before (only mentioned aspirationally).
Their detailed specification is out of scope for this draft and should
be defined when each fetcher is implemented.

## Application Strategy

1. Update `fetcher-infrastructure.md` first (establish the new
   architecture)
2. Update `cvss-scoring.md` (remove misplaced information, fix
   cross-references)
3. Update `ticket-service.md` and `ticket-mutations.md` (rename
   invocation points)
4. Update `cve-service.md` (remove enrichment catch-up step).
   **Ordering note**: this step removes the enrichment catch-up
   dispatch that currently covers `SyncRedhatCves` at CVE association
   time. The Red Hat fetcher draft (step 6) adds `fetch_single(cve_id)`
   to `SyncRedhatCves`, which makes `trigger_on_demand_fetch()` cover
   Red Hat automatically. To avoid a window where Red Hat enrichment
   at CVE association is lost, steps 4 and 6 should be applied
   together (in the same session or PR)
5. Non-CVE fetcher specs: add placeholder "Catch-Up" sections noting
   that the `catch_up()` override is to be specified during
   implementation
6. Coordinate with the Red Hat fetcher draft
   (`redhat-cvss-fetcher-spec-changes.md`): Change #7 (adding
   `fetch_single(cve_id)` to `SyncRedhatCves`) should be applied
   after step 1 and together with step 4 — it depends on the
   `catch_up()` architecture being established in
   `fetcher-infrastructure.md`
7. Add a finding to `docs/reviews/ticket-mutations.md` for the
   inherited gap in the post-regression hook: the current spec
   delegates regression detection to **callers** of
   `reconcile_ticket_status()` (each caller must check old vs new
   status and enqueue catch-up tasks), but does not enumerate which
   callers are responsible. A new caller of
   `reconcile_ticket_status()` could omit the post-regression hook
   and silently skip catch-up enqueuing. This gap predates this
   draft — it is not introduced by the `catch_up()` refactoring —
   but should be tracked as an open finding for future resolution
   (e.g., moving the detection inside `reconcile_ticket_status()`
   itself)
8. Run `@spec-coherence-reviewer` on affected specs to verify
   consistency
9. Run `@docs-placement-reviewer` on `fetcher-infrastructure.md` to
   verify that the catch-up mechanism is correctly placed and that
   no cross-cutting rules leaked into individual fetcher specs
10. Delete this draft file (`docs/drafts/catch-up-architecture.md`)
    once all spec changes have been applied and reviewed
