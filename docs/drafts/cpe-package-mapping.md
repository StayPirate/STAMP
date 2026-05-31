# CPE-to-Package Mapping

## Status

**Draft** -- not yet approved for implementation.

## Summary

Sentinel needs to convert NVD CPE strings (e.g.,
`cpe:2.3:a:gnu:emacs:*:*:*:*:*:*:*:*`) into SUSE source package names
(e.g., `emacs`) to automatically add affected packages to tickets during
CVE ingestion. This spec defines:

1. A local `CPEPackageMapping` table storing the CPE product-to-SUSE
   package mapping
2. A `BaseFetcher` that periodically syncs this table from the AIMAAS
   `cpe-map` endpoint
3. A resolution function (`resolve_cpe_packages`) consumed by the CVE
   ingestion pipeline (Phase 2) to convert CPE strings into package names
   before calling `add_package_to_ticket()`

## Context

Currently, the CVE ingestion pipeline (described in
`docs/features/tickets/cve-service.md`, Post-Ingestion Side Effects,
point 6) stores NVD CPE applicability data in the `CVECPEMatch` table
and triggers package resolution for new CPEs. However, the conversion
from a CPE string to a SUSE source package name is unspecified -- the
spec says "map CPE entries to source package names" without defining the
algorithm.

SMASH (the existing SUSE security tool) solves this by querying the
AIMAAS `cpe-map` endpoint, which provides a curated mapping of ~1,100
CPE product names to SUSE source package names. The mapping is
maintained by SUSE security engineers in AIMAAS.

### AIMAAS cpe-map endpoint

| Property | Value |
|----------|-------|
| URL | `GET https://aimaas.suse.de/api/entity/cpe-map` |
| Auth | None required for read access |
| Pagination | `size` (page size) + `page` (1-indexed); response envelope: `{items, total, page, size, pages}` |
| Total entries | ~1,100 |
| Entry fields | `id` (int), `slug` (string), `name` (CPE 2.3 string), `packages` (string[]), `deleted` (bool) |

Example entry:

```json
{
  "id": 3329,
  "slug": "agendaless-waitress",
  "name": "cpe:2.3:a:agendaless:waitress:*:*:*:*:*:*:*:*",
  "packages": ["python-waitress", "python310-waitress", "python38-waitress", "python39-waitress"],
  "deleted": false
}
```

Key characteristics:

- A single CPE product can map to **multiple** SUSE packages (1:N
  relationship). Approximately 20% of entries are 1:N.
- The `name` field is a full CPE 2.3 URI. The mapping key is the
  `product` component (the 5th colon-separated field, index 4 in
  the full string, index 2 after stripping the `cpe:2.3:` prefix).
- The `deleted` field supports soft-deletion in AIMAAS. Sentinel
  should sync only active entries (`deleted = false`).

## Data Model

### CPEPackageMapping

Stores the mapping between CPE product names and SUSE source package
names. Each row represents one mapping pair; a CPE product with
multiple SUSE packages produces multiple rows.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PK | Internal identifier |
| cpe_product | VARCHAR(255) | NOT NULL | CPE product name (extracted from the CPE 2.3 `product` field, e.g., `emacs`, `linux_kernel`) |
| suse_package_name | VARCHAR(255) | NOT NULL | SUSE source package name (e.g., `emacs`, `kernel-source`) |
| aimaas_entity_id | INTEGER | NOT NULL | AIMAAS entity ID for traceability |
| created_at | TIMESTAMPTZ | NOT NULL, DEFAULT | Record creation timestamp |

**Unique constraint**: `UNIQUE (cpe_product, suse_package_name)`

**No `updated_at`**: records are replaced (delete-and-reinsert per full
table), never updated in place -- consistent with `CVECPEMatch` and
`ProductRepository`.

**Deduplication**: the fetcher performs a full-table replace on each sync
(delete all rows, insert all active entries from AIMAAS). This is the
correct pattern because:

- AIMAAS provides the full dataset on each sync (no delta API)
- Entries can be added, removed, or modified between syncs
- A deletion in AIMAAS is correctly reflected by the missing row

The dataset is small (~1,100 entries expanding to ~1,400 rows after
1:N flattening), making full-table replace efficient.

## Maintenance Fetcher

### Fetcher Properties

| Property | Value |
|----------|-------|
| Fetcher name | `sync_cpe_package_mapping` |
| Class name | `SyncCpePackageMapping` |
| Schedule | Daily at 05:00 UTC (`0 5 * * *`) |
| Source | AIMAAS (`aimaas.suse.de`) |
| Scope | Full `CPEPackageMapping` table (complete replace per sync) |
| Auth | None (anonymous read access) |
| Custom settings | No |

### Algorithm

1. Fetch all active entries from AIMAAS `cpe-map` endpoint. Paginate
   with `size=100` (fewer pages than SMASH's `size=50`, since we
   process the full dataset in batch). Fetch all pages by following
   the `page` counter until `page >= pages`.
2. For each entry, parse the `name` field (CPE 2.3 string) to extract
   the `product` component:
   - Strip the `cpe:` prefix and version indicator (`/` for CPE 2.2,
     `2.3:` for CPE 2.3)
   - Split on unescaped colons
   - The `product` is at index 2 (after `part` and `vendor`)
3. For each entry, flatten the `packages` array into individual
   `(cpe_product, suse_package_name, aimaas_entity_id)` tuples.
   Skip entries where `packages` is empty.
4. Within a single database transaction:
   a. Delete all existing `CPEPackageMapping` rows
   b. Bulk insert all tuples from step 3
5. Report metrics.

### Error Handling

- **AIMAAS unreachable** (connection error, HTTP 5xx): log error, report
  `record_failed(1)`, abort. The existing local data remains unchanged
  (the delete-and-reinsert transaction is never started). The fetcher
  dashboard shows the failure. Retry on next scheduled run.
- **AIMAAS returns HTTP 4xx**: log error with response body, report
  `record_failed(1)`, abort. Same behavior as above.
- **Pagination incomplete** (a page request fails mid-sweep): log error,
  abort the entire sync. Do not partially update the table. The
  complete dataset must be fetched before the transaction begins.
- **CPE parsing failure** (malformed `name` field): log warning with the
  entry's `slug` and `name`, skip the entry, increment
  `record_failed`. Continue processing remaining entries.

### Metrics

| Metric | Definition |
|--------|------------|
| `record_created` | Number of `CPEPackageMapping` rows inserted |
| `record_updated` | Always 0 (full replace, no updates) |
| `record_failed` | Number of entries skipped due to parsing errors, or 1 if the entire sync fails |

## CPE Resolution Function

### `resolve_cpe_packages()`

Converts a CPE 2.3 string into a set of SUSE source package names.
This function is a **pure lookup** with no external I/O -- it reads
only from the local `CPEPackageMapping` table.

**Signature** (conceptual):

```python
async def resolve_cpe_packages(
    db: AsyncSession,
    cpe_criteria: str,
) -> set[str]:
```

**Algorithm** (three steps, consistent with SMASH):

1. **Parse**: strip the CPE prefix (`cpe:/` or `cpe:2.3:`), split on
   unescaped colons, extract the `product` field at index 2.
2. **Lookup**: query `CPEPackageMapping` for all rows where
   `cpe_product` matches the extracted product name.
3. **Fallback**: if no rows are found, return `{product_name}` (the
   raw CPE product name as-is). This handles CPEs not yet mapped in
   AIMAAS -- the name may or may not match a SUSE package in SMELT.

**Return value**: a `set[str]` of SUSE source package names. The set
contains one or more names (1:N mapping), or the raw CPE product name
if no mapping exists.

**Location**: `backend/app/services/cpe_mapping.py` (new module). The
function is stateless and can be called from any context (API handler,
Celery task, CLI command).

### Consumers

| Consumer | Where | How |
|----------|-------|-----|
| CVE ingestion pipeline (Phase 2) | `cve_service` | For each new CPE from `CVECPEMatch`, call `resolve_cpe_packages(cpe_criteria)`, then call `add_package_to_ticket()` for each returned package name |
| `fetch_single_cve` (on-demand) | `cve_service` | Same as above, triggered by on-demand CVE fetch |

## Security

- **No authentication required**: the AIMAAS `cpe-map` endpoint is
  publicly readable within the SUSE network. No credentials need to
  be stored or managed.
- **Input validation**: CPE strings from AIMAAS are validated during
  parsing (step 2 of the algorithm). Malformed entries are skipped
  with a logged warning.
- **No user-controlled input**: the mapping data comes exclusively
  from AIMAAS (curated by SUSE security engineers). The resolution
  function receives CPE strings from `CVECPEMatch` (populated by NVD
  data), not from user input.

## Cross-references

- `docs/features/tickets/cve-service.md` -- Post-Ingestion Side
  Effects (consumer of `resolve_cpe_packages()`)
- `docs/features/tickets/cve-tracking.md` -- Business Rule #5
  (CPE-based package resolution)
- `docs/features/packages/package-model.md` -- Adding Packages to a
  Ticket (`add_package_to_ticket()`)
- `docs/features/packages/package-service.md` --
  `add_package_to_ticket()` function specification
- `docs/data-model.md` -- `CVECPEMatch` table definition
- `docs/data-sources.md` -- AIMAAS section (data source catalog)
- `docs/api-spec.md` -- General Conventions

---

## Implementation Plan

This section lists all changes required to implement this feature,
in dependency order. Each step references the target file and describes
the change.

### Phase 1 -- Specification and documentation

#### 1.1. Promote this draft to `docs/features/packages/cpe-package-mapping.md`

Move this file from `docs/drafts/` to its final location. Remove the
"Status: Draft" header and the "Implementation Plan" section (the plan
is consumed during implementation, not retained in the final spec).

#### 1.2. Update `docs/data-sources.md` -- AIMAAS section

Add the `cpe-map` endpoint to the AIMAAS bullet list:

```
- `GET /api/entity/cpe-map` (paginated) — CPE product-to-SUSE source
  package name mapping (~1,100 entries)
```

Update the "Relevant data" and "See also" fields to mention CPE
package mapping. Add a row to the Fetcher Registry table for
`sync_cpe_package_mapping`.

#### 1.3. Update `docs/data-model.md` -- add `CPEPackageMapping` table

Add the table definition in the appropriate section (near
`CVECPEMatch` or in a new "Reference Data" subsection). Include
columns, constraints, deduplication strategy, and relationship to
`CVECPEMatch` (conceptual, not FK).

Update the ER diagram to include the new table. Add a row to the
Entity Groups table.

#### 1.4. Update `docs/features/tickets/cve-service.md` -- specify CPE mapping step

In the "Post-Ingestion Side Effects" section, update points 2 and 6
to reference `resolve_cpe_packages()` from
`docs/features/packages/cpe-package-mapping.md`. Replace the vague
"map to source packages" with a concrete cross-reference:

```
map CPE entries to source package names via
`resolve_cpe_packages()` (see
docs/features/packages/cpe-package-mapping.md) and call
`package_service.add_package_to_ticket()` for each resolved name
```

Add a consumer-oriented summary (3-5 sentences) per the
single-source-of-truth rule.

#### 1.5. Update `docs/features/tickets/cve-tracking.md` -- Business Rule #5

Update Business Rule #5 to reference the CPE mapping spec instead of
the current vague "maps CPE entries to source package names":

```
5. When a CVE is ingested with CPE data, Sentinel resolves CPE entries
   to SUSE source package names via the CPE package mapping (see
   docs/features/packages/cpe-package-mapping.md) and calls
   `add_package_to_ticket` for each resolved package [...]
```

#### 1.6. Update `docs/system-map.md`

- Add `CPEPackageMapping` to the Data Model ER diagram
- Add a row to the Entity Groups table
- Add `cpe-package-mapping` node to the Feature Specification Map
  with edges to `cve-service`, `cve-tracking`, and `package-model`
- Add a row to the Specification Index table

#### 1.7. Update `docs/features/README.md`

Add `cpe-package-mapping.md` to the packages section index.

### Phase 2 -- Implementation

#### 2.1. Create SQLAlchemy model

File: `backend/app/models/cpe_package_mapping.py`

Define the `CPEPackageMapping` model with the columns specified in
the Data Model section. Register in `backend/app/models/__init__.py`.

#### 2.2. Create Alembic migration

Generate a migration for the new `CPEPackageMapping` table with the
unique constraint on `(cpe_product, suse_package_name)`.

#### 2.3. Implement the resolution function

File: `backend/app/services/cpe_mapping.py`

Implement `resolve_cpe_packages()` with the three-step algorithm
(parse, lookup, fallback).

#### 2.4. Implement the fetcher

File: `backend/app/tasks/sync_cpe_package_mapping.py`

Implement `SyncCpePackageMapping` as a `BaseFetcher` subclass with
the algorithm and error handling specified in this spec.

#### 2.5. Integrate into CVE ingestion pipeline

File: `backend/app/services/cve_service.py` (or the Phase 2 task
module)

In the Phase 2 task for CPE-based package resolution, call
`resolve_cpe_packages()` for each new CPE before calling
`add_package_to_ticket()`. For each package name returned by
the resolution function, call `add_package_to_ticket()` separately.

Update `fetch_single_cve` orchestration to use the same resolution
path.

#### 2.6. Register fetcher schedule

Add `sync_cpe_package_mapping` to the Celery Beat schedule
configuration with the specified cron (`0 5 * * *`).

### Phase 3 -- Testing

#### 3.1. Unit tests for CPE parsing and resolution

File: `backend/tests/services/test_cpe_mapping.py`

- Test CPE 2.3 string parsing (standard format, edge cases)
- Test CPE 2.2 string parsing (`cpe:/` prefix)
- Test resolution with mapping present (1:1 and 1:N)
- Test fallback when no mapping exists
- Test malformed CPE strings

#### 3.2. Unit tests for the fetcher

File: `backend/tests/tasks/test_sync_cpe_package_mapping.py`

- Test successful full sync (mock AIMAAS responses)
- Test pagination handling (multiple pages)
- Test AIMAAS unreachable (connection error)
- Test AIMAAS HTTP error (4xx, 5xx)
- Test malformed entry skipping
- Test empty packages array handling
- Test full-table replace (old data deleted, new data inserted)

#### 3.3. Integration tests for CVE pipeline

File: `backend/tests/services/test_cve_service.py` (extend existing)

- Test Phase 2 CPE resolution with mapping present
- Test Phase 2 CPE resolution with fallback (no mapping)
- Test Phase 2 CPE resolution with 1:N mapping (multiple
  `add_package_to_ticket` calls)
- Test Phase 2 with empty `CPEPackageMapping` table

### Phase 4 -- Post-implementation

#### 4.1. Run spec reviewers

After the spec is finalized (promoted from draft), run the following
reviewers on `cpe-package-mapping`:

- `spec-gap-analyzer` -- uncovered functional cases
- `spec-coherence-reviewer` -- contradictions with other specs
- `design-reviewer` -- architectural decisions
- `api-convention-reviewer` -- if any API endpoints are added
- `docs-placement-reviewer` -- verify rule placement

Also re-run `spec-coherence-reviewer` on `cve-service` and
`cve-tracking` (since their cross-references change).

#### 4.2. Delete the draft file

Remove `docs/drafts/cpe-package-mapping.md` after the spec is
promoted to `docs/features/packages/`.

#### 4.3. Update diagrams and cross-cutting documents

Verify and update as needed:

- `docs/data-model.md` -- ER diagram, Entity Groups table
- `docs/system-map.md` -- System Components, Data Model ER,
  Data Flows (Package and Release Tracking), Feature Specification
  Map, Specification Index
- `docs/architecture.md` -- CVE Ingestion Flow (step 5: update
  "attempts to map CPE data" to reference the new spec)
- `docs/configuration.md` -- if any new env vars are introduced

#### 4.4. Update review tracking

- Add `cpe-package-mapping` entry to `docs/reviews/.tracking.json`
  (enabled, cache null, not_reviewed for all sections)
- Update `docs/reviews/README.md` with the new spec row
