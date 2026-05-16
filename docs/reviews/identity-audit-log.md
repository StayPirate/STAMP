# Review: identity-audit-log

**Spec**: `docs/features/identity/identity-audit-log.md`
**Last reviewed**: 2026-05-15
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### IAL-GAP-01 — Concurrent role mapping creation and AD sync (Medium)

**Status**: RESOLVED — By design: affected_users is a point-in-time count of what the transaction actually did; spec already documents concurrent sync behavior explicitly at ad-integration.md lines 769-772 (2026-05-16)

### IAL-GAP-02 — Deleted target user representation in API response (Medium)

**Status**: RESOLVED — Addressed by cross-cutting convention: added "User References in Responses" to api-spec.md establishing that user objects reflect current profile data, not historical snapshots (2026-05-16)

### IAL-GAP-03 — event_type filter with invalid values (Medium)

**Status**: RESOLVED — Addressed by cross-cutting convention: added "Enum Filter Validation" to api-spec.md establishing silent-ignore behavior for invalid enum filter values (2026-05-16)

### IAL-GAP-04 — target_user_id for self-service operations (Medium)

**Status**: RESOLVED — Not a gap: user_id == target_user_id for self-service is implicit from field definitions; distinction is already queryable by comparing the two fields (2026-05-16)

### IAL-GAP-05 — actor filter value "system" combined with event_type filter (Low)

**Category**: Filter behavior
**Status**: OPEN

The spec says `actor` accepts `"system"` for automated events (NULL user_id). This is clear. However, the spec does not specify behavior when `actor` is provided as a username or UUID that does not exist in the system. The base class `filter_by_actor()` accepts a username for lookup via JOIN — if the username doesn't match any user, the spec doesn't state whether this returns an empty result set or a 400/404 error.

### IAL-GAP-06 — Initial AD sync producing hundreds of events — query performance and pagination (Low)

**Category**: UX / performance
**Status**: OPEN

The spec acknowledges "On initial AD sync this may produce hundreds of `user_created` events" but does not address whether the `per_page` max of 100 and the API's fixed reverse-chronological ordering are sufficient for administrators to navigate a large initial burst. This is a minor UX gap — pagination handles it mechanically, but filtering by `event_type` or date range would be the practical workaround and those are already specified.

### IAL-GAP-07 — revoke_all_user_keys with zero active keys (Low)

**Category**: Edge case
**Status**: OPEN

The spec says `revoke_all_user_keys()` creates "N `api_key_revoked` events (one per revoked key)." When N=0 (user has no active API keys), the spec does not explicitly state whether zero events are created (implicit but obvious) or whether a summary "no keys to revoke" event is expected. The implicit resolution (zero events) is obvious.

---

## Coherence

### IAL-COH-01 — reset_password() in user-service.md omits IdentityAuditEvent creation (High)

**Status**: RESOLVED — Fixed: added password_reset audit event step and IdentityAuditEvent annotation to reset_password() in user-service.md; removed redundant audit event from POST handler in user-management.md; also fixed update_user() missing username_changed, role_mapping_created atomicity in ad-integration.md, and create_user() missing role_added events (2026-05-16)

### IAL-COH-02 — user_locked event has no specified producer in any service spec (Medium)

**Status**: RESOLVED — Removed: user_locked and user_unlocked event types removed from audit trail; lockout is a transient Redis-only state, replaced with application logging (INFO level) in local-authentication.md and user-service.md (2026-05-16)

---

## Design

### IAL-DES-01 — Initial AD sync may produce unbounded batch of audit events in a single transaction (Medium)

**Category**: Scalability
**Status**: OPEN

The spec states "On initial AD sync this may produce hundreds of `user_created` events — this is intentional." If the AD sync creates all users in a single transaction (as implied by the atomicity rule requiring audit events in the same transaction as the mutation), an initial sync of hundreds or thousands of users produces hundreds/thousands of INSERT statements in one transaction. This could cause long-running transactions, lock contention, and potential OOM in the session's identity map. Alternative: batch the AD sync into chunks (e.g., 100 users per transaction), with each chunk producing its own audit events atomically.

### IAL-DES-02 — VARCHAR columns for old_value/new_value lack defined length and structured semantics (Low)

**Category**: Schema design
**Status**: OPEN

The `old_value` and `new_value` columns are VARCHAR with no specified max length, containing human-readable strings whose format varies by event type. This creates implicit coupling between event_type and the interpretation of these fields. However, this mirrors the existing TicketAuditEvent pattern and is consistent with the infrastructure spec.

### IAL-DES-03 — No filtering by event_type combination with target_user for self-service audit visibility (Low)

**Category**: Access control
**Status**: OPEN

The endpoint is admin-only, which means users cannot view their own identity audit history. This is a deliberate access control choice and may be fine for now, but if a future requirement adds user-visible audit, the current design would need a second endpoint or permission relaxation.

---

## Security

### IAL-SEC-01 — No input validation specified for old_value/new_value VARCHAR columns (Low)

**Category**: Input validation
**Status**: OPEN

The old_value and new_value columns are VARCHAR with no specified length constraint. While these are internally generated (not direct user input), AD-synced values originate from an external system. A malicious or corrupted AD attribute could be written without bounds. The spec should define a maximum length.

### IAL-SEC-02 — detail JSONB column accepts unstructured data without schema validation (Low)

**Category**: Input validation
**Status**: OPEN

The detail JSONB column has different schemas per event type but the spec does not define a validation contract or maximum size. Since the values are generated internally by service code, the risk is low.

### IAL-SEC-03 — Audit log endpoint lacks rate limiting specification (Low)

**Category**: Rate limiting
**Status**: OPEN

The GET endpoint has no rate limiting specified. While it is restricted to Admin role, a compromised admin session could repeatedly query with broad date ranges. This is mitigated by pagination (max 100 per page).

### IAL-SEC-04 — Comma-separated event_type filter has no limit on number of values (Low)

**Category**: Input validation
**Status**: OPEN

The event_type query parameter accepts a comma-separated list with no specified maximum count. While the enum is finite, the spec does not explicitly state that invalid values are rejected.

---

## API Conventions

_No findings._
