# Spec Completion: `sync_kernel_cves` Fetcher

## Purpose

This document tracks the gap analysis and completion plan for the
`sync_kernel_cves` fetcher specification. The current spec (in
`docs/features/tickets/cve-tracking.md`, lines 811-903) is marked as
**Partial** in the Fetcher Registry. It must be expanded to the same
level of detail as `sync_mitre_cves` (~300 lines) before implementation
can begin.

**Goal**: produce an unambiguous, implementation-ready specification that
an engineer can implement without asking clarifying questions.

---

## Current State

| Aspect | Status | Notes |
|--------|--------|-------|
| Properties table | Incomplete | Schedule is TBD in spec (resolved D1: every 3h) |
| Algorithm | Partial | Steps are high-level, missing rejection handling details |
| `.dyad` parsing | **Eliminated** | Redundant with JSON — all data already in `affected[]` (see D10) |
| `.json` field path mapping | **Drafted** | Kernel-specific mapping complete in WI-3 (reflects D11/D12 findings, CVSS 4.0 added) |
| JSON structure vs cvelistV5 | **Documented** | vulns.git JSON differs significantly — see D11 |
| Rejection handling | Ambiguous | Says "from file path" but mechanics not specified |
| Error handling | Delegated | "follows common pattern" — no kernel-specific deviations. Abort threshold note added (WI-6) |
| `fetch_single()` | Partial | Missing path construction details for rejected CVEs |
| Metrics | Complete | Same as MITRE |
| Recovery strategy | Complete | Shared convention in fetcher-infrastructure.md (WI-10 Part B), parametric (2-week window, `cve/` filter) |
| `source_reference_url_pattern` | **Resolved** | Fetcher constructs URL directly (see D4, OP-3 resolved) |
| Clone strategy (no partial clone) | Implied | `fetcher-infrastructure.md` mentions it but kernel spec does not |
| `provider_name` | **Resolved + Verified** | Hardcoded `"Linux"` — verified via CVE Services API (D12) |
| `published_date` | **Resolved** | ABSENT in vulns.git — accept `None` (OP-5) |
| `source_container` collision | **Resolved** | Both fetchers write `"cna"` — safe, data identical (OP-6). Transient regression documented |
| First-run detection | **Resolved** | Cursor-based shared convention (WI-10 Part A), not clone-state-based |
| CVSS 4.0 | **Resolved** | Extract both versions, same as MITRE (WI-3 updated) |
| Delta deduplication | **Resolved** | Same CVE in both dirs → keep rejected/ entry (WI-2 updated) |

---

## Decisions Made

These decisions were made during analysis and are final.

### D1. Schedule: every 3 hours

```
Schedule: Every 3 hours (`0 */3 * * *`)
```

Rationale: the kernel team publishes in batches aligned with stable
releases (60-173 CVEs/day on release days). 3 hours provides
reasonable freshness for batch detection without unnecessary overhead
on quiet days. The repo has no rate limits.

### D2. Files to process: `.json` only

The fetcher reads **only** the `.json` file per CVE:

| File | Purpose | Target table |
|------|---------|--------------|
| `.json` | CVE record (JSON 5.x) — metadata, description, CVSS, affected versions, references | CVE, CVESource, CVECVSSAssessment, CVEAffectedVersion, TicketReference |

The `.dyad` file is **not processed** — see D10 for rationale.

All other file types are **ignored**:

| File | Reason for exclusion |
|------|---------------------|
| `.dyad` | Vulnerable:fixed pairs per stable branch — 100% redundant with JSON `affected[]` (see D10) |
| `.sha1` | Fixing commit SHA — already in JSON `affected[].versions[].lessThan` |
| `.vulnerable` | Introducing commit SHA — already in JSON `affected[].versions[].version` (confirmed via sampling: 28/30 match, 2/30 are alternate introducing commits also present in JSON) |
| `.cvss` | CVSS vector — always identical to JSON `metrics[]` (confirmed: 20/20 samples match, 0 cases where .cvss exists without JSON metrics) |
| `.reference` | Extra URLs — already included in JSON `references[]` (confirmed via sampling) |
| `.message` | Commit message — JSON `descriptions[]` contains the same text with an auto-generated prefix |
| `.mbox` | Email announcement format — not relevant for structured data extraction |
| `.mbox.rejected` | Rejection announcement email — not relevant |
| (no extension) | Empty file or metadata — not relevant (0 bytes in samples) |

### D3. Clone strategy: plain bare clone (no `--filter`)

```
git clone --bare --single-branch https://git.kernel.org/pub/scm/linux/security/vulns.git
```

`git.kernel.org` does **not** advertise the `filter` capability (confirmed
via protocol v2 capability negotiation — server announces only `agent`
and `server-option`). The `--filter=blob:none` flag MUST NOT be used.

Full bare clone size: ~91 MB (all objects local, no on-demand blob
downloads needed for `git show`).

### D4. `source_reference_url` — Constructed by Fetcher

The fetcher constructs the source reference URL directly at processing
time (no `source_reference_url_pattern` class attribute — set to `None`):

```
https://git.kernel.org/pub/scm/linux/security/vulns.git/tree/cve/{state}/{year}/{cve_id}.json
```

Where:
- `{state}` = `published` or `rejected` (from the directory where the
  file was found during delta detection)
- `{year}` = extracted from CVE-ID (`CVE-YYYY-NNNNN` → `YYYY`)

The URL is passed explicitly to `reference_service.upsert_references()`.
This avoids extending the `BaseFetcher` pattern template mechanism for
a single use case. The VA can navigate the URL manually; Anubis
bot-protection is not a concern for human access.

### D5. Rejection detection: directory-based

When the kernel team rejects a CVE, they move files from
`cve/published/` to `cve/rejected/`. In `vulns.git`:

- The JSON file in `cve/rejected/` still contains
  `"state": "PUBLISHED"` (the kernel team does not update the JSON
  content on rejection)
- The authoritative state on CVE Services (MITRE API) is `"REJECTED"`
- Sentinel MUST derive `cve_state` from the **directory path**, not
  from `cveMetadata.state` in the JSON

Delta detection with `--diff-filter=AMCR`:

- New files in `cve/rejected/` appear as **Added** → detected
- Deleted files in `cve/published/` are excluded by the filter → not
  detected (but this is fine — the new file in `rejected/` is the signal)

### D6. `.message` files: skip

The JSON `descriptions[]` field contains the same commit message text
with an auto-generated prefix ("In the Linux kernel, the following
vulnerability has been resolved: "). The `.message` file is the raw
commit message without the prefix. Only 7 such files exist in the
entire repo. Sentinel uses the JSON description, which is a superset.

### D7. `.vulnerable` files: skip

The introducing commit SHA in the `.vulnerable` file is already present
in the JSON `affected[].versions[].version` field. In 2/30 sampled
cases with mismatches, the `.vulnerable` pointed to an alternate
introducing commit that was also present elsewhere in the JSON
`affected[]` array (just not as the first entry). The data is fully
covered by the JSON.

### D8. `.cvss` files: skip (read CVSS from JSON `metrics[]`)

On all 20 sampled CVEs with a `.cvss` file, the CVSS vector string was
identical to the one in the JSON `metrics[].cvssV3_1.vectorString`. On
all 10 sampled CVEs without a `.cvss` file, the JSON `metrics[]` array
was empty. The two are always synchronized. Only ~5% of kernel CVEs
have CVSS scores at all (614 out of 12,118).

### D9. `.reference` files: skip (read references from JSON)

On all sampled cases, the URLs in `.reference` files were already
present in the JSON `references[]` array. The `.reference` file is a
legacy input format used by the kernel team's publishing tools — the
generated JSON includes all reference URLs.

### D10. `.dyad` files: skip (100% redundant with JSON `affected[]`)

**Verified 2026-06-13** by cloning `vulns.git` and comparing `.dyad`
content against the corresponding `.json` `affected[]` array on multiple
CVEs (CVE-2024-50055, CVE-2023-52752, CVE-2024-26687).

The kernel CNA JSON contains **two representations** in the `affected[]`
array:

1. **Git block** (`versionType = "git"`): introducing SHA → fixing SHA,
   one entry per stable branch where the fix was applied. Entries
   without `lessThan` represent unfixed branches.
2. **Semver block** (`versionType = "semver"`): human-readable kernel
   version ranges (e.g., `5.10.237`, `6.6.57`).

The `.dyad` file format (`introduced_version:introducing_sha:fixed_version:fixing_sha`)
is a compact representation of **exactly the same data**:

| `.dyad` field | JSON equivalent |
|---------------|-----------------|
| `introducing_sha` | Git block: `versions[].version` |
| `fixing_sha` | Git block: `versions[].lessThan` |
| `introduced_version` | Semver block: `version` with `status: "affected"` |
| `fixed_version` | Semver block: `version` with `status: "unaffected"` |
| `0:0` (unfixed) | Git block: entry with `status: "affected"` and no `lessThan` |

**Consequence**: no `source_container = "vulns.git"` is needed. All
affected version data is stored under `source_container = "cna"` from
the JSON parsing, same as `sync_mitre_cves`.

**Impact on justification**: the "Extra data" argument (#3) in the "Why
a Dedicated Fetcher" section is no longer valid. The remaining
justifications (timing, batch handling, reliability) still hold.

### D11. `vulns.git` JSON differs significantly from `cvelistV5`

**Verified 2026-06-13** by cloning `vulns.git` to `/tmp/vulns.git` and
comparing JSON structure against the same CVEs fetched from
`cvelistV5` on GitHub.

The kernel team's `vulns.git` repository contains the **raw CNA
submission data** — NOT the enriched records that CVE Services
(MITRE) produces in `cvelistV5`. Key structural differences:

| Field | `vulns.git` | `cvelistV5` (MITRE) |
|-------|-------------|---------------------|
| `dataVersion` | `5.1.1` (published) / `5.0` (rejected) | `5.2` |
| `providerMetadata.shortName` | **ABSENT** | `"Linux"` |
| `cveMetadata.assignerShortName` | **ABSENT** | `"Linux"` |
| `cveMetadata.datePublished` | **ABSENT** (0/30 samples) | Present |
| `cveMetadata.dateReserved` | **ABSENT** | Present |
| `cveMetadata.dateUpdated` | **ABSENT** | Present |
| `assignerOrgId` | `f4215fc3-5b6b-47ff-a258-f7189bd81038` | `416baaa9-dc9f-4396-8d5f-8c081fb06d67` |
| `containers.adp[]` | **ABSENT** | Present (CISA-ADP, CVE Program) |

**Consequences for the fetcher:**

1. **`provider_name` must be hardcoded** to `"Linux"` — cannot be
   extracted from `providerMetadata.shortName` (field does not exist)
2. **`published_date` is unavailable** from the JSON. The field will
   be `None` until another fetcher (MITRE or NVD) populates it
3. **The kernel fetcher CANNOT reuse the generic MITRE CNA parser
   without adaptation** — missing fields require kernel-specific
   handling (see updated WI-3)
4. **The `affected[]` content IS structurally identical** between both
   repos (same git/semver blocks, same data) — only metadata differs.
   This confirms D10's `source_container = "cna"` decision is safe
   for the `CVEAffectedVersion` data

**Operational note — Anubis bot protection:**

The `git.kernel.org` web interface is protected by
[Anubis](https://anubis.techaro.lol/) (proof-of-work anti-bot system).
Raw file access via HTTP (`/plain/` URLs, cgit interface) is blocked
for automated tools. WebFetch and similar HTTP clients cannot retrieve
individual files from the repository.

**CRITICAL guidance for future analysis:**

- **NEVER** use `cvelistV5` (MITRE GitHub mirror) as a proxy or
  fallback to inspect `vulns.git` data. The assumption that the two
  repositories contain identical content is **FALSE** — they differ in
  schema version, metadata fields, orgId, and available containers
- When investigation of `vulns.git` content is needed, **ask the
  operator to provide a local clone** (bare clone, ~91 MB, no auth
  required: `git clone --bare --single-branch https://git.kernel.org/pub/scm/linux/security/vulns.git`)
- The clone command succeeds despite Anubis — Git protocol access is
  unaffected; only the HTTP/cgit web interface is protected

### D12. `provider_name = "Linux"` — Verified via CVE Services API

**Verified 2026-06-13** by fetching kernel CVE records from `cvelistV5`
(GitHub raw content, which is what `sync_mitre_cves` processes):

| CVE | `cveMetadata.assignerShortName` | `containers.cna.providerMetadata.shortName` |
|-----|------|------|
| CVE-2024-50055 | `"Linux"` | `"Linux"` |
| CVE-2023-52752 | `"Linux"` | `"Linux"` |
| CVE-2024-26687 | `"Linux"` | `"Linux"` |

The MITRE fetcher extracts `providerMetadata.shortName` from the CNA
container and uses it as `provider_name` for CVSS assessments →
produces `"Linux"`. The kernel fetcher hardcodes the same value to
ensure CVSS deduplication on `(cve_id, provider_name, cvss_version)`
works correctly between both fetchers.

The value `"Linux Kernel CNA"` previously documented in
`data-sources.md` is incorrect — it does not appear in any actual CVE
Services data. It must be corrected to `"Linux"` when applying the
spec updates.

---

## Work Items

Each work item corresponds to a section that must be written or
rewritten in the kernel fetcher spec. Items are ordered for
incremental completion across multiple sessions.

### WI-1: Update Properties Table

**Status**: TODO

Replace the current properties table with:

```markdown
| Property | Value |
|----------|-------|
| Fetcher name | `sync_kernel_cves` |
| Class name | `SyncKernelCves` |
| Description | `"Sync CVE data from the Linux Kernel CNA vulnerability repository"` |
| `cve_source_type` | `"kernel"` |
| Schedule | Every 3 hours (`0 */3 * * *`) |
| Source | `vulns.git` bare clone (`git.kernel.org/pub/scm/linux/security/vulns.git/`) |
| Scope | All `.json` files changed since last processed commit (in `cve/published/` and `cve/rejected/`) |
| Auth | None (public Git repository) |
| Custom settings | No |
| `fetch_single()` | Yes — local Git object store lookup (`.json`) |
| `source_reference_url_pattern` | `None` — URL constructed directly by fetcher (see D4) |
| Queue | `"git"` |
| Recovery window | 2 weeks |
| Recovery file filter | `cve/` |
```

---

### WI-2: Rewrite Algorithm Section

**Status**: TODO

The algorithm must be expanded with the same granularity as MITRE
(steps 1-7 in `cve-tracking.md:527-612`). Key differences from MITRE:

1. **No `--filter=blob:none`** in clone command (plain bare clone)
2. **File filtering** must include BOTH directories:
   - `cve/published/YEAR/CVE-*.json` — published CVEs (normal processing)
   - `cve/rejected/YEAR/CVE-*.json` — rejected CVEs (state change signal)
3. **`cve_state` derivation** from directory path, not from JSON field
4. **`resolved_packages = ["kernel-source"]`** always set (bypasses
   CPE/vendor:product resolution)

Proposed algorithm structure:

```
1. First run (no FetcherRun record with cursor exists for this fetcher):
   - Follows the shared "First-Run Detection" convention in
     fetcher-infrastructure.md (see WI-10 Part A):
     - If clone does not exist or is invalid: delete invalid dir if
       present, then git clone --bare --single-branch (NO --filter)
     - If clone already exists and is valid (previous attempt cloned
       but failed before persisting cursor): skip clone
   - Record HEAD in cursor without processing
   
2. Subsequent runs (cursor exists):
   - git fetch origin
   - If cursor SHA is unreachable (git cat-file -t fails): apply
     shared Recovery Strategy (WI-10 Part B) with recovery_window =
     2 weeks and recovery_file_filter = 'cve/'
   - Otherwise: normal delta detection from cursor
   
3. Delta detection:
   - git diff --name-only --diff-filter=AMCR <stored_sha>..HEAD
   
4. File filtering:
   - Select files matching: cve/{published,rejected}/YEAR/CVE-YEAR-ID.json
   - Ignore all other file types (.dyad, .sha1, .mbox, etc.)
   
5. Processing per CVE:
   a. Deduplicate: if the same CVE-ID appears in both published/ and
      rejected/ paths within the same delta, keep only the rejected/
      entry (it represents the most recent state transition)
   b. Derive cve_state from path:
      - Path starts with cve/published/ → use cveMetadata.state from
        JSON. If the value is not a recognized CVEState enum member,
        the CVE is treated as a per-item failure (logged via
        record_failed(), processing continues with next CVE)
      - Path starts with cve/rejected/ → force REJECTED (ignore JSON state)
   c. Parse .json into CVEIngestPayload (see field mapping table)
   d. Set resolved_packages = ["kernel-source"]
   e. Call cve_service.upsert_cve()
   f. Construct source_reference_url from state/year/cve_id
   g. Call reference_service.upsert_references() passing the
      source_reference_url together with the CVE's own references[].
      All URLs (including source_reference_url) are classified by
      the standard auto-classification logic in reference_service —
      no hardcoded type override
   
6. Batch error handling: common CVE fetcher pattern
   
7. Store new HEAD as cursor
```

---

### WI-3: CVE JSON Field Path Mapping (Kernel-Specific)

**Status**: TODO

The kernel CNA publishes CVE records in `vulns.git` using CVE JSON
5.1.1 format (published) and 5.0 format (rejected). These are the
**raw CNA submission files** — NOT the enriched records found in
MITRE's `cvelistV5` (see D11 for the full comparison).

**Key differences from `cvelistV5` / generic MITRE parsing**:

| Aspect | `cvelistV5` (MITRE) | `vulns.git` (kernel) |
|--------|---------------------|----------------------|
| `dataVersion` | `5.2` | `5.1.1` (published) / `5.0` (rejected) |
| `cveMetadata.state` | Authoritative | NOT authoritative — use directory path |
| `cveMetadata.cveId` field name | Always `cveId` | `cveId` (published, 100%), `cveID` (capital D, rejected, 100%) |
| `cveMetadata.datePublished` | Present | **ABSENT** (never present) |
| `cveMetadata.dateReserved` | Present | **ABSENT** |
| `cveMetadata.dateUpdated` | Present | **ABSENT** |
| `cveMetadata.assignerShortName` | `"Linux"` | **ABSENT** |
| `providerMetadata.shortName` | `"Linux"` | **ABSENT** (only `orgId`) |
| `providerMetadata.orgId` | `416baaa9-dc9f-4396-8d5f-8c081fb06d67` | `f4215fc3-5b6b-47ff-a258-f7189bd81038` |
| `containers.adp[]` | Present (CISA-ADP, CVE Program) | **ABSENT** (CNA-only) |
| `metrics[]` | Common (enriched by ADP) | Rare (~5%, 614/12,118 CVEs). Only CVSS 3.1 observed today; 4.0 not yet used |
| `affected[].versions[].versionType` | Various | Two blocks: `"git"` (SHAs) + `"semver"` / `"original_commit_for_fix"` |
| `affected[].repo` | Optional | Always present: `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` |
| `affected[].programFiles` | Rare | Common (affected source files) |
| `affected[].product` / `.vendor` | Varies | Always `"Linux"` / `"Linux"` |
| `cpeApplicability` | Present (from NVD/ADP) | Present (generated by kernel team tool `bippy`) |

**Schema structural compatibility assertion**: for all fields that
Sentinel extracts (descriptions, affected, metrics, references, title),
schemas 5.0 (rejected CVEs) and 5.1.1 (published CVEs) are
structurally identical. The only parsing difference is the
`cveId`/`cveID` field name in `cveMetadata` (see OP-4). This was
verified by sampling rejected CVEs and comparing their JSON structure
against published CVEs for all extraction paths.

**The kernel fetcher CANNOT reuse the generic MITRE CNA parser
without adaptation.** Missing metadata fields (`shortName`,
`datePublished`, `dateUpdated`) and a different `orgId` require
kernel-specific handling. The `affected[]` parsing logic (git/semver
blocks) IS reusable. ADP container parsing is not applicable.

**Fields to extract from kernel JSON**:

| CVEIngestPayload field | JSON path | Kernel-specific notes |
|---|---|---|
| `cve_state` | Directory path (NOT `cveMetadata.state`) | `published/` → use JSON state; `rejected/` → force `REJECTED` |
| `cve_id` | `cveMetadata.cveId` or `cveMetadata.cveID` | Handle both field names (see OP-4). If both absent, use filename-derived CVE-ID as authoritative (no WARNING logged) |
| `title` | `containers.cna.title` | Usually present (short commit message subject line) |
| `description` | `containers.cna.descriptions[0].value` | All kernel descriptions are English-only (`bippy` tool does not produce multilingual entries). Take `[0].value` without language filtering |
| `published_date` | N/A | **ABSENT in vulns.git** — set to `None`. Populated later by MITRE/NVD fetchers (see OP-5) |
| `modified_date` | N/A | **ABSENT in vulns.git** — set to `None` |
| `cvss_assessments` | `containers.cna.metrics[].cvssV3_1.vectorString` AND `containers.cna.metrics[].cvssV4_0.vectorString` | Extract both CVSS versions (same logic as MITRE). Currently ~5% have 3.1 (614 CVEs); 0% have 4.0 today. `provider_name` = `"Linux"` (hardcoded — no `shortName` in JSON). See D12 for verification |
| `affected_versions` | `containers.cna.affected[].versions[]` | Two blocks: git (SHAs, per-branch) and semver (kernel versions). `source_container = "cna"` |
| `references` | `containers.cna.references[].url` | Kernel patch URLs (auto-classified by reference_service) |
| `program_files` | `containers.cna.affected[].programFiles` | Stored in `CVEAffectedVersion.program_files` JSONB |

**Explicitly ignored fields** (present but not extracted):

| JSON path | Reason for exclusion |
|-----------|---------------------|
| `containers.cna.cpeApplicability` | Sentinel uses `resolved_packages = ["kernel-source"]`, bypassing CPE resolution entirely |
| `containers.cna.x_generator` | Tool metadata (`bippy` engine version), no operational use |
| `cveMetadata.assignerOrgId` | Kernel uses internal orgId (`f4215fc3...`) different from CVE Services registration; not stored |

**CVE-ID field name handling** (OP-4 resolved): published CVEs use
`cveMetadata.cveId` (100%); rejected CVEs use `cveMetadata.cveID`
(100%). Parser rule: try `cveMetadata.cveId` first, fall back to
`cveMetadata.cveID`. No warning for the uppercase form — it is the
standard format for rejected CVEs, not an anomaly.

**CVE-ID cross-validation**: same rule as MITRE — extract CVE-ID from
filename path, compare with `cveMetadata.cveId`/`cveMetadata.cveID`.
On mismatch, log WARNING and use filename-derived ID as authoritative.

**CVSS deduplication**: same rule as MITRE applies — if multiple
entries of the same CVSS version exist in `metrics[]`, use the last
one. In practice, kernel CVEs with metrics have exactly one entry.

**Sentinel value normalization**: vendor/product are always `"Linux"` /
`"Linux"` (not sentinel values), so the skip-if-both-sentinel rule from
MITRE does not trigger. No special handling needed.

**`provider_name` derivation**: since `providerMetadata.shortName` is
absent in `vulns.git`, the fetcher MUST hardcode `provider_name =
"Linux"` for CVSS assessments. This matches the value that CVE
Services assigns (`assignerShortName` in `cvelistV5`) and ensures
consistency when `sync_mitre_cves` later processes the same CVE.
Verified via API on 3 CVEs — see D12.

---

### WI-4: `.dyad` File Format and Parsing

**Status**: ELIMINATED (decision D10)

The `.dyad` file is 100% redundant with the JSON `affected[]` array.
All per-branch version data (introducing/fixing SHAs, human-readable
versions, unfixed branches) is already present in the JSON in two
blocks: `versionType = "git"` and `versionType = "semver"`. No
separate `source_container = "vulns.git"` scope is needed.

See D10 for the full analysis and verification evidence.

---

### WI-5: Rejection Handling Details

**Status**: TODO

The current spec says: "cve_state from file path (published/ vs
rejected/)". This must be expanded into a precise algorithm:

1. **File filter**: the delta detection processes files from BOTH
   `cve/published/` AND `cve/rejected/` directories
2. **State derivation rule**:
   - Path starts with `cve/published/` → set `cve_state` from
     `cveMetadata.state` field in JSON (usually `"PUBLISHED"`)
   - Path starts with `cve/rejected/` → force `cve_state = "REJECTED"`
     regardless of what the JSON contains
3. **Processing**: rejected CVEs are processed through the same
   `upsert_cve()` path. The `cve_state = "REJECTED"` triggers the
   standard rejection handling rules defined in `cve-tracking.md`
   (Rejection Handling section)
4. **Recovery for file filter**: update recovery strategy to use
   `'cve/'` as the filter (not just `'cve/published/'`) to catch
   rejections that occurred during the recovery window

**Rejection revert**: if the kernel team un-rejects a CVE (moves from
`rejected/` back to `published/`), this appears in the delta as:
- An Added file in `cve/published/` → detected, processed with
  `cve_state` from JSON (now `"PUBLISHED"` again)
- A Deleted file in `cve/rejected/` → not detected (excluded by
  `--diff-filter=AMCR`)

This naturally triggers the "Rejection revert handling" rules in
`cve-tracking.md`. No special handling needed.

---

### WI-6: Error Handling Section

**Status**: TODO

The kernel fetcher follows the **shared git-based fetcher error
classification** defined in `fetcher-infrastructure.md` (Error
Classification section). No kernel-specific exceptions are needed
because:

1. **No partial clone** → `git show` failures are always corruption
   (blob is local, never on-demand downloaded). The `GitFileError`
   classification still applies correctly — but the failure is less
   likely than with blobless clones
2. **JSON-only processing** → no additional file format parsers that
   could introduce unique failure modes

Proposed error handling section for the spec:

```markdown
#### Error Handling

- **Git operation failure** (network, corrupt pack): follows the shared
  git-based fetcher error classification in
  `docs/features/platform/fetcher-infrastructure.md` (Error
  Classification)
- **Individual CVE JSON parse/upsert failure**: follows the common CVE
  fetcher error handling pattern (see "Common CVE Fetcher Error
  Handling" above)
- **Abort threshold**: no kernel-specific abort threshold is defined.
  The shared `BaseFetcher` Celery task timeout limits maximum run
  duration. The fetcher dashboard surfaces partial-status runs with
  high `items_failed` counts for operator attention. Failed CVEs are
  individually logged via `record_failed()` and can be reprocessed
  via `fetch_single()`
```

No kernel-specific error handling deviations from the shared pattern.

---

### WI-7: `fetch_single()` Implementation Details

**Status**: TODO

The current spec says it searches for the CVE JSON file but does not
specify the path construction details, especially for rejected CVEs.

Proposed `fetch_single()` algorithm:

1. Construct candidate paths:
   - `cve/published/{year}/{cve_id}.json`
   - `cve/rejected/{year}/{cve_id}.json`
   (where `{year}` is extracted from CVE-ID: `CVE-YYYY-NNNNN` → `YYYY`)
2. Try `git show HEAD:<published_path>` first
3. If not found, try `git show HEAD:<rejected_path>`
4. If found in either location:
   - Parse JSON into `CVEIngestPayload`
   - Derive `cve_state` from which path succeeded
   - Construct `source_reference_url` from state/year/cve_id
   - Call `upsert_cve()`
   - Return normally → `status = success`
5. If not found in either location: raise `CVENotInSource`
   → `status = missing`

**Directories intentionally NOT searched**: `cve/reserved/` and
`cve/returned/` contain no `.json` files (verified: 0 JSON files in
either directory). Reserved CVE-IDs have no data to ingest. Even if
a CVE-ID exists in `reserved/`, it has no content — the correct
response is `CVENotInSource` (other fetchers like NVD may succeed
independently).

**Concurrency**: follows shared rules — MUST NOT run `git fetch`.
Read-only `git show` operations on a plain bare clone are always safe
(all blobs are local, no network I/O).

---

### WI-8: Disk Space Estimate

**Status**: TODO

Add to the "Storage and Recovery" section:

```markdown
**Disk space estimate**: ~91 MB (`vulns.git` full bare clone — all
blobs local, no partial clone filtering). Combined with `cvelistV5`
(~300 MB blobless), total git volume usage is ~400 MB. Provision per
the 1 GB minimum specified in `fetcher-infrastructure.md` (Volume
Requirements).

**Expected processing time**: a typical batch of 173 CVEs (largest
observed on kernel stable release days) requires ~30-60 seconds to
process (local `git show` + JSON parse + DB upsert per CVE). Celery
task timeout should be configured above this threshold to avoid
mid-batch kills. A conservative timeout of 300 seconds (5 minutes)
accommodates batches up to ~500 CVEs under normal database load.
```

---

### WI-9: Recovery Strategy (Update File Filter)

**Status**: TODO

The current recovery strategy (shared with MITRE, documented at
`cve-tracking.md:780-795`) uses `cve/published/` as the file filter
for the kernel fetcher. This must be updated to `cve/` to include
rejected CVEs in the recovery window:

```
git diff --name-only --diff-filter=AMCR <boundary_sha>..HEAD -- 'cve/'
```

Then apply the same file filtering logic (only `.json` files matching
`cve/{published,rejected}/YEAR/CVE-YEAR-ID.json`).

---

### WI-10: Shared Git-Based Fetcher Conventions in `fetcher-infrastructure.md`

**Status**: TODO

The "clone exists but no cursor exists" intermediate state and the
recovery strategy for unreachable cursors are not currently documented
as shared conventions. Both MITRE and kernel fetcher specs implement
the same logic independently. This work item adds two subsections to
`fetcher-infrastructure.md`, section "Git-Based Fetchers".

#### Part A: First-Run Detection

The first-run determination MUST be cursor-based (not clone-state-based).
Both signals can disagree if the first run clones successfully but
fails to persist the cursor.

**Proposed addition** to `fetcher-infrastructure.md`, section
"Git-Based Fetchers", new subsection "First-Run Detection":

```markdown
#### First-Run Detection

A git-based fetcher determines "first run" by the absence of a
`FetcherRun` record with a cursor — NOT by the presence or absence of
the clone directory. The clone directory state is a sub-condition of
the first-run logic:

| Cursor exists? | Clone valid? | Action |
|---|---|---|
| No | No (absent or invalid) | If directory exists but is invalid (fails `git rev-parse --git-dir`): delete entirely. Clone repository. Record HEAD without processing |
| No | Yes | Skip clone (previous attempt succeeded but cursor was not persisted). Record HEAD without processing |
| Yes | Yes | Subsequent run: fetch + delta detection from cursor |
| Yes | No (absent or invalid) | Delete invalid directory if present. Re-clone. Then apply cursor reachability check (see Recovery Strategy below) |

"Invalid" means: the directory exists but `git rev-parse --git-dir`
fails (corrupted pack files, incomplete clone from interrupted
previous attempt, filesystem corruption, etc.).
```

#### Part B: Recovery Strategy (Cursor SHA Unreachable)

The recovery logic for unreachable cursor SHAs is currently documented
only in `cve-tracking.md` (lines 780-795) as a shared section between
MITRE and kernel. It should be elevated to a shared convention since
it applies to any git-based fetcher.

**Proposed addition** to `fetcher-infrastructure.md`, section
"Git-Based Fetchers", new subsection "Recovery Strategy":

```markdown
#### Recovery Strategy (Cursor SHA Unreachable)

When a git-based fetcher's stored cursor SHA is not reachable in the
local clone (detected via `git cat-file -t <sha>` returning non-zero),
it applies a time-bounded recovery reprocessing window. This situation
occurs when:

- The clone was rebuilt (row 4 of First-Run Detection table)
- The upstream repository was force-pushed or rebased (rare for
  published CVE/advisory repos)
- Git garbage collection pruned unreachable objects (should not happen
  for commits reachable from HEAD, but possible with corrupted state)

**Algorithm**:

1. Determine boundary SHA:
   `git rev-list -1 --before="<recovery_window> ago" HEAD`
2. Compute delta:
   `git diff --name-only --diff-filter=AMCR <boundary_sha>..HEAD -- '<file_filter>'`
3. Apply the fetcher's normal file filtering and per-item processing
   logic (MUST be idempotent — previously ingested items produce no
   observable side effects on re-processing)
4. Write HEAD as new cursor on completion

Each git-based fetcher declares these parameters in its properties
table:

| Parameter | Description | Example values |
|---|---|---|
| `recovery_window` | Maximum look-back period for reprocessing | `2 weeks` (CVE fetchers) |
| `recovery_file_filter` | Path filter for the recovery delta command | `cves/` (MITRE), `cve/` (kernel) |

**Window exceeded**: if the actual gap exceeds `recovery_window`
(boundary SHA is HEAD itself because no commits exist before the
window), the run completes with `status = partial`, logs a WARNING
indicating operator intervention is required (manual `fetch_single()`
for specific items or full re-seed via operational tooling), and
records HEAD as cursor to prevent infinite retries.

**Normal case after re-clone**: when a clone is rebuilt from the same
remote (row 4 of First-Run Detection), the cursor SHA is almost always
reachable because git history is preserved. In this case, normal delta
detection proceeds — no recovery window is needed. The recovery
strategy is a fallback for the rare case where the SHA truly does not
exist in the fresh clone.
```

#### Part C: Implementation Note (Git Operations Utility Module)

**Proposed addition** to `fetcher-infrastructure.md`, section
"Git-Based Fetchers", new subsection "Implementation Guidance":

```markdown
#### Implementation Guidance

Git operations (clone, fetch, delta detection, file retrieval, SHA
reachability checks) SHOULD be encapsulated in a shared utility module
(e.g., `backend/app/services/git_operations.py`) rather than
reimplemented per-fetcher. This module:

- Contains stateless utility functions (no database interaction, no
  business logic)
- Centralizes subprocess error handling and maps git failures to the
  typed exception hierarchy (`GitNetworkError`, `GitFileError`, etc.)
- Is independent of `BaseFetcher` lifecycle — fetchers compose these
  utilities within their `execute()` method
- Provides a clean mocking boundary for unit tests (mock one function
  instead of `subprocess.run`)

The module is NOT a "service" in the Sentinel service-layer sense — it
has no database access and no domain logic. Each fetcher retains full
control over its execution flow, using the utility functions as
building blocks.
```

---

### WI-11: Update MITRE Spec First-Run and Recovery References

**Status**: TODO

The MITRE fetcher's first-run logic (`cve-tracking.md`, lines 529-541)
currently uses clone directory state as the primary detection signal:

> **First run** (clone directory does not exist OR is not a valid bare
> git repository — detected via `git rev-parse --git-dir`)

And the recovery strategy (`cve-tracking.md`, lines 780-795) is
documented as a shared section within `cve-tracking.md`. Both must be
updated to reference the shared conventions in
`fetcher-infrastructure.md` (WI-10).

**Changes to MITRE first-run section (lines 529-541)**:

```markdown
1. **First run** (no `FetcherRun` record with cursor exists for this
   fetcher — see "First-Run Detection" in
   `docs/features/platform/fetcher-infrastructure.md`):
   - If clone does not exist or is invalid: `git clone --bare
     --filter=blob:none --single-branch` of
     `https://github.com/CVEProject/cvelistV5.git` into
     `$GIT_CLONE_BASE_DIR/cvelistV5/`
   - If clone already exists and is valid: skip clone (previous
     attempt succeeded but cursor was not persisted)
   - Record HEAD commit SHA in `FetcherRun.cursor` without processing
     any files
```

**Changes to recovery strategy section (lines 780-795)**:

Replace the current shared recovery section with a reference to the
shared convention plus fetcher-specific parameters:

```markdown
**Recovery strategy**: both `sync_mitre_cves` and `sync_kernel_cves`
follow the shared "Recovery Strategy (Cursor SHA Unreachable)"
convention in `docs/features/platform/fetcher-infrastructure.md`
(Git-Based Fetchers section).

Fetcher-specific parameters:

| Fetcher | `recovery_window` | `recovery_file_filter` |
|---|---|---|
| `sync_mitre_cves` | 2 weeks | `cves/` |
| `sync_kernel_cves` | 2 weeks | `cve/` |
```

**Changes to MITRE properties table**: add recovery parameters:

```markdown
| Recovery window | 2 weeks |
| Recovery file filter | `cves/` |
```

**Changes to kernel properties table (WI-1)**: add recovery parameters:

```markdown
| Recovery window | 2 weeks |
| Recovery file filter | `cve/` |
```

---

## Open Points — All Resolved

All open points have been resolved. Decisions documented here for
traceability.

### OP-1: Store Human-Readable Kernel Version from `.dyad`? — RESOLVED

**Decision**: Not applicable. The `.dyad` file is not processed (D10).
The human-readable kernel versions are already present in the JSON
`affected[]` semver block (`versionType = "semver"`) and are stored as
standard `CVEAffectedVersion` records by the CNA container parser —
no additional columns or model changes needed.

---

### OP-2: `.dyad` Parse Failure — Failure or Warning? — RESOLVED

**Decision**: Not applicable. The `.dyad` file is not processed (D10).

---

### OP-3: `source_reference_url_pattern` — Dynamic Path Construction — RESOLVED

**Decision**: (a) Fetcher constructs URL directly. The
`source_reference_url_pattern` class attribute is set to `None`. The
fetcher builds the full URL at processing time using the directory
state (`published`/`rejected`) and year (from CVE-ID), then passes it
explicitly to `reference_service.upsert_references()`. No framework
extension needed.

Rationale: the fetcher already knows the state and year at processing
time. A static pattern would produce broken URLs for rejected CVEs
(~2.4% of total) with no mechanism for correction by other fetchers.

---

### OP-4: `cveMetadata` Field Name Inconsistency — RESOLVED

**Decision**: Try `cveMetadata.cveId` first, fall back to
`cveMetadata.cveID`. No warning for the uppercase form — verified that
published CVEs use `cveId` (100%) and rejected CVEs use `cveID`
(100%). The uppercase form is the standard format for rejected files,
not an anomaly. Only log WARNING on cross-validation mismatch
(JSON field value ≠ filename-derived CVE-ID).

---

### OP-5: `published_date` Fallback Strategy — RESOLVED

**Context**: `vulns.git` JSON contains NO `datePublished` field (see
D11). When `sync_kernel_cves` creates a new CVE record, `published_date`
will be `None` until another fetcher (MITRE or NVD) populates it.

**Decision**: (a) Accept `None`. The publication date is a factual datum
from CVE Services; fabricating it introduces inaccuracy. The MITRE
fetcher (6h cycle) or NVD fetcher will populate it eventually — the
delay is at most ~6 hours for new CVEs.

---

### OP-6: `source_container = "cna"` Scope Collision with MITRE — RESOLVED

**Context**: decision D10 establishes that `source_container = "cna"` is
used by both `sync_kernel_cves` and `sync_mitre_cves`. Per
`cve-service.md`, `upsert_cve()` performs delete-and-reinsert per
`(cve_id, source_container)` — whichever fetcher runs last overwrites
the other's `CVEAffectedVersion` records for that scope.

**Decision**: (a) Accept `"cna"` and document the invariant. The
`affected[]` content is identical by construction — the same CNA
(Linux kernel security team) publishes to both `vulns.git` and CVE
Services (which populates `cvelistV5`). The last-writer-wins behavior
produces no information loss. The overwrite is unnecessary churn
(deleting and recreating identical rows) but is functionally harmless
and occurs at most once per CVE per 6-hour MITRE cycle.

**Known trade-off — transient data regression**: because both fetchers
write `source_container = "cna"` (delete-and-reinsert per
`cve-service.md`), `sync_mitre_cves` may temporarily overwrite fresher
kernel data if it processes a CVE before CVE Services receives the
kernel team's latest update (e.g., a newly-fixed stable branch added
to `affected[]` in `vulns.git` but not yet propagated to `cvelistV5`).
This self-heals within one kernel sync cycle (at most 3 hours). The
alternative (separate `source_container` values) was rejected because
it doubles storage for identical data without improving correctness —
the data is structurally identical by construction, only the
propagation timing differs.

When applying the spec, update `data-model.md` and `cve-service.md`
to remove the `"vulns.git"` source_container reference.

---

### OP-7: `source_reference_url` Staleness on Rejection Revert — RESOLVED

**Context**: when `fetch_single()` finds a CVE in `cve/rejected/`, it
creates a `source_reference_url` pointing to the `rejected/` path. If
the kernel team later un-rejects the CVE (moves it back to
`published/`), the delta processing correctly updates `cve_state` to
`PUBLISHED`. However, the `TicketReference` with the old `rejected/`
URL persists — `upsert_references()` uses URL as dedup key, so the
new `published/` URL creates a second reference.

**Decision**: (a) Accept stale references. Rejection reverts are
extremely rare (< 1% of 292 rejected CVEs). The VA can manually remove
the dead link if encountered. The fetcher should not add complexity
(coupling with reference lifecycle, state-change detection) for an
edge case this rare.

---

### OP-8: Cursor Advancement on Partial Failure — RESOLVED

**Context**: the current spec says "Store new HEAD after successful
processing" (line 876). However, `fetcher-infrastructure.md` specifies
that the cursor advances on both `success` and `partial` status (when
`execute()` returns normally). On partial, some CVEs have failed.

**Decision**: (a) Advance cursor on partial — consistent with MITRE
fetcher behavior and avoids infinite reprocessing loops for
persistently broken JSON files. Each failed CVE is logged individually:

```python
logger.warning("Failed to process %s: %s", cve_id, str(error))
self.record_failed()
```

Failed CVEs are NOT automatically retried on the next scheduled run
(the cursor has moved past them). They can be reprocessed via:
- `fetch_single(cve_id)` — manual on-demand trigger
- Upstream file modification — the CVE reappears in a future delta

---

### OP-9: Year Directory vs CVE-ID Year Mismatch in `fetch_single()` — RESOLVED

**Verified 2026-06-13**: directory year ALWAYS matches CVE-ID year.
Checked all 12,118 published + 292 rejected `.json` files — **zero
mismatches**.

**Decision**: assume year always matches. `fetch_single()` constructs
the path using the year from the CVE-ID (`CVE-YYYY-NNNNN` → `YYYY`).
No fallback search needed. If a future mismatch is ever introduced by
the kernel team, it will be caught by delta processing (which uses the
actual path) and the CVE will be ingested normally on the next
scheduled run. `fetch_single()` missing it is an acceptable degradation
for an event that has never occurred in the repository's history.

---

## Completion Checklist

Track progress across sessions. Mark items as they are completed
(written into the actual spec in `cve-tracking.md`).

### Kernel fetcher spec (`cve-tracking.md`)

- [ ] WI-1: Update properties table (schedule, queue, scope, description,
      source_reference_url_pattern = None)
- [ ] WI-2: Rewrite algorithm section (expanded, 7 steps, cursor-based
      first-run, dedup logic, auto-classification, unrecognized state
      handling)
- [ ] WI-3: CVE JSON field path mapping (kernel-specific table,
      updated for D11/D12 findings — no shortName, no dates, hardcoded
      provider_name = "Linux", CVSS 4.0 extraction, schema 5.0/5.1.1
      structural assertion, language note, both-fields-absent fallback)
- [x] WI-4: ~~`.dyad` file format and parsing~~ — ELIMINATED (D10)
- [ ] WI-5: Rejection handling details (expanded algorithm)
- [ ] WI-6: Error handling section (simplified, JSON-only, abort
      threshold note)
- [ ] WI-7: `fetch_single()` implementation details (path construction,
      reserved/returned exclusion documented)
- [ ] WI-8: Disk space estimate + expected processing time (update
      shared section)
- [ ] WI-9: Recovery strategy (update file filter to `cve/`)
- [ ] Update "Why a Dedicated Fetcher" section (remove point #3 "Extra
      data")

### Shared infrastructure (`fetcher-infrastructure.md`)

- [ ] WI-10 Part A: Add "First-Run Detection" convention to Git-Based
      Fetchers section (cursor-based detection, clone state as
      sub-condition, 4-row truth table with invalid-clone handling)
- [ ] WI-10 Part B: Add "Recovery Strategy (Cursor SHA Unreachable)"
      convention (parametric algorithm with recovery_window and
      recovery_file_filter, window-exceeded behavior, normal-case note)
- [ ] WI-10 Part C: Add "Implementation Guidance" note (git_operations
      utility module — stateless, no DB, typed exceptions, mock boundary)

### MITRE fetcher spec (`cve-tracking.md`)

- [ ] WI-11: Update MITRE first-run detection (lines 529-541) to
      reference shared convention from WI-10 Part A
- [ ] WI-11: Replace shared recovery section (lines 780-795) with
      reference to WI-10 Part B + fetcher-specific parameters table
- [ ] WI-11: Add recovery parameters to MITRE properties table

### Cross-cutting document updates

- [ ] Update `data-sources.md` kernel source entry:
      - Format version: `5.0` → `5.1.1` (published) / `5.0` (rejected)
      - CVE count: `~31,000+` → `12,118` published + `292` rejected
      - `provider_name`: `"Linux Kernel CNA"` → `"Linux"` (verified D12)
      - Add Anubis operational note (from D11)
      - Add clone guidance (from D11)
- [ ] Update Fetcher Registry in `data-sources.md` (status → Complete,
      remove .dyad from description, fix schedule to "Every 3 hours",
      fix CVE count)
- [ ] Update `cve-service.md` (remove `"vulns.git"` source_container
      reference, update "kernel TBD" → "kernel ~3h")
- [ ] Update `data-model.md` (remove `"vulns.git"` from
      source_container example values)
- [ ] Migrate D11 content to permanent locations:
      - JSON structural differences table → `cve-tracking.md`
        (kernel fetcher section, as part of WI-3 application)
      - Operational note (Anubis, never use cvelistV5 as proxy, clone
        guidance) → `data-sources.md` (kernel source entry, access notes)

### Resolved decisions and open points

- [x] Resolve OP-1 — N/A, versions already in JSON semver block
- [x] Resolve OP-2 — N/A, no .dyad processing
- [x] Resolve OP-3 — (a) fetcher constructs URL directly
- [x] Resolve OP-4 — try both field names, no warning for uppercase
- [x] Resolve OP-5 — accept `None`, populated by MITRE/NVD later
- [x] Resolve OP-6 — accept `"cna"`, document data identity invariant
      + transient regression trade-off
- [x] Resolve OP-7 — accept stale references, too rare to justify complexity
- [x] Resolve OP-8 — advance cursor on partial, log each failed CVE
- [x] Resolve OP-9 — year always matches CVE-ID year (verified 12,410/12,410)
- [x] D12 — `provider_name = "Linux"` verified via CVE Services API
      (3/3 CVEs confirmed)

### Final reviews

- [ ] Final review: invoke `@spec-gap-analyzer` on completed spec
- [ ] Final review: invoke `@fetcher-compliance-reviewer`
- [ ] Final review: invoke `@spec-coherence-reviewer` to verify
      cross-spec consistency after all updates
- [ ] Delete this draft file (`docs/drafts/sync-kernel-cves-spec-completion.md`)
      after all changes have been applied and reviewers pass

---

## Reference: Repository Structure (Verified)

From direct analysis of `vulns.git` at HEAD (2026-06-13):

```
vulns.git/
├── .gitignore
├── .gitmodules
├── HOWTO                           # Publishing workflow documentation
├── README
├── LICENSES/
│   ├── GPL-2.0-only.txt
│   └── cve-tou.txt
└── cve/
    ├── CVE_JSON_5.0_schema.json
    ├── CVE_JSON_5.1.1_schema.json
    ├── README                      # State descriptions
    ├── published/YEAR/
    │   ├── CVE-YEAR-ID             # Empty (0 bytes)
    │   ├── CVE-YEAR-ID.json        # ★ CVE record (JSON 5.1.1) — PROCESSED
    │   ├── CVE-YEAR-ID.dyad        # Vulnerable:fixed pairs (redundant with JSON, NOT processed)
    │   ├── CVE-YEAR-ID.sha1        # Fixing commit SHA (redundant)
    │   ├── CVE-YEAR-ID.mbox        # Email announcement
    │   ├── CVE-YEAR-ID.vulnerable  # Introducing SHA (redundant)
    │   ├── CVE-YEAR-ID.reference   # Extra URLs (redundant, 45 files)
    │   ├── CVE-YEAR-ID.cvss        # CVSS vector (redundant, 614 files)
    │   └── CVE-YEAR-ID.message     # Description override (7 files)
    ├── rejected/YEAR/
    │   ├── CVE-YEAR-ID             # Empty
    │   ├── CVE-YEAR-ID.json        # ★ CVE record (state field unreliable!) — PROCESSED
    │   ├── CVE-YEAR-ID.dyad        # (redundant, NOT processed)
    │   ├── CVE-YEAR-ID.sha1
    │   ├── CVE-YEAR-ID.mbox
    │   └── CVE-YEAR-ID.mbox.rejected  # Rejection announcement
    ├── reserved/YEAR/              # Reserved CVE-IDs (no .json files, not processed)
    ├── returned/YEAR/              # Returned CVE-IDs (no .json files, not processed)
    ├── review/                     # Internal review state (2 files, not processed)
    └── testing/                    # Test CVEs (3 files, not processed)
```

**Volume** (as of 2026-06-13, verified from bare clone):
- Published: 12,118 `.json` files
- Rejected: 292 `.json` files
- Total `.json` files to parse per full scan: 12,410
- `.cvss` files (CVSS present in CNA metrics): 614
- Bare clone size: 91 MB

**Publishing pattern**: batches of 60-173 CVEs on kernel stable release
days, followed by quiet periods. A 3-hour schedule typically catches a
batch within one cycle.

---

## Reference: `.dyad` Format (Historical — NOT Processed)

The `.dyad` file format is documented here for reference only. Decision
D10 established that all `.dyad` data is redundant with the JSON
`affected[]` array. The fetcher does NOT parse `.dyad` files.

### Standard case (single introducing commit, multiple branches fixed)

```
# dyad version: add812a5a978
# 	getting vulnerable:fixed pairs for git id 2bcae12c795f32ddfbf8c80d1b5f1d3286341c32
5.10:5af75c747e2a868abbf8611494b50ed5e076fca7:5.10.227:ca36d6c1a49b6965c86dd528a73f38bc62d9c625
5.10:5af75c747e2a868abbf8611494b50ed5e076fca7:5.15.168:ce828b347cf1b3c1b12b091d02463c35ce5097f5
5.10:5af75c747e2a868abbf8611494b50ed5e076fca7:6.1.113:fc357e78176945ca7bcacf92ab794b9ccd41b4f4
5.10:5af75c747e2a868abbf8611494b50ed5e076fca7:6.6.55:26fad69b34fcba80d5c7d9e651f628e6ac927754
5.10:5af75c747e2a868abbf8611494b50ed5e076fca7:6.10.14:ecf310aaf256acbc8182189fe0aa1021c3ddef72
5.10:5af75c747e2a868abbf8611494b50ed5e076fca7:6.11.3:8bb8c12fb5e2b1f03d603d493c92941676f109b5
5.10:5af75c747e2a868abbf8611494b50ed5e076fca7:6.12:2bcae12c795f32ddfbf8c80d1b5f1d3286341c32
```

### Complex case (multiple introducing commits, unfixed branches)

```
# dyad version: 1.2.0
# 	getting vulnerable:fixed pairs for git id 6b504d06976fe4a61cc05dedc68b84fadb397f77
# 	Setting original vulnerable kernel to be kernel 6.0 and git id e0fb8ce2bb9e52c846e54ad2c58b5b7beb13eb09
5.15.61:7b2fbfa4b2cd3a24c1760b85d842e928070d4744:5.15.121:4406fe8a96a946c7ea5724ee59625755a1d9c59d
6.0:e0fb8ce2bb9e52c846e54ad2c58b5b7beb13eb09:6.1.40:477bc74ad1add644b606bff6ba1284943c42818a
6.0:e0fb8ce2bb9e52c846e54ad2c58b5b7beb13eb09:6.4.5:7bbeff613ec0560fb2f6f8b405288f3f043adf64
6.0:e0fb8ce2bb9e52c846e54ad2c58b5b7beb13eb09:6.5:6b504d06976fe4a61cc05dedc68b84fadb397f77
5.18.18:47b583ad1f7e459689eb1bdd222279a6986ccd69:0:0
5.19.2:d2deafaef0330a863b5e046c1154b605588d19f7:0:0
```

Lines with `0:0` as the last two fields indicate branches where the
bug was introduced but no fix has been applied.

---

## Cross-References

- Primary spec location: `docs/features/tickets/cve-tracking.md` (section "Fetcher: sync_kernel_cves")
- Shared git infrastructure: `docs/features/platform/fetcher-infrastructure.md` (section "Git-Based Fetchers")
- CVE service contract: `docs/features/tickets/cve-service.md`
- Data model (CVEAffectedVersion): `docs/data-model.md` (line 573)
- Fetcher Registry: `docs/data-sources.md` (line 941)
- MITRE fetcher (reference): `docs/features/tickets/cve-tracking.md` (lines 513-810)
- Kernel source entry: `docs/data-sources.md` (lines 191-246)
- CVE Services API verification (D12): `cvelistV5` GitHub raw content
  (CVE-2024-50055, CVE-2023-52752, CVE-2024-26687)
- **Follow-up draft**: `docs/drafts/git-base-fetcher-class.md` —
  `GitBaseFetcher` intermediate class design. Applies AFTER this draft
  is completed. The conventions defined here (WI-10 Parts A, B, C)
  become the class interface specified there
