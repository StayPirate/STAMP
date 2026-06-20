# CISA KEV Fetcher — Draft Plan

Status: **Draft** — Working document for multi-session spec development.

## Objective

Complete the `sync_cisa_kev` fetcher specification
(`docs/features/tickets/cve-sync-kev.md`) to full compliance with the fetcher
documentation requirements defined in
`docs/features/platform/fetcher-infrastructure.md`. This plan covers only
specification work — no implementation.

## Current State

The following artifacts exist:

| Artifact | Location | Completeness |
|----------|----------|-------------|
| Fetcher spec stub | `docs/features/tickets/cve-sync-kev.md` | ~10% — name, class, source type only; all sections TBD |
| Data model | `docs/data-model.md` (`CVEKEVEntry`) | Partial — has `remediation_deadline` (to be removed) |
| `CVEIngestPayload` | `docs/features/tickets/cve-service.md` (`KEVEntry`) | Partial — has `remediation_deadline` (to be removed) |
| Data source description | `docs/data-sources.md` (CISA KEV section) | Complete |
| Fetcher Registry entry | `docs/data-sources.md` (table row) | Stub — TBD schedule, TBD status |
| Naming/hierarchy | `docs/features/platform/fetcher-infrastructure.md` | Defined — source type `"kev"`, catch-up = False |
| MITRE KEV extraction | `docs/features/tickets/cve-sync-mitre.md` | Complete — extracts `kev_data.date_added`, `kev_data.reference_url` from CISA-ADP |

## Feed Structure (from live analysis)

Source:
`https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json`

```json
{
  "title": "CISA Catalog of Known Exploited Vulnerabilities",
  "catalogVersion": "2026.06.18",
  "dateReleased": "2026-06-18T16:00:20.1905Z",
  "count": 1623,
  "vulnerabilities": [
    {
      "cveID": "CVE-2026-20253",
      "vendorProject": "Splunk",
      "product": "Enterprise",
      "vulnerabilityName": "Splunk Enterprise Missing Authentication...",
      "dateAdded": "2026-06-18",
      "shortDescription": "Splunk Enterprise contains...",
      "requiredAction": "Apply mitigations in accordance...",
      "dueDate": "2026-06-21",
      "knownRansomwareCampaignUse": "Unknown",
      "notes": "https://advisory.splunk.com/... ; BOD 26-04: https://...",
      "cwes": ["CWE-306"]
    }
  ]
}
```

Key observations:

- Single JSON file (~1.5MB), always contains the **entire** catalog (~1600
  entries)
- No authentication, no rate limits, no pagination
- `cwes` field is a recent addition (array of CWE-ID strings); can be empty
  `[]` on older entries; can have multiple values (e.g., `["CWE-787", "CWE-125"]`)
- `notes` contains semicolon-separated URLs and context text
- `knownRansomwareCampaignUse`: only `"Known"` or `"Unknown"` observed
- `dueDate` is the FCEB (Federal Civilian Executive Branch) remediation
  deadline from BOD 22-01/26-04 — US-federal-specific, not relevant for SUSE

## Gap Analysis

### GAP-1: Data Model — `remediation_deadline` to be removed

The current `CVEKEVEntry` table has a `remediation_deadline` column that stores
the FCEB deadline (`dueDate` from feed). This field:

- Is relevant only to US federal agencies (FCEB)
- Is not available in the MITRE CISA-ADP container (creates merge asymmetry)
- Is not used by any Sentinel logic or display

Decision: **remove** `remediation_deadline` from `CVEKEVEntry` and `KEVEntry`.
The resulting table has only: `cve_id`, `date_added`, `reference_url`.

### GAP-2: `KEVEntry` in `CVEIngestPayload` — Alignment

After GAP-1 resolution, `KEVEntry` has: `date_added`, `reference_url`. This
matches exactly what MITRE already writes. The overlap is complete — no merge
conflict between MITRE and KEV regardless of execution order.

### GAP-3: Fetcher spec — All mandatory sections missing

Per the "Fetcher Documentation Requirements" in `fetcher-infrastructure.md`:

- **Properties table**: incomplete (Schedule, Scope, Custom settings = TBD)
- **Algorithm**: entirely missing
- **Error Handling**: entirely missing
- **Metrics**: entirely missing
- **Class Structure**: missing
- **Field Mapping**: missing
- **Explicitly Ignored Fields**: missing

Note: `fetch_single()` Behavior section is NOT needed — this fetcher opts out
via `supports_fetch_single = False` (see WI-NEW-1).

### GAP-4: Scope — Enrichment-only

Resolved: the fetcher is enrichment-only. CVEs not present in the database are
silently skipped (no log, no `ensure_cve_exists()`). The periodic run every 6h
ensures that CVEs ingested by NVD/MITRE between runs will be enriched on the
next cycle.

### GAP-5: MITRE/KEV overlap — Resolved

After dropping `remediation_deadline`, both MITRE and KEV write the same two
fields (`date_added`, `reference_url`) with identical values. The overlap is
complete — last-writer-wins produces the same result regardless of execution
order. No merge conflict, no temporal gap, no authority ambiguity.

### GAP-6: Source reference URL — Standard mechanism

The CISA KEV catalog is a single page, but it supports per-CVE filtering via
query parameter:
`https://www.cisa.gov/known-exploited-vulnerabilities-catalog?field_cve={cve_id}`

This URL is set as `source_reference_url_pattern` on the class. Two separate
mechanisms store it:

1. **CVE-level data** (`CVEKEVEntry.reference_url`): stored via
   `KEVEntry.reference_url` in the `CVEIngestPayload` passed to `upsert_cve()`
2. **Ticket-level link** (`TicketReference`): created by an explicit
   `reference_service.upsert_references()` call **after** `upsert_cve()`,
   passing the constructed URL as `source_url`. This is the fetcher's
   responsibility (not automated by `upsert_cve()` or `BaseCVEFetcher`)

The `(ticket_id, url)` unique constraint on `TicketReference` prevents
duplicates if MITRE wrote the same URL first (MITRE uses the identical URL
pattern from the CISA-ADP `kev.content.reference` field).

### GAP-7: CWE from KEV feed

Resolved: extract `cwes` array with `source = "CISA KEV"`. Validation:
`^CWE-[1-9][0-9]*$`. Invalid entries skipped with WARNING log.

### GAP-8: `notes` field — Ignored

Decision: do not store `notes` and do not extract URLs from it. The only
`TicketReference` created is the constructed CISA KEV catalog URL (GAP-6).

### GAP-9: Fetcher Registry — To be updated

Will be updated with final values (schedule, data ingested, spec status).

### GAP-10: `catch_up` and `fetch_single` behavior

Resolved via new `supports_fetch_single = False` pattern (see WI-NEW-1).
The fetcher:
- Sets `participates_in_catch_up = False` (already defined)
- Sets `supports_fetch_single = False` (new pattern)
- Has no `fetch_single()` override — uses the `BaseCVEFetcher` default
  (which raises `RuntimeError` if called, as a safety net)

## Work Items

### WI-1: Data Model Change — Remove `remediation_deadline`

**Priority**: HIGH

**Action**: Remove the `remediation_deadline` column from `CVEKEVEntry` in
`docs/data-model.md`.

**Action**: Remove the `remediation_deadline` row from the existing table. The
remaining data columns are:

| Column | Type | Constraints | Source |
|--------|------|-------------|--------|
| `id` | UUID | PK | Internal identifier |
| `cve_id` | UUID | FK(cve.id) ON DELETE CASCADE, UNIQUE, NOT NULL | `cveID` (lookup) |
| `date_added` | DATE | NOT NULL | `dateAdded` |
| `reference_url` | TEXT | nullable | Constructed: `https://www.cisa.gov/known-exploited-vulnerabilities-catalog?field_cve={cve_id}` |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Standard |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Standard |

**Deliverable**: Update `docs/data-model.md`.

### WI-2: `KEVEntry` in `CVEIngestPayload` — Remove `remediation_deadline`

**Priority**: HIGH

**Action**: Remove the `remediation_deadline` field from `KEVEntry` in
`docs/features/tickets/cve-service.md`.

**Final `KEVEntry`**:

```python
class KEVEntry(BaseModel):
    date_added: date
    reference_url: str | None = Field(None, max_length=2048)
```

**Deliverable**: Update `docs/features/tickets/cve-service.md`.

### WI-2B: Add `"kev"` to CVESourceType Enum

**Priority**: HIGH

**Action**: Add `"kev"` to the `CVESourceType` values list in
`docs/data-model.md`. The `BaseCVEFetcher.__init_subclass__` validation
requires `cve_source_type` to be a member of `CVESourceType` — the enum must
be updated before the fetcher class can be registered.

Also add `"epss"` in the same edit — it is already declared by
`cve-sync-epss.md` but missing from the `CVESourceType` table.

**Final entries** (insert after `"ghsa"`):

| Value | Description |
|-------|-------------|
| `kev` | CISA Known Exploited Vulnerabilities catalog |
| `epss` | FIRST EPSS (Exploit Prediction Scoring System) |

**Deliverable**: Update `docs/data-model.md` CVESourceType table.

### WI-3: Fetcher Algorithm

**Priority**: HIGH

**Final algorithm for `execute()`**:

1. Download the full KEV JSON file via HTTP GET (timeout: 30 seconds)
2. Validate top-level structure:
   - `vulnerabilities` key must be present and be a list
   - Log `count` for observability (do not abort if mismatched)
3. For each entry in `vulnerabilities`:
   a. Validate `cveID` format (`^CVE-[0-9]{4}-[0-9]{4,}$`)
   b. Look up the CVE by `cveID` in the database
   c. If CVE does NOT exist: **skip silently** (no log, no creation)
   d. If CVE exists:
      - Build `CVEIngestPayload` with:
        - `kev_data`: `KEVEntry(date_added=entry.dateAdded, reference_url=constructed_url)`
        - `cwe_classifications`: extracted from `entry.cwes` (if present and
          non-empty), with `source = "CISA KEV"`
      - Call `upsert_cve(payload)` → receive `UpsertResult`
      - Call `reference_service.upsert_references()` with:
        - `ticket_id`: from `UpsertResult.ticket_id`
        - `source`: `"sync_cisa_kev"`
        - `source_url`: `source_reference_url_pattern.format(cve_id=cve_id)`
        - `upstream_references`: `[]` (KEV feed has no reference URLs)
      - `record_updated()` on success
   e. On per-entry error (after successful CVE lookup):
      `record_source_status(session, cve_id, "kev", "failure")`,
      `record_failed()`, log error, continue
   f. On per-entry error (before CVE lookup or lookup failure):
      `record_failed()`, log error, continue (no `record_source_status` —
      CVE UUID unavailable)
4. End

**Per-entry isolation**: each entry (steps 3a–3f) is processed
independently with per-entry error isolation. A failure at entry N does
not affect entries 1..N-1. Transaction boundaries are managed internally
by `upsert_cve()` (which acquires its own `FOR UPDATE` lock per CVE).

**Behavioral notes** (to be included in the spec):

- **Data lifecycle**: KEV entries are never deleted from Sentinel. If CISA
  removes a CVE from the catalog, its `CVEKEVEntry` record persists as
  historical enrichment. The fetcher only creates/updates — no removal
  mechanism.
- **Re-invocation safety**: the fetcher is stateless; full catalog
  re-processing is idempotent via `ON CONFLICT DO UPDATE` in `upsert_cve()`.
  A crash mid-run followed by a retry produces the same final state.
- **Empty catalog**: a valid JSON response with `"vulnerabilities": []`
  passes validation, the loop iterates zero times, and the run completes
  with `status = success` and zero metrics.
- **First-run on empty DB**: all entries are silently skipped (no CVEs exist
  yet); run succeeds with zero metrics. This is expected for an
  enrichment-only fetcher — CVEs must be ingested by NVD/MITRE first.

**Deliverable**: Write the Algorithm section in `cve-sync-kev.md`.

### WI-4: Schedule

**Priority**: MEDIUM

**Decision**: Every 6 hours — `0 1,7,13,19 * * *` (01:00, 07:00, 13:00,
19:00 UTC).

Rationale:

- Aligned with NVD/MITRE frequency (every 6h)
- Staggered from Red Hat (03:00 UTC) and MITRE/NVD common slots
- The catalog is small (~1.5MB), so frequent full sync is cheap
- Ensures KEV enrichment is available within 6h of CVE ingestion

**Deliverable**: Set in properties table of `cve-sync-kev.md`.

### WI-5: CANCELLED — Reference URL Extraction from `notes`

Decision: `notes` field is ignored entirely. No URL extraction. The only
`TicketReference` created is the constructed CISA KEV catalog URL.

### WI-6: CWE Extraction

**Priority**: MEDIUM

**Final approach**:

1. For each entry in `cwes` array, validate against `^CWE-[1-9][0-9]*$`
2. Create `CWEEntry(cwe_id=value, source="CISA KEV")`
3. Include in `CVEIngestPayload.cwe_classifications`
4. Invalid entries: skip with WARNING log
5. Empty `cwes` array (`[]`): no CWE entries created (no log)

**Deliverable**: Document in the algorithm and field mapping sections.

### WI-7: CANCELLED — `fetch_single()` Behavior

Replaced by WI-NEW-1 (`supports_fetch_single` spec change). The KEV fetcher
does not implement `fetch_single()`.

### WI-8: Error Handling

**Priority**: HIGH

**`execute()` errors only** (no `fetch_single()` section needed):

| Failure mode | Severity | Action |
|---|---|---|
| Connection error / DNS failure | Fatal | Raise `FetcherError`, abort run |
| HTTP 4xx/5xx | Fatal | Raise `FetcherError`, abort run |
| Request timeout | Fatal | Raise `FetcherError`, abort run |
| Unparseable JSON response | Fatal | Raise `FetcherError`, abort run |
| Missing `vulnerabilities` key | Fatal | Raise `FetcherError`, abort run |
| Invalid `cveID` format on entry | Isolated | `record_failed()`, skip entry, continue (no CVE UUID available) |
| CVE lookup database error | Isolated | `record_failed()`, skip entry, continue (no CVE UUID available) |
| Invalid/missing `dateAdded` on entry | Isolated | `record_source_status("failure")`, `record_failed()`, skip entry, continue |
| `upsert_cve()` failure on entry | Isolated | `record_source_status("failure")`, `record_failed()`, log, continue |
| `upsert_references()` failure on entry | Isolated | WARNING log, continue (CVE data already committed; reference failure is non-critical) |
| Invalid CWE format on entry | Isolated | Skip that CWE, WARNING log, continue processing entry |

**Abort threshold**: no source-specific abort threshold is defined. After
a successful JSON download, all per-entry failures are local (database
errors, parse errors). The shared Celery task timeout limits maximum run
duration. The fetcher dashboard surfaces partial-status runs with high
`items_failed` counts for operator attention.

**`record_source_status` precondition**: called only after the CVE UUID
has been successfully resolved (step 3b succeeds). Errors that occur
before or during CVE lookup (invalid `cveID` format, database error in
lookup) trigger only `record_failed()` + log — `record_source_status`
requires a valid `cve_id` UUID (FK constraint).

**`record_source_status` self-failure**: if `record_source_status` itself
fails (e.g., database connection lost mid-run), the exception propagates
and aborts the run. This is reasonable: a database connection failure
means all subsequent entries would also fail, so continuing the loop
provides no value. The run terminates with `status = failure`.

**Sanitized `FetcherError` messages**:

| Failure mode | Message |
|---|---|
| Connection error | `"Failed to connect to CISA KEV feed"` |
| HTTP 4xx/5xx | `"CISA KEV feed returned HTTP {status_code}"` |
| Unparseable JSON | `"CISA KEV feed returned unparseable response"` |
| Missing `vulnerabilities` key | `"CISA KEV feed has unexpected structure"` |

**Deliverable**: Write error handling section in `cve-sync-kev.md`.

### WI-9: Metrics

**Priority**: MEDIUM

| Metric | When |
|--------|------|
| `record_created` | Never used (N/A) — enrichment-only fetcher |
| `record_updated` | Per CVE entry where `upsert_cve()` succeeds |
| `record_failed` | Per entry on parse/upsert failure |

Note: a single entry that upserts both KEV data and CWE counts as one
`record_updated` (not two).

**Metric semantics**: `record_updated` fires on every successful
`upsert_cve()` call with "processed" semantics — consistent with all CVE
fetchers (NVD, MITRE, Red Hat, GHSA, OSV). This means ~1600
`record_updated` per run even when no data changed. Change-detection
semantics are deferred as a cross-cutting concern (see OP-12 in
`docs/drafts/open-points.md`).

**Deliverable**: Write Metrics section in `cve-sync-kev.md`.

### WI-10: MITRE/KEV Overlap — Resolved (No Conflict)

**Priority**: LOW (documentation only)

After dropping `remediation_deadline`, the overlap between MITRE and KEV is
**complete**: both write `date_added` and `reference_url` with identical values.
Last-writer-wins produces the same result regardless of order. No special merge
logic or authority hierarchy needed.

The `TicketReference` with the CISA KEV URL is also safe: the `(ticket_id, url)`
unique constraint deduplicates. If MITRE wrote it first (with
`source = "sync_mitre_cves"`), the KEV fetcher's upsert hits the "existing URL,
different source" rule and only fills NULL fields — no duplication.

**Deliverable**: Brief note in the fetcher spec documenting the overlap as
benign.

### WI-11: Source Reference URL

**Priority**: MEDIUM

**Decision**: `source_reference_url_pattern = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?field_cve={cve_id}"`
on the class. The fetcher constructs the URL per-CVE and passes it to
`reference_service.upsert_references()`:

```
https://www.cisa.gov/known-exploited-vulnerabilities-catalog?field_cve={cve_id}
```

This URL is stored in two independent places:
1. **CVE-level**: `CVEKEVEntry.reference_url` (via `KEVEntry.reference_url` in
   the payload passed to `upsert_cve()`)
2. **Ticket-level**: `TicketReference` record (via explicit
   `reference_service.upsert_references()` call post-upsert, with
   `source_url` built from `source_reference_url_pattern`)

**Deliverable**: Document in the algorithm and class structure sections.

### WI-12: Custom Settings

**Priority**: LOW

**Decision**: No custom settings. The feed URL is fixed, authentication is
not required, and there are no rate limits. HTTP request timeout is 30
seconds (sufficient for ~1.5MB download; prevents indefinite hang if
CISA infrastructure is degraded).

**Deliverable**: Document "Custom settings: None" and "HTTP timeout: 30s"
in properties table.

### WI-13: Explicitly Ignored Fields

**Priority**: MEDIUM

| Feed field | Reason |
|---|---|
| `vendorProject` | CISA vendor classification; Sentinel uses NVD/MITRE CPE for vendor/product taxonomy |
| `product` | Same as above |
| `vulnerabilityName` | CISA's short title; NVD/MITRE are authoritative for CVE titles |
| `shortDescription` | CISA-authored description; NVD/MITRE are authoritative for CVE descriptions |
| `requiredAction` | FCEB remediation guidance; not actionable for SUSE VAs |
| `dueDate` | FCEB remediation deadline (BOD 22-01/26-04); relevant only to US federal agencies |
| `knownRansomwareCampaignUse` | Binary triage signal (`"Known"`/`"Unknown"`); low value without broader threat intelligence context |
| `notes` | Semicolon-separated URLs and text; vendor advisory URLs are already available from NVD/MITRE references |
| `catalogVersion` | Feed-level metadata, not per-CVE data |
| `dateReleased` | Feed-level metadata |
| `count` | Feed-level metadata; used only for observability log, not for logic |
| `title` | Feed-level metadata (static string) |

**Deliverable**: Write Explicitly Ignored Fields section in `cve-sync-kev.md`.

### WI-14: Class Structure

**Priority**: MEDIUM

```python
class SyncCisaKev(BaseCVEFetcher):
    name = "sync_cisa_kev"
    cve_source_type = "kev"
    description = "Sync Known Exploited Vulnerabilities from CISA KEV catalog"
    default_schedule = "0 1,7,13,19 * * *"  # Every 6h
    participates_in_catch_up = False
    supports_fetch_single = False
    source_reference_url_pattern = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?field_cve={cve_id}"

    # No fetch_single() override — uses BaseCVEFetcher default (RuntimeError
    # safety net). This fetcher is excluded from get_fetch_single_fetchers()
    # by the supports_fetch_single = False attribute.

    async def execute(self, session: AsyncSession) -> None:
        """Download the full KEV catalog and process all entries."""
        ...
```

**Deliverable**: Write class structure section in `cve-sync-kev.md`.

### WI-15: Sanitized Error Messages

Merged into WI-8 (Error Handling).

### WI-16: Cross-references

**Priority**: LOW

Update cross-references section to include:

- `docs/features/tickets/cve-tracking.md`
- `docs/features/tickets/cve-service.md`
- `docs/features/tickets/cve-sync-mitre.md` (KEV overlap)
- `docs/features/tickets/ticket-references.md`
- `docs/features/platform/fetcher-infrastructure.md`
- `docs/data-sources.md`
- `docs/data-model.md`
- `docs/api-spec.md`

Note: the final spec MUST NOT reference "Common First Run Behavior" from
`cve-tracking.md` — this convention does not apply to stateless catalog
fetchers (no cursor, no clone, no incremental state).

### WI-17: Fetcher Registry and `data-sources.md`

**Priority**: LOW

Update the Fetcher Registry row in `docs/data-sources.md` with:
- Schedule: `0 1,7,13,19 * * *`
- Data ingested: KEV date_added, reference_url, CWE classifications
- Spec status: Complete

Additional `data-sources.md` updates:
- Remove "remediation deadline" from the "Relevant data" prose description in
  the CISA KEV source section (replace with accurate field list)
- Add `sync_cisa_kev` to the CVECWE "Populated By" column in the Data
  Population Matrix

## WI-18: MITRE Abort Threshold — Explicit Documentation

**Priority**: LOW

**Problem**: the MITRE fetcher spec (`docs/features/tickets/cve-sync-mitre.md`)
does not explicitly document that it has no source-specific abort threshold.
This is an omission, not a decision. The Kernel fetcher, which has the same
structural pattern (git-based, local iteration after pull), explicitly declares
"no abort threshold" with rationale. MITRE should do the same for consistency.

**Action**: Add an explicit abort threshold note to the Error Handling section
of `cve-sync-mitre.md`, analogous to Kernel's:

> **Abort threshold**: no source-specific abort threshold is defined. After a
> successful git pull, all per-CVE failures are local (parse errors, upsert
> failures). The shared Celery task timeout limits maximum run duration. The
> fetcher dashboard surfaces partial-status runs with high `items_failed`
> counts for operator attention.

**Deliverable**: Update `docs/features/tickets/cve-sync-mitre.md`.

### WI-19: Rewrite `source_reference_url_pattern` Description

**Priority**: MEDIUM

**Problem**: the description of `source_reference_url_pattern` in
`docs/features/platform/fetcher-infrastructure.md` (line 1228, Class
Attributes table) says "a TicketReference with type=advisory is
**automatically** created for each processed CVE" — then immediately
follows with "CVE fetchers **MUST call**
`reference_service.upsert_references()`". The two statements contradict
each other and caused a real error in this draft (session 5 removed the
explicit call believing it was automatic).

**Action**: Rewrite the description column to:

> URL pattern with `{cve_id}` placeholder for human-readable CVE pages.
> Fetchers with this attribute set MUST pass the constructed URL as
> `source_url` to `reference_service.upsert_references()` after each
> `upsert_cve()` call — this creates a TicketReference with
> type=advisory. See `docs/features/tickets/ticket-references.md` for
> details

**Deliverable**: Update `docs/features/platform/fetcher-infrastructure.md`
(executed in step 3 of the execution plan).

## WI-NEW-1: `supports_fetch_single` — Spec-Level Change

**Priority**: HIGH

### Problem Statement

`BaseCVEFetcher` currently defines `fetch_single()` as an abstract method —
all subclasses must implement it. For catalog-based fetchers (KEV, EPSS), this
is architecturally inappropriate: the source is a single monolithic file with
no per-CVE API. Implementing `fetch_single()` requires downloading the entire
catalog (~1.5MB) to extract one entry, which is inefficient and provides
marginal value given periodic full-sync runs every 6h.

### Solution

Add a `supports_fetch_single: bool` class attribute to `BaseCVEFetcher`,
following the existing `participates_in_catch_up` pattern:

- Default: `True` (all existing per-CVE API fetchers unchanged)
- Catalog-based fetchers set `False` to opt out
- `get_fetch_single_fetchers()` filters by this attribute
- `fetch_single()` changes from abstract to concrete with a default that
  raises `RuntimeError` (safety net, never called in practice)

### Impacted Files — Detailed Change Plan

#### HIGH impact (spec text must change)

| File | Required changes |
|------|-----------------|
| `docs/features/platform/fetcher-infrastructure.md` | (1) Change `fetch_single()` from abstract to concrete with default `RuntimeError`. (2) Add `supports_fetch_single: bool = True` to class attributes table (line ~1229). (3) Update `get_fetch_single_fetchers()` definition (line ~504): add filter `where supports_fetch_single is True`. (4) Update "On-demand Single-Item Fetch" section (line ~290): note that method is structurally optional via class attribute. (5) Add catalog-based fetcher exemption to the Retry Policy and Signaling Convention sections (they don't apply when `supports_fetch_single = False`). (6) Update the catch-up table (line ~838): note that `sync_cisa_kev` also sets `supports_fetch_single = False`. (7) Update code examples showing `BaseCVEFetcher` class with new attribute |
| `docs/features/tickets/cve-service.md` | (1) Update `trigger_on_demand_fetch()` documentation (line ~684): clarify it dispatches to fetchers from `get_fetch_single_fetchers()` (which now filters by attribute). (2) Update "No fetcher implements fetch_single()" edge case (line ~825): reword to "no fetcher with `supports_fetch_single = True` is registered". (3) Update `CVEInvalidSourceError` description (line ~932): source validation matches against `get_fetch_single_fetchers()` keys (already correct, just verify wording) |
| `docs/features/tickets/cve-tracking.md` | (1) Update on-demand flow description (line ~143): "for all registered CVE fetchers that implement fetch_single()" → "for all CVE fetchers with `supports_fetch_single = True`". (2) Update `fetch_single_cve` task documentation (line ~461): note it is never dispatched for opt-out fetchers. (3) Update Fetch Status Read Path (line ~596): enumerate only fetchers from `get_fetch_single_fetchers()` |

#### MEDIUM impact (property or statement needs updating)

| File | Required changes |
|------|-----------------|
| `docs/features/tickets/cve-sync-kev.md` | Properties table: add `supports_fetch_single = False`. No `fetch_single` section |
| `docs/features/tickets/cve-sync-epss.md` | Same pattern as KEV — add `supports_fetch_single = False` to properties table when this spec is completed |
| `docs/data-model.md` | Reference to `get_fetch_single_fetchers()` (line ~498): verify wording is still accurate |
| `docs/features/tickets/cvss-scoring.md` | Reference to catch-up delegating to `fetch_single` (line ~352): add note that this applies only to fetchers with `supports_fetch_single = True` |

#### LOW impact (minor wording adjustments)

| File | Required changes |
|------|-----------------|
| `docs/architecture.md` | Line ~100: "provides the `cve_source_type`, `fetch_single()`, and default `catch_up()` contracts" → "provides the `cve_source_type`, optional `fetch_single()`, and default `catch_up()` contracts" |
| `docs/conventions.md` | Line ~186: "Declare `cve_source_type` and implement `fetch_single()`" → "Declare `cve_source_type` and implement `fetch_single()` (unless `supports_fetch_single = False`)" |
| `docs/features/tickets/ticket-references.md` | Line ~446: caller column "CVE fetchers (`execute`, `fetch_single`)" — no change needed (still accurate for fetchers that support it) |
| `docs/features/identity/rbac.md` | Line ~428: `POST /api/v1/cves/{cve_id}/refetch` endpoint — no change to endpoint itself, only the set of sources it dispatches to |

#### No change needed

| File | Reason |
|------|--------|
| `docs/features/tickets/cve-sync-nvd.md` | Has `fetch_single()` → default `supports_fetch_single = True`, no change |
| `docs/features/tickets/cve-sync-redhat.md` | Same as above |
| `docs/features/tickets/cve-sync-ghsa.md` | Same as above |
| `docs/features/tickets/cve-sync-osv.md` | Same as above |
| `docs/features/tickets/cve-sync-mitre.md` | Same as above (inherits from BaseGitFetcher which has fetch_single) |
| `docs/features/tickets/cve-sync-kernel.md` | Same as above |
| `docs/data-sources.md` | GHSA mention is contextual, no change |
| `docs/features/platform/fetcher-operations.md` | On-demand fetch note is accurate as-is |
| `docs/features/packages/cpe-package-mapping.md` | Reference to `fetch_single_cve` as caller — still valid |
| `docs/api-spec.md` | `CVE_INVALID_SOURCE` error code — unchanged |
| `docs/reviews/*.md` | Historical, no updates |

### Design Rationale

- **Follows existing pattern**: `participates_in_catch_up` is the same
  mechanism (class attribute boolean for opt-out)
- **No workarounds**: no dummy methods, no caching, no no-op implementations
- **Semantically clean**: the system knows KEV doesn't support on-demand fetch
- **Zero changes to per-CVE fetchers**: default is `True`, they continue
  unchanged
- **Extensible**: any future catalog-based fetcher sets `False` and is done

## Execution Plan

Ordered sequence of specification changes:

| Step | Action | Target file(s) | Notes |
|------|--------|----------------|-------|
| 1 | Remove `remediation_deadline` from `CVEKEVEntry` | `docs/data-model.md` | Column removal |
| 1B | Add `"kev"` to `CVESourceType` enum | `docs/data-model.md` | Required before fetcher class registration (WI-2B) |
| 2 | Remove `remediation_deadline` from `KEVEntry` | `docs/features/tickets/cve-service.md` | Payload alignment |
| 3 | Implement `supports_fetch_single` changes | `docs/features/platform/fetcher-infrastructure.md` | Core contract change (WI-NEW-1 HIGH items); also rewrite `source_reference_url_pattern` description (line 1228) to remove misleading "automatically" wording (WI-19) |
| 4 | Update on-demand fetch documentation | `docs/features/tickets/cve-service.md`, `docs/features/tickets/cve-tracking.md` | Align with step 3; also update Callers table in `cve-service.md` (add `record_source_status()` and `reference_service.upsert_references()` to `sync_cisa_kev` row) |
| 5 | Update minor references | `docs/architecture.md`, `docs/conventions.md`, `docs/features/tickets/cvss-scoring.md` | LOW/MEDIUM items from WI-NEW-1 |
| 5B | Add explicit abort threshold note to MITRE | `docs/features/tickets/cve-sync-mitre.md` | WI-18 — document intentional absence of abort threshold |
| 6 | Write complete fetcher spec | `docs/features/tickets/cve-sync-kev.md` | Full spec (WI-3, WI-4, WI-6, WI-8, WI-9, WI-11, WI-12, WI-13, WI-14, WI-16) |
| 7 | Update Fetcher Registry and data-sources prose | `docs/data-sources.md` | Schedule, data, status, CVECWE populated-by |
| 8 | Run `@spec-coherence-reviewer` | — | After steps 1-7 |
| 9 | Run `@spec-gap-analyzer` | — | On `cve-sync-kev.md` |
| 10 | Run `@data-model-reviewer` | — | On steps 1 and 1B |
| 11 | Run `@docs-placement-reviewer` | — | After steps 1-7 |

## Open Points — All Resolved

| ID | Question | Decision | Rationale |
|----|----------|----------|-----------|
| OP-1 | Store `vendorProject`/`product`? | **No** — Explicitly Ignored | Sentinel uses NVD/MITRE CPE; CISA values are imprecise |
| OP-2 | Store `shortDescription`? | **No** — Explicitly Ignored | NVD/MITRE authoritative for descriptions |
| OP-3 | Store `knownRansomwareCampaignUse`? | **No** — Explicitly Ignored | Low value without broader threat intel context |
| OP-4 | Store raw `notes`? | **No** — Explicitly Ignored | URLs available from NVD/MITRE; raw text not actionable |
| OP-5 | Unknown CVEs: skip or create? | **Skip silently** | Most KEV entries won't have Sentinel CVEs; 1000+ warnings per run unacceptable |
| OP-6 | `upsert_cve()` or dedicated path? | **`upsert_cve()`** | Consistent with all enrichment fetchers |
| OP-7 | Per-entry error isolation? | **Yes** | Consistent with all CVE fetchers |
| OP-8 | Schedule? | **Every 6h** (`0 1,7,13,19 * * *`) | Aligned with NVD/MITRE; staggered from Red Hat |
| OP-9 | Extract `notes` URLs as `TicketReference`? | **No** | Follows OP-4 (notes ignored entirely) |
| OP-10 | CWE `source` value? | **`"CISA KEV"`** | Distinguishes from `"adp:CISA-ADP"` (MITRE) |
| OP-11 | `fetch_single()` strategy? | **`supports_fetch_single = False`** | New spec-level pattern; no on-demand support for catalog fetchers |
| OP-12 | `record_created` semantics? | **N/A** (never used) | Enrichment-only; consistent with Red Hat |
| OP-13 | KEV vs MITRE authority? | **No conflict** | Dropped `remediation_deadline`; overlap is complete and idempotent |

## Session Log

| Date | Session | Work done | Open points resolved |
|------|---------|-----------|---------------------|
| 2026-06-20 | 1 | Initial analysis, gap identification, draft plan created | — |
| 2026-06-20 | 2 | Live feed verification, all 13 OPs resolved, `supports_fetch_single` pattern designed, execution plan finalized | OP-1 through OP-13 |
| 2026-06-20 | 3 | Ran 4 review agents (`@spec-coherence-reviewer`, `@spec-gap-analyzer`, `@data-model-reviewer`, `@docs-placement-reviewer`). Incorporated findings: added WI-2B (CVESourceType enum), added `record_source_status("failure")` to error paths, added `dateAdded` parse failure to error table, clarified metric semantics with OP-12 reference, added behavioral notes (data lifecycle, re-invocation safety, empty catalog, first-run), expanded WI-17 scope, noted Common First Run Behavior removal, updated execution plan with step 1B. G-6 (count sanity check) explicitly ignored. G-11 resolved via OP-12 alignment (existing "processed" convention) | G-11 resolved |
| 2026-06-20 | 4 | Post-review refinements: added transaction-per-entry boundary to WI-3, split error handling step 3e into 3e/3f based on `record_source_status` precondition (CVE UUID availability), added HTTP timeout (30s) to WI-12, added `"epss"` to WI-2B (pre-existing gap), created OP-13 in `open-points.md` for CWE accumulation cross-cutting issue | — |
| 2026-06-20 | 5 | Ran 4 review agents on draft plan (`@spec-coherence-reviewer`, `@spec-gap-analyzer`, `@data-model-reviewer`, `@docs-placement-reviewer`). Applied 5 findings: (1) changed `source_reference_url_pattern` from `None` to standard URL pattern, removed manual `upsert_references()` call from WI-3/WI-8/WI-11/WI-14/GAP-6; (2) added explicit "no abort threshold" note to WI-8 (Kernel precedent — local iteration after download, no remote API per-entry); (3) added Callers table update to execution plan step 4; (4) reworded WI-3 transaction boundary to "per-entry error isolation" (transactions managed internally by `upsert_cve()`); (5) added WI-18 for MITRE abort threshold documentation gap. Created `docs/drafts/basefetcher-all-items-failed.md` for cross-cutting safety check promotion from `BaseGitFetcher` to `BaseFetcher` | — |
| 2026-06-20 | 6 | Ran 3 review agents (`@spec-coherence-reviewer`, `@spec-gap-analyzer`, `@data-model-reviewer`). Two actionable findings: (1) HIGH — session 5 incorrectly removed the explicit `reference_service.upsert_references()` call from the algorithm; `source_reference_url_pattern` is a data holder, not an automatic mechanism inside `upsert_cve()`. Reinstated the call in WI-3 step 3d, corrected GAP-6 and WI-11. (2) MEDIUM — unspecified behavior when `record_source_status("failure")` itself fails; resolved as "let propagate" (DB down = all subsequent entries fail anyway). Added `upsert_references()` failure to WI-8 error table. Added WI-19 (rewrite misleading `source_reference_url_pattern` description in `fetcher-infrastructure.md` line 1228). Updated execution plan step 3 and 4. Data model review: clean, no issues | — |
