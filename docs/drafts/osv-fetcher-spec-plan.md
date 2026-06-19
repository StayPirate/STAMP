# OSV Fetcher Specification Plan

Working document for the `sync_osv_advisories` fetcher specification.
Tracks research findings, architectural decisions, and the step-by-step
plan to produce a complete spec in `docs/features/tickets/cve-tracking.md`.

**Status**: In progress — multi-session work  
**Created**: 2026-06-19  
**Last updated**: 2026-06-19

---

## 1. Current State

The OSV fetcher exists only as a TBD stub in
`docs/features/tickets/cve-tracking.md` (lines 2273-2295):

```
| Fetcher name    | sync_osv_advisories |
| Class name      | SyncOsvAdvisories   |
| Schedule        | TBD                 |
| Source          | OSV                 |
| Scope           | TBD                 |
| Auth            | None                |
| Custom settings | No                  |

Algorithm: TBD
Error Handling: TBD
Metrics: TBD
```

References in `docs/data-sources.md` (line 967) and
`docs/features/platform/fetcher-infrastructure.md` (source prefix `osv`)
are also placeholders.

---

## 2. API Research Findings

### 2.1 OSV REST API (https://api.osv.dev)

| Endpoint | Purpose | Auth | Rate Limit |
|----------|---------|------|------------|
| `GET /v1/vulns/{id}` | Fetch single record by ID | None | None |
| `POST /v1/query` | Query by package/version/commit | None | None |
| `POST /v1/querybatch` | Batch query (multiple packages) | None | None |

- No authentication required
- No rate limits (confirmed in FAQ)
- Response size limit: 32 MiB (HTTP/1.1), unlimited (HTTP/2)
- SLO: 99.9% availability, P50 latency ~100ms for GET /v1/vulns/{id}

### 2.2 Record Structure for CVE-Prefixed Records

Querying `GET /v1/vulns/CVE-XXXX-YYYY` returns the NVD-converted record:

```json
{
  "id": "CVE-2024-6119",
  "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/..."}],
  "affected": [{
    "ranges": [{"type": "GIT", "repo": "https://github.com/...",
                "events": [{"introduced": "SHA"}, {"fixed": "SHA"}]}],
    "versions": ["openssl-3.0.0", ...]
  }],
  "references": [
    {"type": "FIX", "url": "https://github.com/.../commit/..."},
    {"type": "ADVISORY", "url": "https://..."}
  ],
  "aliases": ["GHSA-xxxx-xxxx-xxxx", "GO-2024-NNNN"],
  "related": ["SUSE-SU-2024:3105-1", "DSA-1234-1", ...]
}
```

Key observations:
- **No `package` field** on the affected entry (no ecosystem, no name)
- GIT-type ranges only (commit hashes for introduced/fixed)
- `aliases` links to ecosystem-specific records (GHSA, GO, PYSEC, RUSTSEC)
- `related` links to distribution advisories (SUSE-SU, DSA, ALSA, USN)
- OSV computes `aliases` bidirectionally via batch processing

### 2.3 Ecosystem Records (via Aliases)

Fetching alias records (e.g., `GET /v1/vulns/GHSA-h75v-3vvj-5mfj`)
returns rich ecosystem data:

```json
{
  "affected": [{
    "package": {"name": "jinja2", "ecosystem": "PyPI", "purl": "pkg:pypi/jinja2"},
    "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.1.4"}]}],
    "versions": ["2.0", "2.1", ..., "3.1.3"]
  }]
}
```

### 2.4 Link Directionality (Verified)

| CVE record field | Points to | Available for fetch? |
|-----------------|-----------|---------------------|
| `aliases` | GHSA-*, GO-*, PYSEC-*, RUSTSEC-*, BIT-* | Yes (bidirectional, OSV-computed) |
| `related` | SUSE-SU-*, DSA-*, ALSA-*, USN-*, openSUSE-SU-* | Yes |

Not all CVE records have `aliases` (e.g., CVE-2024-6119/openssl has
none). Presence depends on whether ecosystem databases have matching
records that OSV has linked.

### 2.5 Multi-CVE Alias Records (Verified)

Some ecosystem records map to multiple CVEs:

| Record | CVEs in aliases | Implication |
|--------|----------------|-------------|
| GO-2021-0159 | CVE-2015-5739, CVE-2015-5740, CVE-2015-5741 | 3 CVEs |
| GO-2022-0536 | CVE-2019-9512, CVE-2019-9514 | 2 CVEs |
| PYSEC-* (20 tested) | Always 1 CVE | Safe |
| RUSTSEC-* (20 tested) | Always 0 or 1 CVE | Safe |
| GHSA-* (30+ tested) | Always 1 CVE | Safe |

---

## 3. Architectural Decisions

### 3.1 Fetcher Role: Enrichment (Not Discovery)

**Decision**: enrichment fetcher, same pattern as `sync_redhat_cves`.

**Rationale**: OSV aggregates from sources Sentinel already integrates
directly (NVD, GHSA). It does not publish CVE-IDs before primary
sources. The value is in additional metadata: GIT fix commits, typed
references, ecosystem affected versions via aliases, and package names
from distribution advisories.

### 3.2 Three-Phase Fetch Per CVE

| Phase | Source | Data extracted |
|-------|--------|---------------|
| 1. CVE record | `GET /v1/vulns/{cve_id}` | GIT fix/introduce commits, CVSS, typed references, `aliases` list, `related` list |
| 2. Alias records | `GET /v1/vulns/{alias_id}` for each | Affected versions (ecosystem, name, PURL, ranges), CVSS from additional providers, references, package names |
| 3. Related records | `GET /v1/vulns/{related_id}` for each | References (advisory URLs), package names for SMELT resolution |

### 3.3 No Filtering — Let Dedup Handle Overlap

**Decision**: fetch ALL aliases and related records without filtering.
If data overlaps with other fetchers (e.g., GHSA record already
ingested by `sync_ghsa_advisories`), the upsert/dedup mechanisms
handle it:

- `CVECVSSAssessment`: UNIQUE on `(cve_id, provider_name, cvss_version)` — no-op if exists
- `CVEAffectedVersion`: delete-and-reinsert per `(cve_id, source_container)` — OSV writes its own set
- `TicketReference`: UNIQUE on `(ticket_id, url)` — no-op if exists
- `CVEExternalIdentifier`: UNIQUE on `(source, identifier)` with ON CONFLICT DO NOTHING

This eliminates filtering logic complexity with no correctness cost.

### 3.4 Package Names from All Records → `resolved_packages`

All package names from alias AND related records go to
`CVEIngestPayload.resolved_packages` for best-effort SMELT resolution:

- Alias records: `affected[].package.name` (e.g., "jinja2", "stdlib")
- Related records: `affected[].package.name` (e.g., "openssl", "curl")

Expected hit rate: high for system-level C/C++ libraries (names
identical across distros), low for language-specific packages (future
OP-11 prefix mapping would improve this).

### 3.5 CVEExternalIdentifier — Whitelist + Safety Guard

**Whitelist approach** (not blacklist): only explicitly approved
prefixes create `CVEExternalIdentifier` records. All other alias IDs
are fetched for their data but NOT registered as external identifiers.

#### Approved Prefixes

| Prefix | `source` enum | URL pattern | Rationale |
|--------|---------------|-------------|-----------|
| `GHSA` | `GHSA` | `https://github.com/advisories/{id}` | Formal 1:1 guarantee — GitHub assigns one advisory per CVE |
| `PYSEC` | `PYSEC` | `https://osv.dev/vulnerability/{id}` | 1:1 by construction — auto-generated from individual NVD CVEs |
| `RUSTSEC` | `RUSTSEC` | `https://rustsec.org/advisories/{id}` | 1:1 by strong editorial convention — template singular, `related` field exists for multi-CVE cases |

#### Excluded Prefixes

| Prefix | Reason for exclusion |
|--------|---------------------|
| `GO-*` | **Confirmed multi-CVE** — GO-2021-0159 maps 3 CVEs, GO-2022-0536 maps 2 CVEs. A single GO advisory can group related vulnerabilities in the same Go package. Incompatible with automated fix detection |
| `BIT-*` | CVE-ID embedded in the identifier name (e.g., `BIT-golang-2024-24790`) — adds no information beyond the CVE-ID itself. Low operational value |
| `DSA/USN/SUSE-SU/ALSA/openSUSE-SU/*` | Distribution advisories — multi-CVE by design (one update fixes N CVEs) |
| `CVE-*` | Cross-CVE alias (e.g., CVE-2019-9512 aliases CVE-2019-9514). Not an external identifier — the CVE is already the canonical ID |
| All others | Unrecognized prefix — skip silently. Adding a new prefix requires explicit decision |

#### Runtime Safety Guard

Even for whitelisted prefixes, a runtime guard protects against future
deviations from 1:1 policy:

```python
# Before creating CVEExternalIdentifier for an alias record:
cves_in_alias = [a for a in alias_record.get("aliases", []) if a.startswith("CVE-")]
if len(cves_in_alias) == 1:
    # Safe — create CVEExternalIdentifier with ON CONFLICT DO NOTHING
```

If a PYSEC or RUSTSEC record ever maps to multiple CVEs, this guard
silently excludes it. Zero risk for automated fix detection.

#### Overlap with `sync_ghsa_advisories`

Both fetchers may attempt to create the same GHSA external identifier.
The `ON CONFLICT (source, identifier) DO NOTHING` strategy ensures:
- First writer wins (typically `sync_ghsa_advisories` due to 3h schedule)
- OSV fetcher's attempt is a silent no-op
- No duplicate records, no overwrites

### 3.6 Ecosystem Column on CVEAffectedVersion

**Decision**: introduce `ecosystem VARCHAR(50) nullable` column.

- **OSV fetcher**: writes values as-is from `affected[].package.ecosystem`
  (these are the canonical OSV/OSSF names)
- **GHSA fetcher**: normalizes GitHub names to OSV canonical names via a
  12-entry mapping dict (e.g., `pip → PyPI`, `go → Go`, `rust → crates.io`)
- Other fetchers (NVD, MITRE, Red Hat, Kernel): leave NULL (no ecosystem
  concept in their data)

Canonical values come from OSV Schema (OSSF standard):
`PyPI`, `npm`, `Go`, `crates.io`, `Maven`, `NuGet`, `Packagist`, `RubyGems`,
`Pub`, `Hex`, `GitHub Actions`, `SwiftURL`, `SUSE`, `Debian`, `Ubuntu`, etc.

---

## 4. Data Model Changes Required

### 4.1 New Column: `CVEAffectedVersion.ecosystem`

```sql
ALTER TABLE cve_affected_version ADD COLUMN ecosystem VARCHAR(50);
```

- Nullable (most records from NVD/MITRE will remain NULL)
- No index initially (add if filtering proves necessary)
- Populated by: `sync_osv_advisories`, `sync_ghsa_advisories`

### 4.2 New Enum Values: `CVEExternalIdentifierSource`

Current: `GHSA`  
Add: `PYSEC`, `RUSTSEC`

(VARCHAR-backed evolving enum — no migration needed beyond code change)

### 4.3 New CVESourceType Value

Add `"osv"` to the `CVESourceType` evolving enum for `CVESource`
tracking.

### 4.4 Source Prefix Registry

In `fetcher-infrastructure.md`, register: `| osv | OSV (osv.dev) |`
(already present as placeholder).

---

## 5. Specification Sections to Write

Checklist of every section required by the Fetcher Documentation
Requirements (per `fetcher-infrastructure.md`):

- [ ] **Properties table** (complete — all fields filled)
- [ ] **Class structure** (Python class skeleton with attributes)
- [ ] **Algorithm** (3-phase numbered steps)
- [ ] **Field mapping — CVE record** (GIT ranges → CVEAffectedVersion)
- [ ] **Field mapping — alias records** (ecosystem data → CVEAffectedVersion)
- [ ] **Field mapping — related records** (references + package names)
- [ ] **CVEExternalIdentifier policy** (whitelist table + guard logic)
- [ ] **`fetch_single()` design** (on-demand single-CVE fetch)
- [ ] **First run behavior** (forward-only strategy)
- [ ] **Cursor mechanism** (stateless — no cursor)
- [ ] **Error handling — `fetch_single()`** (table: condition/retry/status)
- [ ] **Error handling — `execute()`** (per-CVE + consecutive failure abort)
- [ ] **Sanitized error messages** (table)
- [ ] **Metrics** (record_created/updated/failed definitions)
- [ ] **Custom settings** (throttle_delay_seconds)
- [ ] **Explicitly ignored fields** (table with reasons)
- [ ] **Phase 2 side effects** (resolved_packages)
- [ ] **OSV reference type mapping** (OSV types → Sentinel types)
- [ ] **Scope gap / catch_up** (enrichment scope + default catch_up)

---

## 6. Application Plan (Spec Changes)

Step-by-step plan for applying the specification across project
documents. Each step is a self-contained unit of work suitable for
a single session.

### Step 1: Data Model Update

**File**: `docs/data-model.md`

Changes:
1. Add `ecosystem VARCHAR(50) nullable` to `CVEAffectedVersion` table
2. Add column to ER diagram (`CVEAffectedVersion` entity)
3. Add `PYSEC`, `RUSTSEC` to `CVEExternalIdentifierSource` enum table
4. Add `"osv"` to CVESourceType description (if not implicit)
5. Update safety-net unique constraint comment (ecosystem NOT included —
   same package in different ecosystems from different sources is valid)

### Step 2: Fetcher Infrastructure Update

**File**: `docs/features/platform/fetcher-infrastructure.md`

Changes:
1. Confirm `osv` source prefix in registry table (already present)
2. Add `sync_osv_advisories` to fetcher descriptions table
3. Add `"osv"` to `cve_source_type` registry (CVE Source Type Identity
   section)

### Step 3: OSV Fetcher Full Specification

**File**: `docs/features/tickets/cve-tracking.md`

Replace the TBD stub (lines 2273-2295) with the complete fetcher
specification. This is the largest single piece of work. Subsections:

1. Properties table
2. Class structure
3. Algorithm (3-phase)
4. Field mapping (3 tables: CVE record, alias records, related records)
5. External identifier policy (whitelist + guard)
6. Explicitly ignored fields
7. `fetch_single()` design
8. First run behavior (same as Red Hat — no first-run distinction)
9. Error handling (fetch_single + execute)
10. Metrics
11. Custom settings
12. Phase 2 side effects

### Step 4: CVE Service Update

**File**: `docs/features/tickets/cve-service.md`

Changes:
1. Add `sync_osv_advisories` to Phase 2 package resolution sources
   table (Source 3: CNA/ADP CPE, Source 4: vendor:product — both
   potentially populated by OSV alias records)
2. Add `sync_osv_advisories` to callers table for `upsert_cve()`
3. Update crash recovery section (add OSV sync cycle timing)

### Step 5: Data Sources Registry Update

**File**: `docs/data-sources.md`

Changes:
1. Update OSV section (line 273): change integration status from
   "Planned" to "Specified", update description with actual data flow
2. Update Fetcher Registry table (line 967): fill in Schedule, Auth,
   Rate Limits, Data Ingested, Spec link, change Spec Status to
   "Complete"
3. Update CVE Enrichment Data Structures table: confirm
   `sync_osv_advisories` in `CVEAffectedVersion` populated-by list

### Step 6: GHSA Fetcher Ecosystem Normalization

**File**: `docs/features/tickets/cve-tracking.md`

Changes to `sync_ghsa_advisories` section:
1. Add `ecosystem` to field mapping table (affected versions section):
   `ecosystem` ← normalized value from `.package.ecosystem` via
   mapping dict
2. Add normalization mapping table (12 entries: pip→PyPI, go→Go, etc.)
3. Update "Note on version_type" to reference the ecosystem field
   (the ecosystem IS stored now, but version_type derivation remains
   omitted — different concern)

### Step 7: Resolve Open Points

**File**: `docs/drafts/open-points.md`

1. Mark OP-10 (Ecosystem Column) as **Resolved** with reference to
   this work
2. Add note to OP-11 (Ecosystem Prefix Mapping) that the prerequisite
   (ecosystem column) is now in place; OP-11 remains deferred until
   hit rate is measured

---

## 7. Open Points for Future Sessions

### OP-OSV-1: Schedule Selection

**Context**: the fetcher is stateless (re-fetches all active CVEs each
run). With ~5000 active tickets and ~10-15 API calls per CVE (1 CVE +
aliases + related), a run involves ~50,000-75,000 API calls.

**Options**:
- Daily at a specific time (like Red Hat at 03:00 UTC)
- Every 12 hours
- Every 6 hours (potentially too aggressive given volume)

**Factors**: OSV has no rate limits, but run duration matters. At 200ms
throttle, ~75,000 calls ≈ 4 hours. Daily seems appropriate.

**Decision needed**: exact cron expression.

### OP-OSV-2: Throttle Default Value

**Context**: Red Hat uses 2.0s (conservative, undocumented rate limits).
OSV has no rate limits at all (confirmed in docs + FAQ).

**Options**:
- 0.2s (aggressive but respectful — ~5 req/sec)
- 0.5s (moderate — ~2 req/sec)
- 1.0s (conservative)

**Decision needed**: default value and constraint range.

### OP-OSV-3: `source_container` Value for OSV Records

**Context**: `CVEAffectedVersion` uses `source_container` for
delete-and-reinsert scoping. OSV writes data from multiple sub-sources
(the CVE record itself, GHSA alias records, PYSEC records, etc.).

**Options**:
- (a) Single value `"osv"` for everything OSV writes (simplest — one
  delete-and-reinsert per CVE covers all OSV data)
- (b) Per-alias-source values like `"osv:ghsa"`, `"osv:pysec"`,
  `"osv:go"` (preserves provenance per ecosystem, but complicates
  delete-and-reinsert)

**Recommendation**: option (a) — single `"osv"` value. The OSV fetcher
replaces its entire set on each run (same as Red Hat replaces all Red
Hat data). Provenance is implicit in the `ecosystem` field.

**Decision needed**: confirm (a) or choose (b).

### OP-OSV-4: CVSS Provider Name for OSV Records

**Context**: `CVECVSSAssessment.provider_name` identifies who produced
the CVSS score. When OSV returns a CVSS vector in the CVE record's
`severity[]`, it's typically the NVD-converted score.

**Options**:
- (a) `"OSV"` (generic — marks it as coming from OSV regardless of
  origin)
- (b) `"NVD"` (accurate for CVE records — it IS the NVD score). But
  then it's indistinguishable from `sync_nvd_cves` output, and the
  unique constraint `(cve_id, provider_name, cvss_version)` makes it
  a no-op
- (c) Per-alias-source: when from a GO record use `"Go"`, from PYSEC
  use the ecosystem-specific provider name

**Recommendation**: option (b) for CVE record CVSS (it IS NVD data —
no-op is correct). For alias records, use the source database name as
provider (e.g., GHSA record → `"GitHub"`, which is already the same as
`sync_ghsa_advisories` → also a no-op). The dedup handles overlap
naturally.

**Decision needed**: confirm approach.

### OP-OSV-5: Maximum Aliases/Related to Follow Per CVE

**Context**: extreme cases exist (CVE-2023-44487 HTTP/2 Rapid Reset:
17 aliases + ~80 related = 97 additional calls). Most CVEs have 0-3
aliases and 5-15 related.

**Options**:
- (a) No limit — fetch everything (simplest, consistent with "no
  filtering" philosophy)
- (b) Configurable cap (custom setting, e.g., `max_related_records: 50`)
- (c) Hard cap on `related` only (aliases are high-value, related is
  lower-value reference-only)

**Recommendation**: option (a) for now. The extreme cases are rare and
the total run volume is still manageable. If run duration proves
excessive in practice, add a configurable cap later.

**Decision needed**: confirm (a) or set a cap.

### OP-OSV-6: OSV Reference Type Mapping

**Context**: OSV `references[].type` values need to map to Sentinel's
`TicketReference.type` classification.

**Preliminary mapping** (to be confirmed):

| OSV type | Sentinel type | Notes |
|----------|--------------|-------|
| `FIX` | `patch` | Direct link to fixing commit/PR |
| `INTRODUCED` | `patch` | Link to introducing commit (informational) |
| `REPORT` | `issue` | Bug report link |
| `ADVISORY` | `advisory` | Advisory page |
| `ARTICLE` | `article` | External writeup |
| `PACKAGE` | `package` | Package repository link |
| `EVIDENCE` | NULL | No direct Sentinel equivalent |
| `WEB` | NULL | Generic web link |
| `DETECTION` | NULL | Detection tooling |
| `DISCUSSION` | NULL | Discussion forum link |

**Decision needed**: confirm mapping, especially for types without
Sentinel equivalent.

---

## 8. Cross-References

Documents that need to be consulted during spec writing:

- `docs/features/tickets/cve-tracking.md` — host document for the
  fetcher spec (sync_redhat_cves as pattern)
- `docs/features/tickets/cve-service.md` — upsert_cve() contract,
  Phase 2 package resolution sources
- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher
  contract, fetch_single, catch_up, custom settings, error message
  sanitization
- `docs/data-model.md` — CVEAffectedVersion, CVEExternalIdentifier,
  CVECVSSAssessment schemas
- `docs/data-sources.md` — Fetcher Registry table
- `docs/features/tickets/ticket-references.md` — reference type
  classification
- `docs/features/tickets/cvss-scoring.md` — CVSS provider_name
  conventions
- `docs/drafts/open-points.md` — OP-10 (ecosystem column), OP-11
  (prefix mapping)

---

## 9. Session Log

### Session 1 (2026-06-19)

- Completed full API research (REST endpoints, GCS data dumps)
- Verified record structure for CVE, GHSA, GO, PYSEC, RUSTSEC records
- Confirmed `aliases` field IS bidirectional (OSV batch-computes it)
- Confirmed multi-CVE problem for GO-* records
- Confirmed 1:1 mapping for PYSEC (20/20) and RUSTSEC (20/20)
- Checked upstream docs: no formal 1:1 guarantee, but strong
  convention/construction
- Established all architectural decisions (3.1-3.6)
- Identified 6 open points for future sessions
- Created this plan document
