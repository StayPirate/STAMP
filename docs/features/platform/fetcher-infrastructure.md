# Fetcher Infrastructure

## Purpose

This document defines the mandatory infrastructure that all data fetchers
in Sentinel must use. Fetchers are background tasks that periodically
pull data from external sources (NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS)
and update the local database. It covers the generic `BaseFetcher`
abstract base class: the fetcher registry, fetcher discovery, Celery
integration, Beat schedule synchronization, concurrency control, the
per-ticket `catch_up()` mechanism, custom settings schema, error message
sanitization, BaseFetcher HTTP client integration, data model, and data
retention.

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
| **This document** | BaseFetcher base class, naming, error sanitization, custom settings, catch_up mechanism (generic), BaseFetcher HTTP client integration (lazy property, overrides, lifecycle), registry, fetcher discovery, Celery, Beat schedule synchronization, concurrency, stale run detection, data model, retention, deregistered lifecycle, doc requirements |
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
         preserved for diagnostics but do not influence the status.
         This includes `SoftTimeLimitExceeded` — when the soft time limit
         is reached, the exception propagates to `run()`, resulting in
         `failure` status with an enriched error message (see the generic
         fallback table entry for `SoftTimeLimitExceeded` above).
         The hard time limit (`time_limit`) terminates the process if the
         soft limit fails to stop execution within the grace window (5% of
         `run_timeout`).
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
and Celery worker logs (task failure traceback). Recovery happens at
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

In both cases, visibility is provided by: application logs and Celery
worker logs (task failure traceback). No explicit Celery retry is configured.

## Abstract Interface

Concrete fetchers MUST implement:

```python
class SyncExampleData(BaseFetcher):
    name: str = "sync_example_data"      # unique identifier, snake_case, max 100 chars
    description: str = "Human-readable description"
    default_schedule: str = "0 */6 * * *"  # cron expression (every 6h)
    default_request_delay: float = 0  # Optional: initial request_delay at auto-registration
    queue: str | None = None  # Optional: Celery queue name (default = default queue)
    participates_in_catch_up: bool = False  # Optional: set True for per-ticket catch-up participation

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
    """Return fetchers that participate in per-ticket catch-up.

    Selection is based solely on the participates_in_catch_up class
    attribute. This attribute is:
    - Auto-derived from supports_fetch_single for BaseCVEFetcher
      subclasses (unless explicitly overridden)
    - Set explicitly by non-CVE fetchers (on the concrete class or
      an intermediate base)
    - Default False on BaseFetcher (non-participating unless declared)

    Note: this predicate selects by CAPABILITY, not by enabled state.
    The enabled check is performed downstream in run_catch_up at task
    execution time. A disabled fetcher is still returned here but
    skipped silently at runtime.
    """
    return {
        name: cls
        for name, cls in FETCHER_REGISTRY.items()
        if cls.participates_in_catch_up
    }
```

**Caching semantics**: computed on each call from the current registry
state (not cached). No dedicated cache-clearing test helper is needed —
test suites that dynamically register fetcher classes clean
`FETCHER_REGISTRY` directly.

### Celery task wrapper

A single generic Celery task wraps all `catch_up()` invocations:

```python
@celery_app.task(bind=True, max_retries=3)
def run_catch_up(self, fetcher_name: str, ticket_id: str) -> None:
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
            try:
                await fetcher.catch_up(ticket_id, session)
            except (NotImplementedError, CVENotInSource, ValueError):
                return  # Contract violations and defensive catches — non-retryable, silent return
            except Exception as e:
                if is_retryable_condition(e):
                    raise  # propagate to outer scope for self.retry()
                raise  # non-retryable — task fails permanently
            finally:
                if fetcher._http_client is not None:
                    try:
                        await fetcher._http_client.aclose()
                    except Exception:
                        logger.warning("Failed to close HTTP client for %s", fetcher_name)
                    fetcher._http_client = None
    try:
        asyncio.run(_run())
    except (NotImplementedError, CVENotInSource, ValueError):
        return  # already handled inside _run — defensive outer catch
    except Exception as e:
        if is_retryable_condition(e):
            self.retry(exc=e, countdown=5 * 2 ** self.request.retries)
        raise  # non-retryable — task fails permanently
```

`run_catch_up` is a Celery `bind=True` task. `self.retry(exc=e,
countdown=...)` raises a `Retry` exception internally, re-enqueuing the
task with the specified backoff delay.

The `finally` block implements the HTTP client ownership rule (see
"`fetch_single()` and `catch_up()` Lifecycle" below): the outermost
wrapper that invokes a sub-operation owns the client lifecycle. If
`catch_up()` (or any method it delegates to, including `fetch_single()`)
accessed `self.http_client` during execution, the client is closed here.
If no HTTP request was made (`_http_client is None`), the teardown is a
no-op.

If `fetcher_name` is not found in the registry (e.g., a deployment
removed the fetcher between enqueue and execution), the task logs an
error and returns without retry.

**Non-retryable exceptions**: `NotImplementedError`, `CVENotInSource`,
and `ValueError` are caught explicitly before the
`is_retryable_condition()` check because they represent contract
violations and defensive catches (not HTTP errors).
`NotImplementedError` indicates a programming error (incorrect invocation
on a fetcher without a real `catch_up()` implementation). `CVENotInSource`
is caught internally by the default `BaseCVEFetcher.catch_up()` and should
never propagate — if it does, it indicates a custom override that forgot
to catch it. `ValueError` indicates a malformed `ticket_id` parameter
(not a valid UUID) — a contract violation by the caller, not a transient
failure.

**Retry classification**: after the explicit non-retryable exceptions
are handled, the remaining exceptions are classified by
`is_retryable_condition(exc)` — retries transient HTTP conditions
(network errors, HTTP 5xx, timeout, HTTP 429); fails immediately for
permanent conditions (HTTP 4xx except 429, parsing errors, non-httpx
exceptions). See `docs/features/platform/networking.md`, "Celery Retry
Classification". Retry parameters: 3 retries with exponential backoff
(5s → 10s → 20s), matching `fetch_single_cve` (see
`cve-fetcher-infrastructure.md`, "Retry Policy for `fetch_single`").

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
  - **Retry policy**: the `run_catch_up` Celery task wrapper uses
     `is_retryable_condition(exc)` for retry classification (3 retries
     with exponential backoff 5s → 10s → 20s). Retries transient HTTP
     conditions (network errors, HTTP 5xx, timeout, HTTP 429); fails
     immediately for permanent conditions (HTTP 4xx except 429, parsing
     errors, non-httpx exceptions). See
     `docs/features/platform/networking.md`, "Celery Retry
     Classification"
  - **CVE fetchers** (default `catch_up()`): the default
    `catch_up()` implementation MUST catch `CVENotInSource` internally
    and treat it as a no-op (the CVE is not in this source — nothing
    to catch up on). `CVENotInSource` MUST NOT propagate to the
    `run_catch_up` wrapper. Transient errors (network, HTTP 5xx)
    propagate to the wrapper for retry
  - **Post-exhaustion (CVE fetchers)**: when the task fails (whether
     from retry exhaustion or immediate non-retryable failure), no
     `CVESource` status write occurs.
     `run_catch_up` receives only `(fetcher_name, ticket_id)` — it
     lacks direct `cve_id` access. Re-querying the ticket in an error
     handler adds complexity disproportionate to the benefit: retry
     exhaustion indicates infrastructure instability (a rare condition),
     and the next periodic `execute()` run (within 24h) overwrites the
     status with the correct value
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
| `sync_cisa_kev` | Syncs entire KEV catalog (`supports_fetch_single = False` → `participates_in_catch_up` derived as `False`) |

Note: `sync_cisa_kev` inherits from `BaseCVEFetcher` but is excluded from
catch-up because its `supports_fetch_single = False` attribute causes
`participates_in_catch_up` to be auto-derived as `False` via
`BaseCVEFetcher.__init_subclass__`. Its `execute()` syncs the entire catalog
on every run — there is no gap to recover after ticket reactivation. The CISA
KEV catalog is monolithic with no per-CVE API, so `fetch_single_cve` is never
dispatched for this fetcher either. In contrast,
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

**Chaining requirement**: all `FetcherError` raises that wrap a caught
exception MUST use `from e` to preserve the diagnostic chain. Without
chaining, `error_detail` is `NULL` and operators lose visibility into
the underlying failure cause. The only exception is `FetcherError`
raised without a caught exception (e.g., pre-flight configuration
guards like "token not configured") — these have no `__cause__` by
nature and are correct without chaining.

### BaseFetcher fallback

When `execute()` raises an exception that is NOT a `FetcherError` (i.e.,
an unhandled exception), `BaseFetcher.run()` applies a **generic category
fallback** — it maps the exception type to a safe, generic message:

| Exception category | `error_message` |
|--------------------|-----------------|
| `ConnectionError`, `Timeout` | `"External service unreachable"` |
| `HTTPStatusError` (4xx) | `"External service rejected request"` |
| `HTTPStatusError` (5xx) | `"External service returned server error"` |
| `SoftTimeLimitExceeded` | `f"Execution timed out after {self.config.run_timeout}s ({processed} items processed before timeout). Consider increasing run_timeout via FetcherConfig for fetcher '{self.name}'."` (where `processed = self._created + self._updated + self._failed`) |
| Any other exception | `"Unexpected error"` |

In all cases, `error_detail` receives `str(exception)` and
`error_traceback` receives the full traceback.

### `SoftTimeLimitExceeded` handling convention

`SoftTimeLimitExceeded` is a **whole-run signal**, not a per-item error.
All fetcher implementations MUST ensure this exception propagates to
`BaseFetcher.run()` for proper finalization. Specifically:

- Per-item exception handlers (e.g., try/except loops that catch
  `Exception` to isolate individual item failures) MUST exclude
  `SoftTimeLimitExceeded` from the catch. Failing to do so silently
  defeats the timeout mechanism — the exception is consumed, and the
  task continues indefinitely past the soft time limit until the hard
  limit terminates the process.
- `MemoryError` SHOULD also be excluded from per-item catches, as
  continuing after memory exhaustion is futile.

The recommended pattern for per-item exception handling:

```python
from celery.exceptions import SoftTimeLimitExceeded

for item in items:
    try:
        process(item)
    except (SoftTimeLimitExceeded, MemoryError):
        raise  # whole-run signals — never catch per-item
    except Exception as e:
        session.rollback()
        self.record_failed()
        logger.warning("Failed to process %s: %s", item, e)
        continue
```

Concrete fetchers do NOT need to catch `SoftTimeLimitExceeded` at the
`execute()` level — `run()` provides the enriched timeout message
automatically (see the generic fallback table above).

**Not excluded — `OperationalError` and database connection loss**:
database errors are caught per-item (not excluded from the per-item
catch). When a connection is lost, all subsequent items will also fail.
The all-items-failed safety check in `run()` then triggers, setting
status to `failure` and preventing cursor advancement. This is
suboptimal (the loop iterates through doomed items until the timeout
fires) but not dangerous — the safety check protects cursor integrity.
Excluding database errors would add complexity (distinguishing
transient vs. fatal DB errors is non-trivial) for marginal benefit,
and the timeout mechanism provides the actual time bound.

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
    lookback_days: int = Field(
        default=7,
        ge=1,
        le=90,
        json_schema_extra={"warning": "Values above 30 increase run duration significantly."},
        description="Number of days to look back for modified records.",
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
7. If a fetcher participates in catch-up
   (`participates_in_catch_up = True`), it MUST have a `catch_up()`
   implementation available — either defined in its own class body,
   inherited from an intermediate base, or (for CVE fetchers) inherited
   from `BaseCVEFetcher`. Defining `catch_up()` alone is no longer
   sufficient for roster inclusion; `participates_in_catch_up` must also
   resolve to `True`
8. If a non-abstract fetcher defines `catch_up()` in its `__dict__` but
   `participates_in_catch_up` resolves to `False`, emit
   `warnings.warn()` at import time (catches silent-exclusion bugs where
   a developer defines catch-up logic but forgets the flag)
9. `default_request_delay` MUST be a non-negative float (`>= 0`).
   Negative values are structurally invalid (delay cannot be negative).
   The operational upper bound (currently 300) is enforced exclusively
   by the PATCH endpoint's Pydantic validation, not at import time —
   keeping the upper limit defined in a single location.

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

Connection pool limits can also be overridden for fetchers that make
parallel requests (e.g., via `asyncio.gather()` with concurrency above
the factory defaults):

```python
class HighConcurrencyFetcher(BaseFetcher):
    http_client_options = {
        "limits": httpx.Limits(
            max_connections=50,
            max_keepalive_connections=20,
        ),
    }
```

Merge semantics: `http_client_options` entries are keyword arguments to
the factory. For same-key headers, the fetcher-specific value replaces
the factory default (last-writer-wins). User-Agent is always built from
the standard template (cannot be overridden via `http_client_options`).
TLS verification settings (`verify`, `ssl_context`) may be overridden
but trigger a WARNING-level log at client creation time. Other options
(timeout, limits, transport) replace defaults at the top-level kwarg
level (not deep-merged).

### `fetch_single()` and `catch_up()` Lifecycle

#### HTTP Client Ownership Rule

The outermost wrapper that invokes a sub-operation (`fetch_single()` or
`catch_up()`) owns the HTTP client lifecycle and MUST close
`self._http_client` in a `finally` block after the method returns. The
sub-operation methods themselves use `self.http_client` (the lazy
property) freely and MUST NOT close the client.

The three ownership contexts:

| Wrapper (owner) | Sub-operation | Client creation | Teardown |
|-----------------|---------------|-----------------|----------|
| `run()` | `execute()` | Lazy property on first `self.http_client` access | `run()` `finally` block |
| `fetch_single_cve` task | `fetch_single()` | Lazy property on first `self.http_client` access | `fetch_single_cve` `finally` block |
| `run_catch_up` task | `catch_up()` | Lazy property on first `self.http_client` access (direct or via `fetch_single()` delegation) | `run_catch_up` `finally` block |

Connection pooling within a single invocation:

- **`run()` → `execute()` flow**: the client is created once (first
  access) and reused for all HTTP calls within `execute()`, including
  repeated `fetch_single()` calls in a loop (Red Hat, OSV, EPSS).
  Pooling is preserved for the entire run
- **`fetch_single_cve` → `fetch_single()`**: the client is created on
  first access inside `fetch_single()` and closed by the wrapper on
  return. Single-call lifetime
- **`run_catch_up` → `catch_up()`**: the client is created on first
  access (whether by a custom override's direct HTTP calls or by the
  default `catch_up()` delegating to `fetch_single()`). Pooling is
  preserved across multiple HTTP calls within the same `catch_up()`
  invocation. Closed by the wrapper on return

#### Teardown pattern

All three wrappers apply the same teardown in their `finally` block:

```python
finally:
    if fetcher._http_client is not None:
        try:
            await fetcher._http_client.aclose()
        except Exception:
            logger.warning("Failed to close HTTP client for %s", fetcher.name)
        fetcher._http_client = None
```

If `_http_client` is `None` (the sub-operation never made HTTP
requests), teardown is a no-op. The `aclose()` call is wrapped in
try/except to suppress transport errors during shutdown — preventing
them from masking the original exception that triggered the `finally`
block.

#### New call site requirement

Any future code that invokes `fetch_single()` or `catch_up()` outside
the three documented wrappers MUST apply the same `finally` teardown
pattern. Calling these methods without teardown will leak the HTTP
client (unclosed connections).

#### Retry interaction

Both `fetch_single_cve` and `run_catch_up` use Celery native retry
(`self.retry()`). When a retryable exception occurs:

1. The exception propagates through the `finally` block — the HTTP
   client is closed and `_http_client` is set to `None`
2. Celery raises `Retry`, which re-enqueues the task
3. On the next attempt, the task function re-executes from the
   beginning: a **fresh fetcher instance** is created
   (`fetcher = fetcher_cls()`), starting with `_http_client = None`
4. A new HTTP client is created lazily on first access during the
   retry attempt

No HTTP client state survives across retries. Each attempt has an
independent client lifecycle.

#### Error handling

If temporary client creation fails (e.g., TLS misconfiguration from a
corrupt CA bundle), the exception propagates normally to the caller. No
cleanup is needed for a client that was never created
(`_http_client` remains `None`).

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

When defining a new fetcher:

1. The Fetcher Registry table in `docs/data-sources.md` MUST be updated
   with a row for the new fetcher.
2. An import line for the fetcher's module MUST be added to
   `backend/app/services/fetcher_discovery.py` (see "Fetcher Discovery
   (Module Import)" below).

When removing a fetcher, both entries (registry table row and discovery
module import line) MUST be removed.

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

### Fetcher Discovery (Module Import)

`FETCHER_REGISTRY` and `_CVE_SOURCE_TYPE_MAP` are module-level
dictionaries populated at **import time** by
`BaseFetcher.__init_subclass__` and `BaseCVEFetcher.__init_subclass__`
respectively. A registry entry exists only after the module defining the
concrete fetcher class has been imported **in the current process**.
Module-level state is per-process — it is NOT shared across the worker,
API server, and Beat processes.

Every process that consumes either registry MUST import all fetcher
modules at startup:

- **Celery workers**: instantiate and execute fetchers
- **FastAPI API server**: list fetchers, serve config, validate
  `Settings` schemas, run on-demand refetch
- **Celery Beat**: build the redbeat schedule from
  `default_schedule`/`FetcherConfig` (see "Celery Beat Schedule
  Synchronization")

To guarantee all three processes import an identical set of modules with
a single maintenance point, imports are centralized in a **discovery
module**:

```python
# backend/app/services/fetcher_discovery.py
# Single source of truth for fetcher module imports.
# Importing this module populates FETCHER_REGISTRY and
# _CVE_SOURCE_TYPE_MAP.
import app.services.tickets.sync_nvd_cves  # noqa: F401
import app.services.packages.sync_smelt_products  # noqa: F401
# ... one line per concrete fetcher
```

Each entrypoint imports the discovery module once at startup:

```python
import app.services.fetcher_discovery  # noqa: F401
```

Importing `fetcher_discovery` triggers all fetcher module imports as a
side effect, populating **both** registries.

**Registration is not persistence**: the registries map fetcher names to
Python class objects, which cannot be serialized to Redis or PostgreSQL.
A process that uses a fetcher (instantiate, execute, read
`default_schedule`, validate `Settings`) MUST have the module imported.
Persisting fetcher metadata would not remove this requirement and would
duplicate code-defined defaults. Mutable per-fetcher state lives in
`FetcherConfig` (PostgreSQL); code identity lives in the registries
(in-process).

**Adding a fetcher**: add one import line to `fetcher_discovery.py`.
**Removing a fetcher**: delete its import line. Entrypoints never change.

**Drift protection**: a test MUST verify that `fetcher_discovery.py`
imports every concrete `BaseFetcher` subclass present in the domain
directories. The test scans the domain packages (`app.services.tickets`,
`app.services.packages`, `app.services.identity`,
`app.services.platform`, `app.services.integrations`) using
`pkgutil.walk_packages`, collects every concrete `BaseFetcher` subclass
found, and asserts each is present in `FETCHER_REGISTRY` after importing
`fetcher_discovery`. If a fetcher module exists without a corresponding
import line, the test fails naming the missing fetcher. The package scan
is confined to the test suite — production code uses only the explicit
imports.

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

The Celery Beat schedule is built dynamically from the registry at Beat
startup, using each fetcher's effective schedule (config override or
default). When an admin modifies a fetcher's schedule via the API, the
Beat schedule MUST be updated accordingly. See "Celery Beat Schedule
Synchronization" below for the full mechanism.

**Timezone enforcement**: the Celery application is configured with
`timezone = "UTC"` and `enable_utc = True`. All cron expressions in
`default_schedule` and `FetcherConfig.schedule_override` are interpreted
as UTC. The Celery app factory validates these settings at module import
time and raises a `RuntimeError` if they are overridden — this prevents
any Celery-based process (worker, Beat, consumer) from starting with
incorrect timezone configuration. See `docs/conventions.md` (Timestamps
& Timezones) and `docs/configuration.md` (Celery Worker Configuration).

**Result handling**: the Celery application is configured with
`task_ignore_result = True` and **no result backend**. Task return
values are never stored or read — all fetcher tasks return `None`,
and execution state (status, item counts, error message, timing) is
persisted in the `FetcherRun` table, the authoritative source for
task outcomes. `celery-redbeat` stores its dynamic schedule under the
broker URL (`redbeat:` key prefix) and has no dependency on a result
backend.

## Celery Beat Schedule Synchronization

PostgreSQL (`FetcherConfig`) is the source of truth for fetcher schedules.
Redis (redbeat) is the execution layer — it holds the entries that Celery
Beat actually fires. This section specifies the synchronization mechanism
that ensures redbeat always reflects the state defined in PostgreSQL.

### Architecture: PostgreSQL-master, Redbeat-slave

The synchronization follows a strict one-directional pattern:

```
PostgreSQL (FetcherConfig + FETCHER_REGISTRY)
         │
         ▼ writes (never reads from redbeat to update PostgreSQL)
Redis (redbeat entries)
```

- **PostgreSQL** owns the schedule definition:
  `FetcherConfig.schedule_override` (if set) or
  `BaseFetcher.default_schedule` (fallback from the code registry)
- **Redbeat** is a derived cache that can be reconstructed entirely from
  PostgreSQL + the in-memory `FETCHER_REGISTRY`
- The system MUST never read redbeat entries to determine what the
  "correct" schedule is — only PostgreSQL is authoritative

### Redbeat Configuration

Redbeat uses the following configuration:

| Setting | Value | Source |
|---------|-------|--------|
| `redbeat_redis_url` | Not configured explicitly | Defaults to `CELERY_BROKER_URL` (redbeat's standard behavior). A separate variable is unnecessary because Sentinel already configures the Celery broker as Redis. |
| `redbeat_key_prefix` | `redbeat:` | Default. All redbeat entries are stored under this prefix. |
| Scheduler class | `redbeat.RedBeatScheduler` | Configured in the Celery app settings (`beat_scheduler`), not via CLI flag. |

No separate environment variable for `redbeat_redis_url` is required or
supported. If a deployment uses a different Redis instance for the broker
vs. application cache (split deployment per `docs/configuration.md`),
redbeat follows the broker instance — which is correct, since redbeat is
part of the Celery subsystem.

Additionally, the following Celery Beat setting is configured as a
fixed application-level value (not an environment variable):

| Setting | Value | Rationale |
|---------|-------|-----------|
| `beat_max_loop_interval` | `60` | Controls the maximum sleep duration between scheduler ticks. Reduces worst-case lock sentinel detection latency from 300s (default) to 60s, and — critically — reduces `lock_timeout` (derived as `max_interval * 5`) from 1500s to 300s. This means a replacement Beat instance can acquire the lock within ≤5 minutes of a crash, rather than ≤25 minutes with the default. The tick frequency increase (1/min vs 1/5min when idle) has negligible Redis overhead (one lock extend + one sorted set range query per tick). This value MUST NOT be configurable via environment variable — it is a system-level tuning with no deployment-specific variance. |

**Derived values** (from `beat_max_loop_interval = 60`):

| Derived setting | Value | Calculation |
|-----------------|-------|-------------|
| `lock_timeout` | 300s | `max_interval * 5` (redbeat default derivation) |
| `lock.acquire(sleep=...)` | 60s | `max_interval` (retry interval when lock is held) |
| Worst-case tick latency | 60s | Determines lock sentinel detection speed |

**Internal key patterns (informational)**: redbeat stores entry data
under `{key_prefix}{entry_name}` keys and internal structures (schedule
index, distributed lock) under `{key_prefix}:{structure_name}` keys
(e.g., `redbeat::schedule`, `redbeat::lock`). These patterns are
library-internal — Sentinel code MUST interact with entries exclusively
via the `RedBeatSchedulerEntry` Python API, never by constructing Redis
keys directly. The patterns are documented here only for operational
debugging (inspecting Redis with `redis-cli`).

### Redbeat Entry Structure

Each registered and enabled fetcher has one redbeat entry:

| Property | Value |
|----------|-------|
| Entry identifier | `fetcher_name` (e.g., `sync_nvd_cves`) — entries are created and accessed via the `RedBeatSchedulerEntry` API using the fetcher name. The underlying Redis key format is managed by the library. |
| Task | `run_fetcher` |
| Schedule | Cron from effective schedule (override or default) |
| Args | `[]` |
| Kwargs | `{"fetcher_name": "<name>", "triggered_by": "schedule"}` |
| Options | See "Options field" below |
| Enabled | `true` (entry only exists when fetcher is enabled) |

**Options field**: the Options dict passed to `apply_async()` is built
from `FetcherConfig` and fetcher class attributes:

| Key | Value | Condition |
|-----|-------|-----------|
| `time_limit` | `max(5, run_timeout)` | Always |
| `soft_time_limit` | `max(1, floor(run_timeout * 0.95))` | Always |
| `queue` | fetcher's `queue` class attribute | `queue is not None` |

If `queue is None`, Options contains only `time_limit` and `soft_time_limit`.
The `queue` attribute is a code-defined routing decision (class
attribute, not configurable via `FetcherConfig`) — it does not require
redbeat propagation on PATCH.

### Time Limits and Queue Routing: Stored in Redbeat Entry Options

Celery's `time_limit`, `soft_time_limit`, and `queue` are enforced by
the worker at task dispatch time — they cannot be applied from within
the task after execution begins. Since `run_fetcher` is a generic task
shared by all fetchers (each with a potentially different `run_timeout`
and optional queue), these options MUST be passed per-invocation via
`apply_async()` options.

When Beat fires a scheduled task, it uses the `options` stored in the
redbeat entry. Therefore:

- The redbeat entry's Options always include `time_limit` and
  `soft_time_limit` (see Options field table above). Beat passes these
  to `apply_async()`, and the worker enforces them.
- If the fetcher class defines `queue` (non-None): the Options also
  include `"queue": "<queue>"`. Beat passes this to `apply_async()`,
  routing the task to the correct worker pool.

This means that a PATCH to `run_timeout` requires a redbeat entry update
(see "Which Changes Require Redbeat Propagation" below). The change takes
effect on the next scheduled execution after the entry is updated. The
`queue` attribute is code-defined (class attribute) and does not change
via PATCH — it is set once at reconciliation time and remains stable.

**Hard time limit formula**: `max(5, run_timeout)` — the `max(5, ...)`
is a safety net that prevents Celery from interpreting `time_limit = 0`
as "disabled" if an out-of-range value reaches the formula (Celery's
worker dispatch uses `time_limit or default` where `0` is falsy). With
the minimum `run_timeout` of 60, the `max(5, ...)` never activates in
practice.

**Soft time limit formula**: `max(1, floor(run_timeout * 0.95))` — same
formula defined in the `FetcherConfig` section. With the minimum
`run_timeout` of 60, the soft limit is always >= 57 (the `max(1, ...)`
never activates in practice). The gap between soft and hard limits (5%
of `run_timeout`) provides a grace window for clean finalization.

### Startup Reconciliation

When Celery Beat starts (or restarts after a crash), it performs a full
reconciliation of the redbeat schedule against the current system state.
This happens **before** Beat begins firing any tasks.

#### Startup Sequence

**Preconditions** (satisfied before reconciliation begins):
- `FETCHER_REGISTRY` is populated (via `import app.services.fetcher_discovery`)
- `FetcherConfig` records exist for all registered fetchers (via
  `bootstrap_fetcher_configs()` — see "Who Writes Where" below)
- Every fetcher in `FETCHER_REGISTRY` has a valid, non-None
  `default_schedule` (5-field cron expression) — this is guaranteed by
  the `BaseFetcher` abstract interface contract
  (`fetcher-infrastructure.md`, Abstract Interface)
- The redbeat distributed lock has been acquired (see below)

**Lock acquisition** (first step of Beat startup): before reconciliation
begins, redbeat acquires the distributed lock (`redbeat::lock`) via
`lock.acquire(blocking=True, sleep=max_interval)`. If the lock is held
by a stale instance (e.g., previous Beat crashed without releasing it),
the new Beat retries every `max_interval` (60 seconds) until the lock
expires (after `lock_timeout` = 300s from the last successful extend).
In the common recovery case (Redis data loss → lock absent), acquisition
is immediate. In a non-data-loss crash (Beat OOM-killed, Redis intact),
the worst-case wait before the new Beat starts scheduling is
`lock_timeout` = 300s (≤5 minutes).

Steps:

1. **Read state from PostgreSQL**: query all `FetcherConfig` records.
   For each registered fetcher, compute the effective schedule
   (`schedule_override` if set, else `default_schedule` from the class
   attribute in `FETCHER_REGISTRY`).

2. **Write entries for enabled registered fetchers**: for each fetcher
   that is (a) present in `FETCHER_REGISTRY` AND (b) has `enabled = true`
   in `FetcherConfig`:
   - Create or unconditionally overwrite the redbeat entry with the
     computed effective schedule and time limit options (derived from
     `run_timeout` per the formula in "Time Limits" above)
   - This is an idempotent upsert — existing entries are updated, missing
     entries are created
   - The overwrite computes `due_at` from the cron schedule relative to
     the current time. Runs missed during Beat downtime are not
     retroactively triggered — data recovery is handled at the
     application level by each fetcher's `catch_up()` mechanism
   - If `schedule_override` cannot be parsed as a valid 5-field cron
     expression, the parsing exception propagates uncaught and prevents
     Beat from completing startup (same fail-fast semantics as a
     PostgreSQL failure). This can only occur if `schedule_override` is
     corrupted via direct database manipulation — the PATCH endpoint
     validates cron syntax before persisting

3. **Remove entries for disabled fetchers**: for each fetcher that is
   present in `FETCHER_REGISTRY` but has `enabled = false`:
   - Delete the redbeat entry if it exists
   - No-op if no entry exists

4. **Remove entries for deregistered fetchers**: enumerate all scheduled
   entries via the redbeat scheduler API. For each entry whose
   `fetcher_name` (extracted from the entry's kwargs) is NOT present in
   `FETCHER_REGISTRY`:
   - Delete the entry
   - Log at INFO level: `"Removed redbeat entry for deregistered fetcher
     '%s'", fetcher_name`

   **Assumption**: this step assumes that all entries in the redbeat
   schedule are fetcher entries (created by this reconciliation or by
   runtime propagation). If Sentinel introduces non-fetcher periodic
   tasks managed via redbeat in the future, this step must be revised to
   avoid interference with those entries.

5. **Log reconciliation summary**: after all entries are processed, log
   at INFO level:
   `"Beat schedule reconciliation complete: %d entries written, %d
   disabled removed, %d deregistered removed", written, disabled_removed,
   deregistered_removed`

6. **Begin normal Beat operation**: after reconciliation completes, Beat
   begins its normal tick loop (firing tasks per their schedules)

#### Startup Failure: PostgreSQL Unreachable

If PostgreSQL is unreachable during startup reconciliation:

- Beat MUST NOT start with stale redbeat entries. Using outdated schedules
  is dangerous: a disabled fetcher might still have an entry from before
  the disable, or a schedule change might not be reflected.
- Beat logs a CRITICAL error:
  `"CRITICAL: Celery Beat startup failed — cannot read FetcherConfig from
  PostgreSQL: {error}. Redbeat schedule not reconciled. Beat will not
  start."`
- Beat exits with a non-zero exit code
- The orchestrator (Docker/Kubernetes) will restart Beat according to its
  restart policy. On the next attempt, if PostgreSQL is reachable,
  reconciliation succeeds normally.

**Rationale**: Beat is a singleton process. If it starts with stale data,
there is no fallback mechanism to correct the schedule until the next
restart. Failing fast ensures that the system is either correct or stopped
— never silently wrong.

**Error classes covered**: this fail-fast applies to any database-level
error during step 1: connection failures, authentication errors, missing
schema (migrations not yet applied), or query errors. The `{error}`
placeholder contains the specific exception message for operator
diagnosis.

#### Startup Failure: Redis Error During Reconciliation

If Redis becomes unreachable or returns an error during steps 2–4
(after PostgreSQL was read successfully in step 1), the reconciliation
is **incomplete** — some entries may have been written/deleted while
others remain stale. A partially-reconciled schedule is dangerous:
disabled fetchers might still have entries, deregistered fetchers might
still fire.

To prevent this, the entire reconciliation procedure (steps 2–4) MUST
be wrapped in error handling with **fail-on-first-error** semantics: if
any individual Redis write or delete operation fails, reconciliation
aborts immediately without attempting remaining operations. Behavior:

- Beat logs a CRITICAL error:
  `"CRITICAL: Celery Beat reconciliation failed — Redis error during
  schedule sync: {error}. Partial reconciliation may have occurred.
  Beat will not start."`
- Beat exits with a non-zero exit code
- The orchestrator restarts Beat. On the next attempt, the full
  reconciliation runs from scratch (step 2 unconditionally overwrites,
  so partial state from the failed attempt is corrected)

**Rationale**: this is the same fail-fast philosophy as the PostgreSQL
failure case. A partially-reconciled schedule is worse than no schedule
(disabled fetchers firing, deregistered fetchers consuming resources).
The unconditional-overwrite semantics of step 2 ensure that a
subsequent successful reconciliation always produces a correct state,
regardless of what was left behind by a failed attempt.

#### Startup: Redis (redbeat) Unreachable

This is equivalent to Beat being unable to start at all — Beat requires
Redis for its core operation (storing schedule state). Celery Beat's own
startup logic handles this: if the broker is unreachable, Beat cannot
initialize the scheduler and exits with an error. No special handling is
needed beyond Celery's built-in behavior.

### Runtime Propagation

When an admin modifies a fetcher's configuration via
`PATCH /api/v1/fetchers/{name}/config`, changes that affect the Beat
schedule are propagated to redbeat **synchronously** within the same HTTP
request.

#### Which Changes Require Redbeat Propagation

| Change | Propagation |
|--------|-------------|
| `schedule_override` changed (new value or set to null) | Update the redbeat entry's schedule with the new effective cron |
| `enabled` changed to `false` | Remove the redbeat entry |
| `enabled` changed to `true` | Create the redbeat entry with effective schedule and time limit options |
| `run_timeout` changed | Update the redbeat entry's Options (`time_limit`, `soft_time_limit`) with the new derived values. |
| `request_delay` changed | No propagation needed (read from DB at execution time) |
| `custom_settings` changed | No propagation needed (read from DB at execution time) |

#### Propagation Mechanism

The PATCH endpoint handler:

1. Updates `FetcherConfig` in PostgreSQL (within a transaction)
2. Commits the PostgreSQL transaction
3. Propagates to redbeat (if any propagation-requiring field changed):
   - If `enabled` changed to `false`: delete the redbeat entry. Any
     other field changes in the same PATCH are moot (a disabled fetcher
     has no entry) — skip remaining propagation steps
   - If `enabled` changed to `true`: create the redbeat entry with the
     effective schedule and time limit options (incorporating any
     `schedule_override` or `run_timeout` changes from the same PATCH)
   - If `schedule_override` changed (without `enabled` change): update
     the redbeat entry's schedule with the new effective cron expression
    - If `run_timeout` changed (without `enabled` change): update the
      redbeat entry's Options with the new `time_limit` and
      `soft_time_limit` values
   - Uses the `redbeat.RedBeatSchedulerEntry` API to write/delete the
     entry
   - If multiple non-enable propagation-requiring fields changed in the
     same PATCH, a single redbeat write reflects all changes atomically
     (one entry upsert)
   - **Upsert semantics**: all redbeat writes (create and update cases)
     use `RedBeatSchedulerEntry.save()`, which has create-if-missing
     semantics. If the entry does not yet exist in Redis (e.g., Beat has
     not restarted since a new fetcher was deployed), the PATCH
     propagation creates it. This avoids a gap where a PATCH succeeds in
     PostgreSQL but has no effect on scheduling because Beat hasn't
     reconciled yet

The PostgreSQL commit happens BEFORE the redbeat write. This ensures that
even if the redbeat write fails, the source of truth (PostgreSQL) is
correct, and the system self-heals at the next Beat restart.

#### Propagation Failure: Redis Unreachable

If the redbeat write fails (Redis unreachable, timeout, or write error):

1. The PostgreSQL change is ALREADY committed (the source of truth is
   updated)
2. The PATCH endpoint returns **200 OK** to the caller (the configuration
   change was saved successfully)
3. A WARNING-level log is emitted:
   `"WARNING: FetcherConfig for '%s' updated in PostgreSQL but redbeat
   propagation failed: %s. Schedule will be corrected at next Beat
   restart.", fetcher_name, error`
4. **No retry mechanism** — the reconciliation at next Beat startup will
   correct the redbeat state

**Rationale**: the alternative (rolling back the PostgreSQL change on
Redis failure) would make the configuration system fragile — a brief
Redis blip would prevent all fetcher configuration changes. The eventual
consistency model is safe because:

- If a schedule change failed to propagate: the fetcher runs on the old
  schedule until Beat restarts. This is a minor timing deviation, not
  data corruption.
- If a `run_timeout` change failed to propagate: the fetcher runs with
  the old time limits until Beat restarts. If the new limit is shorter
  (admin reduced it), the old limit is still a valid ceiling. If the new
  limit is longer (admin increased it), the task might time out
  prematurely once — recoverable on the next scheduled run after Beat
  restart.
- If an enable→disable failed to propagate: the fetcher's `run()` method
  checks `FetcherConfig.enabled` at execution time. Even if Beat fires
  the task, `run()` skips it (the enabled check is the safety net
  documented in the "Enabled check" section). The next Beat restart
  removes the entry.
- If a disable→enable failed to propagate: the fetcher simply doesn't
  run until Beat restarts. No data corruption.

#### Enable/Disable: Entry Lifecycle

| Action | Effect on redbeat |
|--------|-------------------|
| `enabled` → `false` | **Remove** the redbeat entry entirely. This prevents Beat from firing the task at all (no log noise, no wasted task dispatch). The `enabled` check in `BaseFetcher.run()` is a safety net for the race window between disable and an already-enqueued task. |
| `enabled` → `true` | **Create** a new redbeat entry with the effective schedule. The entry is immediately active on the next Beat tick. |
| Fetcher disabled at startup | Entry is NOT created during reconciliation (step 3 removes it if it exists from a previous state) |

### `next_run_at` Calculation

The `next_run_at` field in the `GET /api/v1/fetchers` response is
calculated by the API endpoint at request time:

1. For each registered and enabled fetcher: read the redbeat entry's
   `due_at` attribute (the timestamp of the next scheduled execution,
   maintained by redbeat automatically)
2. Access pattern: the API endpoint reads the redbeat entry by fetcher
   name via the `RedBeatSchedulerEntry` API. This is an O(1) read per
   fetcher — no full schedule scan.
3. If the entry does not exist in Redis (Beat not started, Redis flushed,
   or entry lost): `next_run_at = null`
4. If the fetcher is disabled: `next_run_at = null` (no entry exists —
   see "Enable/Disable: Entry Lifecycle")
5. If the fetcher is deregistered: `next_run_at = null` (no entry exists)

**Disambiguation**: both "Beat not started" and "fetcher disabled" produce
`null`. This is intentional — both cases mean "this fetcher will not run
on a schedule." The API consumer does not need to distinguish these cases:
a disabled fetcher's `enabled` field is `false`, which provides the
distinction for UI display purposes.

**API endpoint failure handling**: if Redis is unreachable when calculating
`next_run_at` for the fetcher list:

- Individual fetcher `next_run_at` values are set to `null`
- The endpoint does NOT return an error — the rest of the response
  (fetcher metadata, last_run, etc.) is still valid from PostgreSQL
- A WARNING-level log is emitted:
  `"WARNING: Cannot read redbeat schedule state from Redis: %s.
  next_run_at will be null for all fetchers.", error`

### Reconciliation and Divergence Recovery

#### Reconciliation is Startup-Only

Reconciliation (full overwrite of redbeat from PostgreSQL) occurs
exclusively at Beat startup. There is no periodic reconciliation task
during normal operation.

**Rationale**: during normal operation, the only legitimate source of
redbeat changes is the PATCH endpoint (which updates both PostgreSQL and
redbeat). A periodic reconciliation would add complexity (timing, locking,
performance impact of scanning all entries) for a failure mode (drift
during normal operation) that cannot occur without either:

- An external actor directly modifying Redis (operator error) — handled
  by restart-based reconciliation (entry is silently overwritten)
- A Redis flush — triggers automatic Beat crash via lock sentinel and
  orchestrator restart (see "Redis Flush Recovery" below)
- A Redis failure during PATCH propagation — self-heals at next restart

All three are extraordinary operational events where restart-based
recovery (automatic for Redis flush, immediate for the others) is
preferable to the continuous overhead of periodic reconciliation.

**Operational consequence — new fetcher deployment**: adding a new
fetcher to the codebase requires a Beat restart for scheduling to
activate. Workers and the API server will recognize the new fetcher
immediately (after their own restart), but Beat's `FETCHER_REGISTRY`
and reconciliation only update at startup. Until Beat restarts, the new
fetcher can be triggered manually via the API but will not run on
schedule. This is an expected operational constraint of the startup-only
reconciliation design.

#### Runtime: Redis Data Loss (Restart or Flush)

If Redis loses its data while Beat is running — whether due to a Redis
process restart, a `FLUSHALL` command, or any event that clears the
keyspace — Beat automatically detects the loss and terminates, enabling
orchestrator-driven recovery.

**Detection mechanism — lock sentinel**: the first operation in every
`tick()` cycle is `self.lock.extend(lock_timeout)`. When the
`redbeat::lock` key is absent (data loss), the extend Lua script
returns `0` and the Redis client raises `LockNotOwnedError`. This
exception is not caught by the scheduler's internal handlers and
propagates to terminate the process.

**Specified behavior**: when Beat detects lock loss at runtime (via
`LockNotOwnedError` or `RedisError` during the lock extend):

1. Beat MUST exit with a non-zero exit code (invariant — both
   implementation paths guarantee this)
2. Beat SHOULD log at CRITICAL level before exiting: `"CRITICAL: Celery
   Beat lost Redis lock — lock extend failed (possible causes: Redis
   data loss, connection failure, or OOM rejection). Beat will exit for
   orchestrator restart. Recovery: orchestrator restarts Beat →
   reconciliation rebuilds schedule from PostgreSQL."`

**Implementation note**: the native behavior of redbeat + celery beat
already terminates the process when `LockNotOwnedError` propagates
uncaught. The implementation SHOULD wrap the scheduler's `tick()` to
catch `LockNotOwnedError` and `RedisError`, produce the CRITICAL log
message above, and call `sys.exit(1)` — transforming a raw traceback
into an actionable operator message. If wrapping is not feasible, the
native propagation (raw traceback + non-zero exit) satisfies the MUST
requirement: the recovery mechanism works identically in both cases.

The orchestrator restarts Beat according to its restart policy
(Kubernetes: CrashLoopBackOff with exponential backoff up to 300s).
On restart, the standard startup reconciliation rebuilds the full
schedule from PostgreSQL. No manual intervention is required.

**Detection latency**: the lock extend occurs once per tick. The
worst-case time between Redis data loss and Beat termination equals
`beat_max_loop_interval` (60 seconds). If Beat is sleeping when Redis
loses data, it will not detect the loss until it wakes for the next
tick.

**Prerequisite — lock must remain enabled**: the lock sentinel mechanism
requires the redbeat distributed lock to be active (the default
configuration). Disabling the lock (`redbeat_lock_key = None` or
`redbeat_lock_timeout = None`) removes the sentinel and creates a
silent failure mode where Beat continues running with an empty schedule
after data loss. Sentinel MUST NOT disable the redbeat lock.

**`retry_period` must not be configured**: the `redbeat_redis_options`
setting `retry_period` MUST NOT be set. When unset (the default), Redis
operations that fail raise immediately without internal retries,
enabling the fail-fast behavior. If `retry_period` were set to a
positive value, redbeat would retry internally — and if Redis returned
during the retry window in a clean state, Beat would reconnect to an
empty schedule without triggering the lock sentinel (the lock might be
re-acquired transparently during retry). This would reintroduce the
silent failure mode.

**Relationship to startup failures**: this runtime behavior complements
the startup fail-fast mechanisms (PostgreSQL unreachable, Redis error
during reconciliation). The same principle applies: Beat is either
correct or stopped — never silently wrong.

#### Redis Flush Recovery

If Redis is flushed (`FLUSHALL`) or restarted without persistence while
Beat is running:

1. All fetcher schedules and the distributed lock are lost immediately
2. At the next tick (within `beat_max_loop_interval`, ≤60s), Beat
   detects the lock loss via the sentinel mechanism (see "Runtime: Redis
   Data Loss" above)
3. Beat logs CRITICAL and exits with non-zero exit code
4. The orchestrator restarts Beat
5. On startup, the full reconciliation recreates all schedule entries
   from PostgreSQL

**Recovery is automatic** — no manual intervention is required. The
orchestrator's restart policy handles the process lifecycle.

**Operational note**: a Redis flush also affects `REDIS_URL` data
(session liveness cache, login lockout counters, deduplication locks,
distributed locks). Specific impacts:

- **Session liveness cache**: no functional impact — sessions are stored
  in PostgreSQL. Redis cache misses cause a temporary increase in
  database load (~60 seconds while caches warm up). No users are logged
  out.
- **Deduplication locks** (`fetch_pending:*`): tasks already enqueued
  may be duplicated if re-triggered before execution. This is a minor
  efficiency concern — the tasks are idempotent (upsert semantics).
- **Login lockout counters**: brute-force rate limiting resets
  temporarily.
- **`/ready` endpoint**: continues to return 200 (Redis is reachable
  after flush/restart). The flush itself is not detectable via health
  checks — detection is handled by the lock sentinel mechanism.

#### Direct Redis Manipulation

Modifying redbeat entries directly in Redis (via `redis-cli`, RedisInsight,
or any path that bypasses the API) is **undefined behavior**:

- The change will be effective immediately (Beat reads entries from Redis)
- The change will be **silently overwritten** at the next Beat restart
  (startup reconciliation unconditionally overwrites from PostgreSQL)
- No error, no warning, no audit trail
- PostgreSQL remains unchanged — the API will show the "old" schedule
  until the admin changes it via PATCH

The spec does not attempt to detect or prevent direct Redis manipulation.
The self-healing nature of startup reconciliation makes this safe (no
permanent damage), though operationally confusing if done intentionally.

### Multi-Process Coordination

#### Who Writes Where

**Redbeat** (Redis schedule entries) — only two components write:

1. **Celery Beat process** (singleton): writes during startup
   reconciliation
2. **API server process** (potentially multiple replicas): writes during
   PATCH endpoint handling (runtime propagation)

**FetcherConfig** (PostgreSQL) — two types of writes:

1. **Bootstrap** (all processes: worker, Beat, API server):
   `bootstrap_fetcher_configs()` in
   `backend/app/services/fetcher_bootstrap.py`. Idempotent
   `INSERT ON CONFLICT DO NOTHING` at startup. Creates records with
   defaults for newly registered fetchers. Never modifies existing
   records. The function is async; sync callers (worker, Beat) use
   `asyncio.run()`.
2. **PATCH endpoint** (API server only): modifies existing records
   (schedule, enabled, run_timeout, custom_settings).

**Celery workers** do NOT write to redbeat. They only:

- Run the bootstrap (shared with Beat and API)
- Read `FetcherConfig` during task execution

#### Concurrency Between Beat and API

Beat startup reconciliation and API PATCH propagation can theoretically
race (Beat is restarting while an admin is changing a schedule). This is
safe because:

1. Both operations write the same authoritative source (PostgreSQL state)
   to redbeat
2. The worst case is a momentary stale overwrite: Beat writes the
   schedule from the pre-PATCH PostgreSQL state, then the PATCH writes
   the updated schedule (or vice versa). Since both read from PostgreSQL
   (which is serialized by row-level locking), the final state is always
   correct — the last writer wins, and both writers use the committed
   PostgreSQL state at their point in time
3. If Beat reconciliation reads the pre-PATCH state and writes it AFTER
   the PATCH has already written the post-PATCH state to redbeat: the
   entry is momentarily stale. The PATCH's redbeat write was
   "undone" by Beat's reconciliation. This is acceptable because it
   can only happen during the narrow window of Beat startup + concurrent
   PATCH, and the entry reflects a valid (though stale by one change)
   PostgreSQL state. The stale entry persists until the admin re-issues
   the PATCH or Beat restarts again. This is an operationally negligible
   scenario (Beat startup takes < 1 second; a concurrent PATCH during
   that exact window is rare). If it occurs, the admin observes the
   old schedule in the API (since `next_run_at` is calculated from the
   redbeat entry) and can re-issue the PATCH
4. No locking between Beat startup and API writes is required

#### Multiple API Replicas

Multiple API replicas can issue concurrent PATCH requests for different
fetchers without coordination (they write different redbeat keys). For
concurrent PATCH requests on the **same** fetcher:

- PostgreSQL serializes the `FetcherConfig` updates (standard row-level
  locking)
- The redbeat write for the same entry is a simple key SET — the last
  writer wins, which is correct since it reflects the latest committed
  PostgreSQL state

#### Redbeat Distributed Lock

Redbeat uses its own distributed lock (`redbeat::lock`) to ensure only
one Beat process is active at a time.

The redbeat distributed lock serves two purposes in Sentinel:

1. **Singleton enforcement** (redbeat-internal): if a second Beat
   process starts, the lock prevents it from taking over scheduling
   until the first one dies or releases the lock.
2. **Runtime recovery sentinel** (Sentinel-specific): the lock extend
   operation at the start of every `tick()` detects Redis data loss.
   When the lock key is absent, `LockNotOwnedError` terminates Beat,
   enabling automatic orchestrator recovery (see "Runtime: Redis Data
   Loss" above).

**Configuration constraint**: Sentinel MUST NOT disable the redbeat
distributed lock. Without it, Beat cannot detect Redis data loss and
would continue running with an empty schedule (silent failure). The
following configurations are prohibited:

- Setting `redbeat_lock_key` to `None` or empty string
- Setting `redbeat_lock_timeout` to `None` or `0`

These constraints are satisfied by the default redbeat configuration
(lock enabled, key = `redbeat::lock`, timeout derived from
`max_interval * 5`).

### Startup Validation

Timezone enforcement (`CELERY_TIMEZONE = UTC`, `CELERY_ENABLE_UTC = True`)
is validated at the **Celery app factory** level — the module that
constructs the `Celery()` application object
(`backend/app/celery_app.py`). The validation occurs at module import
time: after the app is configured, the factory checks
`app.conf.timezone == "UTC"` and `app.conf.enable_utc is True`. If
either condition fails, the factory raises a `RuntimeError`:

```
"FATAL: Celery timezone must be UTC. Current value: timezone={timezone},
enable_utc={enable_utc}. All fetcher schedules assume UTC — see
docs/conventions.md."
```

**Lock sentinel enforcement**: the Celery app factory MUST also validate
that the redbeat distributed lock is enabled. Specifically, after app
configuration is complete, it checks that `redbeat_lock_key` is not
`None` and not empty, and that `redbeat_lock_timeout` is not `None` and
not `0`. If either condition fails, the factory raises a `RuntimeError`:

```
"FATAL: Redbeat distributed lock must be enabled. Current value:
redbeat_lock_key={lock_key}, redbeat_lock_timeout={lock_timeout}. The
lock is required for automatic recovery from Redis data loss — see
docs/features/platform/fetcher-infrastructure.md (Runtime: Redis Data
Loss)."
```

This validation is satisfied by the default redbeat configuration (key =
`redbeat::lock`, timeout derived from `max_interval * 5` = 300s). It
fires only if an operator explicitly overrides the defaults.

Since every Celery-based process (worker, Beat, IBS RabbitMQ consumer)
MUST import the Celery app object to function, the validation is
inherited automatically — no per-process signal handlers
(`worker_init`, `beat_init`) are needed. The exception prevents any
process from completing initialization.

Additionally, the Beat startup reconciliation implicitly validates:

- PostgreSQL connectivity (reads `FetcherConfig`)
- Redis/redbeat connectivity (writes entries)
- `FETCHER_REGISTRY` population (via `import app.services.fetcher_discovery`
  at process startup — see "Fetcher Discovery (Module Import)" in the
  Registry section)

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
longer than `run_timeout + 60` seconds (the **stale threshold**). The
60-second margin is a hardcoded constant (not configurable). It ensures
that the hard time limit (`time_limit = run_timeout`) has had time to
terminate the process before a new run is started — guaranteeing the
single-instance invariant even if the soft time limit was not honored.
The default `run_timeout` is 3600 (1 hour), yielding a stale threshold
of 3660 seconds. The minimum allowed `run_timeout` is 60 seconds
(threshold: 120s); the maximum is 604800 seconds (7 days, threshold:
604860s). Stale detection is always active for every fetcher.

When a stale run is detected (by the Celery task or the API trigger
endpoint), it is resolved by updating the stale `FetcherRun`
record:

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

**Relationship to hard time limit**: the stale threshold is
intentionally set ABOVE the hard time limit (`run_timeout + 60 > run_timeout`).
This ensures that when stale detection triggers, the process is
already dead (killed by the hard limit at `run_timeout`). The stale
detection mechanism therefore never needs to kill or revoke a task —
it only cleans up the orphaned database record left behind by a
force-killed process.

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
automatically at process startup by `bootstrap_fetcher_configs()`
(`backend/app/services/fetcher_bootstrap.py`) — a shared idempotent
routine that runs in every Celery-based process (worker, Beat, API
server) before role-specific logic begins. The routine executes a batch
`INSERT ... ON CONFLICT DO NOTHING` (on the PK `fetcher_name`) for
every fetcher in `FETCHER_REGISTRY`, guaranteeing safety when multiple
processes start concurrently (common in Kubernetes multi-replica
deployments).

The bootstrap routine:
- **Location**: `backend/app/services/fetcher_bootstrap.py`
- **Signature**: `async def bootstrap_fetcher_configs(db: AsyncSession) -> None`
- **Sync callers**: worker and Beat startup use
  `asyncio.run(bootstrap_fetcher_configs(session))` since they operate
  outside an async event loop. The API server calls it with `await`
  during the FastAPI startup event.
- Runs AFTER `import app.services.fetcher_discovery` (which populates
  `FETCHER_REGISTRY`)
- Runs BEFORE role-specific startup (Beat: reconciliation; API: serving
  requests; worker: consuming tasks)
- Creates records with column defaults (`enabled = true`,
  `run_timeout = 3600`, `request_delay` from `default_request_delay`,
  `custom_settings = '{}'`)
- Never modifies existing records (`DO NOTHING` on conflict)
- Is concurrency-safe: multiple processes running it simultaneously
  produce no conflicts — the first insert succeeds, concurrent
  duplicates are no-ops

| Column | Type | Constraints | Description |
|---|---|---|---|
| fetcher_name | VARCHAR(100) | PK | Fetcher identifier (matches `BaseFetcher.name`) |
| enabled | BOOLEAN | NOT NULL, DEFAULT true | Whether the fetcher is active |
| schedule_override | VARCHAR(50) | nullable | Cron expression to override the fetcher's `default_schedule`. NULL means use the default. |
| run_timeout | INTEGER | NOT NULL, DEFAULT 3600 | Maximum execution time in seconds (hard ceiling). The task is guaranteed to be terminated at this limit. Also used as the basis for the stale run detection threshold. Valid range: 60–604800 (1 minute to 7 days; enforced by API validation). |
| request_delay | FLOAT | NOT NULL, DEFAULT 0 | Minimum inter-request delay in seconds. 0 = no delay. Valid range: 0–300 (enforced by API validation). Applied by the fetcher via `asyncio.sleep(self.config.request_delay)`. |
| custom_settings | JSONB | NOT NULL, DEFAULT `'{}'` | Per-fetcher operational parameters. Structure defined and validated by each fetcher's `Settings` Pydantic model (see "Custom Settings Schema" above). |
| updated_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Last modification timestamp |

**Notes**:
- `FetcherConfig` uses `fetcher_name` as the PK (VARCHAR, not UUID) since
  fetcher names are unique identifiers defined in code.
- The `schedule_override` uses standard cron syntax (5-field). When set,
  the redbeat schedule entry for this fetcher MUST be updated dynamically
  (see "Celery Beat Schedule Synchronization — Runtime Propagation"
  above).
- `run_timeout` serves three purposes:
  1. **Celery hard time limit** (`time_limit`): the Celery task's
     `time_limit` is set to `max(5, run_timeout)`. If the task exceeds
     this duration, the worker forcibly terminates the process (SIGKILL).
     This is the absolute ceiling — the task is guaranteed dead at
     this point. The `max(5, ...)` is a safety net: Celery treats
     `time_limit=0` as "disabled" (Python truthiness), so a direct-DB
     bypass with `run_timeout=0` would otherwise lose the hard-kill
     backstop. With the minimum valid `run_timeout` of 60, the safety
     net never activates in practice.
  2. **Celery soft time limit** (`soft_time_limit`): set to
     `max(1, floor(run_timeout × 0.95))`. When reached, Celery raises
     `SoftTimeLimitExceeded` in the task context. This gives the task
     a grace window (5% of `run_timeout`) to finalize the `FetcherRun`
     record cleanly before the hard kill.
  3. **Stale run detection threshold**: a `FetcherRun` record is
     considered stale if it has been in `running` status for longer
     than `run_timeout + 60` seconds. The 60-second margin accounts
     for clock skew in multi-node deployments (the process is
     guaranteed dead at `run_timeout` by the hard limit).
  All three mechanisms are always active (API validation guarantees
  `run_timeout >= 60`). The default of 3600 seconds (1 hour) applies
  when a `FetcherConfig` record is auto-created for a newly registered
  fetcher. The maximum allowed value is 604800 seconds (7 days),
  providing ample headroom for long-running operations while ensuring
  eventual recovery from stuck processes.

  **Formulas**:
  - `time_limit = max(5, run_timeout)` — the `max(5, ...)` prevents
    Celery from interpreting `time_limit = 0` as "disabled" if an
    out-of-range value reaches the formula (Celery's worker dispatch
    uses `time_limit or default` where `0` is falsy). The 5-second
    minimum guarantees the SIGKILL backstop always fires, with enough
    gap from the soft limit (minimum 1s) to avoid race conditions.
  - `soft_time_limit = max(1, floor(run_timeout × 0.95))` — same
    safety net for the soft limit. API validation enforces
    `run_timeout >= 60`, yielding a minimum soft limit of 57 — both
    `max(...)` safety nets never activate under normal operation.
  The grace window is always 5% of `run_timeout` (e.g., 180s for 3600s,
  30s for 600s, 3s for 60s). With the minimum valid `run_timeout` of
  60s, the grace window is 3s — tight but sufficient for writing a
  single `FetcherRun` status update, with the hard limit as backstop.
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
process restart (worker, Beat, or API server — all import the discovery
module). However, its `FetcherConfig` record and all associated
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

- Celery Beat with `celery-redbeat` dynamic scheduler

## Audit Trail

The `FetcherAuditLog` subclass of `BaseAuditLog` provides the event
creation helper and registers the fetcher audit trail in the global
registry. See `docs/features/platform/audit-trail-infrastructure.md`
for the base class contract.

## Open Questions

None at this time.
