# Remove Callers Tables from Service Specifications

## Purpose

Remove all module-level `## Callers` sections from service-layer
specifications. These tables are hand-maintained reverse-dependency
indexes that duplicate information already owned authoritatively by
the caller specs themselves. Empirical evidence shows they drift
(50% of cve-service rows are inaccurate, and the table has generated
3 logged coherence defects), have zero inbound cross-references (no
document links readers to them), and provide value only against
implemented code (which does not exist yet).

## Background

Five service specs currently have a `## Callers` section:

| Spec | Rows | Scoping | Drift status |
|------|------|---------|-------------|
| `tickets/cve-service.md` | 12 | fine-grained per-fetcher | **50% drifted** (4 of 8 fetcher rows) |
| `tickets/ticket-service.md` | 3 | fine-grained per-caller | accurate |
| `tickets/ticket-mutations.md` | 9 | coarse per-category | accurate |
| `packages/package-service.md` | 9 | coarse per-category | accurate |
| `identity/api-key-service.md` | 5 | fine-grained per-endpoint | accurate |

One service spec (`identity/user-service.md`) has never had a Callers
table, demonstrating the pattern is already applied non-uniformly.

### Decision rationale

- **Zero consumers**: no document cross-references any Callers table.
  The only reactions to them in the review corpus are bug reports about
  their own inaccuracy.
- **Authoritative source exists elsewhere**: each caller's own spec
  (Algorithm/process_item/fetch_single sections) declares its forward
  dependencies authoritatively. Grep or IDE find-references provide
  reverse lookup in code.
- **Drift-prone by construction**: the only enforcement mechanism is
  manual memory during spec edits. The cve-service table demonstrates
  the failure mode: 4 rows drifted undetected.
- **No code yet**: in the spec phase, the impact-analysis value of a
  reverse-index is purely theoretical. When code exists, LSP/IDE
  tooling provides this natively.
- **Coherence**: removal produces uniform behavior across all 6
  service-layer specs (none has a reverse-index). Keeping some and
  removing others requires a justification for the asymmetry.

### Principle established

> Reverse dependencies are documented in the forward direction only:
> each spec declares what it calls (in Algorithm/Cross-references
> sections). No spec maintains a hand-curated reverse-dependency index.
> When code exists, reverse lookup is performed via tooling
> (find-references, grep).

---

## Open Point

### OP-A: Audit of Context-column facts before removal

**Status**: Resolved (2026-07-08)

**Question**: Do any facts in the Context columns of the 3-column
tables (`ticket-mutations.md`, `package-service.md`,
`api-key-service.md`) represent information NOT already documented at
their authoritative source? If so, those facts must be relocated
before removal.

**Conclusion**: all Context-column facts in the 3-column tables are
already documented at their authoritative sources. No orphaned facts
were found — no relocation is required before proceeding to Step 2.

**Verification of high-value facts**:

| Fact | Table | Authoritative source | Verified |
|------|-------|---------------------|----------|
| "User self-revoke (with ownership check in handler)" | api-key-service | `api-key-service.md:165-168` (Note under `revoke_key()`), `authentication.md:790` | ✓ |
| "Admin revoke (no ownership check)" | api-key-service | `api-key-service.md:170`, `authentication.md:881` | ✓ |
| "CLI revoke (`acting_user_id=None`)" | api-key-service | `authentication.md:551-554`, `api-key-service.md:49` | ✓ |
| "Deactivation side effect (`acting_user_id=None`)" | api-key-service | `user-service.md:579`, `authentication.md:273`, `api-key-service.md:40` | ✓ |
| "Celery task `recalc_active_tickets(version)`" | ticket-mutations | `system-settings.md:51,63-65` | ✓ |
| "Catch-up handled internally by `reconcile_ticket_status()` step 4" | ticket-mutations | `ticket-mutations.md:196` (self-contained in reconcile spec), `cvss-scoring.md:794` | ✓ |
| "Package-centric callers now call `package_service` directly" | ticket-mutations | `ticket-mutations.md:85` (Module Dependencies table), `package-service.md` structure | ✓ |

Remaining Context-column entries (e.g., "VA-initiated operations",
"Background CVE ingestion", "Real-time track release detection") are
generic category labels whose meaning is self-evident and not
information requiring an authoritative source.

**Blocking**: resolved — Steps 2-6 are unblocked.

---

## Action Plan

### Step 1: Resolve OP-A (audit and relocation)

Read each of the 3-column Callers tables and verify every Context-column
fact against its authoritative source. Record the result in this section
by updating the table above with a "Verified" column.

If any fact is orphaned:
- Identify the correct authoritative location (the endpoint definition,
  the function contract, or the relevant spec section)
- Add the fact there
- Record the relocation in this draft

Once all facts are verified or relocated, mark OP-A as Resolved and
proceed.

### Step 2: Remove Callers section from `docs/features/tickets/cve-service.md`

**Delete** the entire `## Callers` section (heading + table, lines
1649-1664). This includes:

```markdown
## Callers

| Caller | Operations used |
|--------|----------------|
| `sync_nvd_cves` (fetcher) | `upsert_cve()`, `build_post_ingest_tasks()`, `reference_service.upsert_references()` |
| `sync_mitre_cves` (fetcher) | `upsert_cve()` (via `process_item()`) |
| `sync_kernel_cves` (fetcher) | `upsert_cve()` (via `process_item()`) |
| `sync_redhat_cves` (fetcher) | `upsert_cve()` (via `fetch_single()`) |
| `sync_cisa_kev` (fetcher) | `upsert_cve()`, `build_post_ingest_tasks()`, `reference_service.upsert_references()` |
| `sync_epss_scores` (fetcher) | `upsert_cve()` (via `fetch_single()`) |
| `sync_ghsa_advisories` (fetcher) | `upsert_cve()`, `build_post_ingest_tasks()`, `reference_service.upsert_references()` |
| `sync_osv_advisories` (fetcher) | `upsert_cve()` (via `fetch_single()`) |
| `fetch_single_cve` (orchestrator) | `record_source_status()` (missing/failure path), `commit_and_dispatch()` (all paths) |
| API endpoint (associate-cve) | `ensure_cve_exists()`, `trigger_on_demand_fetch()` |
| API endpoint (create-ticket with CVE) | `ensure_cve_exists()`, `trigger_on_demand_fetch()` |
| API endpoint (refetch) | `trigger_on_demand_fetch(source=...)` |
```

Verify that `## Cross-references` (the following section) remains
correctly positioned.

### Step 3: Remove Callers section from `docs/features/tickets/ticket-service.md`

**Delete** the entire `## Callers` section (heading + table +
supplementary note-table + trailing paragraph, starting at line 764).
This includes:

- The main 3-row table (API endpoint handlers, CVE service,
  IBS track release detection)
- The supplementary "Note — ticket endpoints that route to
  `ticket_mutations` directly" table (3 rows: PATCH severity,
  POST reopen, POST revert-duplicate)
- Trailing paragraph: *"These endpoints bypass `ticket_service`
  entirely — their handlers call `ticket_mutations` functions directly.
  See the Scope Boundary section above for the architectural rationale."*

Verify the adjacent sections remain correctly connected.

### Step 4: Remove Callers section from `docs/features/tickets/ticket-mutations.md`

**Delete** the entire `## Callers` section (heading + intro prose +
table + trailing paragraph, starting at line 981). This includes:

- Intro: *"The callers table is scoped to operation categories rather
  than individual endpoints."*
- The 9-row table
- Trailing paragraph: *"Package-centric callers (IBS release detection,
  product lifecycle transitions, `add_package_to_ticket`) now call
  `package_service` directly — see
  `docs/features/packages/package-service.md`."*

Verify the adjacent sections remain correctly connected.

### Step 5: Remove Callers section from `docs/features/packages/package-service.md`

**Delete** the entire `## Callers` section (heading + intro prose +
table, starting at line 935). This includes:

- Intro: *"The callers table is scoped to operation categories rather
  than individual endpoints."*
- The 9-row table

Verify the adjacent sections remain correctly connected.

### Step 6: Remove Callers section from `docs/features/identity/api-key-service.md`

**Delete** the entire `## Callers` section (heading + table, starting
at line 227). This includes the 5-row table.

Verify the adjacent sections remain correctly connected.

### Step 7: Resolve CSMT-COH-02

**File**: `docs/reviews/cve-sync-mitre.md`

Mark finding CSMT-COH-02 as RESOLVED using compact format:

```
### CSMT-COH-02 — cve-service.md Callers table incomplete for sync_mitre_cves (Low)

**Status**: RESOLVED — Callers table removed from cve-service.md; reverse dependencies are documented in the forward direction by each caller's own spec (YYYY-MM-DD)
```

Remove the `**Category**` line and description body.

**File**: `docs/reviews/.tracking.json`

Update the cache for `cve-sync-mitre`:
- COH Low: 2 → 1
- resolved: 7 → 8
- Total open: 2 → 1

**File**: `docs/reviews/README.md`

Update the cve-sync-mitre row: open `2/9` → `1/9`, COH column
`2` → `1` (severity indicator `2:🟡` → `1:🟡`).

### Step 8: Verify no per-function `**Callers**:` mini-tables are affected

This plan removes only module-level `## Callers` sections. Per-function
inline `**Callers**:` annotations (found in `authentication.md:242`,
`ticket-mutations.md:623`, `package-service.md:291`) are a different
pattern — they document callers of a single specific function within
the function's own contract, co-located with the information they
describe. These are NOT affected by this plan and MUST remain.

Verify after removal that no per-function Callers annotations were
accidentally deleted.

### Step 9: Run reviewers on affected specs

After all removals are applied, invoke the following reviewers to
verify correctness and detect any introduced issues:

1. **`@spec-coherence-reviewer`** on each of the 5 modified specs:
   - `docs/features/tickets/cve-service.md`
   - `docs/features/tickets/ticket-service.md`
   - `docs/features/tickets/ticket-mutations.md`
   - `docs/features/packages/package-service.md`
   - `docs/features/identity/api-key-service.md`

   Purpose: verify no cross-spec contradictions were introduced by the
   removal (e.g., another spec referencing a removed section).

2. **`@docs-placement-reviewer`** on the same 5 specs.

   Purpose: verify no authoritative information was lost (all facts
   previously in the Callers tables are confirmed present at their
   authoritative sources).

3. **`@spec-gap-analyzer`** on `docs/features/tickets/cve-service.md`
   only.

   Purpose: verify the spec remains complete after removal of the
   largest table (12 rows). The other 4 specs lost smaller, less
   information-dense tables and are lower risk.

If any reviewer identifies issues rated "Needs revision", resolve them
before proceeding to Step 10.

### Step 10: Delete this draft

Once all steps are complete and reviewers have passed:

```
rm docs/drafts/remove-callers-tables.md
```

---

## Affected Files Summary

| File | Change type |
|------|-------------|
| `docs/features/tickets/cve-service.md` | Remove `## Callers` section |
| `docs/features/tickets/ticket-service.md` | Remove `## Callers` section |
| `docs/features/tickets/ticket-mutations.md` | Remove `## Callers` section |
| `docs/features/packages/package-service.md` | Remove `## Callers` section |
| `docs/features/identity/api-key-service.md` | Remove `## Callers` section |
| `docs/reviews/cve-sync-mitre.md` | Mark CSMT-COH-02 RESOLVED |
| `docs/reviews/.tracking.json` | Update cve-sync-mitre cache |
| `docs/reviews/README.md` | Update cve-sync-mitre counts |
| `docs/drafts/remove-callers-tables.md` | Delete (final step) |
