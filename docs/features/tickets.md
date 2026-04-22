# Tickets

## Purpose

Define the Ticket entity — the primary workflow unit of STAMP. A ticket
tracks the triage, analysis, and resolution of a security issue across
maintained products. Tickets may or may not be associated with a CVE.

This specification is the authoritative source for ticket identification,
creation pathways, lifecycle, severity resolution, and status transition
rules. Other feature specifications reference this document for
ticket-related behavior.

## Ticket Identification

Every ticket has two identifiers:

| Identifier | Format | Purpose |
|------------|--------|---------|
| `id` | UUID | Internal primary key, used in all foreign key relationships and API paths |
| `sequence_id` | Auto-increment integer, exposed as `STAMP-{n}` | Human-readable identifier for UI display, search, communication, and API lookup |

### STAMP-{n} Format

- The `sequence_id` is an auto-increment integer assigned at ticket
  creation. It is unique and immutable.
- The human-readable form is `STAMP-{sequence_id}` (e.g., `STAMP-1`,
  `STAMP-42`, `STAMP-1337`). No zero-padding.
- `STAMP-{n}` is the primary label shown in ticket lists, detail pages,
  logs, events, and external communications.
- For tickets with an associated CVE, the UI shows both identifiers:
  `STAMP-42 (CVE-2024-1234)`.
- For tickets without a CVE, only `STAMP-{n}` is shown.

### API Dual Lookup

All API endpoints that accept a `{ticket_id}` path parameter support
dual lookup:

- **UUID**: `GET /api/v1/tickets/a1b2c3d4-...` — standard UUID lookup
- **STAMP-{n}**: `GET /api/v1/tickets/STAMP-42` — resolved via
  `sequence_id` lookup

The backend detects the format automatically (UUIDs contain hyphens and
hex characters; `STAMP-{n}` starts with the literal prefix `STAMP-`).

### Search

The `search` query parameter on `GET /api/v1/tickets` searches across:

- `STAMP-{n}` identifier (exact or partial match on the numeric part)
- CVE ID (if the ticket has an associated CVE)
- CVE description
- Package names

## CVE Association

A ticket may optionally be associated with a CVE.

- `Ticket.cve_id`: UUID, FK to `cve.id`, **UNIQUE**, **NULLABLE**
- The UNIQUE constraint ensures that a CVE can be associated with at most
  one ticket (1:0..1 relationship)
- Tickets created from CVE ingestion have `cve_id` set at creation time
- Tickets created manually or from external sources (e.g., bug trackers)
  start without a CVE

### Associating a CVE Later

An IM can associate a CVE with a ticket that does not yet have one, via
`POST /api/v1/tickets/{ticket_id}/associate-cve`.

**Rules**:

- The ticket must not already have a CVE associated (`cve_id IS NULL`)
- The CVE must exist in the STAMP database (identified by CVE-ID string,
  e.g., `CVE-2024-1234`)
- The CVE must not already be associated with another ticket (UNIQUE
  constraint)
- When a CVE is associated:
  - `Ticket.cve_id` is set
  - The automatic severity from CVSS takes over (see
    [Severity Resolution](#severity-resolution))
  - A `TicketEvent` with `event_type = cve_associated` is created
  - CVSS sync and release tracking begin applying to the ticket

### Dissociating a CVE

Dissociating a CVE from a ticket is not supported. If a CVE was
associated in error, the ticket should be marked as Duplicated or
Ignored.

## Ticket Creation

### Automatic: CVE Ingestion

When a CVE is ingested from an external source (NVD, MITRE, or future
sources), a ticket is created automatically. See
`docs/features/cve-tracking.md` for the full ingestion flow.

- `cve_id`: set to the ingested CVE
- `status`: `New`
- `assignee_id`: `NULL`
- `TicketEvent`: `event_type = ticket_created`, `user_id = NULL`,
  `comment` = fetcher source description (e.g., `"CVE ingested from NVD"`)

### Automatic: Codestream Release Detection (Case C)

When the `CodestreamReleaseDetector` finds a CVE fix in IBS for a CVE
that has no ticket in STAMP, a `create_ticket_from_detection` task
creates the ticket. See `docs/features/package-tracking.md` (Case C)
for the full flow.

- `cve_id`: set to the created/fetched CVE
- `status`: `New`
- `assignee_id`: `NULL`
- `TicketEvent`: `event_type = ticket_created`, `user_id = NULL`,
  `comment` = detection context

### Manual Creation

An Incident Manager can create a ticket manually via
`POST /api/v1/tickets` or through the UI.

- `cve_id`: `NULL` (no CVE required; can be associated later)
- `status`: `Analysis` (direct, bypasses `New` — the creating user is
  automatically assigned)
- `assignee_id`: set to the creating user
- Two `TicketEvent` records are created atomically in the same
  transaction:
  1. `event_type = ticket_created`, `user_id = creating user`,
     `comment = "Ticket created manually"`
  2. `event_type = assignment`, `user_id = creating user`,
     `new_value = creating user's username`

**Required role**: Incident Manager.

The UI must provide a mechanism to create tickets manually (button
placement TBD in `docs/features/pages.md`).

### Future: External Sources

The data model supports automatic ticket creation from external systems
(e.g., internal bug trackers). These tickets are created without a CVE
and follow the same rules as automatic creation:

- `cve_id`: `NULL`
- `status`: `New`
- `assignee_id`: `NULL`

Specific integrations will be defined in separate feature specifications.

## Severity Resolution

Ticket severity is resolved transparently — the API and UI expose a
single `severity` field. The resolution logic is internal to the service
layer.

### Resolution Rules

1. If the ticket has a CVE (`cve_id IS NOT NULL`): severity =
   `cve.severity` (derived from CVSS assessments via the resolution
   cascade — see `docs/features/cvss-scoring.md`)
2. If the ticket does not have a CVE (`cve_id IS NULL`): severity =
   `ticket.severity_override`
3. If neither is available: severity = `None` (unknown)

### severity_override Field

- `Ticket.severity_override`: ENUM (Critical, High, Medium, Low, None),
  nullable
- Set manually by the IM via the API or UI
- Only used when `cve_id IS NULL`
- When a CVE is associated later, the automatic severity from CVSS takes
  over and `severity_override` is ignored (but not deleted — it serves
  as a historical record of the IM's initial assessment)

### UI Behavior

- **Ticket with CVE**: severity badge is read-only (derived from CVSS)
- **Ticket without CVE**: severity is editable by the IM (sets
  `severity_override`)
- In both cases, the UI shows a single severity badge — the user is not
  aware of the internal resolution mechanism

## Ticket Lifecycle

### Statuses

| Status     | Description |
|------------|-------------|
| New        | Created automatically (CVE ingestion or external source). Not yet assigned to any IM. |
| Analysis   | Assigned to an IM who is actively analyzing — filling in affectedness data. |
| Analyzed   | All required data has been filled in. Ready for updates to be prepared. |
| Resolved   | Security updates have been released for all affected packages across all products. |
| Ignored    | The issue does not require action. Can only be set from New or Analysis. |
| Duplicated | Duplicate of another ticket. Links to the original. Reversible. |

### Status Transition Diagram

```
New ──→ Analysis ──→ Analyzed ──→ Resolved
 │         │
 ├──→ Ignored (from New or Analysis only)
 │
 └──→ Duplicated (from any state, reversible)
      (also Analysis, Analyzed, Resolved, Ignored → Duplicated)
```

### Status Transitions

| From       | To         | Trigger                                    | Who            |
|------------|------------|--------------------------------------------|----------------|
| New        | Analysis   | IM clicks "Assign to me" or is assigned    | Any IM         |
| New        | Ignored    | IM clicks "Ignore" action                  | Any IM         |
| New        | Ignored    | NVD rejects the CVE (`vulnStatus = Rejected`) | System      |
| Analysis   | Analyzed   | All gates met (see below)                  | Assignee       |
| Analysis   | Ignored    | IM determines issue is not relevant        | Assignee       |
| Analyzed   | Resolved   | All packages in final status               | Assignee       |
| Any        | Duplicated | IM marks ticket as duplicate               | Any IM         |
| Duplicated | (previous) | IM reverts duplicate status                | Any IM (becomes new assignee) |
| Resolved   | Analyzed   | CVSS recalculation causes products to become AFFECTED | System |
| Resolved   | Analysis   | Package added or codestream reset to ANALYSIS | IM          |
| Analyzed   | Analysis   | Package added or codestream reset to ANALYSIS | IM          |

### Gate: Analysis → Analyzed

The "Mark as Analyzed" action is available only when ALL of the following
conditions are met:

1. **At least one package**: the ticket must have at least one package
   added (at least one `TicketPackageCodestream` record exists)
2. **All affectedness data complete**: no `TicketPackageCodestream`
   records in `ANALYSIS` status. Note: `AFFECTED` is non-final but is
   allowed — it indicates the IM has made a decision
3. **All products in final status**: all `TicketPackageProduct` records
   must be in a final status
4. **Severity set**: the ticket must have a determined severity (not
   `None`). For tickets with CVE, this is derived from CVSS. For tickets
   without CVE, `severity_override` must be set by the IM
5. **SUSE CVSS provided** (only for tickets with CVE): the IM must have
   provided BOTH SUSE CVSS v3.1 AND v4.0 assessments (see
   `docs/features/cvss-scoring.md`)

If any gate is not met, the button is disabled with a tooltip explaining
which requirement is missing.

### Gate: Analyzed → Resolved

All `TicketPackageCodestream` and `TicketPackageProduct` records must
have a final status: `RELEASED`, `NOT_AFFECTED`, `WONT_FIX`, `IGNORED`,
or `AFFECTED_RESOLVED`.

### Reverse Transitions

- **Resolved → Analyzed**: triggered by CVSS recalculation when products
  transition from `AFFECTED_RESOLVED` to `AFFECTED` (see
  `docs/features/cvss-scoring.md`, Recalculation Cascade)
- **Resolved → Analysis**: triggered when a package is added (new
  codestreams in `ANALYSIS`) or an IM resets a codestream to `ANALYSIS`
- **Analyzed → Analysis**: same triggers as above

### Reassignment

A ticket can be reassigned to a different IM at any time, regardless of
its current status. Reassignment does not change the ticket status. All
reassignments are logged in the ticket event history.

### Duplicate Handling

- Any ticket can be marked as a duplicate of another ticket, from any
  status
- When marked as duplicate:
  - `status` is set to `Duplicated`
  - `duplicate_of_id` is set to the original ticket's ID
  - `previous_status` stores the status before duplication
- When reverted:
  - `status` is restored to `previous_status`
  - `duplicate_of_id` is cleared
  - `previous_status` is cleared
  - The ticket is reassigned to the IM who performed the revert

## Soft-Delete

- Soft-delete is performed by setting `deleted_at` to the current
  timestamp
- Only users with the Admin role may soft-delete or restore tickets
- Soft-deleted tickets are excluded from all default queries and
  background processing (CVSS sync, release detection, NVD rejection
  handling, recalculation cascades)
- All sub-resources of a soft-deleted ticket remain intact but are
  inaccessible to non-admin users (API returns 410 Gone)
- A soft-deleted ticket can be restored by clearing `deleted_at`
- Both operations create a `TicketEvent` record (see
  `docs/features/ticket-history.md`)

### Status Categories

- **Active tickets**: status `New`, `Analysis`, or `Analyzed` AND
  `deleted_at IS NULL`. Actively monitored by background tasks.
- **Inactive tickets**: status `Resolved`, `Ignored`, or `Duplicated`.
  No longer monitored.
- **Soft-deleted tickets**: `deleted_at IS NOT NULL`. Excluded from
  everything regardless of status.

## Tickets Without CVE: Behavioral Differences

When a ticket has no associated CVE (`cve_id IS NULL`), the following
features behave differently:

| Feature | Behavior |
|---------|----------|
| CVSS scoring | Not applicable — no CVE means no CVSS assessments |
| CVSS sync (NVD, Red Hat) | Not applicable — ticket is skipped |
| Severity | Manual via `severity_override` (editable by IM) |
| Release tracking (codestream) | Not applicable — codestream detection relies on CVE-ID in IBS diffs |
| Release tracking (product) | Not applicable — product detection relies on CVE-ID in `updateinfo.xml` |
| NVD rejection handling | Not applicable |
| CVE Information UI section | Hidden |
| CVSS Card UI section | Hidden |
| Gate: SUSE CVSS required | Not applicable — severity is set via `severity_override` instead |
| Critical CVE notification | Not applicable |

Packages, codestreams, and products can still be added and managed
normally. The IM can set affectedness statuses and the ticket can
progress through the full lifecycle.

## API Endpoints

### Create Ticket

```
POST /api/v1/tickets
```

Creates a ticket manually. The creating user is automatically assigned.

Request body:

```json
{
  "severity": "High"
}
```

- `severity` (string, optional): initial severity override (Critical,
  High, Medium, Low, None). If omitted, severity is `None` until set
  by the IM.

Response: the created ticket object (201 Created).

Requires the Incident Manager role.

### Associate CVE

```
POST /api/v1/tickets/{ticket_id}/associate-cve
```

Associates an existing CVE with a ticket that does not have one.

Request body:

```json
{
  "cve_id": "CVE-2024-1234"
}
```

- `cve_id` (string, required): CVE identifier string

Response: the updated ticket object (200 OK).

Error responses:

- 400: ticket already has a CVE associated
- 404: CVE not found in STAMP database
- 409: CVE is already associated with another ticket

Requires the Incident Manager role.

### Update Severity Override

```
PATCH /api/v1/tickets/{ticket_id}/severity
```

Updates the severity override for a ticket without a CVE.

Request body:

```json
{
  "severity": "High"
}
```

- `severity` (string, required): severity value (Critical, High, Medium,
  Low, None)

Response: the updated ticket object (200 OK).

Error responses:

- 400: ticket has an associated CVE (severity is derived from CVSS, not
  manually settable)

Requires the Incident Manager role.

### Other Ticket Endpoints

All other ticket endpoints (list, detail, assign, ignore, duplicate,
revert-duplicate, soft-delete, restore, packages, CVSS, references,
events) are documented in `docs/api-spec.md` and their respective
feature specifications. All endpoints that accept `{ticket_id}` support
dual lookup (UUID or `STAMP-{n}`).

## Data Model

See `docs/data-model.md` for the full schema. Key fields on the Ticket
table:

| Column            | Type        | Constraints                  | Description |
|-------------------|-------------|------------------------------|-------------|
| id                | UUID        | PK                           | Internal identifier |
| sequence_id       | INTEGER     | UNIQUE, NOT NULL, auto-increment | Human-readable ID, exposed as `STAMP-{n}` |
| cve_id            | UUID        | FK(cve.id), UNIQUE, nullable | Associated CVE (optional) |
| status            | ENUM        | NOT NULL, DEFAULT New        | Ticket status |
| assignee_id       | UUID        | FK(user.id), nullable        | Assigned IM |
| severity_override | ENUM        | nullable                     | Manual severity (Critical, High, Medium, Low, None). Used when `cve_id IS NULL` |
| duplicate_of_id   | UUID        | FK(ticket.id), nullable      | Original ticket when Duplicated |
| previous_status   | ENUM        | nullable                     | Status before Duplicated |
| created_at        | TIMESTAMP   | NOT NULL, DEFAULT            | Record creation timestamp |
| updated_at        | TIMESTAMP   | NOT NULL, DEFAULT            | Record update timestamp |
| deleted_at        | TIMESTAMP   | nullable                     | Soft-delete timestamp |

## Security

- Viewing ticket lists and details: publicly accessible (no
  authentication required)
- Creating tickets, assigning, changing status, associating CVE,
  managing packages, setting severity override: Incident Manager role
- Soft-deleting and restoring tickets: Admin role
- See `docs/features/rbac.md` for the full permission model
