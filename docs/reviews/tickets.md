# Review: tickets

**Spec**: `docs/features/tickets/tickets.md`
**Last reviewed**: 2026-05-19
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TKT-GAP-01 — Duplicate marking of a soft-deleted ticket is unspecified (Medium)

**Status**: RESOLVED — Added explicit validation step for soft-deleted target in Mark as Duplicate section (2026-05-19)

### TKT-GAP-02 — Cascade update when marking duplicate processes multiple tickets without single-ticket scope (Medium)

**Status**: RESOLVED — Spec now explicitly documents cascade as best-effort with independent transactions, partial updates acceptable, and correctness not depending on cascade completion (2026-05-19)

### TKT-GAP-03 — Restore of soft-deleted ticket does not specify status reconciliation (Medium)

**Status**: RESOLVED — Added evaluate_ticket_status call after restore from soft-delete for status reconciliation (2026-05-19)

### TKT-GAP-04 — Duplicate revert with previous_status = New is unspecified (Medium)

**Status**: RESOLVED — Assignee made promotional gate in evaluate_ticket_status: assignee_id IS NOT NULL raises minimum status to Analysis. Removed New from exclusion list. Auto-assignment simplified (2026-05-19)

### TKT-GAP-05 — No transition from Ignored when NVD rejection is reverted (Medium)

**Status**: RESOLVED — Removed absolute terminality of Ignored; added conditional transitions Ignored to Analysis (VA assignment) and Ignored to New (system reopen) with centralization in ticket_mutations (2026-05-19)

### TKT-GAP-06 — Associate-CVE on a ticket in Duplicated status is unspecified (Low)

**Status**: RESOLVED — Mutability guard is now centralized as require_ticket_mutable dependency documented in api-spec.md (Scoped Responses, Manual-Zone Mutability Guard) (2026-05-19)

### TKT-GAP-07 — Set-severity endpoint missing Duplicated status guard in error table (Low)

**Status**: RESOLVED — Mutability guard is now centralized as require_ticket_mutable dependency documented in api-spec.md (Scoped Responses, Manual-Zone Mutability Guard) (2026-05-19)

### TKT-GAP-08 — Unassign operation not specified (Low)

**Status**: RESOLVED — Explicitly declared that unassignment is not supported: user_id is required, a ticket can only be reassigned to another VA (handover by design) (2026-05-19)

### TKT-GAP-09 — Ticket creation with invalid CVE-ID format has no specified error (Low)

**Category**: Boundary conditions
**Status**: OPEN

The Create Ticket and Associate CVE endpoints accept a cve_id string (e.g., "CVE-2024-1234"). The spec does not define what happens when the string does not match a valid CVE-ID format (e.g., "not-a-cve", "CVE-abc-xyz"). While Pydantic validation likely handles this, the spec should indicate the expected format constraint so that the on-demand fetch logic is never triggered for malformed identifiers.

### TKT-GAP-10 — Assign endpoint vs mutability guard contradiction (Medium)

**Category**: Contradictions
**Status**: OPEN

The Reassignment section states that ticket assignment can occur "at any time, regardless of status," but the centralized mutability guard blocks mutations on tickets in Ignored or Duplicated status. These two statements directly conflict. The spec should either exempt the assign endpoint from the mutability guard or restrict reassignment to mutable statuses.

### TKT-GAP-11 — Ignore endpoint mutability guard evaluation order unclear (Low)

**Category**: Ambiguity
**Status**: OPEN

For tickets in the gate zone, the mutability guard passes (Ignored is not in the blocked set for the ignore operation itself), then the transition check fires. However, listing "ignore" in the mutability scope could mislead implementers into thinking the guard applies to the ignore endpoint. The evaluation order and scope exclusion should be documented explicitly.

### TKT-GAP-12 — Deactivation-triggered unassignment contradicts "unassignment not supported" (Medium)

**Category**: Contradictions
**Status**: OPEN

The spec states "unassignment is not a supported operation" but the user-service deactivation flow can set assignee_id to NULL. The promotional-only gate in evaluate_ticket_status handles this correctly (ticket stays in current status, just loses assignee), but the absolute statement about unassignment is misleading. The spec should acknowledge system-initiated unassignment as a distinct case.

### TKT-GAP-13 — evaluate_ticket_status behavior on New tickets undocumented (Low)

**Category**: Missing documentation
**Status**: OPEN

Calling evaluate_ticket_status on a New ticket is safe and correct (evaluates to New, which is a no-op), but this behavior is not explicitly documented. Since several flows can trigger evaluation on New tickets (e.g., restore from soft-delete, orphan cleanup), the spec should confirm that evaluation on New is a defined no-op.

### TKT-GAP-14 — Race between CVE dissociation and concurrent on-demand fetch (Low)

**Category**: Concurrency
**Status**: OPEN

If a VA dissociates a CVE from a ticket while a background on-demand fetch for that same CVE is in progress, the behavior is implicitly correct (the fetch would create a new ticket, which is documented as intentional). However, this specific race condition is not explicitly documented as a considered edge case.

### TKT-GAP-15 — Stale grant cleanup uses updated_at which changes on any ticket modification (Medium)

**Category**: Edge Cases
**Status**: OPEN

The stale access grant cleanup task uses the ticket's updated_at timestamp to determine staleness. However, updated_at changes on any ticket modification (status change, reassignment, etc.), not just when confidentiality is toggled off. A non-confidential ticket that keeps being modified will never have its grants cleaned up because updated_at keeps refreshing. The cleanup condition should use a dedicated timestamp (e.g., confidentiality_changed_at) or filter on is_confidential = false explicitly.

### TKT-GAP-16 — Sequence ID gaps not documented (Low)

**Category**: Missing documentation
**Status**: OPEN

Ticket sequence IDs use a PostgreSQL sequence, which produces gaps on rolled-back transactions. This is standard PostgreSQL behavior and not a bug, but users may perceive gaps as missing tickets. The spec should document that sequence ID gaps are expected and do not indicate data loss.

### TKT-GAP-17 — cve_data_pending lifecycle undefined (Medium)

**Category**: Missing specification
**Status**: OPEN

The cve_data_pending flag is set to true when a ticket is created with a CVE-ID that requires on-demand fetch, but the spec does not define when or how it transitions to false. Specifically: does the background fetch task set it to false on success? What happens if all fetch attempts fail permanently? Is there a maximum retry count or timeout after which the flag is cleared?

### TKT-GAP-18 — Case B package addition can regress Resolved tickets (Medium)

**Category**: Edge Cases
**Status**: OPEN

When a package is added to a Resolved ticket via Case B (automatic addition from CVE CPE mapping), the new TicketPackageTrack records are created with status ANALYSIS. This triggers evaluate_ticket_status, which will regress the ticket from Resolved back to Analysis (or New). While this is correct per the status evaluation rules, the spec does not explicitly acknowledge this regression as intentional behavior for Resolved tickets.

### TKT-GAP-19 — Assignee preservation on Ignore transition not explicitly stated (Low)

**Category**: Missing documentation
**Status**: OPEN

When a ticket transitions to Ignored status, the spec does not explicitly state whether the assignee_id is preserved or cleared. The behavior is implicitly correct (assignee is preserved since there is no documented clearing step), but since Ignored tickets are outside the normal workflow, the spec should confirm this explicitly.

### TKT-GAP-20 — Auto-assignment on Ignore from New audit event sequence undocumented (Low)

**Category**: Missing documentation
**Status**: OPEN

When a VA ignores a ticket that is in New status, auto-assignment occurs (the VA becomes the assignee). The spec documents the audit event sequence for mark-as-duplicate (which also triggers auto-assignment), but does not document the equivalent sequence for the ignore operation: whether the assignment event precedes or follows the status_changed event.

---

## Coherence

### TKT-COH-01 — Tickets spec Security section says viewing is 'publicly accessible' but confidential tickets contradict this (Low)

**Status**: RESOLVED — The Security section explicitly lists confidential tickets as an exception in the same sentence. The RBAC spec marks these endpoints as "Public" which is correct (confidentiality filtering is an additional layer applied after auth, not an access level). No actual contradiction exists. (2026-05-18)

### TKT-COH-02 — Stale Access Grant Cleanup task exemption from BaseFetcher is consistent with conventions (Low)

**Status**: RESOLVED — The exemption is explicitly documented and consistent with the BaseFetcher contract which applies only to tasks fetching from external sources. (2026-05-18)

### TKT-COH-03 — Mark-as-Duplicate target confidentiality check missing (Medium)

**Status**: RESOLVED — Cross-agent duplicate of TKT-SEC-07 (2026-05-19)

### TKT-COH-04 — CVE conflict response leaks confidential ticket UUID (Medium)

**Status**: RESOLVED — Cross-agent duplicate of TKT-SEC-02 (2026-05-19)

### TKT-COH-05 — Cascade update bypasses confidentiality without documentation (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKT-SEC-03 (2026-05-19)

### TKT-COH-06 — Ignore transition source states in permission matrix (Low)

**Status**: RESOLVED — Permission Matrix is a summary table; not a contradiction with the detailed transition rules in the spec body (2026-05-19)

### TKT-COH-07 — duplicate_target_changed not in detail JSONB Schema Contract table in ticket-audit-log.md (Low)

**Category**: Cross-spec inconsistency
**Status**: OPEN

The tickets spec defines a duplicate_target_changed audit event type (emitted during cascade update when a ticket's duplicate_of_id is re-pointed). However, the ticket-audit-log.md detail JSONB Schema Contract table does not include an entry for this event type, leaving the expected detail payload structure undefined.

---

## Design

### TKT-DES-01 — Duplicate cascade update violates single-ticket-per-transaction rule (High)

**Status**: RESOLVED — Spec now explicitly documents cascade as best-effort flattening with independent transactions, and states correctness does not depend on immediate flatness (2026-05-19)

### TKT-DES-02 — Duplicate chain resolution is vulnerable to concurrent marking (Medium)

**Status**: RESOLVED — Spec now explicitly addresses this race under Cycle Prevention section, accepting it as a residual risk with detection at read time via the canonical resolver (2026-05-19)

### TKT-DES-03 — CVE dissociation race with background CVE sync re-creating the ticket (Medium)

**Status**: RESOLVED — Spec explicitly states that new ticket creation after CVE dissociation is intentional behavior to ensure CVEs are not lost (2026-05-19)

### TKT-DES-04 — Stale access grant cleanup misses soft-deleted confidential tickets permanently (Medium)

**Category**: Edge Cases
**Status**: OPEN

The spec states: "Soft-deleted confidential ticket: is_confidential is still TRUE, grants preserved. If the ticket is later restored, all grants are intact. To clean them, an Admin must first restore the ticket, then a VA removes confidentiality, and the cleanup runs 14 days later." If a confidential ticket is soft-deleted and never restored, its access grants persist indefinitely. Over years, this constitutes unbounded growth for abandoned tickets. Consider adding a secondary condition: also delete grants for tickets where deleted_at is older than 90 days.

### TKT-DES-05 — Orphan cleanup cascade calls evaluate_ticket_status multiple times per transaction (Low)

**Category**: Complexity
**Status**: OPEN

The spec shows: "soft_delete_ticket_package_product calls evaluate_ticket_status, which calls _enforce_track_orphan_rule, which calls evaluate_ticket_status, which calls _enforce_package_orphan_rule, which calls evaluate_ticket_status." The function is called up to 3 times in one transaction. While correct (idempotent), it is unnecessary work. Consider deferring evaluate_ticket_status to a single call at the end.

### TKT-DES-06 — evaluate_ticket_status can regress Resolved to New on restore (Medium)

**Category**: Edge Cases
**Status**: OPEN

When a soft-deleted ticket is restored, evaluate_ticket_status is called for status reconciliation. If all packages were removed while the ticket was deleted (or tracks/products changed), the evaluation could regress a previously Resolved ticket to New. The spec should document whether this regression is intentional or whether restore should preserve the pre-deletion status as a floor.

### TKT-DES-07 — Ignore from Analyzed not supported but may be needed for workflow (Medium)

**Category**: Workflow gaps
**Status**: OPEN

The Ignore transition is only specified from New and Analysis statuses. A ticket that has reached Analyzed status cannot be ignored, even if the VA determines the CVE is not relevant after full analysis. This may force VAs to work around the limitation. The spec should either add the Analyzed to Ignored transition or document the rationale for excluding it.

### TKT-DES-08 — Mark-as-duplicate target accessibility and confidentiality (Medium)

**Status**: RESOLVED — Cross-agent duplicate of TKT-SEC-07 (2026-05-19)

### TKT-DES-09 — CVE conflict response leaks confidential UUID (Medium)

**Status**: RESOLVED — Cross-agent duplicate of TKT-SEC-02 (2026-05-19)

### TKT-DES-10 — Auto-assignment on mark-as-duplicate is semantically hollow (Low)

**Category**: Design quality
**Status**: OPEN

When marking a ticket as duplicate, the acting VA is auto-assigned to the ticket if it was previously unassigned. Since the ticket immediately enters Duplicated status (which blocks most mutations), the assignment serves no practical workflow purpose. The VA cannot act on the ticket further. While the assignment creates a useful audit trail, the spec should acknowledge this semantic limitation.

### TKT-DES-11 — Ticket response schema undefined (Medium)

**Status**: RESOLVED — Cross-agent duplicate of TKT-API-06 (2026-05-19)

### TKT-DES-12 — Search across package names performance at scale (Low)

**Category**: Scalability
**Status**: OPEN

The search parameter on GET /api/v1/tickets searches across ticket title, CVE-ID, and package names. Package name search requires joining through TicketPackage records for every search query. At scale (thousands of tickets with multiple packages each), this join-based search could become a performance bottleneck. The spec should indicate whether a database index strategy is expected or whether full-text search should be considered.

---

## Security

### TKT-SEC-01 — Duplicate chain resolution leaks confidential ticket existence (Medium)

**Category**: Authorization
**Status**: OPEN

When marking a ticket as duplicate via POST /api/v1/tickets/{ticket_id}/duplicate, the spec defines chain resolution that follows duplicate_of_id links up to depth 10. There is no specification that the resolved target ticket must be accessible to the caller. If an intermediate ticket in the chain is confidential and the caller is not authorized, the operation could leak the existence of confidential tickets by successfully resolving through them rather than failing.

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

The POST /api/v1/tickets endpoint has no rate limiting specified. While api-spec.md acknowledges rate limiting is "not enforced at this time," ticket creation triggers background tasks (on-demand CVE fetch). A malicious VA could create thousands of tickets with non-existent CVE-IDs, triggering a flood of background fetch tasks.

### TKT-SEC-05 — Access grant management allows granting access to inactive users (Low)

**Category**: Authorization
**Status**: OPEN

The POST /api/v1/tickets/{ticket_id}/access (Grant Access) endpoint accepts any resolved user but does not specify whether inactive users can be granted access. For consistency with the assignment constraint (only active VAs can be assigned), the spec should explicitly state whether inactive users can receive access grants.

### TKT-SEC-06 — Search timing side-channel on confidential tickets (Low)

**Category**: Data exposure
**Status**: OPEN

The search parameter on GET /api/v1/tickets searches across package names. While confidential tickets are filtered from results, the spec should clarify that the search implementation does not leak information about confidential tickets via timing side-channels (e.g., slower queries when a search term matches a confidential ticket's packages).

### TKT-SEC-07 — Mark-as-duplicate target confidentiality check missing (Medium)

**Category**: Authorization
**Status**: OPEN

The mark-as-duplicate endpoint validates that the target ticket exists, is not soft-deleted, and is not the same ticket. However, it does not specify a confidentiality check on the target. A VA could mark their ticket as duplicate of a confidential ticket they are not authorized to view, confirming the confidential ticket's existence and creating a relationship to it.

### TKT-SEC-08 — Public ticket endpoints expose vulnerability information (Low)

**Category**: Data exposure
**Status**: OPEN

The ticket list and detail endpoints are marked as Public access level, meaning any authenticated user can view ticket data including CVE details, affected packages, and severity. While this is by design (security team transparency), the spec should explicitly acknowledge that this exposes vulnerability information before fixes are available and confirm this is an accepted risk.

### TKT-SEC-09 — CVE-ID format validation missing from security perspective (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKT-GAP-09 (2026-05-19)

### TKT-SEC-10 — Any VA can ignore ticket assigned to another VA (Low)

**Category**: Authorization
**Status**: OPEN

The ignore endpoint requires Vulnerability Analyst access level but does not specify whether only the assigned VA (or an unassigned ticket's ignorer) can perform this action. Any VA can ignore any ticket, even one actively being worked on by another VA. The spec should document whether this is intentional or whether an ownership check is needed.

---

## API Conventions

### TKT-API-01 — List Tickets endpoint missing explicit access level declaration (Low)

**Category**: Authorization
**Status**: OPEN

The "List Tickets" endpoint section does not explicitly declare its access level using the standard labels (Public / Authenticated / Vulnerability Analyst / Admin).

### TKT-API-02 — Get Ticket endpoint missing explicit access level declaration (Low)

**Category**: Authorization
**Status**: OPEN

Same as finding TKT-API-01 — the "Get Ticket" endpoint does not explicitly declare its access level inline.

### TKT-API-03 — List Tickets missing sortable fields specification (Low)

**Category**: Sorting
**Status**: OPEN

The List Tickets endpoint declares sort_by and sort_order parameters but does not enumerate which fields are valid for sort_by.

### TKT-API-04 — DELETE /tickets/{ticket_id}/cve returns 204 but does not use data envelope (Low)

**Status**: RESOLVED — The convention requires 204 No Content for DELETE operations with no response body, which is consistent with api-spec.md. (2026-05-18)

### TKT-API-05 — TICKET_CVE_CONFLICT error code not listed in api-spec.md Error Code Categories (Medium)

**Status**: RESOLVED — The convention requires codes to be defined in the Python enum; the examples table in api-spec.md is illustrative, not exhaustive. The spec correctly uses the TICKET_* prefix and documents HTTP status + error code for each error. (2026-05-18)

### TKT-API-06 — Ticket object response schema not defined (Medium)

**Category**: API completeness
**Status**: OPEN

The tickets spec does not define which fields are included in the "ticket object" returned by the list endpoint (GET /api/v1/tickets) vs the detail endpoint (GET /api/v1/tickets/{ticket_id}). The spec only says "paginated list in data/meta envelope" for lists and "ticket object in data envelope" for detail. The only mentioned difference is that detail "includes bugowner information for each package." Fields like duplicate_of_id, previous_status, is_confidential, and nested relationships (packages, tracks, products) have no explicit inclusion/exclusion per endpoint. This makes it ambiguous for implementers and API consumers which fields to expect in each context.

### TKT-API-07 — Inconsistent access level declaration format across endpoints (Low)

**Category**: Consistency
**Status**: OPEN

Some endpoints in the tickets spec declare access levels inline in the endpoint description while others rely on the RBAC spec's Endpoint Permission Map. The spec should use a consistent format for all endpoints, preferably the inline declaration pattern used by other reviewed specs.

### TKT-API-08 — New error codes not in examples (Low)

**Status**: RESOLVED — Error codes defined in the spec follow the TICKET_* prefix convention. The api-spec.md examples table is illustrative, not exhaustive. (2026-05-19)
