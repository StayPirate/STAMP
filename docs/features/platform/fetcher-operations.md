# Fetcher Operations

## Purpose

Provide centralized monitoring and operational control for all data
fetchers in Sentinel. All users have visibility into fetcher health and
performance (no authentication required), while users with the
`manage_fetchers` capability have operational control (manual trigger,
enable/disable, configuration) and CLI commands for diagnostics and
troubleshooting.

This feature depends on the fetcher infrastructure defined in
`docs/features/platform/fetcher-infrastructure.md`. Read that spec first for the
`BaseFetcher` contract, data model (`FetcherRun`, `FetcherConfig`,
`FetcherAuditEvent`), concurrency control, and stale run detection.

## API Endpoints

### IBS RabbitMQ Consumer Status

```
GET /api/v1/ibs-consumer/status
```

**`Access: Public`**
**`Authentication: Optional`**

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
    "processing_failed": 4,
    "last_error": null,
    "reconnect_attempts": 0,
    "next_retry_seconds": null
  }
}
```

**Response when consumer just lost connection** (200 OK):

```json
{
  "data": {
    "status": "disconnected",
    "status_since": "2026-04-23T16:44:55Z",
    "events_received": 12847,
    "events_relevant": 342,
    "events_processed": 338,
    "processing_failed": 4,
    "last_error": "Connection reset by peer",
    "reconnect_attempts": 0,
    "next_retry_seconds": 5
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
    "processing_failed": 4,
    "last_error": "Connection refused",
    "reconnect_attempts": 7,
    "next_retry_seconds": 245
  }
}
```

Note: event counters retain the values from the last active connection
(they are reset only when a new connection is successfully established,
not on disconnection).

**Response when Redis key is absent or Redis is unavailable** (200 OK):

```json
{
  "data": {
    "status": "unreachable",
    "status_since": null,
    "events_received": null,
    "events_relevant": null,
    "events_processed": null,
    "processing_failed": null,
    "last_error": null,
    "reconnect_attempts": null,
    "next_retry_seconds": null
  }
}
```

If reading the heartbeat raises any `RedisError` (including connection
failure, timeout, OOM rejection, or protocol error), the endpoint returns the
same `200 OK` `unreachable` response shown above. It does not distinguish a
missing heartbeat from an unavailable heartbeat store because neither case
can confirm that the consumer is alive. The failure is logged at WARNING for
operators; exception details are not exposed in the public response.

A heartbeat value that cannot be parsed as JSON or validated against the
response fields is handled identically: return the `unreachable` response and
log a WARNING without exposing the invalid value or parsing details.

**Fields**:

| Field | Description | `connected` | `disconnected` | `reconnecting` | `unreachable` |
|---|---|---|---|---|---|
| `status` | Current connection state | `"connected"` | `"disconnected"` | `"reconnecting"` | `"unreachable"` |
| `status_since` | ISO 8601 timestamp of when the current status began | datetime | datetime | datetime | `null` |
| `events_received` | Total events received from all subscribed topics since connection was established | integer | integer (retained) | integer (retained) | `null` |
| `events_relevant` | Events that passed the active codestream/package filter | integer | integer (retained) | integer (retained) | `null` |
| `events_processed` | Events where processing completed successfully | integer | integer (retained) | integer (retained) | `null` |
| `processing_failed` | Events where processing failed (diff request error, metadata fetch timeout, etc.) | integer | integer (retained) | integer (retained) | `null` |
| `last_error` | Last error message (e.g., "Connection refused") | `null` | string (disconnection reason) | string (last retry error) | `null` |
| `reconnect_attempts` | Number of reconnection attempts since disconnection | `0` | `0` | integer (incrementing) | `null` |
| `next_retry_seconds` | Seconds until the next reconnection attempt | `null` | integer (initial delay, 5s) | integer (backoff) | `null` |

### List Fetchers

```
GET /api/v1/fetchers
```

**`Access: Public`**
**`Authentication: Optional`**

Returns all fetchers — both registered (present in the in-memory
`FETCHER_REGISTRY`) and deregistered (removed from the codebase but
with a `FetcherConfig` record still in the database). See
`docs/features/platform/fetcher-infrastructure.md`, "Deregistered
Fetcher Lifecycle" for background on how deregistered fetchers arise.

**Data source**: the endpoint merges two sources:

1. The `FETCHER_REGISTRY` provides registered fetchers with their
   code-defined metadata (`description`, `default_schedule`,
   `Settings` model)
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
      "name": "sync_nvd_cves",
      "registered": true,
      "description": "Incremental CVE sync from NVD",
      "enabled": true,
      "schedule": "0 */6 * * *",
      "schedule_is_override": false,
      "default_schedule": "0 */6 * * *",
      "cve_source_type": "nvd",
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
      "cve_source_type": null,
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
        "results_per_page": 500
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
- `cve_source_type`: the `CVESourceType` identifier for CVE fetchers
  (`BaseCVEFetcher` subclasses), e.g., `"nvd"`, `"mitre"`. `null` for
  non-CVE fetchers and deregistered fetchers (the class attribute is
  unavailable when the code is removed).
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
  Beat state. `null` if the fetcher is disabled, deregistered, or the
  Celery Beat schedule state is unavailable (e.g., Redis flushed, Beat
  not yet started). See `docs/features/platform/fetcher-infrastructure.md`
  (Celery Beat Schedule Synchronization — `next_run_at` Calculation) for
  the computation mechanism.
- `last_run`: the most recent `FetcherRun` record, or `null` if never
  run. Does NOT include `error_traceback` (requires `manage_fetchers`,
  available on the detail endpoint). Note: for deregistered fetchers that have no
  `FetcherRun` records (e.g., a fetcher that was registered but never
  triggered before being removed), this field is `null`.
- `custom_settings`: included in each fetcher's data (current values
  from DB). For deregistered fetchers, contains the raw stored values
  (schema defaults and descriptions are not available). `settings_schema`
  is NOT included in the list response to keep the payload compact — the
  UI fetches it only when opening the configuration panel for a specific
  fetcher (via the GET config endpoint).

### List Fetcher Runs

```
GET /api/v1/fetchers/{fetcher_name}/runs
```

**`Access: Public`**
**`Authentication: Optional`**

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
      "fetcher_name": "sync_nvd_cves",
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
- `triggered_by_user` is a User Reference Object
  `{"id": "uuid", "username": "admin1", "full_name": "Alice Smith", "active": true}`
  when `triggered_by` is `manual`, otherwise `null`

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists (not in the registry and no `FetcherConfig` record in the database) |

### Get Fetcher Run Detail

```
GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}
```

**`Access: Public`**
**`Authentication: Optional`**

Returns full detail for a single run.

**Response** (200 OK):

Same fields as the list response, plus:
- `error_detail`: included ONLY if the requesting user has the
  `manage_fetchers` capability. The field is **absent from the response
  body** for callers without this capability.
- `error_traceback`: included ONLY if the requesting user has the
  `manage_fetchers` capability. The field is **absent from the response
  body** for callers without this capability.

Users with `manage_fetchers` capability see additional fields (`error_detail`,
`error_traceback`).

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists (not in the registry and no `FetcherConfig` record in the database), or the specified run was not found |

**Failure drill-down**: for CVE fetchers (where `cve_source_type` is
defined in the fetcher registry response), the run detail view can link
to `GET /api/v1/cve-sources?source={cve_source_type}&status=failure&from_date={started_at}&to_date={finished_at}`
to show individual CVEs that failed during the run. For runs still in
`running` status, omit `to_date` for a live view of accumulated
failures. See `docs/features/tickets/cve-service.md` (Global CVE Source
Listing).

### Get Fetcher Run Timeline Data

```
GET /api/v1/fetchers/{fetcher_name}/timeline
```

**`Access: Public`**
**`Authentication: Optional`**

Returns time-series data optimized for chart rendering. Each data point
represents an individual `FetcherRun` record.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `from_date` | datetime | 7 days ago | Start of the time range |
| `to_date` | datetime | now | End of the time range |

**Date range constraint**: the maximum allowed interval between
`from_date` and `to_date` is **1825 days** (5 years). If the requested
interval exceeds this limit, the endpoint returns 400 Bad Request with
code `DATE_RANGE_TOO_WIDE`. This constraint provides defense-in-depth
against accidentally unbounded responses on a publicly accessible
endpoint.

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
        "status": "success"
      },
      {
        "timestamp": "2025-04-19T12:00:00Z",
        "duration_seconds": 210.5,
        "items_created": 8,
        "items_updated": 32,
        "items_failed": 0,
        "status": "success"
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
- `points[].timestamp`: `started_at` of the `FetcherRun`
- `points[].status`: the run status (`success`, `failure`, `partial`)
- `points[].duration_seconds`: actual duration of the run
- `points[].items_created/updated/failed`: actual counts from the run
- `disabled_periods`: array of time ranges when the fetcher was disabled,
  derived from `FetcherAuditEvent` records. Used to render grey overlay
  bands on the chart. If the fetcher is currently disabled, `enabled_at`
  and `enabled_by` are `null`.

**Sorting**: results are returned in chronological order (`timestamp`
ascending). Client-controlled sorting is not supported — the data is
time-series and must be in chronological order for chart rendering.

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 400 | `DATE_RANGE_TOO_WIDE` | Requested interval between `from_date` and `to_date` exceeds 1825 days |
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists (not in the registry and no `FetcherConfig` record in the database) |

### Trigger Fetcher

```
POST /api/v1/fetchers/{fetcher_name}/trigger
```

Enqueues a manual run of the specified fetcher.

**Response** (202 Accepted):

```json
{
  "data": {
    "run_id": "uuid",
    "message": "Fetcher 'sync_nvd_cves' has been queued for execution"
  }
}
```

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists (not in the registry and no `FetcherConfig` record in the database) |
| 409 | `FETCHER_DEREGISTERED` | Fetcher exists in DB but is not present in the registry (code removed). Cannot be triggered. |
| 409 | `FETCHER_DISABLED` | Fetcher is disabled (`enabled = false` in `FetcherConfig`) |
| 409 | `FETCHER_ALREADY_RUNNING` | Fetcher is already running (a non-stale `FetcherRun` with status `running` exists for this fetcher). If the active run is stale, it is marked as `failure` and the new run proceeds (returns 202). |
| 503 | `CELERY_UNAVAILABLE` | Task broker unavailable — run record marked as failed |

**`Capability: manage_fetchers`**

**Side effects**:
- Creates a `FetcherAuditEvent` record with `event_type = triggered`
- Creates a `FetcherRun` record **synchronously** (before enqueuing the
  Celery task) with `status = running` and `triggered_by = manual`. This
  ensures the `run_id` is available in the API response
- Passes `run_id` to the Celery task via `run_fetcher.apply_async(kwargs=
  {"fetcher_name": name, "triggered_by": "manual", "user_id": str(user.id),
  "run_id": str(run.id)}, time_limit=time_limit,
  soft_time_limit=soft_time_limit, queue=queue)` where `time_limit` and
  `soft_time_limit` are read from `FetcherConfig.run_timeout` using the
  same formula as the redbeat entry (see
  `docs/features/platform/fetcher-infrastructure.md`, "Celery Beat
  Schedule Synchronization — Time Limits and Queue Routing"). `queue` is read from the
  fetcher's class attribute (`FETCHER_REGISTRY[name].queue`); if `None`,
  no queue option is passed (task goes to default queue). The task
  forwards `run_id` to `fetcher.run(run_id=run_id, ...)`, which updates
  the existing record instead of creating a new one

**Enqueue failure handling**: after creating the `FetcherRun` record, the
endpoint calls `apply_async` on the Celery broker. If enqueue succeeds,
the endpoint returns 202 with the `run_id` (normal path). If enqueue
fails (any exception from Celery/Redis), the endpoint updates the
`FetcherRun` record to `status = failure`,
`error_message = "Celery task enqueue failed: {exception}"`,
`finished_at = now()`, `duration_seconds = 0`, then returns 503 Service
Unavailable with code `CELERY_UNAVAILABLE`. This cleanup is critical
because the `FetcherRun` record with `status = running` is the
concurrency mechanism — if not cleaned up, it blocks all future runs of
this fetcher until the stale detection threshold (default 3660s,
derived from `run_timeout + 60`).

**Note on trigger-then-disable race condition**: if an admin triggers a
fetcher (passing the enabled check in this endpoint) and another admin
disables the fetcher before the Celery worker picks up the task,
`BaseFetcher.run()` detects the pre-existing `FetcherRun` record and
updates it to `status = failure` with
`error_message = 'Fetcher disabled between trigger and execution'`
instead of exiting silently. See `fetcher-infrastructure.md`, "Enabled
check" for the full contract.

**Note on on-demand CVE fetch**: when Sentinel encounters an unknown CVE-ID
during ticket creation or CVE association, it triggers on-demand
single-CVE fetches via standalone Celery tasks (not through this trigger
endpoint). These on-demand fetches are sub-operations that do not create
`FetcherRun` records, do not check concurrency, and do not appear in the
dashboard. See `docs/features/tickets/cve-service.md`, "On-Demand Fetch:
fetch_single_cve" for details.

### Get Fetcher Config

```
GET /api/v1/fetchers/{fetcher_name}/config
```

Returns the current configuration for a fetcher, including any
fetcher-specific custom settings and the schema that describes them.

**Response** (200 OK):

```json
{
  "data": {
    "fetcher_name": "sync_redhat_cves",
    "enabled": true,
    "schedule_override": null,
    "default_schedule": "0 3 * * *",
    "effective_schedule": "0 3 * * *",
    "run_timeout": 3600,
    "request_delay": 0,
    "custom_settings": {
      "results_per_page": 500
    },
    "settings_schema": {
      "type": "object",
      "title": "Settings",
      "properties": {
        "results_per_page": {
          "type": "integer",
          "default": 2000,
          "minimum": 100,
          "maximum": 2000,
          "description": "Number of CVE records per API page."
        }
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
- `settings_schema`: standard JSON Schema generated by the fetcher's
  `Settings` Pydantic model (`Settings.model_json_schema()`). Read-only,
  not stored in DB. Included so the UI can render the settings form
  without hardcoding field definitions. `null` if the fetcher declares no
  `Settings` class or if the fetcher is deregistered.
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

**`Capability: manage_fetchers`**

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists (not in the registry and no `FetcherConfig` record in the database) |

### Update Fetcher Config

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
  "run_timeout": 600,
  "request_delay": 2.0,
  "custom_settings": {
    "results_per_page": 500
  }
}
```

**Validation rules**:
- `schedule_override`: must be a valid 5-field cron expression, or `null`
  to revert to the default schedule
- `run_timeout`: must be an integer between 60 and 604800 (1 minute
  to 7 days). Controls Celery hard/soft time limits and the stale run
  detection threshold. Default: 3600 (1 hour)
- `request_delay`: must be a float >= 0 and <= 300

**Validation rules for `custom_settings`**:
- Each key MUST exist in the fetcher's `Settings` model. Unknown
  keys → 422 with code `FETCHER_SETTING_UNKNOWN` and message
  `"Unknown setting '{key}' for fetcher '{name}'. Available settings:
  [{valid_keys}]."`
- Each value MUST pass the Pydantic field validation (type, constraints).
  Validation failure → 422 with code `FETCHER_SETTING_INVALID` and
  message containing the Pydantic validation error details.
- Partial updates are supported: only the keys included in the request
  are updated. Omitted keys retain their current value. To reset a
  setting to its default, the key must be explicitly set to `null`:
  `{"custom_settings": {"results_per_page": null}}` — this
  removes the key from the JSONB column, causing `get_setting()` to
  fall back to the model's field default.
- If the fetcher declares no `Settings` class and the request
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
    `run_timeout`, `request_delay`): one event with
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
- If `schedule_override`, `run_timeout`, or `enabled` changed: the
  redbeat schedule entry for this fetcher MUST be updated accordingly
  (see `docs/features/platform/fetcher-infrastructure.md`, "Celery Beat
  Schedule Synchronization — Runtime Propagation" for the full
  propagation mechanism, which fields trigger updates, and failure
  semantics)

**`Capability: manage_fetchers`**

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists |
| 409 | `FETCHER_DEREGISTERED` | Fetcher exists in DB but is not present in the registry (code removed) |
| 422 | `FETCHER_SETTING_UNKNOWN` | Unknown key in `custom_settings` (not declared in the fetcher's schema) |
| 422 | `FETCHER_SETTING_INVALID` | Value in `custom_settings` fails type, range, or choices validation |

### Get Fetcher Audit Log

```
GET /api/v1/fetchers/{fetcher_name}/audit-log
```

Returns the audit trail of admin actions for a fetcher.

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number |
| `per_page` | int | 20 | Items per page (max 100) |
| `event_type` | string (repeatable) | -- | Filter by event type. Multiple values use OR semantics (e.g., `?event_type=disabled&event_type=enabled`). See `docs/api-spec.md` (Enum Filter Validation) for handling of invalid values |
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
      "fetcher_name": "sync_nvd_cves",
      "event_type": "config_changed",
      "actor": {
        "id": "uuid",
        "username": "admin1",
        "full_name": "Alice Smith",
        "active": true
      },
      "old_value": "0 */6 * * *",
      "new_value": "0 */4 * * *",
      "detail": {"field": "schedule_override"},
      "created_at": "2025-04-18T14:31:00Z"
    },
    {
      "id": "uuid",
      "fetcher_name": "sync_nvd_cves",
      "event_type": "disabled",
      "actor": {
        "id": "uuid",
        "username": "admin1",
        "full_name": "Alice Smith",
        "active": true
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

**`Capability: manage_fetchers`**

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 404 | `FETCHER_NOT_FOUND` | No fetcher with this name exists (not in the registry and no `FetcherConfig` record in the database) |

## Access Control

| Action | Required |
|---|---|
| View IBS RabbitMQ consumer status | Public |
| View fetcher list | Public |
| View fetcher detail + charts | Public |
| View run history | Public |
| View error messages | Public |
| View error details | `manage_fetchers` |
| View error tracebacks | `manage_fetchers` |
| Trigger manual run | `manage_fetchers` |
| Enable/disable fetcher | `manage_fetchers` |
| Modify fetcher config | `manage_fetchers` |
| View audit log | `manage_fetchers` |

## Background Tasks

### run_fetcher

Generic Celery task that executes any registered fetcher by name.

| Property | Value |
|---|---|
| Task name | `run_fetcher` |
| Parameters | `fetcher_name` (str), `triggered_by` (str), `user_id` (str, optional), `run_id` (str, optional) |
| Schedule | per-fetcher, from `FetcherConfig.schedule_override` or `BaseFetcher.default_schedule` |
| Idempotency | Only one instance per fetcher can run at a time (database-level `SELECT ... FOR UPDATE` — see `fetcher-infrastructure.md`, Concurrency Control) |

## CLI Commands

The `sentinel fetcher` command group provides read-only diagnostic
access to the fetcher infrastructure from the command line. It is
designed for troubleshooting and quick status checks. All mutations
(trigger, enable/disable, configuration changes) are done exclusively
through the API.

### `sentinel fetcher list`

Lists all fetchers (registered and deregistered) with their current
state.

```
sentinel fetcher list
```

Output (human-readable table to stdout):

```
Name                       Enabled   Last Run              Status                       Settings
sync_nvd_cves              yes       2026-04-27 12:00 UTC  running (1m 30s elapsed)     —
sync_smelt_products        yes       2026-04-26 06:00 UTC  success (45s)                —
detect_ibs_track_releases  no        2026-04-25 02:00 UTC  failure                      —
sync_ibs_requests              yes       2026-04-27 02:30 UTC  success (2m 15s)             —

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
- Shows `—` if the fetcher has no `Settings` model or if all
  settings use their defaults (JSONB is `{}`)

**Status column logic** (applies to both registered and deregistered):

1. If a `FetcherRun` with `status = running` exists for the fetcher:
   show `running ({elapsed} elapsed)` where elapsed is calculated from
   `started_at`. If the elapsed time exceeds `run_timeout + 60` (the
   stale threshold), append `(stale?)` — e.g.,
   `running (1h 2m elapsed, stale?)`. This indicates the process was
   terminated by the hard limit and the orphaned record has not yet
   been cleaned up by stale detection.
2. If no running record exists but completed runs exist: show the status
   of the most recent `FetcherRun` with its duration — e.g.,
   `success (3m 12s)`, `failure`, `partial (1m 5s)`
3. If no `FetcherRun` records exist: show `never run`

**Enabled column** (registered fetchers only): reads from
`FetcherConfig.enabled`. If no `FetcherConfig` record exists for the
fetcher, defaults to `yes`.

**Data source**: queries the database directly. The fetcher registry
provides the list of registered fetcher names; `FetcherConfig` rows
whose `fetcher_name` is not in the registry provide deregistered
fetchers. The database provides `FetcherRun` and
`FetcherConfig` data for both. This command's async workflow may execute these
specified reads directly under the narrowly scoped read-only CLI exception in
`docs/architecture.md`; it performs no mutation or lifecycle business logic.

**Idempotency**: Idempotent. Read-only command; safe to re-run at any
time.

**Exit codes**: 0 on success, 2 on system error (database unreachable).

**Output channels**: table to stdout. `"Error: ..."` messages to stderr.

### `sentinel fetcher config <name>`

Displays the full configuration of a fetcher, including custom settings
with their current values, defaults, and descriptions.

```
sentinel fetcher config sync_redhat_cves
```

Output (to stdout):

```
Fetcher: sync_redhat_cves
Enabled: yes
Schedule: 0 3 * * * (default)
Timeout: 3600s
Request delay: 0s

Custom settings:
  results_per_page = 500  (default: 2000, range: 100–2000)
    Number of CVE records per API page.
```

For a fetcher with no custom settings schema:

```
Fetcher: sync_nvd_cves
Enabled: yes
Schedule: 0 */6 * * * (default)
Timeout: 3600s
Request delay: 0s

No custom settings available for this fetcher.
```

For a deregistered fetcher (present in DB but not in the registry):

```
Fetcher: old_fetcher (deregistered)
Enabled: yes
Schedule override: 0 */6 * * *
Timeout: 3600s
Request delay: 0s

Custom settings (schema unavailable — raw stored values):
  results_per_page = 500
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
- `range` is shown only for `int`/`float` with `ge`/`le` constraints
- `choices` are shown as `choices: a, b, c` for fields with choices

**Data source**: queries `FetcherConfig` from the database. For
registered fetchers, also reads the `Settings` model from the
fetcher registry. For deregistered fetchers, only DB-stored data is
available. This command uses the same read-only CLI exception defined for
`fetcher list` above.

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
on `FetcherRun`.

## Dependencies

- Click (CLI framework) — see `docs/conventions.md` for CLI conventions

## Open Questions

None at this time.

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
- `docs/features/platform/audit-trail-infrastructure.md` — shared audit
  trail query builder (`build_audit_query`, `actor` filter resolution)
- `docs/features/tickets/cve-service.md` — Global CVE Source Listing
  endpoint (failure drill-down from fetcher runs)
