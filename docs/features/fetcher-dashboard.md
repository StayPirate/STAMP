# Fetcher Dashboard

## Purpose

Provide a centralized dashboard for monitoring all data fetchers in STAMP.
Fetchers are background tasks that periodically pull data from external
sources (NVD, MITRE, Red Hat, SMELT, AIMAAS, IBS) and update the local
database. The dashboard gives all authenticated users visibility into
fetcher health and performance, while giving admins operational control
(manual trigger, enable/disable, configuration).

This feature also defines the `BaseFetcher` infrastructure — a mandatory
base class that all fetchers must inherit from. The base class handles
automatic metric collection, execution tracking, and registry, ensuring
every fetcher is consistently represented in the dashboard without
per-fetcher integration effort.

## Terminology

| Term | Definition |
|---|---|
| **Fetcher** | A background task that retrieves data from an external source and creates/updates local records. Implemented as a subclass of `BaseFetcher`. |
| **Run** | A single execution of a fetcher, tracked from start to finish with metrics (duration, item counts, status). |
| **Registry** | An in-memory dictionary of all registered fetcher classes, populated automatically via `BaseFetcher` auto-discovery. |

## BaseFetcher Infrastructure

### Base Class

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

### Abstract Interface

Concrete fetchers MUST implement:

```python
class MyConcreteFetcher(BaseFetcher):
    name: str = "my_fetcher"             # unique identifier, snake_case
    description: str = "Human-readable description"
    default_schedule: str = "0 */6 * * *"  # cron expression (every 6h)

    async def execute(self, session: AsyncSession) -> None:
        """Fetch data from the external source.

        Use self.record_created(), self.record_updated(), and
        self.record_failed() to report metrics.
        """
        ...
```

### Registry

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

A fetcher class that is imported but should NOT be registered (e.g., an
intermediate abstract subclass) can set `abstract = True` as a class
attribute to opt out of registration.

### Celery Integration

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

### Concurrency Control

Only one instance of a given fetcher can run at a time. Before invoking
`execute()`, the `run_fetcher` task MUST check whether a `FetcherRun`
record with `status = running` already exists for the requested
`fetcher_name`.

- **If a run is already active**: the new attempt is discarded silently.
  No `FetcherRun` record is created. An application-level log message is
  emitted for observability:
  ```
  logger.info("Skipping scheduled run for '%s': already running (run_id=%s)",
              fetcher_name, active_run_id)
  ```
- **If no run is active**: execution proceeds normally (a new `FetcherRun`
  is created with `status = running`).

This applies to all trigger sources:

| Scenario | Active run triggered by | New attempt triggered by | Behavior |
|---|---|---|---|
| Admin triggers while schedule is running | `schedule` | `manual` | API returns **409 Conflict** with message indicating the fetcher is already running |
| Schedule fires while manual run is active | `manual` | `schedule` | Silent discard with log (async — no caller to notify) |
| Schedule fires while previous schedule run is still active | `schedule` | `schedule` | Silent discard with log |
| Admin triggers while another manual run is active | `manual` | `manual` | API returns **409 Conflict** |

The distinction is:

- **API-triggered attempts** (manual): the caller receives a synchronous
  **409 Conflict** response, so no log is needed — the caller is informed
  directly.
- **Schedule-triggered attempts**: there is no caller to notify, so the
  task logs the skip and returns without side effects.

The concurrency check SHOULD use a database query with row-level locking
(`SELECT ... FOR UPDATE`) or an equivalent atomic mechanism to prevent
race conditions between concurrent task starts.

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
| `manual` | Triggered by an admin via the API |

### FetcherConfig

Per-fetcher configuration, managed by admins. A record is created
automatically when a fetcher is first registered (on worker startup) if
one does not already exist.

| Column | Type | Constraints | Description |
|---|---|---|---|
| fetcher_name | VARCHAR | PK | Fetcher identifier (matches `BaseFetcher.name`) |
| enabled | BOOLEAN | NOT NULL, DEFAULT true | Whether the fetcher is active |
| schedule_override | VARCHAR | nullable | Cron expression to override the fetcher's `default_schedule`. NULL means use the default. |
| timeout_seconds | INTEGER | nullable | Maximum execution time in seconds. NULL means no timeout. |
| rate_limit | VARCHAR | nullable | Rate limit expression (e.g., `"2/s"`, `"100/m"`). NULL means no limit. |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT | Last modification timestamp |

**Notes**:
- `FetcherConfig` uses `fetcher_name` as the PK (VARCHAR, not UUID) since
  fetcher names are unique identifiers defined in code.
- The `schedule_override` uses standard cron syntax (5-field). When set,
  the Celery Beat schedule for this fetcher MUST be updated dynamically.
- `timeout_seconds` is enforced by the Celery task (`soft_time_limit`).
  When a fetcher exceeds this, a `SoftTimeLimitExceeded` exception is
  raised and the run is marked `failure`.

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

## API Endpoints

### List Fetchers

```
GET /api/v1/fetchers
```

Returns all registered fetchers with their current status and
configuration.

**Response** (200 OK):

```json
{
  "data": [
    {
      "name": "sync_cves_nvd",
      "description": "Incremental CVE sync from NVD",
      "enabled": true,
      "schedule": "0 */6 * * *",
      "schedule_is_override": false,
      "default_schedule": "0 */6 * * *",
      "next_run_at": "2025-04-20T18:00:00Z",
      "last_run": {
        "id": "uuid",
        "started_at": "2025-04-20T12:00:00Z",
        "finished_at": "2025-04-20T12:03:45Z",
        "duration_seconds": 225.0,
        "status": "success",
        "items_created": 12,
        "items_updated": 45,
        "items_failed": 0
      }
    }
  ]
}
```

**Fields**:
- `schedule`: the effective schedule (override if set, otherwise default)
- `schedule_is_override`: `true` if the schedule comes from `FetcherConfig`
- `default_schedule`: the schedule defined in code
- `next_run_at`: calculated from the effective schedule and the Celery Beat
  state. May be `null` if the fetcher is disabled.
- `last_run`: the most recent `FetcherRun` record, or `null` if never run.
  Does NOT include `error_traceback` (admin-only, available on the detail
  endpoint).

**Permissions**: any authenticated user.

### List Fetcher Runs

```
GET /api/v1/fetchers/{fetcher_name}/runs
```

Returns paginated run history for a specific fetcher.

**Path parameters**:

| Parameter | Type | Description |
|---|---|---|
| `fetcher_name` | string | Fetcher identifier |

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number |
| `per_page` | int | 20 | Items per page (max 100) |
| `status` | string | — | Filter by status (`success`, `failure`, `partial`) |
| `from_date` | datetime | — | Filter runs started on or after this datetime |
| `to_date` | datetime | — | Filter runs started on or before this datetime |

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "fetcher_name": "sync_cves_nvd",
      "started_at": "2025-04-20T12:00:00Z",
      "finished_at": "2025-04-20T12:03:45Z",
      "duration_seconds": 225.0,
      "status": "success",
      "items_created": 12,
      "items_updated": 45,
      "items_failed": 0,
      "error_message": null,
      "triggered_by": "schedule",
      "triggered_by_user": null
    }
  ],
  "meta": {
    "total": 150,
    "page": 1,
    "per_page": 20
  }
}
```

**Notes**:
- `error_traceback` is NOT included in list responses
- `triggered_by_user` is an object `{"id": "uuid", "username": "admin1"}`
  when `triggered_by` is `manual`, otherwise `null`

**Permissions**: any authenticated user.

**Error responses**:

| Status | Condition |
|---|---|
| 404 | Fetcher not found in registry |

### Get Fetcher Run Detail

```
GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}
```

Returns full detail for a single run.

**Response** (200 OK):

Same fields as the list response, plus:
- `error_traceback`: included ONLY if the requesting user has the Admin
  role. Omitted (or `null`) for non-admin users.

**Permissions**: any authenticated user (admin sees additional fields).

**Error responses**:

| Status | Condition |
|---|---|
| 404 | Fetcher or run not found |

### Get Fetcher Run Timeline Data

```
GET /api/v1/fetchers/{fetcher_name}/timeline
```

Returns time-series data optimized for chart rendering. Automatically
selects the appropriate data source based on the requested time range:
individual runs for the last 90 days, weekly aggregates for older data.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `from_date` | datetime | 7 days ago | Start of the time range |
| `to_date` | datetime | now | End of the time range |

**Response** (200 OK):

```json
{
  "data": {
    "points": [
      {
        "timestamp": "2025-04-20T12:00:00Z",
        "duration_seconds": 225.0,
        "items_created": 12,
        "items_updated": 45,
        "items_failed": 0,
        "status": "success",
        "type": "individual"
      },
      {
        "timestamp": "2025-03-10T00:00:00Z",
        "duration_seconds": 210.5,
        "items_created": 85,
        "items_updated": 320,
        "items_failed": 2,
        "status": null,
        "type": "weekly_aggregate",
        "run_count": 28,
        "success_count": 25,
        "failure_count": 1,
        "partial_count": 2,
        "min_duration_seconds": 180.0,
        "max_duration_seconds": 350.0
      }
    ],
    "disabled_periods": [
      {
        "disabled_at": "2025-03-15T10:00:00Z",
        "disabled_by": "admin1",
        "enabled_at": "2025-03-17T08:30:00Z",
        "enabled_by": "admin2"
      }
    ]
  }
}
```

**Fields**:
- `points[].type`: `"individual"` for actual runs (within 90 days),
  `"weekly_aggregate"` for aggregated data (older than 90 days)
- `points[].status`: the run status for individual points, `null` for
  aggregates (use `run_count`, `success_count`, etc. instead)
- `points[].timestamp`: `started_at` for individual runs, `week_start`
  for aggregates
- `points[].duration_seconds`: actual duration for individual runs,
  `avg_duration_seconds` for aggregates
- `points[].items_created/updated/failed`: actual counts for individual
  runs, totals for aggregates
- `disabled_periods`: array of time ranges when the fetcher was disabled,
  derived from `FetcherAuditLog` records. Used to render grey overlay
  bands on the chart. If the fetcher is currently disabled, `enabled_at`
  and `enabled_by` are `null`.

**Permissions**: any authenticated user.

**Error responses**:

| Status | Condition |
|---|---|
| 404 | Fetcher not found in registry |

### Trigger Fetcher (Admin Only)

```
POST /api/v1/fetchers/{fetcher_name}/trigger
```

Enqueues a manual run of the specified fetcher.

**Response** (202 Accepted):

```json
{
  "data": {
    "run_id": "uuid",
    "message": "Fetcher 'sync_cves_nvd' has been queued for execution"
  }
}
```

**Error responses**:

| Status | Condition |
|---|---|
| 404 | Fetcher not found in registry |
| 409 | Fetcher is disabled (`enabled = false` in `FetcherConfig`) |
| 409 | Fetcher is already running (a `FetcherRun` with status `running` exists for this fetcher) |

**Permissions**: Admin only.

**Side effects**:
- Creates a `FetcherAuditLog` record with action `triggered`
- Creates a `FetcherRun` record with `triggered_by = manual`

**Note on existing trigger endpoints**: some feature specs define
domain-specific trigger endpoints (e.g., `POST /api/v1/cves/sync` in
`docs/features/cve-tracking.md`). These endpoints are **convenience
aliases** that internally delegate to the same `run_fetcher` Celery task.
They remain valid for backward compatibility and domain-specific
permissions but the generic `/fetchers/{name}/trigger` endpoint is the
canonical way to trigger any fetcher from the dashboard.

### Get Fetcher Config (Admin Only)

```
GET /api/v1/fetchers/{fetcher_name}/config
```

Returns the current configuration for a fetcher.

**Response** (200 OK):

```json
{
  "data": {
    "fetcher_name": "sync_cves_nvd",
    "enabled": true,
    "schedule_override": null,
    "default_schedule": "0 */6 * * *",
    "effective_schedule": "0 */6 * * *",
    "timeout_seconds": null,
    "rate_limit": null,
    "updated_at": "2025-04-20T10:00:00Z"
  }
}
```

**Permissions**: Admin only.

**Error responses**:

| Status | Condition |
|---|---|
| 404 | Fetcher not found |

### Update Fetcher Config (Admin Only)

```
PATCH /api/v1/fetchers/{fetcher_name}/config
```

Modifies fetcher configuration. Partial updates are supported — only
include the fields to change.

**Request body** (all fields optional):

```json
{
  "enabled": false,
  "schedule_override": "0 */4 * * *",
  "timeout_seconds": 600,
  "rate_limit": "2/s"
}
```

**Validation rules**:
- `schedule_override`: must be a valid 5-field cron expression, or `null`
  to revert to the default schedule
- `timeout_seconds`: must be a positive integer, or `null` to disable
- `rate_limit`: must match the pattern `"<number>/<unit>"` where unit is
  `s`, `m`, or `h`, or `null` to disable

**Response** (200 OK): the updated config object (same as GET response).

**Side effects**:
- Creates a `FetcherAuditLog` record:
  - If `enabled` changed: action `disabled` or `enabled`
  - If any other field changed: action `config_changed` with `details`
    containing old and new values
- If `enabled` changed to `false` and the fetcher is currently running:
  the current run is allowed to complete. The next scheduled run will not
  start.
- If `schedule_override` changed: the Celery Beat schedule for this
  fetcher MUST be updated dynamically

**Permissions**: Admin only.

**Error responses**:

| Status | Condition |
|---|---|
| 404 | Fetcher not found |
| 422 | Invalid cron expression, timeout, or rate limit format |

### Get Fetcher Audit Log (Admin Only)

```
GET /api/v1/fetchers/{fetcher_name}/audit-log
```

Returns the audit trail of admin actions for a fetcher.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number |
| `per_page` | int | 20 | Items per page (max 100) |

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "fetcher_name": "sync_cves_nvd",
      "action": "disabled",
      "performed_by": {
        "id": "uuid",
        "username": "admin1"
      },
      "details": null,
      "created_at": "2025-04-18T14:30:00Z"
    }
  ],
  "meta": {
    "total": 5,
    "page": 1,
    "per_page": 20
  }
}
```

**Permissions**: Admin only.

## Frontend

### Fetchers Page

**Route**: `/fetchers`

**Access**: all authenticated users.

The page displays a grid of fetcher cards, one per registered fetcher.
Each card shows a summary of the fetcher's current state.

#### Fetcher Card

Each card contains:

1. **Header**: fetcher name (human-readable `description`) and a status
   indicator:
   - Green dot: last run was `success`
   - Red dot: last run was `failure`
   - Yellow dot: last run was `partial`
   - Grey dot: fetcher is disabled
   - Pulsing blue dot: fetcher is currently running
2. **Last run summary** (below header):
   - "Last run: 2 hours ago" (relative time, tooltip with absolute)
   - "Duration: 3m 45s"
   - "Created: 12 | Updated: 45 | Failed: 0"
3. **Schedule info**:
   - "Schedule: every 6 hours" (human-readable interpretation of the cron)
   - "Next run: in 4 hours" (relative time)
   - If disabled: "Disabled" badge instead of next run
4. **Admin controls** (visible only to Admin role):
   - **Toggle switch**: enable/disable the fetcher. On toggle off:
     - Confirmation dialog: "Disable fetcher '{name}'? The current run
       (if any) will complete, but no new runs will be scheduled."
     - On confirm: PATCH config with `enabled: false`
   - **"Run Now" button**: trigger manual execution. Disabled if the
     fetcher is currently running or disabled.
     - On click: POST trigger → show "Queued" feedback
5. **Click target**: clicking anywhere on the card (except admin controls)
   navigates to the fetcher detail page.

#### Fetcher Detail Page

**Route**: `/fetchers/:name`

**Access**: all authenticated users.

##### Timeline Charts

Two charts displayed at the top of the detail page:

1. **Duration chart** (primary):
   - X-axis: time
   - Y-axis: duration in seconds
   - Each run is a point/dot on the chart
   - Point color indicates status (green = success, red = failure,
     yellow = partial)
   - Hover tooltip: "Started: {datetime} | Duration: {formatted} |
     Status: {status} | Created: {n} | Updated: {n}"
   - **Disabled period overlay**: semi-transparent grey band covering
     time ranges when the fetcher was disabled. Vertical dashed markers
     at the boundaries: red dashed line at disable moment, green dashed
     line at enable moment. Tooltip on the band: "Disabled by {username}
     on {datetime} — Enabled by {username} on {datetime}". If currently
     disabled, the band extends to the present with an open (dashed)
     right border.
   - For weekly aggregate points (data older than 90 days): render as a
     range bar showing min/max duration, with a dot at the average.
     Tooltip includes "Week of {date} | Avg: {n}s | Min: {n}s |
     Max: {n}s | Runs: {n}"

2. **Items chart** (secondary, below duration chart):
   - X-axis: time (aligned with duration chart)
   - Y-axis: item count
   - Stacked or grouped bars: created (green), updated (blue),
     failed (red)
   - Same disabled period overlay as the duration chart
   - Same tooltip pattern with item counts

##### Time Range Selector

Above the charts, a selector with presets:

- Last 24 hours
- Last 7 days (default)
- Last 30 days
- Last 90 days
- Last 6 months (uses weekly aggregates for data beyond 90 days)
- Last 1 year (uses weekly aggregates for data beyond 90 days)
- Custom range (date picker)

Changing the time range re-fetches data from the timeline API.

##### Run History Table

Below the charts, a paginated table of individual runs:

| Column | Description |
|---|---|
| Status | Color-coded badge (Success/Failure/Partial) |
| Started | Absolute datetime |
| Duration | Formatted duration (e.g., "3m 45s") |
| Created | Items created count |
| Updated | Items updated count |
| Failed | Items failed count |
| Triggered By | "Schedule" or username of the admin |
| Error | Truncated error message (if any), click to expand |

**Admin-only column**:
- **Error details**: for failed runs, a clickable "View traceback" link
  that opens a modal with the full `error_traceback` in a monospace
  code block.

##### Admin Configuration Panel

Visible only to Admin role users. Displayed as a collapsible section
below the run history table.

Contains:

1. **Enable/Disable toggle**: same behavior as on the card
2. **Schedule**: editable cron expression input with human-readable
   preview. Shows "(default)" label when no override is set. A "Reset to
   default" button when an override is active.
3. **Timeout**: numeric input in seconds, with "No timeout" option
4. **Rate limit**: text input with format hint (`"2/s"`, `"100/m"`)
5. **Save button**: PATCHes the config. Shows confirmation dialog if
   changing the schedule.

##### Admin Audit Log

Visible only to Admin role users. Displayed as a tab or collapsible
section.

A simple chronological list of admin actions from `FetcherAuditLog`:

- "{username} disabled this fetcher — {datetime}"
- "{username} triggered manual run — {datetime}"
- "{username} changed schedule from '{old}' to '{new}' — {datetime}"

## Access Control

| Action | Viewer | Security Team | Packager | Admin |
|---|---|---|---|---|
| View fetcher list | Yes | Yes | Yes | Yes |
| View fetcher detail + charts | Yes | Yes | Yes | Yes |
| View run history | Yes | Yes | Yes | Yes |
| View error messages | Yes | Yes | Yes | Yes |
| View error tracebacks | No | No | No | Yes |
| Trigger manual run | No | No | No | Yes |
| Enable/disable fetcher | No | No | No | Yes |
| Modify fetcher config | No | No | No | Yes |
| View audit log | No | No | No | Yes |

## Background Tasks

### run_fetcher

Generic Celery task that executes any registered fetcher by name.

| Property | Value |
|---|---|
| Task name | `run_fetcher` |
| Parameters | `fetcher_name` (str), `triggered_by` (str), `user_id` (str, optional) |
| Schedule | per-fetcher, from `FetcherConfig.schedule_override` or `BaseFetcher.default_schedule` |
| Idempotency | Only one instance per fetcher can run at a time (Celery `unique` or lock-based) |

### aggregate_fetcher_runs

Periodic task that aggregates old `FetcherRun` records into weekly
summaries.

| Property | Value |
|---|---|
| Task name | `aggregate_fetcher_runs` |
| Schedule | Daily at 03:00 UTC |
| Retention | 90 days for individual runs |
| Aggregation | Weekly (ISO week, Monday start) |

The aggregation task is itself a fetcher (`AggregationFetcher` inheriting
`BaseFetcher`) so its own execution is tracked in the dashboard.

## System Metrics (Future Iteration)

The following metrics are planned for a future iteration and are NOT part
of the initial implementation:

- Memory usage (start, end, peak) via `psutil`
- CPU utilization during the run
- Database connection count

When implemented, these will be stored in a `system_metrics` JSONB column
on `FetcherRun` and displayed in an admin-only panel on the fetcher
detail page.

## Subagent: @fetcher-dashboard-reviewer

A read-only reviewer agent that verifies fetcher implementations are
correctly integrated with the dashboard infrastructure.

### Trigger Conditions

Invoke `@fetcher-dashboard-reviewer` when:

- A new file is created in `backend/app/tasks/` or `backend/app/services/`
  that implements fetching/sync logic
- An existing fetcher is modified in ways that affect its metrics or
  registration
- `BaseFetcher` itself is modified

### What It Checks

1. **Base class inheritance**: the fetcher class inherits from
   `BaseFetcher` (not bypassing it with a raw Celery task)
2. **Required attributes**: `name`, `description`, and `default_schedule`
   are defined on the class
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

## Guardrail: Fetcher Base Class Compliance

See Guardrail 14 in `AGENTS.md`. Every background task that fetches data
from an external source MUST:

1. Inherit from `BaseFetcher`
2. Define `name`, `description`, and `default_schedule`
3. Implement `execute()` with proper metric reporting
4. NOT bypass the base class with a raw Celery task

If there is a compelling reason to bypass `BaseFetcher` for a specific
fetcher, the agent MUST stop and inform the user with a detailed
explanation of why the bypass is advantageous, so the decision can be
made together.

After creating or modifying a fetcher, the `@fetcher-dashboard-reviewer`
agent MUST be invoked.

## Dependencies

- Celery Beat with dynamic schedule support (`celery-redbeat` or
  equivalent)
- A charting library for the frontend (e.g., Recharts, which integrates
  well with shadcn/ui and React)

## Open Questions

None at this time.
