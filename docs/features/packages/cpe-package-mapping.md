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
3. Check if the remaining string contains `\:` (escaped colon). If yes,
   log a warning and skip the CPE entirely (return empty set). Escaped
   colons in vendor/product fields would produce ambiguous lookup keys
   that cannot be reliably matched against the mapping
4. Split on `:` (simple split)
5. Extract fields by index:
   - Index 0: `part` (e.g., `a` for application)
   - Index 1: `vendor` (e.g., `apache`)
   - Index 2: `product` (e.g., `commons_compress`)
6. Normalize `vendor` and `product` to lowercase (e.g.,
   `Apache` → `apache`, `Commons_Compress` → `commons_compress`). NVD
   CPE strings are nominally lowercase, but normalization ensures correct
   lookups regardless of source casing
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

**Example** (escaped colon — skipped):

```
Input:  cpe:2.3:a:foo\:bar:product:*:*:*:*:*:*:*:*
Strip:  a:foo\:bar:product:*:*:*:*:*:*:*:*
Result: contains '\:', log warning, return empty set (no lookup)
```

**Escaped colons**: the CPE 2.3 specification allows escaped colons
(`\:`) in field values. Escaped colons in vendor or product names are
NOT supported in the mapping. If a CPE string contains `\:` (after
prefix stripping), the parser logs a warning and skips lookups for that
CPE entirely. This is acceptable because escaped colons are absent from
the SUSE dataset and extremely rare in NVD data. Attempting to parse
them would produce ambiguous lookup keys (e.g., `foo:bar:product` is
indistinguishable from vendor `foo` + product `bar:product`), making
correct mapping impossible without a more complex key format.

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
`resolve_cpe_packages()`, cached for subsequent calls via
`functools.lru_cache(maxsize=1)` on the internal loader function.
The file path is `backend/app/data/cpe-package-mapping.json`.

The lazy-init pattern avoids coupling all modules that transitively
import `cpe_mapping` to the existence of the JSON file, improving
test ergonomics (tests that don't exercise CPE resolution can import
the module freely without requiring the data file).

**Runtime validation contract**:

Package resolution is a best-effort mechanism — the platform functions
correctly without a CPE mapping (tickets are created normally, VAs can
add packages manually). This informs the loader's error handling:
absence or emptiness of the file is a degraded-but-operational state,
not a fatal error.

**Exception**: `CPEMappingLoadError` — a `RuntimeError` subclass
defined beside the loader in `backend/app/services/cpe_mapping.py`.
Message format: `CPE mapping load failed at {path}: {reason}`.
`{reason}` identifies the failed rule and, for structural failures,
the offending key or zero-based array index; it never includes file
contents or package values.

**Loader behavior by file state**:

| Condition | Behavior | Rationale |
|-----------|----------|-----------|
| File does not exist | Log WARNING `cpe_mapping_absent`; return empty dict | Best-effort degradation — resolution disabled, platform operational |
| File exists, zero bytes or whitespace-only | Log WARNING `cpe_mapping_absent`; return empty dict | Equivalent to absent — no meaningful content to parse |
| File exists, content is `{}` (empty JSON object) | Log WARNING `cpe_mapping_empty`; return empty dict | Explicit empty mapping — resolution disabled, platform operational |
| File exists, non-empty, structurally valid | Return populated dict | Normal operation |
| File exists, non-empty, structurally invalid | Raise `CPEMappingLoadError` | Corrupted file = deployment bug; partial/wrong mappings are worse than no mappings |

"Non-empty" for the purpose of validation means: the file contains at
least one non-whitespace character AND the content is not the empty
JSON object `{}`. Files that are zero bytes, whitespace-only, or
contain exactly `{}` are treated as graceful-degradation cases (no
validation rules applied, no error raised).

**Validation rules** (applied only when file exists and is non-empty;
checked in order, first failure raises `CPEMappingLoadError`):

1. The file is readable and valid UTF-8.
2. The content is syntactically valid JSON.
3. The root value is an object (not array, string, null, etc.).
4. No duplicate keys exist. The loader MUST use a pair-preserving
   decoder hook because a standard dict silently retains only the last
   duplicate.
5. Every key is lowercase with exactly one literal `:` separating
   non-empty `vendor` and `product` components. Key and both components
   must equal their whitespace-trimmed forms. Components MUST NOT
   contain internal whitespace (only `[a-z0-9._-]` characters are
   permitted) — keys with spaces would never be matched by the
   resolution functions, which produce underscore-separated keys from
   CPE data.
6. Every value is a non-empty array of strings. Every string is
   non-empty after trimming and must equal its trimmed form.

**Not enforced at runtime** (CI-only): alphabetical key ordering.
Unsorted but otherwise valid JSON loads successfully.

**Cache behavior**: the loader uses `@lru_cache(maxsize=1)` and
returns `dict[str, tuple[str, ...]]` (immutable tuples for package
lists). Both successful loads (populated or empty dict) are cached. A
load that raises `CPEMappingLoadError` stores no entry — the next
call retries a full read and revalidation. After one successful load,
subsequent calls return the cached mapping without file I/O. There is
no hot-reload or cache invalidation; a mapping update requires a new
deployment and process restart.

File I/O errors (`PermissionError`, `IsADirectoryError`, and any
other `OSError` subclass raised during file access) are wrapped in
`CPEMappingLoadError` with the underlying OS error as `{reason}`.
These indicate deployment/mount issues that prevent determining file
state.

Non-`OSError` exceptions (`MemoryError`, `KeyboardInterrupt`, etc.)
propagate unchanged — they are not wrapped in `CPEMappingLoadError`.

**Worker startup guard**: Celery workers validate the CPE mapping at
process startup via `check_cpe_mapping()` in the unified
`celeryd_after_setup` handler, before accepting any task. This
ensures a corrupted mapping file is detected at boot time — not hours
later when the first ingestion task runs. See
`docs/features/platform/fetcher-infrastructure.md` (Worker Startup
Handler) for the full handler contract.

The API server, Beat, and IBS consumer do not validate the CPE
mapping — they never call `resolve_cpe_packages()` or
`resolve_vendor_product()`.

**`check_cpe_mapping()`**:

- **Location**: `backend/app/core/startup_checks.py`
- **Signature**: `def check_cpe_mapping() -> None`
- **Behavior**: invokes `resolve_cpe_packages()` with the fixed dummy
  CPE `cpe:2.3:a:test:test:*:*:*:*:*:*:*:*`. The loader handles all
  three cases (valid file, absent/empty file, invalid file) internally.
  On success (including file-absent or file-empty), the `lru_cache` is
  warmed for subsequent use by worker tasks. If loading raises
  `CPEMappingLoadError`, propagates it to the caller.
- **Side effects**: cache warming only. No I/O beyond what the loader
  performs, no audit events.
- **Exceptions**: propagates `CPEMappingLoadError` from the loader.
  Unexpected exceptions from the loader propagate unchanged.

**Operational semantics**: the mapping file is treated as source code —
committed to the repository, versioned, and reviewed via normal code
review. It is loaded exactly once per process at first use and remains
immutable in memory for the entire process lifetime (read-once-per-process).
Modifications to the mapping require a commit and a new deployment to
reach production. There is no hot-reload or runtime cache invalidation
mechanism. After a deployment with an updated mapping, Celery workers (the only
consumers) start fresh with the new version. The API server, Beat,
and IBS consumer do not load the mapping.

**Mapping file key format**: all keys in the JSON mapping file MUST be
lowercase (`vendor:product`). The lowercase normalization step in the
parser (step 6) guarantees correct lookups regardless of input casing,
and the CI pipeline validates that no uppercase characters exist in
mapping keys.

**Location**: `backend/app/services/cpe_mapping.py`

### `resolve_vendor_product()`

Converts a free-text vendor/product pair (from CNA/ADP `affected[]`
arrays) into a set of SUSE source package names. Uses the same mapping
dict as `resolve_cpe_packages()`, bypassing CPE 2.3 string parsing.

**Signature**:

```python
def resolve_vendor_product(vendor: str, product: str) -> set[str]:
```

Note: the function is **synchronous** (no `async`, no database session).
It can be called from any context without performance concerns.

**Algorithm**:

1. **Normalize vendor**: strip whitespace, lowercase, replace spaces with
   underscores (e.g., `"Apache Software Foundation"` →
   `"apache_software_foundation"`)
2. **Normalize product**: strip whitespace, lowercase, replace spaces
   with underscores (e.g., `"Commons Compress"` → `"commons_compress"`)
3. **Form lookup key**: `vendor:product` (e.g.,
   `"apache_software_foundation:commons_compress"`)
4. **Lookup**: check the in-memory mapping dict for the key
5. **Return**: if found, return the set of mapped SUSE package names.
   If not found, return `{product}` (the normalized product name as
   fallback)

**Normalization rationale**: CNA/ADP-provided vendor and product strings
are free-text with no enforced format. CPE 2.3 uses lowercase with
underscores for multi-word values. The normalization applied here
matches the CPE convention: `resolve_cpe_packages()` applies the same
lowercase normalization (step 6 of CPE String Parsing) to the extracted
vendor:product pair. Both functions produce identical lookup keys for
equivalent inputs — e.g., a CNA providing `vendor = "Apache"`,
`product = "commons_compress"` resolves to the same key as a CPE string
`cpe:2.3:a:apache:commons_compress:*:...`.

**Fallback behavior**: identical to `resolve_cpe_packages()` — when no
mapping exists, returns the normalized product name as a single-element
set. The subsequent `add_package_to_ticket()` call validates against
SMELT. No phantom data is created.

**Match rate expectations**: this is a best-effort mechanism. Many
CNA-provided vendor/product values use marketing names that differ from
CPE-normalized identifiers (e.g., `"Google"` / `"Chrome"` vs CPE
`"google"` / `"chrome"` — matches; but `"The Apache Foundation"` /
`"HTTP Server"` vs CPE `"apache"` / `"http_server"` — does not match).
Even a partial match rate provides value by reducing manual VA work.

**Loading**: shares the same lazily-loaded mapping dict as
`resolve_cpe_packages()`. No additional file reads or initialization.

**Location**: `backend/app/services/cpe_mapping.py` (same module as
`resolve_cpe_packages()`)

### Consumers

| Consumer | Where | How |
|----------|-------|-----|
| CVE ingestion pipeline — NVD CPE (Phase 2) | `cve_service` | For each CPE entry in the NVD ingestion payload (`CVEIngestPayload.cpe_matches`), call `resolve_cpe_packages(cpe_criteria)` and collect all returned package names into a single set |
| CVE ingestion pipeline — affected[] CPE (Phase 2) | `cve_service` | For each `AffectedVersionEntry` with a non-null `cpe` field (from `CVEIngestPayload.affected_versions`), call `resolve_cpe_packages(cpe)` and add results to the same package set |
| CVE ingestion pipeline — affected[] vendor:product (Phase 2) | `cve_service` | For each `AffectedVersionEntry` with non-null `vendor` and `product` (from `CVEIngestPayload.affected_versions`), call `resolve_vendor_product(vendor, product)` and add results to the same package set |
| CVE ingestion pipeline — resolved_packages (Phase 2) | `cve_service` | Pre-resolved package names from the payload (`CVEIngestPayload.resolved_packages`) are added directly to the package set without mapping resolution |
| `fetch_single_cve` (on-demand) | `cve_service` | Same as above (all applicable sources from the payload), triggered by on-demand CVE fetch |

All sources contribute to a single `set[str]` of package names.
`add_package_to_ticket()` is called once per unique package name in the
set. The set-level deduplication avoids redundant SMELT queries when
multiple sources resolve to overlapping packages (e.g., NVD CPE and
CNA vendor:product both resolving to `emacs`).

**Integration notes**:

- **CPE vulnerable flag**: the resolution function is agnostic to the
  `vulnerable` field in NVD CPE match data. All CPE entries are
  resolved to package names regardless of the `vulnerable` boolean.
  The VA determines affectedness at the track level after packages are
  added to the ticket
- **Version data is informational only**: version ranges from
  `CVEAffectedVersion` records are stored and displayed to VAs but
  are NOT used for package resolution or affectedness determination.
  SUSE backport practices make upstream version information unreliable
  for determining whether a specific track is affected. The VA
  determines affectedness at the track level after packages are added
- **Mapping changes vs existing CVEs**: when the mapping file is
  updated (new entries added or existing ones modified), tickets that
  were previously processed with the old mapping are **not**
  automatically re-resolved. The new mapping applies only to CVEs
  processed after the deployment. This is accepted eventual-consistency
  behavior -- the mapping rarely changes, and VAs can manually add
  packages to tickets when needed

## Security

- **No external I/O**: the resolution function reads from an in-memory
  dict loaded lazily at first use (in worker processes). No network
  calls, no database queries
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
  Effects (consumer of `resolve_cpe_packages()` and
  `resolve_vendor_product()`)
- `docs/features/tickets/cve-tracking.md` -- Business Rule #5
  (package resolution from CVE data)
- `docs/features/packages/package-model.md` -- Adding Packages to a
  Ticket (`add_package_to_ticket()`)
- `docs/features/packages/package-service.md` --
  `add_package_to_ticket()` function specification
- `docs/api-spec.md` -- global API conventions (envelope format, error
  codes, pagination)
