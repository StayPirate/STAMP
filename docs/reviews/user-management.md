# Review: user-management

**Spec**: `docs/features/identity/user-management.md`
**Last reviewed**: 2026-05-09
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### UMGT-GAP-31 — Admin API roles endpoint missing error response for invalid role names (Medium)

**Status**: RESOLVED — Not a real gap — invalid role names are rejected by Pydantic enum validation (global 422). Per api-spec.md Global Responses, per-endpoint tables must not repeat framework-handled errors. Added cross-cutting convention in conventions.md (Feature Specifications > API Cross-references) and added api-spec.md to Cross-references of all 16 specs defining API endpoints (2026-05-09).

### UMGT-GAP-32 — Conflicting --add-role and --remove-role resolution not specified at CLI layer (Medium)

**Status**: RESOLVED — Clarified in step 9 that CLI passes add/remove roles verbatim to the service — no client-side pre-processing. The service handles deduplication and conflict resolution. (2026-05-09)

### UMGT-GAP-33 — Update command step 8 missing error message text for duplicate email (Low)

**Status**: RESOLVED — Added exact error message `"Error: Email '{email}' is already in use."` to step 8 of the update command. (2026-05-09)

### UMGT-GAP-34 — No TTY behavior specified for deactivate command without --yes (Low)

**Status**: RESOLVED — Removed `--yes` flag entirely — deactivate is always interactive (scripted deactivation uses the API). Added no-TTY error message to deactivate command. Uniformized no-TTY messages across create (`password input`), set-password (`password input`), and deactivate (`confirmation required`). (2026-05-09)

### UMGT-GAP-35 — Search parameter with exactly 2 characters — partial match behavior unspecified (Low)

**Status**: RESOLVED — Changed search description from "supports partial matching" to "Case-insensitive substring match (SQL `ILIKE '%query%'`)" in the GET /api/v1/users endpoint. (2026-05-09)

### UMGT-GAP-01 — manage-user create — no TTY detection behavior specified (Medium)

**Status**: RESOLVED — Added TTY detection error clause (exit 1, stderr message) to create command (2026-05-08)

### UMGT-GAP-02 — manage-user set-password — no TTY detection behavior specified (Medium)

**Status**: RESOLVED — Added TTY detection error clause (exit 1, stderr message) to set-password command (2026-05-08)

### UMGT-GAP-03 — API POST /api/v1/admin/users/{user}/roles — invalid role values not specified (Medium)

**Status**: RESOLVED — Non-issue: Pydantic enum validation handles this automatically per api-spec.md Global Responses convention (2026-05-08)

### UMGT-GAP-04 — GET /api/v1/users — type filter not listed as query parameter (Medium)

**Status**: RESOLVED — Added type query parameter (local|ad) to GET /api/v1/users (2026-05-08)

### UMGT-GAP-05 — manage-user update — role validation happens after LDAP identity guard but role operation itself can fail with AD-derived role protection error not shown in example (Low)

**Status**: RESOLVED — Added AD-derived role rejection example to CLI update multi-step output (2026-05-08)

### UMGT-GAP-06 — manage-user update — conflicting --add-role and --remove-role for same role (Low)

**Status**: RESOLVED — Clarified role update output shows net delta (before/after), not individual operations (2026-05-08)

### UMGT-GAP-07 — manage-user deactivate — self-deactivation not guarded in CLI (Low)

**Status**: RESOLVED — Non-issue: intentional design, already documented in user-service.md (acting_user_id=None bypasses self-operation guards) (2026-05-08)

### UMGT-GAP-08 — POST /api/v1/admin/users/{user}/roles — removing a role the user doesn't have (Low)

**Status**: RESOLVED — Added explicit no-op statement for removing non-assigned role to API endpoint (2026-05-08)

### UMGT-GAP-09 — manage-user deactivate — race between impact query and deactivation (Low)

**Status**: RESOLVED — Non-issue: TOCTOU race is theoretical with no functional impact; impact counts are advisory (2026-05-08)

### UMGT-GAP-10 — Admin UI create endpoint not specified (Medium)

**Status**: RESOLVED — Local user creation restricted to CLI only. "Create" removed from Admin UI actions in user-management.md. No API endpoint needed since the operation is not available via UI/API. (2026-05-05)

### UMGT-GAP-11 — No deactivation/reactivation endpoint defined for admin UI (Medium)

**Status**: RESOLVED — Added Admin API endpoints table to user-management.md listing all endpoints used by the Admin UI with cross-references to their source specs. Deactivate/Reactivate now explicitly reference `PATCH /api/v1/admin/users/{user_id}/active` from rbac.md. (2026-05-05)

### UMGT-GAP-12 — `set-password` error behavior for SSO users only implied (Medium)

**Status**: RESOLVED — Added explicit error message and exit code to the `set-password` CLI section: `"Error: Cannot set password for SSO user '{username}'. SSO users authenticate via id.suse.com."` (exit code 1). (2026-05-05)

### UMGT-GAP-13 — Concurrent CLI and LDAP sync role modification (Medium)

**Status**: RESOLVED — Added "Concurrent role modification from multiple entry points" section to `user-service.md` explaining that no locking is needed — operations are atomic INSERT/DELETE on independent tuples with disjoint key spaces (`_manual` vs AD group CN) and UNIQUE constraint protection. (2026-05-05)

### UMGT-GAP-14 — Reactivate on already-active user output unspecified (Low)

**Status**: RESOLVED — The `update` command summary now lists only actual changes. If all operations are no-ops, prints `"No changes applied to user '{username}'."` (exit 0). The `deactivate` command is now also idempotent — delegates to the service directly, which returns no-op for already-inactive users. (2026-05-05)

### UMGT-GAP-15 — Removing a role the user does not have (Low)

**Status**: RESOLVED — Covered by the same fix as UMGT-GAP-05. The `update` command summary only lists actual changes; removing a non-existent role is a service-level no-op and does not appear in the output. (2026-05-05)

### UMGT-GAP-16 — Admin UI password reset lacks confirmation specification (Low)

**Status**: RESOLVED — Added "Reset password flow" subsection to the Administration UI section of user-management.md specifying the full UI flow: inline form with double password entry (new + confirm), client-side validation (match check, length policy), API call, success/error handling. The double-entry form eliminates accidental activation risk. (2026-05-05)

### UMGT-GAP-17 — No `POST /api/v1/admin/users` in api-spec.md (Low)

**Status**: RESOLVED — Local user creation is CLI-only by design. No API endpoint is needed, so its absence from api-spec.md is correct. (2026-05-05)

### UMGT-GAP-18 — Deactivation impact staleness between preview and execution (Medium)

**Status**: RESOLVED — Added explicit "Semantics" paragraph to the `GET /api/v1/admin/users/{user_id}/deactivation-impact` endpoint in `docs/features/identity/user-management.md` clarifying that the response is a point-in-time snapshot with no accuracy guarantee, and that deactivation proceeds regardless of changes since the preview was fetched. (2026-05-05)

### UMGT-GAP-19 — `update` command behavior when user is inactive (Medium)

**Status**: RESOLVED — Expanded the `update` command description in `docs/features/identity/user-management.md` to explicitly state that the command operates on users regardless of active/inactive status, and that modifications to email, full_name, and roles are permitted on inactive users for pre-reactivation preparation. (2026-05-05)

### UMGT-GAP-20 — `set-password` on an inactive local user (Medium)

**Status**: RESOLVED — Added explicit note to the `set-password` CLI command and `PUT /api/v1/admin/users/{user_id}/password` endpoint in `docs/features/identity/user-management.md` stating that both operate on inactive local users for credential preparation before reactivation. Also added a general "Inactive user management principle" in the deactivation section. (2026-05-05)

### UMGT-GAP-21 — `unlock` on an inactive or SSO user (Medium)

**Status**: RESOLVED — Added warning messages (steps 3 and 4) to the `unlock` command behavior in `docs/features/identity/user-management.md` for inactive users and SSO users. The command proceeds in both cases (idempotent, exit code 0) but prints a warning to stderr informing the admin that the operation has no practical effect. (2026-05-05)

### UMGT-GAP-22 — Role validation overlap between manual and AD-derived assignments (Medium)

**Status**: RESOLVED — Added "Role Origins and Coexistence" section to `docs/features/identity/rbac.md` as the canonical definition of dual-origin semantics: manual assignment when role already exists via AD creates a separate `_manual` record, both origins coexist independently. Added cross-references in `docs/features/identity/user-management.md` (role management endpoint + Admin UI roles column) and `docs/features/identity/ad-integration.md` (step 5 of sync algorithm). (2026-05-05)

### UMGT-GAP-23 — PATCH endpoint on deactivated user (Low)

**Status**: RESOLVED — Added explicit note to `PATCH /api/v1/admin/users/{user_id}` endpoint description in `docs/features/identity/user-management.md` stating it operates on both active and inactive users. Covered by the general "Inactive user management principle" added to the deactivation section. (2026-05-05)

### UMGT-GAP-24 — Invalid user_id format in API endpoints (Low)

**Status**: RESOLVED — Auto-resolved: spec now uses `{user}` in all API paths (not `{user_id}`), following the User Identifier Resolution convention from `docs/api-spec.md`. Non-UUID values are treated as username lookups; the only error case is 404. No "invalid format" scenario exists. (2026-05-06)

### UMGT-GAP-25 — `update` command order-of-operations on partial failure (Medium)

**Status**: RESOLVED — Reordered steps (roles before reactivation) and added explicit fail-fast semantics with structured error reporting (✓/✗/—) in `docs/features/identity/user-management.md` (2026-05-06)

### UMGT-GAP-26 — Redis unavailability during deactivation session invalidation (Medium)

**Status**: RESOLVED — Risk is negligible and does not require spec changes. The `get_current_user` middleware checks `user.active` from the database on every request — a deactivated user is rejected with HTTP 401 regardless of Redis session cache state. Maximum theoretical exposure window: 60 seconds. Practical risk: near zero. (2026-05-06)

### UMGT-GAP-27 — Empty role arrays in PUT roles endpoint (Low)

**Status**: RESOLVED — Auto-resolved: spec already explicitly documents that empty arrays are a no-op returning HTTP 200 with unchanged user profile in the standard envelope. (2026-05-06)

### UMGT-GAP-28 — Deactivation impact preview allows self-targeting (Low)

**Status**: RESOLVED — Added 409 `USER_SELF_DEACTIVATION` error response to the `GET /api/v1/admin/users/{user}/deactivation-impact` endpoint in `docs/features/identity/user-management.md`. The preview now rejects self-targeting with the same code as the actual deactivation endpoint. (2026-05-06)

### UMGT-GAP-29 — Username normalization inconsistency across CLI commands (Low)

**Status**: RESOLVED — Added normalization step (trim whitespace, lowercase) to `update`, `deactivate`, and `set-password` commands in `docs/features/identity/user-management.md`. Added "Username normalization" rule to `docs/conventions.md` CLI Command Design section to prevent recurrence in future commands. (2026-05-06)

### UMGT-GAP-30 — No `sentinel manage-user list` command (Low)

**Status**: RESOLVED — Added `sentinel manage-user list` (with --active/--inactive, --role, --type filters, tabular output, no pagination limit) and `sentinel manage-user show` (detailed single-user view with role origins) CLI commands to `docs/features/identity/user-management.md`. (2026-05-06)

---

## Coherence

### UMGT-COH-10 — unlock_user() acting_user_id type mismatch between user-service.md and user-management.md CLI usage (Medium)

**Status**: RESOLVED — Fixed `unlock_user` signature in user-service.md — changed `acting_user_id` type from `UUID` to `UUID | None`, consistent with all other service operations. (2026-05-09)

### UMGT-COH-11 — rbac.md Admin prose understates user management capabilities (Low)

**Status**: RESOLVED — Simplified Admin prose in rbac.md to use aggregative labels without parenthetical examples (e.g., "Manage users" instead of "Manage users (update roles, deactivate local users)"). Added ~40 missing endpoints to the Endpoint Permission Map (tickets, packages, references, CVSS, fetchers, maintainer dashboard, submission tracking). Marked the Endpoint Permission Map as a derived summary index — authoritative source is the owning feature spec. Added access level convention to api-spec.md. Added Guardrail 22 to AGENTS.md for proactive rbac.md sync. (2026-05-09)

### UMGT-COH-12 — Inconsistent terminology: "LDAP user" vs "AD user" vs "LDAP-managed user" (Low)

**Status**: RESOLVED

### UMGT-COH-01 — Access level contradiction for public user endpoints (Medium)

**Status**: RESOLVED — Added Anonymous (read-only) access level to rbac.md; aligned user-management.md endpoint definitions (2026-05-08)

### UMGT-COH-02 — Owning spec mismatch for GET /api/v1/users and GET /api/v1/users/{user} (Low)

**Status**: RESOLVED — Corrected owning spec from ad-integration.md to user-management.md in rbac.md (2026-05-08)

### UMGT-COH-03 — CLI deactivate allows last-admin removal vs deactivation-impact warning (Low)

**Status**: RESOLVED — Non-issue: Warning semantics already defined in docs/conventions.md CLI Output Contract (2026-05-08)

### UMGT-COH-04 — ad-integration.md contradicts Admin UI "Create" action (High)

**Status**: RESOLVED — ad-integration.md Business Rule 1 rewritten to state that local user creation is exclusively via CLI. user-management.md "Create" action removed from Admin UI section. Both specs now agree. (2026-05-05)

### UMGT-COH-05 — Deactivation endpoint not consistently cross-referenced (Medium)

**Status**: RESOLVED — All user mutation endpoints centralized in user-management.md (Admin API endpoints section). `PATCH /api/v1/admin/users/{user_id}/active` is now fully specified there, with rbac.md and other specs containing only cross-references. (2026-05-05)

### UMGT-COH-06 — Password reset endpoint not referenced (Medium)

**Status**: RESOLVED — `PUT /api/v1/admin/users/{user_id}/password` is now fully specified in user-management.md (Admin API endpoints section) including error responses for SSO users. local-authentication.md contains only a cross-reference. (2026-05-05)

### UMGT-COH-07 — "Delete" terminology vs "Deactivate" (Medium)

**Status**: RESOLVED — Renamed CLI command from `sentinel manage-user delete` to `sentinel manage-user deactivate` in `user-management.md` and `cli-reference.md`. Terminology now aligns with all other specs. (2026-05-05)

### UMGT-COH-08 — "SSO users" vs "AD users" terminology (Low)

**Status**: RESOLVED — Terminology is already consistent in practice — "SSO user" is the canonical term used in all user-facing and domain contexts. "AD user" appears only in the infrastructure context of the sync process where it is appropriate. No changes needed. (2026-05-05)

### UMGT-COH-09 — "No last admin" vs self-removal guard scope (Low)

**Status**: RESOLVED — Expanded Business Rule 2 in `user-management.md` with full clarification of UI/API vs CLI/system scenarios, platform continuity, and recovery procedure. Updated `rbac.md` Business Rule 1 and `user-service.md` self-removal guard to cross-reference the canonical definition. (2026-05-05)

---

## Design

### UMGT-DES-10 — Partial failure in CLI update leaves user in ambiguous state without transactional rollback (Medium)

**Status**: RESOLVED — Accepted risk: fail-fast behavior is intentional and well-documented in CLI conventions (docs/conventions.md, Multi-Step Reporting). The structured step reporting (✓/✗/—) gives admins clear visibility into partial state. Wrapping in a single transaction would lose partial progress reporting. (2026-05-09)

### UMGT-DES-11 — Local user creation restricted to CLI with no API equivalent violates API-first principle (Medium)

**Status**: RESOLVED — Not a violation: the API-first rule in docs/conventions.md applies to UI↔API parity ("Every operation available through the UI must be achievable through the API alone"). Local user creation is intentionally CLI-only and is NOT available in the UI either — therefore no parity gap exists. (2026-05-09)

### UMGT-DES-12 — Deactivation-impact endpoint is a TOCTOU preview with no binding guarantee (Low)

**Status**: RESOLVED

### UMGT-DES-13 — Public user endpoints expose email and profile data without authentication (Low)

**Status**: RESOLVED — Cross-agent duplicate of UMGT-SEC-01 (2026-05-09)

### UMGT-DES-01 — Public user endpoints expose all users without authentication (Medium)

**Status**: RESOLVED — Non-issue: intentional design — data already accessible via anonymous LDAP on internal network (2026-05-08)

### UMGT-DES-02 — Unlock CLI bypasses user existence validation (Medium)

**Status**: RESOLVED — Added database lookup step to the `unlock` CLI command behavior — user existence is now validated before the Redis operation, consistent with the API endpoint. (2026-05-05)

### UMGT-DES-03 — Admin UI actions lack explicit API endpoint cross-references (Medium)

**Status**: RESOLVED — All user mutation endpoints centralized in user-management.md with full specifications (request/response schemas, error codes, behavior). Other specs now contain only cross-references. (2026-05-05)

### UMGT-DES-04 — TTY requirement blocks automation for local user creation (Medium)

**Status**: RESOLVED — Local user accounts are always created manually by an admin with shell access. Automated provisioning of local accounts is not a supported use case — bot/service accounts in automated environments should use SSO or be pre-created by an admin. The TTY requirement is an intentional security measure. (2026-05-05)

### UMGT-DES-05 — Unlock command leaks Redis key format across specs (Medium)

**Status**: RESOLVED — Replaced explicit Redis key format references in `user-management.md` with abstract language plus a cross-reference to `local-authentication.md` as the authoritative source for lockout mechanism details. (2026-05-06)

### UMGT-DES-06 — Local user creation restricted to CLI only — no API endpoint (Medium)

**Status**: RESOLVED — Duplicate of UMGT-DES-03/UMGT-GAP-01/UMGT-GAP-08. Same concern already accepted as intentional design. (2026-05-08)

### UMGT-DES-07 — Deactivation impact endpoint returns stale data without TOCTOU mitigation (Medium)

**Status**: RESOLVED — Duplicate of UMGT-GAP-09. TOCTOU already documented with explicit Semantics paragraph. (2026-05-08)

### UMGT-DES-08 — No rate limiting on admin password reset endpoint (Medium)

**Status**: RESOLVED — Duplicate of UMGT-SEC-02. Risk explicitly accepted — admin is highest trust level. (2026-05-08)

### UMGT-DES-09 — Partial failure in manage-user update leaves inconsistent state without transactional rollback (Medium)

**Status**: RESOLVED — Duplicate of UMGT-GAP-16. Fail-fast semantics with structured step reporting already specified. (2026-05-08)

---

## Security

### UMGT-SEC-19 — Public user endpoints expose email addresses and internal identifiers without authentication (Medium)

**Status**: RESOLVED — Cross-agent duplicate of UMGT-DES-01 (2026-05-09)

### UMGT-SEC-20 — No rate limiting on admin password reset endpoint (Low)

**Status**: RESOLVED — Cross-agent duplicate of UMGT-DES-08 (2026-05-09)

### UMGT-SEC-21 — No step-up authentication for destructive admin operations (Low)

**Status**: RESOLVED

### UMGT-SEC-22 — Deactivation impact endpoint is a TOCTOU information-only preview (Low)

**Status**: RESOLVED — Cross-agent duplicate of UMGT-DES-07 (2026-05-09)

### UMGT-SEC-01 — Public user endpoints expose sensitive internal fields (Medium)

**Status**: RESOLVED — Non-issue: data already accessible via anonymous LDAP on internal network (2026-05-08)

### UMGT-SEC-02 — Password transmitted in plaintext in API request body (Medium)

**Status**: RESOLVED — Non-issue: password in JSON body over HTTPS is industry standard practice (2026-05-08)

### UMGT-SEC-03 — User list endpoint accessible without authentication exposes organizational structure (Medium)

**Status**: RESOLVED — Non-issue: organizational data already accessible via anonymous LDAP on internal network (2026-05-08)

### UMGT-SEC-04 — No rate limiting on public user search endpoint (Low)

**Status**: RESOLVED — Non-issue: rate limiting is infrastructure responsibility (WAF/reverse proxy), not application spec (2026-05-08)

### UMGT-SEC-05 — No maximum length on search parameter (Low)

**Status**: RESOLVED — Added maximum 100 characters constraint to search query parameter (2026-05-08)

### UMGT-SEC-06 — No self-deactivation guard explicitly defined for CLI (Low)

**Status**: RESOLVED — Non-issue: intentional design, already documented in user-service.md (CLI passes acting_user_id=None) (2026-05-08)

### UMGT-SEC-07 — Password visible in process listing and shell history (Medium)

**Status**: RESOLVED — Removed `--password` CLI argument entirely from `create` and `set-password` commands. Password is now collected exclusively via hidden interactive prompt. Commands require a TTY and cannot be scripted. (2026-05-05)

### UMGT-SEC-08 — No rate limiting on admin password reset API (Medium)

**Status**: RESOLVED — Accepted as-is. Admin password reset is logged at INFO level with acting admin identity. No rate limiting or step-up auth added — admin is the highest trust level. (2026-05-05)

### UMGT-SEC-09 — No "last admin" protection enables zero-admin lockout (Medium)

**Status**: RESOLVED — The zero-admin state cannot be reached via the API or UI — the self-removal guard prevents the last remaining admin from removing their own role (HTTP 409). The only paths to zero-admin are CLI or LDAP sync. (2026-05-05)

### UMGT-SEC-10 — No audit event for admin unlock action (Low)

**Status**: RESOLVED — Added INFO-level logging requirement to both the API endpoint and the CLI command in `docs/features/identity/user-management.md`. (2026-05-05)

### UMGT-SEC-11 — No destructive action confirmation in CLI (Low)

**Status**: RESOLVED — Added interactive confirmation prompt with impact summary to CLI `deactivate` command with `--yes` flag for scripted use. Added `GET /api/v1/admin/users/{user_id}/deactivation-impact` endpoint. (2026-05-05)

### UMGT-SEC-12 — Rate limiting fail-open on Redis unavailability (High)

**Status**: RESOLVED — This risk is owned and already documented as accepted in `local-authentication.md` (see AUTH-SEC-01). The `user-management` spec is a consumer of the authentication mechanism and is not the appropriate location to document or mitigate this behavior. (2026-05-05)

### UMGT-SEC-13 — LDAP plaintext connection susceptible to MitM (Medium)

**Status**: RESOLVED — The ad-integration.md spec already specifies LDAPS (port 636) with TLS certificate validation against SUSE Trust Root CA. Removed contradictory stale passage. (2026-05-05)

### UMGT-SEC-14 — No notification to user on admin password reset (Low)

**Status**: RESOLVED — Accepted risk — documented in `docs/features/identity/user-management.md` Security Considerations. Justification: admin trust model, audit log provides forensic trail. (2026-05-06)

### UMGT-SEC-15 — Username lockout allows denial of service (Low)

**Status**: RESOLVED — Accepted risk — documented in `docs/features/identity/user-management.md` Security Considerations. Key mitigations: lockout is temporary (auto-expires via Redis TTL), admin can unlock immediately, existing sessions are NOT invalidated. (2026-05-06)

### UMGT-SEC-16 — No maximum length validation on role mapping ad_group_cn (Low)

**Status**: RESOLVED — Added `max_length: 256` validation to `POST /api/v1/admin/role-mappings` in `docs/features/identity/ad-integration.md`. Updated column type in `docs/data-model.md`. (2026-05-06)

### UMGT-SEC-17 — Deactivation-impact endpoint reveals internal state counts (Low)

**Status**: RESOLVED — Accepted risk — endpoint is admin-only; exposed counts are necessary for informed deactivation decisions. No spec change needed. (2026-05-06)

### UMGT-SEC-18 — Admin password reset lacks step-up authentication or confirmation (Medium)

**Status**: RESOLVED — Duplicate of UMGT-SEC-02. Step-up auth explicitly rejected in prior resolution. (2026-05-08)

---

## API Conventions

### UMGT-API-09 — POST /api/v1/admin/users/{user}/roles missing error for invalid role values (Medium)

**Status**: RESOLVED — Not a real gap — invalid role names are rejected by Pydantic enum validation (global 422). Per api-spec.md Global Responses, per-endpoint tables must not repeat framework-handled errors. Added cross-cutting convention in conventions.md (Feature Specifications > API Cross-references) and added api-spec.md to Cross-references of all 16 specs defining API endpoints (2026-05-09).

### UMGT-API-10 — GET /api/v1/users lists "role" as valid sort_by field (Low)

**Status**: RESOLVED

### UMGT-API-11 — Missing error code for invalid sort_by/sort_order values (Low)

**Status**: RESOLVED

### UMGT-API-01 — Missing VALIDATION_ERROR for invalid role values in POST roles (Low)

**Status**: RESOLVED — Non-issue: Pydantic enum validation handles this automatically per api-spec.md Global Responses convention (2026-05-08)

### UMGT-API-02 — Missing sortable fields documentation for GET /api/v1/users (Low)

**Status**: RESOLVED — Added valid sort_by fields: username (default), full_name, email, role, created_at (2026-05-08)

### UMGT-API-03 — Deactivation-impact returns 409 for already-inactive user with non-standard code (Low)

**Status**: RESOLVED — Registered USER_ALREADY_INACTIVE error code in docs/api-spec.md (2026-05-08)

### UMGT-API-04 — POST /roles endpoint uses noun instead of verb but has side effects (Low)

**Status**: RESOLVED — Conformant — POST to a sub-resource collection (/roles) is standard REST for adding items; side effects (audit, protection checks) do not require verb-style paths. (2026-05-08)

### UMGT-API-05 — GET /api/v1/users search minimum length returns 422 correctly (Low)

**Status**: RESOLVED — Conformant — 422 with VALIDATION_ERROR is correct per conventions. (2026-05-08)

### UMGT-API-06 — PATCH for deactivation/reactivation has significant side effects (Medium)

**Status**: RESOLVED — Replaced `PATCH /api/v1/admin/users/{user}/active` with two separate endpoints: `POST .../deactivate` and `POST .../reactivate`. Updated `user-management.md`, `api-spec.md`, and `rbac.md` Endpoint Permission Map. (2026-05-06)

### UMGT-API-07 — Missing error code for empty request body in POST roles (Low)

**Status**: RESOLVED — Documented empty arrays as no-op (200 with unchanged profile) in `docs/features/identity/user-management.md` POST roles validation rules (2026-05-06)

### UMGT-API-08 — POST unlock returns 204 while other action endpoints return 200 with data (Low)

**Status**: RESOLVED — Changed POST unlock response from HTTP 204 to HTTP 200 with `{"data": {"detail": "Account unlocked successfully."}}` in `docs/features/identity/user-management.md`, aligning with POST password and API conventions (2026-05-06)
