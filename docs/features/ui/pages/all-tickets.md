# All Tickets

**Route**: `/tickets`

Displays all tickets in the system with comprehensive search and filtering.

## Layout

- Page title: "All Tickets"
- Total ticket count
- "Create Ticket" button (visible only to Vulnerability Analysts). Opens a
  dialog with an optional CVE-ID field and an optional severity selector
  (Critical, High, Medium, Low, None). When a CVE-ID is provided, severity
  is derived from the CVE's CVSS score and the severity selector is hidden.
  On confirmation, calls `POST /api/v1/tickets` and redirects to
  the newly created ticket's detail page. See `docs/features/tickets.md`
  (Manual Creation) for the full creation flow
- Search bar + filter controls
- Sortable, paginated table

## Table Columns

| Column            | Description                                         |
|-------------------|-----------------------------------------------------|
| Ticket ID         | `SNTL-{n}` identifier, monospace. For tickets with a CVE, also shows CVE ID |
| Severity          | Color-coded severity badge                          |
| Status            | Current ticket status badge                         |
| Assignee          | Username of assigned VA (or "Unassigned")           |
| Affected Packages | Package names (comma-separated, truncated)          |
| Summary           | First ~120 characters of the CVE description (if CVE present) |
| Published         | CVE published date (if CVE present)                 |
| Updated           | Last update timestamp                               |

## Search

Free-text search across:
- `SNTL-{n}` identifier
- CVE ID (if present)
- CVE description
- Package names

## Filters

| Filter         | Type        | Options                                       |
|----------------|-------------|-----------------------------------------------|
| Status         | Multi-select| New, Analysis, Analyzed, Resolved, Ignored, Duplicated |
| Severity       | Multi-select| Critical, High, Medium, Low, None             |
| Assignee       | Select      | List of VAs + "Unassigned"                    |
| Product        | Select      | List of active products                       |
| Published from | Date        | CVE published date range start                |
| Published to   | Date        | CVE published date range end                  |

### Deleted Tickets Filter (Admin only)

This filter is **visible only to users with the Admin role**. Non-admin
users (Vulnerability Analysts, unauthenticated users) do not see this filter
and are not aware that tickets can be deleted.

| Filter            | Type     | Options                                    |
|-------------------|----------|--------------------------------------------|
| Deleted tickets   | Select   | Hidden (default), Include deleted, Only deleted |

- **Hidden** (default): only active tickets are shown (standard behavior)
- **Include deleted**: soft-deleted tickets are included alongside active
  tickets. Deleted tickets are visually distinguished with a "Deleted"
  badge and a muted row style
- **Only deleted**: only soft-deleted tickets are shown

## Default Sort

By `published` date descending.

## Pagination

Default 25 tickets per page. Configurable: 25, 50, 100. The UI always
sends an explicit `per_page` parameter to the API (the API default of 20
is not used).

## Security

- Publicly accessible (no authentication required)
- "Create Ticket" button requires the Vulnerability Analyst role
- "Deleted tickets" filter visible only to Admin role
- Edit actions require the Vulnerability Analyst role
