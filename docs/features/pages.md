# Pages

## Purpose

Define the main pages of the STAMP platform. The platform is designed around
a ticket-based workflow where incident managers (IMs) triage, analyze, and
resolve CVEs that affect maintained distributions.

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
  in affectedness data for each package/distribution combination.
- **Analyzed**: all required data has been filled in (affectedness status for
  every relevant package × distribution). Ready for updates to be prepared.
- **Resolved**: security updates have been released for all affected packages
  across all affected distributions.
- **Ignored**: the CVE affects software that is not supported in our
  distributions. Can only be set from New or Analysis.
- **Duplicated**: the CVE is a duplicate of another CVE (e.g., a CNA later
  merges two CVE IDs for the same vulnerability). Links to the original
  ticket. Reversible: when reverted, the ticket returns to its previous state
  and is reassigned to the IM who performed the revert.

### State Transitions

| From       | To         | Trigger                                    | Who            |
|------------|------------|--------------------------------------------|----------------|
| New        | Analysis   | IM clicks "Assign to me" or is assigned    | Any IM         |
| New        | Ignored    | IM clicks "Ignore" action                  | Any IM         |
| Analysis   | Analyzed   | All affectedness data is complete           | Assignee / Admin |
| Analysis   | Ignored    | IM determines CVE is not relevant           | Assignee / Admin |
| Analyzed   | Resolved   | All updates for affected packages released  | Assignee / Admin |
| Any        | Duplicated | IM marks ticket as duplicate of another     | Any IM         |
| Duplicated | (previous) | IM reverts duplicate status                 | Any IM (becomes new assignee) |

### Reassignment

A ticket can be reassigned to a different IM at any time, regardless of its
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
| CVSS Score        | Numeric CVSS v3 score. Shown only if available      |
| Affected Packages | Package names identified by automatic impact analysis. Comma-separated, truncated if many |
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
| Distribution   | Select      | List of active distributions                  |
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
- **Severity badge**: color-coded severity level
- **Assignee**: current assignee with option to reassign
- **Action buttons**: context-dependent based on current status (see below)

#### Header Actions

| Current Status | Available Actions                                    |
|----------------|------------------------------------------------------|
| New            | Assign to me, Ignore, Mark as Duplicate              |
| Analysis       | Mark as Analyzed (if data complete), Ignore, Reassign, Mark as Duplicate |
| Analyzed       | Mark as Resolved (if all updates released), Reassign, Mark as Duplicate |
| Resolved       | Mark as Duplicate                                    |
| Ignored        | Mark as Duplicate                                    |
| Duplicated     | Revert duplicate (restores previous state, reassigns to current IM) |

#### CVE Information Card

- **Description**: full CVE description text
- **CVSS Score**: numeric score with visual indicator
- **CVSS Vector**: full CVSS v3 vector string
- **Published date**: when the CVE was published
- **Modified date**: when the CVE was last modified at source
- **References**: links to external references (NVD, advisories, etc.)
- **Sources**: which data sources provided this CVE (NVD, MITRE)
  with fetch timestamps

#### Affectedness Table

Matrix showing the impact status for each package × distribution combination.

| Column          | Description                                          |
|-----------------|------------------------------------------------------|
| Package         | Package name, monospace                              |
| Distribution    | Distribution name and version                        |
| Status          | Affected, Not Affected, Investigating, Fixed — editable dropdown |
| Fixed Version   | Version that fixes the CVE — editable text field (shown when status is Fixed) |
| Notes           | Optional notes — editable text field                 |

- Only active distributions are shown
- IMs can edit the status, fixed version, and notes inline
- The ticket can transition to Analyzed only when all rows have a definitive
  status (Affected, Not Affected, or Fixed — not Investigating)

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

- All pages require authentication
- Inbox: viewable by all authenticated users, actions (assign, ignore) require
  IM role (Security Team or Admin)
- My Tickets: shows only tickets assigned to the current user
- All Tickets: viewable by all authenticated users
- Ticket Detail: viewable by all authenticated users; edit actions (change
  status, edit affectedness, reassign) require IM role (Security Team or Admin)
- Reassignment is available to any IM, not just the current assignee
