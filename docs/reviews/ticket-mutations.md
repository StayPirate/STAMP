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

---

## Coherence

### TKM-COH-01 — severity_changed audit event user_id conflict between ticket-mutations and audit-log contract (Medium)

**Status**: RESOLVED — Updated severity_changed event to conditional user_id (NULL for system, acting_user for manual override), aligning with established audit event pattern (2026-05-25)

### TKM-COH-02 — reopen_from_ignored auto_assign_if_needed not called in documented behavior (Low)

**Status**: RESOLVED — auto_assign_actor(force=True) unifies assignment logic; reopen_from_ignored now follows the same pattern as all other mutation functions (2026-05-25)

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

---

## API Conventions

_No findings. The spec is a service-layer specification with no API endpoint definitions._
