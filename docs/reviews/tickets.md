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

**Status**: RESOLVED — Mutability guard scope clarified: removed ambiguous example list, kept only exception list; Reassignment section now restricts to mutable statuses; added TICKET_NOT_MUTABLE to Assign endpoint and all 8 other mutation endpoints missing it (2026-05-19)

### TKT-GAP-11 — Ignore endpoint mutability guard evaluation order unclear (Low)

**Category**: Ambiguity
**Status**: OPEN

For tickets in the gate zone, the mutability guard passes (Ignored is not in the blocked set for the ignore operation itself), then the transition check fires. However, listing "ignore" in the mutability scope could mislead implementers into thinking the guard applies to the ignore endpoint. The evaluation order and scope exclusion should be documented explicitly.

### TKT-GAP-12 — Deactivation-triggered unassignment contradicts "unassignment not supported" (Medium)

**Status**: RESOLVED — Clarified distinction between user-initiated unassignment (not supported via API) and system-initiated unassignment (deactivation side effect); added revisit queue mention in user-service.md deactivation flow; added cross-references (2026-05-19)

### TKT-GAP-13 — evaluate_ticket_status behavior on New tickets undocumented (Low)

**Category**: Missing documentation
**Status**: OPEN

Calling evaluate_ticket_status on a New ticket is safe and correct (evaluates to New, which is a no-op), but this behavior is not explicitly documented. Since several flows can trigger evaluation on New tickets (e.g., restore from soft-delete, orphan cleanup), the spec should confirm that evaluation on New is a defined no-op.

### TKT-GAP-14 — Race between CVE dissociation and concurrent on-demand fetch (Low)

**Category**: Concurrency
**Status**: OPEN

If a VA dissociates a CVE from a ticket while a background on-demand fetch for that same CVE is in progress, the behavior is implicitly correct (the fetch would create a new ticket, which is documented as intentional). However, this specific race condition is not explicitly documented as a considered edge case.

### TKT-GAP-15 — Stale grant cleanup uses updated_at which changes on any ticket modification (Medium)

**Status**: RESOLVED — Accepted risk: the cleanup task is best-effort by design; delayed grant removal for actively-modified tickets is acceptable and does not warrant additional query complexity (audit event join) or schema changes (2026-05-19)

### TKT-GAP-16 — Sequence ID gaps not documented (Low)

**Category**: Missing documentation
**Status**: OPEN

Ticket sequence IDs use a PostgreSQL sequence, which produces gaps on rolled-back transactions. This is standard PostgreSQL behavior and not a bug, but users may perceive gaps as missing tickets. The spec should document that sequence ID gaps are expected and do not indicate data loss.

### TKT-GAP-17 — cve_data_pending lifecycle undefined (Medium)

**Status**: RESOLVED — Deferred: this finding belongs to the cve-tracking spec (currently disabled/WIP), not to the tickets spec; will be addressed when cve-tracking is defined (2026-05-19)

### TKT-GAP-18 — Case B package addition can regress Resolved tickets (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes; the Automatic Status Re-evaluation section (line 363) explicitly documents Resolved → Analysis regression when a new package is added with tracks in ANALYSIS (2026-05-19)

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

**Status**: RESOLVED — Accepted risk: the cleanup task is best-effort by design; unbounded grant growth for permanently soft-deleted confidential tickets is acceptable given the low volume per ticket and the administrative restore path remains available (2026-05-19)

### TKT-DES-05 — Orphan cleanup cascade calls evaluate_ticket_status multiple times per transaction (Low)

**Category**: Complexity
**Status**: OPEN

The spec shows: "soft_delete_ticket_package_product calls evaluate_ticket_status, which calls _enforce_track_orphan_rule, which calls evaluate_ticket_status, which calls _enforce_package_orphan_rule, which calls evaluate_ticket_status." The function is called up to 3 times in one transaction. While correct (idempotent), it is unnecessary work. Consider deferring evaluate_ticket_status to a single call at the end.

### TKT-DES-06 — evaluate_ticket_status can regress Resolved to New on restore (Medium)

**Status**: RESOLVED — Auto-resolved: the premise (packages removed while soft-deleted) is impossible under the soft-delete invisibility rule (lines 925-929); regression on restore is explicitly documented as intentional (lines 935-947); the inactive assignee scenario is now covered by the new Inactive Assignee Sanitization step in evaluate_ticket_status (2026-05-19)

### TKT-DES-07 — Ignore from Analyzed not supported but may be needed for workflow (Medium)

**Status**: RESOLVED — Design rationale documented: Analyzed→Ignored excluded because removing packages triggers automatic regression to Analysis via gate evaluation, allowing the existing Analysis→Ignored transition (2026-05-19)

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

**Status**: RESOLVED — Accepted risk documented: only target identifier exposed (no content leak), link creation requires VA role, bidirectional cascading complexity disproportionate to severity (2026-05-20)

### TKT-SEC-02 — Confidential ticket existence leakage via 409 CVE conflict response (Medium)

**Status**: RESOLVED — Auto-resolved: finding premise invalid — both endpoints require VA role, and all VAs have access to all confidential tickets per Authorization Rule #1 (2026-05-20)

### TKT-SEC-03 — Cascade update on duplicate marking modifies tickets without authorization check (Medium)

**Status**: RESOLVED — Auto-resolved: finding premise invalid — mark-as-duplicate requires VA role, all VAs have access to all confidential tickets; cascade already marked as system operation (user_id=NULL in audit events) (2026-05-20)

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

**Status**: RESOLVED — Auto-resolved: finding premise invalid — endpoint requires VA role, all VAs have access to all confidential tickets per Authorization Rule #1 (2026-05-20)

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
**Status**: RESOLVED — Access level `Public` declared inline using structured format. (2026-05-20)

### TKT-API-02 — Get Ticket endpoint missing explicit access level declaration (Low)

**Category**: Authorization
**Status**: RESOLVED — Access level `Public` declared inline using structured format. (2026-05-20)

### TKT-API-03 — List Tickets missing sortable fields specification (Low)

**Category**: Sorting
**Status**: RESOLVED — Sortable fields enumerated: `created_at`, `updated_at`, `severity`, `status`, `identifier`. (2026-05-20)

### TKT-API-04 — DELETE /tickets/{ticket_id}/cve returns 204 but does not use data envelope (Low)

**Status**: RESOLVED — The convention requires 204 No Content for DELETE operations with no response body, which is consistent with api-spec.md. (2026-05-18)

### TKT-API-05 — TICKET_CVE_CONFLICT error code not listed in api-spec.md Error Code Categories (Medium)

**Status**: RESOLVED — The convention requires codes to be defined in the Python enum; the examples table in api-spec.md is illustrative, not exhaustive. The spec correctly uses the TICKET_* prefix and documents HTTP status + error code for each error. (2026-05-18)

### TKT-API-06 — Ticket object response schema not defined (Medium)

**Category**: API completeness
**Status**: RESOLVED — Response Schemas section added to tickets.md defining `TicketSummary` (list), `TicketDetail` (detail/mutations), and all sub-schemas. Endpoint → Schema mapping table included. (2026-05-20)

### TKT-API-07 — Inconsistent access level declaration format across endpoints (Low)

**Category**: Consistency
**Status**: RESOLVED — All endpoints now use the structured `- **Access level**: ...` format consistently. (2026-05-20)

### TKT-API-08 — New error codes not in examples (Low)

**Status**: RESOLVED — Error codes defined in the spec follow the TICKET_* prefix convention. The api-spec.md examples table is illustrative, not exhaustive. (2026-05-19)
