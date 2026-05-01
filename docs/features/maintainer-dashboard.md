# Maintainer Dashboard

## Purpose

Provide package maintainers (bugowners) with a personal view of their
pending work, in-progress submissions, and completed releases across all
packages they maintain. This gives maintainers immediate visibility into
what needs fixing, what is already in the pipeline, and what has been
recently released — without requiring them to search through individual
tickets.

Additionally, a per-ticket view allows Vulnerability Analysts to share a
focused link with a maintainer showing exactly what needs to be done for a
specific ticket.

## Target Audience

This feature targets **package maintainers (bugowners)** — SUSE employees
who maintain one or more source packages in IBS. It is not a replacement
for the VA-focused "My Tickets" page; it complements it by providing a
package-centric perspective on security update work.

## User Identification

A user is identified as a maintainer of a package through the existing
`PackageBugowner` data (see `docs/features/package-bugowner.md`):

- **Person bugowner**: the user's email matches
  `PackageBugowner.bugowner_email`
- **Group bugowner**: the user's email matches
  `PackageBugownerMember.email` for a group-type `PackageBugowner`

The match is automatic — no manual configuration. If a user is not a
bugowner of any package, the dashboard shows empty states in all sections.

### Group Visibility

When a package has a group bugowner, **all members** of that group see the
same information for that package across all sections (Pending Fixes, In
Progress, Completed). The dashboard is package-centric: it shows the state
of packages the user is responsible for, regardless of which individual
performed a specific action.

When a group member submits a fix (SR created), the corresponding
codestream moves from "Pending Fixes" to "In Progress" for all members of
the group.

## Pages

### Maintainer Dashboard

**Route**: `/my-packages`

**Navigation**: always visible in the main sidebar/navbar for all
authenticated users.

A tabbed page with three tabs:

| Tab              | Content                                              |
|------------------|------------------------------------------------------|
| Pending Fixes    | Codestreams needing a fix, no SR submitted yet       |
| In Progress      | Submissions in flight (SR open → incident → RR open) |
| Completed        | Recently released fixes                              |

Each tab shows a counter with the number of items (e.g.,
`Pending Fixes (12)`).

All three tabs are always visible. When a tab has no items, the table
body shows an empty state message.

#### Tab: Pending Fixes

Shows codestreams where:

1. The user is bugowner of the package (direct or via group membership)
2. The `TicketPackageCodestream.status` is `AFFECTED`
3. The parent ticket status is `Analyzed` (the VA has confirmed that
   fixes are needed)
4. There is no `SubmissionRequest` correlated to this
   `TicketPackageCodestream` record (via `SubmissionRequestCodestream`
   join table), OR all correlated SRs are in a final negative state
   (`revoked`, `superseded`, `declined` with no reopen)

These represent actionable work: the maintainer needs to submit a fix.

**Table columns:**

| Column      | Description                                            |
|-------------|--------------------------------------------------------|
| Package     | Source package name (monospace)                         |
| CVE         | CVE-ID (monospace), links to ticket detail page        |
| Severity    | Color-coded severity badge                             |
| Codestream  | Target codestream name (e.g., `SLE-15-SP6`)            |
| Waiting     | Time since the ticket entered `Analyzed` status        |

**Default sort**: severity descending, then waiting time descending (most
urgent and oldest items first).

**Filters**: package name (text/select filter).

**Empty state**: "No pending fixes. All your packages are up to date."

#### Tab: In Progress

Shows codestreams where:

1. The user is bugowner of the package (direct or via group membership)
2. There is at least one active `SubmissionRequest` correlated to this
   `TicketPackageCodestream` (via `SubmissionRequestCodestream`) in a
   non-final or progressing state

The "Progress" column shows the most advanced submission chain for that
codestream, displayed in the same visual style as the ticket detail page
(see `docs/features/submission-tracking.md`, UI section):

- `SR#XXXXX` — SR submitted, pending review (state: `open`)
- `SR#XXXXX → SM#XXXXX` — SR accepted, incident created, no RR yet
- `SR#XXXXX → SM#XXXXX → RR#XXXXX` — RR created, pending QA/release

Each element in the chain is color-coded by state (yellow = open/review,
green = accepted, red = declined/revoked, grey = incident number).

**Table columns:**

| Column      | Description                                            |
|-------------|--------------------------------------------------------|
| Package     | Source package name (monospace)                         |
| CVE         | CVE-ID (monospace), links to ticket detail page        |
| Codestream  | Target codestream name                                 |
| Progress    | Visual chain: SR → SM → RR with state badges           |
| Since       | Time since the first SR in this chain was created      |

**Default sort**: by `since` descending (oldest submissions first — those
potentially stuck).

**Filters**: package name (text/select filter).

**Empty state**: "No submissions in progress."

#### Tab: Completed

Shows codestreams where:

1. The user is bugowner of the package (direct or via group membership)
2. The `TicketPackageCodestream.status` is `RELEASED`, OR
3. There is a `ReleaseRequest` in `accepted` state correlated to this
   codestream

These represent finished work.

**Table columns:**

| Column      | Description                                            |
|-------------|--------------------------------------------------------|
| Package     | Source package name (monospace)                         |
| CVE         | CVE-ID (monospace), links to ticket detail page        |
| Codestream  | Target codestream name                                 |
| Progress    | Full chain: SR → SM → RR ✓ (all green/accepted)        |
| Released    | Date the fix was released (derived from the accepted `ReleaseRequest.updated_at`, or `TicketPackageCodestream.updated_at` when status is `RELEASED` if no RR exists) |

**Default sort**: by released date descending (most recent first).

**Filters**:
- Package name (text/select filter)
- Date range (default: last 30 days; options: 30d, 90d, 1 year, all)

**Empty state**: "No completed releases in the selected time range."

---

### Per-Ticket Maintainer View

**Route**: `/my-packages/ticket/:ticketId`

**Navigation**: not present in the sidebar/navbar. Reachable via:

- **Direct link**: the VA copies the URL and sends it to the maintainer
  (primary use case)
- **Ticket detail page**: a "Copy maintainer link" action available to VAs
  when the ticket is in `Analyzed` status. Copies this URL to the
  clipboard. This action is always available regardless of whether the
  viewing VA is a bugowner — the purpose is to share the link with the
  actual maintainer.
- **Future**: STAMP may use this URL in automated notifications to
  bugowners

This page shows the work for a **single ticket**, filtered to only the
packages where the viewing user is bugowner (direct or via group). If the
ticket contains packages the user does not maintain, those are not shown.

#### Layout

A header card with ticket information, followed by three vertically
stacked cards (Pending Fixes, In Progress, Completed).

#### Header

- **Ticket ID**: `STAMP-{n}` (monospace), with CVE-ID if present
  (e.g., `STAMP-42 — CVE-2026-1234`)
- **Severity**: color-coded badge
- **Ticket status**: badge showing current ticket status

#### Card: Pending Fixes

Shows codestreams for this ticket where the user is bugowner, the
codestream status is `AFFECTED`, and no active SR exists.

**Table columns:**

| Column      | Description                                            |
|-------------|--------------------------------------------------------|
| Package     | Source package name (monospace)                         |
| Codestream  | Target codestream name                                 |
| Status      | Codestream status badge (`AFFECTED`)                   |

**Empty state**: "No pending fixes for this ticket."

#### Card: In Progress

Shows codestreams for this ticket where the user is bugowner and there
is an active submission chain.

**Table columns:**

| Column      | Description                                            |
|-------------|--------------------------------------------------------|
| Package     | Source package name (monospace)                         |
| Codestream  | Target codestream name                                 |
| Progress    | Visual chain: SR → SM → RR with state badges           |

**Empty state**: "No submissions in progress for this ticket."

#### Card: Completed

Shows codestreams for this ticket where the user is bugowner and the
codestream is `RELEASED` or has an accepted RR.

**Table columns:**

| Column      | Description                                            |
|-------------|--------------------------------------------------------|
| Package     | Source package name (monospace)                         |
| Codestream  | Target codestream name                                 |
| Progress    | Full chain: SR → SM → RR ✓                             |
| Released    | Release date (from accepted RR `updated_at`, or `TicketPackageCodestream.updated_at` when `RELEASED`) |

**Empty state**: "No completed releases for this ticket."

#### Error States

When the per-ticket page cannot show the normal view (header + three
cards), it displays a **minimal status page** instead: an icon, a title,
a short message, and a "Back to My Packages" link pointing to
`/my-packages`. No ticket header or details are exposed. See
`docs/features/tickets.md` for ticket statuses and soft-delete behavior.

**Evaluation order** (first match wins):

1. Ticket does not exist → "not found" (404)
2. Ticket is soft-deleted → "removed" (410)
3. Ticket status is not `Analyzed` → status-specific message
4. User is not a bugowner of any package in the ticket → "no packages"
5. All checks pass → normal view

**Messages by scenario:**

| Condition | HTTP | Icon | Title | Message |
|-----------|------|------|-------|---------|
| Ticket does not exist | 404 | `CircleX` | Ticket not found | The ticket you're looking for doesn't exist or may have been removed. |
| Ticket is soft-deleted | 410 | `CircleX` | Ticket removed | This ticket has been removed and is no longer accessible. Contact the security team if you need more information. |
| Ticket status is `New` or `Analysis` | 200 | `Clock` | Ticket not yet analyzed | This ticket is still being evaluated by the security team. Check back later. |
| Ticket status is `Resolved` | 200 | `CheckCircle` | Ticket resolved | All fixes for this ticket have been completed. No further action is needed. |
| Ticket status is `Ignored` | 200 | `EyeOff` | Ticket ignored | This security issue has been evaluated and does not require action. No fixes are needed. |
| Ticket status is `Duplicated` | 200 | `Copy` | Ticket is a duplicate | This ticket has been marked as a duplicate. See the original ticket: \[STAMP-{n}\](/my-packages/ticket/{duplicate_of_id}). |
| User is not a bugowner | 200 | `ShieldAlert` | No packages assigned to you | You are not listed as a maintainer for any package in this ticket. If you believe this is an error, contact the security team. |

**Implementation notes:**

- **Soft-deleted tickets**: the per-ticket API endpoint returns 410 Gone
  for non-admin users, consistent with all other ticket endpoints (see
  `docs/features/tickets.md`).
- **Duplicated link**: the message includes a clickable link to the
  original ticket's per-ticket view (`/my-packages/ticket/{duplicate_of_id}`).
  If the original ticket is also in an abnormal state, the user will see
  the corresponding status page for that ticket — no special handling.
- **Presentation**: all error states share a single component layout
  (icon + title + message + back link), varying only the content.

## API Endpoints

### GET /api/v1/my/packages/pending

Returns pending fixes for the authenticated user.

**Query parameters:**

| Parameter  | Type    | Default | Description                         |
|------------|---------|---------|-------------------------------------|
| package    | string  | —       | Filter by package name              |
| page       | integer | 1       | Page number                         |
| per_page   | integer | 20      | Items per page                      |
| sort_by    | string  | severity| Sort field: severity, waiting       |
| sort_order | string  | desc    | Sort direction: asc, desc           |

**Response**: paginated list of pending fix items.

**Response item schema:**

```json
{
  "package_name": "kernel-default",
  "ticket_id": "550e8400-e29b-41d4-a716-446655440000",
  "ticket_sequence_id": 42,
  "cve_id": "CVE-2026-1234",
  "severity": "high",
  "codestream_name": "SLE-15-SP6",
  "analyzed_at": "2026-04-15T10:30:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| package_name | string | Source package name |
| ticket_id | uuid | Ticket UUID |
| ticket_sequence_id | integer | Ticket sequence number (for `STAMP-{n}` display) |
| cve_id | string \| null | CVE identifier (null if ticket has no CVE) |
| severity | string | Resolved severity: critical, high, moderate, low |
| codestream_name | string | Target codestream name |
| analyzed_at | datetime | When the ticket entered `Analyzed` status (frontend computes "Waiting" from this) |

### GET /api/v1/my/packages/in-progress

Returns in-progress submissions for the authenticated user.

**Query parameters:**

| Parameter  | Type    | Default | Description                         |
|------------|---------|---------|-------------------------------------|
| package    | string  | —       | Filter by package name              |
| page       | integer | 1       | Page number                         |
| per_page   | integer | 20      | Items per page                      |
| sort_by    | string  | since   | Sort field: since, package          |
| sort_order | string  | desc    | Sort direction: asc, desc           |

**Response**: paginated list of in-progress items with submission chain
details.

**Response item schema:**

```json
{
  "package_name": "kernel-default",
  "ticket_id": "550e8400-e29b-41d4-a716-446655440000",
  "ticket_sequence_id": 42,
  "cve_id": "CVE-2026-1234",
  "codestream_name": "SLE-15-SP6",
  "submission_chain": {
    "sr": {"number": 12345, "state": "accepted"},
    "incident": {"number": 67890},
    "rr": {"number": 11111, "state": "open"}
  },
  "first_sr_created_at": "2026-04-20T08:00:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| package_name | string | Source package name |
| ticket_id | uuid | Ticket UUID |
| ticket_sequence_id | integer | Ticket sequence number |
| cve_id | string \| null | CVE identifier |
| codestream_name | string | Target codestream name |
| submission_chain | object | Most advanced submission chain for this codestream |
| submission_chain.sr | object | Submission request: `number` (int), `state` (string) |
| submission_chain.incident | object \| null | Maintenance incident: `number` (int) |
| submission_chain.rr | object \| null | Release request: `number` (int), `state` (string) |
| first_sr_created_at | datetime | When the first SR was created (frontend computes "Since") |

### GET /api/v1/my/packages/completed

Returns completed releases for the authenticated user.

**Query parameters:**

| Parameter  | Type    | Default  | Description                        |
|------------|---------|----------|------------------------------------|
| package    | string  | —        | Filter by package name             |
| days       | integer | 30       | Number of days to look back        |
| page       | integer | 1        | Page number                        |
| per_page   | integer | 20       | Items per page                     |
| sort_by    | string  | released | Sort field: released, package      |
| sort_order | string  | desc     | Sort direction: asc, desc          |

**Response**: paginated list of completed items with full submission chain
and release date.

**Response item schema:**

```json
{
  "package_name": "kernel-default",
  "ticket_id": "550e8400-e29b-41d4-a716-446655440000",
  "ticket_sequence_id": 42,
  "cve_id": "CVE-2026-1234",
  "codestream_name": "SLE-15-SP6",
  "submission_chain": {
    "sr": {"number": 12345, "state": "accepted"},
    "incident": {"number": 67890},
    "rr": {"number": 11111, "state": "accepted"}
  },
  "released_at": "2026-04-25T14:00:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| package_name | string | Source package name |
| ticket_id | uuid | Ticket UUID |
| ticket_sequence_id | integer | Ticket sequence number |
| cve_id | string \| null | CVE identifier |
| codestream_name | string | Target codestream name |
| submission_chain | object | Full submission chain (all elements in accepted/final state) |
| released_at | datetime | When the fix was released |

### GET /api/v1/my/packages/ticket/{ticket_id}

Returns all three sections (pending, in-progress, completed) for a
specific ticket, filtered to the authenticated user's packages.

**Status codes:**

| Code | Condition |
|------|-----------|
| 200  | Ticket exists — response contains either normal data or an error state object |
| 404  | Ticket does not exist |
| 410  | Ticket is soft-deleted (non-admin caller) |

**Response (normal view)**: returned when ticket status is `Analyzed` and
the user is a bugowner of at least one package. Object with three arrays
using a reduced item schema (ticket-level fields like `severity` and
`cve_id` are excluded since they are available from the ticket header):

```json
{
  "pending": [
    {
      "package_name": "kernel-default",
      "codestream_name": "SLE-15-SP6",
      "status": "AFFECTED"
    }
  ],
  "in_progress": [
    {
      "package_name": "kernel-default",
      "codestream_name": "SLE-15-SP6",
      "submission_chain": {
        "sr": {"number": 12345, "state": "accepted"},
        "incident": {"number": 67890},
        "rr": null
      },
      "first_sr_created_at": "2026-04-20T08:00:00Z"
    }
  ],
  "completed": [
    {
      "package_name": "kernel-default",
      "codestream_name": "SLE-15-SP6",
      "submission_chain": {
        "sr": {"number": 12345, "state": "accepted"},
        "incident": {"number": 67890},
        "rr": {"number": 11111, "state": "accepted"}
      },
      "released_at": "2026-04-25T14:00:00Z"
    }
  ]
}
```

No pagination (a single ticket has a bounded number of codestreams).

**Response (error state)**: returned when ticket status is not `Analyzed`
or the user is not a bugowner. Object with an `error_state` key:

```json
{
  "error_state": {
    "icon": "Clock",
    "title": "Ticket not yet analyzed",
    "message": "This ticket is still being evaluated by the security team. Check back later.",
    "link": null
  }
}
```

The `link` field is populated only for the `Duplicated` case, containing
the path to the original ticket's per-ticket view
(`/my-packages/ticket/{duplicate_of_id}`).

## Security

- All endpoints require authentication
- No role restriction — any authenticated user can access their own
  maintainer dashboard
- Users can only see data for packages they are bugowner of (enforced
  server-side via email matching)
- The per-ticket view (`/my-packages/ticket/:ticketId`) filters by the
  authenticated user's packages; users cannot see other maintainers'
  pending work through this endpoint
- The "Copy maintainer link" action in the ticket detail is available to
  Vulnerability Analysts (users with VA role)

## UI Components

The dashboard uses existing UI components from the design system:

- `Tabs` — for the three-tab layout on `/my-packages`
- `Card` — for the three vertically stacked sections on the per-ticket
  view, and for the ticket header
- `Table` — for listing items within each tab/card
- `Badge` — for severity, status, and submission state indicators
- Submission chain display — reuses the same visual component from the
  ticket detail affectedness tree (see `docs/features/submission-tracking.md`)

### Visual Emphasis

- **Pending Fixes tab/card**: standard styling. The counter on the tab
  provides urgency context
- **In Progress tab/card**: standard styling
- **Completed tab/card**: muted text color for a deemphasized appearance

### Empty States

All sections always render (never hidden). Empty state messages:

| Section        | Message                                              |
|----------------|------------------------------------------------------|
| Pending Fixes  | "No pending fixes. All your packages are up to date."|
| In Progress    | "No submissions in progress."                        |
| Completed      | "No completed releases in the selected time range."  |
| Per-ticket Pending  | "No pending fixes for this ticket."             |
| Per-ticket Progress | "No submissions in progress for this ticket."   |
| Per-ticket Completed| "No completed releases for this ticket."        |

## Performance Considerations

The "Pending Fixes" query involves a multi-table join:

```
User.email
  → PackageBugowner (bugowner_email) OR PackageBugownerMember (email)
    → TicketPackageCodestream (package_name match, status = AFFECTED)
      → Ticket (status = Analyzed)
        → LEFT JOIN SubmissionRequestCodestream (absence of active SR)
```

For users who maintain many packages (e.g., kernel team), this could
return a significant number of rows. Mitigation strategies:

- **Pagination**: all endpoints are paginated (default 20 items per page)
- **Indexes**: ensure indexes exist on `PackageBugowner.bugowner_email`,
  `PackageBugownerMember.email`, `TicketPackageCodestream.package_name`,
  and `Ticket.status`
- **Future**: if performance becomes an issue, consider a materialized
  view or periodic pre-computation of the maintainer work queue

## Future Considerations

- **Notifications**: automated email or chat notifications to bugowners
  when new pending fixes appear, linking to the per-ticket view
- **Claim mechanism**: for group bugowners, ability to "claim" a pending
  fix to signal to other group members that someone is working on it
- **Metrics**: aggregate statistics (average time to fix, submission
  success rate) per maintainer or team
