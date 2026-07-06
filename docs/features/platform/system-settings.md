# System Settings

## Purpose

System-wide configuration and administrative operations for the Sentinel
platform. The System Settings page provides settings that affect platform behavior
across all users and tickets.

## Access Control

All administration endpoints and UI pages require the `manage_settings`
capability.

## Settings

### Default CVSS Version

Controls which CVSS version Sentinel uses for all automated decisions. This
setting affects the entire platform — severity derivation, eligibility
threshold comparison, and any future logic that depends on a CVSS score.

| Property        | Value                            |
|-----------------|----------------------------------|
| Setting key     | `default_cvss_version`           |
| Type            | String                           |
| Allowed values  | `"3.1"`, `"4.0"`                 |
| Initial value   | `"3.1"`                          |
| Changed by      | Admin only                       |

**Impact of changing the default version**:

When the Admin changes the default CVSS version, the PATCH endpoint
executes the following sequence:

1. **Validate** the new value against allowed values (`"3.1"`, `"4.0"`)
2. **No-op check**: if the current value equals the new value, return
   200 immediately with `recalculation_scheduled: false` (no audit
   event, no batch — consistent with the audit-trail-infrastructure
   cross-cutting rule for idempotent no-ops)
3. **Acquire recalculation slot**: `SET cvss_recalc_active <timestamp>
   NX EX 900` on Redis. This step serves as both a Redis liveness probe
   and a flip-flop guard:
   - If Redis is unreachable → return 503 `REDIS_UNAVAILABLE` (nothing
     committed)
   - If the key already exists (a recalculation is in progress) → return
     409 `RECALC_ALREADY_IN_PROGRESS` (nothing committed)
4. **Commit** the new setting value and a `SettingAuditEvent` record to
   the database. If the commit fails: release the slot (`DEL
   cvss_recalc_active`) and return 500
5. **Enqueue** the batch recalculation Celery task
   (`recalc_active_tickets`) with the new version as an explicit
   argument. If the enqueue fails: release the slot and return 200 with
   `recalculation_scheduled: false` (the primary operation — the setting
   change — succeeded; the admin can use the manual re-run endpoint to
   trigger the batch)
6. Return 200 OK with `recalculation_scheduled: true`

**Commit-first rationale**: the `SettingAuditEvent` is always the first
durable record. No ticket mutation can occur without the setting change
being audited. This prevents phantom mutations (ticket audit events
without a recorded cause).

The batch task (`recalc_active_tickets`) iterates all active tickets
with a CVE (status: New, Analysis, Analyzed; `cve_id IS NOT NULL`) and
calls `ticket_mutations.recalculate_cvss_chain()` for each ticket in an
independent database transaction. Failures on individual tickets are
logged and skipped. On completion (or failure), the task releases the
slot (`DEL cvss_recalc_active`) and logs metrics (total, succeeded,
failed). The task has a hard timeout (`time_limit=900`) matching the
slot TTL — this ensures the task is terminated before its slot can
expire, preventing concurrent batches with conflicting versions.

The slot key has a fixed TTL of 900 seconds (internal constant). In
normal operation the task completes in seconds/minutes and releases the
slot immediately. The TTL serves only as a crash-recovery safety net: if
the worker dies without releasing the slot, the key self-expires and the
admin can retry.

See `docs/features/tickets/cvss-scoring.md` (Chain Execution Model) for
additional details on the batch task behavior.

## Bootstrap

The `default_cvss_version` setting (declared above with Initial value
`"3.1"`) MUST exist at runtime before any process reads it. The system
guarantees existence via two complementary mechanisms:

1. **Alembic data migration** (primary — runs before any process starts):

   ```sql
   INSERT INTO system_settings (key, value)
   VALUES ('default_cvss_version', '3.1')
   ON CONFLICT (key) DO NOTHING;
   ```

2. **FastAPI lifespan event** (defense-in-depth, self-healing):

   ```sql
   INSERT INTO system_settings (key, value)
   VALUES ('default_cvss_version', '3.1')
   ON CONFLICT (key) DO NOTHING;
   ```

Properties:

- **Idempotent**: if the setting already exists (e.g., Admin changed it
  to `"4.0"`), the INSERT is a no-op
- **Self-healing**: if the row is accidentally deleted, the next
  application restart restores the default
- **Multi-replica safe**: `ON CONFLICT DO NOTHING` handles concurrent
  startup of multiple API server instances without race conditions
- **Process-order independent**: the Alembic migration guarantees the
  setting exists before any process (API server, Celery worker, RabbitMQ
  consumer) starts. The FastAPI lifespan seed is redundant but harmless

**Failure behavior invariant**: `get_default_cvss_version()` raises if
the setting is absent — this indicates a deployment or data integrity
error, not a recoverable condition. No hardcoded fallback is provided.
A missing setting means migrations have not been applied correctly.

## API Endpoints

All endpoints in this section require the `manage_settings` capability.
Global responses per `api-spec.md` apply to all endpoints in this section.

### Get System Settings

```
GET /api/v1/admin/settings
```

Response:

```json
{
  "data": {
    "default_cvss_version": "3.1"
  }
}
```

**`Capability: manage_settings`**

### Update System Settings

```
PATCH /api/v1/admin/settings
```

Request body:

```json
{
  "default_cvss_version": "4.0"
}
```

Validates the value against allowed values. On a value change, acquires
the recalculation slot, commits the setting and audit event, and
enqueues a batch recalculation task. See "Impact of changing the default
version" above for the full sequence.

**Note on PATCH with side effects**: this endpoint uses PATCH because
semantically it is a configuration field update — the setting changes
value and the response is returned immediately. The recalculation is an
asynchronous side effect (Celery background task) that does not block
the response. This is a documented deviation from the
`POST /resource/{id}/verb` convention for operations with side effects.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `RECALC_ALREADY_IN_PROGRESS` | A recalculation batch is already running (setting change blocked until current batch completes) |
| 503 | `REDIS_UNAVAILABLE` | Redis broker is unreachable (setting change requires broker availability) |

Response (200 OK): the settings object in the standard
`{"data": ...}` envelope. The `recalculation_scheduled` boolean field
is **always present** in the response:

```json
{
  "data": {
    "default_cvss_version": "4.0",
    "recalculation_scheduled": true
  }
}
```

Values of `recalculation_scheduled`:

- `true` — value changed and batch task successfully enqueued
- `false` — either (a) no-op (value unchanged, no batch needed), or
  (b) value changed but enqueue failed (transient broker failure after
  slot acquisition — admin should use
  `POST /api/v1/admin/settings/default-cvss-version/recalculate` to
  trigger the batch manually)

**`Capability: manage_settings`**

### Trigger CVSS Recalculation

```
POST /api/v1/admin/settings/default-cvss-version/recalculate
```

Manually triggers a CVSS recalculation batch for all active tickets
with a CVE, using the current `default_cvss_version` value. Used for
recovery after partial batch failures or as a general refresh mechanism.

The endpoint uses the same shared logic as the PATCH side-effect:

1. Read the current `default_cvss_version` from the database
2. Acquire the recalculation slot (`SET cvss_recalc_active <timestamp>
   NX EX 900`)
3. Enqueue `recalc_active_tickets(version)`. On failure: release slot
   and return 503
4. Return 202 Accepted

No setting change is made. No `SettingAuditEvent` is created.

**Request body**: none.

**Response** (202 Accepted):

```json
{
  "data": {
    "message": "Recalculation batch enqueued",
    "default_cvss_version": "4.0",
    "scope": "active_tickets_with_cve"
  }
}
```

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `RECALC_ALREADY_IN_PROGRESS` | A recalculation batch is already running (slot occupied) |
| 503 | `REDIS_UNAVAILABLE` | Redis is unreachable (slot acquisition failed) |
| 503 | `CELERY_ENQUEUE_FAILED` | Task could not be enqueued (slot released) |

**Idempotency**: safe to call multiple times. If no derived values have
changed since the last run, the batch produces no mutations or audit
events (guaranteed by `recalculate_cvss_chain()` idempotency).

**`Capability: manage_settings`**

## Data Model

System settings are stored in a key-value configuration table. See
`docs/data-model.md` for the schema.

## Setting Audit Log

Every modification to a system setting MUST produce a
`SettingAuditEvent` record in the same database transaction as the
setting update.

### SettingAuditEvent Table

See `docs/data-model.md` for the full table definition. Key columns:

| Column | Type | Description |
|---|---|---|
| event_type | ENUM | `SettingAuditEventType` — currently only `setting_changed` |
| setting_key | VARCHAR(100) | Which setting was changed (e.g., `default_cvss_version`) |
| user_id | UUID | Admin who changed the setting (always present — no system-initiated changes) |
| old_value | TEXT | Previous value |
| new_value | TEXT | New value |

### List Settings Audit Events

```
GET /api/v1/admin/settings/audit-log
```

Returns a paginated list of setting changes, ordered by `created_at`
descending. Sorting is fixed — client-controlled `sort_by` /
`sort_order` parameters are not supported (audit trail entries are
always displayed in reverse chronological order).

**Query parameters**:

| Parameter | Type | Default | Description |
|---|---|---|---|
| `page` | int | 1 | Page number (1-indexed) |
| `per_page` | int | 20 | Items per page (max 100) |
| `event_type` | string | -- | Comma-separated list of event types (currently only `setting_changed`) |
| `setting_key` | string | -- | Filter by setting key |
| `actor` | string | -- | Filter by actor: user UUID or username. `system` is accepted but will return no results (all setting changes are user-initiated) |
| `from_date` | string | -- | ISO 8601 date/datetime. Include events from this date onwards (inclusive) |
| `to_date` | string | -- | ISO 8601 date/datetime. Include events up to this date (inclusive) |

**`Capability: manage_settings`**

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "event_type": "setting_changed",
      "setting_key": "default_cvss_version",
      "old_value": "3.1",
      "new_value": "4.0",
      "created_at": "2026-05-13T14:00:00Z",
      "actor": {
        "id": "uuid",
        "username": "asmith",
        "full_name": "Alice Smith",
        "active": true
      }
    }
  ],
  "meta": {
    "total": 3,
    "page": 1,
    "per_page": 20
  }
}
```

### Data Retention

Indefinite. SettingAuditEvent records are never automatically deleted.

## Cross-references

- `docs/features/platform/audit-trail-infrastructure.md` — BaseAuditLog,
  AuditEventMixin
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
- `docs/features/identity/rbac.md` — Endpoint Permission Map
