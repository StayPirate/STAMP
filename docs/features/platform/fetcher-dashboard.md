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
`FetcherAuditEvent`, `FetcherRunWeeklyAggregate`), concurrency control,
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

Returns all fetchers — both registered (present in the in-memory
`FETCHER_REGISTRY`) and deregistered (removed from the codebase but
with a `FetcherConfig` record still in the database). See
`docs/features/platform/fetcher-infrastructure.md`, "Deregistered
Fetcher Lifecycle" for background on how deregistered fetchers arise.

**Data source**: the endpoint merges two sources:

1. The `FETCHER_REGISTRY` provides registered fetchers with their
   code-defined metadata (`description`, `default_schedule`,
   `custom_settings_schema`)
2. `FetcherConfig` rows whose `fetcher_name` is NOT present in the
   registry provide deregistered fetchers (DB-stored configuration
   only; code-defined metadata is unavailable)

**Pagination**: not paginated. The total number of fetchers (registered
+ deregistered) is bounded — registered fetchers are expected <30, and
deregistered fetchers grow at most by units over the application's
lifetime (see `fetcher-infrastructure.md`). The full list is always
returned.

**Sorting**: results are ordered by `name` ascending (alphabetical).
Client-controlled sorting is not supported. Registered and deregistered
fetchers are interleaved alphabetically — the `registered` field
provides the distinction.

**Response** (200 OK):

```json
{
  "data": [
    {
      "name": "sync_cves_nvd",
      "registered": true,
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
      },
      "custom_settings": {}
    },
    {
      "name": "old_fetcher",
      "registered": false,
      "description": null,
      "enabled": true,
      "schedule": null,
      "schedule_is_override": null,
      "default_schedule": null,
      "next_run_at": null,
      "last_run": {
        "id": "uuid",
        "started_at": "2026-01-15T08:00:00Z",
        "finished_at": "2026-01-15T08:00:45Z",
        "duration_seconds": 45.0,
        "status": "success",
        "items_created": 3,
        "items_updated": 10,
        "items_failed": 0
      },
      "custom_settings": {
        "throttle_delay_seconds": 5.0
      }
    }
  ]
}
```

**Fields**:
- `registered`: `true` if the fetcher class is present in the
  `FETCHER_REGISTRY`, `false` if the class has been removed from the
  codebase (deregistered). Deregistered fetchers cannot be triggered,
  configured, or scheduled — only their historical data is accessible.
- `description`: human-readable description from the fetcher class.
  `null` for deregistered fetchers (the class no longer exists).
- `enabled`: whether the fetcher is active. For deregistered fetchers,
  this reflects the stored DB value at the time the fetcher was removed.
  It has no practical effect — the fetcher cannot be scheduled or
  triggered regardless of this value.
- `schedule`: the effective schedule (override if set, otherwise default).
  For deregistered fetchers: the stored `schedule_override` if set,
  otherwise `null` (the code-defined default is no longer available).
- `schedule_is_override`: `true` if the schedule comes from
  `FetcherConfig`. `null` for deregistered fetchers (the concept does
  not apply without a default to compare against).
- `default_schedule`: the schedule defined in code. `null` for
  deregistered fetchers.
- `next_run_at`: calculated from the effective schedule and the Celery
  Beat state. `null` if the fetcher is disabled or deregistered.
- `last_run`: the most recent `FetcherRun` record, or `null` if never
  run. Does NOT include `error_traceback` (admin-only, available on the
  detail endpoint).
- `custom_settings`: included in each fetcher's data (current values
  from DB). For deregistered fetchers, contains the raw stored values
  (schema defaults and descriptions are not available). `settings_schema`
  is NOT included in the list response to keep the payload compact — the
  UI fetches it only when opening the configuration panel for a specific
  fetcher (via the GET config endpoint).

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
- `error_detail` and `error_traceback` are NOT included in list responses
- `triggered_by_user` is an object `{"id": "uuid", "username": "admin1"}`
  when `triggered_by` is `manual`, otherwise `null`

**Permissions**: publicly accessible (no authentication required).

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No `FetcherConfig` record exists for this fetcher name |

### Get Fetcher Run Detail

```
GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}
```

Returns full detail for a single run.

**Response** (200 OK):

Same fields as the list response, plus:
- `error_detail`: included ONLY if the requesting user has the Admin
  role. The field is **absent from the response body** for
  unauthenticated callers and authenticated users without the Admin role.
- `error_traceback`: included ONLY if the requesting user has the Admin
  role. The field is **absent from the response body** for
  unauthenticated callers and authenticated users without the Admin role.

**Permissions**: publicly accessible (no authentication required). Admin
users see additional fields (`error_detail`, `error_traceback`).

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No `FetcherConfig` record exists for this fetcher name, or run not found |

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
  derived from `FetcherAuditEvent` records. Used to render grey overlay
  bands on the chart. If the fetcher is currently disabled, `enabled_at`
  and `enabled_by` are `null`.

**Permissions**: publicly accessible (no authentication required).

**Sorting**: results are returned in chronological order (`timestamp`
ascending). Client-controlled sorting is not supported — the data is
time-series and must be in chronological order for chart rendering.

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No `FetcherConfig` record exists for this fetcher name |

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
| 404 | `FETCHER_NOT_FOUND` | No `FetcherConfig` record exists for this fetcher name |
| 409 | `FETCHER_DEREGISTERED` | Fetcher exists in DB but is not present in the registry (code removed). Cannot be triggered. |
| 409 | `FETCHER_DISABLED` | Fetcher is disabled (`enabled = false` in `FetcherConfig`) |
| 409 | `FETCHER_ALREADY_RUNNING` | Fetcher is already running (a non-stale `FetcherRun` with status `running` exists for this fetcher). If the active run is stale and `timeout_seconds > 0`, it is marked as `failure` and the new run proceeds (returns 202). |

**Permissions**: Admin only.

**Side effects**:
- Creates a `FetcherAuditEvent` record with `event_type = triggered`
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

Returns the current configuration for a fetcher, including any
fetcher-specific custom settings and the schema that describes them.

**Response** (200 OK):

```json
{
  "data": {
    "fetcher_name": "sync_cvss_redhat",
    "enabled": true,
    "schedule_override": null,
    "default_schedule": "0 3 * * *",
    "effective_schedule": "0 3 * * *",
    "timeout_seconds": 3600,
    "rate_limit": null,
    "custom_settings": {
      "throttle_delay_seconds": 5.0
    },
    "settings_schema": {
      "throttle_delay_seconds": {
        "type": "float",
        "default": 2.0,
        "min": 0.1,
        "max": 30.0,
        "description": "Delay between consecutive Red Hat API requests."
      }
    },
    "updated_at": "2025-04-20T10:00:00Z"
  }
}
```

**Fields**:
- `custom_settings`: current values stored in the DB. Keys not explicitly
  set by an admin are absent (the fetcher code falls back to schema
  defaults via `get_setting()`). An empty object `{}` means all settings
  use their defaults.
- `settings_schema`: the schema declared by the fetcher class (read-only,
  not stored in DB). Included so the UI can render the settings form
  without hardcoding field definitions. `null` if the fetcher declares no
  custom settings or if the fetcher is deregistered.
- `default_schedule`: the schedule defined in code. `null` for
  deregistered fetchers.
- `effective_schedule`: the effective schedule (override if set, otherwise
  default). For deregistered fetchers: the stored `schedule_override` if
  set, otherwise `null`.

**Deregistered fetcher behavior**: when this endpoint is called for a
deregistered fetcher (present in DB but not in the registry), the
response is a read-only snapshot of the stored configuration.
`settings_schema` and `default_schedule` are `null` because the fetcher
class is no longer available. The `custom_settings` field contains the
raw stored values without schema context (descriptions, defaults, and
ranges are unavailable).

**Permissions**: Admin only.

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No `FetcherConfig` record exists for this fetcher name |

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
  "rate_limit": "2/s",
  "custom_settings": {
    "throttle_delay_seconds": 5.0
  }
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

**Validation rules for `custom_settings`**:
- Each key MUST exist in the fetcher's `custom_settings_schema`. Unknown
  keys → 422 with code `FETCHER_SETTING_UNKNOWN` and message
  `"Unknown setting '{key}' for fetcher '{name}'. Available settings:
  [{valid_keys}]."`
- Each value MUST match the declared type. Type mismatch → 422 with
  code `FETCHER_SETTING_INVALID` and message `"Invalid value for
  '{key}': expected {type}, got {actual_type}."`
- Numeric values MUST respect `min`/`max` bounds if declared. Out of
  range → 422 with code `FETCHER_SETTING_INVALID` and message
  `"Value for '{key}' must be between {min} and {max}."`
- String values MUST be in `choices` if declared. Invalid choice → 422
  with code `FETCHER_SETTING_INVALID` and message `"Value for '{key}'
  must be one of: [{choices}]."`
- Partial updates are supported: only the keys included in the request
  are updated. Omitted keys retain their current value. To reset a
  setting to its default, the key must be explicitly set to `null`:
  `{"custom_settings": {"throttle_delay_seconds": null}}` — this
  removes the key from the JSONB column, causing `get_setting()` to
  fall back to the schema default.
- If the fetcher declares no `custom_settings_schema` and the request
  includes `custom_settings` with any keys → 422 with code
  `FETCHER_SETTING_UNKNOWN` and message `"Fetcher '{name}' does not
  accept custom settings."`
- **Merge semantics**: `custom_settings` is merged (not replaced) with
  the existing JSONB value. This is consistent with the partial-update
  semantics of the other fields.

**Response** (200 OK): the updated config object (same as GET response).

**Side effects**:
- Creates `FetcherAuditEvent` records (one per changed field — see
  `docs/features/platform/fetcher-infrastructure.md`, "One Event Per Field
  Rule"):
  - If `enabled` changed: one event with `event_type = disabled` or
    `enabled` (`old_value`, `new_value`, and `detail` are all `null`)
  - For each standard field that changed (`schedule_override`,
    `timeout_seconds`, `rate_limit`): one event with
    `event_type = config_changed`, `old_value` = previous value,
    `new_value` = new value, `detail = {"field": "<field_name>"}`
  - For each `custom_settings` sub-key that changed: one event with
    `event_type = config_changed`, `old_value` = previous value as
    string (or `null` if set for the first time), `new_value` = new
    value as string (or `null` if reset to default),
    `detail = {"field": "custom_settings", "key": "<setting_key>"}`
  - All events from the same PATCH share the same `created_at` and
    `user_id`
- If `enabled` changed to `false` and the fetcher is currently running:
  the current run is allowed to complete. The next scheduled run will not
  start.
- If `schedule_override` changed: the Celery Beat schedule for this
  fetcher MUST be updated dynamically

**Permissions**: Admin only.

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No `FetcherConfig` record exists for this fetcher name |
| 409 | `FETCHER_DEREGISTERED` | Fetcher exists in DB but is not present in the registry (code removed). Cannot be configured. |
| 422 | `FETCHER_SETTING_UNKNOWN` | Unknown key in `custom_settings` (not declared in the fetcher's schema) |
| 422 | `FETCHER_SETTING_INVALID` | Value in `custom_settings` fails type, range, or choices validation |
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
| `event_type` | string | -- | Comma-separated list of event types (e.g., `disabled,enabled`) |
| `actor` | string | -- | Filter by actor: user UUID or username. Follows User Identifier Resolution (see `docs/api-spec.md`). Returns events performed by the specified user. |
| `from_date` | string | -- | ISO 8601 date/datetime. Include events from this date onwards (inclusive) |
| `to_date` | string | -- | ISO 8601 date/datetime. Include events up to this date (inclusive) |

**Sorting**: default `sort_by=created_at`, `sort_order=desc` (most recent
entry first). Follows the project-wide default sorting convention.

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "fetcher_name": "sync_cves_nvd",
      "event_type": "config_changed",
      "actor": {
        "id": "uuid",
        "username": "admin1",
        "full_name": "Alice Smith"
      },
      "old_value": "0 */6 * * *",
      "new_value": "0 */4 * * *",
      "detail": {"field": "schedule_override"},
      "created_at": "2025-04-18T14:31:00Z"
    },
    {
      "id": "uuid",
      "fetcher_name": "sync_cves_nvd",
      "event_type": "disabled",
      "actor": {
        "id": "uuid",
        "username": "admin1",
        "full_name": "Alice Smith"
      },
      "old_value": null,
      "new_value": null,
      "detail": null,
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

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No `FetcherConfig` record exists for this fetcher name |

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

1. **Header**: fetcher name (human-readable `description`; if
   `description` is `null` — as for deregistered fetchers — display the
   `name` field instead) and a status indicator:
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

#### Deregistered Fetcher Section

Below the active fetcher card grid, a collapsible section displays
deregistered fetchers (those with `registered: false` in the API
response). The section is hidden when there are no deregistered
fetchers.

**Section header**: "Deregistered fetchers (N)" where N is the count.
Collapsed by default.

When expanded, each deregistered fetcher is rendered as a card with the
following differences from active fetcher cards:

1. **Visual distinction**: muted colors (reduced opacity) to clearly
   differentiate from active fetchers
2. **Header**: displays `name` (the technical identifier, since
   `description` is `null`). A "Deregistered" badge replaces the
   status dot
3. **Last run summary**: same layout as active cards (data from DB)
4. **Schedule info**: replaced with a single "Deregistered" label — no
   schedule or next run information (the fetcher cannot be scheduled)
5. **Admin controls**: not rendered (no toggle switch, no "Run Now"
   button). The fetcher cannot be triggered or configured
6. **Click target**: clicking navigates to the fetcher detail page

#### Fetcher Detail Page

**Route**: `/fetchers/:name`

**Access**: publicly accessible (no authentication required).

This page works for both registered and deregistered fetchers. When
displaying a deregistered fetcher, the following differences apply:

- An informational banner is displayed at the top of the page: *"This
  fetcher has been deregistered (removed from codebase). Historical
  data is displayed below."*
- The page title displays the `name` field (technical identifier)
  instead of the `description` (which is `null`)
- The **Admin Configuration Panel** is not rendered (the fetcher
  cannot be configured — see `FETCHER_DEREGISTERED` error on the
  PATCH endpoint)
- The **Admin Audit Log** section is rendered normally (read-only
  historical data, useful for forensic investigation)
- The **"Run Now" button** (if present in the page header for admin
  users) is not rendered
- **Run history data availability**: individual `FetcherRun` records
  are subject to the retention window of the `aggregate_fetcher_runs`
  task (default: 90 days). After the retention period, only
  `FetcherRunWeeklyAggregate` records remain. The timeline chart
  displays aggregated data transparently, but the run history table
  will show no entries beyond the retention window

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
   default" button when an override is active. Help text: "Cron expression
   (5-field) to override the default schedule. Leave empty to use the
   default."
3. **Timeout**: numeric input in seconds. Default: 3600 (1 hour). Set to
   0 to disable timeout enforcement and stale run detection. Help text:
   "Controls both the maximum execution time (Celery soft time limit)
   and the stale run detection threshold. Set to 0 to disable."
4. **Rate limit**: text input with format hint (`"2/s"`, `"100/m"`).
   Help text: "Maximum execution frequency for this task (e.g., '2/s',
   '100/m'). This controls how often the task can run, not the delay
   between individual HTTP requests within a run."
5. **Fetcher-specific settings**: a dynamic form section that appears
   only for fetchers that declare a `custom_settings_schema` (see below)
6. **Save button**: PATCHes the config. Shows confirmation dialog if
   changing the schedule.

###### Fetcher-specific settings

This section appears only for fetchers that declare a
`custom_settings_schema`. If a fetcher has no schema (or an empty one),
the section is not rendered — no "this fetcher has no custom settings"
placeholder, just absence.

The section is visually separated from the standard config fields with a
subheading: **"Fetcher-specific settings"**.

The form is rendered dynamically based on `settings_schema` from the
GET config response:

| Schema type | UI control |
|-------------|------------|
| `int` | Number input (step 1, with min/max if declared) |
| `float` | Number input with decimal step (step 0.1, with min/max if declared) |
| `str` | Text input, or select dropdown if `choices` is declared |
| `bool` | Toggle switch |

Each field shows:

- **Label**: the setting key formatted for display (underscores replaced
  with spaces, title case — e.g., `throttle_delay_seconds` becomes
  "Throttle Delay Seconds")
- **Description**: from the `description` property in the schema, shown
  as help text below the input
- **Current value**: from `custom_settings` in the config response. If
  the key is not present (not explicitly configured), the input shows
  the schema default as a placeholder
- **Reset button**: per-field button that sends `null` for that key to
  reset it to the schema default
- **Warning** (conditional): if the schema entry includes a `warning`
  property, the field is rendered with:
  - A yellow triangle icon next to the label
  - The warning text displayed in a visually distinct box (amber/yellow
    background) below the description
  - The field remains editable — no extra confirmation dialog, but the
    warning is always visible

##### Admin Audit Log

Visible only to Admin role users. Displayed as a tab or collapsible
section.

A simple chronological list of admin actions from `FetcherAuditEvent`:

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
| View error details | Yes | No | No |
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
| Retention | Configurable via `retention_days` custom setting (default: 90 days) |
| Aggregation | Weekly (ISO week, Monday start) |

The aggregation task is itself a fetcher (`AggregationFetcher` inheriting
`BaseFetcher`) so its own execution is tracked in the dashboard.

**Custom Settings**

This fetcher declares the following custom settings (see
`docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
Schema" for the schema structure and validation rules):

| Setting | Type | Default | Range | Description |
|---------|------|---------|-------|-------------|
| `retention_days` | int | 90 | 7–365 | Days to retain individual FetcherRun records before aggregation |

## CLI Commands

The `sentinel fetcher` command group provides operational access to the
fetcher infrastructure from the command line. It is designed for
bootstrap, troubleshooting, and environments where the API/UI is not
yet available. It is NOT a replacement for the API — configuration
changes (schedule, timeout, rate limit, enable/disable) are done
exclusively through the API.

### `sentinel fetcher list`

Lists all fetchers (registered and deregistered) with their current
state.

```
sentinel fetcher list
```

Output (human-readable table to stdout):

```
Name                       Enabled   Last Run              Status                       Settings
sync_ldap_directory        yes       2026-04-27 04:00 UTC  success (3m 12s)             2 custom
sync_cves_nvd              yes       2026-04-27 12:00 UTC  running (1m 30s elapsed)     —
sync_products_smelt        yes       2026-04-26 06:00 UTC  success (45s)                —
check_ibs_track_releases  no        2026-04-25 02:00 UTC  failure                      —
sync_requests              yes       2026-04-27 02:30 UTC  success (2m 15s)             —
aggregate_fetcher_runs     yes       —                     never run                    —

Deregistered (historical data only):
Name                       Last Run              Status
old_fetcher                2026-01-15 08:00 UTC  success (45s)
```

The deregistered section is displayed only when `FetcherConfig` records
exist in the database for fetcher names not present in the
`FETCHER_REGISTRY`. If there are no deregistered fetchers, the section
is omitted entirely.

The deregistered table uses a reduced column set: no "Enabled" column
(the fetcher cannot be toggled) and no "Settings" column (the schema
is unavailable).

**Settings column logic** (registered fetchers only):
- Shows the count of explicitly configured (non-default) custom settings
  from `FetcherConfig.custom_settings` — e.g., `2 custom` means 2 keys
  are set to non-default values
- Shows `—` if the fetcher has no `custom_settings_schema` or if all
  settings use their defaults (JSONB is `{}`)

**Status column logic** (applies to both registered and deregistered):

1. If a `FetcherRun` with `status = running` exists for the fetcher:
   show `running ({elapsed} elapsed)` where elapsed is calculated from
   `started_at`. If `timeout_seconds > 0` and the elapsed time exceeds
   it, append `(stale?)` — e.g., `running (2h 30m elapsed, stale?)`.
   If `timeout_seconds = 0`, the `(stale?)` hint is never shown.
2. If no running record exists but completed runs exist: show the status
   of the most recent `FetcherRun` with its duration — e.g.,
   `success (3m 12s)`, `failure`, `partial (1m 5s)`
3. If no `FetcherRun` records exist: show `never run`

**Enabled column** (registered fetchers only): reads from
`FetcherConfig.enabled`. If no `FetcherConfig` record exists for the
fetcher, defaults to `yes`.

**Data source**: queries the database directly (synchronous session).
The fetcher registry provides the list of registered fetcher names;
`FetcherConfig` rows whose `fetcher_name` is not in the registry
provide deregistered fetchers. The database provides `FetcherRun` and
`FetcherConfig` data for both.

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

1. Validate that `<name>` exists in the `FETCHER_REGISTRY`. If not:
   - If a `FetcherConfig` record exists in the database for the name
     (deregistered fetcher), print a specific error to stderr:
     `"Error: fetcher '<name>' is deregistered (removed from codebase)
     and cannot be executed."` and exit with code 1
   - Otherwise (completely unknown name), print an error with the list
     of available fetcher names and exit with code 1
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
Warning: fetcher 'check_ibs_track_releases' is currently disabled.
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
| 1    | User error: unknown or deregistered fetcher name, already running, stale run not confirmed |
| 2    | System error: database unreachable, unhandled exception in `execute()` |
| 130  | Interrupted by SIGINT (Ctrl+C) |
| 143  | Interrupted by SIGTERM |

**Idempotency**: Not idempotent (by design). Each invocation executes
the fetcher and produces side effects intentionally.

**Output channels**: progress and summary to stdout. `"Warning: ..."`
and `"Error: ..."` messages to stderr.

### `sentinel fetcher config <name>`

Displays the full configuration of a fetcher, including custom settings
with their current values, defaults, and descriptions.

```
sentinel fetcher config sync_cvss_redhat
```

Output (to stdout):

```
Fetcher: sync_cvss_redhat
Enabled: yes
Schedule: 0 3 * * * (default)
Timeout: 3600s
Rate limit: —

Custom settings:
  throttle_delay_seconds = 5.0  (default: 2.0, range: 0.1–30.0)
    Delay between consecutive Red Hat API requests.
```

For a fetcher with settings at their defaults (no explicit
configuration):

```
Fetcher: aggregate_fetcher_runs
Enabled: yes
Schedule: 0 3 * * * (default)
Timeout: 3600s
Rate limit: —

Custom settings:
  retention_days = 90  (default, range: 7–365)
    Days to retain individual FetcherRun records before aggregation.
```

For a fetcher with no custom settings schema:

```
Fetcher: sync_cves_nvd
Enabled: yes
Schedule: 0 */6 * * * (default)
Timeout: 3600s
Rate limit: —

No custom settings available for this fetcher.
```

For a deregistered fetcher (present in DB but not in the registry):

```
Fetcher: old_fetcher (deregistered)
Enabled: yes
Schedule override: 0 */6 * * *
Timeout: 3600s
Rate limit: —

Custom settings (schema unavailable — raw stored values):
  throttle_delay_seconds = 5.0
```

Differences from the registered fetcher output:

- The header includes `(deregistered)` after the fetcher name
- "Schedule" becomes "Schedule override" since the code-defined default
  is unavailable — only the stored override value (if any) is shown.
  If no override was stored, the line shows `—`
- Custom settings are displayed as raw key-value pairs without defaults,
  ranges, or descriptions (the schema from the fetcher class is
  unavailable). If `custom_settings` is empty (`{}`), the section shows:
  `"No custom settings stored."`

**Value display logic** (registered fetchers only):
- If a setting is explicitly configured (key exists in JSONB): show
  `key = value  (default: X, range: Y–Z)`
- If a setting uses its default (key absent from JSONB): show
  `key = value  (default, range: Y–Z)`
- `range` is shown only for `int`/`float` with `min`/`max` declared
- `choices` are shown as `choices: a, b, c` for `str`/`int` with choices

**Data source**: queries `FetcherConfig` from the database. For
registered fetchers, also reads `custom_settings_schema` from the
fetcher registry. For deregistered fetchers, only DB-stored data is
available.

**Idempotency**: Idempotent. Read-only command; safe to re-run at any
time.

**Exit codes**:

| Code | Meaning |
|------|---------|
| 0    | Success (including deregistered fetchers — read-only display) |
| 1    | User error: unknown fetcher name (not in registry and not in DB) |
| 2    | System error: database unreachable |

**Output channels**: configuration to stdout. `"Error: ..."` messages
to stderr.

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

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
