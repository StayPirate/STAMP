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

**Status**: RESOLVED — Auto-resolved: finding no longer applicable after spec changes — spec now includes comprehensive fetch_single signaling convention, retry policy, and error categorization (2026-05-28)

### FEI-GAP-009 — FetcherConfig auto-creation race condition on first multi-worker startup (Medium)

**Status**: RESOLVED — Spec updated: added explicit idempotent creation requirement (INSERT ... ON CONFLICT DO NOTHING) to FetcherConfig section (2026-05-28)

### FEI-GAP-003 — FetcherRun records for runs that fail during creation (Low)

**Status**: RESOLVED — Spec updated: documented FetcherRun INSERT failure behavior with CRITICAL log and immediate task abort (2026-05-28)

### FEI-GAP-006 — Aggregation behavior for runs with status 'running' that are never resolved (Low)

**Status**: RESOLVED — Spec updated: documented orphaned run force-resolution during aggregation (2026-05-28)

### FEI-GAP-007 — No specification for metric counter overflow or reset between runs (Low)

**Status**: RESOLVED — Spec updated: documented metric counter reset at start of each run() (2026-05-28)

### FEI-GAP-018 — FetcherRun retrieval failure undocumented for API-trigger flow (Low)

**Category**: Error handling
**Status**: OPEN

When `run_id` is provided to `BaseFetcher.run()` (API trigger flow),
the method retrieves an existing `FetcherRun` record. The spec
documents only FetcherRun creation failure (lines 70-82). Retrieval
failure modes are undocumented:
- DB unreachable during retrieval (same handling as creation failure?)
- Record not found (deleted between API trigger and task execution)

---

## Coherence

_No findings._

---

## Design

### FEI-DES-001 — Custom settings schema is a bespoke validation framework (Medium)

**Status**: RESOLVED — Spec rewritten: replaced bespoke dictionary DSL with Pydantic BaseModel inner class for settings declaration, leveraging native validation, JSON Schema generation, and IDE support (2026-05-28)

### FEI-DES-002 — Concurrency control race window between API check and task execution (Medium)

**Status**: RESOLVED — Cross-agent duplicate of FEI-GAP-002 (2026-05-28)

### FEI-DES-003 — Weekly aggregation loses error diagnostic information permanently (Low)

**Status**: RESOLVED — Accepted risk: 90-day diagnostic data loss is a calculated trade-off for storage simplicity (2026-05-28)

### FEI-DES-004 — fetch_single invoked in parallel across all CVE fetchers without coordination (Low)

**Category**: Complexity
**Status**: OPEN (partially mitigated)

The 'On-demand Single-Item Fetch' section states the system 'invokes them in parallel when an on-demand fetch is needed.' If multiple fetchers write to the same models (CVE, CVECVSSAssessment, CVESource) concurrently for the same CVE-ID, there's potential for conflicting upserts. The spec doesn't specify how concurrent writes to the same CVE row are handled (e.g., last-write-wins, or serialized via row lock).

**Partial mitigation (2026-05-29)**: `cve-tracking.md` now uses `SET NX` for Redis pending keys, preventing duplicate task enqueue for the same CVE+source pair from concurrent triggers. This eliminates duplicate tasks per-source but does not address cross-source concurrent writes to the same CVE row, which is handled by `SELECT ... FOR UPDATE` in `cve-service.md`.

### FEI-DES-005 — Enabled check skips silently without any observability (Low)

**Status**: RESOLVED — Spec updated: added DEBUG log on disabled fetcher skip (2026-05-28)

---

## Security

### FEI-SEC-001 — Fetcher dashboard exposes error_message to unauthenticated users (Medium)

**Status**: RESOLVED — Accepted risk: public error_message visibility is a calculated trade-off for operational transparency (2026-05-28)

### FEI-SEC-002 — No rate limiting on manual fetcher trigger endpoint (Medium)

**Status**: RESOLVED — Accepted risk: manual trigger rate limiting is a calculated trade-off; concurrency control and capability restriction provide sufficient protection (2026-05-28)

### FEI-SEC-003 — TOCTOU in API-level concurrency check (Low)

**Status**: RESOLVED — Cross-agent duplicate of FEI-GAP-002 (2026-05-28)

### FEI-SEC-004 — timeout_seconds=0 disables stale run detection permanently (Low)

**Status**: RESOLVED — Spec updated: added warning in API response when timeout_seconds=0 (2026-05-28)

### FEI-SEC-005 — Custom settings validation lacks string length bounds (Low)

**Status**: RESOLVED — Resolved by FEI-DES-001: Pydantic Field(max_length=...) provides native string length bounds, eliminating the gap (2026-05-28)

---

## API Conventions

_No findings._
