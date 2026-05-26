# Review: ticket-service

**Spec**: `docs/features/tickets/ticket-service.md`
**Last reviewed**: 2026-05-25
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TKS-GAP-01 — associate_cve CVE Resolution I/O ordering inside FOR UPDATE lock (Medium)

**Status**: RESOLVED — Fixed: added explicit Locking note clarifying that step 4 involves only local DB operations (SELECT, INSERT) and Celery enqueue — no synchronous external HTTP calls inside the lock (2026-05-25)

### TKS-GAP-02 — mark_as_duplicate cascade does not filter soft-deleted tickets (Medium)

**Status**: RESOLVED — Spec updated: added deleted_at IS NULL filter to cascade query (2026-05-26)

### TKS-GAP-03 — mark_as_duplicate cascade fan-in without bound (Medium)

**Status**: RESOLVED — Accepted risk: fan-in cascade without bound acknowledged as extremely rare case; adding complexity is counterproductive (2026-05-26)

### TKS-GAP-04 — set_confidentiality on soft-deleted ticket has no deleted_at guard (Medium)

**Status**: RESOLVED — Spec updated: added cross-cutting soft-delete module invariant plus explicit deleted_at check step in all mutation functions (2026-05-26)

### TKS-GAP-05 — grant_access and revoke_access do not verify target user is active (Low)

**Category**: Boundary conditions
**Status**: OPEN

The `grant_access` preconditions state "Target user must exist (else
`UserNotFoundError`)" but do not specify behavior when the target user is
inactive (deactivated). The `assign_ticket` operation explicitly rejects
inactive users with `InvalidAssigneeError`, but `grant_access` has no
equivalent check. An admin could grant explicit access to a deactivated
employee account on a confidential ticket. Since deactivated users cannot
log in, the grant would be inert — but it creates an audit event and a
database record for a user who cannot use it. This is a low-severity gap
because the behavior is functionally harmless (the grant is a no-op in
practice), but the spec should state whether granting access to inactive
users is intentional or should be rejected.

### TKS-GAP-06 — create_ticket with severity_override and cve_id: audit trail gap for severity_override (Low)

**Category**: Data lifecycle
**Status**: OPEN

The `create_ticket` spec states that when both `cve_id` and
`severity_override` are provided, "severity_override is stored but not
used for severity resolution while the CVE is associated." The behavioral
steps create up to 3 audit events: `ticket_created`, assignment, and
`cve_associated`. There is no audit event for storing the
`severity_override` value. If the CVE is later dissociated, the
`severity_override` silently becomes the active severity source. An admin
reviewing the audit trail would see a `cve_removed` event followed by the
ticket suddenly having a severity value with no `severity_changed` event
explaining its origin.

### TKS-GAP-07 — dissociate_cve does not specify ordering of CVSS deletion audit events relative to cve_removed (Low)

**Status**: RESOLVED — Auto-resolved: dissociate_cve no longer deletes CVSS assessments or creates deletion audit events; only cve_removed is emitted (2026-05-25)

### TKS-GAP-08 — mark_as_duplicate cascade caller verification step may silently skip tickets reverted between primary commit and cascade (Low)

**Category**: Temporal / concurrency
**Status**: OPEN

The cascade orchestration (caller responsibility) step 3 says "Verifies
the ticket is still in Duplicated status and still points to the original
ticket (skip if reverted concurrently)." This is correct for handling
concurrent reverts, but the spec does not specify whether a skipped
cascade item should produce any observable output (log entry, warning).
If cascade items are silently skipped due to concurrent reverts, there is
no record that the cascade was attempted but found the ticket in an
unexpected state.

### TKS-GAP-09 — No specification of behavior when create_ticket is called with is_confidential=true but cve_id also provided and CVE already has a ticket (Low)

**Category**: Error paths
**Status**: OPEN

The `create_ticket` preconditions state capability and CVE conflict
checks but the spec does not clarify whether the `manage_confidentiality`
capability check happens before or after the CVE conflict check. The
ordering affects the specific error message the user sees.

### TKS-GAP-10 — assign_ticket does not call auto_assign_actor (Low)

**Category**: State machine completeness
**Status**: OPEN

The `assign_ticket` dependency summary table shows that
`auto_assign_actor` is NOT called by `assign_ticket` (dash in the table).
This makes sense since `assign_ticket` performs an explicit assignment.
However, the spec does not explain why auto-assignment is skipped for
this operation. The rationale is implicit (explicit assignment supersedes
auto-assignment) but other operations consistently document why
auto-assignment is or is not called.

---

## Coherence

### TKS-COH-01 — cvss_assessment_deleted is a non-existent audit event type (High)

**Status**: RESOLVED — Auto-resolved: dissociate_cve no longer deletes CVSS assessments; references to cvss_assessment_deleted removed entirely (2026-05-25)

### TKS-COH-02 — TICKET_SEVERITY_DERIVED HTTP status code conflict between tickets.md (400) and ticket-mutations.md (409) (Medium)

**Status**: RESOLVED — Spec updated: changed HTTP 400 to 409 for TICKET_SEVERITY_DERIVED in tickets.md (2026-05-26)

### TKS-COH-03 — SeverityEnum vs Severity naming inconsistency (Low)

**Status**: RESOLVED — Renamed `SeverityEnum` to `Severity` in `ticket-service.md` to match `ticket-mutations.md` convention (2026-05-25)

---

## Design

### TKS-DES-01 — grant_access and revoke_access immutability guard split across API and service layers (Medium)

**Status**: RESOLVED — Spec updated: moved immutability guard into grant_access and revoke_access service functions (2026-05-26)

### TKS-DES-02 — grant_access and revoke_access skip FOR UPDATE locking while creating ticket-scoped records (Medium)

**Category**: Concurrency control consistency
**Status**: OPEN

The spec states that `grant_access` and `revoke_access` do "Not require"
FOR UPDATE locking on the Ticket row. However, these operations read
`ticket.is_confidential` as a precondition. Without FOR UPDATE, a
concurrent `set_confidentiality(is_confidential=False)` could execute
between the check and the INSERT, resulting in a `TicketAccessGrant` being
created on a now-non-confidential ticket.

### TKS-DES-03 — mark_as_duplicate cascade transaction pattern pushes orchestration responsibility to API handler (Medium)

**Category**: Architectural complexity / layer responsibility
**Status**: OPEN

The `mark_as_duplicate` function returns `cascade_ticket_ids` and the
spec pushes the cascade orchestration loop into the API endpoint handler.
This violates the project's "thin handlers" principle. Recommendation:
Move cascade orchestration into a separate service function.

### TKS-DES-04 — dissociate_cve deletes CVSS assessments inside FOR UPDATE lock without bounding the set (Low)

**Category**: Lock hold duration
**Status**: OPEN

The `dissociate_cve` behavioral step 4 deletes all `CVECVSSAssessment`
records inside the FOR UPDATE lock. While in practice CVEs have a bounded
number of CVSS assessments, the spec does not state an upper bound.
Recommendation: document the expected upper bound rather than
restructure.

### TKS-DES-05 — create_ticket accepts severity_override with CVE but behavior may confuse implementers (Low)

**Category**: API ergonomics / clarity
**Status**: OPEN

At creation time, you can set `severity_override` on a ticket with a CVE;
after creation, you cannot modify it via the API while the CVE is present
(`SeverityDerivedError`). This asymmetry is intentional but subtle.
Recommendation: keep current design with documentation clarity.

### TKS-DES-06 — No explicit deleted_at guard in most service functions despite 'invisible to all business logic' invariant (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKS-GAP-04 (2026-05-26)

---

## Security

### TKS-SEC-01 — Authorization enforcement deferred entirely to API layer with no service-layer defense-in-depth (Medium)

**Category**: Authorization
**Status**: OPEN

The `ticket-service` specification states that all capability checks are
"enforced at API layer" and the module "does NOT perform capability
checks." Any new caller that passes a non-None `acting_user_id` without
verifying capabilities can bypass authorization. Recommended mitigation:
Consider adding an optional `required_capability` parameter or an
architectural test.

### TKS-SEC-02 — grant_access and revoke_access skip FOR UPDATE locking on the Ticket row (Medium)

**Category**: Authorization / Concurrency
**Status**: OPEN

Without a lock, a concurrent `set_confidentiality(is_confidential=False)`
could execute between the `is_confidential` check and the grant INSERT,
resulting in a `TicketAccessGrant` on a non-confidential ticket. Impact:
Low — cosmetic inconsistency. Recommended mitigation: acknowledge the
race or add a note.

### TKS-SEC-03 — mark_as_duplicate scope check deferred to API layer risks information disclosure (Medium)

**Category**: Information disclosure / IDOR
**Status**: OPEN

If a future caller calls `mark_as_duplicate` with a confidential target,
the service will proceed and create a duplicate link to a confidential
ticket — whose `SNTL-{n}` identifier would appear in public API
responses. Recommended mitigation: accept as documented risk or pass
`caller_scope` parameter.

### TKS-SEC-04 — create_ticket confidentiality check documented as API-layer only — dual capability requirement could be bypassed (Medium)

**Category**: Authorization
**Status**: OPEN

A system caller or future internal code path could call
`create_ticket(is_confidential=True)` and bypass the
`manage_confidentiality` capability check. Recommended mitigation: Add
service-layer assertion when `is_confidential=True` and
`acting_user_id is not None`.

### TKS-SEC-05 — assign_ticket does not verify the target user is not soft-deleted/deactivated before assignment (Low)

**Category**: Authorization / Business logic
**Status**: OPEN

TOCTOU concern: target user could become deactivated between check and
commit. Mitigated by `reconcile_ticket_status` inactive assignee
sanitization. Recommended mitigation: No action needed.

### TKS-SEC-06 — dissociate_cve deletes CVSS assessments without recording the full vector/score in audit events (Low)

**Status**: RESOLVED — Auto-resolved: dissociate_cve no longer deletes CVSS assessments; no deletion audit events needed (2026-05-25)

### TKS-SEC-07 — No rate limiting on ticket creation endpoint (Low)

**Category**: Denial of Service
**Status**: OPEN

The `create_ticket` operation has no rate limiting. A compromised bot API
key could flood the system. Acknowledged in `api-spec.md` as a known
deferral.

### TKS-SEC-08 — mark_as_duplicate cascade runs synchronously with unbounded fan-in — potential DoS vector (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKS-GAP-03 (2026-05-25)

---

## API Conventions

### TKS-API-01 — InvalidAssigneeError mapped to non-existent INVALID_ASSIGNEE error code (High)

**Status**: RESOLVED — Service Exceptions table updated to map `InvalidAssigneeError` to `TICKET_ASSIGNEE_NOT_VA` or `TICKET_ASSIGNEE_INACTIVE` with `reason` attribute documentation, matching `tickets.md` and `api-spec.md` (2026-05-25)
