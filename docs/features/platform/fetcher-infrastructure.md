# Fetcher Infrastructure

## Purpose

This document defines the mandatory infrastructure that all data fetchers
in Sentinel must use. Fetchers are background tasks that periodically
pull data from external sources (NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS)
and update the local database. It covers the generic `BaseFetcher`
abstract base class: the fetcher registry, Celery integration,
concurrency control, the per-ticket `catch_up()` mechanism, custom
settings schema, error message sanitization, BaseFetcher HTTP client
integration, data model, and data retention.

The fetcher infrastructure is documented across several complementary
specifications — see the Related Specifications section below for the
full map. For the monitoring dashboard (API endpoints, frontend pages,
CLI diagnostics) that consumes this infrastructure, see
`docs/features/platform/fetcher-operations.md`.

## Terminology

| Term | Definition |
|---|---|
| **Fetcher** | A background task that retrieves data from an external source and creates/updates local records. Implemented as a subclass of `BaseFetcher`. |
| **Run** | A single execution of a fetcher, tracked from start to finish with metrics (duration, item counts, status). |
| **Registry** | An in-memory dictionary of all registered fetcher classes, populated automatically via `BaseFetcher` auto-discovery. |
| **Cursor** | Used in two distinct senses: (1) **Conceptual cursor** — any mechanism a fetcher uses to determine where to resume on the next run (timestamp, page token, commit SHA, offset). A fetcher classified as "cursor-based" uses some form of incremental checkpoint. (2) **`FetcherRun.cursor` column** — the optional JSONB column in the `FetcherRun` table, used only by fetchers that need structured checkpoint data not representable by the scalar fields of `FetcherRun` (e.g., git commit SHA + commit date). Fetchers that use `started_at` as their checkpoint (e.g., NVD, GHSA) are conceptually cursor-based but leave the JSONB column NULL. |

## Related Specifications

This document specifies the generic `BaseFetcher` contract. The
fetcher infrastructure is documented across five complementary specs:

| Spec | Content |
|---|---|
| **This document** | BaseFetcher base class, naming, error sanitization, custom settings, catch_up mechanism (generic), BaseFetcher HTTP client integration (lazy property, overrides, lifecycle), registry, Celery, concurrency, stale run detection, data model, retention, deregistered lifecycle, doc requirements |
| `cve-fetcher-infrastructure.md` | BaseCVEFetcher class, on-demand fetch_single, CVE source type identity, CVE catch_up default, CVE conventions |
| `git-fetcher-infrastructure.md` | BaseGitFetcher class, git_operations.py, clone/delta infrastructure |
| `networking.md` | Shared HTTP client factory, transport retry, TLS trust store (cross-cutting) |
| `fetcher-operations.md` | Monitoring dashboard, API endpoints, CLI diagnostics |

## BaseFetcher Base Class

All fetchers MUST inherit from `BaseFetcher`, an abstract base class in
`backend/app/services/base_fetcher.py`. The base class provides:

1. **Auto-registration**: a metaclass or `__init_subclass__` hook that
   automatically registers each concrete fetcher in a global registry
   keyed by the fetcher's `name` property
2. **Run lifecycle management**: a `run()` method (not meant to be
   overridden) that wraps the fetcher's `execute()` method with:

   Signature:

   ```python
   async def run(
       self,
       *,
       triggered_by: str = "schedule",
       triggered_by_user_id: UUID | None = None,
       run_id: UUID | None = None,
   ) -> None:
   ```

   `run()` manages its own database sessions internally — callers do
   not pass a session. Each database operation (record creation,
   finalization) uses a short-lived session. The connection is not held
   open during `execute()`. The session passed to `execute()` may be
   committed and rolled back multiple times during execution (per-item
   transaction boundaries). This is a documented pattern for both
   git-based and API-based CVE fetchers — see
   `docs/features/platform/cve-fetcher-infrastructure.md` (Session
   Lifecycle for API-based CVE Fetchers) and
   `docs/features/platform/git-fetcher-infrastructure.md` (BaseGitFetcher
   Class, step 10, transaction boundaries).

   - **FetcherRun record acquisition**:
     - When `run_id` is `None` (scheduled runs): creates a new
       `FetcherRun` record with `status = running`, `triggered_by` and
       `triggered_by_user_id` set from the corresponding parameters
     - When `run_id` is provided (API trigger): retrieves the existing
       `FetcherRun` record (created synchronously by the API trigger
       endpoint). The record already has `status = running` and its
       `triggered_by`/`triggered_by_user_id` fields already set.
       `run()` continues its lifecycle without creating a new record
   - Reset of all metric counters (`items_created`, `items_updated`,
     `items_failed`) to zero before each execution. This ensures correct
     behavior regardless of instance lifecycle (singleton vs. per-run
     instantiation)
   - Automatic `started_at` timestamp capture
   - Automatic `finished_at` timestamp and `duration_seconds` calculation
   - Exception handling: if `execute()` raises, the run is marked `failure`
     with `error_message`, `error_detail`, and `error_traceback` populated
     (see "Error Message Sanitization" for the three-tier field
     architecture)
         - Final status determined by status precedence rules (see below)
   - **Cursor persistence**: if `execute()` returns normally, the final
     status is `success` or `partial`, and `self._cursor` is set (a
     dict), `run()` writes it to the `FetcherRun.cursor` column in the
     same transaction that sets `status` and `finished_at`. Cursor is
     NOT written when: `self._cursor` is None (not set), `execute()`
     raised an exception (failure path), or the all-items-failed safety
     check triggers (status set to `failure` despite normal return). See
     `docs/features/platform/git-fetcher-infrastructure.md` (Cursor Persistence) for the full mechanism
     and query pattern
   - **Status determination precedence**: the final status is assigned as
     follows (evaluated in order):
     1. If `execute()` raises an exception: `failure`. Metric counters are
        preserved for diagnostics but do not influence the status
     2. If `execute()` returns normally and all items failed
        (`items_failed > 0` and `items_created + items_updated == 0`):
        `failure`. `error_message` is set to
        `"All {items_failed} items failed"`. `error_detail` and
        `error_traceback` are NULL (no exception). The cursor is NOT
        persisted (same behavior as exception-driven failure)
     3. If `execute()` returns normally and `items_failed > 0` (with at
        least one item created or updated): `partial`
     4. Otherwise: `success`
3. **Metric helpers**: methods that concrete fetchers call within their
   `execute()` to report work done:
   - `self.record_created(count=1)` — increment `items_created`
   - `self.record_updated(count=1)` — increment `items_updated`
   - `self.record_failed(count=1)` — increment `items_failed`
4. **Enabled check**: before executing, `run()` checks `FetcherConfig` for
   the fetcher. If `enabled` is `false`:
   - If a pre-existing `FetcherRun` record was passed via `run_id` (manual
     trigger case), `run()` updates it to `status = failure`,
     `error_message = 'Fetcher disabled between trigger and execution'`,
     `finished_at = now()`, `duration_seconds = 0`, then returns. This
     prevents the record from remaining in `running` status indefinitely
   - Otherwise (scheduled run, no pre-existing record), the run is
     skipped — no `FetcherRun` record is created, the task returns
     immediately
   In both cases, a DEBUG-level log is emitted:
   `logger.debug("Fetcher '%s' is disabled — skipping run", self.name)`
5. **Shared HTTP client**: a pre-configured `self.http_client` lazy
   property for outgoing HTTP requests. See "BaseFetcher HTTP Client
   Integration" section for the local integration, and `networking.md`
   ("Shared HTTP Client") for the full client factory specification.

**FetcherRun creation failure**: if the database INSERT for the `FetcherRun`
record fails (e.g., database connection error), the task MUST:

1. Log a CRITICAL-level message:
   `CRITICAL: Fetcher '{name}' aborted — failed to create FetcherRun record before execution: {error}`
2. Re-raise the exception immediately — Celery does NOT retry top-level
   fetcher tasks

No `FetcherRun` record is produced (since the database is unreachable).
Visibility of this failure is provided by: application logs (CRITICAL level)
and the Celery result backend (task marked as FAILED). Recovery happens at
the next scheduled cycle — no explicit Celery retry is configured for
top-level fetcher tasks.

**FetcherRun retrieval failure**: when `run_id` is provided (API-trigger
flow), the task retrieves an existing `FetcherRun` record instead of creating
one. Two failure modes apply:

1. **Database unreachable during retrieval** — same behavior as creation
   failure: log a CRITICAL-level message
   (`CRITICAL: Fetcher '{name}' aborted — failed to retrieve FetcherRun record '{run_id}': {error}`)
   and re-raise the exception immediately without retry.
2. **Record not found** (valid UUID but no corresponding row exists) — log an
   ERROR-level message
   (`ERROR: Fetcher '{name}' aborted — FetcherRun '{run_id}' not found`)
   and raise an appropriate exception (e.g., `ValueError`) without retry.

In both cases, visibility is provided by: application logs and the Celery
result backend (task marked as FAILED). No explicit Celery retry is configured.

## Abstract Interface

Concrete fetchers MUST implement:

```python
class SyncExampleData(BaseFetcher):
    name: str = "sync_example_data"      # unique identifier, snake_case, max 100 chars
    description: str = "Human-readable description"
    default_schedule: str = "0 */6 * * *"  # cron expression (every 6h)
    default_request_delay: float = 0  # Optional: initial request_delay at auto-registration
    queue: str | None = None  # Optional: Celery queue name (default = default queue)

    # Optional: per-fetcher operational parameters configurable at
    # runtime via the admin dashboard. See "Custom Settings Schema"
    # section below for the schema format and validation rules.
    class Settings(BaseModel):  # optional inner class
        ...

    async def execute(self, session: AsyncSession) -> None:
        """Fetch data from the external source.

        Use self.record_created(), self.record_updated(), and
        self.record_failed() to report metrics.
        """
        ...
```

**CVE fetcher example** — discovery fetcher (implements `fetch_single`):

CVE fetchers inherit from `BaseCVEFetcher` (see
`docs/features/platform/cve-fetcher-infrastructure.md`), which
additionally requires `cve_source_type` and provides
optional `fetch_single()` (override required when
`supports_fetch_single = True`, the default).

```python
class SyncNvdCves(BaseCVEFetcher):
    name = "sync_nvd_cves"                 # registry key (BaseFetcher contract)
    cve_source_type = "nvd"                # CVESourceType identifier (BaseCVEFetcher contract)
    description = "Sync CVEs from NVD REST API v2"
    default_schedule = "0 */6 * * *"
    source_reference_url_pattern = "https://nvd.nist.gov/vuln/detail/{cve_id}"

    async def fetch_single(self, cve_id: str, session: AsyncSession) -> PostIngestTasks | None:
        ...

    async def execute(self, session: AsyncSession) -> None:
        ...
```

**CVE fetcher example** — delegates `execute()` to `fetch_single()`:

```python
class SyncRedhatCves(BaseCVEFetcher):
    name = "sync_redhat_cves"              # registry key
    cve_source_type = "redhat"             # CVESourceType identifier
    description = "Sync CVE data from Red Hat Security API"
    default_schedule = "0 3 * * *"

    async def fetch_single(self, cve_id: str, session: AsyncSession) -> PostIngestTasks | None:
        # Core per-CVE logic: call Red Hat API, upsert CVSS/CWE/refs
        await upsert_cve(db, cve_id, source=self.cve_source_type, ...)

    async def execute(self, session: AsyncSession) -> None:
        for cve_id in active_ticket_cve_ids:
            await self.fetch_single(cve_id, session)
```

The `name` attribute MUST NOT exceed **100 characters**. This limit is
imposed by the `VARCHAR(100)` column type used for `fetcher_name` across
the `FetcherConfig` (PK), `FetcherRun`, and `FetcherAuditEvent` tables.
The `name` value also propagates to
`TicketReference.source` (`VARCHAR(100)`) for automatic references
created by CVE fetchers — exceeding the limit would cause a database
constraint violation.

### Naming Convention

This convention applies exclusively to `BaseFetcher` subclasses — the
background tasks registered in the fetcher infrastructure and visible in
the fetcher dashboard. It does NOT apply to sub-operation Celery tasks
exempt from `BaseFetcher` per Guardrail 14, on-demand service methods,
non-fetcher Celery tasks, or continuous consumers.

#### Pattern: `<verb>_<source>_<noun>`

All fetcher names follow the pattern `verb_source_noun`, which reads as
a natural English compound noun: "sync NVD CVEs", "detect IBS releases".

**Verbs** — three operational categories:

| Verb | Meaning | When to use |
|------|---------|-------------|
| `sync` | Periodic data pull from an external source | Any fetcher that imports or refreshes data from a remote service |
| `detect` | Condition or state change verification against an external source | Release detection, event monitoring, or any fetcher that checks whether a specific condition has changed in an external system |
| `evaluate` | Local computation, no external source | Lifecycle transitions, recalculations, or any fetcher that derives new state from data already in the database |

**Source** — identifies the external system. For local fetchers
(`evaluate`), this segment is omitted and the pattern reduces to
`verb_noun`:

| Source | External system |
|--------|----------------|
| `nvd` | NIST NVD |
| `mitre` | MITRE CVE Services |
| `redhat` | Red Hat Security Data API |
| `kernel` | Linux Kernel CNA |
| `ghsa` | GitHub Advisory Database |
| `osv` | OSV (osv.dev) |
| `cisa` | CISA (KEV catalog) |
| `epss` | FIRST.org EPSS |
| `smelt` | SMELT |
| `aimaas` | AIMAAS |
| `ibs` | IBS (build.suse.de) |
| `ldap` | SUSE Active Directory (LDAP protocol) |

New sources follow the same rule: use the shortest unambiguous lowercase
identifier for the external system.

**Noun** — describes what data is being synced, detected, or evaluated.
Use the most general accurate term:

- `cves` — all CVE-related data the source provides (CVSS, CWE,
  references, affected versions, etc.). Do NOT narrow the noun to a
  single data type when the fetcher extracts multiple types from the
  same API call
- `advisories` — security advisories from sources whose primary unit
  is an advisory (not a CVE record). Use when the source publishes
  advisory objects that Sentinel maps to CVE records
- `scores` — single-purpose numerical scores (e.g., EPSS)
- `products` — product catalog records
- `lifecycle` — product lifecycle dates
- `thresholds` — CVSS thresholds
- `kev` — CISA Known Exploited Vulnerabilities catalog entries
- `track_releases` — codestream-level release detection
- `product_releases` — product-level release detection

#### Class Name Derivation

The Python class name is derived mechanically from the fetcher name by
converting `snake_case` to `PascalCase` (e.g., `sync_nvd_cves` →
`SyncNvdCves`). No suffixes like `Fetcher` or `Sync` are added — the
class name IS the PascalCase form of the fetcher name, nothing more.

**Acronym casing**: all segments are title-cased regardless of whether
they are acronyms — `nvd` → `Nvd`, not `NVD`; `ibs` → `Ibs`, not
`IBS`; `ghsa` → `Ghsa`, not `GHSA`. This keeps the derivation
mechanical and unambiguous.

#### Batch Naming (Specification Files)

When multiple fetchers serve the same feature domain performing the same
action on different sources (e.g., 6+ CVE sync fetchers), use
`<domain>-<action>-<source>.md` (e.g., `cve-sync-nvd.md`,
`cve-sync-ghsa.md`) to ensure alphabetical grouping in directory
listings. The shared prefix replaces the source-first pattern for
discoverability.

This convention applies to **specification filenames** only. Fetcher
class names and Celery task names follow their own naming convention
(`<verb>_<source>_<noun>`) independently.

## Per-Ticket Catch-Up: `catch_up()` Method

Fetchers whose `execute()` scope is filtered by ticket status (e.g.,
`sync_redhat_cves` scopes to CVEs with active tickets) skip inactive
tickets during periodic runs. When a ticket is reactivated (from
Ignored, Duplicated, or Resolved), the system enqueues per-ticket
catch-up tasks to recover data missed during the inactive period.

The catch-up mechanism is a method on `BaseFetcher`:

```python
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
    ...
```

### Override-point contract

`BaseFetcher` defines `catch_up()` as an override point that raises
`NotImplementedError`:

```python
class BaseFetcher:
    async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
        """Per-ticket catch-up after reactivation.

        Override point. BaseCVEFetcher provides the default
        implementation for CVE fetchers. Non-CVE fetchers override
        with custom logic. Direct BaseFetcher subclasses that need
        catch-up MUST define catch_up() explicitly.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement catch_up()"
        )
```

`NotImplementedError` from `catch_up()` is a programming error
(incorrect invocation on a fetcher without a real implementation), not a
transient infrastructure failure. The `run_catch_up` task wrapper MUST
NOT retry it — `NotImplementedError` is in the non-retryable exception
set alongside `CVENotInSource` (see
`docs/features/platform/cve-fetcher-infrastructure.md`).

For the concrete default implementation inherited by CVE fetchers
(delegation to `fetch_single()`), see
`docs/features/platform/cve-fetcher-infrastructure.md` (Default
catch_up Implementation).

**Boundary conditions for custom `catch_up()` overrides** (applies to
all fetchers — CVE and non-CVE):

- **Custom `catch_up()` overrides** MUST apply equivalent guards:
  check that the ticket exists and that the relevant data is present
  (e.g., `TicketPackageTrack` records for IBS track detection) before
  proceeding. If the ticket does not exist or has no relevant data,
  the method MUST return silently (no exception, no log warning)

Non-CVE fetchers override `catch_up()` with custom logic specific to
their data domain.

### Registry accessor: `get_catch_up_fetchers()`

```python
def get_catch_up_fetchers() -> dict[str, type[BaseFetcher]]:
    """Return fetchers implementing catch_up(), keyed by fetcher name.

    A fetcher "implements catch_up" if:
    - It is a BaseCVEFetcher subclass with participates_in_catch_up=True
      (inherits the default catch_up from BaseCVEFetcher), OR
    - It defines catch_up() in its own __dict__ (explicit override —
      non-CVE fetchers)
    """
    ...
```

The detection predicate:

```python
fetchers = {}
for name, cls in FETCHER_REGISTRY.items():
    if issubclass(cls, BaseCVEFetcher) and cls.participates_in_catch_up:
        # Inherits default catch_up from BaseCVEFetcher, not opted out
        fetchers[name] = cls
    elif 'catch_up' in cls.__dict__:
        # Explicit override (non-CVE fetchers)
        fetchers[name] = cls
return fetchers
```

The `participates_in_catch_up` class attribute (default `True` on
`BaseCVEFetcher`) allows global-scope CVE fetchers to opt out of
catch-up while still inheriting the full `BaseCVEFetcher` contract.

Fetchers that match neither condition (global non-CVE fetchers) are
excluded.

**Caching semantics**: computed on each call from the current registry
state (not cached at import time). The returned dict MUST NOT be mutated
by callers (return `types.MappingProxyType`).
A `_clear_catch_up_cache()` test helper MUST be provided to invalidate
any internal state in test suites that dynamically register mock fetcher
classes.

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
    # Enabled check: if fetcher is disabled, skip silently
    config = get_fetcher_config(fetcher_name)
    if config and not config.enabled:
        logger.info("Catch-up skipped for %s: fetcher is disabled", fetcher_name)
        return  # task completes successfully, no error, no retry
    fetcher = fetcher_cls()
    async def _run():
        async with get_async_session() as session:
            await fetcher.catch_up(ticket_id, session)
    async_run(_run())
```

If `fetcher_name` is not found in the registry (e.g., a deployment
removed the fetcher between enqueue and execution), the task logs an
error and returns without retry.

**Non-retryable exceptions**: `NotImplementedError` and `CVENotInSource`
are in the non-retryable exception set. `NotImplementedError` indicates a
programming error (incorrect invocation on a fetcher without a real
`catch_up()` implementation). `CVENotInSource` is caught internally by
the default `BaseCVEFetcher.catch_up()` and should never propagate — if
it does, it indicates a custom override that forgot to catch it.

### Interface contract

`catch_up()` shares the same sub-operation classification as
`fetch_single()` (see
`docs/features/platform/cve-fetcher-infrastructure.md`, On-demand
Single-Item Fetch): no
`FetcherRun` record, no metric reporting, not a `BaseFetcher`
execution. The following additional rules apply:

- **Parameter**: `ticket_id` (UUID as string)
- **Idempotent**: if external data is unchanged, no side effects
- **Mutation path**: when changed data is found, persists through the
  normal mutation path (service modules), which triggers the standard
  chain (audit events, reconciliation)
- **No direct ticket mutations**: MUST NOT acquire `FOR UPDATE` locks
  on the Ticket row — delegates to the appropriate service module
- **Session management**: the `run_catch_up` Celery task wrapper
  creates and manages the `AsyncSession`, following the same pattern
  as `fetch_single_cve`. The session is passed to `catch_up()` as a
  parameter. Transaction boundaries depend on the implementation:
  - **Default `catch_up()`** (CVE fetchers): reads the ticket, calls
    `fetch_single()`, then commits via `self.commit_and_dispatch()`
    internally — not via `run_catch_up` on return
  - **Custom `catch_up()` overrides** (non-CVE fetchers): the method
    receives the session for read-only queries (ticket lookup, item
    enumeration). Mutations on each item are delegated to the
    appropriate service module, which manages its own transaction
    lifecycle. Each item MUST be committed independently so that a
    failure on item N does not roll back items 1..N-1.
    Non-CVE fetchers that mutate data MUST obtain independent
    sessions (via `get_async_session()`) for each item's mutation —
    the session parameter passed by `run_catch_up` is for read-only
    queries only. Writing through the passed session would place all
    items in a single transaction, violating the per-item commit
    requirement
- **Error handling**:
  - **Retry policy**: the `run_catch_up` Celery task wrapper applies
    the same retry policy as `fetch_single_cve` (3 retries with
    exponential backoff). Reserved for infrastructure failure
    (external service completely unreachable)
  - **CVE fetchers** (default `catch_up()`): the default
    `catch_up()` implementation MUST catch `CVENotInSource` internally
    and treat it as a no-op (the CVE is not in this source — nothing
    to catch up on). `CVENotInSource` MUST NOT propagate to the
    `run_catch_up` wrapper. Transient errors (network, HTTP 5xx)
    propagate to the wrapper for retry
  - **Non-CVE fetchers** (custom `catch_up()` override): MUST use
    per-item error handling — if one item (track, product, package)
    fails, continue with the remaining items rather than aborting the
    entire catch-up. Detailed error categorization is defined in each
    fetcher's own specification
  - **Raise/return contract for non-CVE overrides**: custom
    `catch_up()` overrides MUST catch per-item exceptions internally.
    The method MUST only propagate an exception when all items have
    failed, indicating infrastructure failure. Partial failure (some
    items succeed, some fail) MUST result in a normal return — the
    failed items are logged per-item and will be recovered by the next
    periodic `execute()` run
- **Post-commit enqueue**: `run_catch_up` tasks MUST be enqueued
  after the caller's transaction commits, consistent with the
  post-commit enqueue pattern used by `trigger_on_demand_fetch()`.
  Enqueuing before commit risks catch-up tasks running against
  uncommitted data.
  **Exception**: when enqueued from within `reconcile_ticket_status()`
  as part of the internalized post-transition catch-up (step 4), the
  enqueue occurs before the caller's commit. This is safe because:
  (1) `catch_up()` does not read ticket status as a precondition,
  (2) `catch_up()` is idempotent by contract, (3) mutations produced
  by catch-up delegate to service modules that acquire independent
  locks and respect the ticket's committed state at execution time
- **Concurrency safety**: no guard on ticket status is required before
  executing `catch_up()`. If a ticket is re-deactivated after catch-up
  tasks are enqueued but before they execute, the tasks run to
  completion. This is safe by design: mutations produced by
  `catch_up()` are factually correct (the external data is real
  regardless of ticket status), and `reconcile_ticket_status()`
  respects the current ticket status. Duplicate enqueuing (e.g., two
  rapid reactivations) is also safe because `catch_up()` is idempotent.
  **Concurrent catch-up and periodic execution**: if a ticket is
  reactivated shortly before a periodic `execute()` run, both
  `catch_up()` and `execute()` may call `fetch_single()` for the same
  CVE concurrently. This is safe — `upsert_cve()` uses `FOR UPDATE`
  locks and unique constraints, so the second call is a no-op or an
  idempotent update. The duplicated external API call is acceptable
  given the low frequency of reactivation events relative to periodic
  schedules

### Invocation points

`catch_up()` is enqueued exclusively by `reconcile_ticket_status()`
(step 4) when it detects an inactive-state exit (Resolved, Ignored, or
Duplicated → active). All inactive → active transitions converge on
this single invocation point:

- Gate-driven regression: Resolved → active (automatic)
- Un-ignore: Ignored → active (via `_reenter_gate_zone()`)
- Un-duplicate: Duplicated → active (via `_reenter_gate_zone()`)

At the invocation point, the system calls `get_catch_up_fetchers()` and
enqueues a `run_catch_up` Celery task for each registered fetcher.

### Fetcher inventory

#### Fetchers that implement `catch_up()` (scope filtered by ticket status)

| Fetcher | `execute()` scope filter | `catch_up()` type | What catch-up does |
|---|---|---|---|
| `sync_redhat_cves` | CVEs with active tickets | **Inherited from `BaseCVEFetcher`** | Extract `cve_id` → call Red Hat API → upsert CVSS/CWE/refs/packages |
| `sync_epss_scores` | CVEs with active tickets | **Inherited from `BaseCVEFetcher`** | Extract `cve_id` → call EPSS API → upsert score/percentile |
| `sync_nvd_cves` | All CVEs (global) — but has `fetch_single` | **Inherited from `BaseCVEFetcher`** | Already has `fetch_single` for on-demand discovery; catch-up is free |
| `sync_mitre_cves` | All CVEs (global) — but has `fetch_single` | **Inherited from `BaseCVEFetcher`** | Same as NVD |
| `sync_kernel_cves` | All CVEs (global) — but has `fetch_single` | **Inherited from `BaseCVEFetcher`** | Same as NVD |
| `sync_ghsa_advisories` | All advisories (global) — but has `fetch_single` | **Inherited from `BaseCVEFetcher`** | Same as NVD |
| `sync_osv_advisories` | CVEs with active tickets | **Inherited from `BaseCVEFetcher`** | Extract `cve_id` → call OSV API → upsert affected versions/refs/packages |
| `detect_ibs_track_releases` | Tracks in active tickets | **Custom override** | Extract ticket's `TicketPackageTrack` records → check IBS for releases on each codestream |
| `detect_ibs_product_releases` | Products in active tickets | **Custom override** | Extract ticket's `TicketPackageProduct` records → check `updateinfo.xml` for advisories |
| `sync_ibs_requests` | Codestreams in active tickets | **Custom override** | Extract ticket's codestream names → query IBS Request Search API → correlate SRs/RRs |
| `evaluate_lifecycle_transitions` | Products in active tickets | **Custom override** | Extract ticket's products → re-evaluate lifecycle phase and eligibility |
| `sync_ibs_bugowners` | Packages in active tickets | **Custom override** | Extract ticket's package names → refresh bugowner cache for each |

Note: for NVD, MITRE, and kernel CVE fetchers, `execute()` is global
(not filtered by ticket status), but they still benefit from
`catch_up()` because their `fetch_single()` method already exists for
on-demand discovery. The default `catch_up()` inherited from
`BaseCVEFetcher` gives them ticket reactivation support for free.

#### Fetchers that do NOT need `catch_up()` (global scope)

| Fetcher | Why no catch-up needed |
|---|---|
| `sync_smelt_products` | Syncs entire product catalog regardless of ticket state |
| `sync_aimaas_lifecycle` | Syncs all product lifecycle dates |
| `sync_aimaas_thresholds` | Syncs all CVSS thresholds |
| `sync_ldap_directory` | Syncs all employee records |
| `sync_cisa_kev` | Syncs entire KEV catalog (sets `participates_in_catch_up = False`) |

Note: `sync_cisa_kev` inherits from `BaseCVEFetcher` but opts out of
catch-up via `participates_in_catch_up = False` because its `execute()`
syncs the entire catalog on every run — there is no gap to recover after
ticket reactivation. It also sets `supports_fetch_single = False` because
CISA KEV is a monolithic catalog with no per-CVE API — the
`fetch_single_cve` task is never dispatched for this fetcher. In contrast,
`sync_nvd_cves`, `sync_mitre_cves`, `sync_kernel_cves`,
`sync_ghsa_advisories`, `sync_osv_advisories`, `sync_redhat_cves`, and
`sync_epss_scores` participate in catch-up because their `fetch_single()`
provides immediate per-ticket recovery without waiting for the next
periodic run.


## Error Message Sanitization

The `error_message` field in `FetcherRun` is visible to **all users**
(including unauthenticated callers via the fetcher dashboard). Raw Python
exception messages often contain infrastructure details — internal
hostnames, IP addresses, file paths, connection strings, or service
names — that MUST NOT be exposed publicly.

Sentinel uses a **three-tier error field architecture**:

| Field | Audience | Content |
|-------|----------|---------|
| `error_message` | All users (public) | Intentional, sanitized message written by the developer or by BaseFetcher's generic fallback |
| `error_detail` | `manage_fetchers` only | Raw exception message (`str(exception)`) |
| `error_traceback` | `manage_fetchers` only | Full Python traceback |

### Fetcher responsibilities

Each concrete fetcher MUST catch known exceptions in its `execute()`
method and raise a `FetcherError` with a sanitized message that describes
the failure without revealing infrastructure details:

```python
async def execute(self, session: AsyncSession) -> None:
    try:
        response = await self.http_client.get(IBS_API_URL)
    except ConnectionError as e:
        raise FetcherError("Failed to connect to IBS") from e
    except HTTPStatusError as e:
        raise FetcherError(f"IBS returned HTTP {e.response.status_code}") from e
```

`FetcherError` is a dedicated exception class provided by the fetcher
infrastructure module. When `BaseFetcher.run()` catches a `FetcherError`,
it stores the exception message in `error_message` (public) and
`str(exception.__cause__)` in `error_detail` (visible only with
`manage_fetchers` capability). If
`__cause__` is `None` (no chained exception), `error_detail` is set to
`NULL`.

### BaseFetcher fallback

When `execute()` raises an exception that is NOT a `FetcherError` (i.e.,
an unhandled exception), `BaseFetcher.run()` applies a **generic category
fallback** — it maps the exception type to a safe, generic message:

| Exception category | `error_message` |
|--------------------|-----------------|
| `ConnectionError`, `Timeout` | `"External service unreachable"` |
| `HTTPStatusError` (4xx) | `"External service rejected request"` |
| `HTTPStatusError` (5xx) | `"External service returned server error"` |
| `SoftTimeLimitExceeded` | `"Execution timed out"` |
| Any other exception | `"Unexpected error"` |

In all cases, `error_detail` receives `str(exception)` and
`error_traceback` receives the full traceback.

### What constitutes infrastructure details

Error messages MUST NOT contain:

- Internal hostnames (e.g., `build.suse.de`, `pan.suse.de`,
  `smelt.suse.de`)
- IP addresses or port numbers
- File system paths
- Database or Redis connection strings
- API keys, tokens, or credentials
- Internal URL paths beyond the service name

### Referencing error handling in fetcher specifications

Feature specifications that define fetchers MUST include an "Error
Handling" section documenting which exceptions the fetcher catches and
what sanitized messages it produces. The `@fetcher-compliance-reviewer`
agent verifies this documentation exists.

Fetchers that only interact with the local database (e.g.,
`evaluate_lifecycle_transitions`) are exempt from this requirement —
their failure modes do not involve external service details.

Error handling is one of the mandatory sections in the minimum
documentation template — see "Fetcher Documentation Requirements" below
for the full template.

## Custom Settings Schema

Fetcher-specific operational parameters (throttle delays, retry counts,
lookback windows, retention periods) can be declared by each fetcher and
managed at runtime through the admin dashboard without worker restart.

This mechanism complements the generic `FetcherConfig` fields (`enabled`,
`schedule_override`, `run_timeout`, `request_delay`) which apply
uniformly to all fetchers. Custom settings are fetcher-specific — each
fetcher declares its own schema and reads its own values.

### What belongs in custom_settings

Operational parameters that:

- Are safe to change at runtime without security implications
- May need tuning based on workload or external service behavior
- Benefit from visibility and changeability through the admin dashboard

### What stays as environment variables

| Category | Examples | Reason |
|----------|----------|--------|
| Credentials | `IBS_USERNAME`, `IBS_PASSWORD`, `NVD_API_KEY` | Secrets — managed via env vars / Kubernetes Secrets |
| Connection URIs | `LDAP_URI`, `IBS_API_URL` | Infrastructure — changes with deployment environment |
| TLS configuration | `SUSE_CA_CERT_PATH` | Infrastructure — tied to certificate management |

### Schema declaration

Each `BaseFetcher` subclass MAY declare an inner class named `Settings`
that inherits from `pydantic.BaseModel`. If not declared (or set to
`None`), the fetcher accepts no custom settings and the
`custom_settings` JSONB column in `FetcherConfig` remains `{}`.

```python
from pydantic import BaseModel, Field


class SyncNvdCves(BaseCVEFetcher):
    name = "sync_nvd_cves"
    description = "Sync CVE data from NVD"
    default_schedule = "0 */6 * * *"

    class Settings(BaseModel):
        results_per_page: int = Field(
            default=2000,
            ge=100,
            le=2000,
            description="Number of CVE records per API page.",
        )
```

### Supported field types and constraints

Settings fields are limited to scalar types. Pydantic `Field()`
constraints express all validation rules declaratively:

| Type | Supported constraints |
|------|----------------------|
| `int` | `ge`, `le`, `gt`, `lt` |
| `float` | `ge`, `le`, `gt`, `lt` |
| `str` | `max_length`, `pattern`, `json_schema_extra={"choices": [...]}` |
| `bool` | (no additional constraints) |

Nested objects, lists, and complex structures are not supported — only
scalar fields are allowed in the `Settings` model. This is enforced at
import time (see below).

To declare allowed choices for string or integer fields, use
`json_schema_extra`:

```python
class Settings(BaseModel):
    output_format: str = Field(
        default="json",
        json_schema_extra={"choices": ["json", "xml", "csv"]},
        description="Response format preference.",
    )
```

To attach a safety warning for dangerous settings, use
`json_schema_extra`:

```python
class Settings(BaseModel):
    max_concurrent_requests: int = Field(
        default=5,
        ge=1,
        le=50,
        json_schema_extra={"warning": "Values above 20 may trigger rate limiting on the external service."},
        description="Maximum parallel HTTP requests.",
    )
```

### Import-time validation

The following rules are enforced at **import time** by
`BaseFetcher.__init_subclass__`. If any rule is violated, the worker
fails to start with a clear error message identifying the fetcher and
the invalid field.

1. The fetcher's `name` MUST be unique across the entire registry. If a
   concrete fetcher declares a `name` already present in
   `FETCHER_REGISTRY`, `__init_subclass__` MUST raise an exception at
   import time, preventing the worker from starting. The error message
   MUST identify both classes in conflict (the already-registered class
   and the class attempting registration)
2. If `Settings` is declared, it MUST be a subclass of
   `pydantic.BaseModel`
3. All fields in `Settings` MUST have a default value (no required
   fields) — see "Design decisions" below
4. All field types MUST be scalar (`int`, `float`, `str`, `bool`).
   Complex types (lists, dicts, nested models) are rejected
5. Field names MUST be `snake_case` (lowercase letters, digits, and
   underscores only)
6. If a fetcher defines `catch_up()` in its `__dict__`, it must accept
   the signature `(self, ticket_id: str, session: AsyncSession) -> None`
7. If a direct `BaseFetcher` subclass (non-CVE fetcher) needs catch-up,
   it MUST define `catch_up()` explicitly in its own class body — the
   default `catch_up()` implementation is provided by `BaseCVEFetcher`
   and not available to direct `BaseFetcher` subclasses

CVE-specific validation (`cve_source_type` uniqueness, Enum membership)
is handled by `BaseCVEFetcher.__init_subclass__` — see
`docs/features/platform/cve-fetcher-infrastructure.md` (BaseCVEFetcher
Class).

**Abstract fetcher exemption**: fetcher classes with `abstract = True`
(which opt out of registration per the existing `__init_subclass__`
contract) are exempt from concrete-class validation. `BaseCVEFetcher`
itself sets `abstract = True` and is exempt. `BaseGitFetcher` declares
`abstract = True` in its own class body (required — the
`cls.__dict__.get('abstract', False)` check does not see inherited
values). Every intermediate class in the hierarchy MUST explicitly
declare `abstract = True` in its own class body to be recognized as
non-concrete. Concrete subclasses of both are validated normally.

**`super().__init_subclass__()` chaining**: intermediate classes that
define their own `__init_subclass__` MUST call
`super().__init_subclass__(**kwargs)` to ensure `BaseFetcher`'s
validation rules execute for all subclasses in the hierarchy.
`BaseCVEFetcher` follows this pattern (see
`docs/features/platform/cve-fetcher-infrastructure.md`).
`BaseGitFetcher` does not define its own `__init_subclass__` —
validation flows through `BaseCVEFetcher` naturally via the MRO.

**Format constraint**: `CVESourceType` Enum values MUST match
`[a-z][a-z0-9_]*` and not exceed 100 characters (matching the
`CVESource.source` VARCHAR(100) column constraint). This is enforced by
a unit test on the `CVESourceType` Enum definition — not at fetcher
registration time, since `BaseCVEFetcher.__init_subclass__` already
guarantees that any declared `cve_source_type` is a valid Enum member.

Pydantic itself enforces type correctness of defaults, constraint
consistency (e.g., `default` respects `ge`/`le`), and field descriptor
validity at class definition time — no custom validation is needed for
these.

### Accessing settings at runtime

`BaseFetcher` provides a `get_setting(key)` method that resolves values
with a clear precedence:

1. Value in `FetcherConfig.custom_settings` (DB) — if the key exists
2. Default from `Settings` model — if the key is a declared field
3. `KeyError` — if the key is not declared in `Settings`

```python
# Inside execute():
page_size = self.get_setting("results_per_page")  # returns DB value or 2000
```

Settings are read from the DB at the start of each `run()` invocation
(not cached across runs). This means an admin can change a setting and
the next run picks it up immediately.

#### Runtime validation of stored values

At the start of each `run()`, all stored values from
`FetcherConfig.custom_settings` are validated by instantiating the
`Settings` model with the stored values merged over the defaults. If
Pydantic validation fails, `run()` terminates with status `failure` and
raises a `FetcherConfigError`. The error message must identify: the
fetcher name, the invalid field(s), the stored value(s), the constraint
violated, and a suggested corrective action (update the setting via the
API). No silent fallback to the default is performed.

This situation only occurs after a code change (fetcher Settings model
modification + redeploy) or direct DB manipulation — both moments when
operators monitor fetcher health closely, making quick detection and
correction likely.

### Schema registration and API exposure

The `BaseFetcher` registry (populated at import time via
`__init_subclass__`) collects the `Settings` class from each subclass.
This registry is used by:

- The API layer: the GET config endpoint returns
  `Settings.model_json_schema()` as the `settings_schema` field,
  providing a standard JSON Schema that the admin UI renders
  dynamically
- The API validation layer: the PATCH endpoint instantiates the
  `Settings` model with the submitted values to validate them
- The `sentinel fetcher config` CLI command (settings display)

Because Pydantic produces standard JSON Schema, the admin UI can render
settings forms without custom serialization logic — field types,
constraints, defaults, descriptions, choices, and warnings are all
present in the schema output.

### Design decisions

- **All settings have defaults**: no required fields are allowed in
  `Settings`. Every field must have a `default` value so that fetchers
  work out of the box. If a parameter has no reasonable default, it
  likely belongs as an environment variable, not a custom setting.
- **Orphaned keys are ignored silently**: when a field is removed from
  a fetcher's `Settings` model in a future version, old values in the
  JSONB column become orphaned. Pydantic's `model_validate()` ignores
  extra fields by default (using `model_config =
  ConfigDict(extra="ignore")`), so orphaned keys are inert. The PATCH
  endpoint rejects unknown keys, preventing new writes to orphaned
  settings. No cleanup migration is needed.
- **No environment variable override**: there is no mechanism to override
  custom_settings values via environment variables. The purpose of
  custom_settings is to avoid the env-var-requires-restart pattern. If
  deployment-level defaults are needed, an init script can call the
  PATCH API after deployment.
- **Why Pydantic and not a bespoke DSL**: the project already uses
  Pydantic extensively (request/response schemas, configuration). Using
  Pydantic for fetcher settings provides type safety, validation,
  JSON Schema generation, and IDE support for free — avoiding a custom
  validator, custom serialization, and custom documentation format.

### Referencing custom settings in fetcher specifications

Feature specifications that define fetchers with custom settings MUST
include a "Custom Settings" section with a table of settings and a
cross-reference to this document for the schema structure and validation
rules. The specification MUST NOT repeat the schema format, validation
rules, or `get_setting()` behavior inline — it should only list the
fetcher's specific settings with their values.

Example:

> **Custom Settings**
>
> This fetcher declares the following custom settings (see
> `docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
> Schema" for the schema structure and validation rules):
>
> | Setting | Type | Default | Constraints | Description |
> |---------|------|---------|-------------|-------------|
> | `my_setting` | int | 10 | 1–100 | Description of the setting |

## BaseFetcher HTTP Client Integration

All outgoing HTTP requests from fetchers use a shared HTTP client
infrastructure. For the factory module, default configuration, transport
retry, TLS trust store, and non-fetcher usage, see
`docs/features/platform/networking.md`. This section covers only the
`BaseFetcher`-specific integration.

### Lazy Property: `self.http_client`

```python
class BaseFetcher:
    http_client_options: ClassVar[dict] = {}

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Pre-configured HTTP client, created on first access."""
        if self._http_client is None:
            self._http_client = create_http_client(
                name=self.name, **self.http_client_options
            )
        return self._http_client
```

- Created lazily on first access during `execute()`
- Connection pooling active for the entire run
- Destroyed by `BaseFetcher.run()` in the `finally` block (after
  `record_end()`, suppressing `aclose()` exceptions with a log warning)
- Between runs: no client exists, no idle connections
- Stale connection handling: httpx closes idle connections after ~5s of
  inactivity within the pool. If a server closes a connection earlier,
  httpx transparently opens a new one on the next request
- If never accessed during a run: teardown is a no-op

### Override Mechanism

Fetchers with non-standard requirements override via a class attribute:

```python
class ProductReleaseFetcher(BaseFetcher):
    http_client_options = {"timeout": httpx.Timeout(10.0, read=120.0)}
```

Merge semantics: `http_client_options` entries are keyword arguments to
the factory. For same-key headers, the fetcher-specific value replaces
the factory default (last-writer-wins). User-Agent is the sole exception
— always preserved and cannot be overridden. Other options (timeout,
transport) replace defaults at the top-level kwarg level (not
deep-merged).

### `fetch_single()` and `catch_up()` Lifecycle

`fetch_single()` is safe to call from any context:

- If `self._http_client` exists (inside an active `run()` → `execute()`
  flow): reuses it. Connection pooling preserved for fetchers whose
  `execute()` delegates to `fetch_single()` in a loop (Red Hat, OSV, EPSS)
- If `self._http_client` does not exist (standalone invocation from a
  task wrapper, test, or any future call site): creates a temporary
  client for the duration of the call, closes on return
- `catch_up()` inherits this behavior (delegates to `fetch_single()`)
- Error handling: if temporary client creation fails (e.g., TLS
  misconfiguration), the exception propagates normally. No cleanup needed
  for a client that was never created

No caller responsibility: task wrappers (`fetch_single_cve`,
`run_catch_up`) do not manage HTTP client lifecycle.

## Fetcher Documentation Requirements

Every `BaseFetcher` subclass MUST have its complete definition in exactly
one specification document (single source of truth). Other specs may
reference it and include brief consumer-oriented summaries (see
"Cross-reference summaries" below), but MUST NOT specify the fetcher's
algorithm steps, error handling behavior, or custom settings.

### Classification Rule

The deciding factor for whether a fetcher gets a dedicated spec or lives
as a section in a feature spec is its **role**:

| Classification | Criterion | Spec treatment |
|---|---|---|
| The fetcher IS the feature | The spec would not exist without the fetcher. No distinct UI, API, or data model beyond what the fetcher requires. | Dedicated spec in the relevant domain. Named after what it does, not after the mechanism. |
| The fetcher supports a feature | The feature has its own identity (data model, API, UI, operations) and the fetcher is how data enters or exits. | Section within the feature spec, following the mandatory minimum template below. |

Test: if you removed the fetcher from the spec, would the spec still have
something meaningful to say? If yes → embedded. If no → dedicated spec.

Refinement for fetcher-centric specs: if the remaining non-fetcher
content exists primarily to support the fetcher itself (connection
details, authentication rationale, attribute mappings) rather than
serving independent consumers (APIs, UI, other specs), the spec is a
fetcher-centric spec — classify it as "the fetcher IS the feature."

### Minimum Documentation Template

Every fetcher — whether in a dedicated spec or embedded as a section —
MUST include at minimum:

1. **Properties table**:

   | Property | Value |
   |----------|-------|
   | Fetcher name | `<registry name>` |
   | Class name | `<PascalCase class>` |
   | Schedule | `<cron expression>` + human-readable |
   | Source | `<external service name>` |
   | Scope | `<what the fetcher processes per run>` |
   | Auth | `<authentication method>` |
   | Custom settings | Yes / No (link to Custom Settings section if yes) |

2. **Algorithm** (numbered steps describing what the fetcher does on each
   execution)

3. **Error handling** (what happens on failure — retry behavior, sanitized
   messages, partial progress). Exempt: fetchers that only interact with
   the local database.

4. **Metrics** (what counts as `record_created`, `record_updated`,
   `record_failed` — one sentence each)

5. **Custom settings table** (if applicable — following the format defined
   in the Custom Settings Schema section above)

A fetcher whose template contains TBD values is structurally prepared but
NOT considered compliant. Compliance requires real content in all
mandatory sections. TBD placeholders indicate that the fetcher's design
is pending and must be completed before implementation begins.

### Cross-Reference Summaries

Specs that consume data produced by a fetcher defined elsewhere may
include a brief consumer-oriented summary (3-5 sentences) alongside the
cross-reference, to provide reading continuity. The summary MUST describe
*what* the fetcher produces from the consumer's perspective, but MUST NOT
specify algorithm steps, error handling behavior, or custom settings.
The cross-referenced spec remains the single source of truth.

Example: `cvss-scoring.md` may summarize that `sync_nvd_cves` creates
CVSS assessments during each sync run, but must not describe the
incremental fetch strategy or the NVD Source API caching mechanism.

### Registry Maintenance

When defining a new fetcher, the Fetcher Registry table in
`docs/data-sources.md` MUST be updated with a row for the new fetcher.

### Domain Placement

Fetchers live in the domain they serve, not in a centralized `fetchers/`
folder:

- CVE/CVSS fetchers → `tickets/`
- Product/package fetchers → `packages/`
- Identity fetchers → `identity/`
- Platform-internal fetchers → `platform/`
- Integration-layer fetchers (if any) → `integrations/`

## Registry

The global registry is a module-level dictionary in
`backend/app/services/base_fetcher.py`:

```python
FETCHER_REGISTRY: dict[str, type[BaseFetcher]] = {}
```

Populated automatically by `BaseFetcher.__init_subclass__`. The registry
is used by:

- The API endpoints to list all known fetchers
- The Celery Beat schedule to register periodic tasks
- The dashboard frontend (indirectly, via the list endpoint)
- The on-demand single-CVE fetch system to discover fetchers that
  implement `fetch_single`
- The custom settings validation layer (schema lookup for PATCH
  endpoint and CLI display)

A fetcher class that is imported but should NOT be registered (e.g., an
intermediate abstract subclass) can set `abstract = True` as a class
attribute to opt out of registration.

## Celery Integration

Each registered fetcher corresponds to a Celery task in
`backend/app/tasks/fetchers.py`. A single generic task function handles
all fetchers:

```python
@celery_app.task(bind=True)
def run_fetcher(self, fetcher_name: str, triggered_by: str = "schedule",
                user_id: str | None = None,
                run_id: str | None = None) -> None:
    """Run a fetcher by name.

    Args:
        fetcher_name: registry key identifying the fetcher
        triggered_by: "schedule" (Beat) or "manual" (API)
        user_id: UUID of the user who triggered (None for scheduled
                 runs). Passed to run() as triggered_by_user_id after
                 conversion to UUID.
        run_id: UUID of a pre-created FetcherRun record (API trigger
                flow). When provided, run() updates this record instead
                of creating a new one. When None, run() creates a new
                record. Passed to run() after conversion to UUID.
    """
    ...
```

The Celery Beat schedule is built dynamically from the registry at worker
startup, using each fetcher's effective schedule (config override or
default). When an admin modifies a fetcher's schedule via the API, the
Beat schedule MUST be updated accordingly (using `celery-redbeat` or
equivalent dynamic scheduler).

**Timezone enforcement**: the Celery application is configured with
`timezone = "UTC"` and `enable_utc = True`. All cron expressions in
`default_schedule` and `FetcherConfig.schedule` are interpreted as UTC.
The worker validates these settings at startup and refuses to start if
they are overridden. See `docs/conventions.md` (Timestamps & Timezones)
and `docs/configuration.md` (Celery Worker Configuration).

## Concurrency Control

Only one instance of a given fetcher can run at a time. The concurrency
check is performed at **two levels**:

1. **API level** (for manual triggers): the trigger endpoint checks for
   an active `FetcherRun` **synchronously** before enqueuing the Celery
   task. If a run is already active and not stale, the API returns 409
   Conflict immediately — no task is enqueued. If the active run is
   stale, it is marked as `failure` and the new run proceeds (see
   "Stale Run Detection" below).
2. **Task level** (for scheduled triggers): before invoking `execute()`,
   the `run_fetcher` task checks whether a `FetcherRun` record with
   `status = running` already exists for the requested `fetcher_name`.

At the task level:

- **If a run is already active and NOT stale**: the new attempt is
  discarded silently. No `FetcherRun` record is created. An
  application-level log message is emitted for observability:
  ```
  logger.info("Skipping scheduled run for '%s': already running (run_id=%s)",
              fetcher_name, active_run_id)
  ```
- **If a run is already active and stale**: the stale run is marked as
  `failure` (see "Stale Run Detection" below), then execution proceeds
  normally with a new `FetcherRun`.
- **If no run is active**: execution proceeds normally (a new `FetcherRun`
  is created with `status = running`).

This applies to all trigger sources:

| Scenario | Active run triggered by | New attempt triggered by | Behavior |
|---|---|---|---|
| Admin triggers while schedule is running | `schedule` | `manual` | API returns **409 Conflict** with message indicating the fetcher is already running |
| Schedule fires while manual run is active | `manual` | `schedule` | Silent discard with log (async — no caller to notify) |
| Schedule fires while previous schedule run is still active | `schedule` | `schedule` | Silent discard with log |
| Admin triggers while another manual run is active | `manual` | `manual` | API returns **409 Conflict** |
| Schedule fires while stale run exists | any | `schedule` | Stale run marked as `failure`, new run proceeds |
| Admin triggers while stale run exists | any | `manual` | Stale run marked as `failure`, new run proceeds (API returns **202 Accepted**) |
| Any trigger with stale run but `run_timeout = 0` | any | any | Stale detection disabled — treated as active run (409 or silent discard) |

The distinction is:

- **API-triggered attempts** (manual): the caller receives a synchronous
  **409 Conflict** response, so no log is needed — the caller is informed
  directly.
- **Schedule-triggered attempts**: there is no caller to notify, so the
  task logs the skip and returns without side effects.

The concurrency check MUST use a database query with row-level locking
(`SELECT ... FOR UPDATE`) or an equivalent atomic mechanism to prevent
race conditions between concurrent task starts. In multi-worker
deployments, without atomic locking two workers can simultaneously read
"no active run", both proceed, and execute the same fetcher in parallel
— violating the single-instance invariant.

## Stale Run Detection

A run is considered **stale** when it has been in `running` status for
longer than the fetcher's `run_timeout` (from `FetcherConfig`). The
default `run_timeout` is 3600 (1 hour). If `run_timeout` is set
to 0, stale detection is disabled for that fetcher — the run is never
considered stale regardless of how long it has been running.

When a stale run is detected (by the Celery task or the API trigger
endpoint), it is resolved by updating the stale `FetcherRun`
record:

**Operational risk of `run_timeout=0`**: disabling stale detection
means a fetcher that gets stuck will block all future executions
indefinitely, requiring manual intervention. When `run_timeout` is
set to 0 via the API, the validation rules document the operational risk
(see `docs/features/platform/fetcher-operations.md`, "Update Fetcher
Config").

- `status` → `failure`
- `error_message` → `"Marked as stale (running for {elapsed}, timeout
  {timeout}s)"`
- `finished_at` → `now()`
- `duration_seconds` → calculated from `started_at`

An application-level log message is emitted:

```
logger.warning("Marking stale run %s for '%s' as failure (running since %s, timeout %ds)",
               run_id, fetcher_name, started_at, run_timeout)
```

Stale run detection is a recovery mechanism for unclean process
terminations (OOM-kill, node crash, `kill -9`). Celery workers handle
`SIGTERM` via the Celery runtime's own signal handling — when a worker
shuts down gracefully, active tasks are revoked and their `FetcherRun`
records are finalized by the `run()` method's exception handler.

Note: Celery broker unavailability during the trigger endpoint is handled
synchronously (the FetcherRun record is immediately marked as failure).
Stale detection is therefore NOT the recovery mechanism for enqueue
failures — it covers only cases where the worker process dies after
having accepted the task (SIGKILL, OOM, network partition).

## Data Model

### FetcherRun

Records every execution of a fetcher. This is the primary data source for
the dashboard charts.

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Internal identifier |
| fetcher_name | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL, indexed | Fetcher identifier (matches `BaseFetcher.name`) |
| started_at | TIMESTAMPTZ | NOT NULL | When the run started |
| finished_at | TIMESTAMPTZ | nullable | When the run ended (NULL while running) |
| duration_seconds | FLOAT | nullable | Computed: `finished_at - started_at` in seconds |
| status | ENUM | NOT NULL | `running`, `success`, `failure`, `partial` |
| items_created | INTEGER | NOT NULL, DEFAULT 0 | Number of new records created |
| items_updated | INTEGER | NOT NULL, DEFAULT 0 | Number of existing records updated |
| items_failed | INTEGER | NOT NULL, DEFAULT 0 | Number of items that failed processing |
| error_message | TEXT | nullable | Sanitized error description (for all users). Written explicitly by the fetcher (`FetcherError`), by BaseFetcher's generic fallback (see "Error Message Sanitization"), or by the all-items-failed safety check (`"All {N} items failed"` — see "Status determination precedence") |
| error_detail | TEXT | nullable | Raw exception message — `str(exception)` (`manage_fetchers` capability required for visibility) |
| error_traceback | TEXT | nullable | Full Python traceback (`manage_fetchers` capability required for visibility) |
| triggered_by | ENUM | NOT NULL | `schedule`, `manual` |
| triggered_by_user_id | UUID | FK(user.id), nullable | User who triggered the run (only for `manual`) |
| cursor | JSONB | nullable | Fetcher-defined checkpoint for the next run. Generic: may contain a commit SHA, timestamp, offset, page token, or any structured cursor. Written when the final run status is `success` or `partial`; read by the next run to determine the starting point. See `docs/features/platform/git-fetcher-infrastructure.md` (Cursor Persistence) for the git-specific usage pattern |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Record creation timestamp |

**Notes**:
- `finished_at` is NULL while a run is in progress (status `running`).
  This can be used to detect stale runs (running for too long).
- `error_detail` and `error_traceback` are stored for debugging but MUST
  NOT be exposed to users without the `manage_fetchers` capability via
  the API.
- `duration_seconds` is stored (not computed at query time) because it is
  the primary Y-axis value for timeline charts and benefits from indexing.
- `cursor` is written at the end of a successful or partial run and
  read at the start of the next run (query: last `FetcherRun` with
  `status IN ('success', 'partial')` for the same `fetcher_name`,
  ordered by `started_at DESC`, limit 1). Fetchers that derive their
  starting point from other columns (e.g., `started_at`) leave
  `cursor` NULL.
- The cursor value must be a JSON-serializable dict. `BaseFetcher.run()`
  validates via `json.dumps()` before writing; a non-serializable value
  raises `TypeError` and the run fails without persisting a cursor.

### FetcherRunStatus Enum

| Value | Description |
|---|---|
| `running` | Execution in progress |
| `success` | Completed without errors |
| `failure` | Execution failed. Either: (a) `execute()` raised an unhandled exception, or (b) `execute()` returned normally but all items failed (`items_failed > 0` and `items_created + items_updated == 0`) — see "Status determination precedence" |
| `partial` | Completed but some items failed (`items_failed > 0`) and at least one item succeeded (`items_created + items_updated > 0`). Implies `execute()` returned normally (no exception raised) |

### FetcherRunTriggeredBy Enum

| Value | Description |
|---|---|
| `schedule` | Triggered by Celery Beat schedule |
| `manual` | Triggered by an admin (via API) |

### FetcherConfig

Per-fetcher configuration, managed by admins. A record is created
automatically when a fetcher is first registered (on worker startup) if
one does not already exist. The auto-creation MUST use an idempotent
operation (`INSERT ... ON CONFLICT DO NOTHING` on the PK `fetcher_name`)
to guarantee safety when multiple workers start concurrently (common in
Kubernetes multi-replica deployments).

| Column | Type | Constraints | Description |
|---|---|---|---|
| fetcher_name | VARCHAR(100) | PK | Fetcher identifier (matches `BaseFetcher.name`) |
| enabled | BOOLEAN | NOT NULL, DEFAULT true | Whether the fetcher is active |
| schedule_override | VARCHAR(50) | nullable | Cron expression to override the fetcher's `default_schedule`. NULL means use the default. |
| run_timeout | INTEGER | NOT NULL, DEFAULT 3600 | Maximum execution time in seconds. Also used as the stale run detection threshold. 0 disables both soft time limit and stale detection. |
| request_delay | FLOAT | NOT NULL, DEFAULT 0 | Minimum inter-request delay in seconds. 0 = no delay. CHECK (>= 0 AND <= 300). Applied by the fetcher via `asyncio.sleep(self.config.request_delay)`. |
| custom_settings | JSONB | NOT NULL, DEFAULT `'{}'` | Per-fetcher operational parameters. Structure defined and validated by each fetcher's `Settings` Pydantic model (see "Custom Settings Schema" above). |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Last modification timestamp |

**Notes**:
- `FetcherConfig` uses `fetcher_name` as the PK (VARCHAR, not UUID) since
  fetcher names are unique identifiers defined in code.
- The `schedule_override` uses standard cron syntax (5-field). When set,
  the Celery Beat schedule for this fetcher MUST be updated dynamically.
- `run_timeout` serves two purposes:
  1. **Celery soft time limit**: when > 0, enforced by the Celery task
     (`soft_time_limit`). When a fetcher exceeds this, a
     `SoftTimeLimitExceeded` exception is raised and the run is marked
     `failure`.
   2. **Stale run detection threshold**: when > 0, used by the Celery task
      and API trigger endpoint to determine whether a `running`
     record is stale (see "Stale Run Detection" above).
  When set to 0, both mechanisms are disabled: Celery does not enforce a
  time limit, and stale detection treats the run as indefinitely active.
  The default of 3600 seconds (1 hour) applies when a `FetcherConfig`
  record is auto-created for a newly registered fetcher.
- `request_delay` is initialized from the fetcher's
  `default_request_delay` class attribute (default: 0) at auto-creation
  time. This per-fetcher initial value is only used at first registration
  — the `INSERT ... ON CONFLICT DO NOTHING` semantics preserve operator
  overrides across redeployments. Fetchers that target external APIs with
  rate limits MUST declare a non-zero `default_request_delay` to ensure
  safe behavior on a fresh deployment without manual operator
  intervention.

### FetcherAuditEvent

Audit trail for administrative actions on fetchers. Inherits `id`,
`created_at`, and `user_id` from `AuditEventMixin`.

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Inherited from AuditEventMixin |
| fetcher_name | VARCHAR(100) | FK(fetcher_config.fetcher_name) ON DELETE RESTRICT, NOT NULL, indexed | Fetcher identifier |
| event_type | ENUM | NOT NULL | See FetcherAuditEventType enum |
| user_id | UUID | FK(user.id), nullable | Inherited from AuditEventMixin. Admin who performed the action. Nullable at DB level; `FetcherAuditLog.log_event()` validates presence (all fetcher admin actions are human-initiated) |
| old_value | TEXT | nullable | Previous value (e.g., old schedule expression) |
| new_value | TEXT | nullable | New value (e.g., new schedule expression) |
| detail | JSONB | nullable | Additional structured context (e.g., which config field changed) |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Inherited from AuditEventMixin |

### FetcherAuditEventType Enum

| Value | Description |
|---|---|
| `disabled` | Fetcher was disabled by an admin |
| `enabled` | Fetcher was re-enabled by an admin |
| `triggered` | Fetcher was manually triggered by an admin |
| `config_changed` | Fetcher configuration was modified (schedule, run timeout, request delay, custom settings) |

### Event Field Values

Each event type uses `old_value`, `new_value`, and `detail` as follows:

| Event Type | `old_value` | `new_value` | `detail` |
|---|---|---|---|
| `config_changed` (standard field) | Previous value (e.g., `"0 */6 * * *"`) | New value (e.g., `"0 */4 * * *"`) | `{"field": "<field_name>"}` where field is `schedule_override`, `run_timeout`, or `request_delay` |
| `config_changed` (custom setting) | Previous value as string (e.g., `"2.0"`), or `null` if set for the first time | New value as string (e.g., `"5.0"`), or `null` if reset to default | `{"field": "custom_settings", "key": "<setting_key>"}` |
| `disabled` | `null` | `null` | `null` |
| `enabled` | `null` | `null` | `null` |
| `triggered` | `null` | `null` | `null` |

### One Event Per Field Rule

A single PATCH request that modifies N fields produces N separate
`config_changed` events, one for each field that actually changed.
Each custom setting sub-key counts as a separate field. Toggle
changes (`enabled` field) produce a separate `disabled` or `enabled`
event, not a `config_changed` event.

Example: a PATCH that changes `schedule_override`, `run_timeout`,
and `custom_settings.results_per_page` produces three
`config_changed` events, each with its own `old_value`/`new_value`
pair and identifying `detail`. All events share the same `created_at`
timestamp and `user_id`. If the same PATCH also changes `enabled` to
`false`, a fourth event of type `disabled` is created.

## Data Retention

`FetcherRun` records are retained indefinitely. At ~15 fetchers with 1–4
executions per day, the table grows by approximately 20,000 rows per
year — negligible for PostgreSQL. No cleanup task or retention policy is
necessary. Orphaned runs (stuck in `running` status due to unclean
process termination) are resolved automatically by the existing Stale Run
Detection mechanism at the next trigger attempt.

**Manual purge**: if an operator needs to reduce table size for
operational reasons (disaster recovery, database refresh), a simple
time-based DELETE is sufficient:
`DELETE FROM fetcher_run WHERE started_at < now() - interval 'N days'`.
No application-level coordination is required.

## Deregistered Fetcher Lifecycle

When a fetcher class is removed from the codebase (or renamed), its
entry disappears from the in-memory `FETCHER_REGISTRY` at the next
worker restart. However, its `FetcherConfig` record and all associated
`FetcherRun` and `FetcherAuditEvent` records remain in the database.
The FK constraints (`ON DELETE RESTRICT`) on the two dependent tables
prevent accidental deletion of the `FetcherConfig` row while dependent
records exist.

### Observable effects

- The fetcher is no longer present in `FETCHER_REGISTRY`
- Celery Beat does not schedule it
- The `GET /api/v1/fetchers` endpoint and `sentinel fetcher list` CLI
  command include the fetcher with `registered: false`. Code-defined
  metadata (`description`, `default_schedule`, `Settings` model)
  is unavailable and appears as `null`
- Per-fetcher **read** endpoints (`/runs`, `/timeline`, `GET /config`,
  `/audit-log`) work normally — they validate the fetcher name against
  `FetcherConfig` in the database, not against the registry
- Per-fetcher **write** endpoints (`POST /trigger`, `PATCH /config`)
  return `409 FETCHER_DEREGISTERED` — the fetcher cannot be triggered
  or configured since the code has been removed
- `sentinel fetcher config` displays a read-only snapshot of the stored
  configuration without schema context
- Historical data (runs, audit events) remains in the database and is
  accessible through the API and dashboard UI

### No automatic cleanup

Sentinel does not automatically delete `FetcherConfig` records for
deregistered fetchers. This is intentional:

- Historical run and audit data has forensic and operational value
- The `ON DELETE RESTRICT` FK constraints make accidental cleanup
  impossible — dependent records must be removed first
- The number of deregistered fetchers grows slowly (order of units over
  the lifetime of the application) and does not create a storage or
  performance concern

If an operator needs to remove all traces of a deregistered fetcher,
the cleanup is a manual database operation that must respect FK ordering:
delete `FetcherRun` records, then `FetcherAuditEvent` records, and
finally the `FetcherConfig` row.

## Guardrail: Fetcher Base Class Compliance

See AGENTS.md (Guardrail 14) for the full compliance rules.

## Subagent: @fetcher-compliance-reviewer

See `.opencode/agents/fetcher-compliance-reviewer.md` for trigger
conditions, checks, and output format.

## Dependencies

- Celery Beat with dynamic schedule support (`celery-redbeat` or
  equivalent)

## Audit Trail

The `FetcherAuditLog` subclass of `BaseAuditLog` provides the event
creation helper and registers the fetcher audit trail in the global
registry. See `docs/features/platform/audit-trail-infrastructure.md`
for the base class contract.

## Open Questions

None at this time.
