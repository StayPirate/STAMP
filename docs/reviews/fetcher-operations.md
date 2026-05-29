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

**Category**: Data consistency
**Status**: OPEN

When from_date is within 90 days and to_date is now, runs in the 85-90 day window AND aggregates for the same week could produce overlapping data points.

### FEO-GAP-08 — Timeline: from_date after to_date (Low)

**Category**: Input validation
**Status**: OPEN

No behavior specified when from_date > to_date.

### FEO-GAP-09 — Disabled periods: no audit events exist (Low)

**Category**: Edge case
**Status**: OPEN

Doesn't specify response when no audit events of type disabled/enabled exist (new fetcher never disabled).

### FEO-GAP-10 — last_run for deregistered fetcher after aggregation (Low)

**Category**: Data consistency
**Status**: OPEN

After aggregation deletes old FetcherRun records, a deregistered fetcher whose last run was >90 days ago would show last_run: null, which is misleading.

### FEO-GAP-11 — rate_limit field inconsistency (Low)

**Category**: API consistency
**Status**: OPEN

rate_limit is in Get Config response but not in List Fetchers response or CLI list output.

### FEO-GAP-12 — IBS consumer disconnected status: no example response (Low)

**Category**: Documentation completeness
**Status**: OPEN

Only connected, reconnecting, and unreachable have examples. Disconnected field values not shown.

---

## Coherence

### FEO-COH-01 — Missing "View error details" in RBAC Permission Matrix (Low)

**Category**: Cross-spec consistency
**Status**: OPEN

fetcher-operations.md lists "View error details | manage_fetchers" but rbac.md only lists "View error tracebacks" without a separate entry for error details.

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

**Category**: Input validation
**Status**: OPEN

No format constraints on fetcher_name path parameter.

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

**Category**: Response envelope
**Status**: OPEN

Update Fetcher Config adds a `warning` field alongside `data`, introducing a new pattern not in api-spec.md.
