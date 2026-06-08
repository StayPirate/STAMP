# Draft: Fetcher Naming Convention

## Status

**Draft** — agreed in principle, pending application.

## Problem

Current fetcher names use inconsistent patterns:

| Fetcher | Pattern | Example reading |
|---------|---------|-----------------|
| `sync_cves_nvd` | `verb_noun_source` | "sync CVEs from NVD" |
| `sync_cves_mitre` | `verb_noun_source` | "sync CVEs from MITRE" |
| `sync_cvss_redhat` | `verb_noun_source` | "sync CVSS from Red Hat" |
| `sync_smelt_products` | `verb_source_noun` | "sync SMELT products" |
| `sync_aimaas_lifecycle` | `verb_source_noun` | "sync AIMAAS lifecycle" |
| `sync_aimaas_thresholds` | `verb_source_noun` | "sync AIMAAS thresholds" |
| `sync_ldap_directory` | `verb_source_noun` | "sync LDAP directory" |
| `sync_package_bugowners` | `verb_noun` | source implicit (IBS) |
| `sync_requests` | `verb_noun` | source implicit (IBS) |
| `check_ibs_track_releases` | `verb_source_noun_noun` | "check IBS track releases" |
| `check_product_releases` | `verb_noun` | source implicit (IBS) |
| `check_lifecycle_phase_transitions` | `verb_noun` | local, no source |
| `aggregate_fetcher_runs` | `verb_noun` | local, maintenance |

Three problems:

1. **Mixed pattern**: CVE fetchers use `verb_noun_source`, most others
   use `verb_source_noun`
2. **Inconsistent verb semantics**: `sync` and `check` have no clear
   distinction
3. **Implicit source**: some fetchers omit the source, making the name
   ambiguous without reading the spec

## Convention

### Scope

This convention applies exclusively to `BaseFetcher` subclasses — the
background tasks registered in the fetcher infrastructure and visible in
the fetcher dashboard.

Out of scope:

- **Sub-operation Celery tasks** exempt from `BaseFetcher` per
  Guardrail 14 (e.g., `create_ticket_from_detection`,
  `discover_submissions_for_ticket_package`)
- **On-demand service methods** (e.g., `fetch_single_cve`)
- **Non-fetcher Celery tasks** (e.g., `cleanup_sessions`,
  `cleanup_stale_ticket_access_grants`)
- **Continuous consumers** (e.g., `IBSEventConsumer`)

### Pattern: `<verb>_<source>_<noun>`

All fetcher names follow the pattern `verb_source_noun`, which reads as
a natural English compound noun: "sync NVD CVEs", "detect IBS releases".

### Verbs

Three verbs, each with a distinct operational category:

| Verb | Meaning | When to use |
|------|---------|-------------|
| `sync` | Periodic data pull from an external source | Any fetcher that imports or refreshes data from a remote service |
| `detect` | Condition or state change verification against an external source | Release detection, event monitoring, or any fetcher that checks whether a specific condition has changed in an external system |
| `evaluate` | Local computation, no external source | Lifecycle transitions, recalculations, or any fetcher that derives new state from data already in the database |

Plus the existing `aggregate` verb for maintenance operations (data
compaction, cleanup).

### Source

The source segment identifies the external system. For local fetchers
(`evaluate`, `aggregate`), this segment is omitted and the pattern
reduces to `verb_noun`:

| Source | External system |
|--------|----------------|
| `nvd` | NIST NVD |
| `mitre` | MITRE CVE Services |
| `redhat` | Red Hat Security Data API |
| `kernel` | Linux Kernel CNA |
| `ghsa` | GitHub Advisory Database |
| `osv` | OSV (osv.dev) |
| `cisa` | CISA (KEV catalog) |
| `epss` | FIRST.org EPSS |
| `smelt` | SMELT |
| `aimaas` | AIMAAS |
| `ibs` | IBS (build.suse.de) |
| `ldap` | SUSE Active Directory (LDAP protocol) |

New sources follow the same rule: use the shortest unambiguous lowercase
identifier for the external system.

### Noun

The noun describes what data is being synced, detected, or evaluated.
Use the most general accurate term:

- `cves` — all CVE-related data the source provides (CVSS, CWE,
  references, affected versions, etc.). Do NOT narrow the noun to a
  single data type (`cvss`, `cwe`) when the fetcher extracts multiple
  types from the same API call
- `products` — product catalog records
- `lifecycle` — product lifecycle dates
- `thresholds` — CVSS thresholds
- `kev` — CISA Known Exploited Vulnerabilities catalog entries
- `track_releases` — codestream-level release detection
- `product_releases` — product-level release detection

### Complete Rename Mapping

| Current name | New name | Current class | New class | Change reason |
|---|---|---|---|---|
| `sync_cves_nvd` | `sync_nvd_cves` | `SyncCvesNvd` | `SyncNvdCves` | Reorder to `verb_source_noun` |
| `sync_cves_mitre` | `sync_mitre_cves` | `SyncCvesMitre` | `SyncMitreCves` | Reorder to `verb_source_noun` |
| `sync_cvss_redhat` | `sync_redhat_cves` | `SyncCvssRedhat` | `SyncRedhatCves` | Reorder + generalize noun (now syncs CVSS, CWE, refs, packages) |
| `sync_kernel_cves` | `sync_kernel_cves` | `SyncKernelCves` | `SyncKernelCves` | Already correct pattern |
| `sync_ghsa` | `sync_ghsa_advisories` | — | `SyncGhsaAdvisories` | Add noun |
| `sync_osv` | `sync_osv_advisories` | `OSVSyncFetcher` | `SyncOsvAdvisories` | Add noun + fix non-conforming class name |
| `sync_cisa_kev` | `sync_cisa_kev` | — | `SyncCisaKev` | Already correct (KEV is the noun) |
| `sync_epss` | `sync_epss_scores` | — | `SyncEpssScores` | Add noun |
| `sync_smelt_products` | `sync_smelt_products` | TBD | `SyncSmeltProducts` | Already correct |
| `sync_aimaas_lifecycle` | `sync_aimaas_lifecycle` | TBD | `SyncAimaasLifecycle` | Already correct |
| `sync_aimaas_thresholds` | `sync_aimaas_thresholds` | TBD | `SyncAimaasThresholds` | Already correct |
| `sync_package_bugowners` | `sync_ibs_bugowners` | `SyncPackageBugowners` | `SyncIbsBugowners` | Add source |
| `sync_requests` | `sync_ibs_requests` | `RequestSyncFetcher` | `SyncIbsRequests` | Add source + fix non-conforming class name |
| `sync_ldap_directory` | `sync_ldap_directory` | `SyncLdapDirectory` | `SyncLdapDirectory` | Already correct |
| `check_ibs_track_releases` | `detect_ibs_track_releases` | `CheckIbsTrackReleases` | `DetectIbsTrackReleases` | Change verb |
| `check_product_releases` | `detect_ibs_product_releases` | `CheckProductReleases` | `DetectIbsProductReleases` | Change verb + add source |
| `check_lifecycle_phase_transitions` | `evaluate_lifecycle_transitions` | `CheckLifecyclePhaseTransitions` | `EvaluateLifecycleTransitions` | Change verb (local, no source) + simplify noun |
| `aggregate_fetcher_runs` | `aggregate_fetcher_runs` | `AggregationFetcher` | `AggregateFetcherRuns` | Fix non-conforming class name |

Three class names do not follow the mechanical `snake_case` →
`PascalCase` derivation rule and require explicit renames:
`OSVSyncFetcher` → `SyncOsvAdvisories`, `RequestSyncFetcher` →
`SyncIbsRequests`, `AggregationFetcher` → `AggregateFetcherRuns`.
Entries marked "—" in "Current class" are planned fetchers with no
class name assigned yet; entries marked "TBD" have the class name
listed as TBD in their spec.

### Class Name Derivation

The Python class name is derived mechanically from the fetcher name by
converting `snake_case` to `PascalCase` (e.g., `sync_nvd_cves` →
`SyncNvdCves`). No suffixes like `Fetcher` or `Sync` are added — the
class name IS the PascalCase form of the fetcher name, nothing more.

**Acronym casing**: all segments are title-cased regardless of whether
they are acronyms — `nvd` → `Nvd`, not `NVD`; `ibs` → `Ibs`, not
`IBS`; `ghsa` → `Ghsa`, not `GHSA`. This keeps the derivation
mechanical and unambiguous.

The complete rename mapping table above includes both current and new
class names. Three existing class names violate the mechanical rule
and require explicit renames: `OSVSyncFetcher`, `RequestSyncFetcher`,
and `AggregationFetcher` (see "Change reason" column).

### Alphabetical Sort Order

The new names group naturally by verb and then by source:

```
aggregate_fetcher_runs
detect_ibs_product_releases
detect_ibs_track_releases
evaluate_lifecycle_transitions
sync_aimaas_lifecycle
sync_aimaas_thresholds
sync_cisa_kev
sync_epss_scores
sync_ghsa_advisories
sync_ibs_bugowners
sync_ibs_requests
sync_kernel_cves
sync_ldap_directory
sync_mitre_cves
sync_nvd_cves
sync_osv_advisories
sync_redhat_cves
sync_smelt_products
```

## Spec Changes Required

### 1. `docs/features/platform/fetcher-infrastructure.md` — Naming Convention Section

Add the full naming convention (scope, pattern, verbs, sources, nouns,
class name derivation, acronym casing rule) as a new "Naming
Convention" subsection near the Abstract Interface section (after the
existing `name` attribute constraints at line 132). This keeps all
fetcher naming rules in the same document as the `BaseFetcher`
contract, avoiding fragmentation.

Additionally, update all existing fetcher name references throughout
the spec (code examples, registry examples, validation rules). Key
locations:

- Line 132: `name: str = "my_fetcher"` example
- Lines 164-188: `SyncCvesNvd` / `SyncCvssRedhat` code examples →
  rename to `SyncNvdCves` / `SyncRedhatCves`
- Lines 493, 640: `sync_cvss_redhat` references
- Anywhere `sync_cves_nvd`, `sync_cves_mitre`, `check_ibs_*`,
  `check_product_releases`, `sync_package_bugowners`, or
  `sync_requests` appear

### 2. `docs/conventions.md` — Cross-reference

Add a one-line bullet under "Python (Backend) > Naming" (after
line 145) pointing to the fetcher-infrastructure spec. This follows
the same delegation pattern already used by the "Fetcher Documentation"
subsection (line 552):

```markdown
- **Fetchers**: `BaseFetcher` subclass naming follows the
  `<verb>_<source>_<noun>` convention — see
  `docs/features/platform/fetcher-infrastructure.md` (Naming
  Convention)
```

### 3. `docs/data-sources.md` — Fetcher Registry

Update all fetcher names in the Fetcher Registry table (lines 786-806)
and the CVE Enrichment Data Structures table (lines 817-824).

### 4. Markdown anchors and cross-reference links

Fetcher names appear inside markdown headers, which generate anchor
slugs. Renaming the header text changes the anchor, breaking any
inbound links. Both the headers and their inbound links must be
updated atomically.

**Headers containing fetcher names** (11 in approved specs, 10
requiring rename):

| File | Line | Current header | New header |
|---|---|---|---|
| `docs/features/tickets/cve-tracking.md` | 376 | `` ### Fetcher: `sync_cves_nvd` `` | `` ### Fetcher: `sync_nvd_cves` `` |
| `docs/features/tickets/cve-tracking.md` | 484 | `` ### Fetcher: `sync_cves_mitre` `` | `` ### Fetcher: `sync_mitre_cves` `` |
| `docs/features/tickets/cve-tracking.md` | 646 | `` ### Fetcher: `sync_kernel_cves` `` | unchanged |
| `docs/features/tickets/cve-tracking.md` | 736 | `` ### Fetcher: `sync_osv` `` | `` ### Fetcher: `sync_osv_advisories` `` |
| `docs/features/tickets/cve-tracking.md` | 760 | `` ### `sync_cvss_redhat` `` | `` ### `sync_redhat_cves` `` |
| `docs/features/tickets/cvss-scoring.md` | 731 | `` ### Fetcher: `sync_cvss_redhat` `` | `` ### Fetcher: `sync_redhat_cves` `` |
| `docs/features/packages/ibs-track-release-detection.md` | 197 | `` ### Fetcher: `check_ibs_track_releases` `` | `` ### Fetcher: `detect_ibs_track_releases` `` |
| `docs/features/packages/ibs-product-release-detection.md` | 247 | `` ### Fetcher: `check_product_releases` `` | `` ### Fetcher: `detect_ibs_product_releases` `` |
| `docs/features/packages/product-lifecycle-transitions.md` | 36 | `` ### Fetcher: `check_lifecycle_phase_transitions` `` | `` ### Fetcher: `evaluate_lifecycle_transitions` `` |
| `docs/features/packages/ibs-submission-tracking.md` | 996 | `` ### Fetcher: `sync_requests` `` | `` ### Fetcher: `sync_ibs_requests` `` |
| `docs/features/packages/ibs-submission-tracking.md` | 1053 | `### sync_requests — Custom Settings` | `### sync_ibs_requests — Custom Settings` |

**Links to update** (all in `docs/data-sources.md`, Fetcher Registry):

| Line | Current anchor fragment | New anchor fragment |
|---|---|---|
| 788 | `#fetcher-sync_cves_nvd` | `#fetcher-sync_nvd_cves` |
| 789 | `#fetcher-sync_cves_mitre` | `#fetcher-sync_mitre_cves` |
| 790 | `#fetcher-sync_cvss_redhat` | `#fetcher-sync_redhat_cves` |
| 794 | `#fetcher-check_ibs_track_releases` | `#fetcher-detect_ibs_track_releases` |
| 795 | `#fetcher-check_product_releases` | `#fetcher-detect_ibs_product_releases` |
| 798 | `#fetcher-check_lifecycle_phase_transitions` | `#fetcher-evaluate_lifecycle_transitions` |
| 800 | `#fetcher-sync_requests` | `#fetcher-sync_ibs_requests` |

Note: `sync_kernel_cves` (line 804) and `sync_ldap_directory` are
unchanged — their links remain valid.

### 5. Feature specs and cross-cutting documents

Every spec that references a fetcher by name or class name needs
updating. Exhaustive list (verified by project-wide search):

- `docs/features/tickets/cvss-scoring.md`
- `docs/features/tickets/cve-tracking.md`
- `docs/features/tickets/cve-service.md`
- `docs/features/tickets/ticket-references.md`
- `docs/features/tickets/ticket-service.md`
- `docs/features/packages/package-bugowner.md`
- `docs/features/packages/package-model.md`
- `docs/features/packages/ibs-track-release-detection.md`
- `docs/features/packages/ibs-product-release-detection.md`
- `docs/features/packages/ibs-submission-tracking.md`
- `docs/features/packages/product-catalog.md`
- `docs/features/packages/product-lifecycle-transitions.md`
- `docs/features/packages/git-track-release-detection.md`
- `docs/features/integrations/ibs-integration.md`
- `docs/features/identity/ad-integration.md`
- `docs/features/platform/fetcher-operations.md`
- `docs/features/platform/fetcher-infrastructure.md`
- `docs/system-map.md`
- `docs/data-model.md`
- `docs/data-sources.md`
- `docs/configuration.md` (contains `sync_ldap_directory` — unchanged,
  but listed for completeness)
- `docs/architecture.md`

**Class name prose references**: the class name `RequestSyncFetcher`
is used as a prose identifier (not just in properties tables) across
`ibs-submission-tracking.md`, `package-model.md`, `ibs-integration.md`,
and `architecture.md` (~20 occurrences). These must all be updated to
`SyncIbsRequests`.

**Mermaid diagram labels**: `docs/system-map.md` contains fetcher
names inside Mermaid diagram node labels (e.g.,
`SYNC_NVD["sync_cves_nvd"]`, line 435). These are rendered text, not
markdown headers — a simple find-and-replace on the fetcher name
string is sufficient, but the Mermaid variable names (e.g.,
`SYNC_NVD`, `SYNC_RH`) are internal identifiers and do not need to
follow the convention.

**Pre-existing error**: `docs/features/platform/fetcher-operations.md`
line 875 uses `sync_products_smelt` — a name that does not exist in
any spec. The correct current name is `sync_smelt_products`. Fix
during the rename pass.

### 6. `AGENTS.md`

Update `check_ibs_track_releases` → `detect_ibs_track_releases` in the
Guardrail 14 sub-operation exception (line 482). The
`sync_ldap_directory` reference in the AD section is already correct.

### 7. `docs/reviews/` — decision required

Two review finding files contain old fetcher names in headers:

- `docs/reviews/cvss-scoring.md:15` — `sync_cvss_redhat` in header
- `docs/reviews/cve-service.md:94` — `sync_osv` in header

Review findings are historical records. Options:

- **(a)** Update the names for repository-wide consistency
- **(b)** Leave them unchanged — they reflect the state at the time of
  review (recommended)

## Application Strategy

Since no implementation code exists yet (all backend Python files are
stubs), the rename is a documentation-only operation with zero
migration cost. The rename should be applied as a single batch
operation across all spec files to avoid inconsistent intermediate
states.

### Name stability after implementation

Once implementation begins, the fetcher `name` attribute becomes a
stable identifier stored in persistent data:

- `FetcherConfig.fetcher_name` — VARCHAR(100) primary key with FK
  constraints from `FetcherRun`, `FetcherAuditEvent`, and
  `FetcherRunWeeklyAggregate`
- `TicketReference.source` — stores the fetcher name that created
  the reference
- `CVESource.source` — stores the fetcher name via `cve_source_type`

After data exists, renaming a fetcher requires an Alembic data
migration to cascade the name change through all referencing tables
(see `docs/features/tickets/ticket-references.md` and
`docs/features/platform/fetcher-infrastructure.md`, stability rules).
This is why standardizing the names now — before any data is
persisted — has zero migration cost.

### Steps

1. Agree on the convention and rename mapping (this draft)
2. Add naming convention to `fetcher-infrastructure.md` and
   cross-reference from `conventions.md`
3. Apply renames across all spec files in a single pass
4. Delete this draft file (`docs/drafts/fetcher-naming-convention.md`)
   — the convention now lives in `fetcher-infrastructure.md` and
   the rename mapping has been fully applied
5. Verify completeness: grep the old names across `docs/` and
   `AGENTS.md` to confirm zero remaining occurrences:
   ```
   grep -rE 'sync_cves_nvd|sync_cves_mitre|sync_cvss_redhat|sync_package_bugowners|check_ibs_track_releases|check_product_releases|check_lifecycle_phase_transitions|sync_products_smelt' docs/ AGENTS.md
   ```
   Also grep for old class names:
   ```
   grep -rE 'SyncCvesNvd|SyncCvesMitre|SyncCvssRedhat|OSVSyncFetcher|RequestSyncFetcher|AggregationFetcher|CheckIbsTrackReleases|CheckProductReleases|CheckLifecyclePhaseTransitions|SyncPackageBugowners' docs/ AGENTS.md
   ```
6. Run `@spec-coherence-reviewer` on affected specs to verify
   consistency
