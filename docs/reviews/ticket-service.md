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

**Status**: RESOLVED — Fixed in spec: added inactive user precondition to grant_access (2026-05-26)

### TKS-GAP-06 — create_ticket with severity_override and cve_id: audit trail gap for severity_override (Low)

**Status**: RESOLVED — Fixed in spec: added severity_changed audit event for severity_override at creation (2026-05-26)

### TKS-GAP-07 — dissociate_cve does not specify ordering of CVSS deletion audit events relative to cve_removed (Low)

**Status**: RESOLVED — Auto-resolved: dissociate_cve no longer deletes CVSS assessments or creates deletion audit events; only cve_removed is emitted (2026-05-25)

### TKS-GAP-08 — mark_as_duplicate cascade caller verification step may silently skip tickets reverted between primary commit and cascade (Low)

**Status**: RESOLVED — Fixed in spec: added informational log for cascade skip on concurrent revert (2026-05-26)

### TKS-GAP-09 — No specification of behavior when create_ticket is called with is_confidential=true but cve_id also provided and CVE already has a ticket (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable — ordering implicitly defined by API-layer vs service-layer separation (2026-05-26)

### TKS-GAP-10 — assign_ticket does not call auto_assign_actor (Low)

**Status**: RESOLVED — Fixed in spec: added note explaining why auto_assign_actor is not called (2026-05-26)

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

**Status**: RESOLVED — Auto-resolved: spec now explicitly states FOR UPDATE locking on Ticket row for both grant_access and revoke_access (2026-05-26)

### TKS-DES-03 — mark_as_duplicate cascade transaction pattern pushes orchestration responsibility to API handler (Medium)

**Status**: RESOLVED — Cascade orchestration moved to dedicated service function execute_duplicate_cascade; handler pattern reduced to two service calls (2026-05-26)

### TKS-DES-04 — dissociate_cve deletes CVSS assessments inside FOR UPDATE lock without bounding the set (Low)

**Status**: RESOLVED — Auto-resolved: dissociate_cve no longer deletes CVSS assessments; records are preserved as factual CVE data (2026-05-26)

### TKS-DES-05 — create_ticket accepts severity_override with CVE but behavior may confuse implementers (Low)

**Status**: RESOLVED — Fixed in spec: severity_override now rejected when cve_id is provided (SeverityDerivedError) (2026-05-26)

### TKS-DES-06 — No explicit deleted_at guard in most service functions despite 'invisible to all business logic' invariant (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKS-GAP-04 (2026-05-26)

---

## Security

### TKS-SEC-01 — Authorization enforcement deferred entirely to API layer with no service-layer defense-in-depth (Medium)

**Status**: RESOLVED — Accepted design decision: authorization enforcement at API layer is intentional (2026-05-26)

### TKS-SEC-02 — grant_access and revoke_access skip FOR UPDATE locking on the Ticket row (Medium)

**Status**: RESOLVED — Auto-resolved: spec now explicitly states FOR UPDATE locking on Ticket row for both grant_access and revoke_access (2026-05-26)

### TKS-SEC-03 — mark_as_duplicate scope check deferred to API layer risks information disclosure (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-26)

### TKS-SEC-04 — create_ticket confidentiality check documented as API-layer only — dual capability requirement could be bypassed (Medium)

**Status**: RESOLVED — Accepted design decision: authorization enforcement at API layer is intentional (2026-05-26)

### TKS-SEC-05 — assign_ticket does not verify the target user is not soft-deleted/deactivated before assignment (Low)

**Status**: RESOLVED — Accepted risk: TOCTOU window is minimal and mitigated by reconcile_ticket_status inactive assignee sanitization (2026-05-26)

### TKS-SEC-06 — dissociate_cve deletes CVSS assessments without recording the full vector/score in audit events (Low)

**Status**: RESOLVED — Auto-resolved: dissociate_cve no longer deletes CVSS assessments; no deletion audit events needed (2026-05-25)

### TKS-SEC-07 — No rate limiting on ticket creation endpoint (Low)

**Status**: RESOLVED — Accepted risk: rate limiting is an infrastructure concern (reverse proxy/WAF), not application-level (2026-05-26)

### TKS-SEC-08 — mark_as_duplicate cascade runs synchronously with unbounded fan-in — potential DoS vector (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKS-GAP-03 (2026-05-25)

---

## API Conventions

### TKS-API-01 — InvalidAssigneeError mapped to non-existent INVALID_ASSIGNEE error code (High)

**Status**: RESOLVED — Service Exceptions table updated to map `InvalidAssigneeError` to `TICKET_ASSIGNEE_NOT_VA` or `TICKET_ASSIGNEE_INACTIVE` with `reason` attribute documentation, matching `tickets.md` and `api-spec.md` (2026-05-25)
