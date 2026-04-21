# Ticket History

## Purpose

Provide a complete, searchable audit trail for every ticket in STAMP. Every
modification to a ticket or its related data (status, assignee, duplicate
links, packages, codestreams, products) MUST produce a `TicketEvent` record.
Users can browse, filter, and search the history through a dedicated "History"
tab on the Ticket Detail page.

## Data Model

The `TicketEvent` table and `TicketEventType` enum are defined in
`docs/data-model.md`. This specification defines the **contract** for how
each event type must be populated.

### Event Type Contract

Every service that mutates a ticket MUST create a `TicketEvent` with the
fields populated according to this table:

| `event_type` | Trigger | `user_id` | `old_value` | `new_value` | `comment` |
|---|---|---|---|---|---|
| `status_change` | Ticket status transitions (manual or system-initiated) | IM user for manual, `NULL` for system (e.g., NVD rejection, CVSS recalculation) | Previous status (e.g., `New`) | New status (e.g., `Analysis`) | Optional IM note for manual; system-generated description for automatic (e.g., `"CVE rejected by NVD"`) |
| `assignment` | Ticket assigned or reassigned | IM user | Previous assignee username or `NULL` | New assignee username | Optional IM note |
| `duplicate_set` | Ticket marked as duplicate | IM user | `NULL` | CVE ID of the original ticket | Optional IM note |
| `duplicate_removed` | Duplicate mark reverted | IM user | CVE ID of the original ticket | `NULL` | Optional IM note |
| `package_added` | Package added to ticket (manual or automatic) | IM user for manual, `NULL` for automatic | `NULL` | Package name | `NULL` for manual; contextual description for automatic (e.g., `"CPE match"`, `"Detected in codestream SUSE:SLE-15-SP6:Update"`) |
| `package_removed` | IM removes package from ticket | IM user | Package name | `NULL` | `NULL` |
| `codestream_status_changed` | IM changes codestream status | IM user | Old status | New status | `package_name:codestream_name` |
| `product_status_overridden` | IM overrides product status | IM user | Old status | New status | `package_name:product_id` |
| `codestream_released` | CodestreamReleaseDetector (Case A) | `NULL` | `NULL` | `RELEASED` | `package_name:codestream_name` |
| `product_released` | Product release detected via updateinfo.xml | `NULL` | `NULL` | `RELEASED` | `package_name:product_id:advisory_id` |
| `ticket_created` | Ticket created (CVE ingestion or codestream detection) | `NULL` | `NULL` | `NULL` | Creation source description (e.g., `"CVE ingested from NVD"`, `"CVE fix detected in openssl (SUSE:SLE-15-SP6:Update)"`) |
| `severity_changed` | CVSS recalculation changes ticket severity | `NULL` | Old severity (e.g., `High`) | New severity (e.g., `Critical`) | `NULL` |
| `cvss_assessment_changed` | CVSS assessment added, modified, or removed | IM user for SUSE changes, `NULL` for external sync | Previous `"provider_name vX.Y score"` or `NULL` if new | Current `"provider_name vX.Y score"` or `NULL` if removed | `NULL` |
| `product_eligibility_changed` | Product eligibility changed due to CVSS recalculation | `NULL` | Old status | New status | `package_name:product_id` |

**Rules**:

- `user_id` MUST be set for user-initiated actions and `NULL` for system
  actions. This distinction enables the actor filter in the UI.
- `old_value` and `new_value` store human-readable strings. For enum values,
  store the enum name (e.g., `AFFECTED`, `NOT_AFFECTED`). For user
  references, store the username.
- `comment` is used for optional IM notes on user actions, and for structured
  context on system actions (colon-separated identifiers as shown above).
- All events include an implicit `created_at` timestamp set by the database
  default.

## API

### List Ticket Events

```
GET /api/v1/tickets/{ticket_id}/events
```

Returns a paginated list of events for a specific ticket, ordered by
`created_at` descending (newest first).

**Path parameters**:

| Parameter   | Type | Description          |
|-------------|------|----------------------|
| `ticket_id` | UUID | The ticket identifier |

**Query parameters**:

| Parameter    | Type   | Default | Description |
|--------------|--------|---------|-------------|
| `page`       | int    | 1       | Page number (1-indexed) |
| `per_page`   | int    | 20      | Items per page (max 100) |
| `event_type` | string | —       | Comma-separated list of event types to include (e.g., `status_change,assignment`). If omitted, all types are returned. |
| `actor`      | string | —       | Filter by actor. Accepts a user UUID to filter by a specific user, or the literal value `system` to show only automated events (where `user_id IS NULL`). If omitted, all actors are returned. |
| `search`     | string | —       | Case-insensitive substring search on the `comment` field. If omitted, no text filtering is applied. |

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
      "event_type": "codestream_released",
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
- The `event_type` filter accepts multiple values separated by commas. Invalid
  values are ignored (no error). If all provided values are invalid, the
  result is an empty list.
- The `search` filter performs a case-insensitive `ILIKE '%term%'` on the
  `comment` column.

**Error responses**:

| Status | Condition |
|--------|-----------|
| 404    | Ticket not found |

**Permissions**: publicly accessible (no authentication required). The
event history is part of the ticket data, which is public.

## Frontend

### History Tab

The Ticket Detail page (`/tickets/:id`) includes a **"History"** tab that
displays the ticket event timeline. This tab is separate from the main ticket
information and package data tabs.

#### Filter Bar

At the top of the History tab, a horizontal filter bar provides:

1. **Event type filter**: multi-select dropdown with checkboxes. Options are
   derived from the `TicketEventType` enum, displayed with human-readable
   labels:

   | Enum value                 | Display label              |
   |----------------------------|----------------------------|
   | `status_change`            | Status change              |
   | `assignment`               | Assignment                 |
   | `duplicate_set`            | Duplicate set              |
   | `duplicate_removed`        | Duplicate removed          |
   | `package_added`            | Package added              |
   | `package_removed`          | Package removed            |
   | `codestream_status_changed`| Codestream status changed  |
   | `product_status_overridden`| Product status overridden  |
   | `codestream_released`      | Codestream released        |
   | `product_released`         | Product released           |
   | `ticket_created`           | Ticket created             |
   | `severity_changed`         | Severity changed           |
   | `cvss_assessment_changed`  | CVSS assessment changed    |
   | `product_eligibility_changed` | Product eligibility changed |

2. **Actor filter**: single-select dropdown with options:
   - "All" (default — no filter applied)
   - "System" (shows only automated events)
   - Individual users who appear in the ticket's event history (populated
     dynamically from the event data currently loaded)

3. **Text search**: input field with placeholder "Search in comments...".
   Debounced (300ms delay) to avoid excessive API calls. Triggers a new API
   request with the `search` query parameter.

All filters are applied via query parameters on the API request. Changing any
filter resets the pagination to page 1.

A "Clear filters" button appears when any filter is active, allowing the user
to reset all filters at once.

#### Event Timeline

Below the filter bar, events are rendered as a vertical timeline (newest
first). Each event entry displays:

1. **Icon**: a small icon indicating the event category. The mapping from
   event type to icon category is:

   | Icon category | Icon | Event types |
   |---|---|---|
   | Status change | arrow-right-left | `status_change` |
   | Assignment | user | `assignment` |
   | Duplicate | copy | `duplicate_set`, `duplicate_removed` |
   | Creation | plus-circle | `ticket_created` |
   | Package | package | `package_added`, `package_removed` |
   | Affectedness | shield | `codestream_status_changed`, `product_status_overridden`, `product_eligibility_changed` |
   | Release | check-circle | `codestream_released`, `product_released` |
   | CVSS | gauge | `severity_changed`, `cvss_assessment_changed` |

2. **Timestamp**: relative time (e.g., "2 hours ago", "3 days ago") with a
   tooltip showing the absolute datetime in the user's locale.

3. **Actor**: the username of the user who performed the action, rendered as
   a badge. For system events, display "System" with a distinct visual style
   (e.g., muted color or bot icon).

4. **Description**: a human-readable sentence describing the event. Template
   for each event type:

   | Event type | Description template |
   |---|---|
   | `status_change` | Changed status from **{old_value}** to **{new_value}** |
   | `assignment` | Assigned to **{new_value}** (if `old_value` is null: "Assigned to **{new_value}**"; if reassignment: "Reassigned from **{old_value}** to **{new_value}**") |
   | `duplicate_set` | Marked as duplicate of **{new_value}** |
   | `duplicate_removed` | Duplicate mark removed (was duplicate of **{old_value}**) |
   | `package_added` | Added package **{new_value}** (if `comment` present: "Added package **{new_value}** — **{comment}**") |
   | `package_removed` | Removed package **{old_value}** |
   | `codestream_status_changed` | Changed codestream status from **{old_value}** to **{new_value}** for **{comment}** |
   | `product_status_overridden` | Overrode product status from **{old_value}** to **{new_value}** for **{comment}** |
   | `codestream_released` | Codestream release detected for **{comment}** |
   | `product_released` | Product release detected for **{comment}** |
   | `ticket_created` | Ticket created — **{comment}** |
   | `severity_changed` | Severity changed from **{old_value}** to **{new_value}** |
   | `cvss_assessment_changed` | CVSS assessment changed from **{old_value}** to **{new_value}** |
   | `product_eligibility_changed` | Product eligibility changed from **{old_value}** to **{new_value}** for **{comment}** |

5. **Comment**: if present, displayed below the description in a muted style.

#### Pagination

Standard numbered pagination at the bottom of the timeline, consistent with
the pagination pattern used on list pages (Inbox, All Tickets). Shows current
page, total pages, and allows navigation to specific pages.

#### Empty State

When no events match the current filters, display a message: "No events match
the current filters." with a "Clear filters" action.

When a ticket has no events at all (should not happen in practice since ticket
creation always generates at least one event), display: "No history available
for this ticket."

## Service Contract

Every service function that modifies a ticket MUST create a `TicketEvent` as
part of the same database transaction. This ensures atomicity — if the
mutation succeeds, the event is guaranteed to be recorded; if the mutation
fails, no orphan event is created.

### Implementation Guidelines

1. **Same transaction**: the `TicketEvent` insert MUST happen in the same
   database session/transaction as the ticket mutation. Do NOT create events
   in a separate transaction or after committing the main change.

2. **Service layer responsibility**: event creation belongs in the service
   layer (`app/services/`), not in the API layer or model layer. The service
   function that performs the mutation also creates the event.

3. **Helper function**: services SHOULD use a shared helper to create events,
   to ensure consistent field population:

   ```python
   async def create_ticket_event(
       session: AsyncSession,
       ticket_id: UUID,
       event_type: TicketEventType,
       user_id: UUID | None = None,
       old_value: str | None = None,
       new_value: str | None = None,
       comment: str | None = None,
   ) -> TicketEvent:
       ...
   ```

4. **No silent mutations**: if a service modifies ticket data without
   creating a `TicketEvent`, it is a bug. See Guardrail 11 in `AGENTS.md`.

## Testing Requirements

Tests for any ticket-mutating service MUST verify:

1. A `TicketEvent` record is created after the operation
2. The `event_type` matches the expected value
3. `old_value` and `new_value` are correctly populated
4. `user_id` is set for user actions and `NULL` for system actions
5. The event is created in the same transaction (i.e., if the operation is
   rolled back, no event exists)

See Guardrail 6 (Mandatory testing) and Guardrail 11 (Ticket event logging)
in `AGENTS.md` for enforcement.
