# Orphan Tickets

**Route**: `/orphan-tickets`

Displays all unassigned tickets in **Analysis** or **Analyzed** status —
active tickets that require attention but currently have no assignee. This
view helps team leads and VAs identify tickets that lost their assignee
(e.g., due to user deactivation) and need to be picked up.

Tickets in **New** status are excluded because they are monitored through the
Inbox page and have never been assigned.

## Layout

- Page title: "Orphan Tickets"
- Ticket count badge showing total number of orphan tickets
- Sortable, paginated table

## Table Columns

| Column            | Description                                         |
|-------------------|-----------------------------------------------------|
| Ticket ID         | `SNTL-{n}` identifier, monospace. For tickets with a CVE, also shows CVE ID (e.g., `SNTL-42 (CVE-2025-1234)`) |
| Severity          | Color-coded severity badge (Critical/High/Medium/Low/None) |
| Status            | Current ticket status badge (Analysis or Analyzed)  |
| Affected Packages | Package names (comma-separated, truncated)          |
| Summary           | First ~120 characters of the CVE description (if CVE present) |
| Updated           | Last update timestamp                               |
| Actions           | Quick action buttons                                |

## Quick Actions

- **Assign to me**: assigns the ticket to the current VA. The ticket
  disappears from this view. The VA is redirected to the ticket detail page.

## Default Sort

By severity descending (Critical first), then by `updated_at` ascending
(most stale first). This surfaces the most urgent and neglected tickets at
the top.

## Pagination

Default 25 tickets per page. Configurable: 25, 50, 100.

## Empty State

"No orphan tickets. All active tickets are assigned."

## Security

- Requires authentication
- Visible to users with the Vulnerability Analyst role or above
