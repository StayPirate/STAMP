# Review: api-key-service

**Spec**: `docs/features/identity/api-key-service.md`
**Last reviewed**: 2026-05-17
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### AKS-GAP-001 — revoke_all_user_keys does not check user existence (Medium)

**Category**: Error paths
**Status**: OPEN

The `revoke_all_user_keys()` operation does not list any precondition for user existence — unlike `create_key()` which specifies `UserNotFoundError`. If called with a non-existent `user_id`, the behavior is unspecified: it would silently return 0, which is indistinguishable from a valid user with no active keys. While current callers (user_service.deactivate_user) would have already validated the user, the service contract should be self-documenting for future callers.

### AKS-GAP-004 — Idempotent revoke skips audit event creation (Medium)

**Category**: State machine completeness
**Status**: OPEN

For `revoke_key()`, the spec says if the key is already revoked, return the key unchanged without error. Step 2 returns before reaching step 5 (audit event creation). This means an admin revoking an already-revoked key gets no audit record of their attempt. This is likely intentional but creates an asymmetry: the first revocation is audited, subsequent attempts are invisible. The spec should explicitly state whether this is desired.

### AKS-GAP-007 — Concurrent creation of keys with same name (Medium)

**Category**: Temporal and concurrency
**Status**: OPEN

The spec checks name uniqueness among non-revoked keys at service level, but two concurrent requests creating a key with the same name could both pass the check before either commits. The data model has a partial unique index which would catch this at DB level, but the spec doesn't specify what exception the service should translate a database IntegrityError into.

### AKS-GAP-002 — No maximum limit on active API keys per user (Low)

**Status**: RESOLVED — Cross-agent duplicate of AKS-DES-001 (2026-05-17)

### AKS-GAP-003 — Name whitespace handling unspecified (Low)

**Category**: Boundary conditions
**Status**: OPEN

The spec states `name` must be '1-128 characters' but does not specify whether leading/trailing whitespace is trimmed before validation. A name of 128 spaces would pass the length check. Similarly, names that differ only by whitespace would not conflict. This could confuse users viewing their key list.

### AKS-GAP-005 — expires_at race condition at creation boundary (Low)

**Category**: Temporal and concurrency
**Status**: OPEN

The spec validates 'expires_at must be in the future' at service call time, but the key is not yet committed. If `expires_at` is set to seconds from now, it could already be expired by the time the transaction commits and the response reaches the client. This is a minor edge case with an obvious implicit resolution (don't set expiry to seconds from now), but the spec doesn't address minimum expiry duration.

### AKS-GAP-006 — No operation for listing or reading keys (Low)

**Category**: Data lifecycle gaps
**Status**: OPEN

The service defines create, revoke, and revoke-all but no read/list operation. Key listing logic lives elsewhere (presumably directly in API handlers), which diverges from the centralization goal stated in the Purpose section. Listing is arguably not a 'lifecycle operation' but the spec doesn't explicitly scope what's included/excluded.

---

## Coherence

### AKS-COH-001 — Prefix column size contradicts example values across specs (Low)

**Category**: Contradictory definitions
**Status**: OPEN

The api-key-service spec states 'prefix = first 12 characters of the full key' and the data model defines the column as VARCHAR(12). However, the example prefix used in authentication.md and data-model.md (`stl_ak_7f3a9b`) is 13 characters long. Either the prefix should be the first 13 characters (and the column VARCHAR(13)), or the examples should be truncated to 12 characters.

---

## Design

### AKS-DES-001 — No maximum key limit enforcement, only warning log (Medium)

**Category**: Edge cases and risks
**Status**: OPEN

The spec states: 'If count exceeds 20, emit a WARNING log.' A malicious or compromised account could create unlimited keys, increasing the attack surface and bloating the api_keys table. The warning log is invisible to the user and requires someone to monitor logs. Alternative: enforce a hard cap (e.g., 50 active keys) and raise a typed exception. The anomaly log can remain as an additional signal below the hard cap.

### AKS-DES-002 — Race condition on name uniqueness check (Low)

**Status**: RESOLVED — Cross-agent duplicate of AKS-GAP-007 (2026-05-17)

### AKS-DES-003 — Audit event created even on idempotent no-op revocation (Low)

**Status**: RESOLVED — The spec correctly short-circuits before audit event creation on idempotent calls. No issue exists. (2026-05-17)

### AKS-DES-004 — revoke_all_user_keys creates N individual audit events without batching consideration (Low)

**Status**: RESOLVED — Individual audit events per key is the correct granularity for an audit trail. The practical upper bound (~20) makes performance concerns negligible. (2026-05-17)

### AKS-DES-005 — No user existence check in revoke_all_user_keys (Low)

**Status**: RESOLVED — Cross-agent duplicate of AKS-GAP-001 (2026-05-17)

---

## Security

### AKS-SEC-003 — revoke_key() lacks authorization check by design — relies on caller discipline (Medium)

**Category**: Authorization
**Status**: OPEN

The spec explicitly states ownership validation is NOT performed by the service — it's an 'endpoint-level concern'. This is a defense-in-depth weakness: if a new caller forgets the ownership check, any authenticated user could revoke any other user's API key.

### AKS-SEC-008 — No user active-status check during API key validation (Medium)

**Category**: Authentication
**Status**: OPEN

The `create_key()` operation checks that the user is active, but the API key validation flow in authentication.md does not explicitly verify `user.active = true` after loading the user in step 6. If a user is deactivated but key revocation fails partially, their existing API keys could remain usable.

### AKS-SEC-001 — No maximum API key limit per user enables resource exhaustion (Low)

**Status**: RESOLVED — Cross-agent duplicate of AKS-DES-001 (2026-05-17)

### AKS-SEC-002 — No rate limiting on key creation endpoint (Low)

**Category**: Insecure patterns
**Status**: OPEN

The POST /api/v1/api-keys endpoint has no rate limiting specified. Combined with the lack of a hard key limit, a compromised JWT session could rapidly create many keys.

### AKS-SEC-004 — create_key does not verify acting_user_id matches user_id for self-service (Low)

**Category**: Authorization
**Status**: OPEN

The `create_key()` service accepts `user_id` and `acting_user_id` as separate parameters but does not validate their relationship. Authorization enforcement is entirely dependent on the caller passing correct values.

### AKS-SEC-005 — API key creation restricted to JWT sessions — good anti-replication control (Low)

**Status**: RESOLVED — by design in authentication.md (AUTH_SESSION_REQUIRED check) (2026-05-17)

### AKS-SEC-006 — Key generation uses CSPRNG with sufficient entropy (Low)

**Status**: RESOLVED — adequate entropy per authentication.md security considerations (2026-05-17)

### AKS-SEC-007 — Plaintext key exposed only once at creation — good key visibility model (Low)

**Status**: RESOLVED — by design in authentication.md (Key Visibility) (2026-05-17)

### AKS-SEC-009 — Audit events created for all key lifecycle operations (Low)

**Status**: RESOLVED — comprehensive audit trail coverage (2026-05-17)

### AKS-SEC-010 — Idempotent revocation prevents information leakage about key state (Low)

**Status**: RESOLVED — by design (2026-05-17)

---

## API Conventions

### AKS-API-001 — Spec does not define API endpoints — no API convention violations applicable (Low)

**Status**: RESOLVED — spec is a service-layer specification; API endpoint conventions are enforced on the owning spec (authentication.md) (2026-05-17)
