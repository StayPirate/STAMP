# Pages

## Purpose

Define the main pages of the STAMP platform. The platform is designed around
a ticket-based workflow where incident managers (IMs) triage, analyze, and
resolve CVEs that affect maintained products.

Each CVE ingested into the system automatically generates a **Ticket** — the
primary work unit for IMs. Tickets track the full lifecycle from initial
triage to resolution.

## Pages Overview

| Page             | Route             | Description                                      |
|------------------|-------------------|--------------------------------------------------|
| Inbox            | `/inbox`          | New tickets awaiting triage                      |
| My Tickets       | `/my-tickets`     | Tickets assigned to the current IM               |
| All Tickets      | `/tickets`        | All tickets with search and filters              |
| Ticket Detail    | `/tickets/:id`    | Full ticket view with CVE data and actions       |
| Login            | `/login`          | Authentication page                              |

## Ticket Lifecycle

```
New ──→ Analysis ──→ Analyzed ──→ Resolved
 │         │
 ├──→ Ignored (from New or Analysis only)
 │
 └──→ Duplicated (from any state, reversible)
      (also Analysis, Analyzed, Resolved, Ignored → Duplicated)
```

### States

- **New**: ticket created automatically when a CVE is ingested. Not yet
  assigned to any IM.
- **Analysis**: assigned to an IM who is actively analyzing the CVE — filling
  in affectedness data for each package/codestream/product combination.
- **Analyzed**: all required data has been filled in (affectedness status for
  every relevant package × codestream). Ready for updates to be prepared.
- **Resolved**: security updates have been released for all affected packages
  across all affected products.
- **Ignored**: the CVE affects software that is not supported in our
  products. Can only be set from New or Analysis.
- **Duplicated**: the CVE is a duplicate of another CVE (e.g., a CNA later
  merges two CVE IDs for the same vulnerability). Links to the original
  ticket. Reversible: when reverted, the ticket returns to its previous state
  and is reassigned to the IM who performed the revert.

### State Transitions

| From       | To         | Trigger                                    | Who            |
|------------|------------|--------------------------------------------|----------------|
| New        | Analysis   | Incident Manager clicks "Assign to me" or is assigned | Any Incident Manager |
| New        | Ignored    | Incident Manager clicks "Ignore" action    | Any Incident Manager |
| Analysis   | Analyzed   | All affectedness data is complete           | Assignee        |
| Analysis   | Ignored    | Incident Manager determines CVE is not relevant | Assignee    |
| Analyzed   | Resolved   | All packages in final status               | Assignee        |
| Any        | Duplicated | Incident Manager marks ticket as duplicate  | Any Incident Manager |
| Duplicated | (previous) | Incident Manager reverts duplicate status   | Any Incident Manager (becomes new assignee) |
| Resolved   | Analyzed   | CVSS recalculation causes products to become AFFECTED | System |
| Resolved   | Analysis   | Package added or codestream reset to ANALYSIS | Incident Manager |
| Analyzed   | Analysis   | Package added or codestream reset to ANALYSIS | Incident Manager |

### Reassignment

A ticket can be reassigned to a different Incident Manager at any time, regardless of its
current state. Reassignment does not change the ticket status. All
reassignments are logged in the ticket event history.

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
| CVE ID            | CVE identifier (e.g., CVE-2025-1234), monospace     |
| Severity          | Color-coded severity badge (Critical/High/Medium/Low/None). Shown only if available from sources |
| CVSS Score        | Numeric CVSS score (resolved via the default CVSS version). Shown only if available |
| Affected Packages | Package names resolved automatically via CPE mapping during CVE ingestion (see `docs/features/package-tracking.md`). Comma-separated, truncated if many |
| Summary           | First ~120 characters of the CVE description         |
| Published         | Date the CVE was published                           |
| Actions           | Quick action buttons                                 |

### Quick Actions

- **Assign to me**: assigns the ticket to the current IM and transitions
  status to Analysis. The IM is redirected to the ticket detail page.
- **Ignore**: transitions status to Ignored. The ticket disappears from the
  inbox. A confirmation dialog is shown before the action.

### Default Sort

By `published` date descending (most recent first).

### Empty State

When no new tickets are available, show a message: "No new CVEs to triage.
Check back later or sync CVE sources manually."

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
| CVE ID            | CVE identifier, monospace                           |
| Severity          | Color-coded severity badge                          |
| Status            | Current ticket status badge                         |
| Affected Packages | Package names (comma-separated, truncated)          |
| Summary           | First ~120 characters of the CVE description        |
| Updated           | Last update timestamp                               |

### Filters

- **Status**: filter by ticket status (default: all active states — Analysis,
  Analyzed)

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
- Search bar + filter controls
- Sortable, paginated table

### Table Columns

| Column            | Description                                         |
|-------------------|-----------------------------------------------------|
| CVE ID            | CVE identifier, monospace                           |
| Severity          | Color-coded severity badge                          |
| Status            | Current ticket status badge                         |
| Assignee          | Username of assigned IM (or "Unassigned")           |
| Affected Packages | Package names (comma-separated, truncated)          |
| Summary           | First ~120 characters of the CVE description        |
| Published         | CVE published date                                  |
| Updated           | Last update timestamp                               |

### Search

Free-text search across:
- CVE ID
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

### Default Sort

By `published` date descending.

### Pagination

Default 25 tickets per page. Configurable: 25, 50, 100.

## Ticket Detail

**Route**: `/tickets/:id`

Full view of a single ticket with all CVE data, affectedness information,
and workflow actions.

### Layout

The page is divided into the following sections:

#### Header

- **CVE ID**: prominent display, monospace
- **Status badge**: color-coded current status
- **Severity badge**: color-coded severity level, always read-only
  (derived from CVSS assessments via the resolution cascade — see
  `docs/features/cvss-scoring.md`)
- **Assignee**: current assignee with option to reassign
- **Action buttons**: context-dependent based on current status (see below)

#### Header Actions

| Current Status | Available Actions                                    |
|----------------|------------------------------------------------------|
| New            | Assign to me, Ignore, Mark as Duplicate              |
| Analysis       | Mark as Analyzed (if gates met), Ignore, Reassign, Mark as Duplicate |
| Analyzed       | Mark as Resolved (if all updates released), Reassign, Mark as Duplicate |
| Resolved       | Mark as Duplicate                                    |
| Ignored        | Mark as Duplicate                                    |
| Duplicated     | Revert duplicate (restores previous state, reassigns to current IM) |

**Analysis → Analyzed gates**: the "Mark as Analyzed" action is available
only when ALL of the following conditions are met:

1. All affectedness data is complete (no codestreams in Analysis status)
2. SUSE CVSS v3.1 assessment has been provided by the IM
3. SUSE CVSS v4.0 assessment has been provided by the IM

If any gate is not met, the button is disabled with a tooltip explaining
which requirement is missing.

#### CVE Information Card

- **Description**: full CVE description text
- **Published date**: when the CVE was published
- **Modified date**: when the CVE was last modified at source
- **References**: links to external references (NVD, advisories, etc.)
- **Sources**: which data sources provided this CVE (NVD, MITRE)
  with fetch timestamps

#### CVSS Card

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

#### Affectedness Section

Tree structure showing packages, codestreams, and products with their
affectedness status. See `docs/features/package-tracking.md` (UI
Requirements section) for the full specification of the tree layout,
status dropdowns, eligibility indicators, and color coding.

The ticket can transition to Analyzed only when all codestreams have a
status other than Analysis (see `docs/features/package-tracking.md`,
Ticket Lifecycle Integration).

#### Duplicate Information

Shown only when the ticket is in Duplicated state:

- "This ticket is a duplicate of [CVE-XXXX-YYYY]" with link to the original
  ticket
- Button: "Revert duplicate status" — restores the previous state and
  reassigns the ticket to the IM who clicks the button
- Confirmation dialog before reverting

Shown on the original ticket when other tickets reference it as duplicate:

- "Duplicates: [CVE-XXXX-AAAA], [CVE-XXXX-BBBB]" with links

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
- See `docs/features/rbac.md` for the full permission model
