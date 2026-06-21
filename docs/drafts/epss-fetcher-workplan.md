# EPSS Fetcher — Work Plan

**Status**: Draft — work-in-progress across multiple sessions
**Target**: `docs/features/tickets/cve-sync-epss.md` (replace current placeholder)
**Last updated**: 2026-06-21 (Session 1c — completeness review)

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
| Pydantic schema (`EPSSEntry`) | `docs/features/tickets/cve-service.md` (line 1080) | Complete |
| `CVEIngestPayload.epss_score` field | `docs/features/tickets/cve-service.md` (line 1008) | Complete |
| `cve_source_type = "epss"` | `docs/features/platform/fetcher-infrastructure.md` (line 250) | Registered |
| Fetcher Registry entry | `docs/data-sources.md` (line 970) | Incomplete (TBD fields) |
| Source description | `docs/data-sources.md` (lines 145-162) | Complete |
| Child table deduplication | `cve-service.md` (line 520) | ON CONFLICT DO UPDATE on `cve_id` |

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
- HTTP client configuration (timeout, retry, User-Agent)

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
- It uses batch queries for its periodic `execute()` (more efficient than
  Red Hat's one-request-per-CVE approach)

### Locations requiring correction

| File | Line(s) | Current text | Required change |
|------|---------|--------------|-----------------|
| `fetcher-infrastructure.md` | 326-327 | "Catalog-based fetchers (KEV, EPSS) that have no per-CVE API set `supports_fetch_single = False`" | Remove EPSS from this parenthetical; only KEV is catalog-based |
| `fetcher-infrastructure.md` | 530 | "fetchers like KEV and EPSS) are excluded" | Remove EPSS |
| `fetcher-infrastructure.md` | 863 | "sync_epss_scores \| Syncs all EPSS scores (sets participates_in_catch_up = False)" | Move EPSS to the "participate in catch-up" table |
| `fetcher-infrastructure.md` | 865-875 | Groups KEV and EPSS with same rationale | Separate: KEV remains catalog-based, EPSS moves to per-CVE API category |
| `fetcher-infrastructure.md` | 1257 | "Catalog-based fetchers (KEV, EPSS)" in `supports_fetch_single` description | Remove EPSS from the example |
| `cve-tracking.md` | 466 | "(KEV, EPSS) that set `supports_fetch_single = False`" | Remove EPSS |
| `cve-service.md` | 768 | '"epss") that set `supports_fetch_single = False` are excluded' | Remove EPSS |
| `cve-service.md` | 1219 | Caller table: `sync_epss_scores` → only `upsert_cve()` | Add `record_source_status()` (failure path) |

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

### OP-2: Algorithm strategy for `execute()`

Two viable strategies:

**Option A — Batch API (recommended)**:
- Collect CVE-IDs with active tickets
- Split into batches of ~120 CVEs (conservative: 2000-char limit minus overhead)
- For each batch: `GET /epss?cve=CVE-1,CVE-2,...`
- Parse response, upsert per-CVE
- Throttle between batches

**Option B — Full catalog download (CSV)**:
- Download `https://epss.empiricalsecurity.com/epss_scores-YYYY-MM-DD.csv.gz`
  (~15MB compressed)
- Filter locally against CVEs with active tickets
- Bulk upsert

Trade-offs:
- Option A: fewer data transferred (only relevant CVEs), simpler error isolation
  per batch, API-native approach, aligns with `fetch_single()` reuse
- Option B: single HTTP request, but downloads 200k+ entries when we may
  only need a few thousand; no reuse with `fetch_single()` logic

**Proposal**: Option A (batch API). The batch approach naturally aligns with
the `fetch_single()` reuse pattern (fetch_single is just batch_size=1) and
is more efficient for typical deployments (few thousand active tickets vs
200k+ total CVEs in EPSS).

**Decision needed**: approve strategy, or choose alternative.

### OP-3: `source_reference_url_pattern`

FIRST.org does not appear to have a dedicated human-readable page per CVE
on `first.org/epss/`. The API response does not include reference URLs.

Options:
- `None` — EPSS has no per-CVE source page to link to
- A constructed URL to the API itself (but that's not human-friendly)

**Proposal**: `source_reference_url_pattern = None`. No `TicketReference`
is created by this fetcher — it only writes EPSS data (score + percentile).

**Decision needed**: confirm `None`, or identify an alternative URL pattern.

### OP-4: `record_source_status` usage

The current caller table in `cve-service.md` shows `sync_epss_scores`
calling only `upsert_cve()` (no `record_source_status`). However, if
`supports_fetch_single = True`, the `fetch_single_cve` orchestrator needs
`record_source_status` for the failure/missing paths.

Should the periodic `execute()` also call `record_source_status` per CVE?
Red Hat does not (it handles errors internally), but it does have
`record_source_status` available for the `fetch_single` path.

**Proposal**: same as RedHat — `record_source_status()` is used implicitly
by `upsert_cve()` on success, and explicitly by the `fetch_single_cve`
orchestrator on failure/missing paths. The periodic `execute()` handles
errors internally with `record_failed()`. Update caller table to add
`record_source_status()` (failure path).

**Decision needed**: confirm pattern matches RedHat.

### OP-5: Handling "CVE not in EPSS" in `fetch_single()`

Very recent CVEs (< 24h old) may not have EPSS scores yet. How should
`fetch_single()` handle this?

The EPSS API returns a valid response with empty `data: []` when a CVE
is not found (HTTP 200, `total: 0`). This is different from HTTP 404.

**Proposal**: empty `data` array → raise `CVENotInSource`. This is the
same semantic signal used by Red Hat (HTTP 404) and NVD (empty response).
The orchestrator records `status = missing`, and the next periodic run
(or catch-up) will eventually find the score when EPSS has it.

**Decision needed**: confirm `CVENotInSource` on empty response.

### OP-6: Batch size as custom setting

Should the batch size (number of CVE-IDs per API request) be configurable?

**Proposal**: yes, with custom settings:

| Setting | Type | Default | Constraints | Description |
|---------|------|---------|-------------|-------------|
| `batch_size` | int | 100 | 10–500 | Number of CVE-IDs per batch API request |
| `throttle_delay_seconds` | float | 0.5 | 0.1–10.0 | Delay between consecutive batch API requests |

Rationale: batch_size=100 is conservative (~1500 chars for CVE-IDs, well
under the 2000-char API limit). Throttle of 0.5s is less aggressive than
Red Hat (2.0s) because EPSS has higher rate limits (1000 req/min) and
batch requests are more efficient.

**Decision needed**: confirm settings, or adjust defaults/constraints.

### OP-7: Significant score change tracking

When an EPSS score changes, should the fetcher log or trigger any side
effect? Or is simple overwrite sufficient?

Current data model: `CVEEPSSScore` is a point-in-time snapshot (overwritten
daily). No history table exists.

**Proposal**: simple overwrite, no change detection beyond what `upsert_cve()`
provides. The metric `record_updated` fires on every successful upsert
(consistent with KEV "processed" semantics). Significant change alerting
can be added as a future enhancement if needed.

**Decision needed**: confirm simple overwrite approach.

### OP-8: Data model lifecycle note enhancement (optional)

The `data-model.md` lifecycle note (line 733) says:
> "the sync_epss_scores fetcher refreshes EPSS data only for CVEs with
> **active tickets**"

This is correct given the per-active-ticket scope. However, it could also
mention the `fetch_single()` / catch-up behavior (EPSS data is also
refreshed on ticket reactivation via catch-up).

**Proposal**: add a sentence about catch-up to the lifecycle note:
> "If a ticket is reactivated (returns to an active status), the catch-up
> mechanism invokes `fetch_single()` to immediately refresh the EPSS score
> without waiting for the next periodic run."

**Decision needed**: confirm, or defer as trivial.

### OP-9: `record_updated` metric semantics

`cve-service.md` (lines 1192–1197) states that enrichment fetchers always
receive `action = unchanged` from `upsert_cve()` and must "manage their own
metrics based on their internal diff detection." This contradicts OP-7's
proposal of firing `record_updated` on every successful upsert.

Two options:

**Option A — Fire on every upsert (throughput metric)**:
- Every successful `upsert_cve()` call → `record_updated()`
- Simple, no pre-read required
- Semantically a "processed" count, not a true "updated" count
- EPSS scores change daily for most CVEs, so the distinction is
  academic in practice

**Option B — Internal diff detection (true update metric)**:
- Before `upsert_cve()`, read current `CVEEPSSScore` from DB
- Compare score + percentile with fetched values
- Fire `record_updated()` only if values differ
- Adds one SELECT per CVE (or per batch with bulk read)

**Proposal**: Option A. EPSS scores change daily for the vast majority
of CVEs (the model recalculates all scores every day), so diff detection
would fire `record_updated` almost every time anyway. The added
complexity of pre-reads provides no practical value. Document this
explicitly as a deviation from the common metric semantics: "for EPSS,
`record_updated` counts every successfully processed CVE, not only those
whose score actually changed."

**Decision needed**: confirm Option A, or choose Option B.

### OP-10: CVE missing from batch response in `execute()`

When a batch request asks for N CVE-IDs and the response contains M < N
entries, some CVEs are absent from the response. This is distinct from
OP-5 (`fetch_single()` handling).

Scenarios for missing CVEs in batch:
- Very recent CVEs (< 24h) not yet scored by EPSS
- REJECTED CVEs removed from EPSS coverage

**Proposal**: skip silently — no `record_source_status()`, no
`record_failed()`, no log. Rationale:
- These are not errors — the CVE simply has no EPSS data (yet)
- The next periodic run will retry them automatically
- Calling `record_source_status(..., "missing")` for every unscored CVE
  on every run would create noise in `CVESource` for a transient
  condition
- In `fetch_single()` (OP-5), `CVENotInSource` IS raised because the
  orchestrator needs an explicit signal. In `execute()`, no orchestrator
  exists — the fetcher owns the retry loop

**Decision needed**: confirm skip-silently, or choose to record
`missing` status.

### OP-11: `assessed_at` staleness validation

The EPSS API includes a `date` field (YYYY-MM-DD) indicating the
assessment date. Normally this matches the current UTC date (publication
at 13:31 UTC, fetcher runs at 14:00 UTC). Abnormal scenarios:

- EPSS publication delayed → `date` = yesterday
- EPSS API serving stale cached data → `date` several days old
- Fetcher runs before publication (misconfigured schedule) → `date` =
  yesterday

**Proposal**: log-and-proceed strategy:
1. After parsing the first batch response, extract the `date` field
2. If `date < today(UTC) - 1 day` → log WARNING: "EPSS data is stale
   (assessed_at={date}, expected={today})"
3. Proceed with the upsert regardless — stale data is better than no
   data
4. The `assessed_at` field stored in `CVEEPSSScore` enables the frontend
   to display a staleness indicator (already specified in
   `data-model.md` UI display note)

The check runs once per `execute()` invocation (first batch only), not
per CVE. The threshold of `today - 1 day` accounts for timezone edge
cases near midnight UTC.

**Decision needed**: confirm log-and-proceed, adjust threshold, or skip
validation entirely.

### OP-12: HTTP client configuration

The workplan does not specify HTTP client parameters. Other API-based
fetchers (NVD, RedHat) configure these per-fetcher.

**Proposal**:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Request timeout | 30s | Batch responses are small (~10KB per 100 CVEs); 30s accommodates slow responses without blocking the worker |
| Retry | 3 attempts, exponential backoff (1s, 2s, 4s) on 5xx / connection timeout | Standard transient error recovery |
| User-Agent | `Sentinel/1.0 (EPSS fetcher)` | Courtesy identification for public API |

These values are hardcoded constants (not custom settings), consistent
with other API-based fetchers. If the fetcher infrastructure provides a
shared HTTP client with default timeout/retry behavior, EPSS should use
it and document any overrides.

**Decision needed**: confirm values, or identify shared HTTP client
infrastructure that already handles these.

## 6. Application Plan (Steps to Complete the Spec)

### Session 1: Core design decisions (this session)
- [x] Research API capabilities
- [x] Identify preconcept
- [x] Cross-check with RedHat fetcher
- [x] Create this work plan
- [ ] Resolve Open Points OP-1 through OP-12

### Session 2: Write the complete spec
- [ ] Write the full `cve-sync-epss.md` spec with all mandatory sections:
  - Properties table (with resolved schedule, scope, settings)
  - Algorithm (execute + fetch_single)
  - Error Handling (fetch_single + execute, table format like RedHat)
  - Metrics (with explicit deviation note for `record_updated` semantics)
  - Custom Settings table
  - Field Mapping
  - Explicitly Ignored Fields (EPSS API has `date`, `days`, etc.)
  - Behavioral Notes (data lifecycle, re-invocation, first-run)
  - First-run behavior: "no special first-run behavior — iterates over
    all CVEs with active tickets. If no active tickets, completes
    immediately with zero records" (RedHat pattern)
  - Statefulness: "stateless fetcher — no cursor. Each run reprocesses
    the entire in-scope set"
  - CVE existence precondition: document in `fetch_single()` section that
    the CVE record is guaranteed to exist by the caller flow
    (`ensure_cve_exists()` → `trigger_on_demand_fetch()` →
    `fetch_single()`)
  - HTTP client configuration (timeout, retry, User-Agent per OP-12)
  - Class Structure (Python skeleton)
  - Cross-references

### Session 3: Fix cross-spec inconsistencies
- [ ] Update `fetcher-infrastructure.md`: remove EPSS from catalog-based
  references (6 locations)
- [ ] Update `cve-tracking.md`: remove EPSS from `supports_fetch_single = False`
  list
- [ ] Update `cve-service.md`: update caller table and `supports_fetch_single`
  reference
- [ ] Update `data-sources.md`: complete the Fetcher Registry entry (schedule,
  rate limits)
- [ ] Update `data-model.md`: add catch-up sentence to lifecycle note

### Session 4: Review and validation
- [ ] Invoke `@fetcher-compliance-reviewer` on completed spec
- [ ] Invoke `@spec-coherence-reviewer` to verify no contradictions
- [ ] Invoke `@spec-gap-analyzer` to identify uncovered scenarios
- [ ] Invoke `@docs-placement-reviewer` to verify information placement
- [ ] Final review of all changes for consistency
- [ ] Move spec from draft to `docs/features/tickets/cve-sync-epss.md`

## 7. RedHat Cross-Check Summary

Key patterns borrowed from `cve-sync-redhat.md`:

| Pattern | RedHat | EPSS adaptation |
|---------|--------|-----------------|
| Per-CVE API → `fetch_single()` | Yes | Yes (API supports `?cve=X`) |
| `execute()` iterates active tickets | Yes | Yes (batch instead of per-CVE) |
| Default `catch_up()` via `fetch_single()` | Yes | Yes (inherited from BaseCVEFetcher) |
| Throttle delay custom setting | Yes (`throttle_delay_seconds`) | Yes + `batch_size` |
| Error handling per-CVE in execute | Yes | Per-batch (different granularity) |
| Enrichment-only (no CVE creation) | Yes | Yes |
| Field Mapping table | Yes | Yes (simpler: only score + percentile) |
| Explicitly Ignored Fields table | Yes | Yes |
| `source_reference_url_pattern` | `https://access.redhat.com/security/cve/{cve_id}` | `None` (no per-CVE page) |
| Consecutive failure abort threshold | 3 consecutive infra failures | Same pattern applicable |

Differences from RedHat:
- EPSS uses batch API (~130 CVEs/request vs 1 CVE/request)
- EPSS has no CVSS, CWE, references, packages — only score + percentile
- EPSS payload is trivial (3 fields) vs RedHat (multi-data-type extraction)
- Throttle can be less aggressive (higher API rate limit, fewer requests)
- Error isolation is per-batch, not per-CVE (but individual CVE failures
  within a batch response are still tracked)

## 8. Checklist — Ready to Move to `docs/features/`

Before the spec can be moved from draft to approved:

- [ ] All Open Points resolved
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
- [ ] `@fetcher-compliance-reviewer` passed
- [ ] `@spec-coherence-reviewer` passed
- [ ] `@spec-gap-analyzer` passed (no High-severity gaps)
- [ ] Fetcher Registry in `data-sources.md` updated

## 9. Session Log

| Date | Session | Work done |
|------|---------|-----------|
| 2026-06-21 | #1 | Initial research, API verification, preconcept identified, cross-check with RedHat, work plan created, Open Points defined |
| 2026-06-21 | #1b | Verified EPSS publication schedule (13:31 UTC daily, 12+ months of evidence). Resolved OP-1 (schedule → `0 14 * * *`). Confirmed `assessed_at` field already modeled correctly. Verified DST immunity (fixed UTC, no shift). Applied cross-cutting timezone enforcement fix to 4 spec files. Updated design decisions table |
| 2026-06-21 | #1c | Completeness review: added OP-9 (metric semantics reconciliation with `cve-service.md`), OP-10 (batch missing CVEs in `execute()`), OP-11 (`assessed_at` staleness validation), OP-12 (HTTP client configuration). Added first-run behavior, statefulness, and CVE existence precondition to Design Decisions table and Session 2 plan. Updated "What is missing" list |
| | #2 | (pending) |
| | #3 | (pending) |
| | #4 | (pending) |
