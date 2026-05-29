# Review: fetcher-operations

**Spec**: `docs/features/platform/fetcher-operations.md`
**Last reviewed**: 2026-05-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### FEO-GAP-01 — Trigger endpoint: Celery enqueue failure unspecified (Medium)

**Status**: RESOLVED — Enqueue failure handling documented: FetcherRun marked as failure + 503 CELERY_ENQUEUE_FAILED returned (2026-05-29)

### FEO-GAP-02 — Aggregation fetcher: partial failure semantics (Medium)

**Category**: Transactional integrity
**Status**: OPEN

The algorithm says group records by fetcher_name and ISO week, create/update aggregate records, then delete original FetcherRun records. If step 5 succeeds for some groups but fails mid-execution, the spec doesn't say whether already-aggregated runs are deleted or whether the operation is transactional per-group.

### FEO-GAP-03 — Trigger + PATCH disable race condition (Medium)

**Category**: Concurrency
**Status**: OPEN

If an admin triggers a run (passes the enabled check) and concurrently another admin disables the fetcher before the Celery task picks up, the task would be skipped per BaseFetcher.run() but the API already returned 202 with a run_id and a FetcherRun record was created that would remain running forever.

### FEO-GAP-04 — Aggregation fetcher and concurrent reads (Medium)

**Category**: Concurrency
**Status**: OPEN

If a fetcher finished 91 days ago and the aggregation task deletes old records while the timeline endpoint is querying them, no locking or snapshot isolation is specified.

### FEO-GAP-05 — FetcherAuditEvent retention unspecified (Medium)

**Category**: Data lifecycle
**Status**: OPEN

The spec defines aggregation for FetcherRun records but says nothing about retention/cleanup of FetcherAuditEvent records. These grow unboundedly. The disabled_periods derivation queries all historical audit events. No archival or pruning strategy is specified.

### FEO-GAP-06 — CLI fetcher run: unhandled exception exit code ambiguity (Low)

**Category**: Error handling
**Status**: OPEN

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

**Category**: Resilience
**Status**: OPEN

If Redis is flushed or Beat hasn't started, next_run_at is unavailable. No fallback behavior defined.

### FEO-DES-03 — Race between API trigger and scheduled run (Low)

**Status**: RESOLVED — Cross-agent duplicate of FEO-GAP-03 (2026-05-28)

---

## Security

### FEO-SEC-01 — Public error messages may leak internal details (Medium)

**Category**: Information disclosure
**Status**: OPEN

error_message is shown publicly for failed runs. Messages from fetchers interacting with internal services could reveal hostnames, connection strings, or service topology.

### FEO-SEC-02 — Timeline from_date has no lower bound (Medium)

**Category**: Denial of service
**Status**: OPEN

A request with from_date=1970-01-01 forces the server to scan all weekly aggregates. Combined with no auth, this is a DoS vector.

### FEO-SEC-03 — fetcher_name path parameter lacks format validation (Low)

**Category**: Input validation
**Status**: OPEN

No format constraints on fetcher_name path parameter.

### FEO-SEC-04 — CLI bypasses enabled check without audit event (Low)

**Category**: Audit completeness
**Status**: OPEN

CLI runs of disabled fetchers leave no actor attribution.

---

## API Conventions

### FEO-API-01 — RBAC Endpoint Permission Map anchor mismatches (Medium)

**Category**: Cross-reference integrity
**Status**: OPEN

Four admin-only endpoints have broken anchor links in rbac.md (trigger, get-config, update-config, audit-log). Links use `-admin-only` suffix that doesn't match actual headings.

### FEO-API-02 — Authorization declaration format inconsistency (Medium)

**Category**: Convention compliance
**Status**: OPEN

Uses "Capability: manage_fetchers" and "Permissions: publicly accessible" instead of the standardized format "Access: Public" or "Capability: <name>" from api-spec.md.

### FEO-API-03 — Non-standard warning field in PATCH response envelope (Low)

**Category**: Response envelope
**Status**: OPEN

Update Fetcher Config adds a `warning` field alongside `data`, introducing a new pattern not in api-spec.md.
