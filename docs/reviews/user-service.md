# Review: user-service

**Spec**: `docs/features/identity/user-service.md`
**Last reviewed**: 2026-05-08
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### USVC-GAP-01 — User deletion not addressed (Medium)

**Status**: RESOLVED — Addressed in spec update (2026-05-08)

### USVC-GAP-02 — unlock_user behavior when Redis unreachable differs from reset_password (Medium)

**Status**: RESOLVED — Addressed in spec update (2026-05-08)

### USVC-GAP-03 — update_roles with same entry in both add and remove lists — self-removal guard bypass (Medium)

**Status**: RESOLVED — Closed without changes: behavior is correct as designed — cancellation means no role change occurs, self-removal guard is not bypassed (2026-05-08)

### USVC-GAP-04 — deactivate_user ticket unassignment scope unclear for tickets in other statuses (Medium)

**Status**: RESOLVED — Addressed in spec update (2026-05-08)

### USVC-GAP-05 — create_user with ad_object_guid uniqueness not specified (Medium)

**Status**: RESOLVED — Addressed in spec update (2026-05-08)

### USVC-GAP-06 — manager_id referencing non-existent or inactive user (Low)

**Status**: RESOLVED — Closed without changes: FK constraint prevents referencing non-existent users; inactive manager is intentionally allowed (2026-05-08)

### USVC-GAP-07 — deactivate_user ticket unassignment scope unclear for tickets in other statuses (Low)

**Status**: RESOLVED — Closed without changes: duplicate of USVC-GAP-04 (addressed there) (2026-05-08)

### USVC-GAP-08 — No validation that roles list contains valid Role enum values (Low)

**Status**: RESOLVED — Closed without changes: Role enum type validation handled by Pydantic at API layer and Click at CLI layer (2026-05-08)

---

## Coherence

### USVC-COH-01 — user-management.md intro claims admins can deactivate/reactivate AD users (Medium)

**Status**: RESOLVED — Addressed in spec update (2026-05-08)

### USVC-COH-02 — duplicate_target_changed event type added to ticket-history frontend display table (Low)

**Status**: RESOLVED — Added duplicate_target_changed with display label to the Filter Bar display label mapping table in ticket-audit-log.md (2026-05-08)

### USVC-COH-03 — Password minimum length inconsistency across identity specs (High)

**Status**: RESOLVED — Aligned all password length references to 16-128 characters across all identity specs (2026-05-07)

### USVC-COH-04 — Inconsistent Redis failure semantics between unlock_user() and reset_password() (Medium)

**Status**: RESOLVED — Addressed in spec update (2026-05-08)

---

## Design

### USVC-DES-01 — Redis failure in unlock_user raises hard error vs soft handling elsewhere (Medium)

**Status**: RESOLVED — Addressed in spec update (2026-05-08)

### USVC-DES-02 — asyncio.run() from Celery tasks may conflict with existing event loop (Medium)

**Status**: RESOLVED — Closed without changes: project uses prefork pool, asyncio.run() is safe (2026-05-08)

### USVC-DES-03 — No upper bound on tickets unassigned during deactivation (Medium)

**Status**: RESOLVED — Closed without changes: single-transaction atomicity is correct; theoretical scaling concern does not justify added complexity (2026-05-08)

### USVC-DES-04 — Deactivation side-effect ordering creates a window where user is active but locked out (Low)

**Status**: RESOLVED — Closed without changes: intentional security-first ordering, documented in spec (2026-05-08)

### USVC-DES-05 — manager_id blocked for local users but may be needed for organizational hierarchy (Low)

**Status**: RESOLVED — Closed without changes: intentional design — manager_id is AD-only, local users not in org hierarchy (2026-05-08)

---

## Security

### USVC-SEC-01 — No rate limiting on password reset operation (Medium)

**Status**: RESOLVED — Closed without changes: admin-only operation, rate limiting adds no security value given admin trust level (2026-05-08)

### USVC-SEC-02 — Password logged or exposed in error messages (Medium)

**Status**: RESOLVED — Addressed in spec update (2026-05-08)

### USVC-SEC-03 — Session invalidation proceeds despite Redis failure (Medium)

**Status**: RESOLVED — Closed without changes: mitigation already specified in rbac.md (middleware checks User.active) and user-service.md (DB fallback on cache miss) (2026-05-08)

### USVC-SEC-04 — Redis key pattern for lockout uses username without namespace isolation (Low)

**Status**: RESOLVED — Closed without changes: username format [a-z0-9._-] prevents any Redis key injection (2026-05-08)

### USVC-SEC-05 — unlock_user does not restrict to local users only (Low)

**Status**: RESOLVED — Closed without changes: operation is a harmless no-op for SSO users (Redis key never exists) (2026-05-08)

### USVC-SEC-06 — No maximum password length enforcement could enable DoS via bcrypt (Low)

**Status**: RESOLVED — Closed without changes: 72-byte bcrypt limit is well-known and provides sufficient entropy; informational only (2026-05-08)

---

## API Conventions

_No findings — the spec does not define API endpoints._
