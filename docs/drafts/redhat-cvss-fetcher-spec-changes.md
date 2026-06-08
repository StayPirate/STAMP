# Draft: Red Hat CVE Fetcher — Specification Changes

## Status

**Draft** — in progress. Points are added incrementally as design
decisions are agreed upon.

## Scope

This document describes the changes required to the existing
specifications to complete the Red Hat CVE fetcher definition. The
fetcher is currently named `sync_cvss_redhat` in the specs but will be
renamed to `sync_redhat_cves` (class `SyncRedhatCves`) as part of the
fetcher naming convention — see
`docs/features/platform/fetcher-infrastructure.md` (section "Naming
Convention"). This draft uses the new name throughout.

The fetcher is partially specified in
`docs/features/tickets/cvss-scoring.md` (sections "Red Hat Sync",
"Fetcher: `sync_cvss_redhat`", "Sub-operation: `fetch_single_redhat`").
The current spec covers CVSS vector extraction but has gaps in error
handling, CWE/reference extraction, and package best-effort addition.

This draft does NOT propose a new feature — it completes the existing
fetcher specification by resolving open gaps identified during API
research.

## API Research Summary

The Red Hat Security Data API
(`access.redhat.com/hydra/rest/securitydata`) was tested against real
CVEs (CVE-2024-21626, CVE-2024-3094, CVE-2014-0160, CVE-2025-99999) and
the official documentation was reviewed. Key findings that inform the
spec changes below:

- **Endpoint used**: `GET /cve/{CVE-ID}.json` — returns full CVE detail
  including CVSS, CWE, references, affected releases, and package state
- **No authentication** required (public API)
- **HTTP 404** means the CVE is not in Red Hat's database (confirmed
  with non-existent CVE-ID)
- **CVSS v2** (`cvss` field) is present on older CVEs (e.g.,
  CVE-2014-0160), absent on recent ones
- **CVSS v3** (`cvss3` field) is present on recent CVEs, absent on
  older ones. No CVSS v4.0 field exists yet
- **`status`** field (`"draft"` or `"verified"`) appears inside both
  `cvss` and `cvss3` objects
- **`cwe`** field is a single string (e.g., `"CWE-200"`,
  `"CWE-506"`)
- **`references`** is an array where each element is a string
  containing one or more URLs separated by `\n`
- **`package_state[]`** contains clean source package names in
  `package_name` (e.g., `"xz"`, `"openssl"`, `"runc"`)
- **`affected_release[]`** contains NEVRA strings in `package` (e.g.,
  `"runc-4:1.1.12-1.el9_3"`)
- **List endpoint** (`GET /cve.json`): supports `after`/`before`
  (filters on `public_date`), `created_days_ago` (filters on internal
  creation date), and `ids` (comma-separated CVE-IDs). Does NOT support
  `modified_after` — cannot detect modifications to existing CVEs

## Agreed Changes

### 0. Fetcher Spec Relocation — from `cvss-scoring.md` to `cve-tracking.md`

**Decision**: move the full fetcher definition from
`docs/features/tickets/cvss-scoring.md` to
`docs/features/tickets/cve-tracking.md`.

**Rationale**: with the expanded scope (CVSS + CWE + references +
packages), the fetcher no longer belongs in a CVSS-specific spec.
`cve-tracking.md` is already the home for all CVE data fetchers
(`sync_nvd_cves`, `sync_mitre_cves`, `sync_kernel_cves`), all of
which extract multiple data types (CVSS, CWE, references, affected
versions) from a single API call. The Red Hat fetcher is
architecturally identical — it enriches existing CVE records from an
external source via `upsert_cve()`.

This follows the precedent already established for NVD: `cvss-scoring.md`
keeps a consumer-oriented summary ("Red Hat Sync") focused on how the
CVSS data is used for scoring, and points to `cve-tracking.md` for the
full fetcher definition.

#### Spec changes required

**`docs/features/tickets/cve-tracking.md`** — receives:

- A new section "Fetcher: `sync_redhat_cves`" with: properties table
  (including `cve_source_type = "redhat"`,
  `fetch_single() = Yes — Red Hat Security Data API single CVE query`,
  and `source_reference_url_pattern = "https://access.redhat.com/security/cve/{cve_id}"`,
  for consistency with NVD/MITRE/kernel properties tables), complete
  algorithm (CVSS v2 + v3 extraction, CWE, references, best-effort
  packages), error handling, sanitized messages, metrics, custom
  settings
- The `fetch_single` method definition (replacing the separate
  "Sub-operation: `fetch_single_redhat`" template)
- Aligned with the existing NVD and MITRE fetcher sections in the same
  spec

**`docs/data-sources.md`** — line 790: update the "Spec" column link
from
`[cvss-scoring.md](features/tickets/cvss-scoring.md#fetcher-sync_redhat_cves)`
to
`[cve-tracking.md](features/tickets/cve-tracking.md#fetcher-sync_redhat_cves)`
(the fetcher definition moves with the relocation).

**`docs/features/tickets/cvss-scoring.md`** — changes:

- "Red Hat Sync" section (lines 310-366): **kept** as a consumer
  summary focused on CVSS scoring behavior. Trimmed to 3-4 paragraphs
  covering: what CVSS data arrives from Red Hat, periodicity, scope
  gap, catch-up mechanism
- Add cross-reference at the end: "For the full fetcher definition —
  including the complete algorithm, CWE/reference extraction, package
  best-effort addition, and error handling — see `cve-tracking.md`
  (Fetcher: `sync_redhat_cves`)." — same pattern used for NVD (lines
  305-308)
- "Fetcher: `sync_cvss_redhat`" section (lines 731-770): **removed**
  (moved to `cve-tracking.md`)
- "Sub-operation: `fetch_single_redhat`" section (lines 772-801):
  **removed** (replaced by `fetch_single` method on the class in
  `cve-tracking.md`)

### 1. Incremental Fetch via List Endpoint — Not Adopted

**Decision**: the `GET /cve.json` list endpoint will NOT be used for
scheduled fetching.

**Rationale**: the list endpoint filters by `public_date`
(`after`/`before`) and internal creation date (`created_days_ago`), but
has no `modified_after` parameter. This means it can discover newly
created Red Hat CVE entries but cannot detect modifications to existing
ones (CVSS score changes, `draft` to `verified` transitions, CWE
updates). Since the per-CVE poll (`GET /cve/{CVE-ID}.json`) on active
tickets already covers both new assessments and modifications, adding
the list endpoint would increase complexity without eliminating the
per-CVE poll.

**Spec impact**: no changes required. The current design (per-CVE poll
on active tickets + `fetch_single` method for on-demand catch-up) is
confirmed as sufficient. The existing note in `cvss-scoring.md` line
312-314 ("Red Hat's API does NOT support incremental fetching") is
accurate and does not need modification.

### 2. CWE and References Extraction

**Decision**: the `sync_redhat_cves` fetcher will extract CWE
identifiers and reference URLs from the Red Hat API response, in
addition to CVSS data.

**Rationale**: the Red Hat API response includes `cwe` and `references`
fields. `data-sources.md` already lists the Red Hat fetcher as a source
for `CVECWE` and references, but the algorithm in `cvss-scoring.md`
does not include extraction steps. This change closes that
inconsistency.

No duplicate risk exists:

- **CVECWE**: unique constraint `(cve_id, cwe_id, source)`. Red Hat
  CWE entries use `source = "Red Hat"`. If NVD reports the same CWE-ID
  for the same CVE, both appear as separate rows (different `source`),
  providing cross-provider confirmation. Re-syncs for the same source
  are upserted, not duplicated.
- **TicketReference**: unique constraint `(ticket_id, url)`. If the
  same URL is already present (e.g., inserted by the NVD fetcher), the
  upsert logic applies fill-NULL-only semantics (fills `title` or
  `description` if they were NULL, otherwise skips). No duplicate rows.

#### Spec changes required

**`docs/features/tickets/cve-tracking.md`** — new "Fetcher:
`sync_redhat_cves`" section (per relocation in change 0):

The algorithm section must include CWE and reference extraction as
explicit steps alongside CVSS extraction:

   a. **CWE**: if the response contains a `cwe` field (string, e.g.,
      `"CWE-200"`), persist a `CVECWE` record with `source = "Red Hat"`
      via the `cve_service` upsert path. The `cwe` field is always a
      single CWE identifier (not a chain). If the field is absent, skip
      (no CWE to record).

   b. **References**: if the response contains a `references` field
      (array of strings), split each element on `\n` to extract
      individual URLs. For each URL, call
      `reference_service.upsert_references()` with
      `source = "sync_redhat_cves"`. Type auto-classification: URLs
      matching known patterns (e.g., `nvd.nist.gov` → `advisory`,
      `github.com/.../commit` → `patch`) are classified; others default
      to `NULL` (uncategorized).

Metrics clarification for the new "Fetcher: `sync_redhat_cves`"
section:

- **`record_created`**: N/A. The Red Hat fetcher never creates new CVE
  records — it enriches existing ones via `upsert_cve()`. New `CVECWE`
  and `TicketReference` records created as a side effect of enrichment
  are not counted as `record_created` (consistent with NVD, where
  `record_created` means "a new CVE record was inserted for the first
  time"). The fetcher uses only `record_updated` and `record_failed`.
- **`record_updated`**: incremented only when `upsert_cve()` actually
  modifies data — CVSS assessments (v2 and/or v3), CWE records,
  reference URLs, or package names inserted or updated. A single CVE
  fetch that upserts both CVSS and CWE counts as one `record_updated`
  (not two). If the API returns HTTP 200 but no data has changed (all
  upserts are no-ops), no metric is recorded for that CVE — consistent
  with NVD/MITRE fetchers.
- **`record_failed`**: incremented per CVE on non-retryable errors or
  after retry exhaustion.

**`docs/features/tickets/cvss-scoring.md`** — the consumer summary
("Red Hat Sync") does not need CWE/reference detail. It focuses on
CVSS scoring behavior and points to `cve-tracking.md` for the full
algorithm.

**`docs/data-sources.md`** — line 790: change spec status from
`Partial` to `Complete` once all gaps are closed (this will happen when
the error handling section is also resolved — see future additions to
this draft).

### 3. Best-Effort Package Addition from Red Hat Data

**Decision**: the fetcher will extract source package names from the
Red Hat API response and pass them as `resolved_packages` in the
`CVEIngestPayload` to `upsert_cve()`. The service layer handles
Phase 2 side effects (enqueuing `add_package_to_ticket()` tasks).
This is a best-effort operation — packages that SMELT does not
recognize are silently skipped.

**Rationale**: Red Hat and SUSE share upstream heritage for many
packages. Names like `openssl`, `xz`, `curl`, `kernel`, `glibc` are
identical between distributions. When the name matches, the VA gets
the package auto-added to the ticket with all SUSE tracks and products
resolved via SMELT — saving manual work.

**Architecture**: the fetcher follows the same pattern as NVD/MITRE/
kernel fetchers — it provides data to `upsert_cve()` and the service
layer handles ticket association and package enqueue as Phase 2 side
effects after commit. The fetcher has no knowledge of `ticket_id` and
does not directly enqueue `add_package_to_ticket()` tasks.

#### Algorithm

For each CVE fetched (both periodic `execute()` and on-demand
`fetch_single()`):

1. **Extract package names** from the `package_state[]` array in the
   API response. Use the `package_name` field (clean source package
   name, e.g., `"xz"`, `"openssl"`). If `package_state` is absent or
   empty, skip this step.

2. **Filter**: discard entries where `package_name` is `null`, empty,
   or whitespace-only. Additionally, discard names containing `/`
   (these are Red Hat container image paths, e.g.,
   `openshift4/ose-docker-builder-rhel9`, not source packages). No
   other filtering is applied — the cost of a few extra failed SMELT
   lookups is negligible compared to the complexity of maintaining a
   Red Hat-specific exclusion list.

3. **Deduplicate** the remaining names (a single CVE response may list
   the same package under multiple Red Hat products).

4. **Include in payload**: pass the deduplicated package names as
   `resolved_packages` in the `CVEIngestPayload` to `upsert_cve()`.
   The service layer enqueues one `add_package_to_ticket()` background
   task per package name as a Phase 2 side effect — the fetcher does
   not manage this step.

5. **Idempotency**: `add_package_to_ticket()` checks for existing
   `TicketPackage` records before creating new ones (unique constraint
   on `(ticket_id, package_name)`). If the package is already on the
   ticket, the call is a no-op. If SMELT does not recognize the
   package name, the call returns with no records created.

The fetcher does NOT use `affected_release[].package` (NEVRA format
requires parsing and Red Hat release-specific epoch/version would
need to be stripped — the `package_state[].package_name` field is
cleaner and sufficient).

#### Spec changes required

**`docs/features/tickets/cve-tracking.md`** — "Fetcher:
`sync_redhat_cves`" algorithm (per relocation in change 0):

Add package best-effort as a step in the algorithm:

   c. **Package best-effort**: if the response contains a
      `package_state` array, extract `package_name` values, discard
      null/empty values and names containing `/`, deduplicate, and
      pass as `resolved_packages` in the `CVEIngestPayload` to
      `upsert_cve()`.

Add a note clarifying that this is best-effort and does not create
packages that SMELT does not recognize. Package addition metrics are
tracked by the `add_package_to_ticket` Phase 2 tasks, not by the
fetcher itself.

**`docs/features/packages/package-service.md`** — no changes needed.
The existing `add_package_to_ticket()` contract already handles
unknown packages gracefully (SMELT returns empty → no records
created).

**`docs/data-sources.md`** — line 790: update "Data Ingested" column
to "CVSS Red Hat (v2 + v3), CWE, references, best-effort package
names."

### 4. Red Hat `status` Field — Ignored

**Decision**: the `status` field (`"draft"` or `"verified"`) inside
`cvss` and `cvss3` objects is ignored. The fetcher imports any CVSS
assessment that contains a valid vector string, regardless of status.

**Rationale**: the `status` field indicates how far along Red Hat's
internal investigation has progressed, but it does not affect the
validity of the CVSS vector itself. A `"draft"` assessment from Red Hat
is still more information than no assessment at all. The
`CVECVSSAssessment` data model has no column for provider-side status,
and adding one would provide no actionable value — Sentinel already
treats all external assessments as informational inputs to the
resolution cascade.

**Spec impact**: the algorithm in `cve-tracking.md` should explicitly
state that the `status` field is not evaluated — the only gate is
whether the vector string is present and parseable by the `cvss`
library.

### 5. CVSS v2 Import

**Decision**: the fetcher imports CVSS v2 assessments from older CVEs
in addition to v3.

**Rationale**: the Red Hat API returns v2 data in the `cvss` field
(e.g., CVE-2014-0160: `"cvss_scoring_vector":
"AV:N/AC:L/Au:N/C:P/I:N/A:N"`). The `cvss-scoring.md` spec (line
17-19) already allows this: "Other versions (e.g., v2.0) may arrive
from external sources and are stored and displayed but not used for
decisions." The Python `cvss` library supports v2 parsing (`CVSS2`
class).

#### Algorithm

For each Red Hat API response, the fetcher extracts CVSS from both
fields:

1. **`cvss3`** (if present and vector string is non-empty): extract
   `cvss3_scoring_vector`, parse with `cvss` library (`CVSS3` class),
   derive version (`"3.1"`), score, and severity. Persist as
   `CVECVSSAssessment` with `provider_name = "Red Hat"`.
2. **`cvss`** (if present and vector string is non-empty): extract
   `cvss_scoring_vector`, parse with `cvss` library (`CVSS2` class),
   derive version (`"2.0"`), score, and severity. Persist as
   `CVECVSSAssessment` with `provider_name = "Red Hat"`.
3. If neither field is present, no CVSS assessment is recorded (skip).
4. Each field is processed independently — a response may have both,
   one, or neither.

**Boundary condition**: the gate for CVSS extraction is the vector
string's presence AND non-emptiness. If a `cvss3` (or `cvss`) object
is present but its scoring vector is `null`, `""`, or whitespace-only,
the object is treated as absent (skip, do not raise or fail). The
`cvss` library is never invoked with an empty string.

The unique constraint `(cve_id, provider_name, cvss_version)` ensures
v2 and v3 assessments from the same provider coexist as separate rows.

#### Spec changes required

**`docs/features/tickets/cve-tracking.md`** — "Fetcher:
`sync_redhat_cves`" algorithm: document both `cvss` and `cvss3` field
extraction. The current spec only mentions `cvss3`.

**`docs/features/tickets/cvss-scoring.md`** — "Providers > External
Providers > Red Hat" section (lines 146-159): update "CVSS versions:
currently v3.1 only" to "CVSS versions: v2.0 and v3.1. v4.0 will be
supported when Red Hat adds it."

**`docs/data-sources.md`** — "Red Hat Security Data" section (line
107): update "Relevant data" from "CVSS v3.1 base scores and scoring
vectors" to "CVSS v2.0 and v3.1 base scores and scoring vectors, CWE
identifiers, reference URLs, source package names."

### 6. Error Handling (GAP-CVS-008)

**Decision**: define error handling for both `execute()` (periodic
batch) and `fetch_single()` (on-demand single CVE).

**Rationale**: the error handling section in the current spec is
explicitly marked TBD. The behavior for each failure mode was
determined from API research and aligned with the existing error
categorization in `fetcher-infrastructure.md`.

#### `fetch_single()` — On-demand (single CVE)

The `fetch_single()` method follows the standard error categorization
and retry policy defined in `fetcher-infrastructure.md` (Error
Categorization table) with no Red Hat-specific deviations to the retry
or failure classification rules.

The only Red Hat-specific signal is the `missing` condition:
`CVENotInSource` is raised when:

- **HTTP 404** — the CVE is not in Red Hat's database, OR
- **HTTP 200 with no extractable data** — the response contains no
  CVSS (v2 or v3), no CWE, no references, and no package names. This
  means Red Hat has a CVE entry but no actionable data for Sentinel.

If the response contains any extractable data — even without CVSS
(e.g., CWE and references only) — the fetcher upserts what is
available and returns normally (`success`). The fetcher's scope
extends beyond CVSS: discarding valid CWE/reference/package data
solely because CVSS is absent would lose actionable information.

**Partial extraction failures**: if some data types are upserted
successfully but a parsing failure occurs on another (e.g., CVSS
upserted but CWE string is malformed), the CVE counts as
`record_updated` (data was saved) with a WARNING log for the failed
sub-extraction. The partial failure does not invalidate data already
persisted.

**Data preservation**: existing Red Hat data — `CVECVSSAssessment`
records, `CVECWE` records, and `TicketReference` entries created by
this fetcher — is **not deleted** when a later response returns
HTTP 404 or lacks previously-present fields. The data was valid when
fetched; absence in a later response does not invalidate it.

#### `execute()` — Periodic batch

The `execute()` error handling follows the Common CVE Fetcher Error
Handling pattern defined in `cve-tracking.md` with one source-specific
addition: **persistent infrastructure failure abort** — after 3
consecutive failures (HTTP 5xx, network timeout, or DNS error), the
batch run aborts entirely with `FetcherError` rather than continuing
to the next CVE. This is specific to Red Hat because the API has no
rate-limit documentation, so 3 consecutive failures likely indicate
an outage rather than per-CVE issues. All other error categories
(HTTP 404 skip, HTTP 429 throttle, HTTP 200 with unparseable data)
follow the common pattern.

The batch run iterates over all CVEs with active tickets. Error
handling is **per-CVE**, not per-run:

| Condition | Action |
|-----------|--------|
| HTTP 200 with extractable data | Upsert, `record_updated` |
| HTTP 200 with no extractable data | Skip CVE, no metric (not a failure — Red Hat has no actionable data) |
| HTTP 404 | Skip CVE, no metric (not a failure) |
| HTTP 429 | `record_failed` for this CVE, **continue** to next CVE (after throttle delay) |
| HTTP 5xx | `record_failed` for this CVE, continue to next |
| Network timeout | `record_failed` for this CVE, continue to next |
| HTTP 200 with unparseable data | `record_failed` for this CVE, continue to next |
| Persistent network failure (e.g., DNS down) | After 3 consecutive failures, abort the entire run with `FetcherError` |

The consecutive failure counter resets to zero after any successful
CVE fetch (HTTP 200 with data upserted) or clean skip (HTTP 404, 200
with no data). Only uninterrupted sequences of infrastructure failures
(HTTP 5xx, network timeout, DNS error) count toward the 3-failure
abort threshold.

Key design choice: the batch run **never aborts on a single CVE
failure**. It continues to the next CVE after recording the failure.
The only abort condition is persistent infrastructure failure (3
consecutive errors suggesting the network or API is down entirely).

#### Sanitized Messages

Per `fetcher-infrastructure.md` requirement, the fetcher produces
these sanitized `FetcherError` messages:

| Failure mode | `FetcherError` message |
|---|---|
| Connection error | `"Failed to connect to Red Hat Security Data API"` |
| HTTP 5xx | `"Red Hat Security Data API returned HTTP {status_code}"` |
| Persistent infra failure | `"Red Hat Security Data API unreachable — 3 consecutive failures"` |
| Unparseable JSON | `"Red Hat API returned unparseable response for {cve_id}"` |
| Invalid CVSS vector | `"Red Hat API returned invalid CVSS vector for {cve_id}"` |

#### Spec changes required

**`docs/features/tickets/cve-tracking.md`** — two changes:

- "Fetcher: `sync_redhat_cves`" error handling section: replace the
  TBD placeholder with the error handling specification above.
- Common CVE Fetcher Error Handling (line 369): qualify "A batch must
  never abort entirely due to a single CVE failure" to "A batch must
  never abort entirely due to a single CVE failure. Source-specific
  abort conditions (e.g., persistent infrastructure failure after N
  consecutive errors) are documented in the individual fetcher sections
  below."

**`docs/reviews/cvss-scoring.md`** — GAP-CVS-008: mark as **RESOLVED**
once the error handling section is written in `cve-tracking.md`.

**`docs/data-sources.md`** — line 790: update spec status from
`Partial` to `Complete` (all required sections now defined: properties,
algorithm, error handling, metrics).

### 7. `fetch_single` Method and Catch-Up

**Decision**: `SyncRedhatCves` implements `fetch_single(cve_id)` as a
method on the class. `execute()` delegates to it in a loop. The
separate `fetch_single_redhat` sub-operation task is removed.

**Dependency**: this change depends on the catch-up architecture
refactoring defined in `docs/drafts/catch-up-architecture.md`. Both
drafts are applied to the specifications as a single batch — there is
no intermediate state where Red Hat enrichment at CVE association is
lost (the removal of `fetch_single_redhat` and the addition of
`fetch_single()` on the class happen atomically).

**Rationale**: the Red Hat fetcher already operates on individual CVEs
(one API call per CVE-ID). Extracting this into `fetch_single(cve_id)`
provides three benefits:

1. **On-demand discovery**: `get_fetch_single_fetchers()` auto-discovers
   the method, so `trigger_on_demand_fetch()` invokes it in parallel
   with NVD/MITRE when Sentinel encounters a new CVE-ID. This means
   Red Hat enrichment happens immediately at ticket creation, not only
   at the next 24h scheduled run.
2. **Ticket catch-up for free**: the default `catch_up()` on
   `BaseFetcher` extracts `cve_id` from the ticket and calls
   `self.fetch_single()`. No separate `fetch_single_redhat` Celery task
   needed.
3. **DRY**: the core logic (call Red Hat API → build payload →
   `upsert_cve()` with CVSS/CWE/refs/packages) exists in one place.

#### Class structure

```python
class SyncRedhatCves(BaseFetcher):
    name = "sync_redhat_cves"
    cve_source_type = "redhat"
    description = "Sync CVE data from Red Hat Security API"
    default_schedule = "0 3 * * *"

    class Settings(BaseModel):
        throttle_delay_seconds: float = Field(
            default=2.0, ge=0.1, le=30.0,
            description="Delay between consecutive Red Hat API requests.",
        )

    source_reference_url_pattern = (
        "https://access.redhat.com/security/cve/{cve_id}"
    )

    async def fetch_single(self, cve_id: str, session: AsyncSession) -> None:
        """Fetch a single CVE from the Red Hat Security Data API.

        GET /hydra/rest/securitydata/cve/{CVE-ID}.json

        Extracts: CVSS v2 + v3, CWE, references, package names.
        Builds a CVEIngestPayload with resolved_packages and calls
        upsert_cve(). Raises CVENotInSource if HTTP 404 or response
        contains no extractable data.
        """
        ...

    async def execute(self, session: AsyncSession) -> None:
        """Periodic batch: iterate over CVEs with active tickets."""
        for cve_id in active_ticket_cve_ids:
            try:
                await self.fetch_single(cve_id, session)
                self.record_updated()
            except CVENotInSource:
                pass  # skip — no Red Hat data for this CVE
            except Exception:
                self.record_failed()
            await asyncio.sleep(self.settings.throttle_delay_seconds)

    # catch_up(ticket_id) — inherited from BaseFetcher default:
    #   extracts cve_id from ticket → calls self.fetch_single(cve_id)
```

#### Changes to `SyncRedhatCves` vs current spec

| Aspect | Current spec | New |
|---|---|---|
| Class name | `SyncCvssRedhat` | `SyncRedhatCves` (per naming convention draft) |
| `fetch_single` method | Not implemented | Implemented — core per-CVE logic |
| `execute()` | Contains the per-CVE logic directly | Delegates to `self.fetch_single()` in loop |
| `fetch_single_redhat` sub-operation | Separate Celery task (template) | Removed — replaced by default `catch_up()` |
| On-demand fetch at ticket creation | Not covered (enrichment-only) | Covered — auto-discovered by `get_fetch_single_fetchers()` |
| Catch-up at ticket reactivation | Via `fetch_single_redhat` task | Via default `catch_up()` from `BaseFetcher` |

#### Spec changes required

**`docs/features/tickets/cve-tracking.md`** — new "Fetcher:
`sync_redhat_cves`" section (per relocation in change 0):

Document the `fetch_single(cve_id)` method as part of the class
definition. Include the signaling convention (follows standard
`fetch_single` rules from `fetcher-infrastructure.md`), retry policy,
and error categorization (per change 6 above).

**`docs/features/tickets/cvss-scoring.md`**:

- Remove "Sub-operation: `fetch_single_redhat`" section (lines
  772-801) entirely
- Update "Red Hat Sync > Strategy — initial fetch" (lines 316-337):
  replace references to `fetch_single_redhat` with the new mechanism
  (auto-discovered `fetch_single` → default `catch_up`)
- Update "Ticket Reactivation: CVSS Catch-Up" (lines 704-711): remove
  `fetch_single_redhat` reference; point to
  `fetcher-infrastructure.md` for the catch-up architecture (per
  catch-up architecture draft)
- Lines 253 and 256-257 ("plus `fetch_single` tasks are enqueued for
  catch-up/enrichment"): update references from `fetch_single` tasks
  to `catch_up()` method (per catch-up architecture draft)

**`docs/features/tickets/cve-service.md`**:

- Lines 748-754: remove error case (b) from the `CVEInvalidSourceError`
  detail description entirely. After this change, all registered CVE
  fetchers implement `fetch_single()` — the "enrichment-only fetcher
  without `fetch_single`" category is empty. The only remaining error
  case is (a): the source identifier is completely unrecognized (not a
  registered `CVESourceType`). Simplify the error detail description
  accordingly — the distinction between "unrecognized source" and
  "recognized but no `fetch_single`" no longer applies.
- Lines 906-911: **remove** the enrichment catch-up step entirely —
  Red Hat is now covered by `trigger_on_demand_fetch()` (via
  `fetch_single`), and the catch-up architecture draft removes the
  standalone enrichment catch-up mechanism
- Lines 1203-1208 (UpsertResult section): update "enrichment-only
  fetchers" example — Red Hat is no longer enrichment-only. Use a
  generic description or reference future fetchers (CISA KEV, EPSS)
  as examples of fetchers that manage their own metrics

**`docs/features/platform/fetcher-infrastructure.md`**:

- Lines 177-189: remove the "CVE fetcher example — enrichment fetcher
  (no `fetch_single`)" code block entirely. After this change, all CVE
  fetchers implement `fetch_single()` — the "enrichment-only" category
  is empty. The NVD example (lines 161-175) already serves as the
  canonical CVE fetcher example with `fetch_single()`. If a second
  example is desired for variety, replace with `SyncRedhatCves` showing
  both `execute()` and `fetch_single()` (using the class structure from
  Change #7 of this draft)

## All Changes Summary

| # | Change | Status |
|---|---|---|
| 0 | Fetcher spec relocation (`cvss-scoring.md` → `cve-tracking.md`) | Agreed |
| 1 | Incremental fetch via list endpoint — not adopted | Agreed |
| 2 | CWE and references extraction | Agreed |
| 3 | Best-effort package addition from Red Hat data | Agreed |
| 4 | Red Hat `status` field — ignored | Agreed |
| 5 | CVSS v2 import | Agreed |
| 6 | Error handling (GAP-CVS-008) | Agreed |
| 7 | `fetch_single` method and catch-up | Agreed |

## Related Drafts

- `docs/drafts/catch-up-architecture.md` — refactoring of the
  per-ticket catch-up mechanism from standalone Celery tasks to
  `catch_up()` method on `BaseFetcher`. Both drafts are applied to
  the specifications as a single batch — no intermediate state where
  Red Hat enrichment at CVE association is lost.
