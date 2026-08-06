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

**Status**: RESOLVED — Added explicit whitespace trimming rule to `create_key()` Validation section in api-key-service.md (2026-05-17)

### AKS-GAP-005 — expires_at race condition at creation boundary (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-17)

### AKS-GAP-006 — No operation for listing or reading keys (Low)

**Status**: RESOLVED — Added explicit scoping note to Purpose section clarifying that read-only operations are excluded from centralization (2026-05-17)

---

## Coherence

### AKS-COH-001 — Prefix column size contradicts example values across specs (Low)

**Status**: RESOLVED — Truncated all example prefix values from 13 characters to 12 characters across authentication.md and data-model.md to match the VARCHAR(12) column definition (2026-05-17)

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

**Status**: RESOLVED — Accepted risk: key creation requires JWT session authentication (API keys cannot self-replicate), anomaly log at 20 keys provides operational visibility, and rate limiting can be added as cross-cutting middleware if needed in the future (2026-05-17)

### AKS-SEC-004 — create_key does not verify acting_user_id matches user_id for self-service (Low)

**Status**: RESOLVED — Accepted risk: same architectural reasoning as AKS-SEC-003; acting_user_id/user_id relationship validation is an endpoint-level concern by design (2026-05-17)

### AKS-SEC-005 — API key creation restricted to JWT sessions — good anti-replication control (Low)

**Status**: RESOLVED — by design in api-key-management.md (AUTH_SESSION_REQUIRED check) (2026-05-17)

### AKS-SEC-006 — Key generation uses CSPRNG with sufficient entropy (Low)

**Status**: RESOLVED — adequate entropy per authentication.md security considerations (2026-05-17)

### AKS-SEC-007 — Plaintext key exposed only once at creation — good key visibility model (Low)

**Status**: RESOLVED — by design in api-key-management.md (Key Format and Visibility) (2026-05-17)

### AKS-SEC-009 — Audit events created for all key lifecycle operations (Low)

**Status**: RESOLVED — comprehensive audit trail coverage (2026-05-17)

### AKS-SEC-010 — Idempotent revocation prevents information leakage about key state (Low)

**Status**: RESOLVED — by design (2026-05-17)

---

## API Conventions

### AKS-API-001 — Spec does not define API endpoints — no API convention violations applicable (Low)

**Status**: RESOLVED — spec is a service-layer specification; API endpoint conventions are enforced on the owning spec (api-key-management.md) (2026-05-17)
