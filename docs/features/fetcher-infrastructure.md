# Fetcher Infrastructure

## Purpose

Define the mandatory infrastructure that all data fetchers in Sentinel must
use. Fetchers are background tasks that periodically pull data from
external sources (NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS) and update
the local database. This specification covers the `BaseFetcher` abstract
base class, the fetcher registry, Celery integration, concurrency
control, data model, and data retention.

For the monitoring dashboard (API endpoints, frontend pages, CLI
commands) that consumes this infrastructure, see
`docs/features/fetcher-dashboard.md`.

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
   - Creation of a `FetcherRun` record with status `running`
   - Automatic `started_at` timestamp capture
   - Automatic `finished_at` timestamp and `duration_seconds` calculation
   - Exception handling: if `execute()` raises, the run is marked `failure`
     with `error_message` and `error_traceback` populated
   - Final status set to `success` or `partial` (if `items_failed > 0`)
3. **Metric helpers**: methods that concrete fetchers call within their
   `execute()` to report work done:
   - `self.record_created(count=1)` — increment `items_created`
   - `self.record_updated(count=1)` — increment `items_updated`
   - `self.record_failed(count=1)` — increment `items_failed`
4. **Enabled check**: before executing, `run()` checks `FetcherConfig` for
   the fetcher. If `enabled` is `false`, the run is skipped (no
   `FetcherRun` record is created, the task returns immediately)

## Abstract Interface

Concrete fetchers MUST implement:

```python
class MyConcreteFetcher(BaseFetcher):
    name: str = "my_fetcher"             # unique identifier, snake_case
    description: str = "Human-readable description"
    default_schedule: str = "0 */6 * * *"  # cron expression (every 6h)

    # Optional: URL pattern for human-readable CVE page.
    # Only applicable to CVE fetchers. When set, a TicketReference
    # is automatically created with this URL for each processed CVE.
    # Uses {cve_id} as placeholder (e.g., "CVE-2026-3317").
    # See docs/features/references.md for details.
    source_reference_url_pattern: str | None = None

    async def execute(self, session: AsyncSession) -> None:
        """Fetch data from the external source.

        Use self.record_created(), self.record_updated(), and
        self.record_failed() to report metrics.
        """
        ...
```

## On-demand Single-Item Fetch

CVE fetchers MUST additionally implement the `fetch_single` method:

```python
async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:
    """Fetch a single CVE from the external source.

    Called on-demand when Sentinel encounters an unknown CVE-ID during
    ticket creation or CVE association. Writes data to the standard
    models (CVE, CVESource, CVECVSSAssessment, TicketReference).

    If the CVE is not found in the external source (e.g., reserved or
    not yet published), this method should return without error.
    """
    ...
```

This method is **optional** for non-CVE fetchers and **required** for
CVE fetchers. The system discovers all fetchers that implement
`fetch_single` via the registry and invokes them in parallel when an
on-demand fetch is needed (see `docs/features/cve-tracking.md`,
"On-demand Single-CVE Fetch").

The `fetch_single` method does NOT create a `FetcherRun` record. It is
a sub-operation invoked as a standalone Celery task, not a full fetcher
execution. Metric reporting (`record_created`, etc.) is not used.

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
                user_id: str | None = None) -> None:
    """Run a fetcher by name."""
    ...
```

The Celery Beat schedule is built dynamically from the registry at worker
startup, using each fetcher's effective schedule (config override or
default). When an admin modifies a fetcher's schedule via the API, the
Beat schedule MUST be updated accordingly (using `celery-redbeat` or
equivalent dynamic scheduler).

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
| Any trigger with stale run but `timeout_seconds = 0` | any | any | Stale detection disabled — treated as active run (409 or silent discard) |

The distinction is:

- **API-triggered attempts** (manual): the caller receives a synchronous
  **409 Conflict** response, so no log is needed — the caller is informed
  directly.
- **Schedule-triggered attempts**: there is no caller to notify, so the
  task logs the skip and returns without side effects.

The concurrency check SHOULD use a database query with row-level locking
(`SELECT ... FOR UPDATE`) or an equivalent atomic mechanism to prevent
race conditions between concurrent task starts.

## Stale Run Detection

A run is considered **stale** when it has been in `running` status for
longer than the fetcher's `timeout_seconds` (from `FetcherConfig`). The
default `timeout_seconds` is 3600 (1 hour). If `timeout_seconds` is set
to 0, stale detection is disabled for that fetcher — the run is never
considered stale regardless of how long it has been running.

When a stale run is detected (by the Celery task, the API trigger
endpoint, or the CLI), it is resolved by updating the stale `FetcherRun`
record:

- `status` → `failure`
- `error_message` → `"Marked as stale (running for {elapsed}, timeout
  {timeout}s)"` for automatic resolution (Celery/API), or `"Marked as
  stale by operator via CLI"` for CLI resolution
- `finished_at` → `now()`
- `duration_seconds` → calculated from `started_at`

An application-level log message is emitted:

```
logger.warning("Marking stale run %s for '%s' as failure (running since %s, timeout %ds)",
               run_id, fetcher_name, started_at, timeout_seconds)
```

Stale run detection is a recovery mechanism for unclean process
terminations (OOM-kill, node crash, `kill -9`). It is NOT a substitute
for proper signal handling — processes that can handle `SIGINT`/`SIGTERM`
must do so (see `docs/features/fetcher-dashboard.md`, section "CLI
Commands", "Signal handling").

## Data Model

### FetcherRun

Records every execution of a fetcher. This is the primary data source for
the dashboard charts.

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Internal identifier |
| fetcher_name | VARCHAR | NOT NULL, indexed | Fetcher identifier (matches `BaseFetcher.name`) |
| started_at | TIMESTAMP | NOT NULL | When the run started |
| finished_at | TIMESTAMP | nullable | When the run ended (NULL while running) |
| duration_seconds | FLOAT | nullable | Computed: `finished_at - started_at` in seconds |
| status | ENUM | NOT NULL | `running`, `success`, `failure`, `partial` |
| items_created | INTEGER | NOT NULL, DEFAULT 0 | Number of new records created |
| items_updated | INTEGER | NOT NULL, DEFAULT 0 | Number of existing records updated |
| items_failed | INTEGER | NOT NULL, DEFAULT 0 | Number of items that failed processing |
| error_message | TEXT | nullable | Short error description (for all users) |
| error_traceback | TEXT | nullable | Full Python traceback (admin-only visibility) |
| triggered_by | ENUM | NOT NULL | `schedule`, `manual` |
| triggered_by_user_id | UUID | FK(user.id), nullable | User who triggered the run (only for `manual`) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT | Record creation timestamp |

**Notes**:
- `finished_at` is NULL while a run is in progress (status `running`).
  This can be used to detect stale runs (running for too long).
- `error_traceback` is stored for debugging but MUST NOT be exposed to
  non-admin users via the API.
- `duration_seconds` is stored (not computed at query time) because it is
  the primary Y-axis value for timeline charts and benefits from indexing.

### FetcherRunStatus Enum

| Value | Description |
|---|---|
| `running` | Execution in progress |
| `success` | Completed without errors |
| `failure` | Terminated with an unhandled exception |
| `partial` | Completed but some items failed (`items_failed > 0`) |

### FetcherRunTriggeredBy Enum

| Value | Description |
|---|---|
| `schedule` | Triggered by Celery Beat schedule |
| `manual` | Triggered by an admin (via API or CLI) |

### FetcherConfig

Per-fetcher configuration, managed by admins. A record is created
automatically when a fetcher is first registered (on worker startup) if
one does not already exist.

| Column | Type | Constraints | Description |
|---|---|---|---|
| fetcher_name | VARCHAR | PK | Fetcher identifier (matches `BaseFetcher.name`) |
| enabled | BOOLEAN | NOT NULL, DEFAULT true | Whether the fetcher is active |
| schedule_override | VARCHAR | nullable | Cron expression to override the fetcher's `default_schedule`. NULL means use the default. |
| timeout_seconds | INTEGER | NOT NULL, DEFAULT 3600 | Maximum execution time in seconds. Also used as the stale run detection threshold. 0 disables both soft time limit and stale detection. |
| rate_limit | VARCHAR | nullable | Rate limit expression (e.g., `"2/s"`, `"100/m"`). NULL means no limit. |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT | Last modification timestamp |

**Notes**:
- `FetcherConfig` uses `fetcher_name` as the PK (VARCHAR, not UUID) since
  fetcher names are unique identifiers defined in code.
- The `schedule_override` uses standard cron syntax (5-field). When set,
  the Celery Beat schedule for this fetcher MUST be updated dynamically.
- `timeout_seconds` serves two purposes:
  1. **Celery soft time limit**: when > 0, enforced by the Celery task
     (`soft_time_limit`). When a fetcher exceeds this, a
     `SoftTimeLimitExceeded` exception is raised and the run is marked
     `failure`.
  2. **Stale run detection threshold**: when > 0, used by the Celery task,
     API trigger endpoint, and CLI to determine whether a `running`
     record is stale (see "Stale Run Detection" above).
  When set to 0, both mechanisms are disabled: Celery does not enforce a
  time limit, and stale detection treats the run as indefinitely active.
  The default of 3600 seconds (1 hour) applies when a `FetcherConfig`
  record is auto-created for a newly registered fetcher.

### FetcherAuditLog

Audit trail for administrative actions on fetchers.

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Internal identifier |
| fetcher_name | VARCHAR | NOT NULL, indexed | Fetcher identifier |
| action | ENUM | NOT NULL | See FetcherAuditAction enum |
| performed_by_user_id | UUID | FK(user.id), NOT NULL | Admin who performed the action |
| details | JSONB | nullable | Additional context (e.g., old/new schedule values) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT | When the action occurred |

### FetcherAuditAction Enum

| Value | Description |
|---|---|
| `disabled` | Fetcher was disabled by an admin |
| `enabled` | Fetcher was re-enabled by an admin |
| `triggered` | Fetcher was manually triggered by an admin |
| `config_changed` | Fetcher configuration was modified (schedule, timeout, rate limit) |

**Notes on `details` JSONB**:
- For `config_changed`: `{"field": "schedule_override", "old_value": "0 */6 * * *", "new_value": "0 */4 * * *"}`
- For `disabled` / `enabled`: `null` (the action itself is self-explanatory)
- For `triggered`: `null`

## Data Retention

Individual `FetcherRun` records are retained for **90 days**. After 90
days, runs are aggregated into weekly summaries and individual records
are deleted.

### FetcherRunWeeklyAggregate

Stores weekly summaries of fetcher runs after the 90-day retention window.

| Column | Type | Constraints | Description |
|---|---|---|---|
| id | UUID | PK | Internal identifier |
| fetcher_name | VARCHAR | NOT NULL, indexed | Fetcher identifier |
| week_start | DATE | NOT NULL | Monday of the aggregation week |
| run_count | INTEGER | NOT NULL | Total number of runs in the week |
| success_count | INTEGER | NOT NULL | Runs with status `success` |
| failure_count | INTEGER | NOT NULL | Runs with status `failure` |
| partial_count | INTEGER | NOT NULL | Runs with status `partial` |
| avg_duration_seconds | FLOAT | NOT NULL | Average duration across all runs |
| min_duration_seconds | FLOAT | NOT NULL | Minimum duration |
| max_duration_seconds | FLOAT | NOT NULL | Maximum duration |
| total_items_created | INTEGER | NOT NULL | Sum of `items_created` across all runs |
| total_items_updated | INTEGER | NOT NULL | Sum of `items_updated` across all runs |
| total_items_failed | INTEGER | NOT NULL | Sum of `items_failed` across all runs |
| created_at | TIMESTAMP | NOT NULL, DEFAULT | When this aggregate was created |

**Unique constraint**: (fetcher_name, week_start)

### Aggregation Task

A Celery periodic task `aggregate_fetcher_runs` runs daily and:

1. Selects all `FetcherRun` records older than 90 days
2. Groups them by `fetcher_name` and ISO week
3. Creates or updates `FetcherRunWeeklyAggregate` records with the computed
   summaries
4. Deletes the original `FetcherRun` records that were aggregated

This task is itself a fetcher (inherits `BaseFetcher`) so its execution
is also tracked in the dashboard.

## Guardrail: Fetcher Base Class Compliance

See Guardrail 14 in `AGENTS.md`. Every background task that fetches data
from an external source MUST:

1. Inherit from `BaseFetcher`
2. Define `name`, `description`, and `default_schedule`
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
`check_codestream_releases` fetcher (Case C) and fetches CVE data from
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
   human-readable web page (see `docs/features/references.md`), and
   `fetch_single()` must be implemented (see "On-demand Single-Item
   Fetch" above)
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

## Open Questions

None at this time.
