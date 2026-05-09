# Inbox

**Route**: `/inbox`

Displays all tickets in **New** state — CVEs that have been fetched from
external sources but not yet picked up by any VA.

## Layout

- Page title: "Inbox"
- Ticket count badge showing total number of new tickets
- Sortable table with the following columns

## Table Columns

| Column            | Description                                         |
|-------------------|-----------------------------------------------------|
| Ticket ID         | `SNTL-{n}` identifier, monospace. For tickets with a CVE, also shows CVE ID (e.g., `SNTL-42 (CVE-2025-1234)`) |
| Severity          | Color-coded severity badge (Critical/High/Medium/Low/None). Shown only if available (from CVSS or severity_override) |
| CVSS Score        | Numeric CVSS score (resolved via the default CVSS version). Shown only for tickets with a CVE |
| Affected Packages | Package names resolved automatically via CPE mapping during CVE ingestion (see `docs/features/packages/package-tracking.md`). Comma-separated, truncated if many |
| Summary           | First ~120 characters of the CVE description (if CVE present), or empty |
| Published         | Date the CVE was published (if CVE present)          |
| Actions           | Quick action buttons                                 |

## Quick Actions

- **Assign to me**: assigns the ticket to the current VA and transitions
  status to Analysis. The VA is redirected to the ticket detail page.
- **Ignore**: transitions status to Ignored. The ticket disappears from the
  inbox. A confirmation dialog is shown before the action.

## Default Sort

By `published` date descending (most recent first).

## Empty State

When no new tickets are available, show a message: "No new tickets to
triage. Check back later or sync CVE sources manually."

## Security

- Public (no authentication required)
- Edit actions (Assign to me, Ignore) require the Vulnerability Analyst role
