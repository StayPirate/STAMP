# Ticket Audit Log

## Purpose

Provide a complete, searchable audit trail for every ticket in Sentinel. Every
modification to a ticket or its related data (status, assignee, duplicate
links, packages, tracks, products) MUST produce a `TicketAuditEvent` record.
Users can browse, filter, and search the history through a dedicated "History"
tab on the Ticket Detail page (see
`docs/features/ui/pages/ticket-detail.md` for the UI specification).

The `TicketAuditLog` subclass of `BaseAuditLog` provides the event creation
helper and registers this audit trail in the global registry. See
`docs/features/platform/audit-trail-infrastructure.md` for the base class
contract.

## Data Model

The `TicketAuditEvent` table and `TicketAuditEventType` enum are defined in
`docs/data-model.md`. This specification defines the **contract** for how
each event type must be populated.

### Event Type Contract

Every service that mutates a ticket MUST create a `TicketAuditEvent` with the
fields populated according to this table:

| `event_type` | Trigger | `user_id` | `old_value` | `new_value` | `comment` |
|---|---|---|---|---|---|
| `status_change` | Ticket status transitions (manual or system-initiated) | VA user for manual, `NULL` for system (e.g., NVD rejection, CVSS recalculation) | Previous status (e.g., `New`) | New status (e.g., `Analysis`) | Optional VA note for manual; system-generated description for automatic (e.g., `"CVE rejected by NVD"`) |
| `assignment` | Ticket assigned or reassigned | VA user for manual, `NULL` for system (e.g., employee deactivation) | Previous assignee username or `NULL` | New assignee username or `NULL` (unassigned) | Optional VA note for manual; system-generated description for automatic (e.g., `"Unassigned from {old}: employee deactivated"`) |
| `duplicate_set` | Ticket marked as duplicate | VA user | `NULL` | `SNTL-{n}` identifier of the original ticket | Optional VA note |
| `duplicate_removed` | Duplicate mark reverted | VA user | `SNTL-{n}` identifier of the original ticket | `NULL` | Optional VA note |
| `duplicate_target_changed` | Cascade update: the original ticket was itself marked as duplicate, so this ticket's `duplicate_of_id` was re-pointed to the ultimate original | `NULL` | `SNTL-{n}` identifier of the previous original | `SNTL-{n}` identifier of the new original | `NULL` |
| `package_added` | Package added to ticket (manual or automatic) | VA user for manual, `NULL` for automatic | `NULL` | Package name | `NULL` for manual; contextual description for automatic (e.g., `"CPE match"`, `"Detected in track SUSE:SLE-15-SP6:Update"`) |
| `package_excluded` | Package directly soft-deleted (excluded) from ticket. One event per action — child tracks and products are not modified and do not generate events (they become effectively excluded via the hierarchy) | VA user for manual, `NULL` for system (orphan cleanup) | Package name | `NULL` | Optional VA note for manual; `package_name:reason` for automatic (e.g., `"openssl:no_tracks_remaining"`) |
| `package_restored` | Directly excluded package restored to ticket. Only the package record is restored — child records are not modified | VA user | `NULL` | Package name | Optional VA note |
| `track_status_changed` | Track status changed (VA action or release detection) | VA user for manual changes, `NULL` for automatic transitions (e.g., release detected sets FIXED) | Old status | New status | `package_name:track_name` |
| `product_status_overridden` | VA overrides product status | VA user | Old status | New status | `package_name:product_id` |
| `track_released` | Track release detected | `NULL` | `NULL` | `RELEASED` | `package_name:track_name` |
| `product_released` | Product release detected via updateinfo.xml | `NULL` | `NULL` | `RELEASED` | `package_name:product_id:advisory_id` |
| `ticket_created` | Ticket created (CVE ingestion, track detection, or manual) | `NULL` for automatic creation, creating user for manual creation | `NULL` | `NULL` | Creation source description (e.g., `"CVE ingested from NVD"`, `"CVE fix detected in openssl (SUSE:SLE-15-SP6:Update)"`, `"Ticket created manually"`) |
| `cve_associated` | CVE associated with a ticket that previously had no CVE | VA user | `NULL` | CVE-ID string (e.g., `"CVE-2024-1234"`) | `NULL` |
| `cve_removed` | Admin removed CVE association from a ticket | Admin user | CVE-ID string (e.g., `"CVE-2024-1234"`) | `NULL` | Optional admin note |
| `severity_changed` | CVSS recalculation changes ticket severity | `NULL` | Old severity (e.g., `High`) | New severity (e.g., `Critical`) | `NULL` |
| `cvss_assessment_changed` | CVSS assessment added, modified, or removed | VA user for SUSE changes, `NULL` for external sync | Previous `"provider_name vX.Y score"` or `NULL` if new | Current `"provider_name vX.Y score"` or `NULL` if removed | `NULL` |
| `product_eligibility_changed` | Product eligibility changed due to CVSS recalculation, lifecycle phase transition (Reactive LTSS), threshold change, or VA override | VA user for VA overrides, `NULL` for system-triggered changes | Old eligibility (`true` or `false`) | New eligibility (`true` or `false`) | `package_name:product_id:reason` (reason: `reactive_ltss`, `threshold`, `cvss`, `va_override`) |
| `track_excluded` | Track directly soft-deleted (excluded) from ticket. One event per action — child products are not modified and do not generate events (they become effectively excluded via the hierarchy) | VA user for manual, `NULL` for system (orphan cleanup) | Track name | `NULL` | `package_name:reason` |
| `track_restored` | Directly excluded track restored to ticket. Only the track record is restored — child products are not modified | VA user | `NULL` | Track name | Optional VA note |
| `product_excluded` | Product directly soft-deleted (excluded) from ticket | VA user for manual, `NULL` for system (EOL, orphan cleanup) | Product display name | `NULL` | `package_name:product_id:reason` |
| `product_restored` | Directly excluded product restored to ticket | VA user | `NULL` | Product display name | Optional VA note |
| `ticket_deleted` | Admin soft-deletes a ticket | Admin user | `NULL` | `NULL` | Optional admin note |
| `ticket_restored` | Admin restores a soft-deleted ticket | Admin user | `NULL` | `NULL` | Optional admin note |

**Rules**:

- `user_id` MUST be set for user-initiated actions and `NULL` for system
  actions. This distinction enables the actor filter in the UI.
- `old_value` and `new_value` store human-readable strings. For enum values,
  store the enum name (e.g., `AFFECTED`, `NOT_AFFECTED`). For user
  references, store the username.
- `comment` is used for optional VA notes on user actions, and for structured
  context on system actions (colon-separated identifiers as shown above).
- All events include an implicit `created_at` timestamp set by the database
  default.

## API

### List Ticket Events

```
GET /api/v1/tickets/{ticket_id}/audit-log
```

Returns a paginated list of events for a specific ticket, ordered by
`created_at` descending (newest first). Sorting is fixed —
client-controlled `sort_by` / `sort_order` parameters are not supported
(timeline display requires chronological ordering).

**Path parameters**:

| Parameter   | Type | Description          |
|-------------|------|----------------------|
| `ticket_id` | UUID or `SNTL-{n}` | The ticket identifier (supports dual lookup) |

**Query parameters**:

| Parameter    | Type   | Default | Description |
|--------------|--------|---------|-------------|
| `page`       | int    | 1       | Page number (1-indexed) |
| `per_page`   | int    | 20      | Items per page (max 100) |
| `event_type` | string | —       | Comma-separated list of event types to include (e.g., `status_change,assignment`). If omitted, all types are returned. |
| `actor`      | string | —       | Filter by actor: user UUID, username, or `system` for automated events (where `user_id IS NULL`). If omitted, all actors are returned. |
| `search`     | string | —       | Case-insensitive substring search on the `comment` field. If omitted, no text filtering is applied. |
| `from_date`  | string | —       | ISO 8601 date/datetime. Include events from this date onwards (inclusive) |
| `to_date`    | string | —       | ISO 8601 date/datetime. Include events up to this date (inclusive) |

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "ticket_id": "uuid",
      "event_type": "status_change",
      "old_value": "New",
      "new_value": "Analysis",
      "comment": null,
      "created_at": "2025-03-15T10:30:00Z",
      "actor": {
        "id": "uuid",
        "username": "jdoe",
        "full_name": "John Doe"
      }
    },
    {
      "id": "uuid",
      "ticket_id": "uuid",
      "event_type": "track_released",
      "old_value": null,
      "new_value": "RELEASED",
      "comment": "openssl:SUSE:SLE-15-SP6:Update",
      "created_at": "2025-03-14T08:00:00Z",
      "actor": null
    }
  ],
  "meta": {
    "total": 42,
    "page": 1,
    "per_page": 20
  }
}
```

**Notes**:

- `actor` is `null` for system-generated events (where `user_id IS NULL`).
- `actor` contains `id`, `username`, and `full_name` for user-initiated
  events.
- The `event_type` filter accepts multiple values separated by commas. See
  `docs/api-spec.md` (Enum Filter Validation) for handling of invalid values.
- The `search` filter performs a case-insensitive `ILIKE '%term%'` on the
  `comment` column.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found |
| 410    | `TICKET_DELETED` | Ticket is soft-deleted and the caller is not an Admin (see `docs/api-spec.md`, soft-delete protection on sub-resources) |

**Permissions**: publicly accessible for active tickets (no authentication
required). If the ticket is soft-deleted, only Admin users can access its
event history; non-admin callers receive 410 Gone.

## Service Contract

Every service function that modifies a ticket MUST create a `TicketAuditEvent` as
part of the same database transaction. This ensures atomicity — if the
mutation succeeds, the event is guaranteed to be recorded; if the mutation
fails, no orphan event is created.

### Implementation Guidelines

1. **Same transaction**: the `TicketAuditEvent` insert MUST happen in the same
   database session/transaction as the ticket mutation. Do NOT create events
   in a separate transaction or after committing the main change.

2. **Service layer responsibility**: event creation belongs in the service
   layer (`app/services/`), not in the API layer or model layer. The service
   function that performs the mutation also creates the event.

3. **Event creation**: services MUST use `TicketAuditLog.log_event()` to
   create events, ensuring consistent field population and registration
   in the global audit trail registry.

4. **No silent mutations**: if a service modifies ticket data without
   creating a `TicketAuditEvent`, it is a bug. See Guardrail 11 in `AGENTS.md`.

## Testing Requirements

Tests for any ticket-mutating service MUST verify:

1. A `TicketAuditEvent` record is created after the operation
2. The `event_type` matches the expected value
3. `old_value` and `new_value` are correctly populated
4. `user_id` is set for user actions and `NULL` for system actions
5. The event is created in the same transaction (i.e., if the operation is
   rolled back, no event exists)

See Guardrail 6 (Mandatory testing) and Guardrail 11 (Ticket event logging)
in `AGENTS.md` for enforcement.

## Data Retention

Indefinite. TicketAuditEvent records are never automatically deleted.

## Cross-references

- `docs/features/platform/audit-trail-infrastructure.md` — BaseAuditLog,
  AuditEventMixin, naming conventions
- `docs/conventions.md` — Audit Trail Conventions
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
