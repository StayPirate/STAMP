# Review: configuration

**Spec**: `docs/configuration.md`
**Last reviewed**: 2026-07-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### CFG-GAP-01 — Notes for Operators #3 overstates startup validation uniformity (Medium)

**Status**: RESOLVED — LOGIN_MAX_ATTEMPTS and LOGIN_LOCKOUT_MINUTES changed to fail-fast, consistent with JWT_EXPIRY_HOURS; Note #3 now accurate (2026-07-28)

### CFG-GAP-02 — Empty string vs. unset not distinguished for SSO settings (Medium)

**Status**: RESOLVED — Cross-cutting convention added to conventions.md for optional string variables (empty = unset); all 7 ambiguous variables clarified across sso-authentication.md, ibs-integration.md, and configuration.md (2026-07-28)

---

## Coherence

### CFG-COH-01 — SMELT_API_URL / AIMAAS_API_URL circular authority (Medium)

**Status**: RESOLVED — Configuration section added to product-catalog.md with authoritative variable definitions; circular reference removed (2026-07-28)

---

## Design

### CFG-DES-01 — Inconsistent validation behavior for login rate-limiting settings (Medium)

**Status**: RESOLVED — Cross-agent duplicate of CFG-GAP-01 (2026-07-28)

---

## Security

### CFG-SEC-01 — CORS allow_methods and allow_headers are wildcard (Medium)

**Status**: RESOLVED — CORS methods and headers documented as application constants in configuration.md; implementation aligned in main.py (2026-07-28)

### CFG-SEC-02 — IBS_RABBITMQ_URL default embeds credentials in spec (Medium)

**Status**: RESOLVED — Well-known infrastructure defaults annotation added to ibs-rabbitmq-integration.md and configuration.md; credentials retained as not sensitive (2026-07-28)

---

## API Conventions

