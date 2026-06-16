# NVD Fetcher Specification — Completion Plan

## Objective

Complete the `sync_nvd_cves` fetcher specification in
`docs/features/tickets/cve-tracking.md` to the same level of detail as
`sync_redhat_cves` (API-based gold standard) and `sync_mitre_cves`
(field mapping gold standard). The goal is an unambiguous, implementation-ready
specification that eliminates all design decisions from the implementer.

## Current State

The fetcher specification lives in `docs/features/tickets/cve-tracking.md`
(lines 405-512). It includes:

- Properties table (complete)
- Algorithm (4 high-level steps)
- NVD Source API Caching section
- First Run Strategy
- On-demand Single-CVE Fetch (brief cross-reference)
- Error Handling (2 bullet points)
- Metrics (3 definitions)

The specification is marked as "Complete" in the Fetcher Registry
(`docs/data-sources.md`) but does not meet the depth standard set by
the MITRE and Red Hat fetcher sections in the same document.

## Gaps

### Critical (block implementation)

| # | Gap | Impact |
|---|-----|--------|
| G1 | No NVD response field path mapping | Implementer cannot map API response to `CVEIngestPayload` |
| G2 | No `fetch_single()` detailed spec | Implementer cannot build on-demand path correctly |
| G3 | No pagination logic | API returns max 2000 results; batch sync will silently truncate |
| G4 | No rate limiting strategy | Fetcher will exceed NVD limits and get blocked |
| G5 | No >120-day recovery strategy | Post-outage recovery is undefined |
| G6 | No CWE extraction specified | NVD `weaknesses` array ignored; data loss |
| G7 | No sanitized error messages | Violates fetcher-infrastructure requirement |

### Medium (ambiguity for implementer)

| # | Gap | Impact |
|---|-----|--------|
| G8 | Secondary CVSS skip logic underspecified | "Direct source already has data" — when/how checked? |
| G9 | NVD Source API failure handling absent | Unknown behavior if Source API is down |
| G10 | `source_reference_url_pattern` missing from properties table | Inconsistency with fetcher-infrastructure example |
| G11 | No abort threshold defined | Infinite loop on persistent 429 |
| G12 | No explicit data preservation rule on rejection | Unclear if NVD-specific behavior differs |

### Minor (clarity improvements)

| # | Gap | Impact |
|---|-----|--------|
| G13 | No class structure / pseudo-code | Inconsistency with Red Hat section |
| G14 | No custom settings despite rate-limiting need | Operational inflexibility |
| G15 | CVSS v2 handling undocumented | Implementer must guess whether to extract |
| G16 | CVSS deduplication rule absent | Multiple entries per version in same array |

## Open Points

| ID | Question | Recommendation | Resolution |
|----|----------|----------------|------------|
| OP-A | Add `request_delay_seconds` custom setting? | Yes (default 6.0s without key, 0.6s with key) | **Yes** — Session 2 |
| OP-B | Recovery strategy for >120-day gap? | Split into 120-day sub-windows, iterate. WARNING if >365 days | **First-run cursor only + reset on >120d** — Session 3 |
| OP-C | Extract CWE from NVD `weaknesses` array? | Yes — Primary CWEs from NVD, Secondary from CNAs | **Yes** — Session 1 |
| OP-D | Extract CVSS v2 from NVD? | Yes — store with version `"2.0"` | **Yes** — Session 1 |
| OP-E | Extract CVSS v4.0 from NVD? | Yes — extract from `cvssMetricV40` | **Yes** — Session 1 |
| OP-F | Confirm `source_reference_url_pattern`? | Yes — `"https://nvd.nist.gov/vuln/detail/{cve_id}"` | **Yes** — Session 4 |
| OP-G | Extract `cve.cveTags` (specifically `disputed`)? | No — informational metadata, no operational impact | **No** — ignore. See rationale below |
| OP-H | Extract CISA KEV fields embedded in CVE response? | Defer to `sync_cisa_kev` | **Defer** — `sync_mitre_cves` already populates `kev_data` from CISA-ADP container |
| OP-I | Handle `NVD-CWE-Other` and `NVD-CWE-noinfo`? | Skip — these are placeholders, not real CWEs | **Skip** — Session 1 |
| OP-J | `title` field — NVD has no dedicated title. Behavior? | Leave NULL — NVD does not provide titles | **NULL** — Session 1 |
| OP-K | Auto-adjust delay based on API key? | Document only; single configurable value | **No auto-adjust** — Session 2 |
| OP-L | Default `resultsPerPage`? | 2000 (NVD recommended max) | **2000** — Session 2 |
| OP-M | Overlap buffer for `lastModStartDate`? | 5 minutes subtracted from `started_at` | **15 minutes hardcoded** — Session 3 |
| OP-N | Per-window progress metrics during recovery? | No — aggregate at end, one `FetcherRun` | **N/A** — eliminated by OP-B (no sub-windows) |

### OP-G Rationale

NVD `cve.cveTags` (and the equivalent MITRE `containers.cna.tags` /
`containers.adp[].tags`) carry three possible values: `disputed`,
`unsupported-when-assigned`, `exclusively-hosted-service`. These are
informational metadata that do not change the operational workflow:

- They do not affect affectedness, eligibility, or delivery status
- They do not change ticket status gates
- A disputed CVE remains `PUBLISHED` (not `REJECTED`)
- The only actionable use would be a UI badge + filter (~0.4% of CVEs)

The cost (data model change, migration, payload field, extraction in 2
fetchers, UI component, API filter) exceeds the benefit. Revisit if/when
Sentinel implements automated triage suggestions.

Both NVD and MITRE provide the tags in structured form (NVD: `cveTags[]`,
MITRE: `cna.tags[]` / `adp[].tags[]`), so extraction is straightforward
if the decision is reversed in the future.

## Work Sessions

### Session 1: NVD Response Field Path Mapping (COMPLETE)

**Resolves**: G1, G6, G15, G16

**Decides**: OP-C, OP-D, OP-E, OP-G, OP-H, OP-I, OP-J

#### Output: Field Mapping Tables

##### Table 1: Global CVE fields (from `vulnerabilities[].cve`)

| `CVEIngestPayload` field | JSON path | Required | Notes |
|---|---|---|---|
| `cve_id` | `.id` | Yes | Format `CVE-YYYY-NNNN+`. Passed as `cve_id` parameter to `upsert_cve()`, not a `CVEIngestPayload` field |
| `published_date` | `.published` | Yes | ISO 8601 datetime with millis |
| `modified_date` | `.lastModified` | Yes | ISO 8601 datetime with millis |
| `cve_state` | `.vulnStatus` | Yes | See mapping below |
| `description` | `.descriptions[?lang=="en"].value` | No | First English entry. If none, `description = None` in payload (proceed) |
| `title` | — | — | **NULL** — NVD has no title [OP-J] |

**`vulnStatus` → `CVEState` mapping**:

| NVD `vulnStatus` | `CVEState` |
|---|---|
| `Received`, `AwaitingAnalysis`, `UndergoingAnalysis`, `Analyzed`, `Modified`, `Deferred` | `PUBLISHED` |
| `Rejected` | `REJECTED` |
| Any other value | `PUBLISHED` |

**Unknown `vulnStatus` handling**: if the NVD API returns a
`vulnStatus` value not listed above (e.g., a future NVD analysis
state), map it to `PUBLISHED` and log WARNING with CVE-ID and the
unrecognized value. Rationale: all current non-REJECTED NVD states
represent analysis workflow stages that do not affect Sentinel's
operational behavior — a new analysis state would follow the same
pattern.

##### Table 2: CVSS metrics (from `.metrics`)

NVD provides up to four metric arrays: `cvssMetricV2[]`,
`cvssMetricV30[]`, `cvssMetricV31[]`, `cvssMetricV40[]`.

| `CVSSAssessmentEntry` field | Source | Notes |
|---|---|---|
| `provider_name` | `.type` + `.source` | Primary → `"NVD"`. Secondary → resolve `.source` email via NVD Source API cache |
| `cvss_version` | **Derived** from vector string prefix | `"AV:..."` → `"2.0"`, `"CVSS:3.0/..."` → `"3.0"`, `"CVSS:3.1/..."` → `"3.1"`, `"CVSS:4.0/..."` → `"4.0"` |
| `vector_string` | `.cvssData.vectorString` | Raw vector string from response |
| `score` | **Derived** by parsing the vector string | Computed locally using `cvss` library, NOT from `.cvssData.baseScore` |

**Extraction rules**:

1. Iterate all four metric arrays in order: `cvssMetricV2`,
   `cvssMetricV30`, `cvssMetricV31`, `cvssMetricV40`
2. For each entry, extract `.cvssData.vectorString`
3. **Parse locally** with the `cvss` library:
   - If parsing succeeds → derive `cvss_version` and `score` from the
     parsed vector
   - If parsing fails (invalid/malformed vector) → **skip entry**, log
     WARNING with CVE ID + raw vector string
4. Determine `provider_name`:
   - `type == "Primary"` → `provider_name = "NVD"`
   - `type == "Secondary"` → resolve `source` email to display name via
     NVD Source API cache
5. **Empty vector gate**: if `vectorString` is empty or absent, skip the
   entry (log WARNING)
6. **Intra-array deduplication**: if multiple entries share the same
   `(source, type)` within a metric array, use the **last** entry in
   array order

**NVD Source API resolution failure**: if the Source API cache is
unavailable (degraded mode) or does not contain a mapping for a
specific Secondary entry's `source` email, use the raw email address
as `provider_name` (fallback). Do NOT skip the entry. See "NVD Source
API Failure Handling" (Session 3, Section 4) for the complete degraded
mode specification and orphan row implications.

**Ignored metric fields**: `baseScore`, `baseSeverity`, `version` from
`cvssData` (all derived locally from vector). `exploitabilityScore`,
`impactScore`, and all legacy V2 flags (`acInsufInfo`,
`obtainAllPrivilege`, `obtainUserPrivilege`, `obtainOtherPrivilege`,
`userInteractionRequired`) are ignored.

##### Table 3: CWE / weaknesses (from `.weaknesses[]`)

| `CWEEntry` field | JSON path (relative to weakness entry) | Notes |
|---|---|---|
| `cwe_id` | `.description[?lang=="en"].value` | Must match `^CWE-[1-9][0-9]*$` |
| `source` | `.source` + `.type` | Primary → `"NVD"`. Secondary → resolve email via Source API |

**Extraction rules**:

1. Iterate all entries in `weaknesses[]`
2. For each entry, iterate `.description[]` and select entries where
   `lang == "en"`
3. For each description value:
   - If value matches `^CWE-[1-9][0-9]*$` → produce a `CWEEntry`
   - If value is `"NVD-CWE-Other"` or `"NVD-CWE-noinfo"` → **skip**
     (placeholder values, not real CWEs) [OP-I]
4. `source` resolution:
   - `type == "Primary"` → `source = "NVD"`
   - `type == "Secondary"` → resolve email to display name via Source
      API (fallback: raw email — see "NVD Source API Failure Handling"
      for degraded mode and orphan row implications)
5. **Deduplication**: same `(cwe_id, source)` across entries → DB UPSERT
   handles (last write wins)

##### Table 4: CPE configurations (from `.configurations[]`)

| `CPEMatchEntry` field | JSON path (relative to cpeMatch entry) | Notes |
|---|---|---|
| `criteria` | `.criteria` | CPE 2.3 format string, max 255 chars |
| `vulnerable` | `.vulnerable` | Boolean — stored as-is |
| `match_criteria_id` | `.matchCriteriaId` | UUID |

**Extraction rules**:

1. Flatten `configurations[]` → `nodes[]` → `cpeMatch[]` (ignore
   AND/OR tree structure)
2. Extract **all** `cpeMatch[]` entries (no filter on `vulnerable`)
3. Nested nodes: recursively traverse children regardless of depth
4. If `configurations` is absent (CVE not yet analyzed):
   `cpe_matches = None`

##### Table 5: References (post-upsert via `reference_service`)

References are NOT part of `CVEIngestPayload`. After
`cve_service.upsert_cve()` returns an `UpsertResult`, the fetcher calls
`reference_service.upsert_references()`.

**Source reference** (from `source_reference_url_pattern`):

| `TicketReference` field | Value |
|---|---|
| `url` | `https://nvd.nist.gov/vuln/detail/{cve_id}` |
| `title` | `"NVD"` |
| `type` | `advisory` |
| `source` | `"sync_nvd_cves"` |

**Upstream references** (from `.references[]`):

| `TicketReference` field | JSON path | Notes |
|---|---|---|
| `url` | `.references[].url` | Direct extraction |
| `title` | — | **NULL** (NVD API v2 does not provide reference names) |
| `type` | derived from `.references[].tags[]` | Via CVE Source Tag Mapping (see `ticket-references.md`) |
| `source` | `"sync_nvd_cves"` | Constant: fetcher name |

**Tag → type mapping**: derived from `.references[].tags[]` via the
CVE Source Tag Mapping defined in
[`ticket-references.md`](ticket-references.md#cve-source-tag-mapping)
(NVD column). When multiple tags are present on the same reference,
the highest-priority type wins per the priority rule in that section.

##### Table 6: Explicitly ignored fields

| Field | Reason |
|---|---|
| `cve.cveTags` | OP-G: informational metadata, no operational impact |
| `cve.evaluatorComment` | NVD internal analysis notes |
| `cve.evaluatorImpact` | NVD internal analysis notes |
| `cve.evaluatorSolution` | NVD internal analysis notes |
| `cve.vendorComments` | Free-form text, no structured mapping |
| `cve.cisaExploitAdd` | OP-H: defer — `sync_mitre_cves` already populates `kev_data` from CISA-ADP |
| `cve.cisaActionDue` | OP-H: defer |
| `cve.cisaRequiredAction` | OP-H: defer |
| `cve.cisaVulnerabilityName` | OP-H: defer |
| `cve.sourceIdentifier` | Used transiently for Source API resolution, not persisted |
| `cvssData.baseScore` | Derived locally from vector string |
| `cvssData.baseSeverity` | Derived locally from vector string |
| `cvssData.version` | Derived locally from vector string prefix |
| `cvssMetricV*.exploitabilityScore` | NVD-computed sub-score, not CVSS standard |
| `cvssMetricV*.impactScore` | NVD-computed sub-score, not CVSS standard |
| `cvssMetricV2.*` (legacy flags) | `acInsufInfo`, `obtainAllPrivilege`, `obtainUserPrivilege`, `obtainOtherPrivilege`, `userInteractionRequired` — legacy annotations |
| `cvssMetricV40.cvssData.*` (expanded metrics) | Individual metric fields derivable from vector string |
| `external_identifiers` | NVD has no structured identifiers (like GHSA). Not populated by this fetcher |
| `date_rejected` | NVD API does not provide a dedicated rejection date field. Populated by `sync_mitre_cves` from `cveMetadata.dateRejected` |

##### Table 7: CVSS deduplication rules

| Scenario | Rule |
|---|---|
| Same `(source, type)` multiple times in one metric array | Keep **last** entry in array order |
| Same `(cve_id, provider_name, cvss_version)` across fetcher runs | **UPSERT** (`ON CONFLICT DO UPDATE`) |
| Primary (NVD) and Secondary (CNA) for same CVSS version | Both stored as separate rows (different `provider_name`) |

---

### Session 2: Pagination and Rate Limiting (COMPLETE)

**Resolves**: G3, G4, G14

**Decides**: OP-A, OP-K, OP-L

#### Output: Revised Algorithm with Pagination Loop

The following replaces the current algorithm steps 1–2 in
`cve-tracking.md`. Steps 3 (per-CVE processing) and 4 (Phase 2 side
effects) remain unchanged.

##### Revised Algorithm Steps 1–4

1. Derive `last_sync` from the `started_at` timestamp of the most
   recent `FetcherRun` with `status IN ('success', 'partial')` for
   `sync_nvd_cves`. If no such run exists → first run: terminate with
   `status = success`, zero records. The `started_at` of this run
   becomes the cursor for future runs. (See Session 3, "First Run and
   >120-day Gap Handling")

2. Compute time window:
   - `window_start` = `last_sync` minus 15-minute overlap buffer (see
     Session 3, "Overlap Buffer" for rationale and idempotency guarantee)
   - `window_end` = current UTC time
   - If `window_end - window_start > 120 days`: log WARNING ("NVD
     cursor stale — gap exceeds 120 days, resetting cursor"), terminate
     run with `status = success` and zero records. The `started_at` of
     this run becomes the new cursor. No CVE data is fetched.
   - Format both as NVD date format (see "NVD Date Format" below)

3. Fetch NVD Source API cache:
   - `GET /rest/json/source/2.0` → build in-memory
     `source_identifier → display_name` mapping (see "NVD Source API
     Caching")
   - This request counts toward rate limits; sleep
     `request_delay_seconds` after it completes before the first CVE
     page request

4. Pagination loop:
   a. Initialize `start_index = 0`
   b. Request:
      ```
      GET /rest/json/cves/2.0
        ?lastModStartDate={window_start}
        &lastModEndDate={window_end}
        &startIndex={start_index}
        &resultsPerPage={results_per_page}
      ```
   c. Parse response envelope:
      - `vulnerabilities` = response `.vulnerabilities[]`
      - On the **first page only**: `total_results` = response
        `.totalResults` (used as the fixed exit threshold for the
        entire pagination loop — see `totalResults` stability note
        below)
   d. **Empty response short-circuit**: if `total_results == 0` on the
      first page, terminate the run successfully with
      `records_created = 0`, `records_updated = 0`, `records_failed = 0`.
      No CVEs were modified in the time window
   e. For each CVE in `vulnerabilities[]`: apply per-CVE processing
      (field extraction per Session 1 mapping tables 1–5)
   f. Advance: `start_index += results_per_page`
   g. **Exit condition**: if `start_index >= total_results`, exit loop
      (all pages fetched)
   h. Sleep `self.get_setting("request_delay_seconds")` before the next
      page request
   i. **HTTP 429 on page request**: apply rate limit retry (see "Rate
      Limiting" below). If retries exhausted, raise exception
      immediately — run aborts with `status = failure`, cursor does not
      advance (see Session 3, "Page Failure Handling")

**`totalResults` stability**: the `totalResults` value is read from
the **first page only** and used as the fixed exit threshold for the
entire pagination loop. Subsequent pages may report a different
`totalResults` (NVD dataset changes during pagination); these values
are ignored. This prevents infinite loops in an expanding dataset and
ensures a bounded run duration. CVEs modified after the first page
request are captured by the next scheduled run (within the 15-minute
overlap buffer).

5. Phase 2 side effects (unchanged): package resolution from all
   available sources, critical CVE notification (CVSS >= 9.0)

##### NVD Date Format

NVD date parameters (`lastModStartDate`, `lastModEndDate`) accept the
extended ISO 8601 format:

```
YYYY-MM-DDTHH:MM:SS.sss[±HH:MM]
```

Sentinel sends dates **without timezone offset** — NVD interprets the
absence of an offset as UTC:

```
2026-06-16T00:00:00.000
```

Millisecond precision (`.sss`) is required by the NVD API. Sentinel
formats all timestamps with `.000` milliseconds (truncating
sub-millisecond precision from Python's `datetime`). The `T` separator
is a literal character, not a placeholder.

##### Rate Limiting

**Primary mechanism**: `asyncio.sleep(self.get_setting("request_delay_seconds"))`
between every HTTP request to the NVD API (Source API fetch, each
pagination page).

**HTTP 429 handling**: when a page request returns HTTP 429 (Too Many
Requests), the fetcher retries that single request with exponential
backoff:

| Attempt | Delay before retry |
|---------|--------------------|
| 1st retry | 5 seconds |
| 2nd retry | 10 seconds |
| 3rd retry | 20 seconds |

This is consistent with the `fetch_single()` retry policy defined in
`docs/features/platform/fetcher-infrastructure.md` (Retry Policy for
`fetch_single`). The `Retry-After` header is deliberately ignored for
simplicity — the fixed backoff (total 35 seconds) naturally clears
NVD's 30-second rate-limit window.

**After 3 retries exhausted on a single page**: the page is considered
a permanent failure. The fetcher raises an exception immediately,
aborting the run with `status = failure`. The cursor does not advance —
the next scheduled run retries the same time window. No page skipping,
no consecutive failure counter (see Session 3, "Page Failure
Handling").

**Rate limit modes** (informational — affects `request_delay_seconds`
tuning by the operator):

| Mode | NVD limit | Recommended delay |
|------|-----------|-------------------|
| Without API key | 5 requests / 30 seconds | 6.0s (default) |
| With API key | 50 requests / 30 seconds | 0.6s |

The fetcher does **not** auto-detect API key presence to adjust the
delay. The operator configures `request_delay_seconds` via the admin
dashboard based on whether `NVD_API_KEY` is set in the environment.
This follows the same manual-tuning pattern as `sync_redhat_cves`
(`throttle_delay_seconds`).

##### Custom Settings

This fetcher declares the following custom settings (see
`docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
Schema" for the schema structure and validation rules):

| Setting | Type | Default | Constraints | Description |
|---------|------|---------|-------------|-------------|
| `request_delay_seconds` | float | 6.0 | 0.1–30.0 | Delay between consecutive NVD API requests (Source API and pagination pages) |
| `results_per_page` | int | 2000 | 100–2000 | Number of CVE records requested per API page (`resultsPerPage` parameter) |

**Operational notes**:

- With an NVD API key configured (`NVD_API_KEY` env var), the operator
  should reduce `request_delay_seconds` to 0.6 via the fetcher admin
  dashboard to maximize throughput while respecting the 50 req/30s limit
- `results_per_page = 2000` is the NVD-recommended maximum. Lower values
  increase the number of requests (and total sync time due to per-request
  delay) but reduce per-response payload size. There is no operational
  reason to reduce this unless debugging pagination issues
- Both settings are validated at run start per the Custom Settings Schema
  contract. Out-of-range values stored in `FetcherConfig.custom_settings`
  cause the run to terminate with `FetcherConfigError`

##### Properties Table Update

The following property changes from the current specification:

| Property | Old value | New value |
|----------|-----------|-----------|
| `Custom settings` | `No` | `Yes (see below)` |

### Session 3: Recovery, Edge Cases, and Boundary Conditions (COMPLETE)

**Resolves**: G5, G8, G9, G11, G12

**Decides**: OP-B, OP-M, OP-N

#### Output: Behavioral Specifications

##### 1. First Run and >120-day Gap Handling

**First run** (no previous `FetcherRun` with `status IN ('success',
'partial')` for `sync_nvd_cves`):

- Record cursor as `now` (implicitly via `started_at` on the
  `FetcherRun` row with `status = success`)
- Do NOT call the NVD CVE API
- Terminate with `status = success`, zero metrics
- Log INFO: "First run — cursor established, no CVEs fetched"

**Gap >120 days** (`window_end - window_start > 120 days`):

- Log WARNING: "NVD cursor stale — gap exceeds 120 days, resetting
  cursor"
- Do NOT call the NVD CVE API
- Terminate with `status = success`, zero metrics
- The `started_at` of this run becomes the new cursor for the next run
  (same effect as first run)

**Rationale**: a >120-day gap indicates the fetcher was disabled or the
platform was offline for an extended period. Sub-window recovery adds
specification and implementation complexity for a scenario that is
operationally unrealistic. Individual CVEs missed during the gap are
recoverable on-demand via `fetch_single()`. The MITRE and kernel
fetchers provide historical CVE data via their git-based full clone.

##### 2. Overlap Buffer

```
window_start = last_successful_run.started_at - 15 minutes
window_end   = now (UTC)
```

The 15-minute overlap is **hardcoded** (not a custom setting). It
compensates for:

- Clock skew between Sentinel and NVD servers
- NVD indexing delays (a CVE modification occurring during the previous
  run may be indexed with a `lastModified` timestamp slightly earlier
  than the run's `started_at`)

**Idempotency guarantee**: CVEs re-processed due to the overlap produce
no side effects — `cve_service.upsert_cve()` uses UPSERT semantics
(identical data = no-op, changed data = update). The overlap cost is
negligible: ~15 minutes of CVE modifications re-fetched every 6 hours.

##### 3. Page Failure Handling

Two failure categories with distinct behaviors:

**Per-page failure** (HTTP 5xx, timeout, or HTTP 429 after 3 retries on
that specific page):

- `execute()` raises an exception immediately
- `BaseFetcher.run()` catches the exception → `status = failure`
- Cursor does NOT advance (derived from `started_at` of last
  success/partial run, which is unchanged)
- Next scheduled run (6 hours later) retries the same time window
- No page skipping, no consecutive failure counter, no abort threshold

**Per-CVE failure** (malformed data, parsing error, invalid vector
string, missing English description):

- Call `self.record_failed()`
- Log ERROR with CVE-ID and exception details
- Continue processing the next CVE in the page
- Run terminates normally → `status = partial` (if `items_failed > 0`)
  or `success` (if all succeeded)
- Cursor advances (correct because the page was fully scanned — the
  failed CVE simply had unparseable data)

**Rationale for no page skipping**: skipping a page while allowing the
cursor to advance causes permanent data loss — CVEs on the skipped page
have `lastModified` timestamps that fall outside the next run's time
window. Aborting the entire run on page failure is safe because:
(a) NVD infrastructure failures are transient, (b) the 6-hour schedule
provides natural retry, (c) no data is lost since the cursor does not
advance.

##### 4. NVD Source API Failure Handling (Degraded Mode)

The Source API call (`GET /rest/json/source/2.0`) occurs before the
pagination loop. If this call fails:

- Log WARNING: "NVD Source API unavailable — using raw email addresses
  for secondary provider names"
- Proceed with the pagination loop in **degraded mode**
- In degraded mode, all Secondary CVSS and CWE entries use the raw
  `source` email address as `provider_name` (e.g.,
  `"secalert@redhat.com"` instead of `"Red Hat"`)
- This is the same fallback already specified in Session 1 ("if Source
  API cache does not contain a mapping, use the raw email address")
- On the next run where the Source API is available, **new** entries
  are stored with the resolved display name. Previously-stored
  degraded-mode entries with the raw email address are NOT overwritten
  (different UPSERT key) — see orphan row caveat below

**Failure conditions triggering degraded mode**: HTTP 5xx, HTTP 429,
HTTP 403, other 4xx, connection timeout, DNS resolution failure. A
single attempt is made (no retry for the Source API — it is
non-critical).

**Rationale**: provider name resolution is cosmetic. The critical data
(vector string, score, version) is independent of the Source API.
Blocking CVE ingestion because a display-name lookup fails is
disproportionate.

**Orphan row caveat**: the UPSERT conflict key is
`(cve_id, provider_name, cvss_version)`. If a degraded-mode run stores
`provider_name = "secalert@redhat.com"` and a subsequent normal run
stores `provider_name = "Red Hat"`, these produce **different keys** —
the degraded-mode row persists as an orphan. Impact: cosmetic only (the
severity resolution cascade picks the highest score regardless of
provider name duplication; the direct-source fetcher independently
writes the canonical row). No cleanup mechanism is provided. Orphan
rows are harmless but accumulate until a future data-quality sweep
removes them.

##### 5. Secondary CVSS Skip Logic — Removed

The cross-fetcher skip logic specified in early Session 1 drafts is
**removed**. The NVD fetcher UPSERTs all CVSS entries (Primary and
Secondary) unconditionally:

- Conflict key: `(cve_id, provider_name, cvss_version)`
- ON CONFLICT DO UPDATE (vector_string, score)
- No DB lookup before insert
- No awareness of other fetchers' data

**Data convergence model**: each fetcher writes what it has.
Last-writer-wins on the UPSERT conflict key. Since all fetchers run on
regular schedules (NVD every 6h, Red Hat every 12h), data converges to
the most recent value within one cycle.

**Rationale**: the skip logic prevented temporary data oscillation (NVD
overwriting a fresher direct-source CVSS score) at the cost of:
per-entry DB queries, cross-fetcher coupling, and specification
complexity. The benefit was marginal — CVSS scores rarely change after
publication, and when they do, any staleness is corrected within one
fetcher cycle (6-12 hours).

##### 6. Data Preservation on Rejection

Already fully specified in `cve-tracking.md` (CVE Rejection Handling,
Child data preservation). No NVD-specific deviation.

Summary: when a CVE transitions to `REJECTED`, all child data
(`CVECVSSAssessment`, `CVECWE`, `CVEAffectedVersion`, etc.) is
**preserved unconditionally**. The ticket auto-transitions to `Ignored`
if in `New` status (with audit event). Tickets in other statuses are
not modified (assignee is notified).

##### 7. Cursor Mechanism

The NVD fetcher uses a **derived cursor** (not an explicit one):

- Cursor value = `started_at` of the most recent `FetcherRun` with
  `status IN ('success', 'partial')` for `sync_nvd_cves`
- The `FetcherRun.cursor` JSONB column remains `NULL` for NVD runs
- No explicit cursor management in `execute()`

This is appropriate because the NVD checkpoint is purely temporal
("I have seen all modifications up to time X"), unlike git-based
fetchers whose checkpoint is a commit SHA.

**`partial` status and cursor advancement**: a run with `status =
partial` (some CVEs failed processing) correctly advances the cursor
because all pages were fully scanned. The per-CVE failures represent
unparseable data (not missed data). Those CVEs will be re-encountered
if/when NVD modifies them again, or can be fetched on-demand via
`fetch_single()`.

### Session 4: `fetch_single()` and Error Handling (COMPLETE)

**Resolves**: G2, G7, G10, G13

**Decides**: OP-F

#### Output: `fetch_single()`, Error Handling, and Class Structure

##### 1. `fetch_single(cve_id)` Specification

**API call**:

```
GET /rest/json/cves/2.0?cveId={cve_id}
```

No date parameters — requests a specific CVE by ID.

**Source API resolution**: before processing the CVE, call
`GET /rest/json/source/2.0` to build the source identifier → display
name mapping. If the Source API call fails, proceed in degraded mode
(raw email addresses as `provider_name` — same fallback as `execute()`).

**Response parsing**: the response has the same structure as the batch
endpoint — `.vulnerabilities[]` array, but with at most one entry.

**`CVENotInSource` conditions**:

- HTTP 200 with `totalResults == 0` → CVE does not exist in NVD

**Rejected CVEs**: a CVE with `vulnStatus == "Rejected"` is processed
normally (extract all available data, upsert via `cve_service`). The
rejection handling is the responsibility of `cve_service.upsert_cve()`,
not of the fetcher.

**Post-upsert reference creation**: after `upsert_cve()` returns an
`UpsertResult`, call `reference_service.upsert_references()` with:
- The NVD source reference URL
  (`https://nvd.nist.gov/vuln/detail/{cve_id}`, title `"NVD"`,
  type `advisory`, source `"sync_nvd_cves"`)
- All upstream references from the CVE response (`.references[]`),
  classified via the CVE Source Tag Mapping in
  [`ticket-references.md`](ticket-references.md#cve-source-tag-mapping)

This is identical to the `execute()` path (Session 1, Table 5).
Without this step, on-demand fetches would not create NVD references.

**Rate limiting**: not applicable. `fetch_single()` performs a single
HTTP request (plus optionally one Source API call). No
`request_delay_seconds` sleep. HTTP 429 is handled by the Celery retry
policy (5s → 10s → 20s).

**Signaling convention**: follows the standard `fetch_single` signaling
convention defined in
[`fetcher-infrastructure.md`](../platform/fetcher-infrastructure.md#fetch_single-signaling-convention).
Returns normally on success (data written via `upsert_cve()` +
`reference_service`), raises `CVENotInSource` when `totalResults == 0`,
propagates other exceptions for Celery retry.

##### 2. Error Handling — `fetch_single()` (on-demand)

| Condition | Retry? | Final status | Action |
|-----------|--------|--------------|--------|
| HTTP 200 with `totalResults >= 1` | — | `success` | Parse CVE, upsert via `cve_service` |
| HTTP 200 with `totalResults == 0` | No | `missing` | Raise `CVENotInSource` |
| HTTP 404 | No | `missing` | Raise `CVENotInSource` (NVD returns HTTP 200 with `totalResults == 0` for missing CVEs; 404 is treated as `CVENotInSource` by convention, consistent with the generic `fetch_single` error categorization) |
| HTTP 429 | Yes (3x) | `failure` | Standard Celery retry (5s → 10s → 20s) |
| HTTP 5xx | Yes (3x) | `failure` | Standard Celery retry |
| Network timeout / DNS / connection refused | Yes (3x) | `failure` | Standard Celery retry |
| HTTP 403, other 4xx (not 429) | No | `failure` | Non-retryable |
| HTTP 200 with unparseable JSON | No | `failure` | Non-retryable — structurally invalid response |

**Partial extraction**: if the response is parseable but individual
data types fail (e.g., one CVSS vector is invalid), the fetcher
extracts what it can and returns normally (`success`). Only
structurally unparseable responses (malformed JSON, missing `.id`)
cause `failure`.

##### 3. Error Handling — `execute()` (periodic batch)

**Page-level failures** (abort the entire run):

| Condition | Action |
|-----------|--------|
| HTTP 429 (after 3 retries on page) | Raise `FetcherError` — run aborts, `status = failure` |
| HTTP 5xx | Raise `FetcherError` — run aborts |
| HTTP 403 or other 4xx (not 429) | Raise `FetcherError` — run aborts (non-retryable infrastructure issue) |
| Network timeout / connection refused | Raise `FetcherError` — run aborts |
| Unparseable page response (malformed JSON envelope) | Raise `FetcherError` — run aborts |

**Per-CVE handling** (within a successfully fetched page):

| Condition | Action | Metric |
|-----------|--------|--------|
| CVE processed successfully (all or partial data extracted) | Upsert via `cve_service` | `record_created` if `UpsertResult.action == "created"`, `record_updated` if `action == "updated"`. If `action == "unchanged"` (UPSERT no-op), no metric is recorded for that CVE |
| CVE structurally non-processable (`.id` absent, entry-level malformed JSON) | Log ERROR, skip CVE | `record_failed()` |

**Partial extraction model** (within a processable CVE):

| Missing/malformed data | Behavior | Log level |
|---|---|---|
| English description absent | `description = None` in payload, proceed | None |
| CVSS vector invalid (single entry) | Skip that CVSS entry, proceed | WARNING |
| CWE value doesn't match `^CWE-[1-9][0-9]*$` | Skip that value, proceed | None (expected NVD placeholders) |
| `configurations` absent | `cpe_matches = None`, proceed | None |
| `weaknesses` absent | No CWE entries produced, proceed | None |
| `references` absent | No references upserted, proceed | None |
| `metrics` absent | No CVSS entries produced, proceed | None |

A CVE with partial data counts as `record_created` or
`record_updated` — NOT as `record_failed()`. The only condition that
produces `record_failed()` is structural non-processability.

##### 4. Sanitized Error Messages

Per `fetcher-infrastructure.md` requirement, the fetcher produces these
sanitized `FetcherError` messages for page-level abort conditions:

| Failure mode | `FetcherError` message |
|---|---|
| Connection error | `"Failed to connect to NVD API"` |
| HTTP 5xx | `"NVD API returned HTTP {status_code}"` |
| HTTP 403 or other client error | `"NVD API returned HTTP {status_code}"` |
| HTTP 429 (retries exhausted) | `"NVD API rate limit exceeded — retries exhausted"` |
| Request timeout | `"NVD API request timed out"` |
| Unparseable page response | `"NVD API returned unparseable response"` |

Per-CVE failures are logged at ERROR level (internal) but do not
produce `FetcherError` messages (the run does not abort).

##### 5. Class Structure

```python
class SyncNvdCves(BaseFetcher):
    name = "sync_nvd_cves"
    cve_source_type = "nvd"
    description = "Sync CVEs from NVD REST API v2"
    default_schedule = "0 */6 * * *"

    class Settings(BaseModel):
        request_delay_seconds: float = Field(
            default=6.0, ge=0.1, le=30.0,
            description="Delay between consecutive NVD API requests.",
        )
        results_per_page: int = Field(
            default=2000, ge=100, le=2000,
            description="Number of CVE records per API page.",
        )

    source_reference_url_pattern = (
        "https://nvd.nist.gov/vuln/detail/{cve_id}"
    )

    async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:
        """Fetch a single CVE from the NVD REST API v2.

        GET /rest/json/cves/2.0?cveId={cve_id}

        Calls Source API to resolve secondary provider display names.
        Upserts CVE data via cve_service, then creates/updates references
        via reference_service.
        Raises CVENotInSource if totalResults == 0.
        Rejected CVEs are processed normally (extraction + upsert).
        """
        ...

    async def execute(self, session: AsyncSession) -> None:
        """Periodic batch: time-window pagination.

        1. Derive last_sync (first run / >120d gap → early return)
        2. Compute window (started_at - 15min → now)
        3. Fetch Source API cache (degraded mode on failure)
        4. Pagination loop (abort on page failure)
        5. Phase 2 side effects (package resolution, notifications)
        """
        ...

    # catch_up(ticket_id) — inherited from BaseFetcher default:
    #   extracts cve_id from ticket → calls self.fetch_single(cve_id)
```

##### 6. Updated Properties Table

| Property | Value |
|----------|-------|
| Fetcher name | `sync_nvd_cves` |
| Class name | `SyncNvdCves` |
| `cve_source_type` | `"nvd"` |
| Schedule | Every 6 hours (`0 */6 * * *`) |
| Source | NVD REST API v2 (`services.nvd.nist.gov/rest/json/cves/2.0`) |
| Scope | All CVEs modified since the last successful run |
| Auth | API key (optional; required for higher rate limits) |
| Custom settings | Yes (see Custom Settings) |
| `fetch_single()` | Yes — NVD REST API v2 single CVE query |
| `source_reference_url_pattern` | `"https://nvd.nist.gov/vuln/detail/{cve_id}"` |

---

## Application Plan

When all sessions are complete, apply the following changes **in order**.
Each item specifies the exact file, location, and content to write.

### Step 1: Replace `sync_nvd_cves` section in `cve-tracking.md`

**File**: `docs/features/tickets/cve-tracking.md`

**Action**: Replace lines 405-512 (from `### Fetcher: sync_nvd_cves` to
the line before `### Fetcher: sync_mitre_cves`) with the complete new
specification assembled from sessions 1-4.

**New content structure** (assembled from session outputs, in this exact
order):

1. **Properties Table** — Session 4, §6 (Updated Properties Table)
2. **Class Structure** — Session 4, §5 (pseudo-code with docstrings)
3. **Custom Settings** — Session 2 (settings table + operational notes)
4. **Algorithm** — Session 2 (Revised Algorithm Steps 1–5)
   - NVD Date Format (sub-section from Session 2)
   - Rate Limiting (sub-section from Session 2)
5. **Field Mapping** — Session 1 (Tables 1–7 in order: Global CVE
   fields, CVSS metrics, CWE/weaknesses, CPE configurations,
   References, Explicitly ignored fields, CVSS deduplication rules)
6. **NVD Source API Caching** — existing text (lines 470–477), retained
   as-is
7. **First Run and >120-day Gap Handling** — Session 3, §1
8. **Overlap Buffer** — Session 3, §2
9. **Cursor Mechanism** — Session 3, §7
10. **`fetch_single(cve_id)`** — Session 4, §1 (API call, Source API
    resolution, response parsing, CVENotInSource conditions, post-upsert
    reference creation, signaling convention)
11. **Error Handling** — combined from Session 4, §2–§4:
    - `fetch_single()` error table (Session 4, §2)
    - `execute()` page-level failures (Session 4, §3)
    - Per-CVE handling + Partial extraction model (Session 4, §3)
    - Sanitized error messages (Session 4, §4)
12. **NVD Source API Failure Handling (Degraded Mode)** — Session 3, §4
    (including orphan row caveat)
13. **Secondary CVSS Skip Logic — Removed** — Session 3, §5 (rationale
    for removal; explains deviation from current spec step 3e)
14. **Data Preservation on Rejection** — Session 3, §6
    (cross-reference to existing shared section)

### Step 1b: Update "Common First Run Behavior" in `cve-tracking.md`

**File**: `docs/features/tickets/cve-tracking.md`

**Action**: Update the NVD bullet in the "Common First Run Behavior"
section (line 387-388) to match the cursor-only strategy.

**Changes**:

- **Before**: `sync_nvd_cves: fetches CVEs modified in the last 7 days
  (now - 7 days) as a bootstrap window, then proceeds incrementally`
- **After**: `sync_nvd_cves: records the current timestamp as cursor
  (via started_at) without fetching any data`

### Step 1c: Add "Common CVE Fetcher Metrics" section to `cve-tracking.md`

**File**: `docs/features/tickets/cve-tracking.md`

**Action**: Add a new section after "Common First Run Behavior"
(line ~403) and before `### Fetcher: sync_nvd_cves`:

```markdown
### Common CVE Fetcher Metrics

Unless otherwise specified per-fetcher, CVE fetchers use these metric
definitions:

- `record_created`: a new CVE record was inserted (first time seen from
  this source)
- `record_updated`: an existing CVE record was updated (metadata, CVSS
  assessments, CWE, references, or other enrichment data changed). If
  `upsert_cve()` produces no changes (all upserts are no-ops), no
  metric is recorded for that CVE
- `record_failed`: a CVE could not be processed (structural parse
  error, unrecognized field values, or database constraint violation)

Individual fetcher sections below document only deviations from these
definitions.
```

Additionally, remove the per-fetcher `#### Metrics` sections that are
now redundant (they add no information beyond the common section):

- `sync_nvd_cves`: omit `#### Metrics` when assembling the new section
  (no deviation from common)
- `sync_mitre_cves` (lines 779-784): delete
- `sync_kernel_cves` (lines 1097-1102): delete
- `sync_redhat_cves`: **KEEP** (documents deviations: `record_created`
  = N/A, detailed `record_updated` semantics)

### Step 2: Update Fetcher Registry in `data-sources.md`

**File**: `docs/data-sources.md`

**Action**: No change needed — the Fetcher Registry table columns
(Fetcher, Source, Schedule, Auth, Rate Limits, Data Ingested, Spec,
Spec Status) are all already correct for `sync_nvd_cves`. The table
does not have a "Custom settings" column, so the addition of custom
settings does not affect the registry. Step is a no-op.

### Step 3: Update `CVEIngestPayload` in `cve-service.md` (if needed)

**File**: `docs/features/tickets/cve-service.md`

**Action**: No change needed. Session 1 field mapping confirms that all
NVD fields map to existing `CVEIngestPayload` fields:
- Global fields → `cve_id`, `published_date`, `modified_date`,
  `cve_state`, `description` (all present)
- CVSS → `cvss_assessments: list[CVSSAssessmentEntry]` (present)
- CWE → `cwe_classifications: list[CWEEntry]` (present)
- CPE → `cpe_matches: list[CPEMatchEntry]` (present)
- References → handled post-upsert via `reference_service` (not in
  payload by design)

**Changes**: NO CHANGE NEEDED

### Step 4: Update `configuration.md`

**File**: `docs/configuration.md`

**Action**: Add a brief cross-reference to the `NVD_API_KEY` row in the
"External APIs" table, pointing operators toward the fetcher's
`request_delay_seconds` custom setting.

**Changes**: Replace the `NVD_API_KEY` row's Description cell:

- **Before**: `NVD API key for higher rate limits on CVE fetching`
- **After**: `NVD API key for higher rate limits on CVE fetching. When configured, consider reducing the sync_nvd_cves fetcher's request_delay_seconds custom setting from 6.0s to ~0.6s via the admin dashboard`

No new environment variables are introduced — `request_delay_seconds`
and `results_per_page` are custom settings (stored in
`FetcherConfig.custom_settings` JSONB, managed via admin dashboard).

### Step 4b: Fix pre-existing inaccuracies in `cvss-scoring.md` (versions and sync scope)

**File**: `docs/features/tickets/cvss-scoring.md`

**Action 1** (T3 — NVD Primary section, lines 136-138): Replace:

- **Before**:
  ```
  - **CVSS versions**: currently v3.1; v4.0 expected in the future
  - **Fetch mechanism**: extracted from `cvssMetricV31` and `cvssMetricV40`
    arrays in the NVD CVE API response
  ```
- **After**:
  ```
  - **CVSS versions**: v2.0, v3.0, v3.1, and v4.0 (all metric arrays
    present in the NVD API response are extracted)
  - **Fetch mechanism**: extracted from `cvssMetricV2`, `cvssMetricV30`,
    `cvssMetricV31`, and `cvssMetricV40` arrays in the NVD CVE API
    response
  ```

**Action 2** (C3 — Sync Scope section, lines 349-356): Replace:

- **Before**:
  ```
  CVSS sync (both NVD incremental and Red Hat re-fetch) is performed only
  for CVEs with **active tickets** — tickets in status `New`, `Analysis`, or
  `Analyzed` (see `docs/data-model.md` for the authoritative definition of
  active tickets).

  When a ticket transitions to `Resolved`, `Ignored`, or `Duplicated`, Sentinel
  stops monitoring CVSS updates for that CVE. The existing CVSS data remains
  in the database but is no longer refreshed.
  ```
- **After**:
  ```
  CVSS sync scope varies by fetcher:

  - **NVD** (`sync_nvd_cves`): global scope — fetches all CVEs modified
    in the time window, regardless of ticket status. Persistence is
    unrestricted (consistent with the Data Convention below)
  - **Red Hat** (`sync_redhat_cves`): scoped to CVEs with **active
    tickets** — tickets in status `New`, `Analysis`, or `Analyzed` (see
    `docs/data-model.md` for the authoritative definition of active
    tickets). This restriction exists because the Red Hat API requires
    per-CVE lookups (no bulk/incremental endpoint)

  When a ticket transitions to `Resolved`, `Ignored`, or `Duplicated`,
  Red Hat CVSS sync stops monitoring that CVE. NVD data continues to be
  persisted regardless of ticket status (time-window-based fetching is
  independent of ticket lifecycle). In both cases, existing CVSS data
  remains in the database. If the ticket is later reopened, the
  recalculation chain re-derives severity and eligibility from the
  current `CVECVSSAssessment` records (which may have been updated by
  NVD in the interim).
  ```

### Step 4c: Fix pre-existing inaccuracy in `cvss-scoring.md` (deduplication model)

**File**: `docs/features/tickets/cvss-scoring.md`

**Action** (lines 155-157 — "CNA (via NVD Secondary)" section): Replace:

- **Before**:
  ```
  - **Deduplication with direct sources**: if a direct source (e.g., Red Hat)
    provides an assessment for the same provider and CVSS version, the direct
    source takes priority and overwrites the NVD Secondary data
  ```
- **After**:
  ```
  - **Convergence with direct sources**: both NVD Secondary and direct-source
    fetchers (e.g., Red Hat) write to the same UPSERT conflict key
    `(cve_id, provider_name, cvss_version)` — last-writer-wins. Since direct
    sources run on independent schedules, data converges to the direct-source
    value within one fetcher cycle. Temporary oscillation (NVD overwriting a
    fresher direct-source score between cycles) is transient and harmless —
    CVSS scores rarely change after publication
  ```

**Rationale**: Session 3, §5 removed the cross-fetcher skip logic that
previously guaranteed direct-source priority. The new model is
"last-writer-wins via UPSERT" with convergence within one cycle. The old
phrasing ("takes priority and overwrites") implied an explicit priority
mechanism that no longer exists.

### Step 4d: Note on `sync_osv_advisories` Metrics section

**File**: `docs/features/tickets/cve-tracking.md`

**Action**: No change. The `sync_osv_advisories` `#### Metrics` section
(line 1124, currently "TBD") is intentionally left as-is. It is a
placeholder for a fetcher that has not been fully specified yet. When
`sync_osv_advisories` is specified, its Metrics section will either be
removed (if it follows the common pattern) or kept (if it documents
deviations), per the same rule applied to other fetchers in Step 1c.

### Step 5: Run reviewers and address findings

**Action**: After steps 1-4 are applied:

- Run `@spec-coherence-reviewer` on updated `cve-tracking.md`
- Run `@spec-gap-analyzer` on updated `cve-tracking.md`
- Run `@docs-placement-reviewer` if cross-cutting rules were introduced
- Address any "Needs revision" findings before considering the work complete

### Step 6: Delete this draft

**File**: `docs/drafts/nvd-fetcher-spec-completion.md`

**Action**: Delete this file. All decisions and specifications have been
applied to `cve-tracking.md` and other target files. The draft has no
residual value — the authoritative specification lives in
`docs/features/tickets/cve-tracking.md`.

---

## Progress Tracker

| Session | Status | Date | Notes |
|---------|--------|------|-------|
| 1 — Field Mapping | **Complete** | 2026-06-16 | 7 tables produced. G1, G6, G15, G16 resolved |
| 2 — Pagination & Rate Limiting | **Complete** | 2026-06-16 | Algorithm revised, custom settings defined, rate limiting specified. G3, G4, G14 resolved |
| 3 — Recovery & Edge Cases | **Complete** | 2026-06-16 | No sub-window recovery (YAGNI). First run = cursor only. Page failure = abort. Skip logic removed. G5, G8, G9, G11, G12 resolved |
| 4 — fetch_single & Errors | **Complete** | 2026-06-16 | fetch_single specified, error tables complete, partial extraction model, class structure. G2, G7, G10, G13 resolved |

## Resolved Open Points

| ID | Resolution | Date |
|----|-----------|------|
| OP-C | **Yes** — extract CWE. Primary (type=Primary) → source `"NVD"`. Secondary (type=Secondary) → resolve email via Source API. Skip `NVD-CWE-Other`/`NVD-CWE-noinfo` | 2026-06-16 |
| OP-D | **Yes** — extract CVSS v2 from `cvssMetricV2[]`. Version derived from vector prefix `"AV:..."` → `"2.0"` | 2026-06-16 |
| OP-E | **Yes** — extract CVSS v4.0 from `cvssMetricV40[]`. Version derived from vector prefix `"CVSS:4.0/..."` → `"4.0"` | 2026-06-16 |
| OP-G | **No** — ignore `cveTags`. Informational metadata (disputed, unsupported-when-assigned, exclusively-hosted-service) with no operational impact. Both NVD and MITRE provide in structured form; can be added later if needed | 2026-06-16 |
| OP-H | **Defer** — CISA KEV fields in NVD response (`cisaExploitAdd`, etc.) not extracted. `sync_mitre_cves` already populates `kev_data` from CISA-ADP container (authoritative source) | 2026-06-16 |
| OP-I | **Skip** — `NVD-CWE-Other` and `NVD-CWE-noinfo` are NVD placeholder values, not real CWE identifiers. Do not match `^CWE-[1-9][0-9]*$` pattern | 2026-06-16 |
| OP-J | **NULL** — NVD does not provide a title field. `CVEIngestPayload.title` left as `None` | 2026-06-16 |
| OP-A | **Yes** — `request_delay_seconds` custom setting added. Default 6.0s (safe for no-API-key mode: 5 req/30s). Operator reduces to ~0.6s when `NVD_API_KEY` is configured (50 req/30s) | 2026-06-16 |
| OP-K | **No auto-adjust** — single configurable value. The fetcher does not detect API key presence. Operator tunes `request_delay_seconds` manually via admin dashboard. Consistent with `sync_redhat_cves` pattern (`throttle_delay_seconds`) | 2026-06-16 |
| OP-L | **2000** — `results_per_page` custom setting, default 2000 (NVD maximum), range 100–2000. Configurable for debugging but no operational reason to reduce | 2026-06-16 |
| OP-B | **First-run cursor only**. No sub-window recovery. First run and >120d gap: set cursor to `now`, zero records. YAGNI — scenario is operationally unrealistic. Individual CVEs recoverable via `fetch_single()` | 2026-06-16 |
| OP-M | **15 minutes hardcoded**. `window_start = started_at - 15min`. No configurable setting. UPSERT idempotency guarantees safe overlap re-processing | 2026-06-16 |
| OP-N | **N/A** — eliminated by OP-B (no sub-windows exist, no per-window metrics needed) | 2026-06-16 |
| OP-F | **Yes** — `"https://nvd.nist.gov/vuln/detail/{cve_id}"`. Standard NVD page for each CVE | 2026-06-16 |
