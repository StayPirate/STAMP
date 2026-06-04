# Ticket Audit Log

## Purpose

Provide a complete, searchable audit trail for every ticket in Sentinel. Every
modification to a ticket or its related data (status, assignee, duplicate
links, packages, tracks, products) MUST produce a `TicketAuditEvent` record.
Users can browse, filter, and search the history through the audit-log API
endpoint.

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

| `event_type` | Trigger | `user_id` | `old_value` | `new_value` | `comment` | `detail` |
|---|---|---|---|---|---|---|
| `status_change` | Ticket status transitions (manual or system-initiated) | VA user for manual, `NULL` for system (e.g., NVD rejection, CVSS recalculation) | Previous status (e.g., `New`) | New status (e.g., `Analysis`) | `NULL` for manual; system-generated description for automatic (e.g., `"CVE rejected by NVD"`) | `NULL` |
| `assignment` | Ticket assigned or reassigned | VA user for manual, `NULL` for system (e.g., employee deactivation) | Previous assignee username or `NULL` | New assignee username or `NULL` (unassigned) | `NULL` for manual; system-generated description for automatic (e.g., `"Unassigned from {old}: employee deactivated"`) | `NULL` |
| `duplicate_set` | Ticket marked as duplicate | VA user | `NULL` | `SNTL-{n}` identifier of the original ticket | `NULL` | `NULL` |
| `duplicate_removed` | Duplicate mark reverted | VA user | `SNTL-{n}` identifier of the original ticket | `NULL` | `NULL` | `NULL` |
| `duplicate_target_changed` | Cascade update: the canonical target was itself marked as duplicate, so this ticket's `duplicate_of_id` was re-pointed to the new canonical target. This event may be absent if the cascade was interrupted — this is not an error (the canonical resolver handles resolution at read time) | `NULL` | `SNTL-{n}` identifier of the previous canonical target | `SNTL-{n}` identifier of the new canonical target | `NULL` | `{"triggered_by_ticket": "SNTL-{n}"}` — the identifier of the ticket whose mark-as-duplicate operation triggered this cascade |
| `package_added` | Package added to ticket (manual or automatic). One event per package — child tracks and products created as part of the addition do not generate separate events (their initial creation is implicit in the package_added event) | VA user for manual, `NULL` for automatic | `NULL` | Package name | `NULL` for manual; contextual description for automatic (e.g., `"CPE match"`, `"Detected in track SUSE:SLE-15-SP6:Update"`) | `NULL` |
| `package_excluded` | Package directly soft-deleted (excluded) from ticket. One event per action — child tracks and products are not modified and do not generate events (they become effectively excluded via the hierarchy) | VA user for manual, `NULL` for system (orphan cleanup) | Package name | `NULL` | `NULL` | `NULL` for manual; `{"reason": "..."}` for automatic (see detail contract) |
| `package_restored` | Directly excluded package restored to ticket. Only the package record is restored — child records are not modified | VA user | `NULL` | Package name | `NULL` | `NULL` |
| `track_status_changed` | Track status changed (VA action, admin force-FIXED, or release detection) | VA user for manual changes, `NULL` for automatic transitions (e.g., release detected sets FIXED) | Old status | New status | `NULL` | `{"track": "...", "package": "..."}` (see detail contract) |
| `product_released` | Product release detected via updateinfo.xml | `NULL` | `NULL` | `RELEASED` | `NULL` | `{"track": "...", "package": "...", "product_id": "...", "advisory_id": "..."}` (see detail contract) |
| `ticket_created` | Ticket created (CVE ingestion, track detection, or manual) | `NULL` for automatic creation, creating user for manual creation | `NULL` | `NULL` | Creation source description (e.g., `"CVE ingested from NVD"`, `"CVE fix detected in openssl (SUSE:SLE-15-SP6:Update)"`, `"Ticket created manually"`) | `NULL` |
| `cve_associated` | CVE associated with a ticket that previously had no CVE | VA user | `NULL` | CVE-ID string (e.g., `"CVE-2024-1234"`) | `NULL` | `NULL` |
| `severity_changed` | CVSS recalculation changes ticket severity (system) or VA sets/clears severity override (manual) | `NULL` for automatic CVSS recalculation, acting user's UUID for manual severity override (`set_severity_override()`) | Old severity (e.g., `High`) | New severity (e.g., `Critical`) | `NULL` | `NULL` |
| `cvss_assessment_changed` | CVSS assessment added, modified, or removed | VA user for SUSE changes, `NULL` for external sync | Previous `"provider_name vX.Y score"` or `NULL` if new | Current `"provider_name vX.Y score"` or `NULL` if removed | `NULL` | `NULL` |
| `product_eligibility_changed` | Product eligibility changed due to CVSS recalculation, lifecycle phase transition (Reactive LTSS), threshold change, or VA override | VA user for VA overrides, `NULL` for system-triggered changes | Old eligibility (`true` or `false`) | New eligibility (`true` or `false`) | `NULL` | `{"track": "...", "package": "...", "product_id": "...", "reason": "..."}` (see detail contract) |
| `track_excluded` | Track directly soft-deleted (excluded) from ticket. One event per action — child products are not modified and do not generate events (they become effectively excluded via the hierarchy) | VA user for manual, `NULL` for system (orphan cleanup) | Track name | `NULL` | `NULL` | `{"track": "...", "package": "...", "reason": "..."}` (see detail contract) |
| `track_restored` | Directly excluded track restored to ticket. Only the track record is restored — child products are not modified | VA user | `NULL` | Track name | `NULL` | `NULL` |
| `product_excluded` | Product directly soft-deleted from ticket | VA user for manual, `NULL` for system (EOL, orphan cleanup) | Product display name | `NULL` | `NULL` | `{"track": "...", "package": "...", "product_id": "...", "reason": "..."}` (see detail contract) |
| `product_restored` | Directly excluded product restored to ticket | VA user | `NULL` | Product display name | `NULL` | `NULL` |

| `confidentiality_changed` | Ticket `is_confidential` flag toggled | Acting user | `"true"` or `"false"` | `"true"` or `"false"` | `NULL` | `NULL` |
| `access_grant_added` | User manually granted explicit access to a confidential ticket | Acting user | `NULL` | Target username | `NULL` | `NULL` |
| `access_grant_removed` | User manually revoked explicit access to a confidential ticket | Acting user | Target username | `NULL` | `NULL` | `NULL` |
| `reference_added` | Manual reference added to ticket | Acting user | `NULL` | Reference URL | `NULL` | `NULL` |
| `reference_deleted` | Manual reference deleted from ticket | Acting user | Reference URL | `NULL` | `NULL` | `NULL` |
| `reference_url_changed` | Manual reference URL changed via PATCH | Acting user | Previous URL | New URL | `NULL` | `NULL` |
| `reference_type_changed` | Manual reference type changed via PATCH | Acting user | Previous type (e.g., `advisory`) or `NULL` | New type (e.g., `patch`) or `NULL` | `NULL` | `{"url": "..."}` (see detail contract) |
| `reference_title_changed` | Manual reference title changed via PATCH | Acting user | Previous title or `NULL` | New title or `NULL` | `NULL` | `{"url": "..."}` (see detail contract) |
| `reference_description_changed` | Manual reference description changed via PATCH | Acting user | Previous description or `NULL` | New description or `NULL` | `NULL` | `{"url": "..."}` (see detail contract) |

**Rules**:

- `user_id` MUST be set for user-initiated actions and `NULL` for system
  actions.
- `old_value` and `new_value` store human-readable strings. For enum values,
  store the enum name (e.g., `AFFECTED`, `NOT_AFFECTED`). For user
  references, store the username.
- `comment` is used exclusively for system-generated human-readable
  descriptions (e.g., creation source, deactivation reason, detection
  context). It is NOT populated by user input — no API endpoint exposes
  `comment` as a user-provided field. If VA notes become a desired feature
  in the future, they should be introduced as a dedicated feature (new
  parameter across all relevant endpoints, consistent UX, proper spec)
  rather than an ad-hoc addition to individual endpoints. `comment` MUST
  NOT contain structured data intended for programmatic parsing.
- `detail` is used for structured machine-readable context (JSONB). Keys are
  validated per event type — see the detail JSONB Schema Contract below.
  Event types not listed in the contract MUST set `detail` to `NULL`.
- All events include an implicit `created_at` timestamp set by the database
  default.

### detail JSONB Schema Contract

The `detail` column carries structured context for event types where
`old_value`/`new_value` are insufficient to capture the full operational
context (e.g., which track/package/product is affected). Every event type that
populates `detail` MUST have its schema defined in the table below. Event
types not listed here MUST set `detail` to `NULL`.

| Event Type | Required Keys | Optional Keys | Example |
|---|---|---|---|
| `package_excluded` | — | `reason` (string) | `{"reason": "no_tracks_remaining"}` |
| `track_status_changed` | `track` (string), `package` (string) | — | `{"track": "SUSE:SLE-15-SP6:Update", "package": "openssl"}` |
| `track_excluded` | `track` (string), `package` (string), `reason` (string) | — | `{"track": "SUSE:SLE-15-SP6:Update", "package": "openssl", "reason": "orphan_cleanup"}` |
| `product_released` | `track` (string), `package` (string), `product_id` (UUID string), `advisory_id` (string) | — | `{"track": "SUSE:SLE-15-SP6:Update", "package": "openssl", "product_id": "550e8400-e29b-41d4-a716-446655440000", "advisory_id": "SUSE-SU-2025:1234-1"}` |
| `product_eligibility_changed` | `track` (string), `package` (string), `product_id` (UUID string), `reason` (string) | — | `{"track": "SUSE:SLE-15-SP6:Update", "package": "openssl", "product_id": "550e8400-e29b-41d4-a716-446655440000", "reason": "threshold"}` |
| `product_excluded` | `track` (string), `package` (string), `product_id` (UUID string), `reason` (string) | — | `{"track": "SUSE:SLE-15-SP6:Update", "package": "openssl", "product_id": "550e8400-e29b-41d4-a716-446655440000", "reason": "eol"}` |
| `reference_type_changed` | `url` (string) | — | `{"url": "https://bugzilla.suse.com/show_bug.cgi?id=12345"}` |
| `reference_title_changed` | `url` (string) | — | `{"url": "https://bugzilla.suse.com/show_bug.cgi?id=12345"}` |
| `reference_description_changed` | `url` (string) | — | `{"url": "https://bugzilla.suse.com/show_bug.cgi?id=12345"}` |

**Notes**:

- `package_excluded`: `detail` is `NULL` for manual VA actions. The optional
  `reason` key is present only for automatic exclusions (orphan cleanup). When
  `detail` is non-NULL, `reason` MUST be present.
- `product_eligibility_changed`: `reason` values are `reactive_ltss`,
  `threshold`, `cvss`, or `va_override`.
- `track_excluded` and `product_excluded`: `reason` values include
  `orphan_cleanup`, `eol`, and other system-initiated reasons.
- `reference_type_changed`, `reference_title_changed`,
  `reference_description_changed`: `url` is the post-normalization URL of the
  reference being modified — used as the locator since a ticket can have
  multiple references. For `reference_url_changed`, both old and new URLs are
  carried in `old_value`/`new_value`, so `detail` is NULL.
- Maximum payload size: 4 KB. The service layer MUST reject any `detail` value
  exceeding this limit.
- The service layer MUST validate that `detail` contains only keys defined in
  this contract for the given event type — undocumented keys are rejected.
- When a new `TicketAuditEventType` is added that uses the `detail` column,
  this table MUST be extended with the corresponding schema definition before
  the implementation proceeds.

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
| `search`     | string | —       | Case-insensitive substring search across `comment`, `old_value`, `new_value`, and `detail` (cast to text). Matches on any field are included (OR logic). If omitted, no text filtering is applied. |
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
      "detail": null,
      "created_at": "2025-03-15T10:30:00Z",
      "actor": {
        "id": "uuid",
        "username": "jdoe",
        "full_name": "John Doe",
        "active": true
      }
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
- The `search` filter performs a case-insensitive `ILIKE '%term%'` on
  `comment`, `old_value`, `new_value`, and `detail::text` (OR). This allows
  searching for product names, track names, statuses, or any contextual data
  across all event fields.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found |

**`Access: Authenticated`**

Confidentiality filtering is enforced centrally — see `docs/api-spec.md`
([Scoped Responses](docs/api-spec.md#scoped-responses)).

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

5. **detail validation**: `TicketAuditLog` MUST override `log_event()` to
   validate that `detail` contains only keys defined in the JSONB Schema
   Contract for the given event type. Undocumented keys MUST be rejected.
   The maximum `detail` payload is 4 KB.

6. **Concurrency correctness**: the correctness of `old_value` and
   `new_value` fields depends on the pessimistic locking enforced by
   the `ticket_mutations` module (see `docs/features/tickets/ticket-mutations.md`,
   Concurrency Control). The `FOR UPDATE` lock on the `Ticket` row
   serializes concurrent mutations, ensuring that each audit event
   captures the true pre-mutation state. Without this lock, concurrent
   transactions could record stale `old_value` entries.

## Testing Requirements

Tests for any ticket-mutating service MUST verify:

1. A `TicketAuditEvent` record is created after the operation
2. The `event_type` matches the expected value
3. `old_value` and `new_value` are correctly populated
4. `user_id` is set for user actions and `NULL` for system actions
5. `detail` is correctly populated according to the JSONB Schema Contract
   (expected keys present, no extra keys, `NULL` when required)
6. The event is created in the same transaction (i.e., if the operation is
   rolled back, no event exists)

See Guardrail 6 (Mandatory testing) and Guardrail 11 (Ticket event logging)
in `AGENTS.md` for enforcement.

## Data Retention

Indefinite. TicketAuditEvent records are never automatically deleted.

## Cross-references

- `docs/features/platform/audit-trail-infrastructure.md` — BaseAuditLog,
  AuditEventMixin, naming conventions
- `docs/features/identity/identity-audit-log.md` — IdentityAuditEvent detail
  JSONB pattern (reference implementation for the detail column contract)
- `docs/conventions.md` — Audit Trail Conventions
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
