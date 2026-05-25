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

**Category**: Temporal/concurrency
**Status**: OPEN

The gate-relevant mutation pattern calls auto_assign_if_needed() (step 2) which may create an assignment audit event, then the mutation itself creates an audit event (step 5), then reconcile_ticket_status may create a status_change event (step 6). The spec doesn't define the expected ordering of these audit events in the audit trail, though the sequential execution makes it implicitly ordered.

### TKM-GAP-06 — resolve_canonical_target behavior when starting ticket is not Duplicated (Low)

**Category**: Boundary conditions
**Status**: OPEN

The spec says resolve_canonical_target 'follows the duplicate_of_id chain until a non-Duplicated ticket is found'. If called with a ticket that is not in Duplicated status (duplicate_of_id IS NULL), the behavior is unspecified — does it return the ticket itself, or raise an error?

### TKM-GAP-07 — revert_duplicate acting_user_id is required UUID but no handling for invalid/nonexistent user (Low)

**Category**: Error paths
**Status**: OPEN

revert_duplicate declares acting_user_id as 'UUID' (required, not Optional). Step 4 says 'Load the acting user's roles'. If the user UUID doesn't exist in the database (e.g., deleted between auth check and service call), the behavior is unspecified — no exception is listed for this case.

### TKM-GAP-08 — No specification of what happens when ticket has no CVE and CVSS operations are attempted (Low)

**Category**: Error paths
**Status**: OPEN

create_cvss_assessment() has precondition 'Ticket must have an associated CVE (cve_id IS NOT NULL)' but update/delete operate on assessment_id directly. If a CVE is dissociated from a ticket (via ticket_service) while assessments still exist, attempting update/delete on orphaned assessments is not addressed — though the parent ticket check may implicitly cover this.

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

**Category**: Performance / Maintainability
**Status**: OPEN

The spec states that `reconcile_ticket_status` may be called up to 3 times per transaction during orphan cascades and 'Implementations MUST NOT defer or skip intermediate calls for optimization.' While idempotent, each call presumably queries package/track/product state to evaluate gates. For tickets with many packages, this could mean redundant queries. The spec explicitly forbids optimization, which is a reasonable correctness-first stance. The risk is low given the expected data volumes.

### TKM-DES-05 — User deactivation unassigns via direct query bypassing ticket_mutations (Medium)

**Status**: RESOLVED — Spec updated: deactivate_user now calls reconcile_ticket_status() per-ticket after bulk unassignment (2026-05-25)

---

## Security

### TKM-SEC-01 — No authorization check inside service functions for CVSS assessment operations (Medium)

**Status**: RESOLVED — Authorization responsibility contract added to Acting user convention section (2026-05-25)

### TKM-SEC-02 — resolve_canonical_target bypasses confidentiality checks (Medium)

**Status**: RESOLVED — Risk accepted: documented in tickets.md "Accepted risk — duplicate_of_id and confidential targets" section; target scope validation added to ticket-service.md (2026-05-25)

### TKM-SEC-03 — Duplicate chain traversal as potential DoS vector (Low)

**Category**: Denial of Service
**Status**: OPEN

The `resolve_canonical_target()` function allows chains up to 50 hops with database queries per hop. While the 50-hop limit prevents infinite loops, a maliciously constructed chain of 50 duplicates would cause 50 sequential database queries per resolution call. Mitigation: The 50-hop limit is a reasonable guard, but consider caching resolved targets or adding a warning log when chains exceed a low threshold (e.g., 5 hops).

### TKM-SEC-04 — set_severity_override business rule enforced only at API layer (Medium)

**Status**: RESOLVED — Precondition moved to service layer: set_severity_override() now raises SeverityDerivedError when cve_id IS NOT NULL (2026-05-25)

### TKM-SEC-05 — reopen_from_ignored assignee_id parameter trust boundary (Medium)

**Status**: RESOLVED — assignee_id parameter removed; _assign_actor_if_va() helper centralizes VA role validation internally (2026-05-25)

---

## API Conventions

_No findings. The spec is a service-layer specification with no API endpoint definitions._
