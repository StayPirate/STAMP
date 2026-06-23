# EPSS Fetcher — Work Plan

**Status**: Draft — work-in-progress across multiple sessions
**Target**: `docs/features/tickets/cve-sync-epss.md` (replace current placeholder)
**Last updated**: 2026-06-23 (Session 1h — reviewer feedback incorporated)

## 1. Overview

This document tracks the design work required to complete the EPSS fetcher
specification. The current spec (`docs/features/tickets/cve-sync-epss.md`)
is a placeholder with all key sections marked TBD. The goal is to produce
a complete, unambiguous fetcher specification that an implementer can follow
without making autonomous design decisions.

## 2. Current State

### What exists

| Artifact | Location | Status |
|----------|----------|--------|
| Spec file (placeholder) | `docs/features/tickets/cve-sync-epss.md` | TBD sections only |
| Data model (`CVEEPSSScore`) | `docs/data-model.md` (line 708) | Complete |
| Pydantic schema (`EPSSEntry`) | `docs/features/tickets/cve-service.md` (line 1091) | Complete |
| `CVEIngestPayload.epss_score` field | `docs/features/tickets/cve-service.md` (line 1019) | Complete |
| `cve_source_type = "epss"` | `docs/features/platform/fetcher-infrastructure.md` (line 255) | Registered |
| Fetcher Registry entry | `docs/data-sources.md` (line 971) | Incomplete (TBD fields) |
| Source description | `docs/data-sources.md` (lines 146-162) | Complete |
| Child table deduplication | `cve-service.md` (line 522) | ON CONFLICT DO UPDATE on `cve_id` |

### What is missing in the spec

- Algorithm (periodic + fetch_single)
- Schedule
- Scope
- Error Handling
- Metrics
- Custom Settings
- Field Mapping
- Explicitly Ignored Fields
- Behavioral Notes (data lifecycle, re-invocation, first-run)
- Class Structure
- `fetch_single()` method definition
- `source_reference_url_pattern`
- First-run behavior declaration
- Statefulness / cursor declaration
- CVE existence precondition for `fetch_single()`

## 3. Preconcept: KEV ≈ EPSS (ERRONEOUS)

### Problem

The specs erroneously classify EPSS as a "catalog-based fetcher" similar
to CISA KEV — assuming EPSS only provides a monolithic data dump with no
per-CVE API. This assumption is false.

### EPSS API Capabilities (verified 2026-06-21)

- **Single CVE query**: `?cve=CVE-2022-27225`
- **Batch query**: `?cve=CVE-1,CVE-2,...` (max 2000 chars parameter ≈ ~130 CVEs)
- **Full catalog paginated**: `offset` + `limit` (max 10,000 per page)
- **Date filtering**: `?date=YYYY-MM-DD` (historical since 2021-04-14)
- **Score filtering**: `?epss-gt=X`, `?percentile-gt=X`
- **Bulk CSV**: `https://epss.empiricalsecurity.com/epss_scores-YYYY-MM-DD.csv.gz`
- **Rate limit**: 1000 requests/minute (public, unauthenticated)
- **No authentication required**
- **Response format**: JSON with envelope (`status`, `status-code`, `total`,
  `offset`, `limit`, `data[]`)
- **Per-CVE data fields**: `cve` (string), `epss` (string, decimal),
  `percentile` (string, decimal), `date` (string, YYYY-MM-DD)
- **Coverage**: ~341,600 CVEs scored (vs ~359,500 total in NVD) — ~95%
  coverage. Unscored CVEs are typically very recent (< 24h) or REJECTED.

### EPSS Publication Schedule (verified 2026-06-21)

EPSS publishes all scores in a **single daily batch** (not incrementally).
The publication time is extremely stable:

**Every day at 13:31 UTC (±10 seconds)**, including weekends and holidays.

Verified across two independent sources:

| Source | Method | Period verified | Observed time |
|--------|--------|-----------------|---------------|
| `epss.empiricalsecurity.com` | HTTP `Last-Modified` header on daily CSV files | Jun 2025 — Jun 2026 | 13:30–13:31 UTC |
| `github.com/empiricalsec/epss_scores` | Git commit timestamps (automated daily) | Feb 2026 — Jun 2026 | 13:31 UTC |

Sample HTTP headers (all 13:31 UTC):
```
2026-06-20: last-modified: Sat, 20 Jun 2026 13:31:19 GMT
2026-06-19: last-modified: Fri, 19 Jun 2026 13:31:24 GMT
2026-06-18: last-modified: Thu, 18 Jun 2026 13:31:23 GMT
2026-01-15: last-modified: Thu, 15 Jan 2026 13:31:06 GMT
2025-06-01: last-modified: Sun, 01 Jun 2025 13:30:55 GMT
```

The GitHub repo (`empiricalsec/epss_scores`) is the official repository for
historical EPSS data, created 2026-02-02 with a backfill of all scores from
2021-04-14 onward. Daily commits are automated.

EPSS model versions:
- v1: 2021-04-14 (initial)
- v2 (v2022.01.01): 2022-02-04
- v3 (v2023.03.01): 2023-03-07
- v4 (v2025.03.14): 2025-03-17 (current)

### Correct Classification

EPSS is architecturally similar to Red Hat (`sync_redhat_cves`), NOT to
CISA KEV. Specifically:

- It CAN implement `fetch_single()` for on-demand single-CVE queries
- It CAN participate in catch-up
- Its periodic `execute()` uses per-CVE requests (same pattern as Red Hat)

### Locations requiring correction

(Line numbers verified 2026-06-23, Session 1f)

| File | Line(s) | Current text | Required change |
|------|---------|--------------|-----------------|
| `fetcher-infrastructure.md` | 341 | "Catalog-based fetchers (KEV, EPSS) that have no per-CVE API set `supports_fetch_single = False`" | Remove EPSS from this parenthetical; only KEV is catalog-based |
| `fetcher-infrastructure.md` | 557 | "fetchers like KEV and EPSS) are excluded" | Remove EPSS |
| `fetcher-infrastructure.md` | 891 | "sync_epss_scores \| Syncs all EPSS scores (sets participates_in_catch_up = False)" | Move EPSS to the "participate in catch-up" table (lines 866–879) |
| `fetcher-infrastructure.md` | 893–903 | Narrative grouping KEV and EPSS with same rationale (monolithic catalog, no per-CVE API) | Separate: KEV remains catalog-based, EPSS moves to per-CVE API category. Rewrite note to apply to KEV only |
| `fetcher-infrastructure.md` | 1547 | "Catalog-based fetchers (KEV, EPSS)" in `supports_fetch_single` description | Remove EPSS from the example |
| `cve-tracking.md` | 465 | "(KEV, EPSS) that set `supports_fetch_single = False`" | Remove EPSS |
| `cve-service.md` | 770 | '"epss") that set `supports_fetch_single = False` are excluded' | Remove EPSS |
| `cve-service.md` | 1276 | Caller table: `sync_epss_scores` → only `upsert_cve()` | Add `(via fetch_single())` annotation, consistent with RedHat/OSV rows |

## 4. Design Decisions (Determined)

These decisions are already determined by the API capabilities and project
conventions:

| Decision | Value | Rationale |
|----------|-------|-----------|
| `supports_fetch_single` | `True` | EPSS API supports `?cve=CVE-XXX` |
| `participates_in_catch_up` | `True` | `fetch_single()` provides per-ticket recovery |
| Base class | `BaseCVEFetcher` | CVE enrichment fetcher |
| Role | Enrichment-only | Does not create CVE records |
| Data lifecycle | Overwrite (1:1 snapshot) | `ON CONFLICT DO UPDATE` on `cve_id` UNIQUE |
| Scope (periodic run) | CVEs with active tickets | Consistent with `data-model.md` lifecycle note |
| `record_created` | Never used (N/A) | Enrichment-only — never creates CVE records |
| Schedule | Daily at 14:00 UTC (`0 14 * * *`) | EPSS publishes at 13:31 UTC; 29-min margin (see OP-1) |
| Auth | None | Public API |
| `assessed_at` field | Persist API `date` field | Already modeled as `CVEEPSSScore.assessed_at` (DATE, NOT NULL). Enables staleness detection in UI and allows the fetcher to detect stale API responses |
| First-run behavior | No special behavior (RedHat pattern) | Stateless fetcher — iterates over all CVEs with active tickets. If no active tickets exist, the run completes immediately with zero records |
| Cursor | None (stateless) | Re-processes the full in-scope set on each run. No cursor or incremental state to maintain between runs |
| CVE existence precondition | Guaranteed by caller flow | In `execute()`: scope is CVEs with active tickets (already in DB). In `fetch_single()`: the `ensure_cve_exists()` → `trigger_on_demand_fetch()` flow guarantees the CVE record exists before `fetch_single()` is called. The fetcher never creates CVE records |
| Transaction ownership | `upsert_cve()` is a pure service function — does NOT commit/rollback | Aligns with `cve-service.md` Transaction Ownership section and the `ticket_mutations`, `package_service`, `user_service` pattern |
| Per-CVE commit | `commit_and_dispatch(session, post_ingest)` after each `fetch_single()` | Pattern B from Session Lifecycle (`fetcher-infrastructure.md`). `fetch_single()` returns `None` (enrichment-only → `build_post_ingest_tasks()` returns `None`) → no Phase 2 dispatch |
| `fetch_single()` return type | `PostIngestTasks \| None` (always `None` for EPSS) | EPSS payloads contain only `epss_score` — no CPE, no affected versions, no resolved packages. `build_post_ingest_tasks()` returns `None` |
| `record_source_status` in `execute()` | Not used explicitly — rollback is sufficient | Architecture decision (commit 305bab9): rollback discards the `CVESource` "success" written by `upsert_cve()`, preserving previous state. No explicit failure write needed in batch path |
| Algorithm | Pattern B — `execute()` delegates to `fetch_single()` | `execute()` iterates CVE-IDs with active tickets, calls `self.fetch_single(cve_id, session)` per CVE, then `commit_and_dispatch(session, post_ingest)`. Core logic lives in `fetch_single()` only (DRY). Identical to Red Hat pattern |
| `default_request_delay` | `0.2` | 0.2s → max 300 req/min = 30% of EPSS rate limit (1000 req/min). Same value as OSV. At ~200-300 active tickets, runtime ~40-60s |
| Consecutive failure abort | 3 consecutive infra failures → abort with `FetcherError` | Counter resets after any success or clean skip (`CVENotInSource`). Only uninterrupted sequences of infrastructure failures (HTTP 5xx post-transport-retry, network timeout, DNS error) count. Identical to Red Hat |
| Response validation error | `record_failed()` + continue | If EPSS API returns values that fail `EPSSEntry` Pydantic validation (unparseable float, score outside [0.0, 1.0], malformed date), treat as per-CVE error: `record_failed()`, continue to next CVE |
| EPSS model version change | Informational limitation — no detection logic | EPSS model recalibrations (v4→v5, ~every 2 years) cause score discontinuities. No `model_version` field in API response (verified). `assessed_at` provides indirect signal. No automated detection — changes are announced publicly. Documented as a known limitation |
| HTTP client configuration | Shared defaults (no `http_client_options`) | Resolved by infrastructure (commit 6691351) — 30s read timeout, transport-level retry, automatic User-Agent. No per-fetcher override needed |
| `fetch_single()` empty response | Raise `CVENotInSource` | API returns HTTP 200 + `data: []` for unscored CVEs (verified). Same semantic as Red Hat (404) and NVD (empty). Orchestrator records `status = missing` |
| `execute()` empty response | Caught as `CVENotInSource` — silent skip | `fetch_single()` raises `CVENotInSource` when API returns `data: []`. `execute()` catches it, rolls back (defensive), resets failure counter. No error, no metric, no log |
| Score change tracking | None (simple overwrite) | No pre-read, no diff detection. Individual scores may remain stable for days; `assessed_at` update makes upsert mandatory regardless |
| `record_updated` semantics | Throughput (fire on every upsert), called inside `fetch_single()` | Documented deviation from "internal diff detection" guideline. Upsert is unconditional (for `assessed_at`), so pre-read adds overhead without avoiding writes. Placed inside `fetch_single()` per Pattern B metric placement convention |
| Staleness validation | Log-and-proceed (WARNING if `date < today - 1d`) | Check once per `execute()` (first response). `date` is batch-level (same for all CVEs). No abort — stale data > no data. `assessed_at` enables UI staleness indicator |

## 5. Open Points

### OP-1: Schedule — exact cron time — RESOLVED

EPSS data is published daily at **13:31 UTC** (±10 seconds), verified
across 12+ months of HTTP headers and GitHub commit timestamps.

- Other daily fetchers: NVD at 01:00, RedHat at 03:00, OSV at 05:00,
  LDAP at 04:00
- EPSS publication: **13:31 UTC** (verified, not estimated)
- The fetcher must run **after** 13:31 UTC

**Resolution**: daily at 14:00 UTC (`0 14 * * *`) — 29 minutes after
the observed publication time. This provides margin for minor delays
while ensuring the data is available. No conflict with other fetchers
(all run in the 01:00–05:00 UTC window).

**DST verification** (2026-06-21): confirmed that EPSS publication uses
a fixed UTC schedule — it does NOT shift with US daylight saving time.
Verified across both DST transitions:
- Spring forward 2026 (March 8): 13:31 UTC before and after
- Fall back 2025 (November 2): 13:31 UTC before and after

The only instability observed was during the EPSS v4 launch (March 17 –
April 4, 2025), after which the 13:31 UTC pattern stabilized.

**Timezone enforcement** (cross-cutting fix applied in this session):
to ensure the `0 14 * * *` cron is always interpreted as UTC regardless
of the deployment environment, the following specs were updated:
- `docs/configuration.md` — new "Celery Worker Configuration" section
  with `CELERY_TIMEZONE` + `CELERY_ENABLE_UTC` and startup validation
- `docs/deployment.md` — "Timezone Requirements" section
- `docs/conventions.md` — strengthened UTC enforcement wording
- `docs/features/platform/fetcher-infrastructure.md` — timezone note in
  Celery Integration section

### OP-2: Algorithm strategy for `execute()` — RESOLVED

**Resolution**: Pattern B — `execute()` delegates to `self.fetch_single()`.
The core per-CVE logic (HTTP call → parse → build payload → `upsert_cve()`)
lives exclusively in `fetch_single()`. `execute()` iterates over CVE-IDs
with active tickets and calls `self.fetch_single(cve_id, session)` per CVE.
With the expected volume (~200-300 active tickets) and 0.2s delay, this
completes in ~40-60 seconds. Batching is deferred as a future optimization
if volume ever justifies it (the EPSS response format is identical for
single and batch queries, making migration trivial).

Considered alternatives:

- **Batch API** (`?cve=CVE-1,CVE-2,...`): fewer HTTP requests but adds
  batch-splitting logic, URL length management, two-level error handling,
  and a separate code path from `fetch_single()`. Unnecessary at expected
  volume (~200-300 active tickets vs 1000 req/min rate limit)
- **Full CSV download** (~15MB, ~340k entries): single HTTP request but
  downloads vastly more data than needed, no `fetch_single()` reuse,
  different parsing format (CSV vs JSON)

Reference pseudocode (Pattern B):

```python
async def fetch_single(self, cve_id: str, session: AsyncSession) -> PostIngestTasks | None:
    """Fetch EPSS score for a single CVE.

    GET https://api.first.org/data/v1/epss?cve={cve_id}

    Returns None (enrichment-only — no Phase 2 dispatch needed).
    Raises CVENotInSource if EPSS has no score for the CVE.
    Metrics (record_updated) are called inside where upsert result
    is available.
    """
    response = await self._fetch_epss(cve_id)  # GET /epss?cve=CVE-XXX
    entry = self._extract_entry(response, cve_id)
    if entry is None:
        raise CVENotInSource(cve_id, self.cve_source_type)
    payload = self._build_payload(cve_id, entry)
    await upsert_cve(session, cve_id, self.cve_source_type, payload)
    self.record_updated()  # throughput metric (documented deviation)
    return None  # enrichment-only — no post-ingest tasks


async def execute(self, session: AsyncSession) -> None:
    """Periodic batch: iterate over CVEs with active tickets."""
    cve_ids = await self._get_active_ticket_cve_ids(session)
    staleness_checked = False
    consecutive_failures = 0
    for cve_id in cve_ids:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
            consecutive_failures = 0
            if not staleness_checked:
                # Staleness check on first success (date is batch-level)
                self._check_staleness_from_db(session, cve_id)
                staleness_checked = True
        except CVENotInSource:
            await session.rollback()  # defensive: ensure clean session state
            consecutive_failures = 0  # clean skip resets counter
        except ValidationError:
            await session.rollback()
            self.record_failed()
            consecutive_failures += 1
        except Exception:
            await session.rollback()
            self.record_failed()
            consecutive_failures += 1
        if consecutive_failures >= 3:
            raise FetcherError(
                "EPSS API unreachable — 3 consecutive failures"
            )
        await asyncio.sleep(self.config.request_delay)
```

### OP-3: `source_reference_url_pattern` — RESOLVED

**Resolution**: `source_reference_url_pattern = None`. FIRST.org does not
provide a human-readable per-CVE page for EPSS data (verified 2026-06-21
on `first.org/epss/` and `first.org/epss/data_stats`). The only per-CVE
access is the JSON API (`api.first.org/data/v1/epss?cve=CVE-XXX`), which
is not suitable as a `TicketReference` link. This fetcher does not create
`TicketReference` records.

### OP-4: `record_source_status` usage — RESOLVED (architecture)

The current caller table in `cve-service.md` shows `sync_epss_scores`
calling only `upsert_cve()` (no `record_source_status`). However, if
`supports_fetch_single = True`, the `fetch_single_cve` orchestrator needs
`record_source_status` for the failure/missing paths.

Should the periodic `execute()` also call `record_source_status` per CVE?
Red Hat does not (it handles errors internally), but it does have
`record_source_status` available for the `fetch_single` path.

**Resolution**: resolved by architecture (commits 72f8ef5, 305bab9).
`upsert_cve()` is now a pure service function — it calls
`record_source_status("success")` internally but does NOT commit. In
`execute()`, rollback on error discards the "success" record (no explicit
`record_source_status("failure")` needed in batch path — the rollback
preserves the previous `CVESource` state). Only the `fetch_single_cve`
orchestrator writes explicit `"failure"`/`"missing"` status. This matches
the Red Hat pattern exactly.

**Remaining action**: update caller table in `cve-service.md` to add
`record_source_status()` (orchestrator path) and
`build_post_ingest_tasks()` — deferred to Session 3 (cross-spec fixes).

### OP-5: Handling "CVE not in EPSS" in `fetch_single()` — RESOLVED

Very recent CVEs (< 24h old) may not have EPSS scores yet. The EPSS API
returns HTTP 200 with empty `data: []` and `total: 0` (verified
2026-06-23 with `CVE-9999-99999`). This is different from HTTP 404.

**Resolution**:

| Path | Condition | Action |
|------|-----------|--------|
| `fetch_single()` | `data: []` (CVE not scored) | Raise `CVENotInSource` |
| `execute()` | `data: []` (CVE not scored) | Silent skip (`continue`) — no error, no metric |

The orchestrator (`fetch_single_cve`) catches `CVENotInSource` and
records `status = missing` in `CVESource`. The next periodic run (or
catch-up) will find the score when EPSS has it.

Rationale: `CVENotInSource` is the standard semantic signal used by
Red Hat (HTTP 404) and NVD (empty response) for "data does not exist
yet in this source." The silent skip in `execute()` is appropriate
because the CVE is in scope (has an active ticket) — it is not an
error, just data not yet available.

### OP-6: Throttle delay as custom setting — RESOLVED

**Resolution**: no custom settings. The inter-request delay is
configured via `FetcherConfig.request_delay`, initialized from
`default_request_delay = 0.2` at auto-registration.

| Setting | Type | Default | Constraints | Description |
|---------|------|---------|-------------|-------------|
| (none) | — | — | — | The inter-request delay is configured via `FetcherConfig.request_delay` (initial: 0.2, from `default_request_delay`) |

`request_delay` initial 0.2s → max 300 req/min = 30% of EPSS's
1000 req/min rate limit. At expected volume (~200-300 active tickets),
total run time is ~40-60 seconds. Operators can adjust via admin
dashboard without code changes.

### OP-7: Significant score change tracking — RESOLVED

**Resolution**: simple overwrite, no change detection.

- `upsert_cve()` overwrites `score` and `percentile` unconditionally
  (ON CONFLICT DO UPDATE)
- No pre-read to compare previous values
- No logging or side effects on score changes
- `record_updated()` fires on every successful upsert (throughput
  semantics — see OP-9)

Rationale: EPSS recalculates the full model daily, but individual CVE
scores may remain stable for days (verified 2026-06-23: CVE-2024-0001
had identical score for 3 consecutive days). A "significant change
alert" mechanism would require configurable thresholds, pre-reads, and
an alert destination — complexity without a current consumer. If
historical tracking is needed in the future, a `CVEEPSSScoreHistory`
table can be added without impacting the fetcher.

### OP-8: Data model lifecycle note enhancement — RESOLVED

**Resolution**: do NOT add the catch-up/`fetch_single()` sentence (that
is mechanism detail belonging to the fetcher spec). Instead, simplify the
existing lifecycle note in `data-model.md` by removing implementation
details (`reconcile_ticket_status()` reference, explicit status list).

Current text (lines 734–741):
> the `sync_epss_scores` fetcher refreshes EPSS data only for CVEs with
> **active tickets** (New, Analysis, Analyzed). When a ticket transitions
> to Resolved, Ignored, or Duplicated, the CVEEPSSScore record is
> **retained** but no longer refreshed — consistent with the CVSS
> lifecycle pattern [...]. If the ticket later regresses to an active
> status (e.g., `reconcile_ticket_status()` moves it back to Analyzed),
> the fetcher resumes refreshing the record on its next run.

Simplified version (to apply in Session 3):
> the `sync_epss_scores` fetcher refreshes EPSS data only for CVEs with
> **active tickets** (New, Analysis, Analyzed). When a ticket transitions
> to an inactive status, the CVEEPSSScore record is **retained** but no
> longer refreshed — consistent with the CVSS lifecycle pattern [...].
> If the ticket later returns to an active status, the fetcher resumes
> refreshing the record on its next run.

Changes:
- Replaced "Resolved, Ignored, or Duplicated" → "an inactive status"
  (canonical term from `conventions.md`)
- Removed "(e.g., `reconcile_ticket_status()` moves it back to
  Analyzed)" — implementation mechanism detail

Rationale: `data-model.md` documents data completeness and freshness
semantics (essential for schema consumers). Mechanism details (which
function triggers the transition) belong in the fetcher/service specs.

### OP-9: `record_updated` metric semantics — RESOLVED

**Resolution**: Option A — throughput metric, placed inside `fetch_single()`.

Every successful `upsert_cve()` call → `record_updated()`, called inside
`fetch_single()` per Pattern B metric placement convention (metrics are
placed where `UpsertResult.action` is available). No pre-read, no diff
detection.

**Corrected rationale** (verified 2026-06-23): the original justification
("EPSS scores change daily for most CVEs") was inaccurate. Empirical
verification shows individual CVE scores may remain stable for multiple
consecutive days (e.g., CVE-2024-0001 had identical `epss` value for 3+
days). However, Option A remains correct for a different reason:

- The upsert MUST happen regardless of whether the score changed, because
  `assessed_at` (the API `date` field) advances daily and must be
  persisted to enable staleness detection in the UI
- Since the upsert always executes, a pre-read for diff detection would
  add one SELECT per CVE without avoiding any writes
- The overhead is unjustified: diff detection and throughput counting
  produce the same DB writes, only the metric number differs slightly

The spec documents this explicitly as a deviation: "for EPSS,
`record_updated` counts every successfully processed CVE, not only those
whose score actually changed. This is because the upsert is required
regardless (to update `assessed_at`), making pre-read diff detection
pure overhead."

**Architecture context**: `cve-service.md` states enrichment fetchers
always receive `action = unchanged` from `upsert_cve()` and "must manage
their own metrics based on internal diff detection." Option A is a
conscious, documented deviation — justified by the mandatory
`assessed_at` update that makes the upsert unconditional.

### OP-10: CVE missing from batch response in `execute()` — RESOLVED (N/A)

**Resolution**: not applicable. The single-CVE request pattern (OP-2
resolution) eliminates this problem entirely. Each request targets one
CVE; the response is either `data: [entry]` (scored) or `data: []` (not
scored). The empty-response case uses OP-5 semantics (skip silently in
`execute()`, `CVENotInSource` in `fetch_single()`).

### OP-11: `assessed_at` staleness validation — RESOLVED

The EPSS API includes a `date` field (YYYY-MM-DD) indicating the
assessment date. This is a **batch-level** publication date — the same
for ALL CVEs returned on a given day (verified 2026-06-23: all CVEs
queried returned `date: 2026-06-23`). Normally this matches the current
UTC date (publication at 13:31 UTC, fetcher runs at 14:00 UTC).

Abnormal scenarios:
- EPSS publication delayed → `date` = yesterday
- EPSS API serving stale cached data → `date` several days old
- Fetcher runs before publication (misconfigured schedule) → `date` =
  yesterday

**Resolution**: log-and-proceed strategy:

1. After the first API response in `execute()`, extract the `date` field
2. If `date < today(UTC) - 1 day` → log WARNING: "EPSS data is stale
   (assessed_at={date}, expected={today})"
3. Proceed with all upserts regardless — stale data is better than no
   data
4. The `assessed_at` field stored in `CVEEPSSScore` enables the frontend
   to display a staleness indicator (already specified in `data-model.md`
   UI display note)

Behavioral details:
- The check runs **once** per `execute()` invocation (first response
  only) — since the `date` is batch-level, checking one CVE is
  sufficient
- Threshold: `today - 1 day` (not `today`) to tolerate timezone edge
  cases near midnight UTC and minor publication delays
- `date == yesterday` → no warning (within tolerance)
- `date < yesterday` → WARNING in log
- No abort, no error metric, no failure — only diagnostic visibility
  for the operator
- The `fetch_single()` path does NOT perform staleness validation (it
  processes a single CVE on-demand; staleness is only relevant for the
  periodic batch run)

### OP-12: HTTP client configuration — RESOLVED

**Resolution**: resolved by shared HTTP client infrastructure (commit
6691351). The HTTP client draft was finalized and applied to all
approved specs. The EPSS fetcher uses `self.http_client` with zero
configuration — shared defaults are appropriate for the EPSS API:

| Aspect | Shared default | EPSS needs |
|--------|----------------|------------|
| Read timeout | 30s | 30s (responses are small, ~1KB per CVE) |
| Transport retry | 4 attempts on 5xx (1s/2s/4s backoff) | Appropriate |
| 429 handling | Retry-After guided (1 retry if ≤120s) | Appropriate |
| User-Agent | `Sentinel/{ver} (sync_epss_scores; +https://...)` | Automatic |
| TLS | Combined trust store (system CAs + SUSE CA) | Not needed (public API, standard CAs) |

No `http_client_options` override needed. The per-fetcher HTTP client
configuration section originally planned for Session 2 is replaced by
a cross-reference to `fetcher-infrastructure.md` ("Shared HTTP Client").

## 6. Application Plan (Steps to Complete the Spec)

The spec file already exists at `docs/features/tickets/cve-sync-epss.md`
(placeholder). Sessions 2–3 overwrite it with the complete specification
and apply cross-spec corrections. Session 4 validates the result and
removes this workplan.

### Session 1: Core design decisions (this session)
- [x] Research API capabilities
- [x] Identify preconcept
- [x] Cross-check with RedHat fetcher
- [x] Create this work plan
- [x] Resolve OP-12 (resolved by shared HTTP client infrastructure)
- [x] Resolve Open Points OP-5, OP-7, OP-8, OP-9, OP-11

### Session 2: Write the complete spec
- [ ] Write the full `cve-sync-epss.md` spec with all mandatory sections:
  - Properties table (with resolved schedule, scope, settings)
  - Algorithm (execute + fetch_single)
    - `execute()`: Pattern B — delegates to `self.fetch_single()` per CVE,
      then `commit_and_dispatch(session, post_ingest)`. Includes
      consecutive failure abort (3 consecutive → `FetcherError`) and
      staleness check on first success
    - `fetch_single()`: single-CVE API query → parse → `upsert_cve()` →
      `record_updated()` → returns `None` (enrichment-only). Raises
      `CVENotInSource` on empty response
  - Error Handling (fetch_single + execute, table format like RedHat)
    - `execute()` error path: per-CVE — `session.rollback()` +
      `record_failed()` (no explicit `record_source_status("failure")`
      — architecture decision)
    - `fetch_single()` error path: `CVENotInSource` on empty response
      (orchestrator handles failure/missing status)
    - Pydantic `ValidationError` (unparseable/out-of-range values):
      `record_failed()` + continue (same as parse error)
    - Consecutive failure abort: 3 infra failures → `FetcherError`
  - Metrics (with explicit deviation note for `record_updated` semantics
    — throughput metric, not diff-based; placed inside `fetch_single()`
    per Pattern B convention)
  - Custom Settings table (none — declares `default_request_delay = 0.2`,
    delay managed via `FetcherConfig.request_delay`)
  - Field Mapping
  - Explicitly Ignored Fields (envelope: `status`, `status-code`,
    `version`, `access`, `total`, `offset`, `limit`; and `time-series`
    array if present. Note: `date` is NOT ignored — mapped to
    `assessed_at`)
  - Behavioral Notes (data lifecycle, re-invocation, first-run)
  - First-run behavior: "no special first-run behavior — iterates over
    all CVEs with active tickets. If no active tickets, completes
    immediately with zero records" (RedHat pattern)
  - Statefulness: "stateless fetcher — no cursor. Each run reprocesses
    the entire in-scope set"
  - Scope snapshot: queried once at the start of `execute()`. New tickets
    created mid-run are covered by `catch_up()` / `fetch_single()` on
    demand
  - CVE existence precondition: document in `fetch_single()` section that
    the CVE record is guaranteed to exist by the caller flow
    (`ensure_cve_exists()` → `trigger_on_demand_fetch()` →
    `fetch_single()`)
  - HTTP client: cross-reference to `fetcher-infrastructure.md` Shared
    HTTP Client section (no per-fetcher config — shared defaults are
    appropriate)
  - Error handling note: transport-level retry (5xx: 4 attempts,
    429+Retry-After: 1 guided retry) happens before the fetcher sees
    the error. Error table documents post-transport behavior only
  - Known limitations: EPSS model version changes (v4→v5) cause score
    discontinuities not distinguishable from exploitation probability
    changes. No automated detection (no `model_version` field in API)
  - Class Structure (Python skeleton with Pattern B delegation,
    `default_request_delay = 0.2`, `fetch_single()` return type
    `PostIngestTasks | None`, consecutive failure abort, metrics inside
    `fetch_single()`)
  - Cross-references

### Session 3: Fix cross-spec inconsistencies
- [ ] Update `fetcher-infrastructure.md`: remove EPSS from catalog-based
  references (5 locations per Section 3 table)
- [ ] Update `cve-tracking.md`: remove EPSS from `supports_fetch_single = False`
  list
- [ ] Update `cve-service.md`: update caller table and `supports_fetch_single`
  reference
- [ ] Update `data-sources.md`: complete the Fetcher Registry entry (schedule,
  rate limits)
- [ ] Update `data-model.md`: simplify lifecycle note (remove
  `reconcile_ticket_status()` reference, use "inactive status" term)
- [ ] Update `cve-sync-redhat.md`: replace "5000 active tickets" with "800
  active tickets" in operational notes (line 398), recalculate runtime
  (~27 minutes at 2.0s delay)

### Session 4: Review, validation, and cleanup
- [ ] Invoke `@fetcher-compliance-reviewer` on completed spec
- [ ] Invoke `@spec-coherence-reviewer` to verify no contradictions
- [ ] Invoke `@spec-gap-analyzer` to identify uncovered scenarios
- [ ] Invoke `@docs-placement-reviewer` to verify information placement
- [ ] Invoke `@docs-reviewer` to verify documentation completeness after
  cross-spec modifications (Session 3 touches 5+ files)
- [ ] Resolve issues identified by reviewers — fix any problems rated
  "Needs revision" or High severity before proceeding
- [ ] Delete `docs/drafts/epss-fetcher-workplan.md` — the workplan has
  served its purpose; all design decisions are now captured in the
  approved spec and cross-spec corrections are applied

## 7. RedHat Cross-Check Summary

Key patterns borrowed from `cve-sync-redhat.md`:

| Pattern | RedHat | EPSS adaptation |
|---------|--------|-----------------|
| Pattern B (`execute()` delegates to `fetch_single()`) | Yes | Yes — identical structure |
| Per-CVE API → `fetch_single()` | Yes | Yes (API supports `?cve=X`) |
| `execute()` iterates active tickets | Yes | Yes (per-CVE, identical to RedHat) |
| Metrics inside `fetch_single()` | Yes | Yes (`record_updated()` inside `fetch_single()`) |
| Default `catch_up()` via `fetch_single()` | Yes | Yes (inherited from BaseCVEFetcher) |
| `default_request_delay` | 2.0 (no official rate limit) | 0.2 (1000 req/min published limit) |
| `FetcherConfig.request_delay` | Operator-tunable via admin dashboard | Same mechanism |
| Error handling per-CVE in execute | Yes | Yes (per-CVE, identical to RedHat) |
| Consecutive failure abort threshold | 3 consecutive infra failures | Identical (3 consecutive, same reset conditions) |
| Enrichment-only (no CVE creation) | Yes | Yes |
| Field Mapping table | Yes | Yes (simpler: only score + percentile) |
| Explicitly Ignored Fields table | Yes | Yes |
| `source_reference_url_pattern` | `https://access.redhat.com/security/cve/{cve_id}` | `None` (no per-CVE page) |
| HTTP client | Shared defaults (no `http_client_options`) | Same — shared defaults appropriate |

Differences from RedHat:
- EPSS has no CVSS, CWE, references, packages — only score + percentile
- EPSS payload is trivial (3 fields) vs RedHat (multi-data-type extraction)
- Lower `default_request_delay` (0.2 vs 2.0) — EPSS publishes official rate
  limit (1000 req/min), RedHat does not

## 8. Checklist — Ready to Complete

Before the work is considered done:

- [x] All Open Points resolved
- [ ] Algorithm section complete (numbered steps, no TBD)
- [ ] Error Handling section complete (both fetch_single and execute)
- [ ] Metrics section complete
- [ ] Custom Settings section complete (or "None" if no settings)
- [ ] Field Mapping table present
- [ ] Explicitly Ignored Fields table present
- [ ] Behavioral Notes present (lifecycle, re-invocation, first-run)
- [ ] Class Structure skeleton present
- [ ] Cross-references complete
- [ ] All cross-spec corrections applied
- [ ] Fetcher Registry in `data-sources.md` updated
- [ ] `@fetcher-compliance-reviewer` passed
- [ ] `@spec-coherence-reviewer` passed
- [ ] `@spec-gap-analyzer` passed (no High-severity gaps)
- [ ] `@docs-placement-reviewer` passed
- [ ] `@docs-reviewer` passed (cross-spec modifications verified)
- [ ] All reviewer issues resolved (no "Needs revision" or High remaining)
- [ ] This workplan file deleted (`docs/drafts/epss-fetcher-workplan.md`)

## 9. Session Log

| Date | Session | Work done |
|------|---------|-----------|
| 2026-06-21 | #1 | Initial research, API verification, preconcept identified, cross-check with RedHat, work plan created, Open Points defined |
| 2026-06-21 | #1b | Verified EPSS publication schedule (13:31 UTC daily, 12+ months of evidence). Resolved OP-1 (schedule → `0 14 * * *`). Confirmed `assessed_at` field already modeled correctly. Verified DST immunity (fixed UTC, no shift). Applied cross-cutting timezone enforcement fix to 4 spec files. Updated design decisions table |
| 2026-06-21 | #1c | Completeness review: added OP-9 (metric semantics reconciliation with `cve-service.md`), OP-10 (batch missing CVEs in `execute()`), OP-11 (`assessed_at` staleness validation), OP-12 (HTTP client configuration). Added first-run behavior, statefulness, and CVE existence precondition to Design Decisions table and Session 2 plan. Updated "What is missing" list |
| 2026-06-21 | #1d | Architecture alignment: reviewed commits 72f8ef5 (transaction ownership — `upsert_cve()` now pure service function, `commit_and_dispatch()` helper), 305bab9 (`record_source_status("failure")` removed from `execute()` path — rollback is sufficient), daeb249 (HTTP client infrastructure draft). Resolved OP-4 (architecture confirms pattern). Annotated OP-12 (WIP HTTP client draft may impact). Updated OP-2 pseudocode with `commit_and_dispatch` pattern and batch-adapted Pattern B. Added 4 new design decisions (transaction ownership, per-CVE commit, `fetch_single` return type, `record_source_status` in `execute()`). Added architecture context to OP-9. Verified and updated cross-spec correction line numbers (Sezione 3). Updated Session 2 plan with new architecture elements |
| 2026-06-21 | #1e | Resolved OP-2 (single-CVE pattern, RedHat-identical — batch deferred as unnecessary at expected volume of ~200-300 active tickets). Resolved OP-3 (`source_reference_url_pattern = None` — no per-CVE page on FIRST.org, verified). Resolved OP-10 (N/A — no batch responses with single-CVE pattern). Simplified OP-6 (removed `batch_size`, only `throttle_delay_seconds` remains). Updated pseudocode, design decisions table, Session 2 plan, and RedHat cross-check to reflect single-CVE architecture |
| 2026-06-23 | #1f | Infrastructure alignment review: resolved OP-12 (shared HTTP client finalized, commit 6691351 — EPSS uses shared defaults, no per-fetcher HTTP config). Lowered `default_request_delay` from 0.5 to 0.2 (30% of 1000 req/min rate limit, same as OSV). Updated cross-spec correction line numbers (Section 3 — line shifts from +347 lines in fetcher-infrastructure.md). Added RedHat operational note fix (800 tickets, ~27min) to Session 3 plan. Updated Session 2 plan to reference shared infrastructure. Updated RedHat Cross-Check Summary with new `default_request_delay` and HTTP client rows |
| 2026-06-23 | #1g | Resolved all remaining Open Points. OP-5: confirmed `CVENotInSource` on empty `data[]` (verified API returns HTTP 200 + empty array for non-existent CVEs). OP-7: confirmed simple overwrite, no change detection. OP-8: resolved as "simplify existing note" — remove `reconcile_ticket_status()` mechanism detail, do NOT add catch-up sentence (belongs in fetcher spec). OP-9: confirmed Option A (throughput metric) with corrected rationale — upsert is mandatory regardless (to update `assessed_at`), making pre-read diff detection pure overhead. Empirically verified that individual EPSS scores may remain stable for days (CVE-2024-0001: identical score 3 days). OP-11: confirmed log-and-proceed with `today - 1 day` threshold; verified `date` field is batch-level (same for all CVEs on same day). Session 1 complete — all design decisions determined, ready for Session 2 |
| 2026-06-23 | #1h | Reviewer feedback session. Ran 4 reviewers (@spec-coherence, @spec-gap-analyzer, @fetcher-compliance, @docs-placement). Key findings incorporated: (1) Corrected algorithm to Pattern B — `execute()` now delegates to `self.fetch_single()`, metrics placed inside `fetch_single()` (was incorrectly Pattern A in pseudocode). (2) Added consecutive failure abort threshold (3 consecutive infra failures → `FetcherError`, identical to Red Hat). (3) Added `ValidationError` handling as `record_failed()` + continue. (4) Added EPSS model version change as known limitation (no detection logic). (5) Confirmed M4 (no `request_delay` floor — by design) and M5 (CVE REJECTED with active ticket is possible but handled correctly by normal empty-response skip). Updated pseudocode, design decisions, Session 2 plan, and RedHat cross-check table |
| | #2 | (pending) |
| | #3 | (pending) |
| | #4 | (pending) |
