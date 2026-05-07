# My Tickets

**Route**: `/my-tickets`

Displays all tickets assigned to the currently logged-in VA.

## Layout

- Page title: "My Tickets"
- Ticket count badge
- Filter bar with status filter (Analysis, Analyzed, or all active states)
- Sortable, paginated table

## Table Columns

| Column            | Description                                         |
|-------------------|-----------------------------------------------------|
| Ticket ID         | `SNTL-{n}` identifier, monospace. For tickets with a CVE, also shows CVE ID |
| Severity          | Color-coded severity badge                          |
| Status            | Current ticket status badge                         |
| Affected Packages | Package names (comma-separated, truncated)          |
| Summary           | First ~120 characters of the CVE description (if CVE present) |
| Updated           | Last update timestamp                               |

## Filters

- **Status**: filter by ticket status (default: in-progress states —
  Analysis, Analyzed). Note: tickets in `New` status never appear on this
  page because they have no assignee

## Default Sort

By `updated_at` descending (most recently updated first).

## Empty State

"You have no assigned tickets."

## Security

- Requires authentication (shows tickets assigned to the current user)
- Visible to users with the Vulnerability Analyst role
