# CPE-to-Package Mapping

## Status

**Draft** -- not yet approved for implementation.

**Supersedes**: `docs/drafts/cpe-package-mapping.md`. When this spec is
promoted to `docs/features/packages/cpe-package-mapping.md`, both draft
files MUST be deleted.

## Summary

Sentinel needs to convert NVD CPE strings (e.g.,
`cpe:2.3:a:gnu:emacs:*:*:*:*:*:*:*:*`) into SUSE source package names
(e.g., `emacs`) to automatically add affected packages to tickets during
CVE ingestion. This spec defines:

1. A static JSON mapping file shipped with the application at
   `backend/app/data/cpe-package-mapping.json`
2. A resolution function (`resolve_cpe_packages`) that performs an
   in-memory lookup against the mapping, consumed by the CVE ingestion
   pipeline

## Context

The previous draft (`docs/drafts/cpe-package-mapping.md`) proposed a
`BaseFetcher` subclass syncing a `CPEPackageMapping` database table from
the AIMAAS `cpe-map` endpoint. Investigation revealed that the AIMAAS
data is manually curated by SUSE security engineers and updated
infrequently. A daily fetcher, a dedicated database table, and a runtime
dependency on AIMAAS are unnecessary for a dataset that changes at most
a few times per year.

This revision replaces the fetcher-based approach with a static mapping
file committed to the repository. The tradeoff is explicit:

| Aspect | Fetcher (v1) | Static file (v2) |
|--------|-------------|-------------------|
| Update mechanism | Automatic daily sync | Manual file edit + deploy |
| External dependency | AIMAAS at runtime | None |
| DB table + migration | Yes | No |
| Risk of empty/truncated data | Yes (requires safety guards) | No |
| Mapping precision | `product`-only key (~222 collisions) | `vendor:product` key (no collisions) |
| Complexity | BaseFetcher + error handling + metrics | One JSON file + dict lookup |

The static file approach is appropriate because:

- The mapping data changes infrequently (a few times per year at most)
- Updates are always a manual curation activity regardless of source
- The dataset is small (~2,500 entries, <200 KB)
- Eliminating runtime AIMAAS dependency reduces failure modes
- The file is version-controlled, providing full change history via git

This revision also eliminates the `CVECPEMatch` database table. The
previous ingestion pipeline design persisted CPE match data from NVD
into `CVECPEMatch` rows and used the diff between stored and incoming
rows to avoid redundant SMELT queries. That mechanism required
delete-and-reinsert logic, an ordering constraint (diff must run before
reinsert), and a crash recovery fallback. Analysis showed this
complexity is disproportionate to the benefit: the active-ticket filter
already limits CPE resolution to active tickets (`New`, `Analysis`,
`Analyzed`), and a `TicketPackage` existence check covers the common
case (package already added). The remaining SMELT no-match retries
(package not maintained in any codestream) are naturally bounded --
they stop when the ticket transitions to an inactive status, when the
CVE stops being returned by NVD, or when the VA manually resolves the
situation. `CVECPEMatch` served no other purpose: the data was never
exposed in any API response or UI surface.

## Static Mapping File

### Location

`backend/app/data/cpe-package-mapping.json`

### Format

A JSON object where each key is a `vendor:product` pair (extracted from
a CPE 2.3 string) and each value is an array of SUSE source package
names:

```json
{
  "apache:commons_compress": ["apache-commons-compress"],
  "gnu:emacs": ["emacs"],
  "alsa-project:alsa": ["alsa", "python-alsa", "python3-alsa"],
  "rust-lang:rust": ["cargo", "rust", "rust1.84", "..."]
}
```

Key characteristics:

- **Key format**: `vendor:product` -- both components extracted from the
  CPE 2.3 string. Using both vendor and product avoids ambiguity when
  the same product name appears under different vendors (222 such
  collisions exist in the dataset)
- **1:N mapping**: a single CPE key can map to multiple SUSE packages
  (~8% of entries). Example: `rust-lang:rust` maps to 45 packages
- **Sorted**: entries are sorted alphabetically by key for readability
  and merge-friendly diffs
- **No soft-delete**: if a mapping is no longer relevant, the entry is
  removed from the file entirely

### Statistics

| Metric | Value |
|--------|-------|
| Total entries (vendor:product keys) | ~2,450 |
| Total package mappings (flattened) | ~2,800 |
| 1:N entries (multiple packages) | ~190 |
| Max packages per key | 45 (`rust-lang:rust`) |
| File size | <200 KB |

### Maintenance

The mapping file is maintained manually. When a new CPE product needs
to be mapped to a SUSE source package (e.g., a new upstream project
starts being tracked by SUSE), a developer or security engineer adds
the entry to the JSON file and deploys the update.

The file can be updated from AIMAAS as a one-time bulk operation if
needed, but there is no automated sync. The canonical source of truth
is the committed file, not AIMAAS.

**Operational workflow for mapping changes**: when a mapping entry is
added or modified, the person making the change SHOULD:

1. Identify active tickets whose CVEs include the affected
   `vendor:product` CPE entry (search via the NVD API or the ticket
   list filtered by CVE)
2. Manually add the newly mapped package(s) to any relevant tickets
   via `add_package_to_ticket()`
3. Document the affected CVEs in the commit message for traceability

This manual step is necessary because existing tickets are not
automatically re-resolved when the mapping changes (see Integration
notes, "Mapping changes vs existing CVEs").

**CI validation**: a CI check validates the mapping file on every
change:

- Valid JSON syntax
- Keys sorted alphabetically
- No duplicate keys
- Every value is a non-empty array of strings (no empty arrays, no
  non-string elements, no empty strings)

This prevents manual editing errors from reaching deployment. An empty
array value would suppress the raw-product fallback (the key exists,
so the function returns the mapped value instead of falling back),
effectively making the CPE entry unmappable -- the CI check rejects
this case.

## CPE String Parsing

Both the resolution function and any future consumer that needs to
extract fields from a CPE string use the same parsing algorithm.

**Supported format**: CPE 2.3 only. NVD API v2 uses CPE 2.3
exclusively. CPE 2.2 strings (`cpe:/...`) are treated as parse errors
(logged and skipped) because the encoding rules differ (URI
percent-encoding vs. backslash escaping) and the current dataset
contains no CPE 2.2 entries. If a future data source provides CPE 2.2
strings, the parser must be extended with proper encoding handling at
that time.

**Algorithm**:

1. Verify the `cpe:2.3:` prefix. If absent, treat as a parse error
2. Strip the prefix
3. Split on `:` (simple split)
4. Extract fields by index:
   - Index 0: `part` (e.g., `a` for application)
   - Index 1: `vendor` (e.g., `apache`)
   - Index 2: `product` (e.g., `commons_compress`)
5. Form the lookup key: `vendor:product` (e.g.,
   `apache:commons_compress`). The function extracts only `vendor` and
   `product`, discarding all other fields (part, version, update, etc.)

**Example**:

```
Input:  cpe:2.3:a:apache:commons_compress:1.21:*:*:*:*:*:*:*
Strip:  a:apache:commons_compress:1.21:*:*:*:*:*:*:*
Split:  [a, apache, commons_compress, 1.21, *, *, *, *, *, *, *]
Key:    apache:commons_compress
```

**Escaped colons**: the CPE 2.3 specification allows escaped colons
(`\:`) in field values. In practice, escaped colons in vendor or product
names are extremely rare in NVD data and absent from the SUSE mapping
dataset. The implementation uses a simple `str.split(":")`, which is
sufficient for the current dataset. If a CPE string contains escaped
colons in the vendor or product field, the simple split produces
incorrect field boundaries -- the resulting lookup key will not match
any mapping entry, and the function falls through to the raw-product
fallback, which will also be incorrect (a fragment of the original
field). This is a **known limitation**: the implementation accepts
silent wrong lookups for escaped-colon CPEs as a tradeoff for parser
simplicity. If escaped-colon CPEs become relevant in the future (new
vendor/product names in NVD), the parser must be upgraded to handle
`\:` before splitting.

If a CPE string cannot be parsed by this algorithm (fewer
than 3 fields after splitting), it is treated as a parse error.

**Parse errors**: when a CPE string is malformed (unrecognized prefix
or fewer than 3 fields after stripping and splitting), the resolution
function logs a warning and returns an empty set. It does not raise
an exception -- the caller skips the unparseable CPE and continues
processing.

## Resolution Function

### `resolve_cpe_packages()`

Converts a CPE 2.3 string into a set of SUSE source package names.
This function is a **pure in-memory lookup** with no I/O -- it reads
from the mapping dict loaded at module import time.

**Signature**:

```python
def resolve_cpe_packages(cpe_criteria: str) -> set[str]:
```

Note: the function is **synchronous** (no `async`, no database session).
It can be called from any context without performance concerns.

**Algorithm**:

1. **Parse**: extract the `vendor:product` lookup key from the CPE
   string using the CPE String Parsing algorithm (see above)
2. **Lookup**: check the in-memory mapping dict for the key
3. **Return**: if found, return the set of mapped SUSE package names.
   If not found, return `{product}` (the raw CPE product name as
   fallback)

**Fallback behavior**: when no mapping exists for a `vendor:product`
key, the function returns the raw CPE product name as a single-element
set. This optimistic fallback is consistent with SMASH (the predecessor
system) and works correctly when the CPE product name happens to match
the SUSE package name (e.g., `emacs`). When the names differ (e.g., CPE
`linux_kernel` vs SUSE `kernel-source`), the subsequent
`add_package_to_ticket()` call queries SMELT, which returns zero
results, and the package is not added (see `package-service.md`,
`add_package_to_ticket()`, `PACKAGE_NOT_FOUND_IN_SMELT` error). No
phantom data is created.

**Loading**: the mapping dict is loaded once at module import time from
`backend/app/data/cpe-package-mapping.json`. If the JSON file is missing
or malformed, the import fails immediately with a clear error --
fail-fast at application startup rather than silent runtime failures.

**Location**: `backend/app/services/cpe_mapping.py`

### Consumers

| Consumer | Where | How |
|----------|-------|-----|
| CVE ingestion pipeline (Phase 2) | `cve_service` | For each CPE entry in the NVD ingestion payload (`CVEIngestPayload.cpe_matches`), call `resolve_cpe_packages(cpe_criteria)` and collect all returned package names into a single set. Then call `add_package_to_ticket()` once per unique package name. The set-level deduplication avoids redundant SMELT queries when multiple CPE entries resolve to overlapping packages (e.g., `rust-lang:rust` and `rust-lang:cargo` both mapping to `cargo`) |
| `fetch_single_cve` (on-demand) | `cve_service` | Same as above, triggered by on-demand CVE fetch |

**Integration notes** (the first three notes describe consumer-side
behavior that will migrate to `cve-service.md` during promotion --
see Implementation Plan, step 1.2. Post-promotion, this section
retains only the "CPE vulnerable flag" and "Mapping changes vs
existing CVEs" notes):

- **Active-ticket filter**: the Phase 2 task MUST check whether the
  ticket is active (status in `New`, `Analysis`, or `Analyzed` with
  `deleted_at IS NULL`) **before** calling `resolve_cpe_packages()`.
  If the ticket is inactive, the task skips the entire resolution
  flow. This avoids unnecessary lookups and SMELT queries for tickets
  that cannot accept new packages. The active-ticket check is specified
  in `cve-service.md` (Post-Ingestion Side Effects, active-ticket
  filter); this spec does not redefine it
- **Deduplication via TicketPackage existence**: the Phase 2 task
  checks whether a `TicketPackage` record already exists for the
  resolved package name on the ticket before calling
  `add_package_to_ticket()`. If the record exists (active or
  soft-deleted), the SMELT call is skipped. CPE data is not persisted
  to a database table -- it is consumed directly from the NVD
  ingestion payload and discarded after resolution
- **Crash recovery**: if the worker crashes between Phase 1 commit
  (CVE record saved) and Phase 2 completion (package resolution), the
  CPE data in the task arguments is lost. Recovery is natural: the
  next NVD sync delivers the same CPE data in the payload and
  re-triggers resolution. The `TicketPackage` existence check ensures
  idempotency -- packages successfully added before the crash are
  skipped, and only the remaining ones trigger SMELT queries. This
  applies equally to 1:N mappings (e.g., `rust-lang:rust` with 45
  packages): if the worker crashes after adding 20 of 45 packages,
  the next sync re-resolves all 45 names, skips the 20 that already
  have `TicketPackage` records, and processes only the remaining 25
- **SMELT no-match retry**: when SMELT returns zero tracks for a
  mapped package, no `TicketPackage` is created, and subsequent syncs
  that return the same CVE will re-trigger the SMELT call. This retry
  is naturally bounded: it stops when the ticket transitions to an
  inactive status (active-ticket filter), when the CVE stops being
  returned by NVD, or when the VA manually resolves the situation. The
  cost is negligible -- the combination of an active ticket with a CPE
  mapping to a package not maintained in SMELT is rare and transient
- **CPE vulnerable flag**: the resolution function is agnostic to the
  `vulnerable` field in NVD CPE match data. All CPE entries are
  resolved to package names regardless of the `vulnerable` boolean.
  The VA determines affectedness at the track level after packages are
  added to the ticket
- **Mapping changes vs existing CVEs**: when the mapping file is
  updated (new entries added or existing ones modified), tickets that
  were previously processed with the old mapping are **not**
  automatically re-resolved. The new mapping applies only to CVEs
  processed after the deployment. This is accepted eventual-consistency
  behavior -- the mapping rarely changes, and VAs can manually add
  packages to tickets when needed

## Security

- **No external I/O**: the resolution function reads from an in-memory
  dict loaded at startup. No network calls, no database queries
- **Input validation**: CPE strings are validated during parsing. Strings
  that cannot be parsed are logged and skipped, never passed through
  to downstream operations
- **No user-controlled input**: the mapping data is committed to the
  repository and reviewed via normal code review. The resolution
  function receives CPE strings from the NVD ingestion payload
  (`CVEIngestPayload`), not from user input
- **No secrets**: no credentials, tokens, or API keys are involved

## Cross-references

- `docs/features/tickets/cve-service.md` -- Post-Ingestion Side
  Effects (consumer of `resolve_cpe_packages()`)
- `docs/features/tickets/cve-tracking.md` -- Business Rule #5
  (CPE-based package resolution)
- `docs/features/packages/package-model.md` -- Adding Packages to a
  Ticket (`add_package_to_ticket()`)
- `docs/features/packages/package-service.md` --
  `add_package_to_ticket()` function specification
- `docs/api-spec.md` -- General Conventions

---

## Implementation Plan

This section lists all changes required to implement this feature,
in dependency order. Each step references the target file and describes
the change. This section is removed when the spec is promoted.

### Phase 1 -- Specification and documentation

#### 1.1. Promote this draft to `docs/features/packages/cpe-package-mapping.md`

Move this file from `docs/drafts/` to its final location. Remove the
"Status: Draft" header, the "Supersedes" note, and the "Implementation
Plan" section.

Delete the old draft: `docs/drafts/cpe-package-mapping.md`.

#### 1.2. Update `docs/features/tickets/cve-service.md` -- CPE mapping step and CVECPEMatch removal

This is the largest cross-reference update. The changes are:

**a)** In "Post-Ingestion Side Effects", update points 2 and 6 to
reference `resolve_cpe_packages()` from
`docs/features/packages/cpe-package-mapping.md`. Replace the vague
"map to source packages" with a concrete cross-reference. Add a
consumer-oriented summary (3-5 sentences) per the single-source-of-truth
rule.

**b)** Remove the "Diff detection guard" section: no longer needed.
Replace with the simpler `TicketPackage` existence check described in
this spec (Consumers, Integration notes). The deduplication logic is:
check `TicketPackage` existence before calling `add_package_to_ticket()`;
if present (active or soft-deleted), skip the SMELT call.

**c)** Remove the "Ordering constraint": no longer applicable (no
delete-and-reinsert of `CVECPEMatch` rows).

**d)** Simplify the "Crash recovery" section: without `CVECPEMatch`
persistence, crash recovery is natural -- the next NVD sync provides the
same CPE data in the payload and triggers resolution. The `CVECPEMatch`
fallback mechanism is no longer needed.

**e)** Update Phase 2 task description: CPE data is passed to the async
task from the NVD payload (as task arguments), not queried from a
database table.

**f)** Update side effect #6: CPE match entries from the NVD payload are
resolved inline via `resolve_cpe_packages()` and passed to
`add_package_to_ticket()`. They are no longer persisted to a
`CVECPEMatch` table. The `CVEIngestPayload.cpe_matches` field is
retained (the payload still carries CPE data from NVD), but no
database write occurs for CPE data.

**g)** Update "Child Table Deduplication": remove `CVECPEMatch` from the
list of delete-and-reinsert tables.

**h)** Remove the "Residual behavior -- SMELT no-match retry" section:
replaced by the simpler analysis in this spec (Integration notes,
"SMELT no-match retry").

#### 1.3. Update `docs/features/tickets/cve-tracking.md` -- Business Rule #5

Update Business Rule #5 to reference the CPE mapping spec instead of
the current vague "maps CPE entries to source package names":

```
5. When a CVE is ingested with CPE data, Sentinel resolves CPE entries
   to SUSE source package names via the CPE package mapping (see
   docs/features/packages/cpe-package-mapping.md) and calls
   `add_package_to_ticket` for each resolved package [...]
```

#### 1.4. Update `docs/system-map.md`

- Add `cpe-package-mapping` node to the Feature Specification Map
  with edges to `cve-service`, `cve-tracking`, and `package-model`
- Add a row to the Specification Index table

#### 1.5. Update `docs/features/README.md`

Add `cpe-package-mapping.md` to the packages section index.

#### 1.6. Update `docs/data-model.md` -- remove CVECPEMatch

- Remove the `CVECPEMatch` table definition (currently lines 699-747)
- Remove from the ER diagram relationships (`CVE ||--o{ CVECPEMatch`)
- Remove from the "no `updated_at`" exception list
- Remove from any cross-reference or index lists

#### 1.7. Update `docs/features/tickets/cve-tracking.md` -- remove CVECPEMatch references

- Remove `CVECPEMatch` from child data preservation on CVE rejection --
  there are no CPE rows to preserve
- Update NVD fetcher algorithm step 3f -- CPE data is extracted into
  `CVEIngestPayload.cpe_matches` for inline resolution, not persisted
  to a table

#### 1.8. Update `docs/data-sources.md` -- remove CVECPEMatch output reference

Remove `CVECPEMatch` from any fetcher output references in the data
sources catalog.

### Phase 2 -- Post-promotion

#### 2.1. Run spec reviewers

After the spec is promoted, run:

- `spec-gap-analyzer` -- uncovered functional cases
- `spec-coherence-reviewer` -- contradictions with other specs
- `design-reviewer` -- architectural decisions
- `docs-placement-reviewer` -- verify rule placement

Also re-run `spec-coherence-reviewer` on `cve-service` and
`cve-tracking` (since their cross-references change).

#### 2.2. Update cross-cutting documents

Verify and update as needed:

- `docs/architecture.md` -- CVE Ingestion Flow (step 5: update
  "attempts to map CPE data" to reference the new spec)
