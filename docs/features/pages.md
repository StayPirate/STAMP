# Pages

## Purpose

Define the main pages of the STAMP platform. The platform is designed around
a ticket-based workflow where incident managers (IMs) triage, analyze, and
resolve security issues that affect maintained products.

A **Ticket** is the primary work unit for IMs. Tickets may or may not be
associated with a CVE. See `docs/features/tickets.md` for the full ticket
specification (identification, creation pathways, lifecycle, severity
resolution, and status transition rules).

## Pages Overview

| Page             | Route             | Description                                      |
|------------------|-------------------|--------------------------------------------------|
| Inbox            | `/inbox`          | New tickets awaiting triage                      |
| My Tickets       | `/my-tickets`     | Tickets assigned to the current IM               |
| All Tickets      | `/tickets`        | All tickets with search and filters              |
| Ticket Detail    | `/tickets/:id`    | Full ticket view with CVE data and actions       |
| Fetchers         | `/fetchers`       | Fetcher dashboard (see `docs/features/fetcher-dashboard.md`) |
| Fetcher Detail   | `/fetchers/:name` | Individual fetcher detail (see `docs/features/fetcher-dashboard.md`) |
| Admin Settings   | `/admin/settings` | System settings (see `docs/features/admin.md`)   |
| Login            | `/login`          | Authentication page                              |

## Ticket Lifecycle

See `docs/features/tickets.md` for the authoritative specification of
ticket statuses, transitions, gates, and rules. A summary is provided
here for context.

```
New ──→ Analysis ──→ Analyzed ──→ Resolved
 │         │
 ├──→ Ignored (from New or Analysis only)
 │
 └──→ Duplicated (from any state, reversible)
      (also Analysis, Analyzed, Resolved, Ignored → Duplicated)
```

### States

- **New**: ticket created automatically (CVE ingestion, codestream
  detection, or external source). Not yet assigned to any IM. Note:
  manually created tickets skip this state and start directly in Analysis
  (see `docs/features/tickets.md`, Manual Creation).
- **Analysis**: assigned to an IM who is actively analyzing — filling
  in affectedness data for each package/codestream/product combination.
- **Analyzed**: all required data has been filled in. Ready for updates
  to be prepared. See `docs/features/tickets.md` (Gate: Analysis →
  Analyzed) for the full gate conditions.
- **Resolved**: security updates have been released for all affected packages
  across all affected products.
- **Ignored**: the issue does not require action. Can only be set from
  New or Analysis.
- **Duplicated**: the ticket is a duplicate of another ticket. Links to
  the original ticket. Reversible: when reverted, the ticket returns to
  its previous state and is reassigned to the IM who performed the revert.

## Inbox

**Route**: `/inbox`

Displays all tickets in **New** state — CVEs that have been fetched from
external sources but not yet picked up by any IM.

### Layout

- Page title: "Inbox"
- Ticket count badge showing total number of new tickets
- Sortable table with the following columns

### Table Columns

| Column            | Description                                         |
|-------------------|-----------------------------------------------------|
| Ticket ID         | `STAMP-{n}` identifier, monospace. For tickets with a CVE, also shows CVE ID (e.g., `STAMP-42 (CVE-2025-1234)`) |
| Severity          | Color-coded severity badge (Critical/High/Medium/Low/None). Shown only if available (from CVSS or severity_override) |
| CVSS Score        | Numeric CVSS score (resolved via the default CVSS version). Shown only for tickets with a CVE |
| Affected Packages | Package names resolved automatically via CPE mapping during CVE ingestion (see `docs/features/package-tracking.md`). Comma-separated, truncated if many |
| Summary           | First ~120 characters of the CVE description (if CVE present), or empty |
| Published         | Date the CVE was published (if CVE present)          |
| Actions           | Quick action buttons                                 |

### Quick Actions

- **Assign to me**: assigns the ticket to the current IM and transitions
  status to Analysis. The IM is redirected to the ticket detail page.
- **Ignore**: transitions status to Ignored. The ticket disappears from the
  inbox. A confirmation dialog is shown before the action.

### Default Sort

By `published` date descending (most recent first).

### Empty State

When no new tickets are available, show a message: "No new tickets to
triage. Check back later or sync CVE sources manually."

## My Tickets

**Route**: `/my-tickets`

Displays all tickets assigned to the currently logged-in IM.

### Layout

- Page title: "My Tickets"
- Ticket count badge
- Filter bar with status filter (Analysis, Analyzed, or all active states)
- Sortable, paginated table

### Table Columns

| Column            | Description                                         |
|-------------------|-----------------------------------------------------|
| Ticket ID         | `STAMP-{n}` identifier, monospace. For tickets with a CVE, also shows CVE ID |
| Severity          | Color-coded severity badge                          |
| Status            | Current ticket status badge                         |
| Affected Packages | Package names (comma-separated, truncated)          |
| Summary           | First ~120 characters of the CVE description (if CVE present) |
| Updated           | Last update timestamp                               |

### Filters

- **Status**: filter by ticket status (default: in-progress states —
  Analysis, Analyzed). Note: tickets in `New` status never appear on this
  page because they have no assignee

### Default Sort

By `updated_at` descending (most recently updated first).

### Empty State

"You have no assigned tickets."

## All Tickets

**Route**: `/tickets`

Displays all tickets in the system with comprehensive search and filtering.

### Layout

- Page title: "All Tickets"
- Total ticket count
- "Create Ticket" button (visible only to Incident Managers). Opens a
  dialog with an optional severity selector (Critical, High, Medium, Low,
  None). On confirmation, calls `POST /api/v1/tickets` and redirects to
  the newly created ticket's detail page. See `docs/features/tickets.md`
  (Manual Creation) for the full creation flow
- Search bar + filter controls
- Sortable, paginated table

### Table Columns

| Column            | Description                                         |
|-------------------|-----------------------------------------------------|
| Ticket ID         | `STAMP-{n}` identifier, monospace. For tickets with a CVE, also shows CVE ID |
| Severity          | Color-coded severity badge                          |
| Status            | Current ticket status badge                         |
| Assignee          | Username of assigned IM (or "Unassigned")           |
| Affected Packages | Package names (comma-separated, truncated)          |
| Summary           | First ~120 characters of the CVE description (if CVE present) |
| Published         | CVE published date (if CVE present)                 |
| Updated           | Last update timestamp                               |

### Search

Free-text search across:
- `STAMP-{n}` identifier
- CVE ID (if present)
- CVE description
- Package names

### Filters

| Filter         | Type        | Options                                       |
|----------------|-------------|-----------------------------------------------|
| Status         | Multi-select| New, Analysis, Analyzed, Resolved, Ignored, Duplicated |
| Severity       | Multi-select| Critical, High, Medium, Low, None             |
| Assignee       | Select      | List of IMs + "Unassigned"                    |
| Product        | Select      | List of active products                       |
| Published from | Date        | CVE published date range start                |
| Published to   | Date        | CVE published date range end                  |

#### Deleted Tickets Filter (Admin only)

This filter is **visible only to users with the Admin role**. Non-admin
users (Incident Managers, unauthenticated users) do not see this filter
and are not aware that tickets can be deleted.

| Filter            | Type     | Options                                    |
|-------------------|----------|--------------------------------------------|
| Deleted tickets   | Select   | Hidden (default), Include deleted, Only deleted |

- **Hidden** (default): only active tickets are shown (standard behavior)
- **Include deleted**: soft-deleted tickets are included alongside active
  tickets. Deleted tickets are visually distinguished with a "Deleted"
  badge and a muted row style
- **Only deleted**: only soft-deleted tickets are shown

### Default Sort

By `published` date descending.

### Pagination

Default 25 tickets per page. Configurable: 25, 50, 100. The UI always
sends an explicit `per_page` parameter to the API (the API default of 20
is not used).

## Ticket Detail

**Route**: `/tickets/:id`

Full view of a single ticket with all CVE data, affectedness information,
and workflow actions.

### Layout

The page is divided into the following sections:

#### Header

- **Ticket ID**: `STAMP-{n}` in prominent display, monospace. If the
  ticket has an associated CVE, the CVE ID is shown alongside with an
  em-dash separator (e.g., `STAMP-42 — CVE-2024-1234`). Note: ticket
  list tables use parentheses instead (e.g., `STAMP-42 (CVE-2024-1234)`)
  for compactness
- **Status badge**: color-coded current status
- **Severity badge**: color-coded severity level. For tickets with a CVE,
  always read-only (derived from CVSS assessments via the resolution
  cascade — see `docs/features/cvss-scoring.md`). For tickets without a
  CVE, editable by the IM (sets `severity_override` — see
  `docs/features/tickets.md`, Severity Resolution)
- **Assignee**: current assignee with option to reassign
- **Action buttons**: context-dependent based on current status (see below)

#### Header Actions

| Current Status | Available Actions                                    |
|----------------|------------------------------------------------------|
| New            | Assign to me, Ignore, Mark as Duplicate              |
| Analysis       | Ignore, Reassign, Mark as Duplicate                  |
| Analyzed       | Reassign, Mark as Duplicate                          |
| Resolved       | Mark as Duplicate                                    |
| Ignored        | Mark as Duplicate                                    |
| Duplicated     | Revert duplicate (restores previous state, reassigns to current IM) |

#### Delete Ticket (Admin only)

A "Delete ticket" button is displayed in the header actions area,
**visible only to users with the Admin role**. It is available from any
ticket status. Clicking it opens a confirmation dialog. The Admin may
optionally provide a note (stored in the `TicketEvent.comment` field).
Upon confirmation, the ticket is soft-deleted and the Admin is redirected
to the All Tickets page.

Non-admin users (Incident Managers, unauthenticated users) never see the
"Delete ticket" button and are not aware that ticket deletion is possible.

#### Soft-Deleted Ticket View

When an **Admin** opens a soft-deleted ticket, the ticket detail page is
displayed normally with the following additions:

- A prominent warning banner at the top of the page indicating that the
  ticket has been soft-deleted, including the deletion timestamp
- A "Restore ticket" button in the header actions area. Clicking it
  opens a confirmation dialog. The Admin may optionally provide a note.
  Upon confirmation, `deleted_at` is cleared and the page refreshes to
  show the ticket in its normal state
- All other ticket data (CVE info, packages, references, history) is
  displayed as usual

When a **non-admin user** (Incident Manager or unauthenticated) accesses
the URL of a soft-deleted ticket, the ticket detail page displays only a
message: **"This ticket has been deleted. Contact an admin if you think
this is an error."** No ticket data is shown. The API returns 410 Gone.

**Automatic status transitions**: transitions from Analysis to Analyzed
and from Analyzed to Resolved happen automatically when gate conditions
are met. There are no manual action buttons for these transitions. See
`docs/features/tickets.md` (Centralized Status Evaluation) for details.

#### CVE Information Card

Shown only when the ticket has an associated CVE. Hidden for tickets
without a CVE.

- **Description**: full CVE description text
- **Published date**: when the CVE was published
- **Modified date**: when the CVE was last modified at source
- **Sources**: which data sources provided this CVE (NVD, MITRE)
  with fetch timestamps

#### CVSS Card

Shown only when the ticket has an associated CVE. Hidden for tickets
without a CVE (severity is set via `severity_override` instead — see
`docs/features/tickets.md`, Severity Resolution).

A dedicated card displaying CVSS assessments from multiple providers,
organized by CVSS version in tabs. See `docs/features/cvss-scoring.md`
for the full specification.

- **Tabs**: one per CVSS version, ordered by version ascending (e.g.,
  v2.0 → v3.1 → v4.0). Tabs for v3.1 and v4.0 are always visible. Tabs
  for other versions appear only when at least one assessment exists.
  The active tab on page load is the system-wide default CVSS version.
- **Tab content**: table with columns `Provider | Score | [metrics]`, one
  row per provider. Metric columns are version-specific (8 for v3.1, 11
  for v4.0), showing human-readable values parsed from the vector string.
- **SUSE assessment** (v3.1 and v4.0 tabs only):
  - If absent: "Add SUSE CVSS" button below the table
  - If present: "Edit SUSE CVSS" button below the table
  - Both open a modal with a vector string input field. The backend
    validates the vector and calculates the score automatically.
- **Empty state**: "No CVSS data available for this version" with the
  SUSE action button (if v3.1 or v4.0 tab)

**Note**: the severity badge in the header is always read-only and
calculated from the CVSS resolution cascade (SUSE default version →
highest default version). See `docs/features/cvss-scoring.md`.

#### References Section

Dedicated section displaying all external links associated with the
ticket. See `docs/features/references.md` for the full specification.

- References are displayed as clickable links grouped by source (e.g.,
  NVD, MITRE, Manual)
- Each reference shows its title (or URL if no title), source badge, and
  tags (if any)
- "Add Reference" button visible to Incident Managers
- Each reference has an edit/delete action menu for Incident Managers
- All references are editable/deletable regardless of their origin
  (automatic or manual)
- Empty state: "No references yet."

#### Affectedness Section

Tree structure showing packages, codestreams, and products with their
affectedness status. See `docs/features/package-tracking.md` (UI
Requirements section) for the full specification of the tree layout,
status dropdowns, eligibility indicators, and color coding.

The ticket can transition to Analyzed only when all codestreams and
products have a status other than Analysis (see
`docs/features/package-tracking.md`, Ticket Lifecycle Integration).

#### Duplicate Information

Shown only when the ticket is in Duplicated state:

- "This ticket is a duplicate of [STAMP-{n}]" with link to the original
  ticket. If the original has a CVE, also shows the CVE ID
- Button: "Revert duplicate status" — restores the previous state and
  reassigns the ticket to the IM who clicks the button
- Confirmation dialog before reverting

Shown on the original ticket when other tickets reference it as duplicate:

- "Duplicates: [STAMP-{n1}], [STAMP-{n2}]" with links

#### Event History (Tab)

A dedicated **"History" tab** in the Ticket Detail page provides a complete
audit trail with search and filtering capabilities.

- Chronological timeline (newest first) of all ticket events
- Filter bar: filter by event type (multi-select), by actor (user or system),
  and text search on comments
- Paginated results

See `docs/features/ticket-history.md` for the full specification, including
API endpoint, filter parameters, event type contract, and UI details.

## Security

- Ticket list pages (Inbox, All Tickets) and Ticket Detail are publicly
  accessible (no authentication required)
- My Tickets requires authentication (shows tickets assigned to the current
  user)
- Edit actions (assign, change status, edit affectedness, reassign, mark as
  duplicate) require the Incident Manager role
- Reassignment is available to any Incident Manager, not just the current
  assignee
- Soft-deleting and restoring tickets requires the Admin role. The delete
  and restore buttons are only visible to Admin users
- Viewing soft-deleted tickets (via the "Deleted tickets" filter or by
  direct URL) requires the Admin role. Non-admin users receive a 410 Gone
  message instead of ticket data
- See `docs/features/rbac.md` for the full permission model
