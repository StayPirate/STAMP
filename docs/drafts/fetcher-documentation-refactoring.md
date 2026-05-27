# Fetcher Documentation Refactoring

**Status**: Draft — not yet applied  
**Created**: 2026-05-27  
**Goal**: Establish consistent conventions for documenting fetchers in feature
specs, then audit and align all existing fetchers to the new standard.

---

## Background

Sentinel has 13 active `BaseFetcher` subclasses documented across 10 spec
files, using three inconsistent patterns:

1. **Dedicated spec** (3 fetchers): the spec exists solely to define the
   fetcher (e.g., `ibs-track-release-detection.md`)
2. **Dominant in feature spec** (3 fetchers): >45% of the spec is the fetcher
   (e.g., `sync_ldap_directory` is 95% of `ad-integration.md`)
3. **Embedded section** (7 fetchers): a few lines or paragraphs in a larger
   feature spec (e.g., `sync_smelt_products` has 4 lines in
   `product-catalog.md`)

Key problems:
- **Fragmentation**: `sync_cves_nvd` is scattered across 3 specs with no
  single source of truth
- **Inconsistent depth**: ranges from 950 lines (`sync_ldap_directory`) to
  3 lines (`sync_smelt_products`)
- **No classification rule**: no principled basis for choosing dedicated vs.
  embedded
- **Missing error handling**: only 5 of 12 applicable fetchers document it

---

## Fetcher Placement Map

Summary of where each fetcher lives after refactoring. Only 2 fetchers
require consolidation (content moved); the rest stay where they are.

| Fetcher | Current location | Target location | Action |
|---|---|---|---|
| `sync_cves_nvd` | Fragmented: `cve-tracking.md` + `cvss-scoring.md` + `ticket-references.md` | `tickets/cve-tracking.md` | Consolidate from 3 specs. Cross-reference in other 2. |
| `sync_cves_mitre` | 3 lines in `cve-tracking.md` | `tickets/cve-tracking.md` | Stays. TBD template. |
| `sync_cvss_redhat` | Section in `cvss-scoring.md` | `tickets/cvss-scoring.md` | Stays. Compliance audit. |
| `sync_ldap_directory` | Dominant in `ad-integration.md` | `identity/ad-integration.md` | Stays. Compliance audit. |
| `sync_smelt_products` | 4 lines in `product-catalog.md` | `packages/product-catalog.md` | Stays. TBD template. |
| `sync_aimaas_lifecycle` | 4 lines in `product-catalog.md` | `packages/product-catalog.md` | Stays. TBD template. |
| `sync_aimaas_thresholds` | 4 lines in `product-catalog.md` | `packages/product-catalog.md` | Stays. TBD template. |
| `check_ibs_track_releases` | Dedicated spec | `packages/ibs-track-release-detection.md` | Stays. Compliance audit. |
| `check_product_releases` | Dedicated spec | `packages/ibs-product-release-detection.md` | Stays. Compliance audit. |
| `check_lifecycle_phase_transitions` | Dedicated spec | `packages/product-lifecycle-transitions.md` | Stays. Compliance audit. |
| `sync_package_bugowners` | Heavy section in `package-bugowner.md` | `packages/package-bugowner.md` | Stays. Compliance audit. |
| `sync_requests` | Heavy section in `ibs-submission-tracking.md` | `packages/ibs-submission-tracking.md` | Stays. Compliance audit. |
| `aggregate_fetcher_runs` | Split: `fetcher-operations.md` + `fetcher-infrastructure.md` | `platform/fetcher-operations.md` | Consolidate from 2 specs. Cross-reference in other. |

No files are renamed or moved. All changes are content edits within
existing files.

---

## Phase 1: Establish the Rules

### 1.1 Add "Fetcher Documentation" pointer to `docs/conventions.md`

Insert the following section after the existing "Feature Specifications"
section (end of file, after line 529). This follows the same pattern used
by the Audit Trail section (lines 202-211 of `conventions.md`): a brief
pointer in `conventions.md`, with the full specification in the
infrastructure spec.

```markdown
### Fetcher Documentation

Every `BaseFetcher` subclass MUST have its specification documented
following the fetcher documentation requirements defined in
`docs/features/platform/fetcher-infrastructure.md` (section "Fetcher
Documentation Requirements"). This includes the classification rule
(dedicated spec vs. embedded section), the minimum documentation
template, and the Fetcher Registry maintenance obligation.
```

### 1.2 Add "Fetcher Documentation Requirements" section to `fetcher-infrastructure.md`

Insert the following section in `docs/features/platform/fetcher-infrastructure.md`
after the existing "Referencing custom settings in fetcher specifications"
section (after the Custom Settings Schema block). This is the authoritative
location for all fetcher documentation rules — it co-locates them with the
existing error handling and custom settings documentation requirements that
`fetcher-infrastructure.md` already owns.

```markdown
## Fetcher Documentation Requirements

Every `BaseFetcher` subclass MUST have its complete definition in exactly
one specification document (single source of truth). Other specs may
reference it and include brief consumer-oriented summaries (see
"Cross-reference summaries" below), but MUST NOT specify the fetcher's
algorithm steps, error handling behavior, or custom settings.

### Classification Rule

The deciding factor for whether a fetcher gets a dedicated spec or lives
as a section in a feature spec is its **role**:

| Classification | Criterion | Spec treatment |
|---|---|---|
| The fetcher IS the feature | The spec would not exist without the fetcher. No distinct UI, API, or data model beyond what the fetcher requires. | Dedicated spec in the relevant domain. Named after what it does, not after the mechanism. |
| The fetcher supports a feature | The feature has its own identity (data model, API, UI, operations) and the fetcher is how data enters or exits. | Section within the feature spec, following the mandatory minimum template below. |

Test: if you removed the fetcher from the spec, would the spec still have
something meaningful to say? If yes → embedded. If no → dedicated spec.

Refinement for fetcher-centric specs: if the remaining non-fetcher
content exists primarily to support the fetcher itself (connection
details, authentication rationale, attribute mappings) rather than
serving independent consumers (APIs, UI, other specs), the spec is a
fetcher-centric spec — classify it as "the fetcher IS the feature."

### Minimum Documentation Template

Every fetcher — whether in a dedicated spec or embedded as a section —
MUST include at minimum:

1. **Properties table**:

   | Property | Value |
   |----------|-------|
   | Fetcher name | `<registry name>` |
   | Class name | `<PascalCase class>` |
   | Schedule | `<cron expression>` + human-readable |
   | Source | `<external service name>` |
   | Scope | `<what the fetcher processes per run>` |
   | Auth | `<authentication method>` |
   | Custom settings | Yes / No (link to Custom Settings section if yes) |

2. **Algorithm** (numbered steps describing what the fetcher does on each
   execution)

3. **Error handling** (what happens on failure — retry behavior, sanitized
   messages, partial progress). Exempt: fetchers that only interact with
   the local database.

4. **Metrics** (what counts as `record_created`, `record_updated`,
   `record_failed` — one sentence each)

5. **Custom settings table** (if applicable — following the format defined
   in the Custom Settings Schema section above)

A fetcher whose template contains TBD values is structurally prepared but
NOT considered compliant. Compliance requires real content in all
mandatory sections. TBD placeholders indicate that the fetcher's design
is pending and must be completed before implementation begins.

### Cross-Reference Summaries

Specs that consume data produced by a fetcher defined elsewhere may
include a brief consumer-oriented summary (3-5 sentences) alongside the
cross-reference, to provide reading continuity. The summary MUST describe
*what* the fetcher produces from the consumer's perspective, but MUST NOT
specify algorithm steps, error handling behavior, or custom settings.
The cross-referenced spec remains the single source of truth.

Example: `cvss-scoring.md` may summarize that `sync_cves_nvd` creates
CVSS assessments during each sync run, but must not describe the
incremental fetch strategy or the NVD Source API caching mechanism.

### Registry Maintenance

When defining a new fetcher, the Fetcher Registry table in
`docs/data-sources.md` MUST be updated with a row for the new fetcher.

### Domain Placement

Fetchers live in the domain they serve, not in a centralized `fetchers/`
folder:

- CVE/CVSS fetchers → `tickets/`
- Product/package fetchers → `packages/`
- Identity fetchers → `identity/`
- Platform-internal fetchers → `platform/`
- Integration-layer fetchers (if any) → `integrations/`
```

### 1.3 Update Guardrail 14 in `AGENTS.md`

Add a paragraph after the existing Guardrail 14 text (after line 495,
before Guardrail 15), extending it to cover documentation:

```markdown
#### Fetcher documentation compliance

When defining or modifying a fetcher in a feature specification, the
documentation MUST follow the "Fetcher Documentation Requirements"
section in `docs/features/platform/fetcher-infrastructure.md`:

1. The fetcher's complete definition lives in exactly one spec (single
   source of truth)
2. The minimum documentation template is satisfied (properties table,
   algorithm, error handling, metrics)
3. The Fetcher Registry in `docs/data-sources.md` is updated
4. The classification rule is applied correctly (dedicated spec vs.
   embedded section)

If modifying a fetcher whose definition is currently fragmented across
multiple specs, consolidate it into its primary spec before proceeding
with the modification.
```

### 1.4 Update existing error handling section in `fetcher-infrastructure.md`

Change the SHOULD to MUST on line 180 and add a cross-reference to the
new section:

**Current text** (lines 178-188):
```
### Referencing error handling in fetcher specifications

Feature specifications that define fetchers SHOULD include an "Error
Handling" section documenting which exceptions the fetcher catches and
what sanitized messages it produces. The `@fetcher-compliance-reviewer`
agent verifies this documentation exists.

Fetchers that only interact with the local database (e.g.,
`aggregate_fetcher_runs`, `check_lifecycle_phase_transitions`) are exempt
from this requirement — their failure modes do not involve external
service details.
```

**New text**:
```
### Referencing error handling in fetcher specifications

Feature specifications that define fetchers MUST include an "Error
Handling" section documenting which exceptions the fetcher catches and
what sanitized messages it produces. The `@fetcher-compliance-reviewer`
agent verifies this documentation exists.

Fetchers that only interact with the local database (e.g.,
`aggregate_fetcher_runs`, `check_lifecycle_phase_transitions`) are exempt
from this requirement — their failure modes do not involve external
service details.

Error handling is one of the mandatory sections in the minimum
documentation template — see "Fetcher Documentation Requirements" below
for the full template.
```

---

## Phase 2: Consolidate Fragmented Fetchers

**Execution order**: for each consolidation, first write the consolidated
section in the target spec, then update the source specs with
cross-references. This avoids a state where content has been removed from
one spec but not yet added to another.

### 2.1 Consolidate `sync_cves_nvd`

**Problem**: definition is scattered across:
- `tickets/cve-tracking.md` — ticket creation behavior, first run
  strategy, on-demand fetch
- `tickets/cvss-scoring.md` — CVSS assessment extraction, NVD Source API
  caching, incremental strategy
- `tickets/ticket-references.md` — source reference URL pattern

**Action**:

1. Read the fetcher-related content from all 3 specs
2. Write a consolidated "NVD Fetcher (`sync_cves_nvd`)" section in
   `tickets/cve-tracking.md` covering:
   - Properties table (name, class, schedule, scope, source, auth)
   - Algorithm (incremental fetch, CVE upsert, CVSS extraction, reference
     creation, ticket creation trigger)
   - NVD Source API caching strategy (currently in `cvss-scoring.md`)
   - First-run behavior / historical backfill
   - On-demand single CVE fetch (`fetch_single`)
   - Error handling
   - Metrics
3. In `cvss-scoring.md`:
   - Replace the NVD fetcher algorithm details (Data Sync > NVD Sync
     section) with a consumer-oriented summary + cross-reference to
     `cve-tracking.md`
   - Update the Providers > CNA section (lines 125-128) which also
     mentions NVD Source API caching inline — replace the caching
     mechanism detail with a reference to the consolidated section,
     keeping only the consumer-relevant description (CNA name resolution)
4. In `ticket-references.md`, replace the NVD-specific fetcher details
   with a consumer-oriented summary + cross-reference to
   `cve-tracking.md`

### 2.2 Consolidate `aggregate_fetcher_runs`

**Problem**: split between `fetcher-operations.md` (Background Tasks
section) and `fetcher-infrastructure.md` (Data Retention section).

**Action**:

1. Read the aggregation-related content from both specs
2. Write the complete fetcher definition in `fetcher-operations.md`
   (the natural owner — it's part of operational monitoring)
3. In `fetcher-infrastructure.md`, the Data Retention section keeps only a
   reference:
   > The aggregation algorithm is implemented by `aggregate_fetcher_runs`,
   > defined in `docs/features/platform/fetcher-operations.md`.

### 2.3 Fix Fetcher Registry gaps in `data-sources.md`

The Fetcher Registry in `data-sources.md` (lines 741-758) is missing rows
for two active fetchers:

- `aggregate_fetcher_runs` — local-only, defined in
  `fetcher-operations.md`
- `sync_requests` (`RequestSyncFetcher`) — defined in
  `ibs-submission-tracking.md`

Add rows for both fetchers to the registry table.

---

## Phase 3: Apply TBD Templates to Under-Specified Fetchers

These fetchers currently have only a name and one-sentence description.
Their feature design is not yet complete, so detailed algorithm and error
handling content cannot be written. Instead, apply the minimum
documentation template with TBD placeholders for unknown values and
fill in the properties that are already known (name, source, auth).

When the feature is fully specified in a future session, the TBD values
will be replaced with real content.

### 3.1 `sync_smelt_products` (in `product-catalog.md`)

Replace the current minimal entry with:

```markdown
### Fetcher: `sync_smelt_products`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_smelt_products` |
| Class name | TBD |
| Schedule | TBD |
| Source | SMELT (`smelt.suse.de/api`) |
| Scope | TBD |
| Auth | TBD (internal) |
| Custom settings | TBD |

#### Algorithm

TBD

#### Error Handling

TBD

#### Metrics

TBD
```

### 3.2 `sync_aimaas_lifecycle` (in `product-catalog.md`)

```markdown
### Fetcher: `sync_aimaas_lifecycle`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_aimaas_lifecycle` |
| Class name | TBD |
| Schedule | TBD |
| Source | AIMAAS (`aimaas.suse.de/api`) |
| Scope | TBD |
| Auth | TBD (internal) |
| Custom settings | TBD |

#### Algorithm

TBD

#### Error Handling

TBD

#### Metrics

TBD
```

### 3.3 `sync_aimaas_thresholds` (in `product-catalog.md`)

```markdown
### Fetcher: `sync_aimaas_thresholds`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_aimaas_thresholds` |
| Class name | TBD |
| Schedule | TBD |
| Source | AIMAAS (`aimaas.suse.de/api`) |
| Scope | TBD |
| Auth | TBD (internal) |
| Custom settings | TBD |

#### Algorithm

TBD

#### Error Handling

TBD

#### Metrics

TBD
```

### 3.4 `sync_cves_mitre` (in `cve-tracking.md`)

```markdown
### Fetcher: `sync_cves_mitre`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_cves_mitre` |
| Class name | TBD |
| Schedule | Every 6 hours (`0 */6 * * *`) |
| Source | MITRE CVE Services |
| Scope | TBD |
| Auth | None |
| Custom settings | TBD |

#### Algorithm

TBD

#### Error Handling

TBD

#### Metrics

TBD
```

---

## Phase 4: Compliance Audit of Existing Fetchers

For each fetcher that is already well-documented, verify compliance with
the minimum template defined in Phase 1. This audit runs after Phases 2
and 3 so that consolidated and restructured fetchers are also checked.

A fetcher is **compliant** if all mandatory sections contain real content
(not TBD). A fetcher with TBD values is **structurally prepared** but not
compliant — no action is needed during this refactoring, but the TBD
values must be resolved before implementation begins.

### Fetchers to audit:

| Fetcher | Primary spec | Expected status |
|---|---|---|
| `sync_cves_nvd` | `tickets/cve-tracking.md` | Verify after Phase 2 consolidation |
| `sync_cvss_redhat` | `tickets/cvss-scoring.md` | Mostly compliant (verify error handling section) |
| `sync_ldap_directory` | `identity/ad-integration.md` | Compliant (extremely detailed) |
| `check_ibs_track_releases` | `packages/ibs-track-release-detection.md` | Compliant (has algorithm, error handling, properties) |
| `check_product_releases` | `packages/ibs-product-release-detection.md` | Mostly compliant (needs schedule, has open items) |
| `check_lifecycle_phase_transitions` | `packages/product-lifecycle-transitions.md` | Mostly compliant (verify metrics section) |
| `sync_package_bugowners` | `packages/package-bugowner.md` | Compliant (algorithm, error handling, properties) |
| `sync_requests` | `packages/ibs-submission-tracking.md` | Compliant (algorithm, error handling, custom settings) |
| `aggregate_fetcher_runs` | `platform/fetcher-operations.md` | Verify after Phase 2 consolidation |

### Checklist per fetcher:

- [ ] Properties table present with all required fields (including Scope)
- [ ] Algorithm described as numbered steps
- [ ] Error handling section present (or exempt: local-only fetchers)
- [ ] Metrics documented (created/updated/failed semantics)
- [ ] Custom settings table present (if applicable)
- [ ] No definition leakage into other specs (single source of truth)
- [ ] Fetcher Registry row in `data-sources.md` up to date

Record any gaps found during the audit directly in this section (append
notes per fetcher). Minor gaps should be fixed inline during this
refactoring. Gaps that require design decisions should be flagged as TBD
and deferred.

---

## Phase 5: Git-Based Fetchers (Placeholders)

Two specs exist as placeholders with "Status: TBD":
- `packages/git-track-release-detection.md`
- `packages/git-product-release-detection.md`

These will eventually define fetchers. When specified, they must follow
the conventions established in Phase 1. No action needed now, but note
that they will be "fetcher IS the feature" cases (dedicated specs).

---

## Phase 6: Run Reviewers

After all changes from Phases 1-4 are applied, invoke the following
reviewers:

1. **`@docs-placement-reviewer`** — verify that the new pointer in
   `docs/conventions.md` and the new section in `fetcher-infrastructure.md`
   are correctly placed without duplication
2. **`@spec-coherence-reviewer`** — run once per modified spec to verify
   no contradictions were introduced by consolidation (especially for
   `cve-tracking.md`, `cvss-scoring.md`, `ticket-references.md`,
   `fetcher-operations.md`, `fetcher-infrastructure.md`)
3. **`@docs-reviewer`** — verify documentation completeness after the
   consolidation changes
4. **`@fetcher-compliance-reviewer`** — run against each fetcher that was
   modified to verify the new documentation meets infrastructure
   requirements

---

## Phase 7: Delete This Draft

Once all phases are complete and reviewers have passed, delete this file:

```
rm docs/drafts/fetcher-documentation-refactoring.md
```

Update `docs/features/README.md` if any spec was renamed or moved (not
expected based on current plan — all changes are content changes within
existing files or new sections added to existing files).

---

## Execution Notes

All phases are designed to be executed sequentially in a single session.

**Ordering constraints**:
- Phase 1 must complete before Phases 2-4 (the convention must exist
  before auditing against it)
- Within Phase 1: 1.1 and 1.2 can be applied in parallel (different
  files), but 1.4 depends on 1.2 being applied first (same file)
- Phase 1.3 is independent (different file: `AGENTS.md`)
- Phase 2 consolidations must use write-then-reference order: first write
  the consolidated section in the target spec, then replace content in
  source specs with cross-references
- Phase 2.1, 2.2, and 2.3 are independent (different specs)
- Phase 3 has no dependency on Phase 2 (different fetchers, different
  specs)
- Phase 3 items are independent of each other
- Phase 4 runs after Phases 2 and 3 (audits the result)
- Phase 5 is informational only (no changes)
- Phase 6 runs after all content changes are complete
- Phase 7 is the final step

---

## Known Issues (Out of Scope)

The following pre-existing issues were identified during review of this
draft. They are NOT caused by this refactoring and should be tracked
and resolved separately.

### Eligibility Score Resolution contradiction in `cvss-scoring.md`

The "Eligibility Score Resolution" section (lines 69-91) states that
eligibility uses **only** the SUSE assessment of the configured default
CVSS version, with no fallback to other providers. However, the
"Eligibility Threshold" section (lines 276-290) defines a cascade that
includes external providers at step 2 ("Highest score among all providers
for the default version"). These two sections contradict each other.

This must be resolved in a dedicated session working on `cvss-scoring.md`,
not as part of this refactoring.
