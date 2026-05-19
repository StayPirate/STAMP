# Review: tickets

**Spec**: `docs/features/tickets/tickets.md`
**Last reviewed**: 2026-05-19
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TKT-GAP-01 — Duplicate marking of a soft-deleted ticket is unspecified (Medium)

**Category**: State machine completeness
**Status**: OPEN

The spec states "Any ticket can be marked as a duplicate of another ticket, from any status" (Duplicate Handling section). The soft-delete section states "Soft-deleted tickets are invisible to all business logic". However, it is unspecified whether: (a) a soft-deleted ticket can be marked as duplicate (it shouldn't be visible, but the transition table says "any status"), and (b) a non-deleted ticket can be marked as duplicate OF a soft-deleted ticket (the target resolution follows the chain, but what if the resolved target is soft-deleted?). A VA could attempt to mark ticket A as duplicate of ticket B where B has been soft-deleted by an admin — the spec does not state whether the target must be active.

### TKT-GAP-02 — Cascade update when marking duplicate processes multiple tickets without single-ticket scope (Medium)

**Status**: RESOLVED — Auto-resolved: spec now explicitly documents cascade as best-effort with independent transactions, partial updates acceptable, and correctness not depending on cascade completion (Cascade as Best-Effort Flattening section) (2026-05-19)

### TKT-GAP-03 — Restore of soft-deleted ticket does not specify status reconciliation (Medium)

**Category**: State machine completeness
**Status**: OPEN

The spec states a soft-deleted ticket can be restored by clearing "deleted_at". However, while the ticket was soft-deleted, its gate-relevant data may have changed externally (e.g., CVSS scores updated, product eligibility recalculated). The spec does not specify whether "evaluate_ticket_status" is called after restoration to reconcile the ticket's status with current gate conditions — unlike the duplicate revert, which explicitly calls evaluate_ticket_status after restoring previous_status.

### TKT-GAP-04 — Duplicate revert with previous_status = New is unspecified (Medium)

**Category**: State machine completeness
**Status**: OPEN

The spec states when a duplicate is reverted: "status is restored to previous_status" and then "evaluate_ticket_status is called to reconcile". However, evaluate_ticket_status "only evaluates tickets in Analysis, Analyzed, or Resolved status. Tickets in New, Ignored, or Duplicated are excluded." If a ticket was in "New" when marked as duplicate, reverting would restore it to "New", but then evaluate_ticket_status would not run (it's excluded). The ticket would remain in "New" with an assignee (the VA who reverted), which contradicts the New status semantics (unassigned). The spec does not address this case.

### TKT-GAP-05 — No transition from Ignored when NVD rejection is reverted (Medium)

**Category**: State machine completeness
**Status**: OPEN

The spec states "Ignored is a terminal status — there is no transition from Ignored to any other status." It also references NVD rejection revert handling in cve-tracking.md. If a ticket was auto-transitioned to Ignored due to NVD rejection (New → Ignored), and then NVD reverts the rejection, the ticket is stuck in Ignored with no way to automatically or programmatically undo it. The note says "see docs/features/tickets/cve-tracking.md" for rejection revert handling but the tickets spec explicitly declares Ignored as terminal with only admin soft-delete as recourse. This creates an operational gap where legitimate CVEs rejected in error by NVD cannot have their tickets automatically recovered.

### TKT-GAP-06 — Associate-CVE on a ticket in Duplicated status is unspecified (Low)

**Category**: Error paths
**Status**: OPEN

The spec states under "Modifications in Inactive Statuses": "Duplicated: modifications are blocked by the API — endpoints that modify ticket data return 409 if the ticket is in Duplicated status". However, the Associate CVE endpoint (POST /api/v1/tickets/{ticket_id}/associate-cve) does not list a 409 error for Duplicated status in its error responses table. It's implied by the general rule but not explicit in the endpoint definition. A developer implementing this endpoint might miss the Duplicated guard.

### TKT-GAP-07 — Set-severity endpoint missing Duplicated status guard in error table (Low)

**Category**: Error paths
**Status**: OPEN

The Set Severity Override endpoint lists only TICKET_SEVERITY_DERIVED and TICKET_NOT_FOUND errors. Per the general rule "Duplicated: modifications are blocked by the API — endpoints that modify ticket data return 409", this endpoint should also reject calls on Duplicated tickets, but the error table does not document this. Same applies to the Ignore endpoint for tickets already in Duplicated status (though the status check would catch it via TICKET_INVALID_TRANSITION).

### TKT-GAP-08 — Unassign operation not specified (Low)

**Category**: User-facing scenario gaps
**Status**: OPEN

The spec defines assignment and reassignment but does not specify whether a ticket can be unassigned (set assignee_id back to NULL). The Assign Ticket endpoint requires a user_id (required field). If a VA needs to step away from a ticket and no replacement is available, there is no mechanism to return it to the unassigned pool. This may be intentional but is not explicitly stated as a deliberate omission.

### TKT-GAP-09 — Ticket creation with invalid CVE-ID format has no specified error (Low)

**Category**: Boundary conditions
**Status**: OPEN

The Create Ticket and Associate CVE endpoints accept a cve_id string (e.g., "CVE-2024-1234"). The spec does not define what happens when the string does not match a valid CVE-ID format (e.g., "not-a-cve", "CVE-abc-xyz"). While Pydantic validation likely handles this, the spec should indicate the expected format constraint so that the on-demand fetch logic is never triggered for malformed identifiers.

---

## Coherence

### TKT-COH-01 — Tickets spec Security section says viewing is 'publicly accessible' but confidential tickets contradict this (Low)

**Status**: RESOLVED — The Security section explicitly lists confidential tickets as an exception in the same sentence. The RBAC spec marks these endpoints as "Public" which is correct (confidentiality filtering is an additional layer applied after auth, not an access level). No actual contradiction exists. (2026-05-18)

### TKT-COH-02 — Stale Access Grant Cleanup task exemption from BaseFetcher is consistent with conventions (Low)

**Status**: RESOLVED — The exemption is explicitly documented and consistent with the BaseFetcher contract which applies only to tasks fetching from external sources. (2026-05-18)

---

## Design

### TKT-DES-01 — Duplicate cascade update violates single-ticket-per-transaction rule (High)

**Status**: RESOLVED — Auto-resolved: spec now explicitly documents cascade as best-effort flattening with independent transactions, and states correctness does not depend on immediate flatness (lines 738-751, 839) (2026-05-19)

### TKT-DES-02 — Duplicate chain resolution is vulnerable to concurrent marking (Medium)

**Status**: RESOLVED — Auto-resolved: spec now explicitly addresses this race under Cycle Prevention section, accepting it as a residual risk with detection at read time via the canonical resolver (2026-05-19)

### TKT-DES-03 — CVE dissociation race with background CVE sync re-creating the ticket (Medium)

**Status**: RESOLVED — Auto-resolved: spec explicitly states that new ticket creation after CVE dissociation is intentional behavior to ensure CVEs are not lost (2026-05-19)

### TKT-DES-04 — Stale access grant cleanup misses soft-deleted confidential tickets permanently (Medium)

**Category**: Edge Cases
**Status**: OPEN

The spec states: "Soft-deleted confidential ticket → is_confidential is still TRUE → grants preserved. If the ticket is later restored, all grants are intact. To clean them, an Admin must first restore the ticket, then a VA removes confidentiality — the cleanup runs 14 days later." If a confidential ticket is soft-deleted and never restored, its access grants persist indefinitely. Over years, this constitutes unbounded growth for abandoned tickets. Alternative: add a secondary condition — also delete grants for tickets where deleted_at is older than 90 days.

### TKT-DES-05 — Orphan cleanup cascade calls evaluate_ticket_status multiple times per transaction (Low)

**Category**: Complexity
**Status**: OPEN

The spec shows: "soft_delete_ticket_package_product → evaluate_ticket_status() → _enforce_track_orphan_rule() → evaluate_ticket_status() → _enforce_package_orphan_rule() → evaluate_ticket_status()". The function is called up to 3 times in one transaction. While correct (idempotent), it's unnecessary work. Alternative: defer evaluate_ticket_status to a single call at the end.

---

## Security

### TKT-SEC-01 — Duplicate chain resolution lacks authorization check on target ticket (Medium)

**Category**: Authorization
**Status**: OPEN

When marking a ticket as duplicate via POST /api/v1/tickets/{ticket_id}/duplicate, the spec defines chain resolution that follows duplicate_of_id links up to depth 10. However, there is no specification that the resolved target ticket must be accessible to the caller. If the target is confidential and the caller is not authorized, the operation could leak the existence of confidential tickets by successfully resolving through them.

### TKT-SEC-02 — Confidential ticket existence leakage via 409 CVE conflict response (Medium)

**Category**: Data exposure
**Status**: OPEN

When creating a ticket or associating a CVE, if the CVE is already associated with another ticket, the API returns 409 with existing_ticket_id. If that existing ticket is confidential, this response leaks the UUID of a confidential ticket to any VA who may not be authorized to access it.

### TKT-SEC-03 — Cascade update on duplicate marking modifies tickets without authorization check (Medium)

**Category**: Authorization
**Status**: OPEN

When marking ticket B as duplicate of C, all tickets whose duplicate_of_id points to B are automatically updated to point to C. This cascade modifies other tickets (potentially confidential ones) without checking whether the acting VA has access to them. The spec should explicitly state that cascade updates bypass confidentiality checks as a system operation.

### TKT-SEC-04 — No rate limiting on ticket creation endpoint (Low)

**Category**: Insecure patterns
**Status**: OPEN

The POST /api/v1/tickets endpoint has no rate limiting specified. While api-spec.md acknowledges rate limiting is "not enforced at this time", ticket creation triggers background tasks (on-demand CVE fetch). A malicious VA could create thousands of tickets with non-existent CVE-IDs, triggering a flood of background fetch tasks.

### TKT-SEC-05 — Access grant management allows granting access to inactive users (Low)

**Category**: Authorization
**Status**: OPEN

The POST /api/v1/tickets/{ticket_id}/access (Grant Access) endpoint accepts any resolved user but does not specify whether inactive users can be granted access. For consistency with the assignment constraint, the spec should explicitly state whether inactive users can receive access grants.

### TKT-SEC-06 — Public ticket list exposes package names that may indicate confidential vulnerability scope (Low)

**Category**: Data exposure
**Status**: OPEN

The search parameter on GET /api/v1/tickets searches across package names. While confidential tickets are filtered from results, the spec should clarify that the search implementation does not leak information about confidential tickets via timing side-channels.

---

## API Conventions

### TKT-API-01 — List Tickets endpoint missing explicit access level declaration (Low)

**Category**: Authorization
**Status**: OPEN

The "List Tickets" endpoint section does not explicitly declare its access level using the standard labels (Public / Authenticated / Vulnerability Analyst / Admin).

### TKT-API-02 — Get Ticket endpoint missing explicit access level declaration (Low)

**Category**: Authorization
**Status**: OPEN

Same as finding #1 — the "Get Ticket" endpoint does not explicitly declare its access level inline.

### TKT-API-03 — List Tickets missing sortable fields specification (Low)

**Category**: Sorting
**Status**: OPEN

The List Tickets endpoint declares sort_by and sort_order parameters but does not enumerate which fields are valid for sort_by.

### TKT-API-04 — DELETE /tickets/{ticket_id}/cve returns 204 but does not use data envelope (Low)

**Status**: RESOLVED — The convention requires 204 No Content for DELETE operations with no response body, which is consistent with api-spec.md. (2026-05-18)

### TKT-API-05 — TICKET_CVE_CONFLICT error code not listed in api-spec.md Error Code Categories (Medium)

**Status**: RESOLVED — The convention requires codes to be defined in the Python enum — the examples table in api-spec.md is illustrative, not exhaustive. The spec correctly uses the TICKET_* prefix and documents HTTP status + error code for each error. (2026-05-18)

### TKT-API-06 — Ticket object response schema not defined (Medium)

**Category**: API completeness
**Status**: OPEN

The tickets spec does not define which fields are included in the "ticket object" returned by the list endpoint (`GET /api/v1/tickets`) vs the detail endpoint (`GET /api/v1/tickets/{ticket_id}`). The spec only says "paginated list in `{"data": [...], "meta": {...}}` envelope" for lists and "ticket object in `{"data": ...}` envelope" for detail. The only mentioned difference is that detail "includes bugowner information for each package." Fields like `duplicate_of_id`, `previous_status`, `is_confidential`, and nested relationships (packages, tracks, products) have no explicit inclusion/exclusion per endpoint. This makes it ambiguous for implementers and API consumers which fields to expect in each context.
