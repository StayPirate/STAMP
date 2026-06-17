# GHSA Fetcher — Specification Plan

Working document for the `sync_ghsa_advisories` fetcher specification.
This draft tracks open points, design decisions, and the application
plan for producing a complete fetcher spec compliant with
`docs/features/platform/fetcher-infrastructure.md` (Fetcher Documentation
Requirements).

## Status

| Item | State |
|------|-------|
| Draft created | 2026-06-17 |
| Open points resolved | Pending |
| Spec written | Not started |
| Fetcher Registry updated | Not started |
| fetcher-infrastructure.md corrections | Not started |

## Context

GitHub operates as a CVE Numbering Authority (CNA). The GitHub Advisory
Database contains curated security advisories with GHSA-IDs, CVSS
scores, CWE identifiers, and precise affected version ranges across
multiple ecosystems. Since GitHub is a CNA, it may publish CVEs before
other sources — making this a **discovery fetcher** (can create tickets),
not merely an enrichment fetcher.

### Current references in codebase

- `docs/data-sources.md:162-189` — GHSA source description
- `docs/data-sources.md:963` — Fetcher Registry row (Spec Status: TBD)
- `docs/data-sources.md:979-984` — CVE Enrichment Data Structures table
- `docs/features/tickets/cve-tracking.md:274-288` — External Identifiers
  fetcher responsibilities
- `docs/features/tickets/cve-service.md:359-360` — Package resolution
  sources table
- `docs/features/tickets/cve-service.md:1086-1091` —
  ExternalIdentifierEntry schema
- `docs/features/platform/fetcher-infrastructure.md:240` — Source
  identifier `ghsa`
- `docs/features/platform/fetcher-infrastructure.md:789` — Listed under
  "no catch_up needed" (INCORRECT — see Correction Plan below)
- `docs/data-model.md:542-578` — CVEExternalIdentifier table and
  CVEExternalIdentifierSource enum

### Established facts (from existing docs)

- Fetcher name: `sync_ghsa_advisories`
- Class name: `SyncGhsaAdvisories`
- `cve_source_type`: `"ghsa"` (follows pattern: nvd, mitre, redhat,
  kernel)
- CVSS `provider_name`: `"GitHub"`
- External identifiers: `source = GHSA`,
  `url = https://github.com/advisories/<GHSA-ID>`
- CWE source: `"GitHub"` (follows provider_name convention)
- Auth: GitHub personal access token (free)
- Rate limits: 5,000 requests/hour with token (REST API)
- Data ingested: CVSS (v3.1 + v4.0), GHSA-ID, CWE, affected versions,
  references
- Discovery fetcher: YES — GitHub is a CNA, can be first to publish a
  CVE. Must create tickets via `upsert_cve()`
- `fetch_single()`: YES — required for all CVE fetchers
- Catch-up: YES — default catch_up via `fetch_single()` (same as
  NVD/MITRE/Kernel)
- `CVEAffectedVersion` populated: Yes (multi-ecosystem)
- `CVECWE` populated: Yes
- `CVEExternalIdentifier` populated: Yes (primary purpose)

---

## Open Points

### OP-1: API Choice (REST vs GraphQL vs Git Clone)

**Decision required**: which access method to use for periodic sync and
for `fetch_single()`.

#### Option A: REST API (`GET /advisories`)

**Endpoint**: `https://api.github.com/advisories`

Pros:

- Simple HTTP client, standard pagination (cursor-based via Link header)
- Supports `modified` parameter for incremental sync (date range filter)
- Supports `cve_id` parameter for `fetch_single()` (single advisory
  lookup by CVE-ID)
- Well-documented response schema (typed fields, not arbitrary JSON)
- No complex query language
- Supports filtering: `type=reviewed`, `ecosystem`, `severity`
- Response includes CVSS v3 AND v4 (`cvss_severities` object)
- `ghsa_id` parameter for direct lookup by GHSA-ID

Cons:

- Rate limit: 5,000 req/hour with token (without token: 60 req/hour —
  unusable for production)
- Max 100 results per page
- `modified` param uses GitHub date range syntax (e.g., `>=2026-06-01`)
- Volume: ~250,000+ reviewed advisories total — first-run would need
  many pages if historical data were needed (but forward-only strategy
  avoids this)

Technical details:

- Auth header: `Authorization: Bearer <GITHUB_TOKEN>`
- Accept header: `Accept: application/vnd.github+json`
- API version header: `X-GitHub-Api-Version: 2022-11-28`
- Incremental: `?type=reviewed&modified=>={last_sync_iso}&sort=updated&direction=asc&per_page=100`
- fetch_single: `?cve_id=CVE-YYYY-NNNNN&type=reviewed` (returns
  matching advisory)
- Pagination: cursor-based (`before`/`after` params from Link header)

Response includes per advisory: `ghsa_id`, `cve_id`, `summary`,
`description`, `severity`, `cvss_severities` (v3 + v4 with
vector_string and score), `cwes[]` (cwe_id + name), `references[]`
(array of URL strings), `vulnerabilities[]` (ecosystem, package name,
vulnerable_version_range, first_patched_version, vulnerable_functions),
`published_at`, `updated_at`, `withdrawn_at`, `identifiers[]`
(CVE + GHSA), `source_code_location`, `html_url`.

#### Option B: GraphQL API

**Endpoint**: `https://api.github.com/graphql`

Pros:

- Native `updatedSince` parameter on `securityAdvisories` query
- Can request exactly the fields needed (no over-fetching)
- Single request can fetch advisory + vulnerabilities + CVSS

Cons:

- 5,000 point/hour budget (complex queries cost more points)
- Requires GraphQL query construction and response parsing
- Pagination via relay-style cursors (more complex than REST)
- Less documented for security advisories specifically
- Additional complexity without proportional benefit over REST
- Every query costs variable points depending on fields requested

#### Option C: Git Clone (BaseGitFetcher)

**Repository**: `https://github.com/github/advisory-database.git`

Pros:

- Zero rate limits, no authentication needed for clone
- Proven pattern (same as MITRE/Kernel)
- Perfect delta detection via `git diff`
- Natural first-run (record HEAD, no processing)
- Shares infrastructure with existing git fetchers

Cons:

- Repository is LARGE (~4 GB+ full clone; hundreds of thousands of
  files across all types)
- blobless clone likely needed (adds network dependency for `git show`)
- `fetch_single()` requires local lookup — file path uses GHSA-ID not
  CVE-ID, so CVE-ID lookup requires scanning or maintaining an index
- OSV format differs from REST API response — different parser needed
- File path structure:
  `advisories/github-reviewed/YYYY/MM/GHSA-xxxx-xxxx-xxxx/GHSA-xxxx-xxxx-xxxx.json`
- Includes ALL advisory types (reviewed + unreviewed + malware) in
  different directory trees — need path-based filtering
- OSV format has different field names and structure than REST API
- The MITRE/kernel repos are organized by CVE-ID; GHSA repo is
  organized by GHSA-ID — `fetch_single(cve_id)` cannot efficiently
  find the file without scanning or maintaining a reverse index

**Initial assessment**: REST API (Option A) appears most pragmatic.

Key factors:

1. `fetch_single(cve_id)` is a mandatory method. REST API supports
   `?cve_id=X` natively. Git clone would require scanning files by
   GHSA-ID (no CVE-ID index). GraphQL works but adds unnecessary
   complexity
2. REST is simpler than GraphQL with equivalent capabilities for this
   use case
3. Git clone adds significant complexity (large repo, OSV parsing,
   no CVE-ID lookup) without offsetting benefits — unlike MITRE/kernel
   where the repos are small and organized by CVE-ID
4. Forward-only ingestion + incremental `modified` filter keeps
   per-run volume well within rate limits

**Decision**: [PENDING]

---

### OP-2: Advisory Scope

**Decision required**: which advisory types to ingest.

GitHub Advisory Database has three types:

- **reviewed**: GitHub-curated, with ecosystem/package info (VALUE ADD)
- **unreviewed**: imported from NVD feed (REDUNDANT — already ingested
  via `sync_nvd_cves`)
- **malware**: intentionally malicious packages (NOT CVEs — not
  relevant to Sentinel's scope)

**Proposed resolution**: Ingest only `type=reviewed` advisories.

Rationale:

- Unreviewed advisories come from NVD — already ingested directly with
  more complete data (CPE applicability statements, NVD-specific CVSS,
  NVD Source API integration)
- Malware advisories are not CVEs and cannot create tickets in current
  model
- Reviewed advisories are the unique value: curated ecosystem/package
  data, GitHub's own CVSS assessment, CWE classifications
- The REST API defaults to `type=reviewed` when no type is specified

**Decision**: [PENDING]

---

### OP-3: Advisories Without CVE-ID

**Decision required**: what to do with reviewed GHSA advisories that
have no associated CVE-ID.

The REST API response has `cve_id: string | null`. Some reviewed GHSA
advisories exist without a CVE-ID (the CVE may be pending assignment,
or the advisory may never receive one).

**Options**:

A. **Skip advisories without CVE-ID** — only process advisories where
   `cve_id != null`. This is the simplest approach. CVE-ID is the
   canonical identifier in Sentinel; the CVE table has CVE-ID as its
   primary identity field. When GitHub later assigns a CVE-ID (the
   advisory's `updated_at` changes), the next incremental sync picks
   it up automatically.

B. **Track and re-check** — maintain a lightweight list of GHSA-IDs
   without CVE-IDs and periodically re-check. Adds complexity with
   minimal value (the regular sync already handles this naturally
   when the advisory is updated).

**Proposed resolution**: Option A (skip). The advisory will naturally
be ingested on a future sync cycle when its CVE-ID is assigned (the
`updated_at` timestamp changes, triggering the incremental sync
filter).

**Decision**: [PENDING]

---

### OP-4: Affected Version Mapping

**Decision required**: how to map GHSA's `vulnerabilities[]` to
`CVEAffectedVersion` records.

GHSA REST API provides per advisory:

```json
{
  "vulnerabilities": [
    {
      "package": {
        "ecosystem": "npm",
        "name": "lodash"
      },
      "vulnerable_version_range": ">= 4.0.0, < 4.17.21",
      "first_patched_version": "4.17.21",
      "vulnerable_functions": ["merge", "zipObjectDeep"]
    }
  ]
}
```

Sentinel's `CVEAffectedVersion` model fields:

```
vendor | product | package_url | collection_url | package_name |
version | version_type | version_end | version_end_inclusive |
source_container | repo | program_files | cpe
```

**Proposed mapping**:

| GHSA field | CVEAffectedVersion field | Notes |
|---|---|---|
| (constant) | `source_container` | `"ghsa"` |
| `package.name` | `product` | Package name as product |
| `package.name` | `package_name` | Same value, for registry lookup |
| (absent) | `vendor` | NULL — GHSA has no vendor concept |
| `vulnerable_version_range` | `version` + `version_end` + `version_end_inclusive` | See parsing rules below |
| (derived from ecosystem) | `version_type` | See ecosystem mapping table below |
| (derived from ecosystem) | `collection_url` | Registry URL — see ecosystem mapping table below |
| (derived) | `package_url` | Construct PURL from ecosystem + name (if feasible) |
| `vulnerable_functions` | (see sub-question) | Disposition TBD |
| (absent) | `cpe` | NULL — GHSA does not provide CPE |
| `source_code_location` | `repo` | Advisory-level field (not per-vulnerability) |
| (absent) | `program_files` | NULL unless repurposed for vulnerable_functions |

#### Ecosystem mapping table

| GHSA ecosystem | `collection_url` | `version_type` | PURL type |
|---|---|---|---|
| `npm` | `https://www.npmjs.com/` | `semver` | `pkg:npm/` |
| `pip` | `https://pypi.org/` | `semver` | `pkg:pypi/` |
| `go` | `https://pkg.go.dev/` | `semver` | `pkg:golang/` |
| `maven` | `https://repo.maven.apache.org/maven2` | `maven` | `pkg:maven/` |
| `rubygems` | `https://rubygems.org/` | `semver` | `pkg:gem/` |
| `rust` | `https://crates.io/` | `semver` | `pkg:cargo/` |
| `nuget` | `https://www.nuget.org/` | `semver` | `pkg:nuget/` |
| `composer` | `https://packagist.org/` | `semver` | `pkg:composer/` |
| `erlang` | `https://hex.pm/` | `semver` | `pkg:hex/` |
| `pub` | `https://pub.dev/` | `semver` | `pkg:pub/` |
| `swift` | N/A | `semver` | `pkg:swift/` |
| `actions` | `https://github.com/marketplace?type=actions` | `custom` | `pkg:githubactions/` |
| `other` | NULL | `custom` | NULL |

#### Version range parsing rules

GHSA `vulnerable_version_range` uses a comma-separated constraint
syntax. Each `vulnerabilities[]` entry in the REST API response
represents a single package+range combination (GitHub pre-splits
complex ranges into separate entries). The parser handles these
patterns:

| Range format | `version` | `version_end` | `version_end_inclusive` |
|---|---|---|---|
| `< 1.2.3` | NULL | `1.2.3` | `false` |
| `<= 1.2.3` | NULL | `1.2.3` | `true` |
| `>= 1.0, < 2.0` | `1.0` | `2.0` | `false` |
| `>= 1.0, <= 2.0` | `1.0` | `2.0` | `true` |
| `= 1.5.0` | `1.5.0` | `1.5.0` | `true` |
| `> 1.0, < 2.0` | `1.0` | `2.0` | `false` (lower bound NOT inclusive; `version` stores the bound value, semantics derived from operator) |

**Sub-question A**: if `vulnerable_version_range` is NULL or empty,
create one `AffectedVersionEntry` with package info and NULL version
fields (consistent with MITRE "empty versions[] handling").

**Sub-question B**: `vulnerable_functions` disposition.

Options:

1. Store in `program_files` JSONB as-is (array of strings). The field
   is display-only and already stores heterogeneous data (kernel uses
   it for affected source files). The UI tooltip/label can adapt based
   on source context
2. Drop `vulnerable_functions` entirely (low value for SUSE triage —
   the package-level granularity is sufficient)
3. New column `vulnerable_functions` on `CVEAffectedVersion` (adds
   complexity for marginal value)

**Proposed**: Option 2 (drop). The field has low value for SUSE triage
workflows. If needed later, it can be added without breaking changes.

**Sub-question C**: `first_patched_version` disposition.

The REST API provides `first_patched_version` (string) per
vulnerability entry. This is useful information (tells the VA exactly
which version fixes the issue). However, `CVEAffectedVersion` has no
dedicated field for this.

Options:

1. Ignore (information is available via the GHSA URL reference)
2. Use as `version_end` when the range is open-ended (e.g.,
   `< first_patched_version`)
3. New column on `CVEAffectedVersion` (adds complexity)

**Proposed**: already implicitly captured — if the range is
`>= X, < Y`, then `Y` is the patched version. If the range is
`< Y` only, `version_end = Y`. The `first_patched_version` field
is redundant with the parsed range end in most cases. When the range
has `<=` semantics (rare), the patched version is the next version
after `version_end` — this edge case does not warrant a new column.

**Decision**: [PENDING]

---

### OP-5: Schedule

**Decision required**: sync frequency.

**Factors**:

- Rate limit: 5,000 req/hour with token (REST API)
- Volume of updates: GitHub Advisory DB is actively maintained; dozens
  of new/updated advisories per day
- Freshness need: as a discovery fetcher (CNA), faster discovery is
  better for VA triage
- Cost per run: depends on number of modified advisories per cycle.
  At 6h intervals, estimate ~50-200 modified advisories per window =
  1-2 API pages (100/page). Well within rate limits
- Other discovery fetchers: NVD every 6h, MITRE every 6h, Kernel
  every 3h

**Proposed**: Every 6 hours (`0 */6 * * *`) — consistent with
NVD/MITRE.

Rationale: 6h provides 4 sync windows/day. Each sync fetches only
advisories modified since last run. At typical volumes (~100-200
updates per window), each sync requires 1-2 API pages. Well within
rate limits. Worst-case discovery delay is 6 hours (acceptable given
other sources also sync at 6h intervals, and on-demand `fetch_single`
provides immediate access for specific CVEs).

**Decision**: [PENDING]

---

### OP-6: Withdrawn Advisories and CVE State

**Decision required**: how to handle GHSA advisories with
`withdrawn_at` set.

The REST API response includes:

- `withdrawn_at: string | null` — timestamp when advisory was
  withdrawn

**Analysis**:

- A withdrawn GHSA does NOT necessarily mean the CVE is rejected.
  GitHub may withdraw an advisory for various reasons (duplicate GHSA,
  advisory error, moved to another GHSA-ID) while the CVE itself
  remains valid
- CVE rejection is determined by the CVE Program (MITRE) and signaled
  in the CVE record metadata — not in GHSA withdrawal status
- The MITRE and NVD fetchers are authoritative for `cve_state`
  transitions
- However, since GitHub is a CNA, if GitHub rejects a CVE it assigned,
  the rejection would appear in the MITRE cvelistV5 data (the
  canonical source for `cve_state`), not in GHSA metadata

**Proposed resolution**: do NOT set `cve_state` from GHSA
`withdrawn_at`. The fetcher should:

1. **Periodic sync**: skip advisories where `withdrawn_at` is set.
   The query filter `is_withdrawn=false` (REST API param) excludes
   them at the API level — no client-side filtering needed
2. **`fetch_single()`**: if the only advisory matching the requested
   CVE-ID is withdrawn, raise `CVENotInSource` (the source has no
   current active data for this CVE)
3. **Previously ingested data**: if an advisory is withdrawn after
   Sentinel already ingested its data, the data is **preserved**
   (consistent with Red Hat data preservation rule). The advisory
   will simply stop appearing in future incremental syncs. If the
   CVE is truly rejected, the MITRE/NVD fetchers will update
   `cve_state` through their own channels

**Decision**: [PENDING]

---

### OP-7: Source Reference URL

**Decision required**: how to create the source reference
(TicketReference entry) for each advisory.

**Options**:

A. Static `source_reference_url_pattern`: not feasible because the
   URL is `https://github.com/advisories/{ghsa_id}` — the reference
   is per-GHSA-ID, not per-CVE-ID. The pattern mechanism uses
   `{cve_id}` placeholder only.

B. Use `html_url` from API response: the REST API returns `html_url`
   per advisory (e.g.,
   `https://github.com/advisories/GHSA-xxxx-xxxx-xxxx`). Use this
   directly as the source reference URL.

**Proposed resolution**: Option B. No static
`source_reference_url_pattern`. Instead, create the source reference
from the API response's `html_url` field with
`title = "GitHub Advisory"`, `type = advisory`,
`source = "sync_ghsa_advisories"`.

This is consistent with the fetcher's general reference handling
(all references come from the API response, not from patterns).

**Decision**: [PENDING]

---

### OP-8: Data Model — Ecosystem Field on CVEAffectedVersion

**Proposal for consideration**: add an `ecosystem` column to
`CVEAffectedVersion`.

**Current state**: GHSA provides `package.ecosystem` (e.g., "npm",
"pip", "go"). Currently this would map indirectly to `collection_url`
via a hardcoded mapping. But the ecosystem identifier itself has
independent value for:

- UI display (show "npm: lodash >= 4.0.0" rather than a registry URL)
- Filtering (find all CVEs affecting npm packages)
- Cross-source correlation (future: OSV uses the same ecosystem
  concept)
- Package resolution (future: map ecosystem+package to SUSE RPM
  names)

**Schema change**:

```
| ecosystem | VARCHAR(50) | nullable | Package ecosystem identifier (e.g., "npm", "pip", "go"). Populated by GHSA/OSV fetchers |
```

**Impact**:

- Only GHSA and OSV fetchers would populate this field (MITRE/NVD
  use vendor+product, not ecosystems)
- No breaking change (nullable column, added via migration)
- Useful for future `sync_osv_advisories` too (same concept)
- Display-only in the near term; could enable ecosystem-based package
  resolution in the future
- `collection_url` can be derived from `ecosystem` if both are
  stored, but `ecosystem` is more compact and queryable

**Trade-off**: adds a column populated by only 2 of 6+ fetchers. Is
the display and filtering value sufficient to justify the addition?

**Decision**: [PENDING]

---

## Correction Plan (existing docs)

The following changes to existing documents are needed alongside the
fetcher spec. These corrections address inconsistencies discovered
during this analysis.

### 1. `fetcher-infrastructure.md` — Catch-up table correction

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: lines 759-791

Move `sync_ghsa_advisories` from "Fetchers that do NOT need catch_up"
table (line 789) to "Fetchers that implement catch_up" table
(line 761).

Add row:

```
| `sync_ghsa_advisories` | All advisories (global) — but has `fetch_single` | **Default** (via `fetch_single`) | Same as NVD |
```

**Rationale**: as a discovery fetcher with `fetch_single()`, the
default `catch_up()` mechanism applies automatically (same as NVD,
MITRE, Kernel — all global-scope fetchers that have `fetch_single`).
During ticket reactivation (Resolved/Ignored/Duplicated → active),
catch_up re-fetches the ticket's CVE from GitHub to pick up any CVSS,
CWE, or reference changes that occurred while the ticket was inactive.

### 2. `data-sources.md` — Fetcher Registry update

**File**: `docs/data-sources.md`
**Location**: line 963

Update the `sync_ghsa_advisories` row with:

- Schedule: value from OP-5 resolution
- Spec link:
  `[cve-tracking.md](features/tickets/cve-tracking.md#fetcher-sync_ghsa_advisories)`
- Spec Status: `Complete`
- Data Ingested: expanded with accurate detail once field mapping is
  finalized

### 3. `cve-tracking.md` — Add fetcher section

**File**: `docs/features/tickets/cve-tracking.md`
**Location**: after the `sync_kernel_cves` section (before
`sync_osv_advisories` at line 1740)

Add complete `### Fetcher: sync_ghsa_advisories` section following
the Fetcher Documentation Requirements template.

### 4. `cve-tracking.md` — Common First Run Behavior

**File**: `docs/features/tickets/cve-tracking.md`
**Location**: lines 381-401 ("Common First Run Behavior" section)

Add `sync_ghsa_advisories` entry:

```
- `sync_ghsa_advisories`: records the current timestamp as cursor
  without fetching any data (same pattern as `sync_nvd_cves`)
```

### 5. `cve-tracking.md` — CVE Rejection Handling

**File**: `docs/features/tickets/cve-tracking.md`
**Location**: lines 299-303

Whether to add `sync_ghsa_advisories` to the list of fetchers that
can detect rejection depends on OP-6 resolution. If withdrawn
advisories are skipped (proposed), the fetcher does NOT detect
rejection and should NOT be listed here.

---

## Application Plan (Step-by-Step)

This section defines the work sessions needed to produce the complete
fetcher specification. Each session is designed to be self-contained
and completable independently.

### Session 1: Resolve Open Points

**Goal**: make all architectural decisions needed before writing the
spec.

1. Resolve OP-1 (API choice) — evaluate trade-offs, pick REST/GraphQL/Git
2. Resolve OP-2 (scope — reviewed only)
3. Resolve OP-3 (advisories without CVE-ID)
4. Resolve OP-5 (schedule frequency)
5. Resolve OP-6 (withdrawn advisories handling)
6. Resolve OP-7 (source reference URL strategy)
7. Record all decisions in the Decisions Log below

### Session 2: Field Mapping and Data Model

**Goal**: define the complete data extraction specification.

1. Resolve OP-4 (affected version mapping + sub-questions)
2. Resolve OP-8 (ecosystem column proposal)
3. Define complete field mapping table (API response field →
   CVEIngestPayload field) — same format as NVD/MITRE/Red Hat specs
4. Define explicitly ignored fields table — same format as Red Hat
   spec (field + reason)
5. Finalize ecosystem → collection_url mapping table
6. Document version range parsing algorithm with edge cases
7. If OP-8 is accepted: draft the `data-model.md` update for the
   new column

### Session 3: Write Fetcher Spec (Core Sections)

**Goal**: write the complete fetcher section for `cve-tracking.md`.

1. Write properties table
2. Write class structure skeleton (Python class with attributes)
3. Write algorithm (numbered steps for `execute()`)
4. Write `fetch_single()` behavior
5. Write first-run behavior
6. Write error handling section:
   - `fetch_single()` error table (same format as Red Hat)
   - `execute()` error table (periodic batch)
   - Sanitized FetcherError messages table
7. Write metrics definition (`record_created`, `record_updated`,
   `record_failed`)
8. Write custom settings section (if applicable)

### Session 4: Integration, Cross-References, and Review

**Goal**: integrate the spec into existing docs and validate.

1. Apply correction 1: move GHSA in catch-up table
   (`fetcher-infrastructure.md`)
2. Apply correction 2: update Fetcher Registry (`data-sources.md`)
3. Insert fetcher section into `cve-tracking.md`
4. Apply correction 4: add to Common First Run Behavior
5. Apply correction 5: CVE Rejection Handling (if applicable)
6. Verify all cross-references are consistent
7. Invoke `@spec-gap-analyzer` on the new fetcher section
8. Invoke `@spec-coherence-reviewer` for cross-spec consistency
9. Invoke `@fetcher-compliance-reviewer`
10. Invoke `@docs-reviewer`

---

## Decisions Log

Resolved decisions are recorded here as sessions progress.

| # | Decision | Resolution | Date | Session |
|---|----------|------------|------|---------|
| — | — | — | — | — |

---

## Completeness Checklist

Per Fetcher Documentation Requirements
(`fetcher-infrastructure.md:2074-2163`):

- [ ] Properties table (all fields filled, no TBD)
- [ ] `cve_source_type` declared
- [ ] Algorithm (numbered steps, complete execution flow)
- [ ] Error handling (periodic + on-demand, retry behavior, sanitized
      messages)
- [ ] Metrics (`record_created`, `record_updated`, `record_failed`
      definitions)
- [ ] Custom settings table (if applicable)
- [ ] `fetch_single()` behavior documented
- [ ] `fetch_single()` signaling convention referenced
- [ ] Class structure skeleton
- [ ] Field mapping table (API response → CVEIngestPayload)
- [ ] Explicitly ignored fields table
- [ ] First-run behavior documented
- [ ] Source reference URL strategy documented
- [ ] Catch-up classification correct in `fetcher-infrastructure.md`
- [ ] Fetcher Registry updated in `data-sources.md`
- [ ] Common First Run Behavior updated in `cve-tracking.md`
- [ ] CVE Rejection Handling updated (if applicable)
- [ ] Cross-references section includes `docs/api-spec.md`
- [ ] External identifier extraction documented
- [ ] Phase 2 side effects documented (package resolution)
