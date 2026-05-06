# Administration

## Purpose

System-wide configuration and administrative operations for the Sentinel
platform. The Admin panel provides settings that affect platform behavior
across all users and tickets.

## Access Control

All administration endpoints and UI pages require the **Admin** role.

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
   Analysis, Analyzed; `deleted_at IS NULL` — see `docs/data-model.md`)
   using the new default version's resolution cascade
2. Re-evaluate product eligibility for all active tickets using the new
   default version's score
3. Apply the same recalculation cascade as a CVSS score change (see
   `docs/features/cvss-scoring.md`, Recalculation Cascade)
4. Create `TicketEvent` records for every severity or eligibility change

This operation may take time for a large number of active tickets. It
is executed as a background task (Celery). The task reuses the same
`ticket_mutations` functions used for individual CVSS changes — each
ticket is processed in an independent database transaction. See
`docs/features/cvss-scoring.md` (Cascade Execution Model) for the
full batch execution specification.

**Warning**: changing the default CVSS version is a significant operation.
The Admin UI should display a confirmation dialog explaining the impact
before proceeding.

## API Endpoints

All endpoints in this section require the Admin role. Global responses
(401, 422) apply per `api-spec.md` "Global Responses" section. 403
(`AUTH_INSUFFICIENT_ROLE`) is returned for authenticated users without
Admin role.

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

Requires: Admin role.

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
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Admin role |
| 422 | `VALIDATION_ERROR` | Invalid setting value (e.g., unsupported CVSS version) |

Response: the updated settings object in the standard `{"data": ...}`
envelope.

Requires: Admin role.

## UI

### Admin Settings Page

**Route**: `/admin/settings`

A simple settings page containing:

1. **Default CVSS Version**: dropdown with display labels `"v3.1"` and
   `"v4.0"` (the API values sent in the PATCH request are `"3.1"` and
   `"4.0"` without the "v" prefix), showing the current value. On change,
   a confirmation dialog:
   - "Changing the default CVSS version will re-evaluate all products on
     currently open tickets. Continue?"
   - Confirm → PATCH API call → show success feedback
   - Cancel → no change

Future administrative settings will be added to this page as needed.

## Data Model

System settings are stored in a key-value configuration table. See
`docs/data-model.md` for the schema.
