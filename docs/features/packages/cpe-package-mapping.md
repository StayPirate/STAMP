# CPE-to-Package Mapping

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

An earlier design proposed a `BaseFetcher` subclass syncing a
`CPEPackageMapping` database table from the AIMAAS `cpe-map` endpoint.
Investigation revealed that the AIMAAS data is manually curated by SUSE
security engineers and updated infrequently. A daily fetcher, a dedicated
database table, and a runtime dependency on AIMAAS are unnecessary for a
dataset that changes at most a few times per year.

This spec uses a static mapping file committed to the repository. The
tradeoff is explicit:

| Aspect | Fetcher approach | Static file approach |
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
3. Replace escaped colons (`\:`) with a placeholder character (e.g.,
   `\x00`) to protect them from the field split
4. Split on `:` (simple split on unescaped colons)
5. Restore escaped colons: replace the placeholder back to `:` in each
   field value
6. Extract fields by index:
   - Index 0: `part` (e.g., `a` for application)
   - Index 1: `vendor` (e.g., `apache`)
   - Index 2: `product` (e.g., `commons_compress`)
7. Form the lookup key: `vendor:product` (e.g.,
   `apache:commons_compress`). The function extracts only `vendor` and
   `product`, discarding all other fields (part, version, update, etc.)

**Example** (standard):

```
Input:  cpe:2.3:a:apache:commons_compress:1.21:*:*:*:*:*:*:*
Strip:  a:apache:commons_compress:1.21:*:*:*:*:*:*:*
Split:  [a, apache, commons_compress, 1.21, *, *, *, *, *, *, *]
Key:    apache:commons_compress
```

**Example** (escaped colon):

```
Input:    cpe:2.3:a:foo\:bar:product:*:*:*:*:*:*:*:*
Strip:    a:foo\:bar:product:*:*:*:*:*:*:*:*
Replace:  a:foo\x00bar:product:*:*:*:*:*:*:*:*
Split:    [a, foo\x00bar, product, *, *, *, *, *, *, *, *]
Restore:  [a, foo:bar, product, *, *, *, *, *, *, *, *]
Key:      foo:bar:product
```

**Escaped colons**: the CPE 2.3 specification allows escaped colons
(`\:`) in field values. In practice, escaped colons in vendor or product
names are extremely rare in NVD data and absent from the SUSE mapping
dataset. Nevertheless, the parser MUST handle them correctly using the
replace-split-restore pattern described above. A simple `str.split(":")`
without escaped-colon handling would produce silent wrong lookups -- the
vendor and product fields would be misaligned, the lookup key would not
match any mapping entry, and the raw-product fallback would return a
fragment of the original field. Silent wrong lookups are unacceptable in
a security-critical mapping.

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
from the mapping dict loaded lazily on first call.

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

**Loading**: the mapping dict is loaded lazily on first call to
`resolve_cpe_packages()`, cached for subsequent calls (e.g., via
`functools.lru_cache(maxsize=1)` on the internal loader function).
The file path is `backend/app/data/cpe-package-mapping.json`. If the
JSON file is missing or malformed, the loader raises an error
immediately.

To preserve the fail-fast property at application startup, the FastAPI
`lifespan` event MUST call `resolve_cpe_packages()` once with a dummy
CPE string (e.g., `cpe:2.3:a:test:test:*:*:*:*:*:*:*:*`). This
ensures a broken mapping file is detected at boot time -- before any
CVE ingestion occurs -- rather than silently failing hours later when
the first NVD sync runs. The lazy-init pattern avoids coupling all
modules that transitively import `cpe_mapping` to the existence of
the JSON file, improving test ergonomics (tests that don't exercise
CPE resolution can import the module freely without requiring the
data file).

**Location**: `backend/app/services/cpe_mapping.py`

### Consumers

| Consumer | Where | How |
|----------|-------|-----|
| CVE ingestion pipeline (Phase 2) | `cve_service` | For each CPE entry in the NVD ingestion payload (`CVEIngestPayload.cpe_matches`), call `resolve_cpe_packages(cpe_criteria)` and collect all returned package names into a single set. Then call `add_package_to_ticket()` once per unique package name. The set-level deduplication avoids redundant SMELT queries when multiple CPE entries resolve to overlapping packages (e.g., `rust-lang:rust` and `rust-lang:cargo` both mapping to `cargo`) |
| `fetch_single_cve` (on-demand) | `cve_service` | Same as above, triggered by on-demand CVE fetch |

**Integration notes**:

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
