# Fetcher Dashboard

## Purpose

Provide a centralized dashboard for monitoring all data fetchers in Sentinel.
The dashboard gives all users visibility into fetcher health and
performance (no authentication required), while giving admins operational
control (manual trigger, enable/disable, configuration) and CLI access
for bootstrap and troubleshooting.

This feature depends on the fetcher infrastructure defined in
`docs/features/platform/fetcher-infrastructure.md`. Read that spec first for the
`BaseFetcher` contract, data model (`FetcherRun`, `FetcherConfig`,
`FetcherAuditLog`, `FetcherRunWeeklyAggregate`), concurrency control,
and stale run detection.

## API Endpoints

### IBS RabbitMQ Consumer Status

```
GET /api/v1/ibs-consumer/status
```

Returns the current status of the `IBSEventConsumer` by reading the
`sentinel:ibs_consumer_status` key from Redis. See
`docs/features/integrations/ibs-rabbitmq-integration.md`, section "Redis Heartbeat"
for the key format and TTL behavior.

**Response when consumer is alive** (200 OK):

```json
{
  "data": {
    "status": "connected",
    "status_since": "2026-04-20T02:08:00Z",
    "events_received": 12847,
    "events_relevant": 342,
    "events_processed": 338,
    "diffs_failed": 4,
    "last_error": null,
    "reconnect_attempts": 0,
    "next_retry_seconds": null
  }
}
```

**Response when consumer is reconnecting** (200 OK):

```json
{
  "data": {
    "status": "reconnecting",
    "status_since": "2026-04-23T16:45:00Z",
    "events_received": 12847,
    "events_relevant": 342,
    "events_processed": 338,
    "diffs_failed": 4,
    "last_error": "Connection refused",
    "reconnect_attempts": 7,
    "next_retry_seconds": 245
  }
}
```

Note: event counters retain the values from the last active connection
(they are reset only when a new connection is successfully established,
not on disconnection).

**Response when Redis key is absent** (200 OK):

```json
{
  "data": {
    "status": "unreachable",
    "status_since": null,
    "events_received": null,
    "events_relevant": null,
    "events_processed": null,
    "diffs_failed": null,
    "last_error": null,
    "reconnect_attempts": null,
    "next_retry_seconds": null
  }
}
```

**Fields**:
- `status`: one of `connected`, `disconnected`, `reconnecting`,
  `unreachable`
- `status_since`: ISO 8601 timestamp of when the current status began.
  `null` when `unreachable`.
- `events_received`: total `package.commit` events received since the
  current connection was established. Reset on each new connection.
- `events_relevant`: events that passed the active codestream filter.
- `events_processed`: events where the IBS diff completed successfully.
- `diffs_failed`: events where the IBS diff request failed.
- `last_error`: last error message (e.g., "Connection refused"). `null`
  when connected.
- `reconnect_attempts`: number of reconnection attempts since
  disconnection. `0` when connected.
- `next_retry_seconds`: seconds until the next reconnection attempt.
  `null` when connected or unreachable.

**Permissions**: publicly accessible (no authentication required).

### List Fetchers

```
GET /api/v1/fetchers
```

Returns all registered fetchers with their current status and
configuration.

**Pagination**: not paginated. The number of fetchers is bounded by the
application's fetcher registry (expected <30 entries). The full list is
always returned.

**Sorting**: results are ordered by `name` ascending (alphabetical).
Client-controlled sorting is not supported.

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

**Permissions**: publicly accessible (no authentication required).

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

**Sorting**: default `sort_by=started_at`, `sort_order=desc` (most recent
run first). Follows the project-wide default sorting convention.

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

**Permissions**: publicly accessible (no authentication required).

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | Fetcher not found in registry |

### Get Fetcher Run Detail

```
GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}
```

Returns full detail for a single run.

**Response** (200 OK):

Same fields as the list response, plus:
- `error_traceback`: included ONLY if the requesting user has the Admin
  role. Omitted (or `null`) for non-admin users.

**Permissions**: publicly accessible (no authentication required). Admin
users see additional fields (`error_traceback`).

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | Fetcher or run not found |

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

**Permissions**: publicly accessible (no authentication required).

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | Fetcher not found in registry |

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

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | Fetcher not found in registry |
| 409 | `FETCHER_DISABLED` | Fetcher is disabled (`enabled = false` in `FetcherConfig`) |
| 409 | `FETCHER_ALREADY_RUNNING` | Fetcher is already running (a non-stale `FetcherRun` with status `running` exists for this fetcher). If the active run is stale and `timeout_seconds > 0`, it is marked as `failure` and the new run proceeds (returns 202). |

**Permissions**: Admin only.

**Side effects**:
- Creates a `FetcherAuditLog` record with action `triggered`
- Creates a `FetcherRun` record **synchronously** (before enqueuing the
  Celery task) with `status = running` and `triggered_by = manual`. This
  ensures the `run_id` is available in the API response. The
  `BaseFetcher.run()` method detects the existing `FetcherRun` record
  (matched by `run_id`) and updates it rather than creating a new one

**Note on on-demand CVE fetch**: when Sentinel encounters an unknown CVE-ID
during ticket creation or CVE association, it triggers on-demand
single-CVE fetches via standalone Celery tasks (not through this trigger
endpoint). These on-demand fetches are sub-operations that do not create
`FetcherRun` records, do not check concurrency, and do not appear in the
dashboard. See `docs/features/tickets/cve-tracking.md`, "On-demand Single-CVE
Fetch" for details.

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
    "timeout_seconds": 3600,
    "rate_limit": null,
    "updated_at": "2025-04-20T10:00:00Z"
  }
}
```

**Permissions**: Admin only.

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | Fetcher not found |

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
- `timeout_seconds`: must be a non-negative integer. 0 disables both
  the Celery soft time limit and stale run detection. Default: 3600
  (1 hour)
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

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | Fetcher not found |
| 422 | `VALIDATION_ERROR` | Invalid cron expression, timeout, or rate limit format |

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

**Sorting**: default `sort_by=created_at`, `sort_order=desc` (most recent
entry first). Follows the project-wide default sorting convention.

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

**Access**: publicly accessible (no authentication required).

The page is divided into two sections:

1. **IBS RabbitMQ Consumer Card** — a dedicated card at the top of the
   page showing the status of the real-time event consumer (see below)
2. **Fetcher Card Grid** — a grid of fetcher cards, one per registered
   fetcher, each showing a summary of the fetcher's current state

#### IBS RabbitMQ Consumer Card

A dedicated card displayed **above** the fetcher card grid. It shows the
real-time status of the `IBSEventConsumer` (see
`docs/features/integrations/ibs-rabbitmq-integration.md`). This card is visually
distinct from the fetcher cards (different layout, no schedule info, no
admin toggle) since the consumer is not a `BaseFetcher`.

The card reads its data from the `GET /api/v1/ibs-consumer/status`
endpoint (see [IBS RabbitMQ Consumer Status](#ibs-rabbitmq-consumer-status)
above).

**Access**: publicly accessible (no authentication required).

##### When status is `connected`

```
IBS RabbitMQ Consumer
Status: Connected
Uptime: 3d 14h 22m
Events received: 12,847
Events relevant: 342
Events processed: 338
Diffs failed: 4
```

- **Uptime**: human-readable duration since `status_since`. On mouse
  hover, a tooltip shows the absolute timestamp (e.g.,
  "Since: 2026-04-20 02:08 UTC").
- **Status indicator**: green dot next to the status text.

##### When status is `reconnecting`

```
IBS RabbitMQ Consumer
Status: Reconnecting
Downtime: 2h 15m
Last error: Connection refused
Reconnect attempts: 7
Next retry in: 245s
```

- **Downtime**: human-readable duration since `status_since`. On mouse
  hover, a tooltip shows the absolute timestamp (e.g.,
  "Since: 2026-04-23 16:45 UTC").
- **Status indicator**: pulsing yellow dot.

##### When status is `disconnected`

Same layout as `reconnecting`, but with:
- **Status indicator**: red dot.

##### When status is `unreachable`

```
IBS RabbitMQ Consumer
Status: Unreachable
```

- Displayed when the API returns `unreachable` (Redis key expired,
  consumer process presumed dead).
- **Status indicator**: grey dot.
- No counters or timestamps are available.

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

**Access**: publicly accessible (no authentication required).

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
3. **Timeout**: numeric input in seconds. Default: 3600 (1 hour). Set to
   0 to disable timeout enforcement and stale run detection. A help text
   explains: "Controls both the maximum execution time (Celery soft time
   limit) and the stale run detection threshold. Set to 0 to disable."
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

| Action | Admin | VA | Unauth |
|---|---|---|---|
| View IBS RabbitMQ consumer status | Yes | Yes | Yes |
| View fetcher list | Yes | Yes | Yes |
| View fetcher detail + charts | Yes | Yes | Yes |
| View run history | Yes | Yes | Yes |
| View error messages | Yes | Yes | Yes |
| View error tracebacks | Yes | No | No |
| Trigger manual run | Yes | No | No |
| Enable/disable fetcher | Yes | No | No |
| Modify fetcher config | Yes | No | No |
| View audit log | Yes | No | No |

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

## CLI Commands

The `sentinel fetcher` command group provides operational access to the
fetcher infrastructure from the command line. It is designed for
bootstrap, troubleshooting, and environments where the API/UI is not
yet available. It is NOT a replacement for the API — configuration
changes (schedule, timeout, rate limit, enable/disable) are done
exclusively through the API.

### `sentinel fetcher list`

Lists all registered fetchers with their current state.

```
sentinel fetcher list
```

Output (human-readable table to stdout):

```
Name                       Enabled   Last Run              Status
sync_ldap_directory        yes       2026-04-27 04:00 UTC  success (3m 12s)
sync_cves_nvd              yes       2026-04-27 12:00 UTC  running (1m 30s elapsed)
sync_products_smelt        yes       2026-04-26 06:00 UTC  success (45s)
check_codestream_releases  no        2026-04-25 02:00 UTC  failure
sync_requests              yes       2026-04-27 02:30 UTC  success (2m 15s)
aggregate_fetcher_runs     yes       —                     never run
```

**Status column logic**:

1. If a `FetcherRun` with `status = running` exists for the fetcher:
   show `running ({elapsed} elapsed)` where elapsed is calculated from
   `started_at`. If `timeout_seconds > 0` and the elapsed time exceeds
   it, append `(stale?)` — e.g., `running (2h 30m elapsed, stale?)`.
   If `timeout_seconds = 0`, the `(stale?)` hint is never shown.
2. If no running record exists but completed runs exist: show the status
   of the most recent `FetcherRun` with its duration — e.g.,
   `success (3m 12s)`, `failure`, `partial (1m 5s)`
3. If no `FetcherRun` records exist: show `never run`

**Enabled column**: reads from `FetcherConfig.enabled`. If no
`FetcherConfig` record exists for the fetcher, defaults to `yes`.

**Data source**: queries the database directly (synchronous session).
The fetcher registry provides the list of fetcher names; the database
provides `FetcherRun` and `FetcherConfig` data.

**Idempotency**: Idempotent. Read-only command; safe to re-run at any
time.

**Exit codes**: 0 on success, 2 on system error (database unreachable).

**Output channels**: table to stdout. `"Error: ..."` messages to stderr.

### `sentinel fetcher run <name>`

Executes a fetcher synchronously (in-process, no Celery). Output is
printed to stdout as the fetcher runs.

```
sentinel fetcher run sync_ldap_directory
```

Successful output:

```
Running fetcher 'sync_ldap_directory'...
Fetcher 'sync_ldap_directory' completed successfully in 3m 12s.
  Created: 15
  Updated: 898
  Failed:  0
```

#### Execution model

The command executes the fetcher directly in the CLI process using a
synchronous database session. It does NOT enqueue a Celery task. This
makes the command self-contained — it works even when Celery workers
are not running (e.g., during initial deployment bootstrap).

The command MUST:

1. Validate that `<name>` exists in the `FETCHER_REGISTRY`. If not,
   print an error with the list of available fetcher names and exit
   with code 1
2. Perform the concurrency check (see below)
3. Create a `FetcherRun` record with `status = running` and
   `triggered_by = manual` **before** calling `execute()`
4. Call the fetcher's `execute()` method
5. Update the `FetcherRun` record with final status, metrics, and
   timestamps
6. Print the summary to stdout

The `triggered_by_user_id` is set to `NULL` for CLI executions (there
is no authenticated user context in the CLI).

#### Concurrency check

Before executing, the command checks for an existing `FetcherRun` with
`status = running` for the requested fetcher.

**If a run is active and NOT stale**:

```
$ sentinel fetcher run sync_cves_nvd
Error: fetcher 'sync_cves_nvd' is already running (started 2026-04-27 12:00 UTC, 1m 30s ago).
```

Exit code 1.

**If a run is active and stale** (elapsed time exceeds the fetcher's
`timeout_seconds` from `FetcherConfig`, default 3600s). If
`timeout_seconds = 0`, the run is never considered stale — the command
treats it as an active run and exits with code 1 (same as the "not
stale" case above).

```
$ sentinel fetcher run sync_ldap_directory
Warning: fetcher 'sync_ldap_directory' has a run marked as 'running'
since 2026-04-27 04:00 UTC (2h 30m ago), which exceeds the timeout
(300s). This run appears stale.
Mark it as failed and proceed? [y/N]: y
```

On confirmation (`y`): the stale `FetcherRun` is updated to
`status = failure`, `error_message = "Marked as stale by operator via
CLI"`, `finished_at = now()`. Then the new run proceeds normally.

On rejection (`N` or Enter): exit with code 1.

If stdin is not a TTY (e.g., running in a script), the stale run
prompt is skipped and the command exits with code 1 and the warning
message. The operator must resolve the stale run interactively.

#### Enabled check bypass

Unlike the Celery `run_fetcher` task, the CLI command does NOT check
the `FetcherConfig.enabled` flag. The CLI is an explicit operator
action — if someone runs `sentinel fetcher run <name>`, they intend to
run it regardless of the enabled state. A warning is printed when
running a disabled fetcher:

```
Warning: fetcher 'check_codestream_releases' is currently disabled.
Running anyway (CLI bypass).
```

#### Signal handling

The CLI process MUST register handlers for `SIGINT` (Ctrl+C) and
`SIGTERM` to ensure the `FetcherRun` record is cleaned up on
interruption:

1. On signal received: update the `FetcherRun` record to
   `status = failure`, `error_message = "Interrupted by operator
   (SIGINT)"` (or `SIGTERM`), `finished_at = now()`,
   `duration_seconds` calculated from `started_at`
2. Print a message to stderr: `"\nInterrupted. Run marked as failed."`
3. Exit with code 130 for `SIGINT` (Unix convention) or 143 for
   `SIGTERM`

**SIGKILL (kill -9)**: cannot be intercepted. The `FetcherRun` record
will remain `running` in the database. This is the same situation that
occurs when a Celery worker is OOM-killed or crashes. The stale run
detection in `sentinel fetcher list` (showing `stale?`) and the stale
run resolution in `sentinel fetcher run` (interactive prompt) handle
this scenario.

#### Exit codes

| Code | Meaning |
|------|---------|
| 0    | Fetcher completed successfully (`success` or `partial`) |
| 1    | User error: unknown fetcher name, already running, stale run not confirmed |
| 2    | System error: database unreachable, unhandled exception in `execute()` |
| 130  | Interrupted by SIGINT (Ctrl+C) |
| 143  | Interrupted by SIGTERM |

**Idempotency**: Not idempotent (by design). Each invocation executes
the fetcher and produces side effects intentionally.

**Output channels**: progress and summary to stdout. `"Warning: ..."`
and `"Error: ..."` messages to stderr.

## System Metrics (Future Iteration)

The following metrics are planned for a future iteration and are NOT part
of the initial implementation:

- Memory usage (start, end, peak) via `psutil`
- CPU utilization during the run
- Database connection count

When implemented, these will be stored in a `system_metrics` JSONB column
on `FetcherRun` and displayed in an admin-only panel on the fetcher
detail page.

## Dependencies

- A charting library for the frontend (e.g., Recharts, which integrates
  well with shadcn/ui and React)
- Click (CLI framework) — see `docs/conventions.md` for CLI conventions

## Open Questions

None at this time.
