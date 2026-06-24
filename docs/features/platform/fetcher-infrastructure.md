# Fetcher Infrastructure

## Purpose

Define the mandatory infrastructure that all data fetchers in Sentinel must
use. Fetchers are background tasks that periodically pull data from
external sources (NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS) and update
the local database. This specification covers the `BaseFetcher` abstract
base class, the fetcher registry, Celery integration, concurrency
control, data model, and data retention.

For the monitoring dashboard (API endpoints, frontend pages, CLI
diagnostics) that consumes this infrastructure, see
`docs/features/platform/fetcher-operations.md`.

## Terminology

| Term | Definition |
|---|---|
| **Fetcher** | A background task that retrieves data from an external source and creates/updates local records. Implemented as a subclass of `BaseFetcher`. |
| **Run** | A single execution of a fetcher, tracked from start to finish with metrics (duration, item counts, status). |
| **Registry** | An in-memory dictionary of all registered fetcher classes, populated automatically via `BaseFetcher` auto-discovery. |

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
   git-based and API-based CVE fetchers — see "Session Lifecycle for
   API-based CVE Fetchers" and "BaseGitFetcher Class" (step 10,
   transaction boundaries).

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
     "Git-Based Fetchers — Cursor Persistence" for the full mechanism
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
   property for outgoing HTTP requests. See "Shared HTTP Client" section
   for the full specification.

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

CVE fetchers inherit from `BaseCVEFetcher` (see "BaseCVEFetcher Class"
below), which additionally requires `cve_source_type` and provides
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

## On-demand Single-Item Fetch

`fetch_single` is defined as a concrete method on `BaseCVEFetcher`
(see "BaseCVEFetcher Class" below). Its default implementation raises
`RuntimeError` as a safety net — it is never called for fetchers that
set `supports_fetch_single = False`. CVE fetchers that support on-demand
fetch override it with their source-specific logic:

```python
async def fetch_single(self, cve_id: str, session: AsyncSession) -> PostIngestTasks | None:
    """Fetch a single CVE from the external source.

    Called on-demand when Sentinel encounters an unknown CVE-ID during
    ticket creation or CVE association. Writes data to the standard
    models (CVE, CVESource, CVECVSSAssessment, CVEExternalIdentifier,
    TicketReference) via cve_service.upsert_cve().

    Returns `PostIngestTasks` containing the Celery task arguments for
    Phase 2 dispatch, or `None` if no post-ingest tasks are needed
    (e.g., enrichment-only upsert with no ticket or no CPE data).
    Metrics (`record_created`/`record_updated`) are called inside
    `fetch_single()` where `UpsertResult.action` is available.

    Raises CVENotInSource if the external source explicitly confirms
    the CVE does not exist (e.g., HTTP 404, empty response).
    """
    ...
```

CVE fetchers that support per-CVE APIs override this method (the
default). Catalog-based fetchers (KEV) that have no per-CVE API set
`supports_fetch_single = False` and do NOT override `fetch_single`.
The system discovers fetchers eligible for on-demand fetch via
`get_fetch_single_fetchers()` (which filters by this attribute) and
invokes them in parallel when an on-demand fetch is needed (see
`docs/features/tickets/cve-service.md`, "On-Demand Fetch: fetch_single_cve").

The `fetch_single` method does NOT create a `FetcherRun` record. It is
a sub-operation invoked as a standalone Celery task, not a full fetcher
execution. Metric reporting (`record_created`/`record_updated`) is
performed inside `fetch_single()` where `UpsertResult.action` is
available — the caller (`execute()` loop or `fetch_single_cve`
orchestrator — see `docs/features/tickets/cve-service.md`) does not
record metrics.

### `CVENotInSource` Signal

`CVENotInSource` is a dedicated signal class (not an error) provided by
the fetcher infrastructure module. It indicates that the external source
explicitly confirmed the CVE does not exist (e.g., HTTP 404, empty
response). The orchestrator (`fetch_single_cve` task wrapper — see
`docs/features/tickets/cve-service.md`) catches this specific exception and records
`status=missing` via `record_source_status()`.

`CVENotInSource` does NOT inherit from `FetcherError` — it is not a
failure condition. It is a distinct outcome that maps to the `missing`
status in `CVESourceFetchStatus`.

### `fetch_single` Signaling Convention

This convention applies to CVE fetchers with `supports_fetch_single = True`.
The "Caller action" column describes the **`fetch_single_cve` orchestrator's**
response (on-demand path). Other callers (`execute()` loops and `catch_up()`)
apply context-specific handling — see "Session Lifecycle for API-based CVE
Fetchers" for the batch `execute()` pattern where `CVENotInSource` is a
simple rollback-and-skip without status writes.

| Behavior | Meaning | Caller action (orchestrator) |
|----------|---------|------------------------------|
| Returns `PostIngestTasks` | Data written to session buffer via `upsert_cve()` | `commit_and_dispatch(session, result)` — commits, dispatches Phase 2 |
| Returns `None` | Data written but no post-ingest needed (enrichment-only, no ticket or no CPE data) | `commit_and_dispatch(session, None)` — commits without dispatch |
| Raises `CVENotInSource` | CVE not present in source | `record_source_status(session, cve_id, source, "missing")`, then `commit_and_dispatch(session, None)` — commits the "missing" status, no dispatch |
| Raises other exception (retryable) | Transient error | `session.rollback()`, Celery retry. After exhaustion: `record_source_status(session, cve_id, source, "failure")`, `commit_and_dispatch(session, None)` |
| Raises other exception (non-retryable) | Permanent error | `session.rollback()`, `record_source_status(session, cve_id, source, "failure")`, `commit_and_dispatch(session, None)` |

Fetchers MUST NOT catch transient exceptions internally — they must
propagate to allow Celery retry to function. Fetchers MUST raise
`CVENotInSource` (not return a sentinel value) when the source explicitly
indicates the CVE does not exist.

### Retry Policy for `fetch_single`

The Celery task wrapping `fetch_single` (`fetch_single_cve`) uses native
Celery retry. This policy applies only to fetchers with
`supports_fetch_single = True`:

- **Max retries**: 3
- **Backoff**: 5s → 10s → 20s (exponential with cap)
- **Retryable conditions**: network errors, HTTP 5xx, timeout, HTTP 429
- **Non-retryable conditions**: `CVENotInSource` (→ `missing`), HTTP 403
  (→ `failure` immediately), other 4xx (→ `failure`), parsing errors on
  HTTP 200 (→ `failure`)

After retries are exhausted, the task writes
`record_source_status(session, cve_id, fetcher_cls.cve_source_type, "failure")`.

**Enabled check**: before invoking `fetch_single()`, the
`fetch_single_cve` task wrapper checks `FetcherConfig.enabled` for the
fetcher. If the fetcher is disabled, the task logs at INFO level
("On-demand fetch skipped for {fetcher_name}: fetcher is disabled") and
returns — the task completes successfully without error or retry. This
ensures `enabled = false` is a kill switch on ALL execution paths:
periodic schedule (`run()`), automated catch-up (`run_catch_up`), and
user-initiated on-demand fetch (`fetch_single_cve`). The check lives at
the task/orchestration boundary, not inside
`trigger_on_demand_fetch()` which has a "No Database Dependency" contract.

> **Design note — on-demand dispatch simplicity**
>
> `trigger_on_demand_fetch()` always enqueues `fetch_single_cve`
> tasks for all available CVE sources without checking
> `FetcherConfig.enabled`. The enabled check is performed at task
> execution time by the Celery worker. This keeps the dispatch
> function database-free (see "No Database Dependency" contract in
> `cve-service.md`) and avoids conditional enqueue logic.
>
> Consequence: disabled fetchers receive enqueued tasks that complete
> as silent no-ops (INFO log, no error, no retry). To prevent user
> confusion, the UI MUST NOT display per-source refetch buttons for
> disabled fetchers. The broadcast "refetch all" action is
> fire-and-forget — users do not receive per-source feedback
> regardless of enabled state.

### Error Categorization

| Condition | Retry? | Final status |
|-----------|--------|-------------|
| Network unreachable, DNS failure, connection refused | Yes (3x) | `failure` |
| HTTP 5xx (server error) | Yes (3x) | `failure` |
| Request timeout | Yes (3x) | `failure` |
| HTTP 429 (rate limit) | Yes (3x) | `failure` |
| HTTP 404, empty response | No | `missing` |
| HTTP 403 (forbidden) | No | `failure` |
| HTTP 400, 401, 405, other 4xx (not 404/403/429) | No | `failure` |
| HTTP 200 with valid data | — | `success` |
| HTTP 200 with unparseable data (schema mismatch, missing fields) | No | `failure` |

**Catch-all rule**: any HTTP status code or error condition not explicitly
listed above is treated as non-retryable → immediate `failure`. This
prevents wasting retry attempts on permanent errors.

**Parsing errors**: an HTTP 200 response that cannot be parsed (unexpected
schema, missing required fields, malformed JSON) is a non-retryable
condition. Retrying would hit the same response from the same source. The
fetcher MUST NOT catch parsing exceptions internally — it should let them
propagate, but the orchestrator should classify them as non-retryable
(immediate `failure`) rather than feeding them into the Celery retry loop.

**HTTP 429 and `Retry-After`**: 429 responses with a `Retry-After` header
are handled at the transport level (see "Shared HTTP Client — Transport-Level
Retry"): the transport waits the indicated value (capped at 120s) and retries
once. If the guided retry fails or `Retry-After` is absent/exceeds the cap,
the error propagates to the Celery retry loop. 429 without `Retry-After` is
not retried at transport level — it propagates directly to the fetcher or
Celery retry.

### Isolation Guarantee

When multiple fetchers are invoked in parallel for the same CVE-ID:

- Each fetcher runs as an independent Celery task
- Failure of one fetcher does NOT cancel, block, or affect other fetchers
- Each fetcher writes its own `CVESource` record independently
- The CVE record may end up with partial data (some sources succeeded,
  others failed)

## CVE Source Type Identity

### `cve_source_type` class attribute

ALL CVE fetchers — those that ingest or enrich CVE-related data from
external sources — MUST declare a `cve_source_type: str` class attribute
containing the CVESourceType identifier (e.g., `"nvd"`, `"mitre"`,
`"kernel"`, `"redhat"`). This attribute is abstract on `BaseCVEFetcher`
— concrete subclasses MUST provide a value.

This is the value stored in `CVESource.source` and used in Redis pending
keys (`fetch_pending:{cve_id}:{cve_source_type}`). Non-CVE fetchers do
not inherit from `BaseCVEFetcher` and structurally cannot declare this
attribute (it would serve no purpose without the `BaseCVEFetcher`
contract).

The valid values are defined by the `CVESourceType` Python Enum in
`app/core/enums.py`. See `docs/data-model.md` (CVESource table) for the
Enum definition and format constraints.

### Data contract stability rule

The `cve_source_type` value is stored persistently in `CVESource.source`
and used in Redis keys. Changing the value after data has been written
creates orphaned records:

- Existing `CVESource` records with the old value become invisible to
  the Fetch Status Read Path (which enumerates active fetchers)
- The refetch endpoint rejects the old value as unregistered
- Redis keys with the old value are never cleaned up by application
  code (TTL handles this, but it is unclean)

**Stability rule**: `cve_source_type` MUST NOT be changed without an
Alembic data migration that updates existing `CVESource.source` records
to the new value. This parallels the existing stability clause for
`TicketReference.source` (`docs/features/tickets/ticket-references.md`:
"if a fetcher is renamed... an Alembic data migration is required").

If a fetcher is deregistered (removed from the codebase), existing
`CVESource` records with the old `cve_source_type` value remain in the
database. Historical source provenance is preserved — old data persists
and remains queryable via the CVE detail API response, but the source
is no longer actively polled or dispatchable on-demand.

### Code convention: `self.cve_source_type` usage

CVE fetchers MUST use `self.cve_source_type` as the `source` argument
to `upsert_cve()` and `record_source_status()`. Hardcoded source strings
are forbidden:

```python
# Correct — in any CVE fetcher's execute():
await upsert_cve(db, cve_id, source=self.cve_source_type, cve_data=payload)

# Correct — in error handling:
await record_source_status(session, cve_id, self.cve_source_type, "failure")

# WRONG — hardcoded string:
await upsert_cve(db, cve_id, source="nvd", cve_data=payload)
```

This convention provides runtime enforcement for CVE fetchers: if a
fetcher calls `upsert_cve(source=self.cve_source_type)` without
declaring `cve_source_type`, the call raises `AttributeError`
immediately — catching the omission at the first test run.

This rule is enforced by code review and test coverage (not mechanically
at import time, since `__init_subclass__` cannot statically detect
`upsert_cve()` calls).

### Registry accessor: `get_fetch_single_fetchers()`

The fetcher infrastructure provides a registry-level accessor function:

```python
def get_fetch_single_fetchers() -> dict[str, type[BaseCVEFetcher]]:
    """Return CVE fetchers that support on-demand single-item fetch.

    Returns a dict mapping cve_source_type -> fetcher class for all
    registered BaseCVEFetcher subclasses where
    supports_fetch_single = True. Fetchers that opt out (catalog-based
    fetchers like KEV) are excluded.
    """
```

This function:

- Encapsulates the CVE fetcher enumeration logic in one place,
  avoiding fragile `hasattr(cls, 'fetch_single')` checks at multiple
  call sites
- Filters by `supports_fetch_single = True`, excluding catalog-based
  fetchers that have no per-CVE API
- Returns results keyed by `cve_source_type` (not `BaseFetcher.name`),
  matching the primary use case (Redis key construction, source
  validation, status enumeration)
- Is used by: on-demand fetch loop, refetch endpoint validation, fetch
  status read path

**Implementation**: a filtered read of `_CVE_SOURCE_TYPE_MAP` (the
module-level dictionary populated at import time by
`BaseCVEFetcher.__init_subclass__`):

```python
def get_fetch_single_fetchers() -> dict[str, type[BaseCVEFetcher]]:
    return {
        source_type: cls
        for source_type, cls in _CVE_SOURCE_TYPE_MAP.items()
        if cls.supports_fetch_single
    }
```

The map is fully populated after all fetcher modules have been imported.
In production, Celery workers import all task modules during startup, so
the map is complete before any consumer reads it. The FastAPI application
MUST also import all fetcher modules at startup (e.g., via an explicit
import in `app/main.py` or a startup event) — the refetch endpoint,
on-demand fetch loop, and fetch status read path all run in the API
server process and depend on a complete registry.

**Immutability**: the returned dict is a shallow copy. The
implementation SHOULD return a `types.MappingProxyType` (read-only view)
to prevent accidental corruption of the map.

**Test helper**: the existing `_clear_fetch_single_cache()` test helper
MUST clear `_CVE_SOURCE_TYPE_MAP` to prevent cross-test pollution from
dynamically created mock fetcher classes.

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

### Default implementation for CVE fetchers

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
set alongside `CVENotInSource`.

`BaseCVEFetcher` provides the concrete default implementation that all
CVE fetchers inherit (see "BaseCVEFetcher Class" below for the code
block). The default delegates to `fetch_single()` — for fetchers with
`supports_fetch_single = True`, this calls their override; for fetchers
with `supports_fetch_single = False`, `catch_up()` is never invoked
(they also set `participates_in_catch_up = False`).

**Boundary conditions for custom `catch_up()` overrides** (applies to
all fetchers — CVE and non-CVE):

- **Custom `catch_up()` overrides** MUST apply equivalent guards:
  check that the ticket exists and that the relevant data is present
  (e.g., `TicketPackageTrack` records for IBS track detection) before
  proceeding. If the ticket does not exist or has no relevant data,
  the method MUST return silently (no exception, no log warning)

CVE fetchers with `supports_fetch_single = True` only need to implement
`fetch_single(cve_id)`:

- `execute()` calls `self.fetch_single()` in a loop over active CVEs
- `catch_up()` is inherited from `BaseCVEFetcher` (delegates to
  `fetch_single()` automatically)

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
`fetch_single()` (see "On-demand Single-Item Fetch" above): no
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
`str(exception.__cause__)` in `error_detail` (admin-only). If
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
is handled by `BaseCVEFetcher.__init_subclass__` — see "BaseCVEFetcher
Class" below.

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
`BaseCVEFetcher` follows this pattern (see "BaseCVEFetcher Class"
below). `BaseGitFetcher` does not define its own `__init_subclass__` —
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

## Shared HTTP Client

All outgoing HTTP requests from fetchers and service-level clients use
a shared HTTP client infrastructure based on httpx `AsyncClient`. The
infrastructure provides two layers:

1. **Standalone factory module** (`backend/app/services/http_client.py`):
   creates a pre-configured httpx `AsyncClient` with all cross-cutting
   defaults. Any component can call this factory — fetchers, `IBSClient`,
   or future consumers.

2. **BaseFetcher integration**: `BaseFetcher` exposes a `self.http_client`
   lazy property that internally calls the standalone factory. Fetcher
   authors use `self.http_client` directly — zero configuration, zero
   boilerplate.

### Factory Module

Location: `backend/app/services/http_client.py`

```python
def create_http_client(**overrides) -> httpx.AsyncClient:
    """Create a pre-configured httpx AsyncClient.

    Applies all cross-cutting defaults (User-Agent, timeouts, TLS,
    compression, Accept header, transport-level retry). Keyword
    arguments override individual defaults.
    """
```

### Default Configuration

| Setting | Default | Override mechanism |
|---------|---------|-------------------|
| User-Agent | `Sentinel/{version} ({name}; +https://github.com/SUSE/sentinel)` | Not overridable |
| Connect timeout | 10 seconds | `http_client_options` |
| Read timeout | 30 seconds | `http_client_options` |
| Write timeout | 10 seconds | `http_client_options` |
| Pool timeout | 10 seconds | `http_client_options` |
| Accept | `application/json` | `http_client_options` (headers) |
| Accept-Encoding | `gzip, deflate` (httpx built-in) | — |
| TLS | Combined trust store (system CAs + SUSE CA) | See "TLS Trust Store Configuration" section |
| Transport retry | See "Transport-Level Retry" below | `http_client_options` |
| Proxy | Standard env vars (`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY`) | System-level |

#### User-Agent

Format: `Sentinel/{version} ({fetcher.name}; +https://github.com/SUSE/sentinel)`

- Platform version from `importlib.metadata.version("sentinel")`. If
  `PackageNotFoundError` (running from source without installation),
  defaults to `"dev"`
- Fetcher name automatic from `BaseFetcher.name` (mandatory, unique)
- Project URL hardcoded — not configurable
- Example: `Sentinel/1.0 (sync_nvd_cves; +https://github.com/SUSE/sentinel)`
- Example (dev): `Sentinel/dev (sync_nvd_cves; +https://github.com/SUSE/sentinel)`

For non-fetcher components (e.g., `IBSClient`), the `name` parameter
is passed explicitly to the factory.

#### Timeouts

- Connect: 10 seconds (TCP + TLS handshake)
- Read: 30 seconds (time to receive response body)
- Write: 10 seconds (time to send request body)
- Pool: 10 seconds (time waiting for a connection from the pool)

Not configurable via env var or admin panel — these are engineering
decisions. Fetchers that need different values override via
`http_client_options`.

Timeout hierarchy (independent concerns):

```
┌─────────────────────────────────────────────────────┐
│ FetcherConfig.run_timeout (default: 3600s)          │  ← Celery task level
│ Detects stale runs (worker crashed, deadlock)       │     (per entire run)
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │ Per-HTTP-request timeout                      │  │  ← HTTP transport level
│  │ connect: 10s, read: 30s                       │  │     (per single request)
│  │                                               │  │
│  │ A single execute() run may make hundreds of   │  │
│  │ HTTP requests, each with its own timeout.     │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

#### TLS Configuration

See the peer-level "TLS Trust Store Configuration" section (below) for
the full specification. The shared HTTP client uses the combined trust
store (system CAs + SUSE CA) by default — no per-fetcher configuration
needed.

#### Transport-Level Retry

The shared client automatically retries transient errors before the
fetcher sees them. If all retries fail, the error propagates to the
fetcher, which applies its own logic (abort, skip-and-continue, etc.).

**Dispatch rule**: when a response matches multiple rows (e.g., 503 is
both a 5xx and may carry `Retry-After`), the most specific row wins. If
`Retry-After` is present and parseable, the guided path is selected;
otherwise, the generic status-code row applies.

| Condition | Retry | Backoff |
|-----------|-------|---------|
| 5xx, connection error, timeout | 4 attempts (1 original + 3 retries) | 1s / 2s / 4s (fixed) |
| 429/503 with `Retry-After` ≤ 120s | 1 retry | Wait the indicated value |
| 429/503 with `Retry-After` > 120s | No retry | Error propagated immediately |
| 429 without `Retry-After` | No retry at transport | Fetcher decides |
| 4xx (non-429) | No retry | Client error — retrying is pointless |

**Path exclusivity**: if a response enters the Retry-After guided path,
the guided retry is the final attempt. The two retry paths are mutually
exclusive within a single request sequence. Consequence: a server that
sends `Retry-After` receives one guided retry, whereas the same status
without the header receives three fixed-backoff retries. This is
intentional — the server's explicit guidance replaces blind attempts.
If the server-guided retry fails, a more persistent issue is likely.

**Retry-After parsing**: integer (seconds) or HTTP-date (RFC 7231).
Malformed values (unparseable strings, negative integers) are treated as
absent — the response falls through to the "Retry-After absent" row.

**Shutdown**: all retry sleeps (both fixed-backoff and Retry-After waits)
use `asyncio.sleep()`, cancelled automatically on `SoftTimeLimitExceeded`
or task revocation. No special handling needed.

#### HTTP Response Compression

The HTTP client sends `Accept-Encoding: gzip, deflate` by default (httpx
built-in behavior using Python standard library codecs). Responses are
decompressed transparently. Brotli (`br`) additionally supported if the
`brotli` package is installed. No per-fetcher configuration needed.

#### Proxy Configuration

The shared HTTP client respects the standard `HTTPS_PROXY`, `HTTP_PROXY`,
and `NO_PROXY` environment variables for proxy configuration. No
application-level proxy settings exist. These are system-level variables
set at the container or host level.

If the deployment uses a TLS-intercepting proxy, the proxy's CA
certificate must be present in the system CA bundle (standard procedure,
no Sentinel-specific configuration needed).

### BaseFetcher Integration

#### Lazy Property: `self.http_client`

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

#### Override Mechanism

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

#### `fetch_single()` and `catch_up()` Lifecycle

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

### Non-Fetcher Components

`IBSClient` calls the standalone factory directly and manages its own
client lifecycle independently of `BaseFetcher`:

- Instantiated per-process (each Celery worker and IBSEventConsumer)
- Long-lived client with connection pooling
- Uses `Accept: application/xml` override (from JSON default)
- TLS validated via the same combined trust store
- httpx idle connection management (~5s timeout) prevents stale
  connections without manual intervention
- Certificate rotation requires process restart (same as BaseFetcher)

## TLS Trust Store Configuration

All outgoing TLS connections from Sentinel — HTTP (shared client), LDAP
(`sync_ldap_directory`), AMQP (`IBSEventConsumer`) — use a combined
trust store that includes both the system CA bundle and the SUSE
internal CA.

- **Env var**: `SUSE_CA_CERT_PATH` (default: `certs/SUSE_Trust_Root.crt`)
  - The SUSE Trust Root CA file is committed in the repository. The
    default path works both in containers (workdir `/app`) and in local
    development (run from project root). No configuration needed for
    standard deployments
  - The env var exists as an override for non-standard deployments
- **Combined trust store**: at runtime, Python builds an SSL context
  that includes system CAs (for public services: NVD, GitHub, CISA,
  Red Hat, OSV, FIRST.org) and the SUSE CA (for internal services: IBS,
  SMELT, AIMAAS, RabbitMQ). All connections use the same trust store —
  no host matching, no fallback, no host list to maintain
- **If file does not exist**: combined trust store contains only system
  CAs. Connections to SUSE internal services fail with TLS error. A log
  warning is emitted at startup (does not block startup)
- **If file is corrupt or unparseable**: SSL context creation raises an
  error at client creation time. The fetcher fails with a clear error
  message
- **TLS verification**: always enforced. Failed handshake is an immediate
  error — never proceed with an unverified connection
- **Certificate rotation**: SSL context is built at client creation time.
  Long-lived clients (IBSClient) require process restart to pick up a
  rotated CA certificate. Acceptable given CA rotations are infrequent
  (years between)

### Protocol-Specific Integration

| Protocol | Component | Trust Store Source |
|----------|-----------|-------------------|
| HTTPS | Shared HTTP client (all fetchers, IBSClient) | Combined trust store via factory |
| LDAPS | `sync_ldap_directory` fetcher | Same `SUSE_CA_CERT_PATH`, passed to python-ldap SSL context |
| AMQPS | `IBSEventConsumer` | Same `SUSE_CA_CERT_PATH`, passed to aio-pika/aiormq SSL context |

## BaseCVEFetcher Class

Intermediate abstract class for all CVE fetchers — those that ingest or
enrich CVE-related data from external sources. Sits between `BaseFetcher`
(generic fetcher infrastructure) and concrete CVE fetchers, providing the
CVE-specific contract: `cve_source_type`, optional `fetch_single()`,
default `catch_up()`, and CVE-specific import-time validation.

**File location**: `backend/app/services/base_cve_fetcher.py`

**Position in the hierarchy**:

```
BaseFetcher (generic: lifecycle, metrics, FetcherRun, cursor, registry,
             concurrency, enabled check, Settings, error sanitization)
│
└── BaseCVEFetcher (CVE-specific: cve_source_type, fetch_single (opt-out),
    │               CVENotInSource, source_reference_url_pattern,
    │               default catch_up, CVE import-time rules)
    │
    ├── BaseGitFetcher (git-specific: clone, fetch, delta, recovery,
    │   │               SHA ops, queue="git", template method execute(),
    │   │               default fetch_single implementation)
    │   ├── SyncMitreCves
    │   └── SyncKernelCves
    │
    ├── SyncNvdCves          (API-based CVE discovery + enrichment)
    ├── SyncRedhatCves       (API-based CVE enrichment)
    ├── SyncGhsaAdvisories   (API-based CVE discovery + enrichment)
    ├── SyncCisaKev          (API-based CVE enrichment, catalog fetch)
    ├── SyncEpssScores       (API-based CVE enrichment)
    └── SyncOsvAdvisories    (API-based CVE enrichment)
```

### Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `abstract` | `bool` | `True` | Prevents registration in `FETCHER_REGISTRY` (intermediate class) |
| `cve_source_type` | `str` | (required, abstract) | `CVESourceType` Enum value. Unique per fetcher. Stored in `CVESource.source` |
| `source_reference_url_pattern` | `str \| None` | `None` | URL pattern with `{cve_id}` placeholder for human-readable CVE pages. Fetchers with this attribute set MUST pass the constructed URL as `source_url` to `reference_service.upsert_references()` after each `upsert_cve()` call — this creates a TicketReference with type=advisory. See `docs/features/tickets/ticket-references.md` for details |
| `participates_in_catch_up` | `bool` | `True` | Whether the fetcher is included in `get_catch_up_fetchers()` results. Global-scope CVE fetchers that sync entire catalogs set this to `False` to opt out of per-ticket catch-up |
| `supports_fetch_single` | `bool` | `True` | Whether the fetcher supports on-demand single-CVE fetch. Catalog-based fetchers (KEV) that have no per-CVE API set this to `False`. Fetchers with `False` are excluded from `get_fetch_single_fetchers()` results, are never dispatched by `fetch_single_cve`, and do not need to override `fetch_single()` |

### Concrete Methods

Inherited by all CVE fetchers:

| Method | Description |
|--------|-------------|
| `fetch_single(cve_id, session)` | Default implementation raises `RuntimeError("fetch_single() called on a fetcher that does not support it")`. Fetchers with `supports_fetch_single = True` MUST override this method. Fetchers with `supports_fetch_single = False` inherit the default (never called in practice — `get_fetch_single_fetchers()` excludes them) |
| `catch_up(ticket_id, session)` | Default implementation: extract `cve_id` from ticket, call `self.fetch_single()`, catch `CVENotInSource` as no-op. Only meaningful for fetchers with `supports_fetch_single = True`; fetchers with `False` also set `participates_in_catch_up = False` (so `catch_up()` is never invoked) |
| `commit_and_dispatch(session, post_ingest)` | Helper method: commits the session, then dispatches `post_ingest` tasks (if not `None`) via `apply_async()`. Dispatches exactly one `resolve_ticket_packages.apply_async()` call per invocation. If `post_ingest` is `None`, commits without dispatching. **Commit failure**: if `session.commit()` raises (e.g., `OperationalError` from lost DB connection), the exception propagates to the caller — no dispatch is attempted. The caller is responsible for rollback. **Celery failure**: if `apply_async()` raises after a successful commit (e.g., Celery broker unreachable), the error is logged at WARNING level and the function returns normally — Phase 2 recovery relies on the next sync cycle (see `cve-service.md`, Crash Recovery). **Re-invocation safety**: if called twice with the same `post_ingest` (e.g., Celery retry after successful first invocation), the second `session.commit()` is a no-op (empty buffer); the second dispatch sends a duplicate Phase 2 task. Phase 2 tasks are idempotent (`TicketPackage` existence check), so duplicate dispatch is safe |

**Default `catch_up()` implementation**:

```python
class BaseCVEFetcher(BaseFetcher):
    async def catch_up(self, ticket_id: str, session: AsyncSession) -> None:
        """Default: extract cve_id from ticket, call fetch_single().

        All boundary conditions from the BaseFetcher catch_up()
        interface contract apply.
        """
        ticket = await session.get(Ticket, UUID(ticket_id))
        if ticket and ticket.cve_id:
            try:
                result = await self.fetch_single(str(ticket.cve_id), session)
                await self.commit_and_dispatch(session, result)
            except CVENotInSource:
                await session.rollback()  # defensive: ensure clean session state
```

**Boundary conditions** (CVE-specific):

- **Ticket does not exist** (deleted between enqueue and execution):
  `session.get()` returns `None`, the `if ticket` guard causes a
  silent return. This is expected — the catch-up is a no-op
- **Ticket has no CVE** (`cve_id IS NULL`, e.g., manually created
  ticket): the `if ticket.cve_id` guard causes a silent return.
  There is nothing for a CVE fetcher to catch up on
- **`CVENotInSource`**: caught silently — the CVE is not in this
  source, nothing to catch up on
- **Transient errors** (network, HTTP 5xx): propagate to the
  `run_catch_up` wrapper for retry

### `__init_subclass__` Validation

For concrete subclasses (those not setting `abstract = True` in their
own class body — checked via `cls.__dict__.get('abstract', False)`,
consistent with `BaseFetcher`):

1. `cve_source_type` MUST be declared and MUST be a member of the
   `CVESourceType` Python Enum
2. `cve_source_type` MUST be unique across all registered CVE fetchers
3. If `cve_source_type` was already registered by another fetcher, raise
   an import-time error identifying both classes

**`super().__init_subclass__()` chaining**:

`BaseCVEFetcher.__init_subclass__` MUST call
`super().__init_subclass__(**kwargs)` after its own CVE-specific
validation but before registering in `_CVE_SOURCE_TYPE_MAP`. Without
this call, `BaseFetcher.__init_subclass__` would NOT execute for CVE
fetcher subclasses — Python calls only the nearest `__init_subclass__`
in the MRO. The chain ensures that `BaseFetcher`'s rules (name
uniqueness, Settings validation, catch_up signature, etc.) are applied
to all CVE fetchers.

Execution order for a concrete CVE fetcher (e.g., `SyncNvdCves`):

1. `BaseCVEFetcher.__init_subclass__()` — validates `cve_source_type`
   (Enum membership, uniqueness check — read-only, no registration)
2. `BaseCVEFetcher.__init_subclass__()` calls
   `super().__init_subclass__(**kwargs)`
3. `BaseFetcher.__init_subclass__()` — validates general rules (name
   uniqueness, Settings, registration in `FETCHER_REGISTRY`)
4. Control returns to `BaseCVEFetcher.__init_subclass__()` — registers
   `cve_source_type` in `_CVE_SOURCE_TYPE_MAP` (commit step)

For `BaseGitFetcher` subclasses (e.g., `SyncMitreCves`), the chain
flows through `BaseCVEFetcher` naturally — `BaseGitFetcher` does not
define its own `__init_subclass__`, so Python resolves to
`BaseCVEFetcher.__init_subclass__()` as the nearest in the MRO.

**Uniqueness tracking — `_CVE_SOURCE_TYPE_MAP`**:

`BaseCVEFetcher` maintains a module-level dictionary
`_CVE_SOURCE_TYPE_MAP: dict[str, type[BaseCVEFetcher]]` that maps each
registered `cve_source_type` value to its owning class. This structure
serves two purposes:

1. Import-time uniqueness validation (O(1) lookup)
2. Runtime enumeration for `get_fetch_single_fetchers()` (replaces
   MRO-based detection)

**Registration ordering — check-before, register-after**:

The uniqueness check and the registration MUST be separated across the
`super().__init_subclass__()` call to prevent orphaned registrations
when `BaseFetcher.__init_subclass__()` fails (e.g., duplicate `name`):

```python
_CVE_SOURCE_TYPE_MAP: dict[str, type[BaseCVEFetcher]] = {}

class BaseCVEFetcher(BaseFetcher):
    abstract = True
    participates_in_catch_up: bool = True

    def __init_subclass__(cls, **kwargs):
        if not cls.__dict__.get('abstract', False):
            # 1. Validate: Enum membership
            if cls.cve_source_type not in CVESourceType:
                raise TypeError(...)
            # 2. Check uniqueness (read-only, no registration yet)
            if cls.cve_source_type in _CVE_SOURCE_TYPE_MAP:
                raise TypeError(
                    f"Duplicate cve_source_type '{cls.cve_source_type}': "
                    f"already registered by "
                    f"{_CVE_SOURCE_TYPE_MAP[cls.cve_source_type].__name__}"
                )

        # 3. Chain to BaseFetcher (validates name, Settings, registers
        #    in FETCHER_REGISTRY — may raise)
        super().__init_subclass__(**kwargs)

        # 4. Register ONLY after BaseFetcher succeeded
        if not cls.__dict__.get('abstract', False):
            _CVE_SOURCE_TYPE_MAP[cls.cve_source_type] = cls
```

This ordering guarantees: if `BaseFetcher.__init_subclass__()` raises
(step 3), step 4 never executes and `_CVE_SOURCE_TYPE_MAP` remains
clean. No orphaned registrations.

**Test helper extension**: the existing `_clear_fetch_single_cache()`
test utility MUST also clear `_CVE_SOURCE_TYPE_MAP` to prevent
cross-test pollution from dynamically created mock fetcher classes.

### Non-Modification Statement

`BaseCVEFetcher` does not define or modify `execute()`, `run()`, or any
metric helper. The `BaseFetcher` lifecycle contract applies to all CVE
fetchers unchanged. `BaseCVEFetcher` adds only:

1. The `cve_source_type` + `fetch_single()` contract (opt-out via `supports_fetch_single`)
2. The default `catch_up()` implementation
3. CVE-specific import-time validation
4. The `participates_in_catch_up` opt-out for catch-up participation
5. The `supports_fetch_single` opt-out for on-demand single-CVE fetch
6. The `source_reference_url_pattern` attribute (optional, default `None`)
7. The `commit_and_dispatch()` helper method for per-CVE commit and
   Phase 2 task dispatch

### Session Lifecycle for API-based CVE Fetchers

API-based CVE fetchers (NVD, Red Hat, GHSA, OSV, KEV, EPSS) MUST commit
per-CVE in their `execute()` loop. Each iteration has its own
transaction boundary.

#### When `execute()` delegates to `fetch_single()` in a loop

Use this structure when the fetcher iterates over a pre-known list of
CVE-IDs (e.g., all CVEs with active tickets) and delegates per-CVE
processing to `self.fetch_single()`. This allows the same per-CVE logic
to serve both periodic batch execution and on-demand invocation.

```python
async def execute(self, session: AsyncSession) -> None:
    for cve_id in scope:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
        except CVENotInSource:
            await session.rollback()  # defensive: ensure clean session state
        except Exception:
            await session.rollback()
            self.record_failed()
        await asyncio.sleep(self.config.request_delay)
```

#### When `execute()` processes paginated API responses inline

Use this structure when the fetcher iterates over paginated API responses
(e.g., NVD time-window query, GHSA cursor pagination) and processes each
item inline. The CVE-IDs are discovered during iteration, so
`fetch_single()` cannot be used in the loop.

```python
async def execute(self, session: AsyncSession) -> None:
    for item in source_items:
        try:
            result = await upsert_cve(session, cve_id, self.cve_source_type, payload)
            await upsert_references(session, ...)
            post_ingest = build_post_ingest_tasks(result, payload)
            await self.commit_and_dispatch(session, post_ingest)
            # record_created/record_updated based on result.action
        except Exception:
            await session.rollback()
            self.record_failed()
```

Both structures use `commit_and_dispatch()` as the per-CVE finalization
step. The helper commits the session (releasing the `FOR UPDATE` lock
acquired by `upsert_cve()`) and dispatches Phase 2 tasks if
`post_ingest` is not `None`.

**Metric placement**: metric helpers (`record_created`,
`record_updated`, `record_failed`) are in-memory counter increments
with no database interaction. Their placement relative to
`commit_and_dispatch()` is functionally irrelevant — whether recorded
before commit (inside `fetch_single()`) or after commit (in the
`execute()` loop) does not affect correctness.

This session lifecycle was always true for git-based fetchers (the
`BaseGitFetcher` template commits per-item in step 10). This section
formalizes the same pattern for API-based fetchers.

## CVE Fetcher Conventions

All CVE fetchers (inheriting from `BaseCVEFetcher` or `BaseGitFetcher`)
share these conventions. Individual fetcher specifications document only
source-specific deviations.

### Batch Error Handling

All CVE fetchers follow the same error handling pattern for individual
CVE parse/upsert failures during batch execution (`execute()`):

1. Log ERROR with CVE-ID and exception details
2. `await session.rollback()` — clean the session for the next item.
   The rollback discards the `CVESource` "success" written by
   `upsert_cve()`, naturally preserving the previous `CVESource` state.
   No explicit `record_source_status("failure")` is needed in the
   batch path
3. Call `self.record_failed()` and continue processing the next CVE.
   A batch must never abort entirely due to a single CVE failure.
   Source-specific abort conditions (e.g., persistent infrastructure
   failure after N consecutive errors) are documented in each fetcher's
   dedicated specification (see the CVE Fetcher Specifications table
   in `docs/features/tickets/cve-tracking.md`)

**Distinction from the on-demand path**: the `fetch_single_cve`
orchestrator (on-demand path) explicitly writes
`record_source_status("failure"/"missing")` because user-triggered
fetches require visible per-source feedback via the Fetch Status Read
Path (see `docs/features/tickets/cve-service.md`). The `execute()` batch
path does not write explicit failure status — the rollback is sufficient.

Each individual fetcher specification documents only source-specific
error handling deviations (e.g., API-level vs. Git-level failures). The
common pattern above is inherited by all CVE fetchers.

### First Run Behavior

All CVE fetchers are designed for **forward-only ingestion**: they
begin tracking from the moment of deployment and do not bulk-ingest
historical CVE data. First-run behavior is determined by the fetcher's
category:

| Category | First-run behavior | Examples |
|----------|-------------------|----------|
| Cursor-based (API with timestamp/cursor) | Records the current cursor position without fetching data | NVD, GHSA |
| Git-based (`BaseGitFetcher`) | Clones the repository and records HEAD commit SHA without processing files | MITRE, kernel |
| Stateless (iterates over all in-scope CVEs each run) | No first-run distinction; behaves identically to subsequent runs | Red Hat, OSV, EPSS |

Individual fetcher specifications document their specific first-run
behavior in their own Algorithm sections. The category rules above are
inherited from the base class hierarchy (`BaseCVEFetcher` /
`BaseGitFetcher`).

**Historical CVE access**: individual historical CVEs are accessible
on-demand via `fetch_single()`. When a VA associates a historical
CVE-ID with a ticket, the on-demand fetch mechanism
(`trigger_on_demand_fetch()`) retrieves it from the source and ingests
it with the same `cve_service.upsert_cve()` path as batch-processed
CVEs.

### Metric Definitions

Unless otherwise specified per-fetcher, CVE fetchers use these metric
definitions:

- `record_created`: a new CVE record was inserted (first time seen from
  this source)
- `record_updated`: an existing CVE record was updated (metadata, CVSS
  assessments, CWE, references, or other enrichment data changed). If
  `upsert_cve()` produces no changes (all upserts are no-ops), no
  metric is recorded for that CVE
- `record_failed`: a CVE could not be processed (structural parse
  error, unrecognized field values, or database constraint violation)

Individual fetcher specifications document only deviations from these
definitions.

## Git-Based Fetchers

Some fetchers synchronize data from external Git repositories rather
than HTTP APIs. These fetchers share common infrastructure requirements
documented in this section. Individual fetcher specs define their own
algorithm, metrics, and source-specific behavior; this section defines
only the shared operational pattern.

Current git-based fetchers: `sync_mitre_cves`, `sync_kernel_cves`.

### Bare Clone Pattern

Git-based fetchers use **bare clones without a working tree**. This
minimizes disk usage (no checkout of hundreds of thousands of files)
while providing full access to file contents via Git object store
operations.

The pattern:

1. **Clone** (first run only — clone directory does not exist OR is not
   a valid bare git repository): `git clone --bare --single-branch <url>`
   into `$GIT_CLONE_BASE_DIR/<subdirectory>/`. For sources that support
   Git partial clone (protocol v2 with `filter` capability), add
   `--filter=blob:none` to defer blob downloads. For sources that do not
   support filtering (e.g., `git.kernel.org`), use a plain bare clone.
   **Validity check**: before deciding "first run vs. subsequent run",
   verify the directory is a valid bare git repository via
   `git rev-parse --git-dir`. If the directory exists but the check
   fails (partially-initialized clone from a previous interrupted
   attempt), delete the directory and proceed with a fresh clone.
2. **Fetch** (subsequent runs): `git fetch origin` updates refs and
   downloads new objects. This is incremental and typically completes in
   seconds.
3. **Delta detection**: `git diff --name-only --diff-filter=AMCR
   <old_sha>..<new_sha>` returns the list of Added, Modified, Copied,
   and Renamed files. Deleted files are excluded — they do not represent
   CVE data that needs processing.
4. **File content access**: `git show <ref>:<path>` reads a single
   file's content from the object store without creating a working tree.
   For blobless clones, this triggers an on-demand blob download for
   that specific file only.
5. **First-run file enumeration**: `git ls-tree -r --name-only HEAD`
   lists all files in the repository without checkout.

No `git merge`, `git checkout`, or working tree manipulation is
performed at any point.

### Cursor Persistence

Git-based fetchers persist their checkpoint (the last successfully
processed commit SHA) in the `FetcherRun.cursor` JSONB column. After
a run completes with `success` or `partial` status, the fetcher writes:

```json
{"sha": "<40-char hex SHA>", "committed_at": "<ISO 8601 date>"}
```

The next run reads the cursor from the most recent `FetcherRun` with
`status IN ('success', 'partial')` for the same `fetcher_name`:

- `sha`: the HEAD commit SHA at the end of a `success` or `partial` run
- `committed_at`: the committer date of that commit (ISO 8601
  format). Used as the recovery boundary when the cursor SHA becomes
  unreachable (see "Cursor SHA Unreachable" below)

If no run with a cursor exists (first run), the fetcher applies its own
first-run strategy (see the individual fetcher spec — e.g., "record
HEAD only" for CVE fetchers). For recovery scenarios where a stored
SHA is unreachable, the fetcher applies the date-based recovery
strategy (see "Cursor SHA Unreachable" below).

This mechanism is generic — non-git fetchers may use `cursor` for any
checkpoint data (timestamps, offsets, page tokens). The column is
nullable; fetchers that derive their cursor from other fields (e.g.,
NVD uses `started_at`) leave it NULL.

#### Write Mechanism

Inside `execute()`, the fetcher sets `self._cursor` (a dict) with the
checkpoint data. After `execute()` returns, `run()` determines the
final status (see "Status determination precedence") and then, only if
the final status is `success` or `partial`, reads `self._cursor` and
writes it to the `FetcherRun` row in the same transaction that sets
`status` and `finished_at`. If `self._cursor` is None (not set), or
the final status is `failure` (including the all-items-failed case),
no cursor is written.

This avoids giving `execute()` direct access to the `FetcherRun` row
and keeps cursor persistence as a `run()` responsibility — consistent
with how `run()` already manages metrics (`items_created`,
`items_updated`, `items_failed`).

#### Empty Delta

If `git fetch` succeeds but the delta contains zero files matching
the fetcher's filter (no CVE files changed), the run completes with
`status = success`, zero metrics, and the cursor advances to the new
HEAD SHA. This is the normal case during low-activity periods.

### First-Run Detection

A git-based fetcher determines "first run" by the absence of a
`FetcherRun` record with a cursor — NOT by the presence or absence of
the clone directory. The clone directory state is a sub-condition of
the first-run logic:

| Cursor exists? | Clone valid? | Action |
|---|---|---|
| No | No (absent or invalid) | If directory exists but is invalid (fails `git rev-parse --git-dir`): delete entirely. Clone repository. Record HEAD without processing |
| No | Yes | Skip clone (previous attempt succeeded but cursor was not persisted). Record HEAD without processing |
| Yes | Yes | Subsequent run: fetch + delta detection from cursor |
| Yes | No (absent or invalid) | Delete invalid directory if present. Re-clone. Then apply cursor reachability check (see Recovery Strategy below) |

"Invalid" means: the directory exists but `git rev-parse --git-dir`
fails (corrupted pack files, incomplete clone from interrupted
previous attempt, filesystem corruption, etc.).

The cursor-based approach ensures correctness when the first run
clones successfully but fails before persisting the cursor. In that
scenario, a clone-state-based check would incorrectly conclude
"subsequent run" and attempt delta detection without a stored SHA.
The cursor-based check correctly identifies this as a first run and
records HEAD without processing.

For `BaseGitFetcher` subclasses, this decision matrix is implemented
by `BaseGitFetcher.execute()` — concrete fetchers do not reimplement
it. See "BaseGitFetcher Class" below.

### Environment Configuration

| Env Var | Type | Default | Description |
|---------|------|---------|-------------|
| `GIT_CLONE_BASE_DIR` | string (path) | `/var/lib/sentinel/git` | Base directory for all git-based fetcher clones |

Each fetcher creates a subdirectory named after its repository:

```
$GIT_CLONE_BASE_DIR/
├── cvelistV5/      (sync_mitre_cves — bare clone of github.com/CVEProject/cvelistV5)
└── vulns.git/      (sync_kernel_cves — bare clone of git.kernel.org/.../vulns.git)
```

The base directory MUST be backed by persistent storage in containerized
deployments (named volume in Docker/Podman, PersistentVolumeClaim in
Kubernetes). The storage is treated as a **recoverable cache**, not as a
source of truth — if lost or corrupted, the fetcher re-clones
automatically (see Recovery below).

### Volume Requirements

| Property | Value |
|----------|-------|
| Persistence | Required across container restarts |
| Capacity | 1 GB minimum (current usage ~400 MB; provides headroom for growth and transient git operations) |
| Access mode | ReadWriteOnce (single worker pod) |
| Filesystem | Any POSIX-compliant filesystem |
| Backup | Not required (recoverable from upstream repos) |

### Worker Affinity

Git-based fetcher tasks MUST execute on a Celery worker with the Git
volume mounted. This is achieved via a dedicated Celery queue:

- **Queue name**: `git`
- **Routing**: `BaseGitFetcher` sets `queue = "git"` as a fixed class
  attribute — all concrete subclasses inherit this value automatically.
  Fetchers that inherit from `BaseFetcher` directly and need git queue
  affinity set it in their own class body
- **`queue` class attribute on BaseFetcher**: `BaseFetcher` defines a
  `queue: str | None = None` class attribute (default = default Celery
  queue). `BaseGitFetcher` overrides it to `"git"` for the entire
  git-fetcher hierarchy. Non-git fetchers that omit it are routed
  normally — safe by default
- **Worker configuration**: the worker process with access to the Git
  volume consumes from the `git` queue (in addition to the default
  queue, if desired)
- **`fetch_single()` routing**: `trigger_on_demand_fetch()` reads
  `fetcher_cls.queue` when dispatching via `.apply_async(queue=...)`.
  If `None`, no queue parameter is passed and Celery uses default
  routing. This ensures on-demand fetches for git-based fetchers
  reach the worker with the volume mounted

In single-worker deployments (local dev, simple Docker/Podman), all
queues are consumed by the same worker process and no explicit routing
configuration is needed.

### Concurrency Rules

These rules apply to ALL git-based fetchers sharing the same volume:

1. **Only the periodic sync modifies the clone**: `git fetch` and any
   other write operations are performed exclusively by the periodic
   sync task. `fetch_single()` MUST NOT run `git fetch` or any
   operation that modifies the object store or refs.
2. **`fetch_single()` reads from the object store only**: uses
   `git show <ref>:<path>` (via async subprocess) to read committed
   objects. The Git object store is append-only with atomic file
   operations — concurrent reads during a `git fetch` are safe.
3. **Stale reads are acceptable**: if `fetch_single()` reads HEAD just
   before `git fetch` updates it, a recently-published CVE might not be
   found. This is not an error — `trigger_on_demand_fetch()` dispatches
   all registered fetchers and other sources may succeed.
4. **No concurrent fetches per repo**: two periodic sync tasks for the
   same repository MUST NOT run concurrently. The fetcher infrastructure
   already enforces this via the singleton execution guarantee
   (BaseFetcher prevents overlapping runs for the same fetcher).
5. **Cross-fetcher concurrency is safe**: different git-based fetchers
   operating on distinct subdirectories within `$GIT_CLONE_BASE_DIR`
   MAY execute concurrently. The singleton constraint — no overlapping
   runs of the same fetcher — is enforced by `BaseFetcher` (see
   "BaseFetcher Base Class" above). It applies per-fetcher, not
   per-volume. A `sync_mitre_cves` run and a `sync_kernel_cves` run
   can overlap without conflict.

### Recovery

**Volume loss** (directory does not exist):

1. Re-clone the repository (same clone command as first run)
2. Read the `cursor` from the last `FetcherRun` with
   `status IN ('success', 'partial')` for this fetcher in the database
3. Check if the stored SHA exists in the new clone
   (`git cat-file -t <sha>`)
4. If reachable: normal delta processing from stored SHA to HEAD
5. If not reachable (upstream force-push, branch deletion, or SHA
   garbage-collected): apply the date-based recovery strategy (see
   "Cursor SHA Unreachable" below). For `BaseGitFetcher` subclasses
   this is handled automatically by `execute()` — only
   `recovery_path_prefix` varies per fetcher

**Corrupted clone** (git operations fail with corruption errors):

1. Log WARNING with the error details
2. Delete the entire clone directory
3. Re-clone (same as volume loss recovery)

#### Cursor SHA Unreachable

When a git-based fetcher's stored cursor SHA is not reachable in the
local clone (detected via `git cat-file -t <sha>` returning non-zero),
it applies a date-based recovery strategy using the `committed_at`
field stored in the cursor. This situation occurs when:

- The clone was rebuilt (row 4 of the First-Run Detection table)
- The upstream repository was force-pushed or rebased (rare for
  published CVE/advisory repos)
- Git garbage collection pruned unreachable objects (should not
  happen for commits reachable from HEAD, but possible with
  corrupted state)

**Algorithm**:

1. Compute `before_date` as `cursor_committed_at` minus 1 day (the
   1-day margin ensures no items are missed around the boundary —
   reprocessing is idempotent)
2. Determine boundary SHA:
   `git rev-list -1 --before="<before_date>" HEAD`
3. If no commit exists before `before_date` (empty output — the
   repository history does not extend that far back): log WARNING
   ("Recovery boundary not found — treating as first-run"), return
   empty delta. Cursor advances to HEAD
4. Compute delta:
   `git diff --name-only --diff-filter=AMCR <boundary_sha>..HEAD
   -- '<recovery_path_prefix>'`
5. Apply the fetcher's normal file filtering and per-item processing
   logic (MUST be idempotent — previously ingested items produce no
   observable side effects on re-processing)
6. Write HEAD as new cursor on completion

Each git-based fetcher declares this parameter in its properties
table:

| Parameter | Description | Example values |
|---|---|---|
| `recovery_path_prefix` | Path filter for the recovery delta command | `cves/` (MITRE), `cve/` (kernel) |

**Advantages over a fixed window**: the date-based approach always
covers the exact gap regardless of how long the fetcher was offline.
Reprocessing overlap is always ~1 day (idempotent, negligible cost).
No configurable `recovery_window` parameter is needed.

**Normal case after re-clone**: when a clone is rebuilt from the
same remote (row 4 of First-Run Detection), the cursor SHA is
almost always reachable because git history is preserved. In this
case, normal delta detection proceeds — no recovery is needed. The
recovery strategy is a fallback for the rare case where the SHA
truly does not exist in the fresh clone.

For `BaseGitFetcher` subclasses, this recovery algorithm is
implemented by `BaseGitFetcher.execute()` — concrete fetchers only
declare `recovery_path_prefix` as a class attribute. See
"BaseGitFetcher Class" below.

### Runtime Dependencies

Git-based fetchers require the `git` binary available in the
container image of the worker that consumes the `git` queue.

| Dependency | Minimum version | Reason |
|---|---|---|
| `git` | 2.25 | First stable release with partial clone (`--filter`) support. Required for blobless clones of cvelistV5 |

The `python:3.12-slim` base image does not include git — it must be
added explicitly to the container image.

**No Python Git library is used.** All git operations are performed
via async subprocess invocation of the system `git` binary through a
shared internal helper. This decision is based on:

- `pygit2` (libgit2 bindings): **eliminated** — libgit2 cannot open
  repositories with the `extensions.partialclone` extension
  (libgit2/libgit2#5564, open since Jun 2020; #6880 confirms the
  error persists in v1.7.2, Sep 2024). Unusable with blobless clones
- `GitPython`: **eliminated** — 8 security advisories including 5
  High-severity RCE/command-injection vulnerabilities published
  April–May 2026 affecting all platforms. Unacceptable for a security
  platform
- Raw subprocess: no additional Python dependency, full access to all
  git features (partial clone, protocol v2), no additional attack
  surface

The helper provides typed exceptions for phase-based error
classification (see "Error Classification" below), with hardcoded
timeouts per operation category:

| Operation | Timeout | Examples |
|---|---|---|
| Clone | 20 minutes | Initial bare clone (~300 MB download) |
| Fetch | 5 minutes | Incremental `git fetch origin` |
| Read | 30 seconds | `git show`, `git log`, `git ls-tree`, `git rev-parse` |

### Error Classification

Git operation failures are classified by the **phase** in which they
occur, not by parsing exit codes or stderr messages. This avoids
fragile dependencies on git's unstable error message format.

```python
class GitError(Exception): ...
class GitFetchError(GitError): ...       # Transient — clone is intact
class GitCorruptionError(GitError): ...  # Delete + re-clone required
class GitFileError(GitError): ...        # Per-file — continue processing
```

| Phase | Failure condition | Exception | Fetcher action |
|-------|-------------------|-----------|----------------|
| `git clone` / `git fetch` | Any failure (network, auth, timeout) | `GitFetchError` | Do NOT delete clone. Raise `FetcherError`. Next cycle retries |
| Read after successful fetch (`git diff`, `git rev-parse`, `git ls-tree`, `git cat-file -t`) | Any failure | `GitCorruptionError` | Delete clone directory. Raise `FetcherError`. Next cycle re-clones + applies recovery strategy |
| `git show` during delta file processing | Any failure (timeout, missing blob) | `GitFileError` | `record_failed()` for that item. Continue to next file |

**Design rationale**: classification is purely phase-based because a
successful `git fetch` proves network connectivity. If a subsequent
read operation fails, the only remaining explanation is local
corruption. No stderr parsing or exit code mapping is needed.

**No anti-loop logic**: Celery task timeout limits each run's
duration. Repeated failures (e.g., corruption loop from faulty disk)
produce visible `failure` records in the fetcher dashboard for
operator intervention.

### Implementation Location

The shared async subprocess helper for git operations lives at
`backend/app/services/git_operations.py`. All git-based fetchers
import from this module — they MUST NOT invoke `subprocess` or
`asyncio.create_subprocess_exec` for git commands directly.

The module exports:
- Async functions for each git operation category (clone, fetch, read
  operations, show)
- The exception hierarchy (`GitError`, `GitFetchError`,
  `GitCorruptionError`, `GitFileError`)
- Timeout constants per operation category

#### Design Principles

The git operations module is NOT a "service" in the Sentinel
service-layer sense:

- Contains stateless utility functions (no database interaction, no
  business logic)
- Centralizes subprocess error handling and maps git failures to the
  typed exception hierarchy
- Is consumed by `BaseGitFetcher` methods, which delegate subprocess
  execution to this module. Can also be used independently by code that
  needs git operations without the `BaseGitFetcher` lifecycle (e.g.,
  fetchers inheriting from `BaseFetcher` directly)
- Provides a clean mocking boundary for unit tests (mock one function
  instead of `subprocess.run`)

Fetchers that inherit from `BaseGitFetcher` delegate execution flow
to the template method — they implement only processing hooks.
Fetchers that inherit from `BaseFetcher` directly retain full control
over their execution flow, using the utility functions as building
blocks.

#### Responsibility Separation

The utility module is **policy-free** — it executes git commands with
the parameters it receives. It does not apply domain-specific defaults.

- **Domain defaults** (bare=True, filter=blob:none, single-branch=True)
  live on `BaseGitFetcher` class attributes
- **`BaseGitFetcher` methods** read `self.*` attributes and pass them as
  explicit parameters to `git_operations` functions
- **Concrete subclasses** override class attributes to change behavior
  (e.g., kernel sets `clone_filter = None`)

This separation ensures `git_operations.py` remains general-purpose and
independently usable.

#### Function Catalog

The following table defines the complete public interface of
`git_operations.py`. These are the functions that `BaseGitFetcher`
delegates to and that any `BaseFetcher`-direct subclass may also call.

##### Clone Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `clone` | `async def clone(url: str, dest: Path, *, bare: bool = False, filter_spec: str \| None = None, single_branch: bool = False) -> None` | `None` | Clone (20 min) | `GitFetchError` |

**Behavior**:

1. Build the base command: `["git", "clone", url, str(dest)]`
2. If `bare` is `True`: append `--bare`
3. If `filter_spec` is not `None`: append `--filter=<filter_spec>`
4. If `single_branch` is `True`: append `--single-branch`
5. Execute the command via `asyncio.create_subprocess_exec` with the
   clone timeout (20 minutes)
6. If the process exits with non-zero code: raise `GitFetchError` with
   stderr content

##### Fetch Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `fetch_origin` | `async def fetch_origin(repo_path: Path) -> None` | `None` | Fetch (5 min) | `GitFetchError` |

Semantics: runs `git fetch origin` in the specified repository.
Incremental — only new objects are transferred.

##### Read Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `get_head_sha` | `async def get_head_sha(repo_path: Path) -> str` | 40-char hex SHA | Read (30 sec) | `GitCorruptionError` |
| `get_commit_date` | `async def get_commit_date(repo_path: Path, ref: str) -> str` | ISO 8601 date string in UTC (e.g., `2025-06-01T18:00:00+00:00`) | Read (30 sec) | `GitCorruptionError` |
| `is_clone_valid` | `async def is_clone_valid(repo_path: Path) -> bool` | `bool` | Read (30 sec) | Never (returns `False` on any failure) |
| `check_sha_reachable` | `async def check_sha_reachable(repo_path: Path, sha: str) -> bool` | `bool` | Read (30 sec) | `GitCorruptionError` (only for unexpected failures; unreachable SHA returns `False`) |
| `diff_names` | `async def diff_names(repo_path: Path, from_sha: str, to_sha: str, *, path_filter: str \| None = None) -> list[str]` | List of file paths | Read (30 sec) | `GitCorruptionError` |
| `rev_list_before` | `async def rev_list_before(repo_path: Path, before_date: str) -> str \| None` | 40-char hex SHA or `None` | Read (30 sec) | `GitCorruptionError` |

Semantics:

- **`get_head_sha`**: returns the commit SHA that HEAD points to
  (`git rev-parse HEAD`)
- **`get_commit_date`**: returns the committer date of the specified ref
  as an ISO 8601 string normalized to UTC
  (`git log -1 --format=%cI <ref>`, then converted to UTC; or
  equivalently, executed with `TZ=UTC` environment to produce UTC
  output directly). Follows the project's "UTC everywhere" convention
  (`docs/conventions.md`, Timestamps & Timezones). Used to store
  `committed_at` in the cursor for recovery boundary computation
- **`is_clone_valid`**: returns `True` if `repo_path` is a valid git
  repository (`git rev-parse --git-dir` succeeds). Returns `False` if
  the directory does not exist, is not a git repository, or the check
  fails for any reason. NEVER raises — used as a guard condition
- **`check_sha_reachable`**: determines whether a given SHA exists in
  the local object store as a valid git object
  (`git cat-file -t <sha>`).

  **Behavior**:

  1. Execute `git cat-file -t <sha>` in the repository
  2. If exit code is 0: return `True` (object exists and is valid)
  3. If exit code is 1 and stderr indicates "not a valid object name" or
     similar: return `False` (SHA not reachable — expected condition)
  4. If exit code indicates a different failure (I/O error, repository
     corruption): raise `GitCorruptionError`

- **`diff_names`**: returns the list of added, modified, copied, and
  renamed files between two commits
  (`git diff --name-only --diff-filter=AMCR <from>..<to>`). If
  `path_filter` is set, appends `-- '<path_filter>'` to restrict
  results. Deleted files are excluded
- **`rev_list_before`**: returns the most recent commit SHA on HEAD
  before the specified date
  (`git rev-list -1 --before="<before_date>" HEAD`). Returns `None` if
  no commit exists before the specified date (empty output from git).
  Used for recovery boundary detection

##### Show Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `show_file` | `async def show_file(repo_path: Path, ref: str, file_path: str) -> bytes \| None` | File content as `bytes`, or `None` if path does not exist | Read (30 sec) | `GitFileError` (for errors other than "path not found") |

**Behavior**:

1. Execute `git show <ref>:<file_path>` in the repository
2. If exit code is 0: return stdout as `bytes` (file content)
3. If exit code is 128 and stderr contains "does not exist in" or
   "path not found": return `None` (file does not exist at this ref —
   expected condition, not an error)
4. If exit code indicates a different failure (network error in blobless
   clone, corrupt object, timeout): raise `GitFileError` with stderr
   content

In blobless clones, step 1 triggers an on-demand blob download from the
remote — requires network access. If the remote is unreachable, step 4
fires.

##### Filesystem Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `delete_clone` | `async def delete_clone(path: Path) -> None` | `None` | N/A | `OSError` (filesystem errors) |

Semantics: recursively deletes the directory at `path` if it exists.
No-op if the path does not exist. This is NOT a git operation — it is
included in the module for co-location with clone lifecycle management.

#### Bare and Blobless Compatibility

All git operations in the function catalog are compatible with both
plain bare clones and blobless bare clones (`--filter=blob:none`):

- **Local-only operations** (`get_head_sha`, `get_commit_date`,
  `is_clone_valid`, `check_sha_reachable`, `diff_names`,
  `rev_list_before`): access only commit and tree objects, which are
  always present locally in both plain and blobless clones. No network
  access required
- **On-demand operations** (`show_file`): access blob content. In
  blobless clones, this triggers an on-demand blob download from the
  remote for the specific file requested. This is the intended access
  pattern — blobs are fetched individually rather than bulk-downloaded
  during clone or fetch
- **`check_sha_reachable` on commit SHAs**: `BaseGitFetcher` uses this
  exclusively on commit SHAs (the cursor). Commit objects are always
  present locally, even in blobless clones. The function would also
  work on tree SHAs (present locally) but NOT reliably on blob SHAs
  (may be absent in blobless clones)

No special handling is needed per clone type — the git binary
transparently handles both modes. The only operational difference is
that `show_file` requires network access in blobless clones (and may
raise `GitFileError` if the remote is unreachable at that moment).

### BaseGitFetcher Class

Template Method intermediate class for fetchers that follow the standard
delta-based git flow (clone → fetch → SHA reachability → delta detection
→ per-item processing → cursor advance). Eliminates duplicated state
machine code by providing a single `execute()` implementation that
delegates per-item processing to concrete subclasses via hook methods.

**Class hierarchy**:

```
BaseFetcher (generic: lifecycle, metrics, FetcherRun, cursor, registry)
  └── BaseCVEFetcher (CVE-specific: cve_source_type, fetch_single (opt-out), catch_up)
        └── BaseGitFetcher (git-specific: clone, fetch, delta, recovery, SHA ops)
              ├── SyncMitreCves (per-item: cvelistV5 JSON, CNA+ADP containers)
              └── SyncKernelCves (per-item: vulns.git JSON, kernel-specific mapping)
```

**File location**: `backend/app/services/base_git_fetcher.py`

#### Class Attributes

Concrete subclasses declare the configurable attributes as class-level
values. The fixed attributes are set by `BaseGitFetcher` and inherited
automatically.

**Configurable (declared by subclasses)**:

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `repo_url` | `str` | (required) | Git remote URL |
| `clone_dir_name` | `str` | (required) | Directory name under `$GIT_CLONE_BASE_DIR` |
| `clone_bare` | `bool` | `True` | Whether to use `--bare` |
| `clone_filter` | `str \| None` | `"blob:none"` | Value for `--filter=`. `None` = no filter (plain bare clone) |
| `clone_single_branch` | `bool` | `True` | Whether to use `--single-branch` |
| `recovery_path_prefix` | `str` | (required) | Path prefix for recovery delta (`-- '<prefix>'`) |
| `delta_path_prefix` | `str` | (required) | Path prefix for normal delta detection |

**Fixed (set by `BaseGitFetcher`, not overridable)**:

| Attribute | Value | Description |
|-----------|-------|-------------|
| `abstract` | `True` | Prevents registration in `FETCHER_REGISTRY` (intermediate class, not a concrete fetcher). Both `BaseCVEFetcher` and `BaseGitFetcher` set `abstract = True` (both are intermediate classes). Concrete subclasses do not set `abstract` in their own class body; `__init_subclass__` checks `cls.__dict__.get('abstract', False)` and proceeds with registration when the attribute is absent from the subclass's own namespace |
| `queue` | `"git"` | Celery queue for worker affinity. Ensures tasks execute on the worker with the git volume mounted. Inherited from `BaseFetcher` interface (default `None`), overridden at the `BaseGitFetcher` level |

These configurable attributes are also exposed in each fetcher's
properties table in its specification document. The fixed `queue`
attribute is inherited automatically and does not appear in
per-fetcher properties tables.

#### Template Method: `execute()`

The `execute()` method implements the full git-based fetcher state
machine. Concrete subclasses MUST NOT override `execute()` (they
implement hooks instead).

Implements the First-Run Detection truth table and Recovery Strategy
algorithm from the sections above.

**Behavior**:

1. Resolve repository path from `$GIT_CLONE_BASE_DIR / clone_dir_name`
2. Read last cursor from the previous successful `FetcherRun.cursor`
   (via `BaseFetcher`). Extract `cursor_sha` and `cursor_committed_at`
   from the stored dict. If no prior successful run exists, both are
   `None`
3. **First-run branch** — if `cursor_sha` is `None`:
   a. If clone is NOT valid at `repo_path`: delete directory if it
      exists, then clone repository with configured options
   b. If clone IS valid: skip clone (reuse existing)
   c. Read HEAD SHA from the repository
   d. Read HEAD commit date via `get_commit_date(repo_path, "HEAD")`
   e. Set cursor to `{"sha": head_sha, "committed_at": head_date}`
   f. Return — first-run complete, no items processed
4. **Subsequent-run branch** — `cursor_sha` exists:
   a. If clone is NOT valid: log WARNING ("Clone invalid but cursor
      exists — rebuilding"), delete directory if exists, clone repository
   b. If clone IS valid: fetch origin (incremental update)
5. Read HEAD SHA from the repository
6. Read HEAD commit date via `get_commit_date(repo_path, "HEAD")`
7. **SHA reachability check**:
   a. If `cursor_sha` is NOT reachable in the local object store: log
      WARNING ("Cursor SHA unreachable — applying recovery"), compute
      recovery delta via
      `_compute_recovery_delta(repo_path, head_sha, cursor_committed_at)`
   b. If `cursor_sha` IS reachable: compute normal delta via
      `diff_names(repo_path, cursor_sha, head_sha)` with
      `delta_path_prefix` as path filter
8. Apply `filter_delta_files()` hook on the file list
9. Apply `deduplicate_items()` hook on the filtered list
10. **Per-item processing loop** — for each `path` in the deduplicated
    list:
    a. Read file content via `show_file(repo_path, "HEAD", path)`
    b. If content is `None` (file not found at HEAD — file was added
       then deleted/renamed between cursor and HEAD): log WARNING
       ("File {path} in delta but not at HEAD — skipping"), continue
       to next item. No metric is recorded
    c. Call `post_ingest = process_item(path, content, session)` →
       returns `PostIngestTasks | None`. On successful return, call
       `self.commit_and_dispatch(session, post_ingest)`
    d. If any exception is raised during steps 10a, 10c, or
       `commit_and_dispatch()`: call `session.rollback()`, log WARNING
       ("Failed to process {path}: {error}"), call `record_failed()`,
       continue to next item

    **Transaction boundaries**: each iteration of the processing loop
    operates in its own transaction boundary. `process_item()` returns
    `PostIngestTasks | None`; after a successful return, the template
    calls `self.commit_and_dispatch(session, post_ingest)` which
    commits the session and dispatches Phase 2 tasks if `post_ingest`
    is not `None`. On exception (caught by step 10d), the template
    calls `session.rollback()` before `record_failed()`. This ensures
    that a failure in one item does not corrupt the session or affect
    the processing of subsequent items.

11. Set cursor to `{"sha": head_sha, "committed_at": head_date}`

Note: the all-items-failed safety check (preventing cursor advance when
every item fails) is handled by `BaseFetcher.run()` after `execute()`
returns — see "Status determination precedence" in the BaseFetcher
section. Items skipped in step 10b (file not at HEAD) do not increment
any counter and do not trigger the safety check.

**Infrastructure errors**: exceptions from clone, fetch, HEAD read, or
delta computation propagate naturally — `BaseFetcher.run()` catches them
and records a failed run without advancing the cursor. The template
method does NOT catch infrastructure-level exceptions. On the next
scheduled run, the First-Run Detection truth table re-evaluates the
clone state and applies appropriate recovery.

**Error Handling Strategy**:

Infrastructure failures and their outcomes:

| Infrastructure failure | Exception | BaseFetcher behavior |
|------------------------|-----------|---------------------|
| Clone fails (network) | `GitFetchError` | `status = failure`, cursor not advanced |
| Fetch fails (network) | `GitFetchError` | `status = failure`, cursor not advanced |
| HEAD unreadable (corruption) | `GitCorruptionError` | `status = failure`, cursor not advanced |
| Delta computation fails | `GitCorruptionError` | `status = failure`, cursor not advanced |

On the next scheduled run, the First-Run Detection truth table
re-evaluates the clone state and applies the appropriate recovery
(row "Cursor exists + Clone invalid" → re-clone).

The **all-items-failed safety check** is now handled by
`BaseFetcher.run()` (see "Status determination precedence" in the
BaseFetcher section). If all items fail (e.g., network drops after fetch
in a blobless clone, making every `show_file()` fail), `run()` sets
`status = failure` directly after `execute()` returns. Since the cursor
is only persisted on `success` or `partial`, the previous cursor is
preserved and the next run retries the same delta. This applies to all
fetcher subclasses uniformly, not just git-based fetchers.

**Status Determination**:

`BaseGitFetcher` relies entirely on `BaseFetcher`'s existing status
mechanism — no additional logic is needed:

| Scenario | Status | Cursor advances? |
|----------|--------|-----------------|
| First run (no processing) | `success` | Yes (step 3e) |
| Empty delta (HEAD unchanged) | `success` | Yes (step 11) |
| All items succeed | `success` | Yes (step 11) |
| Some items fail, some succeed | `partial` | Yes (step 11) |
| All items fail | `failure` | No (`BaseFetcher.run()` safety check) |
| Infrastructure error | `failure` | No (propagates) |

#### Hook Methods (Override Points)

These are the extension points for concrete subclasses:

**Hooks for `execute()`**:

| Method | Required? | Default | Purpose |
|--------|-----------|---------|---------|
| `process_item(path, content, session)` | **Yes** (abstract) | — | Process a single file from the delta. Calls `self.record_created()` or `self.record_updated()` on success |
| `filter_delta_files(file_list)` | No | Return all | Filter raw delta output to relevant files (e.g., only `.json` in specific dirs) |
| `deduplicate_items(file_list)` | No | No-op | Deduplicate items before processing (e.g., same CVE-ID in both `published/` and `rejected/`) |

**Hooks for `fetch_single()`**:

| Method | Required? | Default | Purpose |
|--------|-----------|---------|---------|
| `_construct_candidate_paths(item_id)` | **Yes** (abstract) | — | Return ordered list of candidate file paths for local clone lookup |

##### `process_item(path: str, content: bytes, session: AsyncSession) -> PostIngestTasks | None`

The core extension point. Receives:
- `path`: relative path within the repository (e.g., `cve/published/2024/CVE-2024-50055.json`)
- `content`: raw file content as bytes (from `git show`)
- `session`: the database session for the current execution (same
  `AsyncSession` instance passed to `execute()` by `BaseFetcher.run()`)

The hook is responsible for:
1. Parsing the content and applying business logic (upsert, etc.)
2. Calling `self.record_created()` or `self.record_updated()` to report
   the outcome (same pattern as non-git `BaseFetcher` subclasses)
3. Returning `PostIngestTasks` if post-ingest dispatch is needed, or
   `None` in two cases: (a) the item was skipped (already up-to-date,
   no work done — no metric is recorded), or (b) the item was
   processed but no post-ingest tasks are needed (e.g.,
   enrichment-only upsert with no ticket or no CPE data — metric IS
   recorded). Both `None` cases result in
   `commit_and_dispatch(session, None)` — the template commits without
   dispatching Phase 2 tasks

Raises any exception on failure → caught by `execute()`, logged,
`record_failed()` called.

**Phase 2 side effects**: hooks that call `cve_service.upsert_cve()`
return `PostIngestTasks` containing the Phase 2 task arguments. The
`BaseGitFetcher` template dispatches these tasks via
`commit_and_dispatch()` after committing the per-item transaction.
No post-processing batch hook is needed — Phase 2 is per-item and
self-contained.

##### `filter_delta_files(file_list: list[str]) -> list[str]`

Optional override. Receives the file list from `git diff` (after
applying `delta_path_prefix` path restriction at the git level).
Returns only the files that should be processed.

The two-level filtering design:
- **`delta_path_prefix`** (class attribute): coarse path-prefix
  filtering at the git subprocess level (`-- '<prefix>'`). Reduces the
  diff output before it reaches Python
- **`filter_delta_files()`** (hook): fine-grained filtering in Python
  (e.g., regex matching, extension checks, directory logic)

Example (kernel): keep only `.json` files matching
`cve/{published,rejected}/YEAR/CVE-YEAR-ID.json`.

##### `deduplicate_items(file_list: list[str]) -> list[str]`

Optional override. Receives the filtered file list. Returns a
deduplicated list resolving conflicts (e.g., if same CVE appears in
both `published/` and `rejected/`, keep only the `rejected/` entry).

Default implementation returns the list unchanged.

#### Default `fetch_single()` Implementation

`BaseGitFetcher` provides a concrete implementation of `fetch_single()`
that overrides the `BaseCVEFetcher` default (which raises `RuntimeError`).
Concrete subclasses inherit it automatically (no override needed).

**Behavior**:

1. Resolve repository path from `$GIT_CLONE_BASE_DIR / clone_dir_name`
2. Check if clone is valid at `repo_path`. If NOT valid: raise
   `RuntimeError` ("Clone not available at {repo_path} for single-item
   lookup")
3. Call `_construct_candidate_paths(item_id)` to obtain an ordered list
   of candidate file paths
4. For each `path` in the candidate list:
   a. Read file content via `show_file(repo_path, "HEAD", path)`
   b. If content is not `None` (file found): return the result of
      `process_item(path, content, session)` (`PostIngestTasks | None`)
5. If no candidate path produced content: raise
   `CVENotInSource(item_id)`

**Audit events**: none created directly. Side effects (DB mutations,
audit events) are delegated entirely to `process_item()` — the
concrete subclass's hook determines what is recorded.

**Re-invocation**: calling `fetch_single()` with the same `item_id`
multiple times is safe. Idempotency depends on the concrete
`process_item()` implementation — both current subclasses
(`SyncMitreCves`, `SyncKernelCves`) delegate to `cve_service.upsert_cve()`
which is idempotent (no-op if data unchanged, update if changed).

**Exceptions**:

- `RuntimeError` — clone not available (step 2)
- `CVENotInSource` — item not found in any candidate path (step 5)
- Exceptions from `process_item()` propagate uncaught to the caller

The two exception types serve different purposes for the caller
(`trigger_on_demand_fetch()`):

- **`RuntimeError`** — "source not queryable right now" (clone missing,
  corrupt, or not yet created by the first periodic run). The dispatch
  system logs a WARNING and tries the next fetcher. The clone will be
  available after the next scheduled sync.
- **`CVENotInSource`** — "item does not exist in this source"
  (authoritative negative). The dispatch system logs INFO and tries the
  next fetcher.

##### `_construct_candidate_paths(item_id: str) -> list[str]`

Abstract method. Returns an ordered list of candidate file paths where
the item might exist in the repository. The default `fetch_single()`
tries each path in order via `git show` until one succeeds.

The ordering is significant: the first match wins. If the same item
could exist in multiple locations (e.g., `published/` vs. `rejected/`),
place the most likely or authoritative path first.

```python
# Kernel example:
def _construct_candidate_paths(self, cve_id: str) -> list[str]:
    year = cve_id.split("-")[1]  # CVE-YYYY-NNNNN → YYYY
    return [
        f"cve/published/{year}/{cve_id}.json",
        f"cve/rejected/{year}/{cve_id}.json",
    ]
```

#### Inherited Utility Methods

These methods are available to concrete subclasses (e.g., for use in
`fetch_single()`). They are NOT hook methods — subclasses call them
but do not override them.

All methods in this table propagate exceptions from the corresponding
`git_operations` function they delegate to. No method creates audit
events or mutates database state — they are pure infrastructure
operations.

| Method | Purpose |
|--------|---------|
| `_get_last_cursor_sha()` | Reads the `"sha"` field from the previous `FetcherRun.cursor` (via `BaseFetcher`). Returns `None` if no prior successful run exists |
| `_get_last_cursor_committed_at()` | Reads the `"committed_at"` field from the previous `FetcherRun.cursor` (via `BaseFetcher`). Returns `None` if no prior successful run exists or if the field is absent |
| `_repo_path()` | Returns `Path($GIT_CLONE_BASE_DIR / clone_dir_name)` |
| `_clone_repo(path)` | Clones the repository with configured options (bare, filter, single-branch). Delegates to `git_operations.clone()` |
| `_fetch_origin(path)` | Runs `git fetch origin`. Delegates to `git_operations.fetch_origin()` |
| `_get_head_sha(path)` | Returns current HEAD SHA. Delegates to `git_operations.get_head_sha()` |
| `_get_commit_date(path, ref)` | Returns commit date as ISO 8601 string in UTC. Delegates to `git_operations.get_commit_date()` |
| `_is_clone_valid(path)` | Returns bool. Delegates to `git_operations.is_clone_valid()` |
| `_check_sha_reachable(path, sha)` | Returns bool. Delegates to `git_operations.check_sha_reachable()` |
| `_compute_delta(path, from_sha, to_sha)` | Returns file list from `git diff` with `delta_path_prefix`. Delegates to `git_operations.diff_names()` |
| `_compute_recovery_delta(repo_path, head_sha, cursor_committed_at)` | Applies recovery using stored commit date minus 1 day + `recovery_path_prefix`. See detailed behavior below |
| `_show_file(path, ref, file_path)` | Returns file content or None. Delegates to `git_operations.show_file()` |
| `_delete_if_exists(path)` | Deletes directory if it exists. Delegates to `git_operations.delete_clone()` |

##### `_compute_recovery_delta()`

Computes the file delta using the stored cursor commit date when the
cursor SHA is unreachable (force-push, history rewrite, or clone
rebuild).

`async def _compute_recovery_delta(repo_path: Path, head_sha: str, cursor_committed_at: str) -> list[str]`

**Behavior**:

1. Compute `before_date` as `cursor_committed_at` minus 1 day (the
   1-day margin ensures no items are missed around the boundary —
   reprocessing is idempotent)
2. Call `rev_list_before(repo_path, before_date)` to find the boundary
   SHA — the most recent commit before `before_date`
3. If `rev_list_before` returns `None` (no commit exists before
   `before_date` — the repository history does not extend that far
   back):
   a. Log WARNING: "Recovery boundary not found — repository may have
      been completely rewritten. Treating as first-run. Cursor reset to
      HEAD. Use fetch_single() to recover specific items if needed."
   b. Return empty list
4. Call `diff_names(repo_path, boundary_sha, head_sha)` with
   `recovery_path_prefix` as path filter
5. Return the file list

**Edge case rationale (step 3)**: this scenario requires that ALL
commits reachable from HEAD have dates more recent than the stored
`cursor_committed_at - 1 day`. For repositories like cvelistV5
(history since 2022) or vulns.git (history since 2024), this is
virtually impossible — it would require complete repository recreation
with new commit dates. The first-run treatment (cursor advances to
HEAD, zero items processed) is the correct response: the fetcher
restarts cleanly and `fetch_single()` is available for manual recovery
of specific items.

**Exceptions**: propagates `GitCorruptionError` from `rev_list_before()`
(step 2) and `diff_names()` (step 4).

#### Registry Detection Predicate Update

The `get_fetch_single_fetchers()` and `get_catch_up_fetchers()` registry
accessors use `_CVE_SOURCE_TYPE_MAP` and `BaseCVEFetcher` subclass
detection respectively (see "On-demand Single-Item Fetch" and
"Per-Ticket Catch-Up" sections above). Since `BaseGitFetcher` inherits
from `BaseCVEFetcher`, its concrete subclasses are automatically
included in both accessors — they declare their own `cve_source_type`
via the `BaseCVEFetcher.__init_subclass__` chain.

#### When NOT to Use `BaseGitFetcher`

`BaseGitFetcher` is NOT a requirement for all fetchers that interact
with git repositories. It is the correct choice only for fetchers that
follow the standard delta-based flow (clone → fetch → SHA reachability
→ delta detection → per-item processing → cursor advance).

A future git-based fetcher MUST inherit from `BaseCVEFetcher` directly
(using `git_operations.py` as a utility module) when it is a CVE
fetcher but:

- It requires multi-branch tracking (the template assumes
  single-branch, single HEAD)
- It uses sparse checkout or non-standard clone strategies not
  expressible via the class attributes
- Its delta detection is not commit-range based (e.g., full tree scan,
  tag-based comparison)
- It needs non-linear traversal (e.g., walking merge commits
  individually)
- Its processing flow has steps between delta detection and per-item
  processing that cannot be expressed as a filter hook

A non-CVE git-based fetcher inherits from `BaseFetcher` directly
(using `git_operations.py` as a utility module).

In these cases, `BaseCVEFetcher` (or `BaseFetcher`) +
`git_operations.py` provides the same subprocess utilities without
imposing a fixed execution order.

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
| cursor | JSONB | nullable | Fetcher-defined checkpoint for the next run. Generic: may contain a commit SHA, timestamp, offset, page token, or any structured cursor. Written when the final run status is `success` or `partial`; read by the next run to determine the starting point. See "Git-Based Fetchers" for the git-specific usage pattern |
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

See Guardrail 14 in `AGENTS.md`. Every background task that fetches data
from an external source MUST:

1. Inherit from `BaseFetcher`
2. Define `name`, `description`, and `default_schedule`. Additionally,
   define `default_request_delay` if the target API has rate limits
3. Implement `execute()` with proper metric reporting
4. NOT bypass the base class with a raw Celery task

**Exception — sub-operation tasks**: background tasks that fetch from
external sources as a sub-operation of an existing fetcher (not as an
independent periodic sync) are exempt from `BaseFetcher`. These tasks:

- Are triggered on-demand by a parent fetcher, not by Celery Beat
- Do not have their own schedule
- Do not appear as separate cards in the dashboard
- Their metrics are not tracked independently

Example: `create_ticket_from_detection` is enqueued by the
`detect_ibs_track_releases` fetcher (Case C) and fetches CVE data from
NVD and package data from SMELT. It is a standalone Celery task, not a
`BaseFetcher` subclass, because it is a reaction to a discovery made by
the parent fetcher, not an independent sync process.

If there is a compelling reason to bypass `BaseFetcher` for a specific
case beyond this exception, the agent MUST stop and inform the user with
a detailed explanation of why the bypass is advantageous, so the decision
can be made together.

After creating or modifying a fetcher, the `@fetcher-compliance-reviewer`
agent MUST be invoked.

## Subagent: @fetcher-compliance-reviewer

A read-only reviewer agent that verifies fetcher implementations are
correctly integrated with the fetcher infrastructure.

### Trigger Conditions

Invoke `@fetcher-compliance-reviewer` when:

- A new file is created in `backend/app/tasks/` or `backend/app/services/`
  that implements fetching/sync logic
- An existing fetcher is modified in ways that affect its metrics or
  registration
- `BaseFetcher` itself is modified

### What It Checks

1. **Base class inheritance**: the fetcher class inherits from
   `BaseFetcher` (not bypassing it with a raw Celery task)
2. **Required attributes**: `name`, `description`, and `default_schedule`
   are defined on the class. For CVE fetchers,
   `source_reference_url_pattern` should be set if the source has a
   human-readable web page (see `docs/features/tickets/ticket-references.md`), and
   `fetch_single()` must be implemented unless
   `supports_fetch_single = False` (see "On-demand Single-Item Fetch"
   above)
3. **Unique name**: the fetcher's `name` does not conflict with any
   existing registered fetcher
4. **Metric reporting**: the `execute()` method calls
   `self.record_created()` and/or `self.record_updated()` where
   appropriate (creating/updating records without calling these methods
   means the dashboard will show 0 items)
5. **Test coverage**: tests exist that:
   - Verify `FetcherRun` records are created after execution
   - Verify item counts are correct
   - Verify error handling produces `failure` status
6. **No raw Celery tasks for fetching**: any background task that fetches
   external data MUST go through `BaseFetcher`, not be a standalone
   `@celery_app.task`

### Output

Structured summary with:

1. **Clean**: aspects that correctly follow the `BaseFetcher` pattern
2. **Integration issues**: problems with registration, metrics, or
   dashboard representation
3. **Test gaps**: missing test coverage for fetcher runs
4. **Verdict**: `Clean`, `Minor issues`, or `Needs revision`

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
