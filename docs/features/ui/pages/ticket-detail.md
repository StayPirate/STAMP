# Ticket Detail

**Route**: `/tickets/:id`

Full view of a single ticket with all CVE data, affectedness information,
and workflow actions.

## Layout

The page is divided into the following sections:

### Header

- **Ticket ID**: `SNTL-{n}` in prominent display, monospace. If the
  ticket has an associated CVE, the CVE ID is shown alongside with an
  em-dash separator (e.g., `SNTL-42 — CVE-2024-1234`). Note: ticket
  list tables use parentheses instead (e.g., `SNTL-42 (CVE-2024-1234)`)
  for compactness
- **Status badge**: color-coded current status
- **Severity badge**: color-coded severity level. For tickets with a CVE,
  always read-only (derived from CVSS assessments via the resolution
  cascade — see `docs/features/tickets/cvss-scoring.md`). For tickets without a
  CVE, editable by the VA (sets `severity_override` — see
  `docs/features/tickets/tickets.md`, Severity Resolution)
- **Assignee**: current assignee with option to reassign
- **Action buttons**: context-dependent based on current status (see below)

### Header Actions

| Current Status | Available Actions                                    |
|----------------|------------------------------------------------------|
| New            | Assign to me, Ignore, Mark as Duplicate              |
| Analysis       | Ignore, Reassign, Mark as Duplicate                  |
| Analyzed       | Reassign, Mark as Duplicate, Copy maintainer link    |
| Resolved       | Reassign, Mark as Duplicate                          |
| Ignored        | Reassign, Mark as Duplicate                          |
| Duplicated     | Revert duplicate (restores previous state, reassigns to current VA) |

**Copy maintainer link** (VA role only, available when ticket is in
`Analyzed` status): copies the URL `/my-packages/ticket/:id` to the
clipboard. The VA can then share this link with the package maintainer.
See `docs/features/ui/maintainer-dashboard.md` for the per-ticket view.

### Delete Ticket (Admin only)

A "Delete ticket" button is displayed in the header actions area,
**visible only to users with the Admin role**. It is available from any
ticket status. Clicking it opens a confirmation dialog. The Admin may
optionally provide a note (stored in the `TicketEvent.comment` field).
Upon confirmation, the ticket is soft-deleted and the Admin is redirected
to the All Tickets page.

Non-admin users (Vulnerability Analysts, unauthenticated users) never see the
"Delete ticket" button and are not aware that ticket deletion is possible.

### Soft-Deleted Ticket View

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

When a **non-admin user** (Vulnerability Analyst or unauthenticated) accesses
the URL of a soft-deleted ticket, the ticket detail page displays only a
message: **"This ticket has been deleted. Contact an admin if you think
this is an error."** No ticket data is shown. The API returns 410 Gone.

**Automatic status transitions**: transitions from Analysis to Analyzed
and from Analyzed to Resolved happen automatically when gate conditions
are met. There are no manual action buttons for these transitions. See
`docs/features/tickets/tickets.md` (Centralized Status Evaluation) for details.

### CVE Information Card

Shown only when the ticket has an associated CVE. Hidden for tickets
without a CVE.

- **Description**: full CVE description text
- **Published date**: when the CVE was published
- **Modified date**: when the CVE was last modified at source
- **Sources**: which data sources provided this CVE (NVD, MITRE)
  with fetch timestamps

### CVSS Card

Shown only when the ticket has an associated CVE. Hidden for tickets
without a CVE (severity is set via `severity_override` instead — see
`docs/features/tickets/tickets.md`, Severity Resolution).

A dedicated card displaying CVSS assessments from multiple providers,
organized by CVSS version in tabs. See `docs/features/tickets/cvss-scoring.md`
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
highest default version). See `docs/features/tickets/cvss-scoring.md`.

### References Section

Dedicated section displaying all external links associated with the
ticket. See `docs/features/ui/references.md` for the full specification.

- References are displayed as clickable links grouped by source (e.g.,
  NVD, MITRE, Manual)
- Each reference shows its title (or URL if no title), source badge, and
  tags (if any)
- "Add Reference" button visible to Vulnerability Analysts
- Each reference has an edit/delete action menu for Vulnerability Analysts
- All references are editable/deletable regardless of their origin
  (automatic or manual)
- Empty state: "No references yet."

### Affectedness Section

Tree structure showing packages, codestreams, and products with their
affectedness status. See `docs/features/packages/package-tracking.md` (UI
Requirements section) for the full specification of the tree layout,
status dropdowns, eligibility indicators, and color coding.

Each package node in the tree displays the **bugowner** next to the
package name. For group bugowners, the group name and collective email
are shown, with a tooltip or expandable section listing group members.
For person bugowners, the name and email are shown. If the bugowner is
unknown, "Unknown" is displayed in a neutral/greyed-out style. See
`docs/features/packages/package-bugowner.md` for details.

The ticket can transition to Analyzed only when all codestreams and
products have a status other than Analysis (see
`docs/features/packages/package-tracking.md`, Ticket Lifecycle Integration).

### Duplicate Information

Shown only when the ticket is in Duplicated state:

- "This ticket is a duplicate of [SNTL-{n}]" with link to the original
  ticket. If the original has a CVE, also shows the CVE ID
- Button: "Revert duplicate status" — restores the previous state and
  reassigns the ticket to the VA who clicks the button
- Confirmation dialog before reverting

Shown on the original ticket when other tickets reference it as duplicate:

- "Duplicates: [SNTL-{n1}], [SNTL-{n2}]" with links

### Event History (Tab)

A dedicated **"History" tab** in the Ticket Detail page provides a complete
audit trail with search and filtering capabilities.

- Chronological timeline (newest first) of all ticket events
- Filter bar: filter by event type (multi-select), by actor (user or system),
  and text search on comments
- Paginated results

See `docs/features/tickets/ticket-history.md` for the full specification, including
API endpoint, filter parameters, event type contract, and UI details.

## Security

- Public (no authentication required)
- Edit actions (assign, change status, edit affectedness, reassign, mark as
  duplicate) require the Vulnerability Analyst role
- Reassignment is available to any Vulnerability Analyst, not just the current
  assignee
- Soft-deleting and restoring tickets requires the Admin role
- Non-admin users accessing a soft-deleted ticket receive 410 Gone
- See `docs/features/identity/rbac.md` for the full permission model
