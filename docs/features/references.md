# References

## Purpose

Provide a unified system for tracking external links associated with
tickets. References connect tickets to external resources such as NVD
entries, vendor advisories, patch URLs, bug tracker entries, and any other
relevant web resource.

References come from two sources:

- **Automatic**: created by CVE fetchers during ingestion. Each fetcher
  adds its own source URL (if it has a human-readable page) plus all
  references extracted from the CVE data.
- **Manual**: added by Vulnerability Analysts through the API or UI for any
  purpose (e.g., linking a Bugzilla bug, an internal advisory, a patch).

All references are stored in a single `TicketReference` table associated
with the ticket, regardless of their origin.

## Data Model

See `docs/data-model.md` for the full schema. Key table:

- **TicketReference**: stores external links associated with a ticket

### TicketReference

| Column     | Type           | Constraints                  | Description                        |
|------------|----------------|------------------------------|------------------------------------|
| id         | UUID           | PK                           | Internal identifier                |
| ticket_id  | UUID           | FK(ticket.id), NOT NULL      | Related ticket                     |
| url        | VARCHAR        | NOT NULL                     | URL of the external resource       |
| title      | VARCHAR        | nullable                     | Optional human-readable label      |
| source     | VARCHAR        | NOT NULL                     | Origin of the reference: fetcher name (e.g., `"sync_cves_nvd"`, `"sync_cves_mitre"`) or `"manual"` for user-added references |
| tags       | ARRAY(VARCHAR) | nullable                     | Descriptive tags (e.g., `"Patch"`, `"Vendor Advisory"`, `"Third Party Advisory"`). Populated from CVE source data when available |
| created_by | UUID           | FK(user.id), nullable        | User who added the reference. NULL for automatic references created by fetchers |
| created_at | TIMESTAMP      | NOT NULL, DEFAULT            | Record creation timestamp          |
| updated_at | TIMESTAMP      | NOT NULL, DEFAULT            | Record update timestamp            |

**Unique constraint**: `(ticket_id, url)` — a URL cannot be referenced
twice on the same ticket.

## Fetcher Integration

### source_reference_url_pattern

CVE fetchers that inherit from `BaseFetcher` (see
`docs/features/fetcher-infrastructure.md`) have an optional class attribute
`source_reference_url_pattern` that defines the URL pattern for the
fetcher's human-readable CVE page.

```python
class SyncCvesNvd(BaseFetcher):
    name = "sync_cves_nvd"
    description = "Incremental CVE sync from NVD"
    default_schedule = "0 */6 * * *"
    source_reference_url_pattern: str | None = "https://nvd.nist.gov/vuln/detail/{cve_id}"

class SyncCvesMitre(BaseFetcher):
    name = "sync_cves_mitre"
    description = "Syncs CVEs from MITRE"
    default_schedule = "0 */6 * * *"
    source_reference_url_pattern: str | None = "https://cve.org/CVERecord?id={cve_id}"
```

- When `source_reference_url_pattern` is set, the fetcher creates a
  `TicketReference` with the URL built from the pattern (replacing
  `{cve_id}` with the actual CVE ID).
- When `source_reference_url_pattern` is `None`, the fetcher does not
  create a self-referencing `TicketReference` (but still creates
  references from the CVE data if available).
- The pattern uses `{cve_id}` as the only placeholder, formatted with
  the CVE identifier (e.g., `CVE-2026-3317`).

**Convention for new CVE fetchers**: when implementing a new fetcher that
ingests CVE data, the implementer must determine whether the fetcher's
source has a human-readable web page for each CVE. If it does,
`source_reference_url_pattern` must be set with the appropriate URL
pattern so that the URL is automatically added as a `TicketReference`.

### Ingestion Flow

When a CVE fetcher processes a CVE (new or updated), the following
reference-related steps are performed **after** the ticket exists (i.e.,
after CVE upsert and ticket creation for new CVEs):

1. **Source reference** (if `source_reference_url_pattern` is set):
   upsert a `TicketReference` with:
   - `url`: pattern with `{cve_id}` replaced (e.g.,
     `https://nvd.nist.gov/vuln/detail/CVE-2026-3317`)
   - `title`: a short human-readable label derived from the source name
     (e.g., `"NVD"` for `sync_cves_nvd`, `"MITRE"` for `sync_cves_mitre`)
   - `source`: fetcher name (e.g., `"sync_cves_nvd"`)
   - `tags`: `[]` (empty)
   - `created_by`: `NULL` (automatic)

2. **CVE data references**: for each reference in the CVE data (e.g., the
   NVD API v2 `references` array), upsert a `TicketReference` with:
   - `url`: the reference URL from the CVE data
   - `title`: `NULL` (not provided by most sources)
   - `source`: fetcher name (e.g., `"sync_cves_nvd"`)
   - `tags`: tags from the CVE data (e.g., `["Vendor Advisory"]`), or
     `NULL` if not provided
   - `created_by`: `NULL` (automatic)

3. **Stale reference cleanup**: remove any `TicketReference` records that:
   - Have `source` matching the current fetcher name
   - Have a `url` that is no longer present in the current CVE data AND
     is not the source reference URL
   - This ensures that references removed from the upstream source are
     also removed from Sentinel

### Upsert Strategy

References are matched by `(ticket_id, url)` — the unique constraint.

- **New URL**: INSERT a new `TicketReference`
- **Existing URL**: UPDATE `title`, `tags`, `source` if changed
- **URL no longer in source data**: DELETE (only for references with
  matching `source` — manual references are never touched by fetchers)

### Example

When the NVD fetcher processes CVE-2026-3317 for the first time, it
creates the following `TicketReference` records:

| url | title | source | tags | created_by |
|-----|-------|--------|------|------------|
| `https://nvd.nist.gov/vuln/detail/CVE-2026-3317` | `NVD` | `sync_cves_nvd` | `[]` | NULL |
| `https://www.incibe.es/en/incibe-cert/notices/aviso/reflected-cross-site-scripting-navigate-cms-application` | NULL | `sync_cves_nvd` | NULL | NULL |

If the MITRE fetcher later processes the same CVE and finds additional
references, it adds its own references with `source = "sync_cves_mitre"`
without affecting the NVD references.

## API Endpoints

### List References

```
GET /api/v1/tickets/{ticket_id}/references
```

Returns all references for a ticket. This endpoint is **not paginated**
because the number of references per ticket is expected to be small
(typically fewer than 20). All references are returned in a single
response.

**Query parameters**:

| Parameter | Type   | Default | Description                       |
|-----------|--------|---------|-----------------------------------|
| source    | string | —       | Filter by source (e.g., `"sync_cves_nvd"`, `"manual"`) |

**Sorting**: results are ordered by `created_at` ascending (oldest first).
Client-controlled sorting is not supported (small dataset).

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "ticket_id": "uuid",
      "url": "https://nvd.nist.gov/vuln/detail/CVE-2026-3317",
      "title": "NVD",
      "source": "sync_cves_nvd",
      "tags": [],
      "created_by": null,
      "created_at": "2026-04-21T10:20:00Z",
      "updated_at": "2026-04-21T10:20:00Z"
    },
    {
      "id": "uuid",
      "ticket_id": "uuid",
      "url": "https://www.incibe.es/en/incibe-cert/notices/aviso/...",
      "title": null,
      "source": "sync_cves_nvd",
      "tags": null,
      "created_by": null,
      "created_at": "2026-04-21T10:20:00Z",
      "updated_at": "2026-04-21T10:20:00Z"
    },
    {
      "id": "uuid",
      "ticket_id": "uuid",
      "url": "https://bugzilla.suse.com/show_bug.cgi?id=12345",
      "title": "SUSE Bugzilla #12345",
      "source": "manual",
      "tags": null,
      "created_by": {
        "id": "uuid",
        "username": "jdoe"
      },
      "created_at": "2026-04-21T14:30:00Z",
      "updated_at": "2026-04-21T14:30:00Z"
    }
  ]
}
```

**Permissions**: publicly accessible for active tickets (no authentication
required). If the ticket is soft-deleted, only Admin users can access its
references; non-admin callers receive 410 Gone.

**Error responses**:

| Status | Code | Condition           |
|--------|------|---------------------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found    |
| 410    | `TICKET_DELETED` | Ticket is soft-deleted and the caller is not an Admin (see `docs/api-spec.md`, soft-delete protection on sub-resources) |

### Add Reference

```
POST /api/v1/tickets/{ticket_id}/references
```

Adds a manual reference to a ticket.

**Request body**:

```json
{
  "url": "https://bugzilla.suse.com/show_bug.cgi?id=12345",
  "title": "SUSE Bugzilla #12345"
}
```

| Field | Type   | Required | Description                     |
|-------|--------|----------|---------------------------------|
| url   | string | yes      | URL of the external resource    |
| title | string | no       | Optional label for the reference |

**Response** (201 Created):

```json
{
  "data": {
    "id": "uuid",
    "ticket_id": "uuid",
    "url": "https://bugzilla.suse.com/show_bug.cgi?id=12345",
    "title": "SUSE Bugzilla #12345",
    "source": "manual",
    "tags": null,
    "created_by": {
      "id": "uuid",
      "username": "jdoe"
    },
    "created_at": "2026-04-21T14:30:00Z",
    "updated_at": "2026-04-21T14:30:00Z"
  }
}
```

**Validation rules**:
- `url` must be a valid URL
- `url` must not already exist for this ticket (unique constraint)

**Side effects**:
- `source` is always set to `"manual"`
- `created_by` is set to the authenticated user
- `tags` is set to `NULL` (manual references do not use tags)

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition                                        |
|--------|------|--------------------------------------------------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found                                 |
| 409    | `RESOURCE_CONFLICT` | URL already exists for this ticket               |
| 410    | `TICKET_DELETED` | Ticket is soft-deleted and the caller is not an Admin |
| 422    | `VALIDATION_ERROR` | Invalid URL format or missing required fields    |

### Update Reference

```
PATCH /api/v1/tickets/{ticket_id}/references/{reference_id}
```

Updates an existing reference. Any reference can be updated regardless
of its source (automatic or manual). Only the fields included in the
request body are updated — omitted fields remain unchanged.

**Note on automatic references**: if a user changes the `url` of an
automatic reference, the fetcher's stale cleanup will not recognize the
new URL as its own and will not delete it on the next sync. The original
URL will be recreated by the fetcher. Effectively, editing the URL of an
automatic reference turns it into a de facto manual reference that
coexists with the fetcher-managed one.

**Request body**:

```json
{
  "url": "https://bugzilla.suse.com/show_bug.cgi?id=12345",
  "title": "Updated title"
}
```

| Field | Type   | Required | Description                     |
|-------|--------|----------|---------------------------------|
| url   | string | no       | URL of the external resource    |
| title | string | no       | Optional label for the reference |

At least one field must be provided.

**Response** (200 OK): the updated reference object (same format as
create response).

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition                                        |
|--------|------|--------------------------------------------------|
| 404    | `RESOURCE_NOT_FOUND` | Ticket or reference not found                    |
| 409    | `RESOURCE_CONFLICT` | URL already exists for this ticket (if changed)  |
| 410    | `TICKET_DELETED` | Ticket is soft-deleted and the caller is not an Admin |
| 422    | `VALIDATION_ERROR` | Invalid URL format or missing required fields    |

### Delete Reference

```
DELETE /api/v1/tickets/{ticket_id}/references/{reference_id}
```

Deletes a reference. Any reference can be deleted regardless of its
source (automatic or manual).

**Response** (204 No Content)

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition                          |
|--------|------|------------------------------------|
| 404    | `RESOURCE_NOT_FOUND` | Ticket or reference not found      |
| 410    | `TICKET_DELETED` | Ticket is soft-deleted and the caller is not an Admin |

## UI Requirements

### Ticket Detail Page

References are displayed in a dedicated **References** section in the
Ticket Detail page (see `docs/features/pages.md`).

#### Layout

- Section title: "References"
- Each reference is displayed as a clickable link with:
  - The `title` if available, otherwise the URL itself (truncated if long)
  - A small badge or label showing the source. The raw `source` value
    (e.g., `"sync_cves_nvd"`) is mapped to a human-readable display
    label in the frontend (e.g., "NVD", "MITRE", "Manual")
  - Tags displayed as small badges next to the link (e.g., "Patch",
    "Vendor Advisory")
- References are grouped by `source` for visual clarity (using the
  human-readable display labels)
- "Add Reference" button (visible only to Vulnerability Analysts)

#### Add Reference

Clicking "Add Reference" opens a simple form:
- URL input field (required)
- Title input field (optional)
- "Save" and "Cancel" buttons

#### Edit and Delete

Each reference has an action menu (visible only to Vulnerability Analysts)
with:
- "Edit" — opens the same form pre-filled with the current values
- "Delete" — confirmation dialog before deletion

#### Empty State

When no references exist: "No references yet."

## Ticket Event Logging

Reference mutations (add, edit, delete) do **not** generate
`TicketEvent` records. References are supplementary external metadata
and do not constitute ticket state changes. The `TicketEventType` enum
does not include reference-related event types.

## Security

- Reference list is publicly accessible (no authentication required)
- Adding, editing, and deleting references requires the Vulnerability Analyst
  role
- All references are editable/deletable by any Vulnerability Analyst,
  regardless of who created them or whether they were created
  automatically
- See `docs/features/rbac.md` for the full permission model

## Dependencies

- `docs/features/cve-tracking.md` — CVE ingestion flow creates references
- `docs/features/fetcher-infrastructure.md` — `BaseFetcher` contract for
  `source_reference_url_pattern`
- `docs/features/pages.md` — Ticket Detail page displays references
