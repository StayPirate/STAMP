# Review: ad-integration

**Spec**: `docs/features/identity/ad-integration.md`
**Last reviewed**: 2026-05-12
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### ADI-GAP-009 — Admin self-lockout via role mapping deletion bypasses guard (High)

**Status**: RESOLVED — all role mapping operations (sync step 5, POST /role-mappings, DELETE /role-mappings) now delegate to `user_service.sync_role_mapping()` and `user_service.delete_role_mapping_roles()`. The self-admin guard uses the centralized `SelfRoleRemovalError` / `USER_SELF_ROLE_REMOVAL` error code — no custom endpoint-level guard or error code needed. Guardrail 19 compliant. (2026-05-12)

### ADI-GAP-001 — Missing/empty `mail` attribute handling (Medium)

**Status**: RESOLVED — Spec updated: added explicit mail attribute validation in sync step 3 — skip entry with record_failed() and WARNING log (2026-05-12)

### ADI-GAP-003 — Missing `objectGUID` attribute handling (Medium)

**Status**: RESOLVED — Spec updated: added explicit objectGUID validation in sync step 3 — skip entry with record_failed() and WARNING log. Complementary to Level 1 safety check (2026-05-12)

### ADI-GAP-005 — Concurrent sync execution behavior (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-12)

### ADI-GAP-008 — Missing `EMPLOYEESTATUS` attribute interpretation (Medium)

**Status**: RESOLVED — Spec updated: missing EMPLOYEESTATUS entries are skipped after pre-flight checks; Level 3 excludes them from deactivation count (2026-05-12)

### ADI-GAP-002 — Missing/empty `sAMAccountName` handling (Low)

**Status**: RESOLVED — Spec updated: added explicit validation block in step 3 for missing/empty sAMAccountName with skip + WARNING + record_failed() pattern (2026-05-12)

### ADI-GAP-004 — AD member query failure after group existence check (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-12)

### ADI-GAP-006 — Manager circular reference in chain traversal (Low)

**Status**: RESOLVED — Removed speculative "walk up the manager chain" guidance from sync step 4. No feature currently traverses the chain; cycle detection will be addressed when such features are specified (2026-05-12)

### ADI-GAP-007 — Reactivation with zero roles after role mapping removal (Low)

**Status**: RESOLVED — Not a gap: spec already covers this scenario via Business Rule 2 (zero roles = read-only access), step ordering rationale (steps 5/7 operate on independent data), and reactivate_user() contract (roles unaffected by reactivation) (2026-05-12)

### ADI-GAP-010 — `created_by` display for inactive admin (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-12)

### ADIN-GAP-01 — Username rename collision during LDAP sync upsert (Medium)

**Status**: RESOLVED — Added explicit `UserConflictError` handling sub-bullet to Step 3 existing-user branch in `docs/features/identity/ad-integration.md` — mirrors the pattern already used for new user creation (log WARNING, `record_failed()`, skip entry) (2026-05-10)

### ADIN-GAP-02 — AD query pagination not specified (Medium)

**Status**: RESOLVED — Added LDAP Simple Paged Results Control (RFC 2696) requirement to Step 1 of sync algorithm, corrected ~913 user count to ~3,200 (verified by live LDAP query), and added `ldap3` client configuration and optional attribute handling notes to Implementation Notes section in `docs/features/identity/ad-integration.md` (2026-05-10)

### ADIN-GAP-03 — Role mapping creation - partial failure during immediate role application (Medium)

**Status**: RESOLVED — Cross-agent duplicate of ADIN-DES-01 (2026-05-10)

### ADIN-GAP-04 — DELETE role-mapping transaction scope ambiguity (Medium)

**Status**: RESOLVED — Added parenthetical atomicity clause to DELETE /api/v1/admin/role-mappings/{id} Processing line in docs/features/identity/ad-integration.md (2026-05-10)

### ADIN-GAP-05 — No specification for MEMBEROF attribute format matching (Medium)

**Status**: RESOLVED — Spec updated: added MEMBEROF CN extraction and case-insensitive matching details to sync algorithm step 5 (2026-05-11)

### ADIN-GAP-06 — Concurrent role mapping creation and LDAP sync (Medium)

**Status**: RESOLVED — Spec updated: added concurrency considerations section documenting role mapping creation during sync (2026-05-11)

### ADIN-GAP-07 — Safety check freeze does not cover role mapping application (Low)

**Status**: RESOLVED — Addressed: redesigned safety check as 3-level pre-flight system — Level 1 (missing user detection), Level 2 (group membership sanity), Level 3 (mass deactivation threshold) — with full-block behavior replacing partial execution (2026-05-11)

### ADIN-GAP-08 — LDAP connection timeout for background sync not specified (Low)

**Status**: RESOLVED — Addressed: added LDAP_CONNECT_TIMEOUT (30s) and LDAP_OPERATION_TIMEOUT (120s) environment variables in Implementation Notes, distinct from Celery task timeout (2026-05-11)

### ADIN-GAP-09 — Preview endpoint does not specify behavior when AD group has zero members (Low)

**Status**: RESOLVED — Fixed: added zero-member group behavior and UI rendering notes to preview endpoint section (2026-05-11)

### ADIN-GAP-10 — Metrics not reported for deactivation and reactivation operations (Low)

**Status**: RESOLVED — Fixed: added deactivations and reactivations to record_updated() in step 8 metrics (2026-05-11)

### ADIN-GAP-11 — EMPLOYEESTATUS filter vs Level 2 pre-flight check logical inconsistency (High)

**Status**: RESOLVED — Removed EMPLOYEESTATUS=active filter from LDAP query; reordered pre-flight checks (L1: missing users, L2: group membership sanity, L3: mass deactivation threshold) so each check operates on the complete dataset and serves its intended purpose (2026-05-11)

### ADIN-GAP-12 — LDAP connection failure during sync — no retry behavior specified (Medium)

**Status**: RESOLVED — Added internal retry mechanism (2 retries, 30s/60s delay) for LDAP connection and operation timeouts in Step 1; increased Celery task timeout from 300s to 900s (2026-05-11)

### ADIN-GAP-13 — Step 3 upsert — unhandled exceptions beyond UserConflictError (Medium)

**Status**: RESOLVED — Extended Step 3 error handling to catch UserConflictError, UsernameFormatError, UserValidationError, and UserNotFoundError with skip-and-continue semantics; other exceptions propagate as bug indicators (2026-05-11)

### ADIN-GAP-14 — Role mapping POST — AD query succeeds but immediate role application partially fails (Medium)

**Status**: RESOLVED — Clarified that affected_users_count counts only newly created UserRole records; duplicates are silently skipped with the unique constraint as authoritative guard (2026-05-11)

### ADIN-GAP-15 — Concurrent sync executions (Medium)

**Status**: RESOLVED — Already covered by fetcher infrastructure — BaseFetcher provides concurrency control at API level (409 Conflict) and task level (silent discard), with stale run detection as recovery. No AD-specific changes needed (2026-05-11)

### ADIN-GAP-16 — Ordering between steps 5 (role mappings) and 6 (deactivations) (Medium)

**Status**: RESOLVED — Added step ordering rationale (5→6→7) documenting that the operations are independent: step 5 manages UserRole (no TicketEvents), step 6 manages active status/tickets (with TicketEvents), step 7 sets active=true (no TicketEvents). Roles persist across status changes. Lists are mutually exclusive by construction (2026-05-11)

### ADIN-GAP-17 — What happens to manager_id when the referenced manager user is deactivated? (Medium)

**Status**: RESOLVED — Added note to step 4 that manager_id may point to an inactive user by design (AD reporting relationship persists). Future notification/escalation features must handle inactive managers (2026-05-11)

### ADIN-GAP-18 — First sync on empty database — Level 1 pre-flight check is vacuously true (Low)

**Status**: RESOLVED — Spec updated: added explicit "First sync (empty database)" note to pre-flight checks documenting that Level 1 and Level 3 pass vacuously with an empty database (2026-05-11)

### ADIN-GAP-19 — LDAP_SYNC_MAX_DEACTIVATIONS set to 0 (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-11)

### ADIN-GAP-20 — RoleMapping created_by when creating admin is later deleted/deactivated (Low)

**Status**: RESOLVED — Accepted risk: data model handles this correctly — users are never deleted, FK remains valid (2026-05-11)

### ADIN-GAP-21 — No specification for LDAP_CONNECT_TIMEOUT and LDAP_OPERATION_TIMEOUT invalid values (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-11)

---

## Coherence

### ADI-COH-001 — Inconsistent exception name for username format validation in update_user (Medium)

**Status**: RESOLVED — Fixed: aligned update_user() to use UsernameFormatError (same as create_user()) in both user-service.md and ad-integration.md (2026-05-12)

### ADIN-COH-01 — RBAC Permission Matrix missing role mapping operations (Medium)

**Status**: RESOLVED — Spec updated: added 'Manage role mappings' to rbac.md Admin Operations Permission Matrix (2026-05-11)

### ADIN-COH-02 — Terminology consistency: 'sso' vs 'ad' for user type (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-11)

### ADIN-COH-03 — LDAP sync schedule consistent across specs (Low)

**Status**: RESOLVED — architecture.md defers to the feature spec for schedule details. No inconsistency. (2026-05-10)

### ADIN-COH-04 — Role mapping endpoints consistently documented in rbac.md Endpoint Permission Map (Low)

**Status**: RESOLVED — All endpoints match between ad-integration.md and rbac.md Endpoint Permission Map. (2026-05-10)

### ADIN-COH-05 — Configuration variables consistent between spec and configuration.md (Low)

**Status**: RESOLVED — All LDAP configuration variables are consistently documented. (2026-05-10)

### ADIN-COH-06 — UserRole assigned_by semantics consistent (Low)

**Status**: RESOLVED — All specs agree: LDAP sync creates UserRole with assigned_by = NULL. (2026-05-10)

### ADIN-COH-07 — Role mapping POST response status code not explicitly stated as 201 (Low)

**Status**: RESOLVED — api-spec.md correctly defers response details to the owning spec. (2026-05-10)

### ADIN-COH-08 — DELETE role-mappings response 200 vs convention (Low)

**Status**: RESOLVED — The 200 response is explicitly justified in the spec. No contradiction. (2026-05-10)

### ADIN-COH-09 — Data model and spec agree on User table LDAP fields (Low)

**Status**: RESOLVED — Data model and LDAP spec are aligned on all AD-specific User fields. (2026-05-10)

### ADIN-COH-10 — Business Rule 6 (admin self-removal) consistent across specs (Low)

**Status**: RESOLVED — All specs consistently describe the admin self-removal protection and zero-admin recovery. (2026-05-10)

### ADIN-COH-11 — Missing environment variables in configuration.md (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-11)

---

## Design

### ADI-DES-002 — No runtime detection of CN collisions in MEMBEROF matching (High)

**Status**: RESOLVED — Accepted risk: CN collision is not a real concern in the SUSE AD environment. Risk acknowledged and accepted (2026-05-12)

### ADI-DES-001 — Transaction boundary unspecified for sync steps 3-7 (Medium)

**Status**: RESOLVED — Spec updated: added Transaction boundaries section documenting per-service-call model, crash recovery, and rationale (2026-05-12)

### ADI-DES-004 — Username/email collision resolution guidance missing (Medium)

**Status**: RESOLVED — Accepted risk: local users are primarily service accounts; username/email collision with AD entries is unlikely and does not warrant dedicated remediation guidance (2026-05-12)

### ADI-DES-003 — Concurrent sync run behavior not addressed (Medium)

**Status**: RESOLVED — Cross-agent duplicate of GAP-ADI-005 (2026-05-11)

### ADI-DES-005 — Cache staleness error message lacks actionable guidance (Low)

**Status**: RESOLVED — Proxy cache details moved to docs/data-sources.md (developer/debug reference). Implementation Notes simplified to reference data-sources.md without exposing cache internals to administrators. Error message unchanged — admin sees "group not found" without implementation details (2026-05-12)

### ADIN-DES-01 — Role mapping creation performs live AD query in synchronous API request without transactional safety (Medium)

**Status**: RESOLVED — Spec updated: specified transactional atomicity for POST role-mapping processing steps (2026-05-11)

### ADIN-DES-02 — Safety check threshold is a static env var with no runtime override for catch-up scenarios (Medium)

**Status**: RESOLVED — Spec updated: added design rationale for static safety check threshold as friction-by-design (2026-05-11)

### ADIN-DES-03 — Username rename during sync could break active sessions and references (Medium)

**Status**: RESOLVED — Spec updated: documented username rename impact and fetcher log tracking (2026-05-11)

### ADIN-DES-04 — No pagination or timeout protection for the initial AD query fetching all ~913 users (Medium)

**Status**: RESOLVED — Cross-agent duplicate of ADIN-GAP-02 (2026-05-10)

### ADIN-DES-05 — DELETE role-mapping asymmetry with users added after last sync (Low)

**Status**: RESOLVED — Accepted: DELETE operates on existing UserRole records only; deleted mapping prevents future creation during sync — behavior is correct by design (2026-05-11)

### ADIN-DES-06 — Conflict resolution for AD user colliding with existing local user requires manual intervention (Low)

**Status**: RESOLVED — Accepted: conflict already handled with WARNING log + record_failed() visible in fetcher dashboard — no escalation needed (2026-05-11)

### ADIN-DES-07 — Safety check freezes both deactivations AND reactivations together (Low)

**Status**: RESOLVED — Accepted: all-or-nothing abort is a deliberate design choice with documented rationale; separating thresholds would break consistency guarantee (2026-05-11)

### ADIN-DES-08 — Sync step 3 builds deactivation list with dead logic (Medium)

**Status**: RESOLVED — Step 3 deactivation logic simplified — "absent from AD results" branch removed since Level 1 pre-flight guarantees all known users are present; deactivation candidates now identified solely by EMPLOYEESTATUS != Active (2026-05-11)

### ADIN-DES-09 — No timeout or size guard on live AD queries from API endpoints (Medium)

**Status**: RESOLVED — Accepted risk: admin-only endpoints with existing general LDAP timeout (10-15s) provide adequate protection (2026-05-11)

### ADIN-DES-10 — UserConflictError during sync skips entry permanently (Medium)

**Status**: RESOLVED — Accepted risk: record_failed() and WARNING logs provide sufficient visibility via fetcher dashboard (2026-05-11)

### ADIN-DES-11 — CN-only matching collision risk is documented but unmonitored (Low)

**Status**: RESOLVED — Accepted risk: consistent with ADIN-SEC-11 — CN collision risk accepted as controlled in corporate AD (2026-05-11)

### ADIN-DES-12 — Celery task timeout (300s) may be tight for ~3,200 users (Low)

**Status**: RESOLVED — Celery task timeout increased from 300s to 900s, providing adequate headroom for ~3,200 users plus retry attempts (2026-05-11)

---

## Security

### SEC-ADI-001 — CN-only matching risks group name collision for privilege escalation (Medium)

**Status**: RESOLVED — Cross-agent duplicate of DES-ADI-002 (2026-05-11)

### ADI-SEC-002 — No minimum admin count enforcement enables complete admin lockout via sync (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-12)

### ADI-SEC-003 — Mass deactivation threshold upper bound allows deactivating 100 users in one run (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-12)

### ADI-SEC-004 — Preview endpoint exposes user PII to any admin without rate limiting (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-12)

### ADI-SEC-005 — Anonymous bind allows any internal host to read employee data (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-12)

### ADIN-SEC-01 — Anonymous bind exposes all employee data without authentication (Medium)

**Status**: RESOLVED — Added "Anonymous Bind Risk Acceptance" subsection to Security Considerations in `docs/features/identity/ad-integration.md` documenting the deliberate choice, acknowledged risk, existing mitigations (network access, TLS, low-sensitivity data), and future adaptation path via env vars (2026-05-11)

### ADIN-SEC-02 — No validation of ad_group_cn input for LDAP injection (Medium)

**Status**: RESOLVED — Added `ad_group_cn` input validation to both POST `/api/v1/admin/role-mappings` and POST `/api/v1/admin/role-mappings/preview` in `docs/features/identity/ad-integration.md` — rejects characters invalid for AD group CN (allows letters, numbers, spaces, hyphens, underscores, dots only), which blocks LDAP metacharacters per RFC 4515. Error code: `ROLE_MAPPING_INVALID_GROUP_CN` (422) (2026-05-11)

### ADIN-SEC-03 — No audit logging for role mapping creation and deletion (Medium)

**Status**: RESOLVED — Addressed: added structured audit logging requirement for role mapping creation and deletion in Processing sections and Security Considerations (2026-05-11)

### ADIN-SEC-04 — No minimum admin count protection during LDAP sync (Medium)

**Status**: RESOLVED — Addressed: added "Admin Lockout Risk" subsection in Security Considerations documenting the deliberate design choice, scenario, mitigations (CLI recovery), and rationale (2026-05-11)

### ADIN-SEC-05 — No TLS certificate pinning or hostname verification details (Low)

**Status**: RESOLVED — Accepted: hostname verification is implicit in standard TLS certificate validation — specifying it would be redundant (2026-05-11)

### ADIN-SEC-06 — CA certificate committed to repository (Low)

**Status**: RESOLVED — Accepted: public CA certificates are not secrets; committing them simplifies deployment with no security risk (2026-05-11)

### ADIN-SEC-07 — Role mapping preview and creation endpoints query AD live without rate limiting (Low)

**Status**: RESOLVED — Accepted: admin-only endpoints with AD timeout; rate limiting is an infrastructure concern (reverse proxy/API gateway), not application-level (2026-05-11)

### ADIN-SEC-08 — Safety check threshold is configurable via environment variable (Low)

**Status**: RESOLVED — Accepted: operator trust model — env var access implies full system control; friction by design documented in spec (2026-05-11)

### ADIN-SEC-09 — Preview endpoint may expose AD group membership to any admin (Low)

**Status**: RESOLVED — Accepted: admin-only endpoint; AD is anonymously accessible on internal network; data serves legitimate functional purpose (2026-05-11)

### ADIN-SEC-10 — MEMBEROF attribute trusted without cross-verification (Low)

**Status**: RESOLVED — Accepted: MEMBEROF is a computed attribute maintained by AD domain controller; cross-verification would query the same authoritative source twice (2026-05-11)

### ADIN-SEC-11 — CN-only matching for MEMBEROF could enable privilege escalation (Medium)

**Status**: RESOLVED — Accepted risk: CN collision is a controlled risk in a corporate AD where group creation is restricted (2026-05-11)

### ADIN-SEC-12 — No minimum admin count on role mapping deletion (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-11)

### ADIN-SEC-13 — No rate limiting on preview endpoint (Medium)

**Status**: RESOLVED — Cross-agent duplicate of ADIN-SEC-07 (2026-05-11)

### ADIN-SEC-14 — Pre-flight log leaks all missing GUIDs (Low)

**Status**: RESOLVED — Accepted risk: GUIDs are opaque identifiers with no exploitable value without AD access; needed for troubleshooting (2026-05-11)

### ADIN-SEC-15 — Pre-flight log leaks usernames (Low)

**Status**: RESOLVED — Accepted risk: username list is essential for admin troubleshooting; log access is restricted to system admins (2026-05-11)

---

## API Conventions

All endpoints are conformant with the conventions defined in `docs/api-spec.md`. No open findings.

### ADIN-API-01 — POST role-mappings/preview missing error responses for AD connectivity failures (Medium)

**Status**: RESOLVED — Added error response table to the preview endpoint definition in `docs/features/identity/ad-integration.md` documenting `422 VALIDATION_ERROR` and `503 RESOURCE_UNAVAILABLE` (2026-05-06)

### ADIN-API-02 — POST role-mappings missing 503 error code for AD unreachability (Medium)

**Status**: RESOLVED — Added `503 RESOURCE_UNAVAILABLE` to the Validation section of the POST role-mappings endpoint in `docs/features/identity/ad-integration.md` (2026-05-06)

### ADIN-API-03 — DELETE role-mappings/{id} missing 503 error for AD unreachability (Medium)

**Status**: RESOLVED — Corrected the endpoint description in `docs/features/identity/ad-integration.md` to clarify that affected users are identified from local `UserRole` records (no AD query needed). The 503 error is therefore not applicable — the operation is entirely local. (2026-05-06)

### ADIN-API-04 — POST role-mappings missing response schema (Medium)

**Status**: RESOLVED — Added explicit Response section with `201 Created` status code and JSON schema showing `data` envelope with `id`, `ad_group_cn`, `role`, `created_at`, and `affected_users_count` fields in `docs/features/identity/ad-integration.md` (2026-05-06)

### ADIN-API-05 — GET /api/v1/users and GET /api/v1/users/{user} missing error responses (Medium)

**Status**: RESOLVED — Added error response tables to both endpoints in `docs/features/identity/ad-integration.md`: `422 VALIDATION_ERROR` for search parameter violations on GET /users, and `404 USER_NOT_FOUND` for GET /users/{user} (2026-05-06)

### ADIN-API-06 — DELETE role-mappings/{id} returns 200 with body instead of standard 204 (Low)

**Status**: RESOLVED — Added explicit justification note for the 200 vs 204 deviation (side effect visibility for admin). Corrected processing steps to proper order (lookup → count → remove roles → delete mapping → respond). Fixed response message from future tense ("will revoke") to past tense ("Removed...") since the operation is already completed when the response is sent. (2026-05-06)

### ADIN-API-07 — GET /api/v1/users missing explicit data + meta envelope specification (Low)

**Status**: RESOLVED — Added explicit paginated envelope declaration (`data` array + `meta` object) with cross-reference to the User detail schema defined below in the same spec. (2026-05-06)

### ADIN-API-08 — GET /api/v1/users/{user} missing data wrapper specification (Low)

**Status**: RESOLVED — Added full JSON response example with `{"data": {...}}` envelope showing all fields (id, username, email, full_name, active, ldap_uid, manager object, roles array, timestamps). This is now the canonical schema definition — `user-management.md` endpoints reference it via cross-link. (2026-05-06)

### ADIN-API-09 — POST role-mappings missing created_by in response (Low)

**Status**: RESOLVED — Fixed: added created_by to POST role-mappings 201 response (2026-05-11)

### ADIN-API-10 — ROLE_MAPPING_GROUP_NOT_FOUND error code prefix doesn't match api-spec.md categories (Medium)

**Status**: RESOLVED — Addressed: added ROLE_MAPPING_* as new error code category in api-spec.md Error Code Categories table (2026-05-11)

### ADIN-API-11 — POST role-mappings missing error code for AD unavailability during immediate role application (Medium)

**Status**: RESOLVED — Cross-agent duplicate of ADIN-DES-01 (2026-05-10)
