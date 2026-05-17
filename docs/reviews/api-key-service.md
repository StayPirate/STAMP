# Review: api-key-service

**Spec**: `docs/features/identity/api-key-service.md`
**Last reviewed**: 2026-05-17
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### AKS-GAP-001 — revoke_all_user_keys does not check user existence (Medium)

**Status**: RESOLVED — Added Preconditions section and user existence validation step to `revoke_all_user_keys()` in api-key-service.md (2026-05-17)

### AKS-GAP-004 — Idempotent revoke skips audit event creation (Medium)

**Status**: RESOLVED — Added idempotent no-op rule to audit-trail-infrastructure.md and cross-reference in api-key-service.md revoke_key() idempotency clause (2026-05-17)

### AKS-GAP-007 — Concurrent creation of keys with same name (Medium)

**Status**: RESOLVED — Fixed: added IntegrityError-to-ApiKeyNameConflictError translation for concurrent name collisions (2026-05-17)

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

**Status**: RESOLVED — Accepted risk: warning log at 20 keys is sufficient for current scale; hard cap deferred as premature given low abuse likelihood with authenticated-only access (2026-05-17)

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

**Status**: RESOLVED — Accepted risk: authorization is endpoint-level by design; moving ownership checks into the service would set an architectural precedent inconsistent with the project's layering convention where all role/resource authorization is handled via FastAPI Depends() (2026-05-17)

### AKS-SEC-008 — No user active-status check during API key validation (Medium)

**Status**: RESOLVED — Auto-resolved: the user.active check already exists in get_current_user step 5 (authentication.md), which executes after both JWT and API key validation sub-flows converge; no API key path bypasses this gate (2026-05-17)

### AKS-SEC-001 — No maximum API key limit per user enables resource exhaustion (Low)

**Status**: RESOLVED — Cross-agent duplicate of AKS-DES-001 (2026-05-17)

### AKS-SEC-002 — No rate limiting on key creation endpoint (Low)

**Category**: Insecure patterns
**Status**: OPEN

The POST /api/v1/api-keys endpoint has no rate limiting specified. Combined with the lack of a hard key limit, a compromised JWT session could rapidly create many keys.

### AKS-SEC-004 — create_key does not verify acting_user_id matches user_id for self-service (Low)

**Status**: RESOLVED — Accepted risk: same architectural reasoning as AKS-SEC-003; acting_user_id/user_id relationship validation is an endpoint-level concern by design (2026-05-17)

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
