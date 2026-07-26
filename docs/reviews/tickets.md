# Review: tickets

**Spec**: `docs/features/tickets/tickets.md`
**Last reviewed**: 2026-07-25
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### TKT-GAP-009 — No API path to clear severity_override (Medium)

**Status**: RESOLVED — Made `severity` field nullable (string | null) in PATCH /api/v1/tickets/{ticket_id}/severity request body; null clears the override (2026-07-26)

### TKT-GAP-001 — Ignore from Analyzed/Resolved status not explicitly rejected (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-21)

### TKT-GAP-002 — CVE dissociation on ticket without CVE — race with concurrent associate-cve (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-21)

### TKT-GAP-003 — Inactive assignee sanitization reference lacks inline definition (Low)

**Status**: RESOLVED — Fixed: changed broken anchor to cross-file reference to ticket-mutations.md (2026-05-21)

### TKT-GAP-004 — No specification for what happens when severity_override is set to None explicitly (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-20)

### TKT-GAP-005 — Search behavior for SNTL-{n} partial match is underspecified (Medium)

**Status**: RESOLVED — Search section rewritten: removed CVE description, specified prefix-match for SNTL-{n} and CVE ID, substring for package names (2026-05-20)

### TKT-GAP-006 — Manual ticket creation with CVE that triggers on-demand fetch — severity gate timing (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-20)

### TKT-GAP-007 — Duplicate target that is soft-deleted after the link was created (Medium)

**Status**: RESOLVED — Fixed: added soft-deleted target behavior in API Response Behavior section and Soft-Delete section (2026-05-21)

### TKT-GAP-008 — Access grant endpoints on non-confidential tickets that become confidential concurrently (Low)

**Status**: RESOLVED — Accepted risk: race is harmless, inert grants cleaned by periodic task (2026-05-21)

---

## Coherence

### TKT-COH-003 — data-model.md narrows automatic Ignored transition source to NVD only (Low)

**Status**: RESOLVED — Replaced "NVD rejection" with "CVE rejection" in data-model.md status transition summary (2026-07-26)

### TKT-COH-001 — Data model allows Ignored → Duplicated but tickets.md blocks it (Medium)

**Status**: RESOLVED — Fixed: corrected transition summary in data-model.md to 'Any except Ignored and Duplicated' (2026-05-21)

### TKT-COH-002 — Ambiguous severity resolution description in response schema (Low)

**Status**: RESOLVED — Fixed: corrected severity field description to 'CVSS-derived → override fallback' (2026-05-21)

---

## Design

### TKT-DES-001 — Concurrent duplicate marking can create cycles despite mitigation claims (Low)

**Status**: RESOLVED — Fixed: added Cycle Resolution subsection with explicit revert-duplicate procedure (2026-05-21)

### TKT-DES-002 — Confidentiality filtering on ticket list may have O(n) authorization checks (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-21)

### TKT-DES-003 — CVE dissociation leaves orphaned package/track data with no clear resolution path (Medium)

**Status**: RESOLVED — Accepted: future Bugzilla ID will serve as alternative detection identifier; manual status change for CVE-less tickets is a rare edge case (2026-05-21)

### TKT-DES-004 — Search across multiple fields without specifying matching strategy (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKT-GAP-005 (2026-05-20)

---

## Security

### TKT-SEC-001 — Public ticket list and detail endpoints expose sensitive security data (Medium)

**Status**: RESOLVED — Accepted risk: Sentinel is accessible only from internal network where this data is already available via LDAP (2026-05-21)

### TKT-SEC-002 — duplicate_of_id leaks existence of confidential tickets (Low)

**Status**: RESOLVED — Accepted risk: explicitly documented in spec as accepted risk with detailed rationale (2026-05-21)

### TKT-SEC-003 — No rate limiting specified for public endpoints (Low)

**Status**: RESOLVED — Accepted risk: rate limiting is a front-end proxy responsibility, not implemented by Sentinel (2026-05-21)

### TKT-SEC-004 — Concurrent duplicate marking can create cycles under READ COMMITTED (Low)

**Status**: RESOLVED — Cross-agent duplicate of TKT-DES-001 (2026-05-20)

### TKT-SEC-005 — Access grant management lacks self-revocation protection (Low)

**Status**: RESOLVED — By design: no practical impact, VAs retain role-based access regardless of explicit grants (2026-05-21)

---

## API Conventions

### TKT-API-001 — Create Ticket request body uses PascalCase severity values instead of lowercase (Low)

**Status**: RESOLVED — Fixed: corrected severity values to lowercase in examples and descriptions (2026-05-21)

### TKT-API-002 — TICKET_CVE_CONFLICT error code not registered in api-spec.md Error Code Categories (Low)

**Status**: RESOLVED — Fixed: added 7 missing TICKET_* error codes to api-spec.md (2026-05-21)

### TKT-API-003 — Ignore Ticket endpoint allows transition from Analyzed/Resolved per mutability guard but spec says only New/Analysis (Low)

**Status**: RESOLVED — Fixed: clarified error table with explicit status-to-error mapping (2026-05-21)

### TKT-API-004 — List Access Grants endpoint lacks justification note format for unpaginated response (Low)

**Status**: RESOLVED — Justification is provided inline. (2026-05-20)
