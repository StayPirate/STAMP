# Review: rbac

**Spec**: `docs/features/identity/rbac.md`
**Last reviewed**: 2026-05-15
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### RBAC-GAP-01 — Deactivated user as assignment target (Medium)

**Status**: RESOLVED — Added active-user check to assignment target constraint in tickets.md and rbac.md (2026-05-13)

### RBAC-GAP-02 — Business Rule 2 self-role-add enforcement scope unclear (Low)

**Status**: RESOLVED — Cross-agent duplicate of RBAC-DES-03 (2026-05-13)

### RBAC-GAP-03 — Behavior when authenticated user's account is deactivated mid-session (Low)

**Status**: RESOLVED — Already fully specified in authentication.md: middleware returns HTTP 401 for inactive users, sessions are invalidated on deactivation, no cause disclosed (2026-05-13)

### RBAC-GAP-04 — No specification of response when a user with zero roles accesses a role-protected endpoint (Low)

**Status**: RESOLVED — Covered by docs/api-spec.md Global Responses table (403 AUTH_INSUFFICIENT_ROLE) (2026-05-13)

### RBAC-GAP-05 — Permission enforcement mechanism ambiguity (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-13)

---

## Coherence

### RBAC-COH-01 — Permission Matrix incomplete — missing Admin user management operations (Medium)

**Status**: RESOLVED — Added 8 missing admin operations to Permission Matrix in rbac.md (2026-05-13)

### RBAC-COH-02 — Business Rule 2 contradicted by user-management.md (Medium)

**Status**: RESOLVED — Cross-agent duplicate of RBAC-DES-03 (2026-05-13)

### RBAC-COH-03 — Endpoint catalog removed from api-spec.md eliminates divergence (High)

**Status**: RESOLVED — Endpoint catalog removed from api-spec.md; rbac.md Endpoint Permission Map is now the single cross-cutting endpoint index. Divergence eliminated at the root. (2026-05-13)

### RBAC-COH-04 — "track" vs "codestream" terminology inconsistency in api-spec.md (Medium)

**Status**: RESOLVED — Resolved together with RBAC-COH-03: the api-spec.md endpoint catalog containing the "codestreams" term has been removed entirely. (2026-05-13)

### RBAC-COH-05 — "View users" in prose but missing from Permission Matrix (Low)

**Status**: RESOLVED — Added "View users (list and detail)" row to Permission Matrix in rbac.md (2026-05-13)

---

## Design

### RBAC-DES-01 — No granularity within the VA role creates an all-or-nothing access model (Medium)

**Status**: RESOLVED — Added design rationale note documenting VA role simplicity as intentional trade-off (2026-05-13)

### RBAC-DES-02 — require_role() as the sole authorization mechanism cannot express resource-level constraints (Medium)

**Status**: RESOLVED — Already covered: resource-level constraints are documented in individual service specs (e.g., user-service.md). rbac.md correctly defines require_role() as the role-level mechanism; service-layer validation for resource constraints is the established pattern (2026-05-13)

### RBAC-DES-03 — Business Rule 2 wording is misleading (Medium)

**Status**: RESOLVED — Business Rule 2 reworded to clarify that admins can manage any user's roles including their own, subject to the self-removal guard in BR1 (2026-05-13)

### RBAC-DES-04 — No rate limiting or abuse protection on public endpoints (Low)

**Status**: RESOLVED — Cross-agent duplicate of RBAC-SEC-02 (2026-05-13)

### RBAC-DES-05 — No mechanism to require both roles simultaneously (Low)

**Status**: RESOLVED — Closed without spec changes: require_role() OR-only limitation is irrelevant with the current two-role model, and FastAPI dependency stacking provides AND semantics if needed (2026-05-13)

### RBAC-DES-06 — Public read access exposes pre-disclosure security assessments (Low)

**Status**: RESOLVED — Already planned: ticket public/private visibility extension will address pre-disclosure access control (2026-05-13)

### RBAC-DES-07 — No "Authenticated" access level in Permission Matrix (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-13)

### RBAC-DES-08 — Race condition between role removal and in-flight requests (Low)

**Status**: RESOLVED — Cross-agent duplicate of RBAC-GAP-05 (2026-05-13)

---

## Security

### RBAC-SEC-01 — User enumeration via public user endpoints (Medium)

**Status**: RESOLVED — Public read access is an intentional design principle explicitly stated in rbac.md. The endpoint access levels are consistent with the documented design (2026-05-13)

### RBAC-SEC-02 — No rate limiting specification (Medium)

**Status**: RESOLVED — Rate limiting is documented as not implemented in api-spec.md. This is a cross-cutting concern owned by api-spec.md, not an rbac.md gap (2026-05-13)

### RBAC-SEC-03 — No audit logging for role removals (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-15)

### RBAC-SEC-04 — Fetcher error messages may leak infrastructure details (Low)

**Status**: RESOLVED — Added three-tier error field architecture (error_message/error_detail/error_traceback) and Error Message Sanitization section to fetcher-infrastructure.md. Added error_detail column to data-model.md. Extended fetcher-compliance-reviewer with sanitization check (2026-05-13)

### RBAC-SEC-05 — IBS consumer status endpoint publicly exposes operational data (Low)

**Status**: RESOLVED — Accepted risk: endpoint intentionally public; exposed operational data contains no credentials or PII, public monitoring access is a deliberate design choice (2026-05-13)

### RBAC-SEC-06 — Role changes not immediately enforced for in-flight requests (Low)

**Status**: RESOLVED — Cross-agent duplicate of RBAC-GAP-05. The spec explicitly documents this as expected behavior. (2026-05-13)

### RBAC-SEC-07 — No object-level authorization for VA operations (Low)

**Status**: RESOLVED — Accepted risk: absence of object-level authorization is intentional; specs already consistently indicate "Any VA" as actor and assignee_id is organizational, not an authorization boundary (2026-05-13)

---

## API Conventions

### RBAC-API-01 — GET /api/v1/users missing pagination specification in rbac.md (Medium)

**Status**: RESOLVED — Restructured the "API Endpoints" section into a compact "Endpoint Permission Map" table that only documents access rules and links to owning specs. Pagination, response schemas, and other endpoint details are no longer in scope for `rbac.md`. (2026-05-06)

### RBAC-API-02 — GET /api/v1/users/me missing response envelope and error codes (Low)

**Status**: RESOLVED — Restructured the "API Endpoints" section into a compact "Endpoint Permission Map" table. The table links to `authentication.md` as the owning spec — endpoint details (response envelope, error codes) are defined there, not in `rbac.md`. (2026-05-06)

### RBAC-API-03 — GET /api/v1/users listed under Admin only heading but is public (Low)

**Status**: RESOLVED — Restructured the "API Endpoints" section into a flat "Endpoint Permission Map" table with an explicit "Access" column per endpoint. The contradictory heading "User Management (Admin only)" no longer exists. Each endpoint clearly states its own access level. (2026-05-06)

### RBAC-API-04 — Missing response envelope for GET /api/v1/users and GET /api/v1/users/{user} (Low)

**Status**: RESOLVED — Restructured the "API Endpoints" section into a compact "Endpoint Permission Map" table that only documents access rules. Response envelopes are defined in the owning spec (`ad-integration.md`), which is linked from the table. (2026-05-06)

### RBAC-API-05 — GET /api/v1/admin/role-mappings missing unpaginated justification in rbac.md (Low)

**Status**: RESOLVED — Restructured the "API Endpoints" section into a compact "Endpoint Permission Map" table that only documents access rules and links to owning specs. Pagination justifications belong in the owning spec (`ad-integration.md`), not in `rbac.md`. (2026-05-06)

### RBAC-API-06 — Path parameter naming inconsistency between api-spec.md and rbac.md (High)

**Status**: RESOLVED — Cross-agent duplicate of RBAC-COH-03 (2026-05-13)

### RBAC-API-07 — Missing DELETE endpoint for packages in Permission Map (Medium)

**Status**: RESOLVED — Auto-resolved: api-spec.md no longer contains an endpoint catalog; package-tracking uses POST .../exclude (already in Permission Map), not DELETE (2026-05-13)

### RBAC-API-08 — Missing product endpoints in Permission Map (Medium)

**Status**: RESOLVED — Auto-resolved: api-spec.md no longer has an endpoint hierarchy; the only product endpoint (GET /api/v1/products) is already in the Permission Map (2026-05-13)

### RBAC-API-09 — Exclude/restore endpoints absent from api-spec.md (Medium)

**Status**: RESOLVED — Cross-agent duplicate of RBAC-COH-03 (2026-05-13)
