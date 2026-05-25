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
- `SNTL-{n}` is the primary label shown in logs, events, and external
  communications.

### API Dual Lookup

All API endpoints that accept a `{ticket_id}` path parameter support
dual lookup:

- **UUID**: `GET /api/v1/tickets/a1b2c3d4-...` — standard UUID lookup
- **SNTL-{n}**: `GET /api/v1/tickets/SNTL-42` — resolved via
  `sequence_id` lookup

The backend detects the format automatically (UUIDs contain hyphens and
hex characters; `SNTL-{n}` starts with the literal prefix `SNTL-`).

### Search

The `search` query parameter on `GET /api/v1/tickets` searches across
the following fields. A ticket matches if any field matches.

- **SNTL-{n} identifier**: prefix-match on the numeric part of the
  sequence number. The `SNTL-` prefix is optional in the query — a
  purely numeric term is treated as a sequence number search (e.g.,
  `42` matches SNTL-42 and SNTL-420 but not SNTL-1042). A query of
  just `SNTL-` with no digits is ignored for this field.
- **CVE ID** (if the ticket has an associated CVE): prefix-match on
  the full CVE-ID string (e.g., `CVE-2024-12` matches `CVE-2024-1234`
  and `CVE-2024-1200`). The `CVE-` prefix is optional if the format is
  recognizable as year-number (e.g., `2024-1234`).
- **Package names**: case-insensitive substring match (ILIKE). Matches
  any package associated with the ticket whose name contains the search
  term.

## CVE Association

A ticket may optionally be associated with a CVE.

- `Ticket.cve_id`: UUID, FK to `cve.id`, **UNIQUE**, **NULLABLE**
- The UNIQUE constraint ensures that a CVE can be associated with at most
  one ticket (1:0..1 relationship)
- Tickets created from CVE ingestion have `cve_id` set at creation time
- Tickets created manually or from external sources (e.g., bug trackers)
  start without a CVE

### CVE Resolution Behavior

Whenever a CVE-ID is provided for association with a ticket (whether at
ticket creation or via explicit association), the following rules apply:

- **Conflict**: if the CVE exists in the database and is already
  associated with another ticket, the operation fails with 409 Conflict.
  The response body includes `existing_ticket_id` (UUID) to identify
  the conflicting ticket
- **On-demand fetch**: if the CVE does not exist in the Sentinel
  database, a minimal CVE record (only `cve_id` set) is created and an
  on-demand single-CVE fetch is triggered in the background (see
  `docs/features/tickets/cve-tracking.md`, "On-demand Single-CVE Fetch").
  The operation proceeds immediately with the minimal record.
- **Normal**: if the CVE exists and is not associated with any ticket,
  the association proceeds directly

### Associating a CVE Later

An VA can associate a CVE with a ticket that does not yet have one, via
`POST /api/v1/tickets/{ticket_id}/associate-cve`.

**Rules**:

- The ticket must not already have a CVE associated (`cve_id IS NULL`)
- The CVE-ID string must be provided (e.g., `CVE-2024-1234`)
- [CVE Resolution Behavior](#cve-resolution-behavior) applies
- When a CVE is associated:
  - `Ticket.cve_id` is set
  - The automatic severity from CVSS takes over (see
    [Severity Resolution](#severity-resolution)) — initially `None` if
    the CVE data has not been fetched yet; updated automatically once
    CVSS data arrives from the on-demand fetch
  - CVSS sync and release tracking begin applying to the ticket
  - The ticket may regress from Analyzed to Analysis if CVSS data has
    not arrived yet (gate #3 and #4 may fail)

See [ticket-service.md](ticket-service.md#associate_cve) for the full
service-layer contract (locking, audit events, status evaluation).

### Dissociating a CVE

Dissociating a CVE from a ticket requires the `admin_ticket_ops`
capability. Users without this capability cannot remove a CVE from a
ticket. If a user believes a CVE was associated in error, they should
request an admin to remove it.

A user with `admin_ticket_ops` can remove a CVE from a ticket via
`DELETE /api/v1/tickets/{ticket_id}/cve`.

**Effects**:

- `Ticket.cve_id` is set to `NULL`
- Severity resolution falls back to `severity_override` (see
  [Severity Resolution](#severity-resolution)). If `severity_override`
  is also `NULL`, the ticket severity becomes `None` — the ticket may
  regress to Analysis
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
- The CVE record itself is not deleted — it remains in the database.
  If no other ticket references this CVE, a subsequent CVE sync will
  create a new ticket for it — this is intentional to ensure CVEs are
  not lost. If the Admin intends to re-associate the CVE with a
  different ticket, this should be done before the next sync cycle

See [ticket-service.md](ticket-service.md#dissociate_cve) for the full
service-layer contract (locking, audit events, status evaluation).

**Capability**: `admin_ticket_ops`.

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

A user with the `create_ticket` capability can create a ticket manually
via `POST /api/v1/tickets` or through the UI.

- `cve_id`: optionally, the user may specify a CVE-ID string (e.g.,
  `"CVE-2024-1234"`) at creation time. If omitted, the ticket is
  created without a CVE (can be associated later)
- When a CVE-ID is provided:
  - [CVE Resolution Behavior](#cve-resolution-behavior) applies
- If the creating user holds the `vulnerability_analyst` role:
  - `status`: `Analysis` (direct, bypasses `New` — the creating user is
    automatically assigned)
  - `assignee_id`: set to the creating user
- If the creating user does NOT hold the `vulnerability_analyst` role
  (e.g., `automation_agent`):
  - `status`: `New` (auto-assignment is skipped — see
    [Auto-Assignment on Unassigned Tickets](#auto-assignment-on-unassigned-tickets))
  - `assignee_id`: `NULL`

See [ticket-service.md](ticket-service.md#create_ticket) for the full
service-layer contract (audit events, CVE uniqueness handling).

**Capability**: `create_ticket`.

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

## Ticket Lifecycle

### Statuses

| Status     | Description |
|------------|-------------|
| New        | Created automatically (CVE ingestion or external source). Not yet assigned to any VA. |
| Analysis   | Assigned to an VA who is actively analyzing — filling in affectedness data. |
| Analyzed   | All required data has been filled in. Ready for updates to be prepared. |
| Resolved   | Security updates have been released for all affected packages across all products. |
| Ignored    | The issue does not require action. Can only be set from New or Analysis. See design note below. |
| Duplicated | Duplicate of another ticket. Links to the original. Reversible. |

**Design note — why Analyzed → Ignored is intentionally excluded**: A ticket
in Analyzed status has had all its packages and tracks fully evaluated. If a VA
later determines the CVE does not require action, the natural workflow is to
remove (soft-delete) the remaining packages. This triggers the orphan cleanup
cascade, which calls `evaluate_ticket_status()` — the "at least one package"
gate condition (Analyzed gate #1) fails and the ticket automatically regresses
to Analysis. At that point the VA can use the existing Analysis → Ignored
transition. Adding a direct Analyzed → Ignored transition would bypass the
package cleanup step, leaving stale affectedness data attached to an Ignored
ticket. The regression path ensures a clean state.

### Status Transition Diagram

```
                     automatic         automatic
New ──→ Analysis ──────────→ Analyzed ──────────→ Resolved
 │         │    ◄────────────    │    ◄────────────
 │         │     automatic       │     automatic
 ├──→ Ignored (from New or Analysis only)
 │         ◄── Ignored → Analysis (VA assigns) or Ignored → New (system reopen)
 │
 └──→ Duplicated (from any gate-zone state, reversible)
      (New, Analysis, Analyzed, Resolved → Duplicated)
```

### Status Transitions

| From       | To         | Trigger                                                | Mode               | Who                                    |
|------------|------------|--------------------------------------------------------|--------------------|----------------------------------------|
| New        | Analysis   | User assigned, or any modifying operation on unassigned ticket | Manual (implicit)  | `triage_ticket`                        |
| New        | Ignored    | User clicks "Ignore" action                            | Manual             | `triage_ticket`                        |
| New        | Ignored    | NVD rejects the CVE (`vulnStatus = Rejected`)          | Automatic          | System                                 |
| Analysis   | Analyzed   | All "Analyzed" gate conditions met                     | Automatic          | System                                 |
| Analysis   | Ignored    | User determines issue is not relevant                  | Manual             | `triage_ticket`                        |
| Analyzed   | Resolved   | All "Resolved" gate conditions met                     | Automatic          | System                                 |
| Analyzed   | Analysis   | "Analyzed" gate conditions no longer met               | Automatic          | System (triggered by user or system action) |
| Resolved   | Analyzed   | "Resolved" gate conditions no longer met, but "Analyzed" gates still met | Automatic | System (triggered by user or system action) |
| Resolved   | Analysis   | Both "Resolved" and "Analyzed" gate conditions no longer met | Automatic    | System (triggered by user or system action) |
| New, Analysis, Analyzed, Resolved | Duplicated | User marks ticket as duplicate | Manual | `triage_ticket` |
| Duplicated | (evaluated) | User reverts duplicate status; `_reenter_gate_zone` determines target | Manual | `triage_ticket` (assignment only if actor holds VA role) |
| Ignored    | (evaluated) | User reopens or system reopens (e.g., NVD rejection revert); `_reenter_gate_zone` determines target | Manual / Automatic | `triage_ticket` or System (assignment only if actor holds VA role) |

**Note on NVD Rejections**: When a CVE's `vulnStatus` changes to `Rejected` in NVD, only tickets in `New` status are automatically transitioned to `Ignored`. Tickets in `Analysis` or later statuses are NOT automatically transitioned; instead, a notification is sent to the assignee for manual review. For the complete flow regarding NVD rejections and rejection reverts, see `docs/features/tickets/cve-tracking.md` ("Rejection handling" and "Rejection revert handling").

### Gate: Analysis → Analyzed

The system automatically transitions a ticket from Analysis to Analyzed
when ALL of the following conditions are met:

1. **At least one package**: the ticket must have at least one package
   added (at least one active `TicketPackageTrack` record exists)
2. **All track affectedness decided**: no active `TicketPackageTrack`
   records in `ANALYSIS` status
3. **Severity set**: the ticket must have a determined severity (not
   `None`). For tickets with CVE, this is derived from CVSS. For tickets
   without CVE, `severity_override` must be set by the VA
4. **SUSE CVSS provided** (only for tickets with CVE): the VA must have
   provided BOTH SUSE CVSS v3.1 AND v4.0 assessments (see
   `docs/features/tickets/cvss-scoring.md`)

This evaluation is performed automatically by the centralized status
evaluation function (see "Centralized Status Evaluation" below) after
every operation that modifies gate-relevant data. There is no manual
"Mark as Analyzed" action — the transition happens as soon as all
conditions are satisfied.

Conversely, if any of these conditions ceases to be met (e.g., a package
is added with tracks in ANALYSIS, a SUSE CVSS assessment is deleted, or
severity becomes undetermined), the ticket automatically transitions back
from Analyzed to Analysis.

### Gate: Analyzed → Resolved

The system automatically transitions a ticket from Analyzed to Resolved
when ALL of the following conditions are met (only records that are not
effectively excluded are considered — see
`docs/features/packages/package-model.md`, "Hierarchical Exclusion
Model"):

1. Every active `TicketPackageTrack` has a final status:
   `FIXED`, `NOT_AFFECTED`, or `WONT_FIX`
2. Every eligible product (`eligible = true`) under a `FIXED` track has
   `released_at IS NOT NULL` (confirmed receipt of the update)

This evaluation is performed by the centralized status evaluation
function after every operation that modifies track statuses, product
eligibility, or product release confirmation.
There is no manual "Mark as Resolved" action.

Conversely, if any of these conditions ceases to be met (e.g., CVSS
recalculation changes product eligibility, or a VA resets a track status
from a final state to `AFFECTED`), the ticket automatically transitions
back from Resolved to Analyzed (or to Analysis, if the "Analyzed" gates
are also no longer met).

### Automatic Status Evaluation

Forward and reverse transitions between Analysis, Analyzed, and
Resolved are governed by a single mechanism: the centralized status
evaluation function (`evaluate_ticket_status`) in the
`ticket_mutations` module. This function re-evaluates gate conditions
after every relevant data change and sets the ticket to the highest
valid status. It is the sole authority for gate-zone status.

- If all "Resolved" AND "Analyzed" gates are met → Resolved
- If all "Analyzed" gates are met → Analyzed
- If the "Analysis" gate is met → Analysis
- Otherwise → New

Reverse transitions are not special cases — they emerge naturally
when gate conditions are no longer met.

All automatic transitions create a `TicketAuditEvent` with
`user_id = NULL` (system action), even when the underlying data
change was initiated by a VA.

See [ticket-mutations.md](ticket-mutations.md) for the full function
contract, inactive assignee sanitization, concurrency control rules,
orphan cleanup invariants, and architectural test requirements.

#### Concurrency Control

Every operation that modifies the `Ticket` row MUST acquire
`FOR UPDATE` on the Ticket row before any modification. See
[ticket-mutations.md](ticket-mutations.md#concurrency-control) for
the full locking rules and
[ticket-service.md](ticket-service.md#concurrency-control) for the
per-operation locking matrix.

### Reassignment

A ticket can be reassigned to a different VA at any time, as long as the
ticket is in a mutable status (not Ignored or Duplicated). For Ignored
tickets, the dedicated reopen flow (`POST .../reopen`) handles
assignment; for Duplicated tickets, the revert-duplicate flow
(`POST .../revert-duplicate`) handles it. Reassignment does not change
the ticket status. All reassignments are logged in the ticket event
history.

**Target constraint**: the assignment target MUST be an **active** user
holding the `vulnerability_analyst` role. Attempting to assign a ticket
to a user without this role, or to an inactive user, fails with 400 Bad
Request. This applies to the explicit assignment endpoint
(`PATCH .../assignee`). Auto-assignment checks internally whether the
acting user holds the `vulnerability_analyst` role — if not (e.g., an
`automation_agent`), auto-assignment is skipped and the ticket remains
unassigned.

**System-initiated unassignment**: in addition to the bulk unassignment
performed by `deactivate_user` (see
[user-service.md](../identity/user-service.md#deactivate_user)),
`evaluate_ticket_status` also performs system-initiated unassignment
when it encounters an inactive assignee on a non-final ticket (see
[Inactive Assignee Sanitization](ticket-mutations.md#inactive-assignee-sanitization)).
This ensures that even if a ticket enters the gate zone or is evaluated
after the deactivation event, the stale assignee is cleared.

### Auto-Assignment on Unassigned Tickets

When a user with the `vulnerability_analyst` role performs any modifying
operation on a ticket with `assignee_id = NULL`, the ticket is
automatically assigned to the acting user. A `TicketAuditEvent` with
`event_type = assignment` is created atomically in the same transaction
as the modifying operation. If the acting user does not hold the
`vulnerability_analyst` role (e.g., an `automation_agent`),
auto-assignment is skipped — the ticket remains unassigned for a human
to claim.

After the assignment, `evaluate_ticket_status` is called within the same
transaction. If the ticket was in `New` status and the operation does not
include an explicit status change (e.g., marking as duplicate or ignored),
the assignee gate (`assignee_id IS NOT NULL`) promotes the ticket to
`Analysis` automatically.

If the operation includes an explicit status change (e.g.,
`New → Duplicated` or `New → Ignored`), the status follows the explicit
transition and the assignee is set — the assignee gate does not override
explicit transitions.

This rule does not apply to system operations (background tasks,
automated ingestion) or to users without the `vulnerability_analyst`
role.

This rule is enforced via the shared helper
`ticket_mutations.auto_assign_if_needed()`, which is called by all
modules that modify tickets under a `FOR UPDATE` lock
(`ticket_mutations`, `package_service`, `ticket_service`). See
[ticket-mutations.md](ticket-mutations.md#auto_assign_if_needed) for
the helper's signature and behavior.

### Duplicate Handling

#### Terminology

- **Canonical target**: the non-Duplicated ticket at the end of the
  `duplicate_of_id` resolution chain. This is the technically precise
  term used throughout the resolver logic, cascade operations, and
  concurrency sections.
- **Original ticket**: the user-facing synonym for "canonical target."
  Used in UI copy (e.g., "See the original ticket: SNTL-42"), audit
  event descriptions, and high-level prose where the technical
  resolution mechanism is not relevant.

#### Canonical Target Resolver

A centralized public function `resolve_canonical_target` in the
`ticket_mutations` module follows the `duplicate_of_id` chain to
find the non-Duplicated canonical target. See
[ticket-mutations.md](ticket-mutations.md#resolve_canonical_target)
for the full function contract (parameters, hop limit, cycle detection,
error codes).

All code paths that need the canonical target MUST use this function:
- `mark-as-duplicate` operation (pre-write validation)
- API response serialization (see
  [API Response Behavior](#api-response-behavior))
- Any future logic that reads `duplicate_of_id` for decision-making

Direct reads of `duplicate_of_id` without resolution are only permitted
for:
- Audit event recording (`old_value`/`new_value` store the `SNTL-{n}`
  identifier corresponding to the DB value at the time of the event)
- Database-level queries that need the raw FK (e.g., finding all tickets
  whose raw `duplicate_of_id` points to a specific ticket, for cascade
  purposes)

#### Mark-as-Duplicate Operation

A ticket can be marked as duplicate from any **gate-zone** status (New,
Analysis, Analyzed, Resolved). Tickets in the manual zone (Ignored or
Duplicated) are blocked by the `require_ticket_mutable` guard (409
`TICKET_NOT_MUTABLE`) — an Ignored ticket must be reopened first, and a
Duplicated ticket must be reverted first.

Steps:

1. Verify the ticket is not in `Ignored` or `Duplicated` status (the
   `require_ticket_mutable` guard handles this — 409 if violated).
2. Resolve the requested target to its canonical target using the
   resolver.
3. If the resolved canonical target has `deleted_at IS NOT NULL`, reject
   with 404 `TICKET_NOT_FOUND` — the target is invisible to business
   logic per the soft-delete invariant.
4. If the canonical target equals the ticket being modified, reject with
   400 Bad Request ("a ticket cannot be a duplicate of itself").
5. Set `duplicate_of_id = canonical_target_id` and
   `status = Duplicated`.
6. Cascade: all tickets whose `duplicate_of_id` points to the
   just-duplicated ticket are updated to point to the canonical target
   (synchronous, best-effort per-item in independent transactions).
7. If the cascade is interrupted or individual steps fail, the system
   is NOT corrupted — subsequent reads resolve the chain through the
   canonical resolver.

The cascade is synchronous (completes before the API response returns)
because chains longer than two tickets are almost nonexistent, making
the overhead negligible (1–2 extra DB operations in the worst case).

See [ticket-service.md](ticket-service.md#mark_as_duplicate) for the
full service-layer contract (locking, auto-assignment, audit events,
cascade transaction isolation).

> **Note — soft-deleted source ticket**: attempting to mark a
> soft-deleted ticket itself as duplicate is already rejected by the
> shared sub-resource router dependency (410 `TICKET_DELETED`), which
> fires before endpoint-specific logic runs. No additional guard is
> needed in the mark-as-duplicate steps above.

#### Cascade as Best-Effort Flattening

The cascade is an optimization that reduces hops for future resolutions.
It is NOT a correctness requirement. The system is correct with or
without cascade completion because:

- All reads use the canonical resolver.
- Duplicated tickets are immutable (API returns 409 on modification
  attempts), so intermediate links are stable.
- The only operation that can alter an intermediate link is
  `revert-duplicate` on that specific ticket, which clears
  `duplicate_of_id` entirely (correct behavior regardless of chain
  state).

#### Revert-Duplicate Operation

When reverting a ticket from Duplicated status
(`ticket_mutations.revert_duplicate()`):

- `duplicate_of_id` is cleared (set to NULL)
- If the acting user holds the `vulnerability_analyst` role, the ticket
  is reassigned to them. If the acting user does not hold the VA role
  (e.g., an `automation_agent`), the reassignment step is skipped — the
  ticket retains its current assignee (or remains unassigned)
- The ticket re-enters the gate zone; `evaluate_ticket_status`
  determines the correct status based on current gate conditions
- Creates two `TicketAuditEvent` records: `duplicate_removed` (user
  action) + `status_change` (system action)

See [ticket-mutations.md](ticket-mutations.md#revert_duplicate) for the
full function contract.

The revert operation does NOT need to know or care about the canonical
target — it simply removes the ticket from the duplicate chain.

#### Revert of an Intermediate Ticket

Scenario: `A → B → C` (A points to B, B points to C, cascade was
interrupted so A was not flattened).

If a VA reverts B (removes B from Duplicated status):
- B.`duplicate_of_id` is cleared, `_reenter_gate_zone` determines B's
  new status based on current gate conditions.
- A still points to B, but B is no longer Duplicated.
- Therefore A.`duplicate_of_id` resolves to B directly (B is the
  canonical target now).
- This is correct: A is a duplicate of B, which is now a live ticket
  again.
- No cascade or repair needed on A.

#### API Response Behavior

Wherever the `duplicate_of_id` field is included in an API response,
its value MUST be the resolved canonical target (the `SNTL-{n}`
identifier of the non-Duplicated ticket at the end of the chain), not
the raw DB value. The API does NOT expose the raw DB value in a separate
field.

This rule is endpoint-agnostic: it applies to any response schema that
includes `duplicate_of_id`. This ensures:

- UI links always point to the correct non-Duplicated ticket (the
  "original ticket").
- Third-party scripts and integrations can trust that following
  `duplicate_of_id` always leads to a non-Duplicated ticket — no
  client-side chain resolution needed.
- The transient state (interrupted cascade) is invisible to API
  consumers.

When the resolver follows the `duplicate_of_id` chain and the canonical
target has `deleted_at IS NOT NULL`, the resolver still returns the
`SNTL-{n}` identifier of that soft-deleted ticket. The duplicate link is
historical data; the duplicated ticket remains in Duplicated status
regardless of the target's lifecycle state.

The raw value remains accessible through the audit history
(`duplicate_set` and `duplicate_target_changed` events record what was
written to the DB).

#### Cycle Prevention

Under normal sequential operations, cycles cannot form because:
1. `mark-as-duplicate` always resolves the target to a canonical
   non-Duplicated ticket before writing.
2. A ticket in Duplicated status cannot be the target of
   mark-as-duplicate (it would be resolved through to its canonical).
3. A non-Duplicated ticket being marked as duplicate has its target
   resolved — if the resolution leads back to itself, the operation is
   rejected.

Under concurrent operations (two users simultaneously marking tickets
that reference each other), a cycle could theoretically form under READ
COMMITTED isolation. This is accepted as a residual risk because:
- Duplicate operations are rare.
- Concurrent conflicting duplicate operations on the same chain are
  essentially zero probability.
- The cycle detection in the resolver catches this at read time with a
  clear integrity error (`TICKET_DUPLICATE_CYCLE_DETECTED`).
- Resolution requires manual admin intervention.
- Adding `FOR UPDATE` on the target ticket would lock two tickets in the
  same transaction, contradicting the single-ticket-scope rule — a
  disproportionate cost for an essentially impossible scenario.

#### Cycle Resolution

When `TICKET_DUPLICATE_CYCLE_DETECTED` is encountered, a VA or Admin
must invoke `POST /api/v1/tickets/{ticket_id}/revert-duplicate` on at
least one ticket in the cycle to break it.

The `revert-duplicate` operation is designed to work correctly in the
presence of cycles — it clears `duplicate_of_id` unconditionally
regardless of chain state. No chain resolution is performed during
revert, so the cycle does not interfere with the operation.

After breaking the cycle, the reverted ticket returns to its
pre-duplicate status (determined by `_reenter_gate_zone` based on
current gate conditions) and can be re-evaluated normally.

#### Correctness Guarantee

`duplicate_of_id` SHOULD reference the canonical non-Duplicated ticket
after normal write operations complete successfully. A link to a
Duplicated ticket is a valid transient state (e.g., after an interrupted
cascade) and MUST be handled gracefully by the canonical resolver.
Correctness MUST NOT depend on immediate flatness of the link. Multiple
tickets may reference the same canonical target.

This operation modifies the `Ticket` row and calls
`evaluate_ticket_status`. It MUST acquire `FOR UPDATE` on the `Ticket`
row before any modification (see
[Concurrency Control](#concurrency-control)).

## Soft-Delete

- Soft-delete is performed by setting `deleted_at` to the current
  timestamp
- Only users with the `admin_ticket_ops` capability may soft-delete or
  restore tickets
- Soft-deleted tickets (`deleted_at IS NOT NULL`) are invisible to all
  business logic — no operation (API query, service-layer side effect,
  or background task) queries, modifies, or produces side effects for
  soft-deleted tickets unless it explicitly deals with deletion or
  restoration management
- All sub-resources of a soft-deleted ticket remain intact but are
  inaccessible to users without the `admin_ticket_ops` capability. This
  is enforced centrally by a shared dependency on the ticket sub-resource
  router — see `docs/api-spec.md`
  ([Scoped Responses](docs/api-spec.md#scoped-responses)) for the
  HTTP-level contract (410 `TICKET_DELETED`)
- Soft-deleting a ticket does NOT alter or invalidate `duplicate_of_id`
  links from other tickets pointing to it. This is a controlled exception
  to the general invisibility invariant — the duplicate link is historical
  data and the duplicated ticket remains in Duplicated status regardless
  of the target's lifecycle state.
- A soft-deleted ticket can be restored by clearing `deleted_at`.
  After restoring, the ticket's status is reconciled with current gate
  conditions — while the ticket was soft-deleted, gate-relevant data
  may have changed externally (CVSS scores deleted, product eligibility
  changed, AIMAAS thresholds updated). If the gates for the ticket's
  current status are no longer met, the ticket is automatically
  regressed to the appropriate status. Manual-zone tickets (Ignored or
  Duplicated) retain their pre-deletion status unchanged

See [ticket-service.md](ticket-service.md#soft_delete_ticket) for the
full service-layer contract (locking, audit events, status evaluation
after restore).

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

## Terminal Statuses and Mutability

### Ignored

Ignored is a **manual-zone status** — `evaluate_ticket_status` never
operates on Ignored tickets. Two exit transitions are allowed:

1. **VA assigns themselves (manual):** the VA becomes the assignee.
2. **System reopens (automatic):** the last active assignee is restored,
   or no assignee is set if none exists or the previous one is
   deactivated. This handles cases like NVD rejection reverts (see
   `docs/features/tickets/cve-tracking.md`, "Rejection revert handling").

Both transitions go through `ticket_mutations.reopen_from_ignored()`:
1. Acquires `FOR UPDATE` on the ticket
2. Verifies current status is Ignored
3. Sets assignee (if applicable)
4. Re-enters the gate zone; `evaluate_ticket_status` determines the
   correct status (typically Analysis if an assignee is present)

See [ticket-mutations.md](ticket-mutations.md#reopen_from_ignored) for
the full function contract.

All other modifications on Ignored tickets are blocked — mutation
endpoints return 409 `TICKET_NOT_MUTABLE` (same guard as Duplicated).
This prevents gate-relevant data from accumulating while the ticket is
in the manual zone, which would cause unexpected status jumps on reopen.
See [Mutability Guard](#mutability-guard) for enforcement details.

### Modifications in Inactive Statuses

Tickets in inactive statuses (`Resolved`, `Ignored`, `Duplicated`) are
not monitored by background tasks.

- **Resolved**: modifying gate-relevant data triggers centralized status
  evaluation, which may regress the ticket to Analyzed or Analysis
- **Ignored and Duplicated** (manual zone): mutation endpoints return
  409 `TICKET_NOT_MUTABLE` via the `require_ticket_mutable` dependency.
  Only the dedicated exit endpoints (`POST .../reopen` for Ignored,
  `POST .../revert-duplicate` for Duplicated) bypass this guard. See
  [Mutability Guard](#mutability-guard) for the enforcement mechanism.

### Mutability Guard

Enforcement of the manual-zone immutability is centralized in a shared
FastAPI dependency `require_ticket_mutable`, applied as a `Depends()` on
each mutation endpoint's handler signature:

```python
async def require_ticket_mutable(ticket: Ticket = Depends(get_ticket)):
    if ticket.status in (TicketStatus.Ignored, TicketStatus.Duplicated):
        raise HTTPException(
            status_code=409,
            detail={"code": "TICKET_NOT_MUTABLE", ...}
        )
    return ticket
```

**Scope**:
- Applied to: all endpoints that modify ticket data
- NOT applied to: read endpoints (GET), manual-zone exit endpoints
  (`POST .../reopen`, `POST .../revert-duplicate`), and soft-delete/restore
  (which have their own admin-only guard)

**Relationship with `require_accessible_ticket`**: the accessibility
check is a router-level dependency (applies to all operations on a
single ticket, including reads — see `docs/api-spec.md`, Ticket
Accessibility Check). `require_ticket_mutable` is per-endpoint
(applies only to mutations). A ticket can be both accessible and
not-mutable (e.g., an active Duplicated ticket). The two guards are
independent checks evaluated in sequence.

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
| Gate: SUSE CVSS required | Not applicable — severity is set via `severity_override` instead |
| Critical CVE notification | Not applicable |

Packages, tracks, and products can still be added and managed
normally. The VA can set affectedness statuses and the ticket can
progress through the full lifecycle.

## Confidential Tickets

Sentinel supports "Confidential Tickets" to securely handle embargoed
vulnerabilities. Confidential tickets restrict read and write access to a
specific subset of authorized users, preventing data leaks prior to public
disclosure.

- **Confidentiality Flag**: A boolean state (`is_confidential`) on the
  Ticket entity that determines if the ticket is under embargo.
- **Access Grants**: The mechanism determining who can access a
  confidential ticket. Access is granted via roles, automated maintainer
  inheritance (from IBS bugowners), and explicit manual grants.
- **Confidentiality Filtering**: Confidential tickets are excluded at
  the database query level for unauthorized and unauthenticated users.
  They do not appear in list results, are not returned by detail
  endpoints, and leave no visible trace (no placeholders, no redacted
  entries). Authorized users see confidential tickets normally alongside
  non-confidential ones.

Ticket response objects (both list and detail) MUST include the
`is_confidential: boolean` field. This field is always present — there
is no information leakage concern because a user only receives tickets
they are authorized to see (see [Confidentiality Filtering](#confidentiality-filtering)).

See `docs/data-model.md` for the `TicketAccessGrant` entity definition
and the `is_confidential` column on the Ticket table.

### Authorization Rules

When a ticket is `is_confidential=True`, any read/write HTTP request
MUST be evaluated against these rules. Access is **GRANTED** if the user
meets at least one condition:

1. **Scope-based**: The user's effective scope is `all` (see
   `docs/features/identity/rbac.md`, Scope).
2. **Explicit Grant**: The user's `id` exists in the `TicketAccessGrant`
   table for the requested `ticket_id`.
3. **Bugowner (Person)**: The user's `email` matches the
   `bugowner_email` of any `PackageBugowner` associated with any of the
   ticket's *currently associated* packages. The email comparison MUST
   be case-insensitive.
4. **Bugowner (Group)**: The user's `email` matches the `email` of any
   `PackageBugownerMember` associated with a group bugowner of any
   *currently associated* package in the ticket. The email comparison
   MUST be case-insensitive.

*Dynamic Access Note:* Bugowner access is dynamic. A maintainer gains
access when a package they support is added to the ticket, and loses
access the moment the last package they support is removed from the
ticket. "Currently associated packages" means
`TicketPackage.deleted_at IS NULL` — soft-deleted (excluded) packages do
not grant bugowner access.

If no condition is met, or if the user is unauthenticated, the
confidential ticket is **invisible**: it is excluded from list queries
and detail endpoints return `404 Not Found`. Unauthenticated users never
see confidential tickets.

*Note: System background tasks (Celery fetchers, event consumers) bypass
these rules and process confidential tickets normally.*

### Confidentiality Filtering

Confidential tickets are filtered at the database query level.
Unauthorized and unauthenticated users never see them — no placeholders,
no redacted entries, no trace of their existence.

**Ticket List (`GET /api/v1/tickets`)**:
The list query includes only non-confidential tickets plus confidential
tickets for which the current user satisfies at least one authorization
rule from [Authorization Rules](#authorization-rules). For
unauthenticated users, only non-confidential tickets are returned.
Pagination counts reflect only the tickets visible to the caller.

**Package Search (`GET /api/v1/packages`)**:
The cross-ticket package search endpoint applies the same
confidentiality filtering as the ticket list. Packages belonging to
confidential tickets are excluded for unauthorized callers. See
`docs/features/packages/package-model.md` (Search Packages Across
Tickets).

**Maintainer Dashboard (`GET /api/v1/my/packages/*`)**:
The maintainer dashboard endpoints MUST apply the same confidentiality
filtering as the ticket list. Although the bugowner email match used by
the dashboard already coincides with authorization rules 3 and 4
([Authorization Rules](#authorization-rules)), the confidentiality filter
MUST be applied explicitly as defense in depth — protecting against
future changes to the dashboard query logic that might inadvertently
bypass the authorization check.

**CVE Details (`GET /api/v1/cves/{id}`)**:
If the CVE is linked to a confidential ticket that the caller is not
authorized to access (or is unauthenticated), the ticket reference MUST
be omitted entirely from the response. The caller sees no indication
that a ticket exists for this CVE.

#### Shared Utility: `confidential_ticket_filter()`

The confidentiality filtering logic is implemented as a shared stateless
function in `backend/app/core/filters.py`. It returns a SQLAlchemy
`ColumnElement` (a WHERE clause fragment) that any query can apply. The
endpoint handler constructs the filter; the service function receives it
as a parameter.

```python
# backend/app/core/filters.py

def confidential_ticket_filter(
    ticket_id_col: Column,          # e.g., Ticket.id or TicketPackage.ticket_id
    is_confidential_col: Column,    # e.g., Ticket.is_confidential
    caller_scope: Scope | None,     # None for unauthenticated
    caller_user_id: UUID | None,    # for TicketAccessGrant lookup
    caller_email: str | None,       # for bugowner matching (case-insensitive)
) -> ColumnElement:
    """Build a SQL filter expression for confidential ticket visibility.

    Returns a boolean SQL expression that evaluates to TRUE for rows
    the caller is authorized to see. Apply with query.where(...).

    Visibility rules (from rbac.md, Scope and Confidential Ticket
    Visibility):
    - Scope 'all': see everything (returns TRUE)
    - Unauthenticated (scope is None): only non-confidential
    - Scope 'non_confidential': non-confidential OR any of:
        - TicketAccessGrant exists for (ticket_id, user_id)
        - PackageBugowner.bugowner_email matches caller_email (person)
        - PackageBugownerMember.email matches caller_email (group)
    """
```

**Behavior**:

```
IF caller_scope is None:
    return is_confidential_col == False  # unauthenticated

IF caller_scope == Scope.ALL:
    return literal(True)  # no filter -- scope 'all' sees everything

return OR(
    is_confidential_col == False,
    EXISTS(TicketAccessGrant for caller_user_id),
    EXISTS(PackageBugowner person match for caller_email),
    EXISTS(PackageBugownerMember match for caller_email),
)
```

**Design properties**:

- **Stateless**: pure function, no side effects, no database calls
- **Decoupled**: accepts column references, not model instances — works
  with any query shape (ticket list, package list, CVE details, etc.)
- **Testable**: can be tested in isolation by inspecting the generated
  SQL expression
- **Single responsibility**: the handler knows the user; the service
  knows the query; the filter knows the rules

**Consumers**:

| Consumer | `ticket_id_col` | Notes |
|----------|-----------------|-------|
| `GET /api/v1/tickets` | `Ticket.id` | Ticket list endpoint |
| `GET /api/v1/packages` | `Ticket.id` (via JOIN) | Cross-ticket package search |
| `GET /api/v1/my/packages/*` | `Ticket.id` (via JOIN) | Maintainer operations |
| `require_accessible_ticket` | `Ticket.id` | Single-ticket access guard |

**Accepted risk — `duplicate_of_id` and confidential targets**: A
Duplicated ticket that is non-confidential may have a `duplicate_of_id`
pointing (directly or through a chain) to a confidential ticket. The
`resolve_canonical_target` function resolves this chain without
confidentiality checks (it operates at the service layer), and the
resolved canonical target identifier (`SNTL-{n}`) appears in public API
responses. This reveals the *existence* of the confidential target ticket
but not its content (the detail endpoint returns 404 for unauthorized
callers, indistinguishable from a non-existent ticket). This is an
accepted risk because: (a) only the identifier is exposed — no title,
CVE, severity, or package data leaks; (b) creating the duplicate link
requires `triage_ticket` capability — users with this capability via
the `vulnerability_analyst` role already have scope `all`;
`automation_agent` users have `non_confidential` scope but only reach
this code path for non-confidential source tickets; (c) the reverse
scenario (target becomes
confidential after the link is created) is rare and the leak is limited
to existence inference; (d) implementing bidirectional cascading
confidentiality adds significant complexity (reverse chain traversal,
audit events, revert semantics) disproportionate to the severity of the
information leak.

### Audit Trail

Three `TicketAuditEventType` values support confidentiality operations:

| `event_type` | Trigger | `user_id` | `old_value` | `new_value` | `comment` | `detail` |
|---|---|---|---|---|---|---|
| `confidentiality_changed` | `is_confidential` toggled | Acting user | `"true"` or `"false"` | `"true"` or `"false"` | `NULL` | `NULL` |
| `access_grant_added` | User manually added to access grants | Acting user | `NULL` | Target username | `NULL` | `NULL` |
| `access_grant_removed` | User manually removed from access grants | Acting user | Target username | `NULL` | `NULL` | `NULL` |

See `docs/features/tickets/ticket-audit-log.md` for the audit event
contract and detail JSONB schema.

### Stale Access Grant Cleanup

When a ticket's `is_confidential` flag is set to `FALSE` (embargo
lifted) or the ticket is soft-deleted, the explicit `TicketAccessGrant`
records remain in the database inertly.

To prevent infinite database growth, a Celery Beat background task
(`cleanup_stale_ticket_access_grants`) will be implemented:

- **Type**: Plain Celery Beat task (NOT a `BaseFetcher`, as it does not
  fetch external data).
- **Schedule**: Weekly, Sunday at 04:00 UTC.
- **Logic**: Deletes all `TicketAccessGrant` records belonging to
  tickets where `is_confidential = FALSE` AND `updated_at` is older
  than 14 days.

This single condition covers all cases:

- Embargo lifted (ticket made non-confidential) → grants cleaned after
  14 days
- Soft-deleted non-confidential ticket → `is_confidential` is already
  `FALSE` → grants cleaned after 14 days
- Soft-deleted confidential ticket → `is_confidential` is still `TRUE`
  → grants preserved. If the ticket is later restored, all grants are
  intact. To clean them, an Admin must first restore the ticket, then a
  VA removes confidentiality — the cleanup runs 14 days later

## API Endpoints

For the service-layer contract (function signatures, locking, audit
event creation) of these operations, see
[ticket-service.md](ticket-service.md).

### Response Schemas

This section defines the response schemas for ticket endpoints. All
endpoints that return a ticket use one of two representations depending
on the context: a compact summary for list views, or a full detail
object for single-ticket views and mutation responses.

**Enum serialization**: all enum values (`status`, `severity`,
`workflow_type`, `delivery_status`, and `PackageStatus`) are serialized
as **lowercase** strings in API responses (e.g., `"new"`, `"critical"`,
`"affected"`). Request bodies and query parameters also use lowercase.
The PascalCase forms used elsewhere in this spec (e.g., `New`,
`Analysis`, `Critical`) refer to the logical values; the wire format is
always lowercase.

**Soft-deletion visibility**: all entities with soft-deletion include a
`deleted_at` field (`datetime | null`). This field is present only when
the request includes `include_deleted=true` or `include_deleted=only`
and the caller has the `admin_ticket_ops` capability. When not
applicable, the field is omitted from the response.

#### Shared Sub-Schemas

**UserSummary** — inline representation of a user reference. Fields:
`id` (UUID), `username` (string), `full_name` (string), `active`
(boolean). See `docs/api-spec.md`, "User References in Responses" for
the canonical definition.

**CVESummary** — compact CVE representation for list views:

| Field | Type | Description |
|-------|------|-------------|
| `cve_id` | string | CVE identifier (e.g., `CVE-2024-1234`) |
| `description` | string \| null | Vulnerability description |

**CVESource** — individual CVE data source:

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Source identifier (e.g., `nvd`, `mitre`) |
| `url` | string | Source URL for the CVE |

**CVEDetail** — expanded CVE representation for detail views:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | CVE record primary key |
| `cve_id` | string | CVE identifier (e.g., `CVE-2024-1234`) |
| `description` | string \| null | Vulnerability description |
| `published_date` | datetime \| null | Date published (UTC) |
| `modified_date` | datetime \| null | Date last modified (UTC) |
| `nvd_status` | string \| null | NVD vulnerability status |
| `sources` | CVESource[] | Data sources for this CVE |

**BugownerMember** — individual member within a group bugowner:

| Field | Type | Description |
|-------|------|-------------|
| `userid` | string | IBS username |
| `email` | string | Member email address |

**BugownerInfo** — bugowner data for a package (see
`docs/features/packages/package-bugowner.md`):

| Field | Type | Description |
|-------|------|-------------|
| `type` | `"person"` \| `"group"` | Bugowner type |
| `name` | string | IBS userid or group name |
| `email` | string | Contact email |
| `members` | BugownerMember[] \| null | Group members. `null` for person bugowners |

When the bugowner is unknown (not resolved), the entire `bugowner` field
is `null` rather than an object.

**ProductDetail** — product within a track:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | TicketPackageProduct primary key |
| `product_id` | UUID | Product foreign key |
| `product_name` | string | Product display name (from `Product.display_name`) |
| `eligible` | boolean | Whether this product receives the fix |
| `is_eligible_override` | boolean | `true` if VA manually set eligibility |
| `released_at` | datetime \| null | When the fix was detected in the product repository (UTC) |
| `deleted_at` | datetime \| null | Soft-deletion (see above) |

**TrackDetail** — track (codestream) within a package:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | TicketPackageTrack primary key |
| `workflow_type` | string | `"ibs"` or `"git"` |
| `reference` | string | Codestream project name or branch reference |
| `status` | string | PackageStatus enum: `analysis`, `affected`, `not_affected`, `fixed`, `wont_fix` |
| `delivery_status` | string | DeliveryStatus enum: `pending`, `in_progress`, `released` |
| `delivery_relevant` | boolean | Computed field (see `docs/features/packages/package-model.md`) |
| `products` | ProductDetail[] | Products under this track |
| `deleted_at` | datetime \| null | Soft-deletion (see above) |

**PackageDetail** — package within a ticket (detail view only):

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | TicketPackage primary key |
| `package_name` | string | Source package name |
| `bugowner` | BugownerInfo \| null | Bugowner data. `null` if not resolved |
| `tracks` | TrackDetail[] | Tracks (codestreams) for this package |
| `deleted_at` | datetime \| null | Soft-deletion (see above) |

#### TicketSummary

Returned by the list endpoint. Provides enough information for table
views without the full package tree.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Ticket primary key |
| `identifier` | string | Human-readable identifier (`SNTL-{n}`) |
| `status` | string | TicketStatus enum: `new`, `analysis`, `analyzed`, `resolved`, `ignored`, `duplicated` |
| `severity` | string \| null | Resolved severity (CVSS-derived → override fallback). Values: `critical`, `high`, `medium`, `low`, `none`, or `null` if unresolved |
| `assignee` | UserSummary \| null | Assigned VA, or `null` if unassigned |
| `cve` | CVESummary \| null | Associated CVE summary, or `null` if no CVE |
| `duplicate_of` | string \| null | Canonical duplicate target identifier (`SNTL-{n}`), or `null` |
| `is_confidential` | boolean | Whether the ticket is confidential |
| `package_names` | string[] | Flat list of affected package names (e.g., `["curl", "openssl-3"]`) |
| `created_at` | datetime | Creation timestamp (UTC) |
| `updated_at` | datetime | Last modification timestamp (UTC) |
| `deleted_at` | datetime \| null | Soft-deletion (see above) |

#### TicketDetail

Returned by the detail endpoint and all mutation endpoints. Extends
TicketSummary with the full package tree and expanded CVE data.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Ticket primary key |
| `identifier` | string | Human-readable identifier (`SNTL-{n}`) |
| `status` | string | TicketStatus enum: `new`, `analysis`, `analyzed`, `resolved`, `ignored`, `duplicated` |
| `severity` | string \| null | Resolved severity (CVSS-derived → override fallback). Values: `critical`, `high`, `medium`, `low`, `none`, or `null` if unresolved |
| `assignee` | UserSummary \| null | Assigned VA, or `null` if unassigned |
| `cve` | CVEDetail \| null | Expanded CVE data with dates and sources, or `null` if no CVE |
| `duplicate_of` | string \| null | Canonical duplicate target identifier (`SNTL-{n}`), or `null` |
| `is_confidential` | boolean | Whether the ticket is confidential |
| `packages` | PackageDetail[] | Full package/track/product tree with bugowner data |
| `created_at` | datetime | Creation timestamp (UTC) |
| `updated_at` | datetime | Last modification timestamp (UTC) |
| `deleted_at` | datetime \| null | Soft-deletion (see above) |

Note: TicketDetail does not include `package_names` — the same
information is available from `packages[].package_name`.

Note: the API field `duplicate_of` exposes the resolved canonical target
as a human-readable `SNTL-{n}` string (after chain resolution). This
corresponds to the database column `duplicate_of_id` (UUID FK), but the
API performs resolution and format conversion before serialization.

#### Endpoint → Schema Mapping

| Endpoint | Response Schema |
|----------|----------------|
| `GET /api/v1/tickets` | `TicketSummary[]` (paginated) |
| `GET /api/v1/tickets/{ticket_id}` | `TicketDetail` |
| `POST /api/v1/tickets` | `TicketDetail` (201 Created) |
| `POST .../associate-cve` | `TicketDetail` |
| `PATCH .../severity` | `TicketDetail` |
| `PATCH .../assignee` | `TicketDetail` |
| `POST .../ignore` | `TicketDetail` |
| `POST .../duplicate` | `TicketDetail` |
| `POST .../reopen` | `TicketDetail` |
| `POST .../revert-duplicate` | `TicketDetail` |
| `POST .../restore` | `TicketDetail` |
| `PATCH .../confidentiality` | `TicketDetail` |
| `DELETE .../cve` | 204 No Content (no body) |
| `DELETE /api/v1/tickets/{ticket_id}` | 204 No Content (no body) |

### List Tickets

```
GET /api/v1/tickets
```

- **Access level**: Public
- **Response schema**: `TicketSummary[]` (paginated)

Lists tickets with filtering, search, pagination, and sorting.

Query parameters:

- `search` (string, optional): free-text search across `SNTL-{n}`
  identifier (prefix-match on numeric part), CVE ID (prefix-match),
  and package names (case-insensitive substring). See
  [Search](#search) for detailed matching behavior per field.
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
  has the `admin_ticket_ops` capability. For callers without this
  capability, the parameter is silently ignored. Values: `true` (include active and deleted tickets), `only`
  (return only deleted tickets). Default (absent or `false`): return only
  active tickets.
- `page` (integer, optional): page number for pagination (default: 1).
- `per_page` (integer, optional): items per page (default: 20).
- `sort_by` (string, optional): field to sort by (default: `created_at`).
  Valid values: `created_at`, `updated_at`, `severity`, `status`,
  `identifier` (sorts by numeric `sequence_id`).
- `sort_order` (string, optional): `asc` or `desc` (default: `desc`).

Response: paginated `TicketSummary` array in standard
`{"data": [...], "meta": {...}}` envelope (200 OK).

### Get Ticket

```
GET /api/v1/tickets/{ticket_id}
```

- **Access level**: Public
- **Response schema**: `TicketDetail`

Returns a single ticket by UUID or `SNTL-{n}`. The `packages` field
in the response is populated via
`package_service.get_ticket_packages()` — the same function used by
`GET /api/v1/tickets/{ticket_id}/packages`. The response includes
the full package/track/product tree with bugowner information for each
package (type, name, email, and group members when applicable — see
`docs/features/packages/package-bugowner.md`). See
[Soft-Delete](#soft-delete) for soft-deleted ticket visibility rules.

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found

### Create Ticket

```
POST /api/v1/tickets
```

- **Capability**: `create_ticket`
- **Response schema**: `TicketDetail` (201 Created)

Creates a ticket manually. The creating user is automatically assigned
(if the user holds the `vulnerability_analyst` role — see
[Auto-Assignment on Unassigned Tickets](#auto-assignment-on-unassigned-tickets)).

Request body:

```json
{
  "cve_id": "CVE-2024-1234",
  "severity": "high",
  "is_confidential": false
}
```

- `cve_id` (string, optional): CVE identifier string to associate with
  the ticket. If the CVE is not in the database, a minimal CVE record
  is created and on-demand fetch is triggered (see
  `docs/features/tickets/cve-tracking.md`, "On-demand Single-CVE Fetch")
- `severity` (string, optional): initial severity override (critical,
  high, medium, low, none). If omitted, severity is `None` until set
  by the user. Ignored if `cve_id` is provided (severity is derived from
  CVSS)
- `is_confidential` (boolean, optional): if `true`, the ticket is
  created as confidential. Requires the `manage_confidentiality`
  capability in addition to `create_ticket`. If the caller lacks
  `manage_confidentiality`, the endpoint returns 403
  `AUTH_INSUFFICIENT_PERMISSION`. Default: `false`

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(201 Created).

Error responses:

- 409 with code `TICKET_CVE_CONFLICT`: CVE is already associated with
  another ticket. Response body includes `existing_ticket_id` (UUID) to
  allow the frontend to link to the existing ticket

### Associate CVE

```
POST /api/v1/tickets/{ticket_id}/associate-cve
```

- **Capability**: `triage_ticket`
- **Response schema**: `TicketDetail`

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

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 400 with code `TICKET_CVE_ALREADY_SET`: ticket already has a CVE
  associated
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_CVE_CONFLICT`: CVE is already associated with
  another ticket. Response body includes `existing_ticket_id` (UUID) to
  allow the frontend to link to the existing ticket
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status

### Remove CVE from Ticket (Admin Only)

```
DELETE /api/v1/tickets/{ticket_id}/cve
```

- **Capability**: `admin_ticket_ops`
- **Response**: 204 No Content

Removes the CVE association from a ticket. The CVE record itself is not
deleted. After removal, severity resolution falls back to
`severity_override`.

Error responses:

- 400 with code `TICKET_CVE_NOT_SET`: ticket does not have a CVE
  associated
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status

### Set Severity Override

```
PATCH /api/v1/tickets/{ticket_id}/severity
```

- **Capability**: `triage_ticket`
- **Response schema**: `TicketDetail`

Sets the severity override for a ticket without a CVE.

Request body:

```json
{
  "severity": "high"
}
```

- `severity` (string, required): severity value (critical, high, medium,
  low, none)

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 400 with code `TICKET_SEVERITY_DERIVED`: ticket has an associated CVE
  (severity is derived from CVSS, not manually settable)
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status

### Assign Ticket

```
PATCH /api/v1/tickets/{ticket_id}/assignee
```

- **Capability**: `triage_ticket`
- **Response schema**: `TicketDetail`

Assigns or reassigns a ticket. See
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

> **No unassignment by design**: the `user_id` field is required and
> cannot be null. Via the API, a ticket can only be **reassigned** to
> another active VA — never unassigned. This enforces explicit handover.
> System-initiated unassignment may occur as a side effect of user
> deactivation (see
> [user-service.md](../identity/user-service.md#deactivate_user)); the
> promotional-only assignee gate ensures this never causes status
> regression.

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 400 with code `TICKET_ASSIGNEE_NOT_VA`: target user does not hold the
  Vulnerability Analyst role
- 400 with code `TICKET_ASSIGNEE_INACTIVE`: target user is inactive
- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 404 with code `USER_NOT_FOUND`: target user not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status (use the dedicated reopen or revert-duplicate endpoints instead)

### Ignore Ticket

```
POST /api/v1/tickets/{ticket_id}/ignore
```

- **Capability**: `triage_ticket`
- **Response schema**: `TicketDetail`

Marks a ticket as Ignored. Allowed transitions: New → Ignored,
Analysis → Ignored (see [Status Transitions](#status-transitions)). If
the ticket has no assignee, auto-assignment applies (see
[Auto-Assignment on Unassigned Tickets](#auto-assignment-on-unassigned-tickets)).

No request body is required.

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status (ticket is in Ignored or Duplicated status)
- 409 with code `TICKET_INVALID_TRANSITION`: current status does not
  allow transition to Ignored (ticket is in Analyzed or Resolved status)

### Mark Ticket as Duplicate

```
POST /api/v1/tickets/{ticket_id}/duplicate
```

- **Capability**: `triage_ticket`
- **Response schema**: `TicketDetail`

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

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 400 with code `TICKET_SELF_DUPLICATE`: resolved target is the same
  ticket (self-reference after chain resolution)
- 404 with code `TICKET_NOT_FOUND`: ticket or target ticket not found
- 409 with code `TICKET_NOT_MUTABLE`: ticket is in Ignored or Duplicated
  status
- 409 with code `TICKET_DUPLICATE_CHAIN_DEPTH`: chain depth exceeded
  (indicates data corruption requiring manual intervention)

### Reopen Ticket

```
POST /api/v1/tickets/{ticket_id}/reopen
```

- **Capability**: `triage_ticket`
- **Response schema**: `TicketDetail`

Reopens an Ignored ticket. If the calling user holds the
`vulnerability_analyst` role, they become the new assignee; otherwise,
the ticket retains its current assignee (or remains unassigned). After
assignment (if applicable), `_reenter_gate_zone()` determines the
correct gate-zone status (typically Analysis, since an assignee is now
present). See [Ignored](#ignored) for the full reopen behavior and
audit trail.

No request body is required.

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_INVALID_TRANSITION`: ticket is not in Ignored
  status

This endpoint is **not** subject to the `require_ticket_mutable` guard
(it is the dedicated exit from the Ignored manual-zone status).

### Revert Duplicate Status

```
POST /api/v1/tickets/{ticket_id}/revert-duplicate
```

- **Capability**: `triage_ticket`
- **Response schema**: `TicketDetail`

Reverts a Duplicated ticket to its previous status. If the user who
performed the revert holds the `vulnerability_analyst` role, the ticket
is reassigned to them; otherwise, the ticket retains its current
assignee. After restoring the status, `evaluate_ticket_status`
reconciles with current gate conditions.
See [Duplicate Handling](#duplicate-handling) for revert behavior and
status reconciliation.

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_INVALID_TRANSITION`: ticket is not in Duplicated
  status

### Soft-Delete Ticket

```
DELETE /api/v1/tickets/{ticket_id}
```

- **Capability**: `admin_ticket_ops`
- **Response**: 204 No Content

Soft-deletes a ticket by setting `deleted_at`. Creates a `ticket_deleted`
TicketAuditEvent. See [Soft-Delete](#soft-delete) for visibility rules and
sub-resource behavior.

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_ALREADY_DELETED`: ticket is already soft-deleted

### Restore Ticket

```
POST /api/v1/tickets/{ticket_id}/restore
```

- **Capability**: `admin_ticket_ops`
- **Response schema**: `TicketDetail`

Restores a soft-deleted ticket by clearing `deleted_at`. Creates a
`ticket_restored` TicketAuditEvent. See [Soft-Delete](#soft-delete) for
soft-delete lifecycle.

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

Error responses:

- 404 with code `TICKET_NOT_FOUND`: ticket not found
- 409 with code `TICKET_NOT_DELETED`: ticket is not soft-deleted

### Set Confidentiality

```
PATCH /api/v1/tickets/{ticket_id}/confidentiality
```

- **Capability**: `manage_confidentiality`
- **Response schema**: `TicketDetail`
- **Request body**: `{ "is_confidential": boolean }`
- **Idempotency**: If the ticket already has the requested value, the
  operation returns 200 OK without side effects.

Sets the confidentiality status of a ticket. See
[ticket-service.md](ticket-service.md#set_confidentiality) for the
service-layer contract (locking, audit events).

Response: `TicketDetail` object in standard `{"data": ...}` envelope
(200 OK).

| Status | Code | Condition |
|--------|------|-----------|
| 200    | -    | Success (or already in requested state) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found |
| 409    | `TICKET_NOT_MUTABLE` | Ticket is in Ignored or Duplicated status |

### Access Grant Management

Endpoints to manage `TicketAccessGrant` records. Requires the
`manage_confidentiality` capability.

#### List Access Grants

```
GET /api/v1/tickets/{ticket_id}/access
```

List all users with explicit access grants for a confidential ticket.

- **Capability**: `manage_confidentiality`
- **Response** (200 OK, unpaginated):
  ```json
  {
    "data": [
      {
        "user": {
          "id": "uuid",
          "username": "jdoe",
          "full_name": "John Doe",
          "active": true
        },
        "granted_at": "2025-03-15T10:30:00Z",
        "granted_by": {
          "id": "uuid",
          "username": "asmith",
          "full_name": "Alice Smith",
          "active": true
        }
      }
    ]
  }
  ```
  *Note: Unpaginated because explicit access grants per ticket are a
  bounded dataset (typically a handful of users).*

| Status | Code | Condition |
|--------|------|-----------|
| 200    | -    | Success |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

#### Grant Access

```
POST /api/v1/tickets/{ticket_id}/access
```

Grant explicit access to a user on a confidential ticket.

- **Capability**: `manage_confidentiality`
- **Request body**: `{ "user": str }` (Accepts UUID or username per User
  Identifier Resolution convention; backend resolves via
  `resolve_user_identifier`).
- **Idempotency**: If the grant already exists, returns 200 OK with the
  existing grant data (reflecting the original `granted_by` and
  `granted_at`), without creating an audit event. Otherwise, creates the
  grant and returns 201 Created.
- **Response** (200 OK or 201 Created): The grant object wrapped in the
  standard `{"data": <grant>}` envelope. The grant object has the same
  shape as items in the list response.
- **Audit**: Creates `TicketAuditEvent` with
  `event_type = access_grant_added`.

| Status | Code | Condition |
|--------|------|-----------|
| 201    | -    | Grant created |
| 200    | -    | Grant already exists (idempotent success) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 404    | `USER_NOT_FOUND` | Target user not found |
| 409    | `TICKET_NOT_MUTABLE` | Ticket is in Ignored or Duplicated status |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

#### Revoke Access

```
DELETE /api/v1/tickets/{ticket_id}/access/{user}
```

Revoke explicit access from a user on a confidential ticket. The
`{user}` path parameter is of type `str` and accepts either a UUID or
username.

- **Capability**: `manage_confidentiality`
- **Idempotency**: If the grant does not exist, returns 204 No Content
  without creating an audit event.
- **Response**: 204 No Content.
- **Audit**: Creates `TicketAuditEvent` with
  `event_type = access_grant_removed`.

| Status | Code | Condition |
|--------|------|-----------|
| 204    | -    | Grant revoked (or did not exist — idempotent success) |
| 404    | `TICKET_NOT_FOUND` | Ticket not found (or confidential and caller is not authorized) |
| 404    | `USER_NOT_FOUND` | Target user not found |
| 409    | `TICKET_NOT_MUTABLE` | Ticket is in Ignored or Duplicated status |
| 409    | `TICKET_NOT_CONFIDENTIAL` | Ticket is not confidential |

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
| created_at        | TIMESTAMPTZ   | NOT NULL, DEFAULT            | Record creation timestamp |
| updated_at        | TIMESTAMPTZ   | NOT NULL, DEFAULT            | Record update timestamp |
| is_confidential   | BOOLEAN       | NOT NULL, DEFAULT FALSE      | Confidentiality flag. See [Confidential Tickets](#confidential-tickets) |
| deleted_at        | TIMESTAMPTZ   | nullable                     | Soft-delete timestamp |

## Security

- Viewing ticket lists and details: publicly accessible (no
  authentication required). Exceptions: (1) the ticket audit log
  sub-resource (`/audit-log`) requires authentication — see
  `docs/features/tickets/ticket-audit-log.md`; (2) confidential tickets
  are invisible to users whose effective scope is not `all` (unless they
  have an explicit `TicketAccessGrant` or bugowner match) — see
  [Confidential Tickets](#confidential-tickets)
- Creating tickets: `create_ticket` capability
- Assigning, changing status, associating CVE, setting severity override:
  `triage_ticket` capability
- Managing packages: `manage_packages` capability
- Setting confidentiality, managing access grants: `manage_confidentiality`
  capability
- Removing a CVE from a ticket: `admin_ticket_ops` capability
- Soft-deleting and restoring tickets: `admin_ticket_ops` capability
- See `docs/features/identity/rbac.md` for the full permission model

## Cross-references

- `docs/features/tickets/ticket-service.md` — service-layer contract for
  non-gate ticket lifecycle operations and confidentiality management
- `docs/features/tickets/ticket-mutations.md` — ticket-centric mutations,
  `evaluate_ticket_status()`, `auto_assign_if_needed()`, concurrency rules,
  and architectural test requirements
- `docs/features/packages/package-service.md` — package-centric mutations,
  orchestration, and query operations (populates `TicketDetail.packages`)
- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
- `docs/features/tickets/ticket-audit-log.md` — audit event contract, detail
  JSONB schema
- `docs/features/identity/rbac.md` — Endpoint Permission Map
- `docs/features/packages/package-bugowner.md` — bugowner resolution for
  dynamic access
