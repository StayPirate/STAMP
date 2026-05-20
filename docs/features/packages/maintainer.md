# Maintainer Operations

## Purpose

Provide package maintainers (bugowners) with API access to their pending
work, in-progress submissions, and completed releases across all packages
they maintain. This gives maintainers immediate visibility into what needs
fixing, what is already in the pipeline, and what has been recently
released — without requiring them to search through individual tickets.

Additionally, a per-ticket endpoint allows Vulnerability Analysts to share
a focused link with a maintainer showing exactly what needs to be done for
a specific ticket.

## Target Audience

This feature targets **package maintainers (bugowners)** — SUSE employees
who maintain one or more source packages in IBS. It complements the
VA-focused ticket workflow by providing a package-centric perspective on
security update work.

## User Identification

A user is identified as a maintainer of a package through the existing
`PackageBugowner` data (see `docs/features/packages/package-bugowner.md`):

- **Person bugowner**: the user's email matches
  `PackageBugowner.bugowner_email`
- **Group bugowner**: the user's email matches
  `PackageBugownerMember.email` for a group-type `PackageBugowner`

The match is automatic — no manual configuration. If a user is not a
bugowner of any package, all endpoints return empty results.

### Group Visibility

When a package has a group bugowner, **all members** of that group see the
same information for that package across all sections (Pending Fixes, In
Progress, Completed). The data is package-centric: it shows the state of
packages the user is responsible for, regardless of which individual
performed a specific action.

When a group member submits a fix (SR created), the corresponding
codestream moves from "Pending Fixes" to "In Progress" for all members of
the group.

## Filtering Criteria

### Pending Fixes

Codestreams where:

1. The user is bugowner of the package (direct or via group membership)
2. The `TicketPackageTrack.status` is `AFFECTED`
3. The parent ticket status is `Analyzed` (the VA has confirmed that
   fixes are needed)
4. There is no `SubmissionRequest` correlated to this
   `TicketPackageTrack` record (via `SubmissionRequestTrack`
   join table), OR all correlated SRs are in a final negative state
   (`revoked`, `superseded`, `declined` with no reopen)

These represent actionable work: the maintainer needs to submit a fix.

### In Progress

Codestreams where:

1. The user is bugowner of the package (direct or via group membership)
2. There is at least one active `SubmissionRequest` correlated to this
   `TicketPackageTrack` (via `SubmissionRequestTrack`) in a
   non-final or progressing state

### Completed

Codestreams where:

1. The user is bugowner of the package (direct or via group membership)
2. The `TicketPackageTrack.status` is `FIXED` with
   `delivery_status = RELEASED`, OR
3. There is a `ReleaseRequest` in `accepted` state correlated to this
   track

These represent finished work.

## Per-Ticket View

The per-ticket endpoint returns all three sections (pending, in-progress,
completed) for a specific ticket, filtered to only the packages where the
requesting user is bugowner (direct or via group). Packages in the ticket
that the user does not maintain are excluded.

### Evaluation Order

When the per-ticket endpoint cannot return the normal three-section
response, it returns an error state instead. Evaluation order (first match
wins):

1. Ticket does not exist → 404 `TICKET_NOT_FOUND`
2. Ticket is soft-deleted → 410 `TICKET_DELETED`
3. Ticket status is not `Analyzed` → 200 with `error_state` (status-specific)
4. User is not a bugowner of any package in the ticket → 200 with `error_state`
5. All checks pass → 200 with normal data

**Error state conditions:**

| Condition | HTTP | Error state type |
|-----------|------|------------------|
| Ticket does not exist | 404 | — (standard error response) |
| Ticket is soft-deleted | 410 | — (standard error response) |
| Ticket status is `New` or `Analysis` | 200 | `not_analyzed` |
| Ticket status is `Resolved` | 200 | `resolved` |
| Ticket status is `Ignored` | 200 | `ignored` |
| Ticket status is `Duplicated` | 200 | `duplicated` (includes `duplicate_of_id`) |
| User is not a bugowner | 200 | `no_packages` |

**Duplicated link**: the `duplicate_of_id` value in API responses is
always the resolved canonical target (a non-Duplicated ticket).

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

**Response**: paginated list of pending fix items using the standard
`{"data": [...], "meta": {"total", "page", "per_page"}}` envelope.

**Response item schema:**

```json
{
  "package_name": "kernel-default",
  "ticket_id": "550e8400-e29b-41d4-a716-446655440000",
  "ticket_sequence_id": 42,
  "cve_id": "CVE-2026-1234",
  "severity": "high",
  "reference": "SLE-15-SP6",
  "analyzed_at": "2026-04-15T10:30:00Z"
}
```

| Field | Type | Description |
|-------|------|-------------|
| package_name | string | Source package name |
| ticket_id | uuid | Ticket UUID |
| ticket_sequence_id | integer | Ticket sequence number (for `SNTL-{n}` display) |
| cve_id | string \| null | CVE identifier (null if ticket has no CVE) |
| severity | string | Resolved severity: critical, high, moderate, low |
| reference | string | Target codestream name |
| analyzed_at | datetime | When the ticket entered `Analyzed` status (consumers compute "Waiting" from this) |

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
details, using the standard `{"data": [...], "meta": {"total", "page", "per_page"}}` envelope.

**Response item schema:**

```json
{
  "package_name": "kernel-default",
  "ticket_id": "550e8400-e29b-41d4-a716-446655440000",
  "ticket_sequence_id": 42,
  "cve_id": "CVE-2026-1234",
  "reference": "SLE-15-SP6",
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
| reference | string | Target codestream name |
| submission_chain | object | Most advanced submission chain for this codestream |
| submission_chain.sr | object | Submission request: `number` (int), `state` (string) |
| submission_chain.incident | object \| null | Maintenance incident: `number` (int) |
| submission_chain.rr | object \| null | Release request: `number` (int), `state` (string) |
| first_sr_created_at | datetime | When the first SR was created (consumers compute "Since") |

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
and release date, using the standard `{"data": [...], "meta": {"total", "page", "per_page"}}` envelope.

**Response item schema:**

```json
{
  "package_name": "kernel-default",
  "ticket_id": "550e8400-e29b-41d4-a716-446655440000",
  "ticket_sequence_id": 42,
  "cve_id": "CVE-2026-1234",
  "reference": "SLE-15-SP6",
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
| reference | string | Target codestream name |
| submission_chain | object | Full submission chain (all elements in accepted/final state) |
| released_at | datetime | When the fix was released |

### GET /api/v1/my/packages/ticket/{ticket_id}

Returns all three sections (pending, in-progress, completed) for a
specific ticket, filtered to the authenticated user's packages.

**Status codes:**

| Code | Error Code | Condition |
|------|------------|-----------|
| 200  | — | Ticket exists — response contains either normal data or an error state object |
| 404  | `TICKET_NOT_FOUND` | Ticket does not exist |
| 410  | `TICKET_DELETED` | Ticket is soft-deleted (non-admin caller) |

**Response (normal view)**: returned when ticket status is `Analyzed` and
the user is a bugowner of at least one package. Object with three arrays
using a reduced item schema (ticket-level fields like `severity` and
`cve_id` are excluded since they are available from the ticket header):

```json
{
  "data": {
    "pending": [
      {
        "package_name": "kernel-default",
        "reference": "SLE-15-SP6",
        "status": "AFFECTED"
      }
    ],
    "in_progress": [
      {
        "package_name": "kernel-default",
        "reference": "SLE-15-SP6",
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
        "reference": "SLE-15-SP6",
        "submission_chain": {
          "sr": {"number": 12345, "state": "accepted"},
          "incident": {"number": 67890},
          "rr": {"number": 11111, "state": "accepted"}
        },
        "released_at": "2026-04-25T14:00:00Z"
      }
    ]
  }
}
```

No pagination (a single ticket has a bounded number of codestreams).

**Response (error state)**: returned when ticket status is not `Analyzed`
or the user is not a bugowner. Object with an `error_state` key:

```json
{
  "data": {
    "error_state": {
      "type": "not_analyzed",
      "duplicate_of_id": null
    }
  }
}
```

The `duplicate_of_id` field is populated only for the `duplicated` type,
containing the UUID of the original ticket.

## Security

- All endpoints require authentication
- No role restriction — any authenticated user can access their own
  maintainer data
- Users can only see data for packages they are bugowner of (enforced
  server-side via email matching)
- The per-ticket endpoint filters by the authenticated user's packages;
  users cannot see other maintainers' pending work through this endpoint

## Performance Considerations

The "Pending Fixes" query involves a multi-table join:

```
User.email
  → PackageBugowner (bugowner_email) OR PackageBugownerMember (email)
    → TicketPackageTrack (package_name match, status = AFFECTED)
      → Ticket (status = Analyzed)
        → LEFT JOIN SubmissionRequestTrack (absence of active SR)
```

For users who maintain many packages (e.g., kernel team), this could
return a significant number of rows. Mitigation strategies:

- **Pagination**: all endpoints are paginated (default 20 items per page)
- **Indexes**: ensure indexes exist on `PackageBugowner.bugowner_email`,
  `PackageBugownerMember.email`, `TicketPackageTrack.package_name`,
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

## Open Points

1. **Soft-deleted packages in queries**: the endpoint queries (Pending
   Fixes, In Progress, Completed) do not explicitly filter out
   soft-deleted `TicketPackage` records
   (`TicketPackage.deleted_at IS NULL`). Soft-deleted packages are
   excluded from a ticket and should not appear in the maintainer's
   work queue. While the filtering conditions (status, SR existence)
   may implicitly exclude most soft-deleted records, an explicit filter
   should be added for correctness.

## Dependencies

- `docs/features/packages/package-bugowner.md` — bugowner resolution and
  group membership data
- `docs/features/packages/package-model.md` — TicketPackageTrack status
  and delivery status model
- `docs/features/packages/ibs-submission-tracking.md` — SubmissionRequest
  and ReleaseRequest records for in-progress/completed views
- `docs/features/tickets/tickets.md` — ticket status lifecycle and
  confidentiality model

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
