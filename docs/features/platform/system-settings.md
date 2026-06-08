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

When the Admin changes the default CVSS version, Sentinel MUST:

1. Recalculate severity for **all CVEs with active tickets** (status: New,
   Analysis, Analyzed — see `docs/data-model.md`) using
   `resolve_severity_score` (5-step severity cascade, multi-provider)
2. Re-evaluate product eligibility for all active tickets using
   `resolve_eligibility_score` (2-step SUSE-only cascade)
3. Apply the same recalculation cascade as a CVSS score change (see
   `docs/features/tickets/cvss-scoring.md`, Recalculation Cascade)
4. Create `TicketAuditEvent` records for every severity or eligibility change

This operation may take time for a large number of active tickets. It
is executed as a background task (Celery). The task calls
`ticket_mutations.recalculate_cvss_cascade()` for each ticket in an
independent database transaction. When the default CVSS version changes,
the batch recalculation task uses a singleton Redis lock to serialize
concurrent executions. See `docs/features/tickets/cvss-scoring.md`
(Cascade Execution Model) for details.

**Warning**: changing the default CVSS version is a significant operation.

## API Endpoints

All endpoints in this section require the `manage_settings` capability.
Global responses
(401, 422) apply per `api-spec.md` "Global Responses" section. 403
(`AUTH_INSUFFICIENT_PERMISSION`) is returned for authenticated users
without the required capability.

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

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |

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

Validates the value against allowed values. Triggers recalculation for all
active tickets as a background task.

**Note on PATCH with side effects**: this endpoint uses PATCH because
semantically it is a configuration field update — the setting changes value
and the response is returned immediately. The recalculation cascade is an
asynchronous side effect (Celery background task) that does not block the
response. The client experience is that of a simple field update with
instant confirmation. This is a documented deviation from the
`POST /resource/{id}/verb` convention for operations with side effects.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |
| 422 | `VALIDATION_ERROR` | Invalid setting value (e.g., unsupported CVSS version) |

Response: the updated settings object in the standard `{"data": ...}`
envelope.

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

### API

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

**Error responses**:

| Status | Code | Condition |
|---|---|---|
| 403 | `AUTH_INSUFFICIENT_PERMISSION` | Caller does not have required capability |

### Data Retention

Indefinite. SettingAuditEvent records are never automatically deleted.

## Cross-references

- `docs/features/platform/audit-trail-infrastructure.md` — BaseAuditLog,
  AuditEventMixin
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
- `docs/features/identity/rbac.md` — Endpoint Permission Map
