# OSV Fetcher Specification Plan

Working document for the `sync_osv_advisories` fetcher specification.
Tracks research findings, architectural decisions, and the step-by-step
plan to produce a complete spec in `docs/features/tickets/cve-tracking.md`.

**Status**: All decisions resolved — ready for application  
**Created**: 2026-06-19  
**Last updated**: 2026-06-19  
**Open points remaining**: 0 (all resolved in sessions 3-5)

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

### References in Other Documents (verified line numbers)

| Document | Line(s) | Content | Status |
|----------|---------|---------|--------|
| `fetcher-infrastructure.md` | 236 | Source prefix `osv` in registry | OK |
| `fetcher-infrastructure.md` | 826 | Opt-out group (KEV/EPSS/OSV) | **INCORRECT** — must be removed (see 3.7) |
| `fetcher-infrastructure.md` | 828-836 | Note grouping OSV with KEV/EPSS | **INCORRECT** — must be corrected |
| `fetcher-infrastructure.md` | 1207 | Class hierarchy: `SyncOsvAdvisories (planned)` | **INCORRECT** — says "discovery + enrichment" but role is enrichment only (decision 3.1). Correct to "API-based CVE enrichment" |
| `cve-tracking.md` | 1241 | Incidental mention "OSV.dev/Google" | OK (no change) |
| `cve-tracking.md` | 2273-2295 | TBD stub | Replace with full spec |
| `cve-service.md` | 359-360 | Phase 2 sources `[Planned]` | Update |
| `cve-service.md` | 1213 | Callers table — only `upsert_cve()` | Add `record_source_status() (failure path)` |
| `data-sources.md` | 35 | Summary table: "Planned" | See Step 8 (summary table alignment) |
| `data-sources.md` | 273-294 | OSV section description | Update with design details; **remove** CVSS references (per decision 3.8) |
| `data-sources.md` | 967 | Fetcher Registry row — all TBD | Complete all fields, `Spec Status: Complete` |
| `data-sources.md` | 981 | CVE Enrichment structures | OK (already lists OSV) |

### Post-Refactoring Context (commit 8fea018)

Since sessions 1-2, a major refactoring introduced `BaseCVEFetcher` as
an intermediate abstract class between `BaseFetcher` and all CVE
fetchers. Key impacts on this draft:

- All CVE fetchers now MUST inherit from `BaseCVEFetcher` (not
  `BaseFetcher` directly)
- `fetch_single()` is an abstract method on `BaseCVEFetcher` —
  structurally enforced, not optional
- `catch_up()` has a default implementation on `BaseCVEFetcher` that
  calls `fetch_single()`
- `participates_in_catch_up` class attribute controls opt-out (default
  `True`)
- `cve_source_type` is an abstract class attribute with import-time
  uniqueness validation
- `source_reference_url_pattern` is a class attribute on `BaseCVEFetcher`
- The spec erroneously classified OSV in the KEV/EPSS opt-out group —
  this contradicts the draft's design and must be corrected

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
| 1. CVE record | `GET /v1/vulns/{cve_id}` | GIT fix/introduce commits, typed references, `aliases` list, `related` list |
| 2. Alias records | `GET /v1/vulns/{alias_id}` for each | Affected versions (ecosystem, name, PURL, ranges), references, package names |
| 3. Related records | `GET /v1/vulns/{related_id}` for each | References (advisory URLs), package names for SMELT resolution |

Note: `severity` fields are present in Phase 1 and Phase 2 responses
but deliberately ignored (see decision 3.8).

### 3.3 No Filtering — Let Dedup Handle Overlap

**Decision**: fetch ALL aliases and related records without filtering.
If data overlaps with other fetchers (e.g., GHSA record already
ingested by `sync_ghsa_advisories`), the upsert/dedup mechanisms
handle it:

- `CVEAffectedVersion`: delete-and-reinsert per `(cve_id, source_container)` — OSV writes its own set
- `TicketReference`: UNIQUE on `(ticket_id, url)` — no-op if exists
- `CVEExternalIdentifier`: UNIQUE on `(source, identifier)` with ON CONFLICT DO UPDATE (per `cve-service.md` contract — last writer wins, updates `cve_id`, `url`, `updated_at`)

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
    # Safe — include in CVEIngestPayload.external_identifiers (upsert_cve handles ON CONFLICT DO UPDATE)
```

If a PYSEC or RUSTSEC record ever maps to multiple CVEs, this guard
silently excludes it. Zero risk for automated fix detection.

#### Overlap with `sync_ghsa_advisories`

Both fetchers may attempt to create the same GHSA external identifier.
The `ON CONFLICT (source, identifier) DO UPDATE` strategy (per
`cve-service.md`) ensures:
- Last writer wins — updates `cve_id`, `url`, `updated_at`
- If a GHSA is reassociated with a different CVE, the record is
  corrected by whichever fetcher runs next
- No duplicate records

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

### 3.7 Inheritance and catch_up Participation

**Decision**: inherit from `BaseCVEFetcher`. Participate in catch_up
(default behavior, `participates_in_catch_up = True`).

**BaseCVEFetcher inheritance** (structurally required — not optional):

- `cve_source_type = "osv"` (class attribute, import-time validated)
- `fetch_single(cve_id, session)` (abstract method, must implement)
- Inherits default `catch_up()` (extracts `cve_id` from ticket → calls
  `fetch_single()` → catches `CVENotInSource` as no-op)
- `source_reference_url_pattern = "https://osv.dev/vulnerability/{cve_id}"`
- Import-time validation: `cve_source_type` uniqueness + Enum membership

**catch_up rationale**: OSV uses the same execution model as Red Hat:

| Execution mode | Scope | Trigger |
|---|---|---|
| `execute()` (scheduled) | Only CVEs with active tickets | Cron schedule |
| `fetch_single()` (on-demand) | Any CVE | Ticket creation, CVE association, refetch |
| `catch_up()` (reactivation) | Specific CVE of reactivated ticket | `reconcile_ticket_status()` on inactive-state exit |

The `execute()` scope is limited to active tickets. CVEs whose tickets
are in inactive status (Ignored, Duplicated, Resolved) do NOT receive
OSV enrichment during the inactive period. This creates a **scope gap**
identical to Red Hat's (documented at `cve-tracking.md:2434-2440`).

The default `catch_up()` (inherited from `BaseCVEFetcher`) fills this
gap: when a ticket is reactivated, `catch_up()` calls
`fetch_single(cve_id)` for immediate recovery without waiting for the
next periodic run.

**Why NOT opt-out (unlike KEV/EPSS)**: KEV and EPSS opt out because
their `execute()` syncs the **entire catalog** regardless of ticket
status — there is no gap to recover. OSV does NOT sync the entire
catalog; it queries per-CVE for active tickets only.

**Correction required**: `fetcher-infrastructure.md:826` erroneously
groups OSV with KEV/EPSS. Must be removed from the opt-out table and
the explanatory note (lines 828-836) must be updated to mention only
KEV and EPSS.

### 3.8 CVSS Scores: Do Not Extract

**Decision**: do NOT extract CVSS scores from OSV responses. The
`severity` field is added to the "Explicitly Ignored Fields" table.

**Rationale** (verified against live API data):

1. The OSV schema's `severity` field provides only `{type, score}` —
   there is NO attribution/provider field. It is impossible to
   determine reliably who produced the score without hardcoding a
   mapping.

2. The CVSS data from OSV adds zero value beyond what dedicated
   fetchers already provide:
   - CVE records (`/v1/vulns/CVE-*`): NVD-converted score →
     already ingested by `sync_nvd_cves` with explicit attribution
   - GHSA alias records: GitHub score → already ingested by
     `sync_ghsa_advisories` with `provider_name = "GitHub"`
   - PYSEC alias records: typically the same NVD score
   - GO alias records: **no `severity` field at all** (verified)
   - RUSTSEC alias records: rare, low value for SUSE packages

3. Extracting CVSS would require either:
   - A hardcoded `id_prefix → provider_name` mapping (maintenance
     burden, fragile if OSV changes internal aggregation)
   - A generic `"OSV"` provider name (creates duplicates with
     existing NVD/GitHub records under different keys)

4. The value of OSV for Sentinel is in **affected versions**,
   **references**, and **package names** — not CVSS scores.

**Impact on spec**: no `CVECVSSAssessment` records are created by this
fetcher. The `CVEIngestPayload.cvss_assessments` field is always empty.
This simplifies the algorithm (no CVSS parsing, no provider derivation)
and eliminates OP-OSV-4 entirely.

**Spec placement**: the full rationale above MUST be transcribed into
the "Explicitly Ignored Fields" table of the `sync_osv_advisories`
section in `cve-tracking.md`. The table entry for `severity` must be
self-contained — a future reader must understand the decision without
consulting this draft. Include: (1) what the OSV schema lacks, (2) what
was verified against live data, (3) why existing fetchers already cover
the data, (4) what would go wrong if we extracted anyway.

### 3.9 Transaction Boundaries and Multi-Phase Atomicity

**Decision**: all HTTP requests (Phase 1, 2, and 3) complete and their
results accumulate in memory before calling `upsert_cve()`. The database
transaction (and any row-level lock on the CVE) is acquired only during
the final write.

**Rationale**:

1. **Data regression prevention**: the `source_container = "osv"`
   strategy uses delete-and-reinsert. If `upsert_cve()` were called
   with partial data (e.g., Phase 1 succeeded but Phase 2 failed
   midway), the DELETE would remove all previously-ingested ecosystem
   data and the INSERT would write only the incomplete set — a net data
   loss compared to the previous run.

2. **I/O-then-Lock compliance** (per `conventions.md`): holding a
   `FOR UPDATE` lock while performing N+M HTTP requests to an external
   service (potentially 20+ seconds for extreme CVEs) would block all
   concurrent mutations on the same CVE for the duration.

**Consequence**: if Phase 1 returns an HTTP error (5xx, network
failure), the CVE is marked `record_failed` and `upsert_cve()` is
never called — previous data remains intact. If Phase 1 returns HTTP
404, `CVENotInSource` is raised (clean skip, no metric, no upsert).
If individual alias/related records fail during Phase 2/3, the
successfully-fetched data is kept and `upsert_cve()` is called with
the partial (but valid) result (see decision 3.10).

**Boundary "no extractable data"**: if Phase 1 returns HTTP 200 but
the record contains no extractable data (no `affected`, `references`,
`aliases`, or `related`), the fetcher treats the CVE as
`CVENotInSource` — clean skip, no metric, no upsert, previous data
intact. A record that is only `{"id": "CVE-..."}` is an NVD-imported
placeholder with zero enrichment value. Consistent with the Red Hat
fetcher pattern (`cve-tracking.md:2583`: "HTTP 200 with no extractable
data → Raise `CVENotInSource`").

### 3.10 Per-Alias Failure Isolation

**Decision**: individual alias or related records that return HTTP
errors are skipped. The remaining successfully-fetched data is kept.

**Behavior**:

- A single alias/related returning HTTP 500/timeout → log WARNING,
  skip that record, continue to the next
- Data from all successful alias/related fetches is accumulated
  normally alongside Phase 1 data
- The CVE counts as `record_updated` (enrichment is partial but valid)
- Failed alias/related records do NOT increment the abort threshold
  counter (see decision 3.11)
- No retry logic for individual sub-requests — the next daily run
  retries naturally (stateless fetcher)

**Rationale**: a transient HTTP 500 on one PYSEC alias should not
block the update of GIT ranges and 4 other successfully-fetched
aliases. The proportionality principle applies: Phase 1 data alone is
already valuable enrichment.

**Metric strategy**: `record_updated()` is called every time
`upsert_cve()` executes successfully, regardless of whether the data
changed compared to the previous run. The metric means "CVEs processed
and written successfully in this run", not "CVEs with actually new
data". Consistent with all other CVE fetchers (Red Hat, GHSA, NVD). No
change-detection pre-write.

Full signaling schema:

| Condition | Metric action |
|-----------|---------------|
| `upsert_cve()` called successfully | `record_updated()` |
| `CVENotInSource` (HTTP 404 or "no extractable data" boundary) | No metric |
| Guard 3.12 triggers (skip upsert) | `record_failed()` |
| Phase 1 HTTP error (5xx / network) | `record_failed()` |

**Edge case**: if ALL alias/related records fail but Phase 1 succeeds,
see decision 3.12 — `upsert_cve()` is NOT called (previous data
preserved). The CVE counts as `record_failed`.

### 3.11 Abort Threshold Semantics

**Decision**: 3 consecutive Phase 1 failures abort the entire
`execute()` run. Only Phase 1 HTTP errors count toward the threshold.

**What increments the counter**:

| Condition | Increments? | Rationale |
|-----------|-------------|-----------|
| Phase 1 returns HTTP 5xx / network error | Yes | Infrastructure failure signal |
| Phase 1 returns HTTP 404 (`CVENotInSource`) | No — clean skip | CVE not in OSV, normal condition |
| Phase 2/3 individual alias/related fails | No | Per-alias isolation (3.10) |
| Phase 2/3 all aliases fail but Phase 1 OK | No | Guard 3.12 skips upsert; CVE counted as `record_failed` |

**What resets the counter**: any CVE where Phase 1 returns HTTP 200
(regardless of Phase 2/3 outcome) resets the counter to zero.

**Threshold**: 3 consecutive failures (aligned with `sync_redhat_cves`
pattern at `cve-tracking.md:2625`).

**On abort**: the `execute()` loop terminates early. The fetcher
records status as `failure` with a sanitized message. The next
scheduled run retries from the beginning (stateless — no cursor).

### 3.12 Per-CVE Guard on Phase 2/3 Completeness

**Decision**: if the CVE record lists alias and/or related IDs, but
zero Phase 2/3 sub-requests succeed, `upsert_cve()` is NOT called for
that CVE. Previous data remains intact. The CVE counts as
`record_failed`.

**Rule**:

| Condition | Action |
|-----------|--------|
| Phase 1 has alias/related IDs, ≥1 Phase 2/3 fetch OK | Proceed with upsert (partial but valid) |
| Phase 1 has alias/related IDs, 0 Phase 2/3 fetches OK | **Skip upsert**, `record_failed()` |
| Phase 1 has no alias and no related | Proceed (Phase 1 is the complete dataset) |

**Rationale**: delete-and-reinsert for `(cve_id, "osv")` is the
correct mechanism when the fetcher has a complete snapshot. Calling it
with only Phase 1 data when the source provides alias/related records
causes regression — the DELETE removes previously-ingested ecosystem
data and the INSERT writes only GIT ranges. The guard prevents this
without global state or configuration (a single `if` per CVE).

**Trade-off accepted**: in the remote case where OSV keeps an alias ID
in the CVE record but the alias record itself has been deleted (an
internal OSV inconsistency), the guard prevents reflecting the removal.
The previous data (valid at the time of its successful fetch) is
preserved. This is preferable to risking loss of valid enrichment data.

**Interaction with decision 3.11**: the abort threshold table row
"Phase 2/3 all aliases fail but Phase 1 OK" changes consequence —
previously said "CVE is updated"; now the CVE is skipped (no upsert)
and counted as `record_failed`. The abort threshold counter is still
NOT incremented (Phase 1 succeeded, infrastructure is not failing).

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

(VARCHAR(20) + Python Enum in `app/core/enums.py` — adding values
requires only a code change, no Alembic migration. Aligned with
`CVESourceType` pattern per `data-model.md`.)

### 4.3 New CVESourceType Value

Add `"osv"` to the `CVESourceType` evolving enum for `CVESource`
tracking. This value is declared as `cve_source_type = "osv"` on the
`SyncOsvAdvisories` class and validated at import time by
`BaseCVEFetcher.__init_subclass__` (uniqueness + Enum membership).

### 4.4 Source Prefix Registry

In `fetcher-infrastructure.md`, the `osv` source prefix is already
registered (line 236: `| osv | OSV (osv.dev) |`). No change needed.

---

## 5. Specification Sections to Write

Checklist of every section required by the Fetcher Documentation
Requirements (per `fetcher-infrastructure.md`). The properties table
must follow the extended format used by all `BaseCVEFetcher` subclasses
(see `sync_redhat_cves` at `cve-tracking.md:2297-2310` as template).

- [ ] **Properties table** (extended BaseCVEFetcher format):
  - Standard: `Fetcher name`, `Class name`, `Schedule`, `Source`,
    `Scope`, `Auth`, `Custom settings`
  - BaseCVEFetcher: `cve_source_type = "osv"`,
    `fetch_single() = Yes — OSV REST API single CVE query`,
    `source_reference_url_pattern = "https://osv.dev/vulnerability/{cve_id}"`
- [ ] **Class structure** (inherits `BaseCVEFetcher`, includes
  `cve_source_type`, `source_reference_url_pattern`, `fetch_single()`
  signature, `execute()` signature, `catch_up` inherited comment)
- [ ] **Algorithm** (3-phase numbered steps for both `execute()` loop
  and per-CVE `fetch_single()` core logic)
- [ ] **Field mapping — CVE record** (GIT ranges → CVEAffectedVersion)
- [ ] **Field mapping — alias records** (ecosystem data → CVEAffectedVersion)
- [ ] **Field mapping — related records** (references + package names)
- [ ] **CVEExternalIdentifier policy** (whitelist table + guard logic)
- [ ] **`fetch_single(cve_id)` design** (signaling convention:
  normal return = success, `CVENotInSource` if HTTP 404 or empty,
  other exceptions propagate for Celery retry)
- [ ] **Scope gap + catch_up** (same pattern as Red Hat
  `cve-tracking.md:2434-2440` — inherited default, participates)
- [ ] **First run behavior** (stateless — no first-run distinction,
  same as Red Hat)
- [ ] **Cursor mechanism** (stateless — no cursor)
- [ ] **Error handling — `fetch_single()`** (follows standard signaling
  convention per `fetcher-infrastructure.md:323-409`)
- [ ] **Error handling — `execute()`** (per-CVE isolation +
  consecutive failure abort threshold)
- [ ] **Sanitized error messages** (table per
  `fetcher-infrastructure.md:839-920`)
- [ ] **Metrics** (`record_created`/`record_updated`/`record_failed`
  definitions)
- [ ] **Custom settings** (`throttle_delay_seconds`: default=0.2,
  ge=0.05, le=10.0)
- [ ] **Explicitly ignored fields** (table — includes `severity` per
  decision 3.8. **Important**: the rationale for skipping CVSS must be
  self-contained in this table so it survives independently of the
  draft. The entry must explain: (1) OSV schema provides no provider
  attribution in `severity`, (2) live API verification confirmed GO
  records have no severity at all, (3) all useful CVSS data already
  ingested by dedicated fetchers with explicit provenance, (4) extracting
  would require fragile `id_prefix → provider` mapping or create
  duplicate rows under a generic name. See section 3.8 for the full
  reasoning to transcribe.)
- [ ] **Phase 2 side effects** (`resolved_packages` → SMELT resolution)
- [x] **OSV reference type mapping** (resolved — see OP-OSV-6)
- [x] **Transaction boundaries and multi-phase atomicity** (resolved —
  see decision 3.9)
- [x] **Per-alias failure isolation** (resolved — see decision 3.10)
- [x] **Abort threshold semantics** (resolved — see decision 3.11)
- [x] **Throttle scope** (resolved — see OP-OSV-2 amendment)
- [x] **Per-CVE guard on Phase 2/3 completeness** (resolved — see
  decision 3.12)
- [x] **Boundary "no extractable data"** (resolved — see decision 3.9
  amendment)
- [x] **Metric strategy and signaling schema** (resolved — see
  decision 3.10 amendment)

---

## 6. Application Plan (Spec Changes)

Step-by-step plan for applying the specification across project
documents. Each step is a self-contained unit of work suitable for
a single session. Line numbers verified against current file state
(post-commit 8fea018).

### Step 1: Data Model Update

**File**: `docs/data-model.md` (1502 lines)

Changes:
1. Add `ecosystem VARCHAR(50) nullable` to `CVEAffectedVersion` table
2. Add column to ER diagram (`CVEAffectedVersion` entity)
3. Add `PYSEC`, `RUSTSEC` to `CVEExternalIdentifierSource` enum table
4. Add `"osv"` to CVESourceType description (if not implicit)
5. Update safety-net unique constraint comment (ecosystem NOT included —
   same package in different ecosystems from different sources is valid)
6. Convert `CVEExternalIdentifierSource` from PG ENUM to VARCHAR(20) +
   Python Enum:
   - Rewrite section "CVEExternalIdentifierSource Enum" (line 544)
     following `CVESourceType` pattern (lines 471-497): header
     "Python Enum", explicit "NOT a PostgreSQL ENUM", instructions for
     adding values
   - Column `source` in `CVEExternalIdentifier` table (line 564): type
     from `ENUM(CVEExternalIdentifierSource)` to `VARCHAR(20)`
   - Cross-reference in `CVESource.source` description (line 451):
     "PG ENUM, e.g., `GHSA`" → "VARCHAR, Python Enum, e.g., `GHSA`"
   - General note (line 1494): from "ENUM types are defined as
     PostgreSQL enums" to hybrid note (stable=PG ENUM,
     evolving=VARCHAR + Python Enum)

### Step 2: Fetcher Infrastructure Correction

**File**: `docs/features/platform/fetcher-infrastructure.md` (2875 lines)

Changes:

| Line(s) | Action |
|---------|--------|
| 236 | Verify `osv` source prefix — already present, OK |
| 797-803 | Add `sync_osv_advisories` to the list of fetchers using default `catch_up()` inherited from BaseCVEFetcher |
| **826** | **Remove** `sync_osv_advisories` row from the opt-out table |
| **828-836** | **Rewrite** the explanatory note to mention only `sync_cisa_kev` and `sync_epss_scores` (remove all OSV references) |
| 1207 | **Correct** classification from "API-based CVE discovery + enrichment" to "API-based CVE enrichment" (per decision 3.1 — enrichment only) |

Additional (if not already present):
- Add `"osv"` to `cve_source_type` registry in the CVE Source Type
  Identity section (lines 421-489) if not already listed in the
  `CVESourceType` Enum values table

### Step 3: OSV Fetcher Full Specification

**File**: `docs/features/tickets/cve-tracking.md` (2914 lines)

Replace TBD stub (lines 2273-2295) with complete fetcher specification.
Template: `sync_redhat_cves` (lines 2297-2550). Subsections:

1. Properties table (extended BaseCVEFetcher format)
2. Class structure (inherits `BaseCVEFetcher`)
3. Algorithm (3-phase per-CVE, called from both `execute()` loop and
   `fetch_single()`)
4. Field mapping — CVE record (GIT ranges, references)
5. Field mapping — alias records (ecosystem affected versions,
   references, package names)
6. Field mapping — related records (references, package names)
7. External identifier policy (whitelist + runtime guard)
8. Explicitly ignored fields (includes `severity` — see decision 3.8)
9. `fetch_single(cve_id)` design (signaling convention)
10. Scope gap + catch_up (inherited from BaseCVEFetcher, participates)
11. Error handling (`fetch_single` + `execute`)
12. Metrics
13. Custom settings (`throttle_delay_seconds`: 0.2, ge=0.05, le=10.0)
14. Phase 2 side effects (`resolved_packages`)
15. OSV reference type mapping

### Step 4: CVE Service Update

**File**: `docs/features/tickets/cve-service.md` (1238 lines)

| Line(s) | Action |
|---------|--------|
| 357 | Add `sync_osv_advisories` to `resolved_packages` populated-by column in Phase 2 sources table |
| 359-360 | **Remove** `sync_osv_advisories [Planned]` from CNA/ADP CPE and vendor:product rows — OSV does not provide CPE or vendor:product data (per decision 3.4, package names go to `resolved_packages` only) |
| **1213** | Change from `upsert_cve()` to `upsert_cve(), record_source_status() (failure path)` — consistent with Red Hat/GHSA pattern |
| ~1117-1122 | Update `resolved_packages` field definition: acknowledge both "exact match" and "best-effort" usage patterns |
| ~1037-1057 | Add `ecosystem: str \| None = Field(None, max_length=50)` to `AffectedVersionEntry` schema |
| 1089-1090 | Change comment `# DB: PG ENUM but evolving` → `# DB: VARCHAR(20), Python Enum` |

### Step 5: Data Sources Registry Update

**File**: `docs/data-sources.md` (986 lines)

| Line(s) | Action |
|---------|--------|
| 273-294 | Update OSV section description with actual design details (3-phase, no auth, no rate limits). **Remove** CVSS references: line 283 ("CVSS scores (aggregated from source databases)") and line 290 ("CVSS scores are stored as `CVECVSSAssessment` entries") — per decision 3.8, CVSS is explicitly NOT extracted |
| 289 | Change "Schedule: TBD" to actual cron (per OP-OSV-1 decision) |
| **967** | Complete Fetcher Registry row: schedule, auth, rate limits, data ingested, spec link `[cve-tracking.md](features/tickets/cve-tracking.md#fetcher-sync_osv_advisories)`, `Spec Status: Complete` |

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

### Step 7b: New Open-Point — Fetcher Metrics Granularity

**File**: `docs/drafts/open-points.md`

Add a new open-point:

> **OP-N: Fetcher metrics — granularity and semantics**
>
> Current problem: `record_updated` is incremented for every CVE where
> `upsert_cve()` succeeds, regardless of whether the data actually
> changed compared to the previous run. The metric means "processed"
> not "updated with new data." It loses diagnostic value as the system
> matures.
>
> Evaluate the feasibility of:
> - `record_updated` → only when written data differs from previous
>   state (change-detection pre-write)
> - `record_skipped` → CVE processed but no upsert performed (e.g.,
>   `CVENotInSource`, completeness guard 3.12)
> - `record_missed` → CVEs tracked by Sentinel that the fetcher does
>   not cover (delta between active tickets and CVEs present in the
>   source)
>
> Impact: cross-cutting on `BaseFetcher`/`BaseCVEFetcher` and the
> fetcher-operations dashboard. Must be evaluated together with the
> dashboard design.

### Step 8: Summary Table Alignment

**File**: `docs/data-sources.md` (lines 25-41)

The "Integration status" column is inconsistent: GHSA has a complete
spec (`Spec Status: Complete` in the Fetcher Registry at line 965) but
is marked "Planned" in the summary table. No fetcher has code
implementation yet — the project is entirely in specification phase.

**Alignment rule**: use `Specified` for sources with a complete fetcher
spec (ready for implementation); keep `Planned` for those with
incomplete or TBD specs.

| Line | Source | Current | Fetcher Registry Status | Correct value |
|------|--------|---------|------------------------|---------------|
| 31 | CISA KEV | Planned | TBD | Planned (unchanged) |
| 32 | EPSS | Planned | TBD | Planned (unchanged) |
| **33** | **GHSA** | **Planned** | **Complete** | **Specified** |
| 34 | Linux Kernel CVE | Active | Complete | **Specified** |
| **35** | **OSV** | **Planned** | TBD → Complete | **Specified** (after Step 3) |

Note: NVD, MITRE, IBS, SMELT, AIMAAS, AD are currently "Active" with
complete or partial specs. These should also become "Specified" for
consistency (none have implementation), but this is a broader
normalization. Minimum viable fix: correct GHSA (33) and Kernel (34),
then set OSV (35) after Step 3 is applied.

### Step 9: Update This Draft

After all application steps, update this document:
- Mark resolved open points
- Update session log
- Change status to "Complete — ready for application" or archive

### Step 10: Post-Application Review

Run the following reviewers to verify correctness and coherence of the
applied changes:

1. **`@spec-coherence-reviewer`** on `docs/features/tickets/cve-tracking.md`
   — verify no contradictions with other fetcher specs (Red Hat, GHSA,
   NVD, MITRE, Kernel) and cross-cutting documents
2. **`@data-model-reviewer`** — verify `ecosystem` column addition,
   `CVEExternalIdentifierSource` VARCHAR conversion, and `CVESourceType`
   new value are consistent with existing conventions
3. **`@docs-reviewer`** — verify documentation completeness across all
   modified files (data-model, data-sources, cve-service,
   fetcher-infrastructure, cve-tracking)
4. **`@fetcher-compliance-reviewer`** on the new `sync_osv_advisories`
   spec — verify correct BaseCVEFetcher inheritance, metrics, and
   dashboard representation

Address any findings rated "Needs revision" before proceeding to
Step 11. Minor issues should be fixed inline.

### Step 11: Delete This Draft

Once all application steps are complete, all reviewers pass, and the
specification lives in its permanent location
(`docs/features/tickets/cve-tracking.md`), delete this working document:

```
rm docs/drafts/osv-fetcher-spec-plan.md
```

This file is a working artifact — all decisions and rationale must be
captured in the specification itself (Explicitly Ignored Fields table,
inline notes, etc.) before deletion. Verify that no information is lost
that is not already present in the applied spec.

---

## 7. Open Points — ALL RESOLVED

### OP-OSV-1: Schedule Selection — RESOLVED

**Decision**: `0 5 * * *` (daily at 05:00 UTC)

**Rationale**: slot completely free (02:00-04:00 occupied by IBS/Red
Hat/LDAP chain). Results available for VAs when they start working in
the morning (~07:00 CET). Run duration ~4.2h at 0.2s throttle fits
comfortably in the 24h period.

### OP-OSV-2: Throttle Default Value — RESOLVED

**Decision**: `throttle_delay_seconds` = `0.2` (default), range
`ge=0.05, le=10.0`.

**Scope**: the throttle delay applies between every individual HTTP
request regardless of phase (Phase 1, Phase 2 sub-requests, Phase 3
sub-requests). The 4.2h runtime estimate assumes this uniform pacing.

**Rationale**: OSV has no rate limits (confirmed in docs + FAQ). At
0.2s (~5 req/sec), a full run of ~75,000 calls takes ~4.2h — well
within the 24h daily window. The wide range allows:
- 0.05s (20 req/sec) for emergency catch-up
- 10.0s for self-throttling if OSV introduces rate limits in future

### OP-OSV-3: `source_container` Value — RESOLVED

**Decision**: single value `"osv"` for all `CVEAffectedVersion` records
written by this fetcher (Phase 1 GIT ranges + Phase 2 ecosystem
versions from alias records).

**Rationale**: one delete-and-reinsert per `(cve_id, "osv")` replaces
the entire OSV set cleanly each run. Same strategy as Red Hat
(`source_container = "redhat"`). Provenance is traceable via the
`ecosystem` column. Phase 3 (related records) does NOT produce
`CVEAffectedVersion` — only `TicketReference` + `resolved_packages`.

### OP-OSV-4: CVSS Provider Name — RESOLVED (Skip Entirely)

**Decision**: do NOT extract CVSS scores from OSV. See architectural
decision 3.8 for full rationale.

**Summary**: the OSV schema's `severity` field provides no provider
attribution. All CVSS data available from OSV is already covered by
dedicated fetchers (NVD, GHSA) with explicit attribution. The `severity`
field is documented in the "Explicitly Ignored Fields" table.

### OP-OSV-5: Maximum Aliases/Related — RESOLVED

**Decision**: no limit — fetch all aliases and related records without
cap.

**Rationale**: extreme cases (e.g., CVE-2023-44487: 97 extra calls)
add ~20s per CVE. With <1% of CVEs being extreme, the total impact on
run duration is negligible (~200s). The simplicity of "no filtering"
(decision 3.3) is preserved. A configurable cap can be added later if
run duration proves excessive in practice.

### OP-OSV-6: OSV Reference Type Mapping — RESOLVED

**Context**: OSV `references[].type` values need to map to Sentinel's
`TicketReference.type` classification.

**Final mapping**:

| OSV type | Sentinel type | Notes |
|----------|--------------|-------|
| `FIX` | `patch` | Direct link to fixing commit/PR |
| `INTRODUCED` | NULL | Link to introducing commit. Not a fix artifact — let URL pattern matching handle it (commit URLs → `patch` via heuristic). Introducing commit data is already captured structurally in `CVEAffectedVersion` (`version` + `version_type = "git"`) |
| `REPORT` | `issue` | Bug report link |
| `ADVISORY` | `advisory` | Advisory page |
| `ARTICLE` | `article` | External writeup |
| `PACKAGE` | NULL | Package registry/project page (e.g., pypi.org, npmjs.com). No `package` type exists in Sentinel's `ReferenceType` enum; low operational value for VAs. URL patterns for registries are not in the matching table → stays NULL (uncategorized) |
| `GIT` | NULL | Link to git repository (not a specific commit) |
| `EVIDENCE` | NULL | No direct Sentinel equivalent |
| `WEB` | NULL | Generic web link |
| `DETECTION` | NULL | Detection tooling |
| `DISCUSSION` | NULL | Discussion forum link |

**Decision**: confirmed. Only `FIX`, `REPORT`, `ADVISORY`, and
`ARTICLE` produce explicit type values. All other OSV reference types
fall through to URL pattern matching (which may classify some commit
URLs as `patch`) or remain NULL.

---

## 8. Cross-References

Documents that need to be consulted during spec writing:

- `docs/features/tickets/cve-tracking.md` — host document for the
  fetcher spec (`sync_redhat_cves` at lines 2297-2550 as template)
- `docs/features/tickets/cve-service.md` — `upsert_cve()` contract,
  Phase 2 package resolution sources, callers table
- `docs/features/platform/fetcher-infrastructure.md` — `BaseCVEFetcher`
  class (lines 1176-1369), `fetch_single` contract (lines 277-419),
  `catch_up` contract (lines 542-836), custom settings (lines 922-1174),
  error message sanitization (lines 839-920)
- `docs/data-model.md` — CVEAffectedVersion, CVEExternalIdentifier,
  CVECVSSAssessment schemas
- `docs/data-sources.md` — Fetcher Registry table (line 967), summary
  table (line 35)
- `docs/features/tickets/ticket-references.md` — reference type
  classification, `source_reference_url_pattern` integration
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

### Session 2 (2026-06-19)

- Coherence review of draft against current state of all referenced
  documents
- Confirmed all line references and structural assumptions still valid
- **Resolved OP-OSV-6** (reference type mapping):
  - `PACKAGE` → NULL (type `package` does not exist in Sentinel;
    registry URLs have no URL pattern match → stays uncategorized)
  - `INTRODUCED` → NULL (let URL pattern matching handle commit URLs;
    introducing commit data already captured in `CVEAffectedVersion`)
  - Added `GIT` type to mapping (→ NULL)
- Fixed missing properties in checklist: `cve_source_type`,
  `source_reference_url_pattern`, `fetch_single()`
- Fixed imprecise note about `osv` source prefix (already registered,
  not a placeholder)
- Added Step 4 sub-item: update `resolved_packages` definition in
  `cve-service.md` to align with best-effort usage pattern (drift from
  original "Pre-resolved SUSE package names" comment)
- Remaining open points: OP-OSV-1 through OP-OSV-5

### Session 3 (2026-06-19)

- Analyzed impact of commit 8fea018 (BaseCVEFetcher intermediate class
  introduction) on this draft
- **Added architectural decision 3.7** (inheritance + catch_up):
  - OSV inherits from `BaseCVEFetcher` (structurally required)
  - OSV participates in catch_up (same pattern as Red Hat — scope gap
    exists because `execute()` only covers active tickets)
  - `participates_in_catch_up = True` (default, NOT opt-out)
- **Identified spec incoherence**: `fetcher-infrastructure.md:826`
  erroneously groups OSV with KEV/EPSS in the catch-up opt-out table.
  The rationale ("syncs entire catalog on every run") does not apply to
  OSV's design (per-CVE polling of active tickets only). Added
  correction to Step 2.
- Updated all line number references to post-refactoring state
- Updated section 5 (checklist) to reflect BaseCVEFetcher requirements
- Rewrote section 6 (application plan) with:
  - Correct line numbers for all files
  - New Step 2 actions (remove OSV from opt-out, add to participants)
  - Updated Step 4 (add `record_source_status()` to callers table)
  - New Step 8 (summary table alignment for GHSA, Kernel, OSV)
- Updated section 8 (cross-references) with precise line ranges for
  BaseCVEFetcher, fetch_single, and catch_up sections
- **Resolved all remaining open points (OP-OSV-1 through OP-OSV-5)**:
  - OP-OSV-1: schedule = `0 5 * * *` (daily 05:00 UTC)
  - OP-OSV-2: throttle = 0.2s default, range ge=0.05, le=10.0
  - OP-OSV-3: source_container = `"osv"` (single value, Phase 1+2)
  - OP-OSV-4: **skip CVSS entirely** — no extraction, no provider
    derivation needed (added decision 3.8 with full rationale based
    on live API data verification: OSV `severity` has no attribution
    field, GO records have no severity at all, all useful CVSS data
    already comes from dedicated fetchers with explicit attribution)
  - OP-OSV-5: no limit on aliases/related (fetch all)
- All open points resolved — draft ready for application

### Session 4 (2026-06-19)

- Ran design-reviewer, spec-gap-analyzer, and spec-coherence-reviewer
  on the draft
- 8 findings evaluated; 3 must-fix + 5 should-address identified
- Findings that did not address real problems were discarded (no
  over-documentation)
- **Corrections applied**:
  - Fixed `ON CONFLICT DO NOTHING` → `ON CONFLICT DO UPDATE` for
    `CVEExternalIdentifier` (contradicted `cve-service.md` contract)
  - Added `ecosystem` field to Step 4 (`AffectedVersionEntry` schema
    gap in application plan)
- **New architectural decisions added**:
  - 3.9: Transaction Boundaries and Multi-Phase Atomicity — all HTTP
    requests complete in memory before `upsert_cve()`. Prevents data
    regression from partial writes with delete-and-reinsert. Also
    satisfies I/O-then-Lock convention
  - 3.10: Per-Alias Failure Isolation — individual alias/related HTTP
    errors are skipped (WARNING log), remaining data kept, CVE counts
    as `record_updated`
  - 3.11: Abort Threshold Semantics — only Phase 1 HTTP failure
    increments counter. Threshold = 3. Reset on any Phase 1 success
- **OP-OSV-2 amended**: throttle delay applies between every individual
  HTTP request regardless of phase
- Phase 3 reviewed for value — confirmed: keep as-is, no toggle
- All findings resolved — draft ready for application

### Session 5 (2026-06-19)

- Ran design-reviewer, spec-gap-analyzer, spec-coherence-reviewer on
  the draft (second pass, post-Session 4 amendments)
- 5 findings evaluated; all addressed (0 discarded as
  over-documentation)
- **New architectural decision 3.12**: per-CVE guard on Phase 2/3
  completeness — if alias/related IDs listed but zero fetched
  successfully, skip upsert (preserve previous data, count as
  `record_failed`). Trade-off: remote case of OSV internal
  inconsistency (alias ID listed but record deleted) preserves stale
  data rather than risking loss of valid enrichment
- **Decision 3.9 amended**: added "no extractable data" boundary —
  HTTP 200 with empty record (no affected/references/aliases/related)
  treated as `CVENotInSource`. Consistent with Red Hat fetcher pattern
- **Decision 3.10 amended**: explicit metric strategy —
  `record_updated` on every successful upsert (no change-detection);
  full signaling schema documented. Updated edge case to reference
  guard 3.12
- **Decision 3.11 updated**: abort threshold table row for "all
  aliases fail" updated to reflect guard 3.12 (skip upsert, not
  "CVE is updated")
- **Section 4.2 corrected**: `CVEExternalIdentifierSource` is
  VARCHAR(20) + Python Enum (not PG ENUM). Aligned with `CVESourceType`
  pattern. Application plan Steps 1 and 4 expanded accordingly
- **New Step 7b**: open-point for fetcher metrics granularity
  (`record_updated` semantics, `record_skipped`, `record_missed`) —
  cross-cutting, deferred to future evaluation
- Updated checklist (section 5) with resolved items
- Open points remaining: 0
