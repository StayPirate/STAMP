# Review: local-authentication

**Spec**: `docs/features/identity/local-authentication.md`
**Last reviewed**: 2026-05-07
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### LAUTH-GAP-01 — Password prompt confirmation (double-entry) not specified (Medium)

**Status**: RESOLVED — Removed detailed CLI command descriptions from `local-authentication.md` (owned by `user-management.md`) and replaced with brief paragraph + cross-reference. The confirmation prompt detail now exists only in its single source of truth (`user-management.md`). (2026-05-07)

### LAUTH-GAP-02 — Local-to-SSO user transition unspecified (Medium)

**Status**: RESOLVED — Finding not applicable — the scenario is already handled by `ad-integration.md` (UserConflictError handling, lines 156-162) and prevented at the DB level by CHECK constraint `chk_user_auth_exclusive`. The LDAP sync skips conflicting usernames and requires manual admin resolution. (2026-05-07)

### LAUTH-GAP-03 — Race condition around lockout threshold (Low)

**Status**: RESOLVED — Clarified lockout threshold semantics in Account lockout section — explicitly states the check uses >= comparison before password verification, attacker gets exactly N verifications, and the Nth attempt is the last allowed. (2026-05-07)

### LAUTH-GAP-04 — Empty or whitespace-only username after normalization (Low)

**Status**: RESOLVED — Added explicit step 3 in login endpoint behavior that rejects empty normalized username with 401 (no lockout counter created for empty usernames). (2026-05-07)

### LAUTH-GAP-05 — TTL extension behavior on rejected-while-locked attempts (Low)

**Status**: RESOLVED — Added explicit clarification in Account lockout point 3 that rejected attempts (step 6) do NOT increment the counter or reset the TTL — lockout expires naturally from the last failed password verification. (2026-05-07)

### LAUTH-GAP-06 — Admin password reset own account — UI behavior unspecified (Low)

**Status**: RESOLVED — Added explicit reference to `authentication.md` § Frontend session behavior in the Security Considerations "Session invalidation on password change" bullet. The standard 401 handling in the parent spec covers this scenario. (2026-05-07)

### LAUTH-GAP-07 — CLI set-password missing exit code 2 for system errors (Low)

**Status**: RESOLVED — Removed detailed CLI command specification (including exit codes) from `local-authentication.md`. Exit code documentation now exists only in `user-management.md` which is the CLI command owner. (2026-05-07)

---

## Coherence

### LAUTH-COH-01 — Broken cross-reference paths missing identity/ segment (Medium)

**Status**: RESOLVED — Fixed all 8 broken cross-reference paths in `docs/features/identity/local-authentication.md` — added the missing `identity/` subdirectory segment. (2026-05-07)

### LAUTH-COH-02 — Password confirmation prompt omitted in set-password (Low)

**Status**: RESOLVED — Removed detailed CLI command flow from `local-authentication.md`. CLI behavior (including confirmation prompt) now exists only in `user-management.md`, eliminating the inconsistency. (2026-05-07)

### LAUTH-COH-03 — Inconsistent session invalidation message wording (Low)

**Status**: RESOLVED — Removed the duplicated CLI success message from `local-authentication.md`. The message wording now exists only in `user-management.md`, eliminating the wording discrepancy. (2026-05-07)

---

## Design

### LAUTH-DES-01 — Argon2id memory exhaustion under concurrent login attempts (Medium)

**Status**: RESOLVED — Replaced Argon2id (64 MiB/op) with bcrypt + SHA-256 pre-hash (~4 KB/op) in `local-authentication.md`, `authentication.md`, `user-service.md`, `rbac.md`, and `data-model.md`. The OOM risk is eliminated at the root. (2026-05-07)

### LAUTH-DES-02 — No self-service password change for local users (Medium)

**Status**: RESOLVED — Documented as explicit v1 scoping decision in "Setting a password" section and as accepted risk in Security Considerations with planned follow-up (self-service password change endpoint). (2026-05-07)

### LAUTH-DES-03 — Lockout counter extended on every failed attempt including non-existent users (Low)

**Status**: RESOLVED — Added "Permanent lockout is not possible" note in Account lockout section — once locked, rejected attempts (step 6) do not extend the TTL, so lockout expires naturally even under sustained attack. (2026-05-07)

### LAUTH-DES-04 — Redis key namespace collision for special characters in usernames (Low)

**Status**: RESOLVED — Added "Redis key namespace safety" note in Account lockout section referencing the username charset restriction `[a-z0-9._-]` from `user-management.md`. No conflicting characters possible. (2026-05-07)

---

## Security

### LAUTH-SEC-01 — Rate limiting fail-open allows brute-force during Redis outage (Medium)

**Status**: RESOLVED — Documented as accepted risk in Security Considerations with recommendation for reverse proxy rate limiting (nginx `limit_req`) for deployments in untrusted environments. (2026-05-07)

### LAUTH-SEC-02 — Per-username lockout enables account denial-of-service (Medium)

**Status**: RESOLVED — Documented as accepted risk in Account lockout Notes section with explicit mitigations: (a) no session invalidation on lockout, (b) admin unlock available, (c) auto-expiry after TTL, (d) reverse proxy per-IP rate limit for untrusted environments. (2026-05-07)

### LAUTH-SEC-03 — Lockout counter for non-existent users without bound (Low)

**Status**: RESOLVED — Documented as accepted risk in Account lockout Notes — keys are TTL-bounded (expire after `LOGIN_LOCKOUT_MINUTES`), each key is small, and per-IP rate limiting at the reverse proxy layer limits key creation rate. (2026-05-07)

### LAUTH-SEC-04 — No IP-based rate limiting on login endpoint (Low)

**Status**: RESOLVED — Added explicit "Per-IP rate limiting delegated to reverse proxy" note in Security Considerations — clarifies that IP-based throttling is intentionally handled at the reverse proxy layer (sees real client IP without X-Forwarded-For spoofing risk), not at the application level. (2026-05-07)

### LAUTH-SEC-05 — No password breach database check (Low)

**Status**: RESOLVED — Documented as explicit v1 decision in Password validation section — no breach DB check due to internal tool context, admin-set passwords, and disproportionate integration complexity. May be reconsidered if self-service password change is added. (2026-05-07)

### LAUTH-SEC-06 — No self-service password change (Low)

**Status**: RESOLVED — Resolved together with LAUTH-DES-02 — documented as explicit v1 scoping decision with accepted risk and planned follow-up in Security Considerations. (2026-05-07)

---

## API Conventions

### LAUTH-API-01 — HTTP 423 status code is non-standard for REST APIs (Medium)

**Status**: RESOLVED — Replaced HTTP 423 with HTTP 429 (Too Many Requests) in all three occurrences in `docs/features/identity/local-authentication.md`. Added `Retry-After` header documentation to the error table and the rate limiting section. (2026-05-06)
