# Administration

## Purpose

System-wide configuration and administrative operations for the STAMP
platform. The Admin panel provides settings that affect platform behavior
across all users and tickets.

## Access Control

All administration endpoints and UI pages require the **Admin** role.

## Settings

### Default CVSS Version

Controls which CVSS version STAMP uses for all automated decisions. This
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

When the Admin changes the default CVSS version, STAMP MUST:

1. Recalculate severity for **all CVEs with active tickets** (status: New,
   Analysis, Analyzed) using the new default version's resolution cascade
2. Re-evaluate product eligibility for all active tickets using the new
   default version's score
3. Apply the same recalculation cascade as a CVSS score change (see
   `docs/features/cvss-scoring.md`, Recalculation Cascade)
4. Create `TicketEvent` records for every severity or eligibility change

This operation may take time for a large number of active tickets. It
should be executed as a background task (Celery) with progress feedback
to the Admin.

**Warning**: changing the default CVSS version is a significant operation.
The Admin UI should display a confirmation dialog explaining the impact
before proceeding.

## API Endpoints

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

Response: the updated settings object, plus a task status indicator if
recalculation is in progress.

Requires: Admin role.

## UI

### Admin Settings Page

**Route**: `/admin/settings`

A simple settings page containing:

1. **Default CVSS Version**: dropdown with options `"v3.1"` and `"v4.0"`,
   showing the current value. On change, a confirmation dialog:
   - "Changing the default CVSS version will recalculate severity and
     product eligibility for all active tickets. This operation may take
     several minutes. Continue?"
   - Confirm → PATCH API call → show progress/success feedback
   - Cancel → no change

Future administrative settings will be added to this page as needed.

## Data Model

System settings are stored in a key-value configuration table. See
`docs/data-model.md` for the schema.
