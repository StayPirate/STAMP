# Tickets

## Purpose

Define the Ticket entity — the primary workflow unit of Sentinel. A ticket
tracks the triage, analysis, and resolution of a security issue across
maintained products. Tickets may or may not be associated with a CVE.

This specification is the authoritative source for ticket identification,
creation pathways, lifecycle, severity resolution, and status transition
rules. Other feature specifications reference this document for
ticket-related behavior.

## Ticket Identification

Every ticket has two identifiers:

| Identifier | Format | Purpose |
|------------|--------|---------|
| `id` | UUID | Internal primary key, used in all foreign key relationships and API paths |
| `sequence_id` | Auto-increment integer, exposed as `SNTL-{n}` | Human-readable identifier for UI display, search, communication, and API lookup |

### SNTL-{n} Format

- The `sequence_id` is an auto-increment integer assigned at ticket
  creation. It is unique and immutable.
- The human-readable form is `SNTL-{sequence_id}` (e.g., `SNTL-1`,
  `SNTL-42`, `SNTL-1337`). No zero-padding.
- `SNTL-{n}` is the primary label shown in ticket lists, detail pages,
  logs, events, and external communications.
- For tickets with an associated CVE, the UI shows both identifiers:
  `SNTL-42 (CVE-2024-1234)`.
- For tickets without a CVE, only `SNTL-{n}` is shown.

### API Dual Lookup

All API endpoints that accept a `{ticket_id}` path parameter support
dual lookup:

- **UUID**: `GET /api/v1/tickets/a1b2c3d4-...` — standard UUID lookup
- **SNTL-{n}**: `GET /api/v1/tickets/SNTL-42` — resolved via
  `sequence_id` lookup

The backend detects the format automatically (UUIDs contain hyphens and
hex characters; `SNTL-{n}` starts with the literal prefix `SNTL-`).

### Search

The `search` query parameter on `GET /api/v1/tickets` searches across:

- `SNTL-{n}` identifier (exact or partial match on the numeric part)
- CVE ID (if the ticket has an associated CVE)
- CVE description
- Package names

## CVE Association

A ticket may optionally be associated with a CVE.

- `Ticket.cve_id`: UUID, FK to `cve.id`, **UNIQUE**, **NULLABLE**
- The UNIQUE constraint ensures that a CVE can be associated with at most
  one ticket (1:0..1 relationship)
- Tickets created from CVE ingestion have `cve_id` set at creation time
- Tickets created manually or from external sources (e.g., bug trackers)
  start without a CVE

### Associating a CVE Later

An VA can associate a CVE with a ticket that does not yet have one, via
`POST /api/v1/tickets/{ticket_id}/associate-cve`.

**Rules**:

- The ticket must not already have a CVE associated (`cve_id IS NULL`)
- The CVE-ID string must be provided (e.g., `CVE-2024-1234`)
- If the CVE exists in the Sentinel database and is already associated with
  another ticket, the API returns 409 Conflict with
  `existing_ticket_id` in the response body (see
  [Associate CVE endpoint](#associate-cve) for details)
- If the CVE does not exist in the Sentinel database, Sentinel creates a
  minimal CVE record (only `cve_id` set) and triggers on-demand
  single-CVE fetch in the background (see
  `docs/features/tickets/cve-tracking.md`, "On-demand Single-CVE Fetch"). The
  association proceeds immediately with the minimal CVE record. The API
  response includes `cve_data_pending: true`
- When a CVE is associated:
  - `Ticket.cve_id` is set
  - The automatic severity from CVSS takes over (see
    [Severity Resolution](#severity-resolution)) — initially `None` if
    the CVE data has not been fetched yet; updated automatically once
    CVSS data arrives from the on-demand fetch
  - A `TicketAuditEvent` with `event_type = cve_associated` is created
  - CVSS sync and release tracking begin applying to the ticket

### Dissociating a CVE

Dissociating a CVE from a ticket is restricted to the **Admin role**.
Vulnerability Analysts cannot remove a CVE from a ticket. If an VA believes
a CVE was associated in error, they should request an Admin to remove it.

An Admin can remove a CVE from a ticket via
`DELETE /api/v1/tickets/{ticket_id}/cve`.

**Effects**:

- `Ticket.cve_id` is set to `NULL`
- Severity resolution falls back to `severity_override` (see
  [Severity Resolution](#severity-resolution)). If `severity_override`
  is also `NULL`, the ticket severity becomes `None`
- A `TicketAuditEvent` with `event_type = cve_removed` is created (see
  `docs/features/tickets/ticket-audit-log.md`)
- CVSS sync and release tracking cease applying to the ticket
- Existing `TicketPackageTrack` and `TicketPackageProduct` records
  are preserved. However, without an associated CVE, automatic release
  detection (both track-level and product-level) cannot function —
  there is no CVE-ID to match in IBS diffs or `updateinfo.xml`
  advisories. The VA must manually set these records to a final status
  (`FIXED`, `NOT_AFFECTED`, or `WONT_FIX`) or soft-delete them for the
  ticket to progress toward Resolved. If a CVE is later re-associated
  with the ticket (via `POST .../associate-cve`), automatic release
  detection resumes
- `evaluate_ticket_status` is called after the dissociation: if severity
  becomes `None` and the Analyzed gate requires severity, the ticket may
  regress to Analysis
- The CVE record itself is not deleted — it remains in the database.
  If no other ticket references this CVE, a subsequent CVE sync will
  create a new ticket for it — this is intentional to ensure CVEs are
  not lost. If the Admin intends to re-associate the CVE with a
  different ticket, this should be done before the next sync cycle

**Required role**: Admin.

## Ticket Creation

### Automatic: CVE Ingestion

When a CVE is ingested from an external source (NVD, MITRE, or future
sources), a ticket is created automatically. See
`docs/features/tickets/cve-tracking.md` for the full ingestion flow.

- `cve_id`: set to the ingested CVE
- `status`: `New`
- `assignee_id`: `NULL`
- `TicketAuditEvent`: `event_type = ticket_created`, `user_id = NULL`,
  `comment` = fetcher source description (e.g., `"CVE ingested from NVD"`)

### Automatic: Codestream Release Detection (Case C)

When the `IBSTrackReleaseDetector` finds a CVE fix in IBS for a CVE
that has no ticket in Sentinel, a `create_ticket_from_detection` task
creates the ticket. See `docs/features/packages/ibs-track-release-detection.md`
(Case C) for the full flow.

- `cve_id`: set to the created/fetched CVE
- `status`: `New`
- `assignee_id`: `NULL`
- `TicketAuditEvent`: `event_type = ticket_created`, `user_id = NULL`,
  `comment` = detection context

### Manual Creation

An Vulnerability Analyst can create a ticket manually via
`POST /api/v1/tickets` or through the UI.

- `cve_id`: optionally, the VA may specify a CVE-ID string (e.g.,
  `"CVE-2024-1234"`) at creation time. If omitted, the ticket is
  created without a CVE (can be associated later)
- When a CVE-ID is provided:
  - If the CVE exists in the database and is already associated with
    another ticket, the creation fails with 409 Conflict and
    `existing_ticket_id` in the response body
  - If the CVE does not exist in the database, Sentinel creates a minimal
    CVE record (only `cve_id` set) and triggers on-demand single-CVE
    fetch in the background (see `docs/features/tickets/cve-tracking.md`,
    "On-demand Single-CVE Fetch"). The ticket is created immediately.
    The API response includes `cve_data_pending: true`
  - If the CVE exists in the database and is not associated with any
    ticket, the ticket is created with that CVE
- `status`: `Analysis` (direct, bypasses `New` — the creating user is
  automatically assigned)
- `assignee_id`: set to the creating user
- Two `TicketAuditEvent` records are created atomically in the same
  transaction (three if a CVE-ID is provided):
  1. `event_type = ticket_created`, `user_id = creating user`,
     `comment = "Ticket created manually"`
  2. `event_type = assignment`, `user_id = creating user`,
     `new_value = creating user's username`
  3. (if CVE-ID provided) `event_type = cve_associated`,
     `user_id = creating user`, `new_value = CVE-ID string`

**Required role**: Vulnerability Analyst.

The UI must provide a mechanism to create tickets manually (button
placement TBD in `docs/features/ui/pages.md`).

### Future: External Sources

The data model supports automatic ticket creation from external systems
(e.g., internal bug trackers). These tickets are created without a CVE
and follow the same rules as automatic creation:

- `cve_id`: `NULL`
- `status`: `New`
- `assignee_id`: `NULL`

Specific integrations will be defined in separate feature specifications.

## Severity Resolution

Ticket severity is resolved transparently — the API and UI expose a
single `severity` field. The resolution logic is internal to the service
layer.

### Resolution Rules

1. If the ticket has a CVE (`cve_id IS NOT NULL`): severity =
   `cve.severity` (derived from CVSS assessments via the resolution
   cascade — see `docs/features/tickets/cvss-scoring.md`)
2. If the ticket does not have a CVE (`cve_id IS NULL`): severity =
   `ticket.severity_override`
3. If neither is available: severity = `None` (unknown)

### severity_override Field

- `Ticket.severity_override`: ENUM (Critical, High, Medium, Low, None),
  nullable
- Set manually by the VA via the API or UI
- Only used when `cve_id IS NULL`
- When a CVE is associated later, the automatic severity from CVSS takes
  over and `severity_override` is ignored (but not deleted — it serves
  as a historical record of the VA's initial assessment)

### UI Behavior

- **Ticket with CVE**: severity badge is read-only (derived from CVSS)
- **Ticket without CVE**: severity is editable by the VA (sets
  `severity_override`)
- In both cases, the UI shows a single severity badge — the user is not
  aware of the internal resolution mechanism

## Ticket Lifecycle

### Statuses

| Status     | Description |
|------------|-------------|
| New        | Created automatically (CVE ingestion or external source). Not yet assigned to any VA. |
| Analysis   | Assigned to an VA who is actively analyzing — filling in affectedness data. |
| Analyzed   | All required data has been filled in. Ready for updates to be prepared. |
| Resolved   | Security updates have been released for all affected packages across all products. |
| Ignored    | The issue does not require action. Can only be set from New or Analysis. |
| Duplicated | Duplicate of another ticket. Links to the original. Reversible. |

### Status Transition Diagram

```
                     automatic         automatic
New ──→ Analysis ──────────→ Analyzed ──────────→ Resolved
 │         │    ◄────────────    │    ◄────────────
 │         │     automatic       │     automatic
 ├──→ Ignored (from New or Analysis only)
 │
 └──→ Duplicated (from any state, reversible)
      (also Analysis, Analyzed, Resolved, Ignored → Duplicated)
```

### Status Transitions

| From       | To         | Trigger                                                | Mode               | Who                                    |
|------------|------------|--------------------------------------------------------|--------------------|----------------------------------------|
| New        | Analysis   | VA assigned, or any modifying operation on unassigned ticket | Manual (implicit)  | Any VA                                 |
| New        | Ignored    | VA clicks "Ignore" action                              | Manual             | Any VA                                 |
| New        | Ignored    | NVD rejects the CVE (`vulnStatus = Rejected`)          | Automatic          | System                                 |
| Analysis   | Analyzed   | All "Analyzed" gate conditions met                     | Automatic          | System                                 |
| Analysis   | Ignored    | VA determines issue is not relevant                    | Manual             | Assignee                               |
| Analyzed   | Resolved   | All "Resolved" gate conditions met                     | Automatic          | System                                 |
| Analyzed   | Analysis   | "Analyzed" gate conditions no longer met               | Automatic          | System (triggered by VA or system action) |
| Resolved   | Analyzed   | "Resolved" gate conditions no longer met, but "Analyzed" gates still met | Automatic | System (triggered by VA or system action) |
| Resolved   | Analysis   | Both "Resolved" and "Analyzed" gate conditions no longer met | Automatic    | System (triggered by VA or system action) |
| Any        | Duplicated | VA marks ticket as duplicate                           | Manual             | Any VA                                 |
| Duplicated | (previous) | VA reverts duplicate status                            | Manual             | Any VA (becomes new assignee)          |

### Gate: Analysis → Analyzed

The system automatically transitions a ticket from Analysis to Analyzed
when ALL of the following conditions are met:

1. **At least one package**: the ticket must have at least one package
   added (at least one active `TicketPackageTrack` record exists)
2. **All track affectedness decided**: no active `TicketPackageTrack`
   records in `ANALYSIS` status
3. **All product affectedness decided**: no active `TicketPackageProduct`
   records in `ANALYSIS` status
4. **Severity set**: the ticket must have a determined severity (not
   `None`). For tickets with CVE, this is derived from CVSS. For tickets
   without CVE, `severity_override` must be set by the VA
5. **SUSE CVSS provided** (only for tickets with CVE): the VA must have
   provided BOTH SUSE CVSS v3.1 AND v4.0 assessments (see
   `docs/features/tickets/cvss-scoring.md`)

This evaluation is performed automatically by the centralized status
evaluation function (see "Centralized Status Evaluation" below) after
every operation that modifies gate-relevant data. There is no manual
"Mark as Analyzed" action — the transition happens as soon as all
conditions are satisfied.

Conversely, if any of these conditions ceases to be met (e.g., a package
is added with tracks or products in ANALYSIS, a SUSE CVSS assessment
is deleted, or severity becomes undetermined), the ticket automatically
transitions back from Analyzed to Analysis.

### Gate: Analyzed → Resolved

The system automatically transitions a ticket from Analyzed to Resolved
when ALL of the following conditions are met (only records that are not
effectively excluded are considered — see
`docs/features/packages/package-tracking.md`, "Hierarchical Exclusion
Model"):

1. Every active `TicketPackageTrack` has a terminal affectedness status:
   `FIXED`, `NOT_AFFECTED`, or `WONT_FIX`
2. Every active track with status `FIXED` has
   `delivery_status = RELEASED`
3. Every eligible product (`eligible = true`) under a `FIXED` track has
   `released_at IS NOT NULL` (confirmed receipt of the update)

This evaluation is performed by the centralized status evaluation
function after every operation that modifies package or product statuses.
There is no manual "Mark as Resolved" action.

Conversely, if any of these conditions ceases to be met (e.g., CVSS
recalculation changes product eligibility, or a VA resets a track status
from a final state to `AFFECTED`), the ticket automatically transitions
back from Resolved to Analyzed (or to Analysis, if the "Analyzed" gates
are also no longer met).

### Automatic Status Re-evaluation

Forward and reverse transitions between Analysis, Analyzed, and Resolved
are governed by a single mechanism: the centralized status evaluation
function re-evaluates gate conditions after every relevant data change
and sets the ticket to the highest valid status.

This means reverse transitions are not special cases — they emerge
naturally when gate conditions are no longer met:

- **Analyzed → Analysis**: any "Analyzed" gate ceases to be met (e.g.,
  package added with tracks in ANALYSIS, SUSE CVSS assessment
  deleted, track status reset to ANALYSIS, severity cleared)
- **Resolved → Analyzed**: any resolved gate condition ceases to be met
  while "Analyzed" gates remain met (e.g., CVSS recalculation changes
  product eligibility, causing a previously satisfied gate to fail —
  see `docs/features/tickets/cvss-scoring.md`, Recalculation Cascade)
- **Resolved → Analysis**: both "Resolved" and "Analyzed" gates are
  broken (e.g., a new package is added with tracks in ANALYSIS)

All automatic transitions create a `TicketAuditEvent` with `user_id = NULL`
(system action), even when the underlying data change was initiated by
an VA.

### Centralized Status Evaluation

All automatic status transitions between Analysis, Analyzed, and
Resolved are handled by a single **internal** service-layer function:
`evaluate_ticket_status`. This function is the **sole authority** for
determining a ticket's status based on its current data.

#### Behavior

1. The function receives a ticket and evaluates gate conditions top-down
   (most advanced status first):
   - If all "Resolved" gates AND all "Analyzed" gates are met → status
     is Resolved
   - If all "Analyzed" gates are met (but "Resolved" gates are not) →
     status is Analyzed
   - Otherwise → status is Analysis
2. If the determined status differs from the current status, the function
   updates the ticket and creates a `TicketAuditEvent` with
   `event_type = status_change`
3. The function operates within the **same database transaction** as the
   triggering operation (atomicity guarantee)

#### Scope

The function only evaluates tickets in `Analysis`, `Analyzed`, or
`Resolved` status. Tickets in `New`, `Ignored`, or `Duplicated` are
excluded — these statuses are governed by explicit user actions or
specific system events (e.g., NVD rejection), not by gate evaluation.

#### Ticket Mutations Module

`evaluate_ticket_status` is an **internal implementation detail** — it
is not called directly by other services. Instead, all operations that
modify data relevant to ticket status gates are centralized in a
dedicated service module (`ticket_mutations`). This module exposes
functions for every type of ticket-relevant mutation:

- Track status changes
- Product status changes (including eligibility overrides)
- Product eligibility changes
- CVSS assessment creation, update, and deletion
- Severity changes (`severity_override`)
- Package addition and soft-deletion/restore

Each function in the module calls `evaluate_ticket_status` internally
at the end of the operation, within the same database transaction.
External services (CVSS sync, release detection, package tracking,
API endpoints) MUST use these functions instead of modifying
ticket-related models directly.

**Relationship with `services/cvss.py`**: the CVSS-related functions in
`ticket_mutations` (assessment creation, update, deletion) delegate
CVSS resolution and severity calculation to pure functions in
`services/cvss.py`. The resolution cascade logic is never reimplemented
inside `ticket_mutations`. See `docs/features/tickets/cvss-scoring.md` (Service
Architecture) for the full responsibility split between the two modules.

**Relationship with `add_package_to_ticket`**: the centralized package
addition function defined in `docs/features/packages/package-tracking.md` handles
SMELT resolution and external I/O. It delegates the actual creation of
`TicketPackage`, `TicketPackageTrack`, and `TicketPackageProduct` records
to `ticket_mutations` functions. Similarly, package soft-deletion
delegates record updates to the module. Soft-deletion follows the
hierarchical exclusion model — only the directly targeted record
receives `deleted_at`; child records are not modified. The SMELT query
logic does not belong in `ticket_mutations` — only the record mutations
do.

**Idempotency**: the record creation functions in `ticket_mutations` are
idempotent. If a `TicketPackageTrack` or `TicketPackageProduct`
record already exists for the given combination (including soft-deleted
records), it is skipped without modification. Only missing records are
created.

**Record creation logic**: when `ticket_mutations` creates a new
`TicketPackageTrack` record, the initial status is always `ANALYSIS`
and `delivery_status` is `PENDING`. When it creates a new
`TicketPackageProduct` record, it determines the initial status by
inheriting from the parent `TicketPackageTrack`:

- Parent in `ANALYSIS` → `ANALYSIS`
- Parent in `AFFECTED` → status is set to `AFFECTED`; eligibility is
  calculated separately (CVSS threshold, Reactive LTSS override) and
  stored in the `eligible` boolean
- Parent in any other status (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`) →
  inherit the same status

This logic is internal to `ticket_mutations` — callers (including
`add_package_to_ticket`) do not specify the initial status.

Operations that do NOT modify gate-relevant data (assignment, duplicate
set/remove, CVE association/removal, ticket-level soft-delete/restore)
are NOT required to go through this module — they create `TicketAuditEvent`
records in their own services.

#### Orphan Cleanup Invariants

The `ticket_mutations` module enforces automatic cleanup of empty parent
records. These are generic rules that apply regardless of the trigger —
any current or future feature that soft-deletes a product or track
automatically benefits from these invariants. The orphan rule triggers
**only on soft-deletion events**, not on restore or other mutations.

**Invariant 1 — Track orphan rule**: after every product soft-deletion,
`ticket_mutations` checks whether the parent `TicketPackageTrack` has
zero remaining products with `deleted_at IS NULL` (direct check). If
zero directly-active products remain, the track receives its own
`deleted_at` (direct soft-deletion). Products under the track are NOT
modified — they already have their own `deleted_at`.

**Invariant 2 — Package orphan rule**: after every track soft-deletion,
`ticket_mutations` checks whether the parent `TicketPackage` has zero
remaining tracks with `deleted_at IS NULL` (direct check). If zero
directly-active tracks remain, the package receives its own `deleted_at`.
Tracks and products under the package are NOT modified.

**Cascading composition**: the invariants compose naturally. Soft-deleting
a product may trigger the track orphan rule, which may trigger the
package orphan rule:

```
soft_delete_ticket_package_product(record, user)
  → TicketAuditEvent (product_excluded)
  → evaluate_ticket_status()
  → _enforce_track_orphan_rule()
      → if 0 directly-active products:
          set track.deleted_at (direct)
          → TicketAuditEvent (track_excluded, user_id=NULL)
          → evaluate_ticket_status()
          → _enforce_package_orphan_rule()
              → if 0 directly-active tracks:
                  set package.deleted_at (direct)
                  → TicketAuditEvent (package_excluded, user_id=NULL)
                  → evaluate_ticket_status()
```

Note: orphan-triggered soft-deletions create `TicketAuditEvent` records with
`user_id = NULL` (system action), distinguishing them from VA-initiated
exclusions. Each orphan soft-deletion sets `deleted_at` only on the
parent — no cascade to children (per the hierarchical exclusion model).

Each public function in `ticket_mutations` calls
`evaluate_ticket_status` at the end of its execution. This ensures the
ticket is always in a consistent state after any mutation, regardless of
how many times `evaluate_ticket_status` is called within the same
transaction.

#### Contract

Every service-layer operation that modifies data relevant to ticket
status gates MUST go through the `ticket_mutations` module. Direct
modification of `TicketPackageTrack`, `TicketPackageProduct`,
or `CVECVSSAssessment` records outside this module is a bug — it
bypasses status re-evaluation and may leave the ticket in an
inconsistent state.

Relevant data includes:

- `TicketPackageTrack` records (creation, soft-deletion, status change,
  delivery status change)
- `TicketPackageProduct` records (creation, soft-deletion, status change,
  eligibility change)
- `CVECVSSAssessment` records (creation, update, deletion)
- Ticket severity (`severity_override` or CVSS-derived severity)
- Package addition or soft-deletion/restore

#### Architectural Test Requirement

A parametrized integration test MUST be implemented to verify that
the `ticket_mutations` module produces the correct ticket status after
every type of relevant mutation. The test must cover:

- **Forward transitions**: each gate condition being satisfied one by
  one until the ticket advances (Analysis → Analyzed → Resolved)
- **Backward transitions**: each gate condition being broken after the
  ticket has advanced (Analyzed → Analysis, Resolved → Analyzed,
  Resolved → Analysis)
- **No-op cases**: mutations that do not affect gate conditions
- **Edge cases**: ticket with no packages, ticket without CVE (no SUSE
  CVSS gate), all tracks in final status but severity not set

This test serves as a permanent architectural fitness function: if a
new service operation modifies gate-relevant data without going through
the `ticket_mutations` module, the test will fail because the ticket
status will not match the expected state.

### Reassignment

A ticket can be reassigned to a different VA at any time, regardless of
its current status. Reassignment does not change the ticket status. All
reassignments are logged in the ticket event history.

**Target constraint**: the assignment target MUST be an **active** user
holding the `vulnerability_analyst` role. Attempting to assign a ticket
to a user without this role, or to an inactive user, fails with 400 Bad
Request. This applies to the
explicit assignment endpoint (`POST /assign`); auto-assignment is
inherently safe because only VAs can perform modifying operations on
tickets.

### Auto-Assignment on Unassigned Tickets

When an VA performs any modifying operation on a ticket with
`assignee_id = NULL`, the ticket is automatically assigned to the acting
VA. A `TicketAuditEvent` with `event_type = assignment` is created atomically
in the same transaction as the modifying operation.

If the ticket is in `New` status and the operation does not include an
explicit status change (e.g., marking as duplicate or ignored), the
ticket also transitions to `Analysis`.

If the operation includes an explicit status change (e.g.,
`New → Duplicated` or `New → Ignored`), the status follows the explicit
transition and the assignee is set, but the ticket does not transition
to `Analysis` first.

This rule does not apply to system operations (background tasks,
automated ingestion). Only VA-initiated actions trigger
auto-assignment.

### Duplicate Handling

- Any ticket can be marked as a duplicate of another ticket, from any
  status
- **Target resolution**: when marking ticket A as duplicate of ticket B:
  - If B is in `Duplicated` status, follow the `duplicate_of_id` chain
    until a non-Duplicated ticket is found (the "ultimate original")
  - A maximum chain depth of 10 is enforced; if exceeded, the operation
    fails with 409 Conflict and an ERROR is logged (indicates data
    corruption requiring manual intervention)
  - If the resolved target equals ticket A, the operation fails with
    400 Bad Request ("a ticket cannot be a duplicate of itself")
  - `duplicate_of_id` is set to the resolved target (always a
    non-Duplicated ticket)
- **Cascade update**: when marking ticket B as duplicate of ticket C,
  all existing tickets whose `duplicate_of_id` points to B are
  automatically updated to point to C (the resolved target). For each
  updated ticket, a `TicketAuditEvent` is created with `event_type =
  duplicate_target_changed`, `user_id = NULL` (system action),
  `old_value` = previous original identifier, `new_value` = new
  original identifier
- **Invariant**: `duplicate_of_id` always references a ticket that is
  NOT in `Duplicated` status. Multiple tickets may reference the same
  original.
- When marked as duplicate:
  - `status` is set to `Duplicated`
  - `duplicate_of_id` is set to the resolved target ticket's ID
  - `previous_status` stores the status before duplication
  - If the ticket had no assignee (`assignee_id = NULL`), the acting VA
    becomes the assignee (see
    [Auto-Assignment on Unassigned Tickets](#auto-assignment-on-unassigned-tickets))
- When reverted:
  - `status` is restored to `previous_status`
  - `duplicate_of_id` is cleared
  - `previous_status` is cleared
  - The ticket is reassigned to the VA who performed the revert
  - After restoring the status, `evaluate_ticket_status` is called to
    reconcile the restored status with current gate conditions. If the
    gates for `previous_status` are no longer met (e.g., a CVSS
    assessment was deleted while the ticket was Duplicated), the ticket
    is automatically regressed to the appropriate status (Analysis or
    Analyzed). This may produce two `TicketAuditEvent` records in the same
    transaction: `duplicate_removed` (user action) followed by
    `status_change` (system action)

## Soft-Delete

- Soft-delete is performed by setting `deleted_at` to the current
  timestamp
- Only users with the Admin role may soft-delete or restore tickets
- Soft-deleted tickets (`deleted_at IS NOT NULL`) are invisible to all
  business logic — no operation (API query, service-layer side effect,
  or background task) queries, modifies, or produces side effects for
  soft-deleted tickets unless it explicitly deals with deletion or
  restoration management
- All sub-resources of a soft-deleted ticket remain intact but are
  inaccessible to non-admin users (API returns 410 Gone)
- A soft-deleted ticket can be restored by clearing `deleted_at`
- Both operations create a `TicketAuditEvent` record (see
  `docs/features/tickets/ticket-audit-log.md`)

**Automated verification**: every service-layer operation that queries
tickets as part of its logic MUST include a parametrized test verifying
that soft-deleted tickets are excluded. At minimum:

- Create a ticket in each relevant active status (New, Analysis, Analyzed)
- Soft-delete it (`deleted_at = now()`)
- Execute the operation under test
- Assert the soft-deleted ticket was NOT affected (no TicketAuditEvents
  created, no status changes, no unassignment, no inclusion in results)

### Status Categories

- **Active tickets**: status `New`, `Analysis`, or `Analyzed` AND
  `deleted_at IS NULL`. Actively monitored by background tasks.
- **Inactive tickets**: status `Resolved`, `Ignored`, or `Duplicated`.
  No longer monitored.
- **Soft-deleted tickets**: `deleted_at IS NOT NULL`. Excluded from
  everything regardless of status.

## Tickets Without CVE: Behavioral Differences

When a ticket has no associated CVE (`cve_id IS NULL`), the following
features behave differently:

| Feature | Behavior |
|---------|----------|
| CVSS scoring | Not applicable — no CVE means no CVSS assessments |
| CVSS sync (NVD, Red Hat) | Not applicable — ticket is skipped |
| Severity | Manual via `severity_override` (editable by VA) |
| Release tracking (track) | Not applicable — track-level detection relies on CVE-ID in IBS diffs |
| Release tracking (product) | Not applicable — product-level detection relies on CVE-ID in `updateinfo.xml` |
| NVD rejection handling | Not applicable — no CVE means no `vulnStatus` changes |
| NVD rejection revert handling | Not applicable |
| CVE Information UI section | Hidden |
| CVSS Card UI section | Hidden |
| Gate: SUSE CVSS required | Not applicable — severity is set via `severity_override` instead |
| Critical CVE notification | Not applicable |

Packages, tracks, and products can still be added and managed
normally. The VA can set affectedness statuses and the ticket can
progress through the full lifecycle.

## API Endpoints

### List Tickets

```
GET /api/v1/tickets
```

Lists tickets with filtering, search, pagination, and sorting.

Query parameters:

- `search` (string, optional): free-text search across `SNTL-{n}`
  identifier, CVE ID, CVE description, and package names. See
  [Search](#search) for search behavior across fields.
- `status` (string, repeatable, optional): filter by ticket status.
  Accepts one or more values from: `new`, `analysis`, `analyzed`,
  `resolved`, `ignored`, `duplicated`. When multiple values are provided,
  tickets matching any of the specified statuses are returned.
- `assignee` (string, optional): filter by assignee. Accepts a user UUID,
  a username, or the special value `none` to return only unassigned
  tickets.
- `severity` (string, repeatable, optional): filter by severity level.
  Accepts one or more values from: `critical`, `high`, `medium`, `low`,
  `none`.
- `bugowner` (string, optional): filter tickets to those containing at
  least one package whose bugowner matches the value (matches against
  bugowner email, name, or group member email/userid — see
  `docs/features/packages/package-bugowner.md`).
- `include_deleted` (string, optional): `true` or `only`. Accepted from
  any caller, but soft-deleted tickets are included only if the caller
  holds the Admin role. For non-admin callers the parameter is silently
  ignored. Values: `true` (include active and deleted tickets), `only`
  (return only deleted tickets). Default (absent or `false`): return only
  active tickets.
- `page` (integer, optional): page number for pagination (default: 1).
- `per_page` (integer, optional): items per page (default: 20).
- `sort_by` (string, optional): field to sort by (default: `created_at`).
- `sort_order` (string, optional): `asc` or `desc` (default: `desc`).

Response: paginated list in `{"data": [...], "meta": {...}}` envelope
(200 OK).

### Get Ticket

```
GET /api/v1/tickets/{ticket_id}
```

Returns a single ticket by UUID or `SNTL-{n}`. The response includes
bugowner information for each package (type, name, email, and group
members when applicable — see
`docs/features/packages/package-bugowner.md`). See
[Soft-Delete](#soft-delete) for soft-deleted ticket visibility rules.

Response: ticket object in `{"data": ...}` envelope (200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 410 with code `TICKET_DELETED`: ticket is soft-deleted and caller is
  not an Admin

### Create Ticket

```
POST /api/v1/tickets
```

Creates a ticket manually. The creating user is automatically assigned.

Request body:

```json
{
  "cve_id": "CVE-2024-1234",
  "severity": "High"
}
```

- `cve_id` (string, optional): CVE identifier string to associate with
  the ticket. If the CVE is not in the database, a minimal CVE record
  is created and on-demand fetch is triggered (see
  `docs/features/tickets/cve-tracking.md`, "On-demand Single-CVE Fetch")
- `severity` (string, optional): initial severity override (Critical,
  High, Medium, Low, None). If omitted, severity is `None` until set
  by the VA. Ignored if `cve_id` is provided (severity is derived from
  CVSS)

Response: the created ticket object wrapped in the standard `{"data": ...}`
envelope (201 Created). Includes `cve_data_pending: true` when a CVE-ID
was provided and the CVE data is being fetched in the background.

Error responses:

- 409 with code `TICKET_CVE_CONFLICT`: CVE is already associated with
  another ticket. Response body includes `existing_ticket_id` (UUID) to
  allow the frontend to link to the existing ticket

Requires the Vulnerability Analyst role.

### Associate CVE

```
POST /api/v1/tickets/{ticket_id}/associate-cve
```

Associates a CVE with a ticket that does not have one. If the CVE is not
yet in the Sentinel database, a minimal CVE record is created and on-demand
fetch is triggered automatically (see `docs/features/tickets/cve-tracking.md`,
"On-demand Single-CVE Fetch").

Request body:

```json
{
  "cve_id": "CVE-2024-1234"
}
```

- `cve_id` (string, required): CVE identifier string

Response: the updated ticket object wrapped in the standard `{"data": ...}`
envelope (200 OK). Includes `cve_data_pending: true` when the CVE data
is being fetched in the background.

Error responses:

- 400 with code `TICKET_CVE_ALREADY_SET`: ticket already has a CVE
  associated
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_CVE_CONFLICT`: CVE is already associated with
  another ticket. Response body includes `existing_ticket_id` (UUID) to
  allow the frontend to link to the existing ticket

Requires the Vulnerability Analyst role.

### Remove CVE from Ticket (Admin Only)

```
DELETE /api/v1/tickets/{ticket_id}/cve
```

Removes the CVE association from a ticket. The CVE record itself is not
deleted. After removal, severity resolution falls back to
`severity_override`.

Response: 204 No Content.

Error responses:

- 400 with code `TICKET_CVE_NOT_SET`: ticket does not have a CVE
  associated
- 404 with code `TICKET_NOT_FOUND`: ticket not found

Requires the Admin role.

### Update Severity Override

```
PATCH /api/v1/tickets/{ticket_id}/severity
```

Updates the severity override for a ticket without a CVE.

Request body:

```json
{
  "severity": "High"
}
```

- `severity` (string, required): severity value (Critical, High, Medium,
  Low, None)

Response: the updated ticket object wrapped in the standard `{"data": ...}`
envelope (200 OK).

Error responses:

- 400 with code `TICKET_SEVERITY_DERIVED`: ticket has an associated CVE
  (severity is derived from CVSS, not manually settable)
- 404 with code `TICKET_NOT_FOUND`: ticket not found

Requires the Vulnerability Analyst role.

### Assign Ticket

```
POST /api/v1/tickets/{ticket_id}/assign
```

Assigns or reassigns a ticket to a VA. See
[Reassignment](#reassignment) for reassignment rules and
[Auto-Assignment on Unassigned Tickets](#auto-assignment-on-unassigned-tickets)
for auto-assignment behavior.

Request body:

```json
{
  "user_id": "jdoe"
}
```

- `user_id` (string, required): UUID or username of the target user. The
  target must hold the `vulnerability_analyst` role.

Response: the updated ticket object wrapped in the standard `{"data": ...}`
envelope (200 OK).

Error responses:

- 400 with code `TICKET_ASSIGNEE_NOT_VA`: target user does not hold the
  Vulnerability Analyst role
- 400 with code `TICKET_ASSIGNEE_INACTIVE`: target user is inactive
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 404 with code `USER_NOT_FOUND`: target user not found

Requires the Vulnerability Analyst role.

### Ignore Ticket

```
POST /api/v1/tickets/{ticket_id}/ignore
```

Marks a ticket as Ignored. Allowed transitions: New → Ignored,
Analysis → Ignored (see [Status Transitions](#status-transitions)). If
the ticket has no assignee, auto-assignment applies (see
[Auto-Assignment on Unassigned Tickets](#auto-assignment-on-unassigned-tickets)).

Response: the updated ticket object wrapped in the standard `{"data": ...}`
envelope (200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_INVALID_TRANSITION`: current status does not
  allow transition to Ignored

Requires the Vulnerability Analyst role.

### Mark Ticket as Duplicate

```
POST /api/v1/tickets/{ticket_id}/duplicate
```

Marks a ticket as a duplicate of another ticket. The target is resolved
following the chain if it is itself Duplicated. Existing tickets pointing
to this ticket are cascade-updated to the resolved target. See
[Duplicate Handling](#duplicate-handling) for chain resolution, cascade
updates, and invariants.

Request body:

```json
{
  "duplicate_of_id": "SNTL-42"
}
```

- `duplicate_of_id` (string, required): UUID or `SNTL-{n}` of the
  target ticket

Response: the updated ticket object wrapped in the standard `{"data": ...}`
envelope (200 OK).

Error responses:

- 400 with code `TICKET_SELF_DUPLICATE`: resolved target is the same
  ticket (self-reference after chain resolution)
- 404 with code `TICKET_NOT_FOUND`: ticket or target ticket not found
- 409 with code `TICKET_DUPLICATE_CHAIN_DEPTH`: chain depth exceeded
  (indicates data corruption requiring manual intervention)

Requires the Vulnerability Analyst role.

### Revert Duplicate Status

```
POST /api/v1/tickets/{ticket_id}/revert-duplicate
```

Reverts a Duplicated ticket to its previous status. The ticket is
reassigned to the VA who performed the revert. After restoring the
status, `evaluate_ticket_status` reconciles with current gate conditions.
See [Duplicate Handling](#duplicate-handling) for revert behavior and
status reconciliation.

Response: the updated ticket object wrapped in the standard `{"data": ...}`
envelope (200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_INVALID_TRANSITION`: ticket is not in Duplicated
  status

Requires the Vulnerability Analyst role.

### Soft-Delete Ticket

```
DELETE /api/v1/tickets/{ticket_id}
```

Soft-deletes a ticket by setting `deleted_at`. Creates a `ticket_deleted`
TicketAuditEvent. See [Soft-Delete](#soft-delete) for visibility rules and
sub-resource behavior.

Response: 204 No Content.

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_ALREADY_DELETED`: ticket is already soft-deleted

Requires the Admin role.

### Restore Ticket

```
POST /api/v1/tickets/{ticket_id}/restore
```

Restores a soft-deleted ticket by clearing `deleted_at`. Creates a
`ticket_restored` TicketAuditEvent. See [Soft-Delete](#soft-delete) for
soft-delete lifecycle.

Response: the restored ticket object wrapped in the standard
`{"data": ...}` envelope (200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_NOT_DELETED`: ticket is not soft-deleted

Requires the Admin role.

## Data Model

See `docs/data-model.md` for the full schema. Key fields on the Ticket
table:

| Column            | Type        | Constraints                  | Description |
|-------------------|-------------|------------------------------|-------------|
| id                | UUID        | PK                           | Internal identifier |
| sequence_id       | INTEGER     | UNIQUE, NOT NULL, auto-increment | Human-readable ID, exposed as `SNTL-{n}` |
| cve_id            | UUID        | FK(cve.id), UNIQUE, nullable | Associated CVE (optional) |
| status            | ENUM        | NOT NULL, DEFAULT New        | Ticket status |
| assignee_id       | UUID        | FK(user.id), nullable        | Assigned VA |
| severity_override | ENUM        | nullable                     | Manual severity (Critical, High, Medium, Low, None). Used when `cve_id IS NULL` |
| duplicate_of_id   | UUID        | FK(ticket.id), nullable      | Original ticket when Duplicated |
| previous_status   | ENUM        | nullable                     | Status before Duplicated |
| created_at        | TIMESTAMP   | NOT NULL, DEFAULT            | Record creation timestamp |
| updated_at        | TIMESTAMP   | NOT NULL, DEFAULT            | Record update timestamp |
| deleted_at        | TIMESTAMP   | nullable                     | Soft-delete timestamp |

## Security

- Viewing ticket lists and details: publicly accessible (no
  authentication required)
- Creating tickets, assigning, changing status, associating CVE,
  managing packages, setting severity override: Vulnerability Analyst role
- Removing a CVE from a ticket: Admin role
- Soft-deleting and restoring tickets: Admin role
- See `docs/features/identity/rbac.md` for the full permission model

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
