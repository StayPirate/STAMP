# Review: ticket-mutations

**Spec**: `docs/features/tickets/ticket-mutations.md`
**Last reviewed**: 2026-05-25
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TKM-GAP-01 — No exception for duplicate CVSS assessment (Medium)

**Status**: RESOLVED — Added `DuplicateCVSSAssessmentError` to Service Exceptions table and `create_cvss_assessment()` preconditions with 409 Conflict mapping (2026-05-25)

### TKM-GAP-02 — Assessment not found has no defined exception (Medium)

**Status**: RESOLVED — Added CVSSAssessmentNotFoundError to Service Exceptions table with HTTP 404 mapping and explicit references in update/delete functions and endpoints (2026-05-25)

### TKM-GAP-03 — reopen_from_ignored 'last active assignee' restoration undefined (Medium)

**Status**: RESOLVED — Clarified 'last active assignee' source as current ticket.assignee_id value; added note that Ignored status preserves assignee_id (2026-05-25)

### TKM-GAP-04 — Severity recalculation after CVSS delete may leave ticket with no severity (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable; cvss-scoring.md defines severity=None when no score available and gate blocking behavior (2026-05-25)

### TKM-GAP-05 — auto_assign_if_needed creates audit event but reconcile_ticket_status also creates one — ordering unspecified (Low)

**Status**: RESOLVED — Accepted risk: ordering is implicitly guaranteed by sequential execution within a single transaction; explicit documentation not warranted for Low-severity implicit behavior (2026-05-25)

### TKM-GAP-06 — resolve_canonical_target behavior when starting ticket is not Duplicated (Low)

**Status**: RESOLVED — Auto-resolved: finding invalid — the termination condition "until a non-Duplicated ticket is found" inherently covers the base case where the starting ticket is already non-Duplicated (chain length = 0) (2026-05-25)

### TKM-GAP-07 — revert_duplicate acting_user_id is required UUID but no handling for invalid/nonexistent user (Low)

**Status**: RESOLVED — Auto-resolved: finding invalid — auth layer (get_current_user) guarantees user existence before service functions are called; parameter is UUID | None not required UUID as described (2026-05-25)

### TKM-GAP-08 — No specification of what happens when ticket has no CVE and CVSS operations are attempted (Low)

**Status**: RESOLVED — Fixed: added CVSS assessment cascade-delete to dissociate_cve in ticket-service.md — orphaned assessments are now structurally impossible (2026-05-25)

### TKM-GAP-09 — Missing exception for create_cvss_assessment when ticket has no CVE (Medium)

**Status**: RESOLVED — Added TicketNoCVEError exception to Service Exceptions table and precondition reference (2026-05-25)

### TKM-GAP-10 — update_cvss_assessment None semantics ambiguity (Medium)

**Status**: RESOLVED — Redesigned CVSS functions: removed score/cvss_version inputs, derived from vector via cvss library (2026-05-25)

### TKM-GAP-11 — Missing exception for reopen_from_ignored wrong status (Medium)

**Category**: Missing error specification
**Status**: OPEN

Precondition "Ticket must be in Ignored status" exists for `reopen_from_ignored`, but no named exception is specified in the Service Exceptions table for when this precondition fails. Implementers cannot determine the correct error class or HTTP status code.

### TKM-GAP-12 — Missing exception for revert_duplicate wrong status (Medium)

**Category**: Missing error specification
**Status**: OPEN

Same gap as TKM-GAP-11: precondition "Ticket must be in Duplicated status" for `revert_duplicate` has no named exception for violation in the Service Exceptions table.

### TKM-GAP-13 — Analysis gate not defined within this document (Low)

**Category**: Missing definition / cross-reference
**Status**: OPEN

`reconcile_ticket_status` references the "Analysis gate" without defining it within this spec. An implementer reading only this document would not know what the Analysis gate evaluates (e.g., `assignee_id IS NOT NULL`). A cross-reference or inline summary is needed.

### TKM-GAP-14 — Severity becoming None after last CVSS deletion not documented (Low)

**Category**: Undocumented side effect
**Status**: OPEN

When the last `CVECVSSAssessment` is deleted via `delete_cvss_assessment`, severity resolution returns `None`, which breaks the Analyzed gate and causes status regression. This side effect is implied by `reconcile_ticket_status` behavior but not explicitly documented as a known scenario in the `delete_cvss_assessment` section.

### TKM-GAP-15 — CVSS assessments linked to CVE persist after dissociation (Low)

**Category**: Undocumented lifecycle behavior
**Status**: OPEN

`CVECVSSAssessment` records belong to the CVE entity, not the ticket. If a CVE is dissociated from ticket A and associated with ticket B, assessments created under ticket A now affect ticket B. The spec does not address cleanup or explicitly document this as intentional behavior.

### TKM-GAP-16 — auto_assign_actor with force=True creates noise on self-reassignment (Low)

**Category**: Missing idempotency check
**Status**: OPEN

When `force=True` and the acting user is already the assignee, an assignment audit event is created recording a no-op change (old=VA, new=VA). No idempotency check exists for the force path to suppress this redundant event.

### TKM-GAP-17 — Gate interaction with configurable CVSS version (Low)

**Category**: Cross-spec inconsistency
**Status**: OPEN

The Analyzed gate #4 requires "BOTH SUSE CVSS v3.1 AND v4.0 assessments" (hardcoded versions). It is unclear how this interacts with the configurable `default_cvss_version` setting from `cvss-scoring.md`. The gate condition may need to reference the configured version rather than hardcoding both.

---

## Coherence

### TKM-COH-01 — severity_changed audit event user_id conflict between ticket-mutations and audit-log contract (Medium)

**Status**: RESOLVED — Updated severity_changed event to conditional user_id (NULL for system, acting_user for manual override), aligning with established audit event pattern (2026-05-25)

### TKM-COH-02 — reopen_from_ignored auto_assign_if_needed not called in documented behavior (Low)

**Status**: RESOLVED — auto_assign_actor(force=True) unifies assignment logic; reopen_from_ignored now follows the same pattern as all other mutation functions (2026-05-25)

### TKM-COH-03 — Internal ordering discrepancy in revert_duplicate audit events (Low)

**Category**: Internal contradiction
**Status**: OPEN

The behavioral steps for `revert_duplicate` show `_reenter_gate_zone()` at step 5 (creating a `status_change` event) BEFORE `duplicate_removed` at step 6. But the summary prose states "duplicate_removed (user action) followed by status_change" — implying the opposite order. This is an internal contradiction within the same function specification.

---

## Design

### TKM-DES-01 — Inactive assignee sanitization inside reconcile_ticket_status creates hidden side effects (Medium)

**Status**: RESOLVED — Renamed to reconcile_ticket_status() with explicit reconciler contract documenting side effects (inactive assignee sanitization, audit events) as intentional behavior (2026-05-25)

### TKM-DES-02 — resolve_canonical_target 50-hop limit and cycle detection (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKM-SEC-03 (2026-05-25)

### TKM-DES-03 — reopen_from_ignored assignee logic splits decision across caller and function (Medium)

**Status**: RESOLVED — Cross-agent duplicate of TKM-SEC-05 (2026-05-25)

### TKM-DES-04 — Multiple reconcile_ticket_status calls per transaction during orphan cascades lack deduplication (Low)

**Status**: RESOLVED — Auto-resolved: finding invalid — this is a deliberate documented design decision prioritizing correctness over performance, explicitly imposed as an implementation constraint (2026-05-25)

### TKM-DES-05 — User deactivation unassigns via direct query bypassing ticket_mutations (Medium)

**Status**: RESOLVED — Spec updated: deactivate_user now calls reconcile_ticket_status() per-ticket after bulk unassignment (2026-05-25)

### TKM-DES-06 — Duplicate chains not flattened at mark-time (Medium)

**Category**: Suboptimal data structure
**Status**: OPEN

Nothing prevents building duplicate chains of arbitrary depth (A→B→C→...→Z). The 50-hop limit is a safety net, but chains of 10-20 degrade performance for every `resolve_canonical_target` call. Alternative: resolve target to canonical at mark-time, keeping all chains at depth 1. This would eliminate traversal overhead entirely.

### TKM-DES-07 — Race window between deactivate_user and concurrent ticket mutations (Medium)

**Category**: Concurrency gap
**Status**: OPEN

`deactivate_user` iterates and unassigns tickets but doesn't hold a lock on each ticket during the operation. A concurrent mutation may complete (with assignee still active) just before deactivation proceeds. The ticket could be left in Analysis status with no assignee until the next `reconcile_ticket_status` call from another operation.

### TKM-DES-08 — Redundant gate evaluation for inactive assignees (Low)

**Category**: Unnecessary computation
**Status**: OPEN

`reconcile_ticket_status` performs a full gate evaluation, then checks assignee activity and nulls it, then re-evaluates gates. Every call on a ticket with an inactive assignee does double evaluation. Acceptable (microseconds) but unnecessary — the activity check could be performed before the first gate evaluation.

### TKM-DES-09 — Multiple reconcile calls in orphan cascades produce multiple audit events (Low)

**Category**: Audit noise
**Status**: OPEN

Up to 3 `status_change` events may be created in one transaction for a single user action (product→track→package deletion cascade). While this accurately reflects intermediate states, it may confuse audit log readers who expect one status change per user action.

### TKM-DES-10 — force=True reassignment overwrites without notification (Low)

**Category**: UX concern
**Status**: OPEN

When a VA reopens a ticket assigned to someone else, `auto_assign_actor(force=True)` overwrites the existing assignee without any notification mechanism. The original assignee loses ownership silently.

---

## Security

### TKM-SEC-01 — No authorization check inside service functions for CVSS assessment operations (Medium)

**Status**: RESOLVED — Authorization responsibility contract added to Acting user convention section (2026-05-25)

### TKM-SEC-02 — resolve_canonical_target bypasses confidentiality checks (Medium)

**Status**: RESOLVED — Risk accepted: documented in tickets.md "Accepted risk — duplicate_of_id and confidential targets" section; target scope validation added to ticket-service.md (2026-05-25)

### TKM-SEC-03 — Duplicate chain traversal as potential DoS vector (Low)

**Status**: RESOLVED — Auto-resolved: finding invalid — threat is purely theoretical: requires compromised privileged account, 50 manual ticket creations, and 50 PK lookups take milliseconds total (2026-05-25)

### TKM-SEC-04 — set_severity_override business rule enforced only at API layer (Medium)

**Status**: RESOLVED — Precondition moved to service layer: set_severity_override() now raises SeverityDerivedError when cve_id IS NOT NULL (2026-05-25)

### TKM-SEC-05 — reopen_from_ignored assignee_id parameter trust boundary (Medium)

**Status**: RESOLVED — assignee_id parameter removed; _assign_actor_if_va() helper centralizes VA role validation internally (2026-05-25)

### TKM-SEC-06 — Authorization enforcement is purely contractual (Medium)

**Category**: Defense-in-depth gap
**Status**: OPEN

The module does NOT perform capability checks internally — it relies entirely on callers (API handlers) to enforce authorization before invoking service functions. There is no defense-in-depth assertion. A future caller forgetting the capability check would silently bypass authorization with no runtime warning or error.

### TKM-SEC-07 — No CVSS score/vector bounds validation at service layer (Low)

**Category**: Input validation gap
**Status**: OPEN

`create_cvss_assessment` accepts `score: Decimal` and `vector: str` without specifying bounds (0.0-10.0) or CVSS vector format validation at the service layer. It relies on Pydantic validation at the API boundary. System callers (fetchers, background tasks) that bypass the API layer could persist invalid CVSS data.

### TKM-SEC-08 — resolve_canonical_target bypasses confidentiality checks (Low)

**Category**: Information disclosure risk
**Status**: OPEN

`resolve_canonical_target` is explicitly documented as intentional for service-layer use without confidentiality filtering. However, if any API endpoint exposes the resolved target ticket ID to non-privileged users, it could leak the existence of confidential tickets. No guardrail prevents this misuse by future API handlers.

### TKM-SEC-09 — force=True lacks explicit caller restriction (Low)

**Category**: Privilege escalation vector
**Status**: OPEN

Any caller can pass `force=True` to `auto_assign_actor` to reassign a ticket regardless of current assignment. Only manual-zone exit functions should use this parameter, but no enforcement (assertion, caller validation, or documentation contract) prevents arbitrary callers from using it.

---

## API Conventions

_No findings. The spec is a service-layer specification with no API endpoint definitions._
