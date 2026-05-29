# Review: fetcher-operations

**Spec**: `docs/features/platform/fetcher-operations.md`
**Last reviewed**: 2026-05-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### FEO-GAP-01 — Trigger endpoint: Celery enqueue failure unspecified (Medium)

**Status**: RESOLVED — Enqueue failure handling documented: FetcherRun marked as failure + 503 CELERY_ENQUEUE_FAILED returned (2026-05-29)

### FEO-GAP-02 — Aggregation fetcher: partial failure semantics (Medium)

**Status**: RESOLVED — Per-group transactional semantics added to aggregation algorithm in fetcher-operations.md (2026-05-29)

### FEO-GAP-03 — Trigger + PATCH disable race condition (Medium)

**Status**: RESOLVED — Race condition documented: BaseFetcher.run() now cleans up pre-existing FetcherRun when fetcher disabled between trigger and execution, in fetcher-infrastructure.md and fetcher-operations.md (2026-05-29)

### FEO-GAP-04 — Aggregation fetcher and concurrent reads (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-29)

### FEO-GAP-05 — FetcherAuditEvent retention unspecified (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-29)

### FEO-GAP-06 — CLI fetcher run: unhandled exception exit code ambiguity (Low)

**Category**: Error handling
**Status**: RESOLVED — Moot: CLI `sentinel fetcher run` command removed from specification (2026-05-29)

Exit code 2 for unhandled exception, but unclear if the FetcherRun record is updated to failure before exiting.

### FEO-GAP-07 — Timeline: range spanning the 90-day boundary (Low)

**Status**: RESOLVED — Added query strategy paragraph clarifying retention_days split point and transition week precedence rule (2026-05-29)

### FEO-GAP-08 — Timeline: from_date after to_date (Low)

**Status**: RESOLVED — Added DATE_RANGE_INVERTED global rule to api-spec.md Date Range Interpretation section; added error to timeline endpoint error table (2026-05-29)

### FEO-GAP-09 — Disabled periods: no audit events exist (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-29)

### FEO-GAP-10 — last_run for deregistered fetcher after aggregation (Low)

**Status**: RESOLVED — Added documentation note to last_run field definition clarifying null behavior after aggregation for deregistered fetchers (2026-05-29)

### FEO-GAP-11 — rate_limit field inconsistency (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-29)

### FEO-GAP-12 — IBS consumer disconnected status: no example response (Low)

**Status**: RESOLVED — Added disconnected example response; replaced bullet-point field descriptions with comprehensive per-status matrix table covering all four states (2026-05-29)

---

## Coherence

### FEO-COH-01 — Missing "View error details" in RBAC Permission Matrix (Low)

**Status**: RESOLVED — Added "View error details" as separate entry in rbac.md manage_fetchers capability description and Capability Actions table (2026-05-29)

### FEO-COH-02 — Anchor mismatch in RBAC endpoint permission map links (Low)

**Status**: RESOLVED — Cross-agent duplicate of FEO-API-01 (2026-05-28)

---

## Design

### FEO-DES-01 — Aggregation deletes data without a safety net (Medium)

**Status**: RESOLVED — Cross-agent duplicate of FEO-GAP-02 (2026-05-28)

### FEO-DES-02 — next_run_at calculation from Celery Beat state is fragile (Medium)

**Status**: RESOLVED — Beat state unavailability added as explicit null condition for next_run_at in fetcher-operations.md (2026-05-29)

### FEO-DES-03 — Race between API trigger and scheduled run (Low)

**Status**: RESOLVED — Cross-agent duplicate of FEO-GAP-03 (2026-05-28)

---

## Security

### FEO-SEC-01 — Public error messages may leak internal details (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-29)

### FEO-SEC-02 — Timeline from_date has no lower bound (Medium)

**Status**: RESOLVED — Max 365-day range constraint and DATE_RANGE_TOO_WIDE error added to timeline endpoint in fetcher-operations.md (2026-05-29)

### FEO-SEC-03 — fetcher_name path parameter lacks format validation (Low)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-29)

### FEO-SEC-04 — CLI bypasses enabled check without audit event (Low)

**Category**: Audit completeness
**Status**: RESOLVED — Moot: CLI `sentinel fetcher run` command removed from specification (2026-05-29)

CLI runs of disabled fetchers leave no actor attribution.

---

## API Conventions

### FEO-API-01 — RBAC Endpoint Permission Map anchor mismatches (Medium)

**Status**: RESOLVED — Removed -admin-only suffix from 4 anchor links in rbac.md Endpoint Permission Map (2026-05-29)

### FEO-API-02 — Authorization declaration format inconsistency (Medium)

**Status**: RESOLVED — Fixed: standardized all authorization declarations across 12 feature specs to use the prescribed format from api-spec.md (2026-05-29)

### FEO-API-03 — Non-standard warning field in PATCH response envelope (Low)

**Status**: RESOLVED — Removed non-standard warning field from PATCH response envelope; moved advisory info into timeout_seconds field description to maintain envelope consistency (2026-05-29)
