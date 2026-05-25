# Review: ticket-service

**Spec**: `docs/features/tickets/ticket-service.md`
**Last reviewed**: 2026-05-25
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TKS-GAP-01 — associate_cve CVE Resolution I/O ordering inside FOR UPDATE lock (Medium)

**Category**: Transaction hygiene
**Status**: OPEN

The `associate_cve` behavioral steps show step 1 as "Acquire FOR UPDATE
on the Ticket row" and step 4 as "Resolve CVE via CVE Resolution
Behavior." The CVE Resolution Behavior (defined in `tickets.md`) includes
an on-demand fetch that creates a minimal CVE record and triggers a
background NVD fetch. While the NVD HTTP call is asynchronous, the spec
does not clarify that only the local CVE record INSERT happens inside the
locked transaction — not the external HTTP call. An implementer could
interpret "resolve CVE" as including the full NVD fetch synchronously,
violating the transaction hygiene rule in `docs/conventions.md`
(Transaction and Locking) and blocking concurrent mutations on the ticket.

### TKS-GAP-02 — mark_as_duplicate cascade does not filter soft-deleted tickets (Medium)

**Category**: Data lifecycle
**Status**: OPEN

The `mark_as_duplicate` cascade orchestration (step 8) queries tickets
that currently point to the source ticket via `duplicate_of_id` and
returns their IDs as `cascade_ticket_ids`. The query does not specify a
`deleted_at IS NULL` filter. If a ticket in the cascade set is
soft-deleted, the cascade would update its `duplicate_of_id` and create
a `TicketAuditEvent` on a ticket that is supposed to be "invisible to
all business logic" per `tickets.md`. The cascade query should likely
filter `deleted_at IS NULL` to maintain this invariant.

### TKS-GAP-03 — mark_as_duplicate cascade fan-in without bound (Medium)

**Category**: Temporal / concurrency
**Status**: OPEN

The cascade phase of `mark_as_duplicate` runs synchronously — each
cascade ticket is updated in its own transaction before the API response
returns. While `tickets.md` notes that chains "longer than two tickets
are almost nonexistent," a fan-in scenario (many tickets all marked as
duplicates of the same target) could produce a large cascade set. If 100
tickets point to ticket B and B is marked as duplicate of C, the cascade
updates all 100 tickets synchronously, each requiring FOR UPDATE, a
write, and a commit. The spec should either acknowledge this as an
accepted slow-but-correct case or set a bound (e.g., defer to a
background task if cascade exceeds N items).

### TKS-GAP-04 — set_confidentiality on soft-deleted ticket has no deleted_at guard (Medium)

**Category**: Error paths
**Status**: OPEN

The `set_confidentiality` function applies the immutability guard
(rejects Ignored/Duplicated tickets) but does not check `deleted_at`.
The API layer's `require_accessible_ticket` dependency returns 410
`TICKET_DELETED` before the service function is reached, but the service
function itself has no guard. A system caller (or a future code path
that bypasses the API layer) could toggle confidentiality on a
soft-deleted ticket, creating an audit event on a ticket that is
supposed to be "invisible to all business logic." The service function
should either check `deleted_at IS NULL` or the spec should explicitly
document that the API layer is the sole enforcement point.

---

## Coherence

### TKS-COH-01 — SeverityEnum vs Severity naming inconsistency (Minor)

**Status**: RESOLVED — Renamed `SeverityEnum` to `Severity` in
`ticket-service.md` to match `ticket-mutations.md` convention
(2026-05-25)

---

## Design

_No findings._

---

## Security

_No findings._

---

## API Conventions

### TKS-API-01 — InvalidAssigneeError mapped to non-existent INVALID_ASSIGNEE error code (High)

**Status**: RESOLVED — Service Exceptions table updated to map
`InvalidAssigneeError` to `TICKET_ASSIGNEE_NOT_VA` or
`TICKET_ASSIGNEE_INACTIVE` with `reason` attribute documentation,
matching `tickets.md` and `api-spec.md` (2026-05-25)
