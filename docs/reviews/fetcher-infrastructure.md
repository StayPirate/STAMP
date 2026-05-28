# Review: fetcher-infrastructure

**Spec**: `docs/features/platform/fetcher-infrastructure.md`
**Last reviewed**: 2026-05-28
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions

---

## Gap Analysis

### FEI-GAP-001 — No specification for partial status transition logic (Medium)

**Status**: RESOLVED — Added explicit status determination precedence rule in BaseFetcher Base Class section and clarifying note in FetcherRunStatus enum (2026-05-28)

### FEI-GAP-002 — Race condition window between API concurrency check and task execution (Medium)

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes (2026-05-28)

### FEI-GAP-004 — No behavior specified for duplicate fetcher names at registration (Medium)

**Status**: RESOLVED — Added explicit duplicate name enforcement rule at import time (2026-05-28)

### FEI-GAP-005 — No specification for what happens when FetcherConfig.custom_settings contains invalid values (Medium)

**Status**: RESOLVED — Added fail-fast runtime validation for stored settings against schema (2026-05-28)

### FEI-GAP-008 — fetch_single error handling not specified for non-FetcherError exceptions (Medium)

**Category**: Missing Error Path
**Status**: OPEN

The spec states 'The fetch_single method does NOT create a FetcherRun record. It is a sub-operation invoked as a standalone Celery task'. However, the spec does not specify what happens when fetch_single raises an exception. Since there is no FetcherRun record and no BaseFetcher.run() wrapper, the error handling (sanitization, logging, retry) for fetch_single failures is entirely unspecified. The caller (on-demand fetch system) has no defined contract for handling these failures.

### FEI-GAP-009 — FetcherConfig auto-creation race condition on first multi-worker startup (Medium)

**Category**: Concurrency
**Status**: OPEN

The spec states 'A record is created automatically when a fetcher is first registered (on worker startup) if one does not already exist'. If multiple Celery workers start simultaneously (common in Kubernetes), they could all attempt to INSERT the same FetcherConfig row concurrently. The spec does not specify whether this uses INSERT ... ON CONFLICT or another idempotent mechanism to prevent unique constraint violations.

### FEI-GAP-003 — FetcherRun records for runs that fail during creation (Low)

**Category**: Missing Error Path
**Status**: OPEN

The spec states the run() method creates 'a FetcherRun record with status running' before calling execute(). If the database INSERT for the FetcherRun record itself fails (e.g., database connection error), the spec does not describe the behavior. The fetcher would fail before execute() is called, with no FetcherRun record to mark as failure. This is an implicit boundary but could cause observability gaps (a fetcher attempted to run but left no trace).

### FEI-GAP-006 — Aggregation behavior for runs with status 'running' that are never resolved (Low)

**Category**: Data Lifecycle
**Status**: OPEN

The spec defines stale run detection for concurrency purposes, but the aggregation task (which processes records older than retention_days) does not specify what happens if a FetcherRun record with status='running' and finished_at=NULL is older than the retention window. If a stale run was never detected (e.g., timeout_seconds=0), it would remain with status 'running' indefinitely. The aggregation task would encounter it — should it be aggregated as-is, skipped, or force-marked as failure before aggregation?

### FEI-GAP-007 — No specification for metric counter overflow or reset between runs (Low)

**Category**: Boundary Condition
**Status**: OPEN

The spec provides metric helpers: 'self.record_created(count=1) — increment items_created'. It does not specify whether these counters are reset at the start of each run() invocation or persist across calls. It's implicitly per-run (since they map to FetcherRun columns), but if a fetcher implementation stores a reference to the BaseFetcher instance across runs (singleton pattern), counters from a previous run could leak into the next.

---

## Coherence

_No findings._

---

## Design

### FEI-DES-001 — Custom settings schema is a bespoke validation framework (Medium)

**Category**: Over-engineering
**Status**: OPEN

The 'Custom Settings Schema' section defines a custom type system with validation rules, min/max bounds, choices, import-time validation, and a runtime resolution cascade. This is essentially a mini-DSL for configuration that could be handled by Pydantic models (which the project already uses extensively). Each fetcher could declare a Pydantic model for its settings, gaining richer validation, nested types if ever needed, and IDE support for free. The custom schema format requires a custom validator, custom serialization for the admin UI, and custom documentation — all of which Pydantic already provides.

### FEI-DES-002 — Concurrency control race window between API check and task execution (Medium)

**Status**: RESOLVED — Cross-agent duplicate of FEI-GAP-002 (2026-05-28)

### FEI-DES-003 — Weekly aggregation loses error diagnostic information permanently (Low)

**Category**: Missing Alternative
**Status**: OPEN

The 'Data Retention' section specifies that individual FetcherRun records are deleted after 90 days and replaced by weekly aggregates. The aggregates only store counts and duration statistics — all error_message, error_detail, and error_traceback data is permanently lost. For fetchers with recurring intermittent failures, operators cannot investigate patterns older than 90 days. An alternative would be to retain the last N failure records per fetcher indefinitely, adding minimal storage cost but preserving diagnostic value.

### FEI-DES-004 — fetch_single invoked in parallel across all CVE fetchers without coordination (Low)

**Category**: Complexity
**Status**: OPEN

The 'On-demand Single-Item Fetch' section states the system 'invokes them in parallel when an on-demand fetch is needed.' If multiple fetchers write to the same models (CVE, CVECVSSAssessment, CVESource) concurrently for the same CVE-ID, there's potential for conflicting upserts. The spec doesn't specify how concurrent writes to the same CVE row are handled (e.g., last-write-wins, or serialized via row lock).

### FEI-DES-005 — Enabled check skips silently without any observability (Low)

**Category**: Maintainability
**Status**: OPEN

BaseFetcher Base Class: 'If `enabled` is `false`, the run is skipped (no `FetcherRun` record is created, the task returns immediately).' This means there's no way to confirm that a disabled fetcher's scheduled task is actually firing and being skipped vs. the Beat schedule being broken entirely. A log line or metric is warranted here but the spec doesn't mention one.

---

## Security

### FEI-SEC-001 — Fetcher dashboard exposes error_message to unauthenticated users (Medium)

**Category**: Information Disclosure
**Status**: OPEN

The spec explicitly states that error_message is visible to 'all users (including unauthenticated callers via the fetcher dashboard)'. While the sanitization framework is well-designed, any developer mistake in a concrete fetcher (forgetting to wrap exceptions in FetcherError) would expose infrastructure details publicly. The generic fallback mitigates this, but even sanitized messages like 'IBS returned HTTP 403' reveal which external services Sentinel integrates with. The fetcher-operations spec confirms 'All users have visibility into fetcher health and performance (no authentication required)'.

### FEI-SEC-002 — No rate limiting on manual fetcher trigger endpoint (Medium)

**Category**: Missing Rate Limiting
**Status**: OPEN

The concurrency control (only one run at a time per fetcher) prevents parallel execution, but the spec does not mention rate limiting on the manual trigger endpoint. An admin could repeatedly trigger fetchers in rapid succession (trigger -> wait for completion -> trigger again), causing excessive load on external services. The per-fetcher rate_limit in FetcherConfig controls execution pacing within a run, not trigger frequency. While limited to manage_fetchers capability holders, a compromised admin account or rogue automation could abuse this.

### FEI-SEC-003 — TOCTOU in API-level concurrency check (Low)

**Status**: RESOLVED — Cross-agent duplicate of FEI-GAP-002 (2026-05-28)

### FEI-SEC-004 — timeout_seconds=0 disables stale run detection permanently (Low)

**Category**: Insecure Default
**Status**: OPEN

Setting timeout_seconds to 0 disables both the Celery soft time limit AND stale run detection. A fetcher with timeout_seconds=0 that gets stuck will block all future executions of that fetcher indefinitely since the stale run can never be resolved automatically. While this is documented behavior and requires admin action to set, there's no warning about the operational risk, and no mechanism to alert operators when a run has been in 'running' status for an unreasonable time with stale detection disabled.

### FEI-SEC-005 — Custom settings validation lacks string length bounds (Low)

**Category**: Input Validation
**Status**: OPEN

The custom_settings_schema supports type 'str' with optional 'choices' constraint, but there is no 'max_length' property defined for string settings. An admin with manage_fetchers capability could store arbitrarily large strings in the JSONB column. While this requires admin access, unbounded strings in JSONB could lead to unexpected memory usage or query performance issues.

---

## API Conventions

_No findings._
