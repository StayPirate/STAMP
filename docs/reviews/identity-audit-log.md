# Review: identity-audit-log

**Spec**: `docs/features/identity/identity-audit-log.md`
**Last reviewed**: 2026-05-15
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### IAL-GAP-01 — Concurrent role mapping creation and AD sync (Medium)

**Category**: Data accuracy
**Status**: OPEN

The `role_mapping_created` event includes `"affected_users": N` in the detail JSONB, representing users affected at mapping creation time. If an AD sync is running concurrently and adding users who are members of the mapped group, the `affected_users` count could be stale by the time the transaction commits. The spec does not clarify whether `affected_users` is a point-in-time snapshot (acceptable) or must reflect the final committed state. This affects audit accuracy for role mapping events.

### IAL-GAP-02 — Deleted target user representation in API response (Medium)

**Category**: Schema completeness
**Status**: OPEN

The response schema shows `target_user` as an object with `id`, `username`, and `full_name`. The spec states users are soft-deleted (deactivated), never hard-deleted, but does not specify what happens if a deactivated user's `username` or `full_name` was changed before deactivation. More importantly, if the `target_user_id` FK references a user whose username was later changed (via `username_changed` event from AD sync), historical audit events would display the *current* username, not the username at the time of the event. The spec does not clarify whether `target_user` reflects the user's current state or their state at event creation time.

### IAL-GAP-03 — event_type filter with invalid values (Medium)

**Category**: Input validation
**Status**: OPEN

The `event_type` parameter accepts a "comma-separated list of event types" but the spec does not define behavior when one or more values in the list are not valid `IdentityAuditEventType` enum values. Should invalid values be silently ignored (returning no matches for those types), or should the request be rejected with a 400 error? Different implementers would resolve this differently.

### IAL-GAP-04 — target_user_id for self-service operations (Medium)

**Category**: Behavioral ambiguity
**Status**: OPEN

For `api_key_created` and `api_key_revoked`, the spec says `user_id` = "Acting user" and `target_user_id` = "Key owner". When a user creates/revokes their own API key, both fields would contain the same UUID. The spec does not explicitly confirm this is the intended behavior (user_id == target_user_id for self-service). While implied, this affects filter behavior — filtering by `actor=jdoe` and `target_user=jdoe` would both return the same event, which could confuse administrators trying to distinguish "admin actions on others" from "self-service actions."

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

**Category**: Cross-spec contradiction
**Status**: OPEN

The identity-audit-log.md defines `password_reset` as an event type and its Service Contract states "Every service function that modifies identity-related data... MUST create an IdentityAuditEvent." However, user-service.md's `reset_password()` operation lists 6 behavioral steps and does NOT include creating a `password_reset` IdentityAuditEvent. In contrast, user-management.md explicitly mentions "Create IdentityAuditEvent with event_type = password_reset" as step 3 of the admin password reset endpoint. The authoritative service contract (user-service.md) contradicts both the identity-audit-log.md service contract and the user-management.md endpoint specification.

### IAL-COH-02 — user_locked event has no specified producer in any service spec (Medium)

**Category**: Missing producer
**Status**: OPEN

The identity-audit-log.md defines `user_locked` with trigger "Failed password threshold exceeded" and user_id=NULL (system). However, no other spec documents which code path creates this event. The local-authentication.md describes the lockout mechanism (Redis counter reaches threshold) but never mentions creating an IdentityAuditEvent. The user-service.md does not have a `lock_user()` operation. Since lockout is a Redis-only operation with no DB transaction, the atomicity requirement ("same database transaction as the mutation") cannot be satisfied in the standard way.

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
