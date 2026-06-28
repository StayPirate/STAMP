# CVESource Visibility Redesign

**Status**: Draft
**Created**: 2026-06-28
**Origin**: Discussion during review-spec fix session on `cve-fetcher-infrastructure`

## Motivation

The current design of `CVESource` status tracking has a visibility gap:
only the on-demand fetch path (`fetch_single_cve` orchestrator) writes
`failure` and `missing` statuses. The batch `execute()` path and the
catch-up path only write `success` (via `upsert_cve()`), relying on
rollback for errors and silent skip for `CVENotInSource`.

This means:

1. **The UI cannot distinguish "never attempted" from "attempted and
   failed"** for sources processed via batch — both appear as absent
   from `sources[]`
2. **The UI cannot show all available CVE sources** without hardcoding —
   there is no API endpoint that exposes the registered source types
3. **The VA has no visibility** into which sources have been checked for
   a given CVE when the check happened during periodic batch execution

This draft proposes a redesign that makes `CVESource` a complete record
of all fetch attempts, regardless of the invocation path, and introduces
an API endpoint for source status discovery.

## Current Behavior

### CVESource status writes by path

| Path | success | missing | failure |
|------|---------|---------|---------|
| On-demand (`fetch_single_cve`) | Yes (via `upsert_cve()`) | Yes (orchestrator) | Yes (orchestrator) |
| Batch (`execute()`) | Yes (via `upsert_cve()`) | No (rollback-and-skip) | No (rollback + `record_failed()`) |
| Catch-up (`run_catch_up`) | Yes (via `upsert_cve()`) | No (silent rollback) | No (propagates to wrapper, no status write) |

### Fetch Status Read Path (current)

Defined in `docs/features/tickets/cve-service.md` (lines 980-1004):

- Iterates `get_fetch_single_fetchers()` (only sources with
  `supports_fetch_single = True` — excludes KEV)
- Queries `CVESource` DB records for the CVE
- Checks Redis pending keys for in-flight fetches
- **Omits sources with no DB record and no Redis key** ("source not
  attempted")

### Consequences

- Sources never attempted are invisible to the frontend
- The frontend has no way to discover which CVE sources exist without
  hardcoding
- The `GET /api/v1/cves/{cve_id}/refetch` endpoint accepts a `source`
  parameter but valid values are not discoverable
- Batch failures leave no persistent trace (only metrics on `FetcherRun`)
- KEV (catalog-based, `supports_fetch_single = False`) is invisible in
  the read path until its bulk sync writes `success`

### Relevant spec references

- `docs/features/tickets/cve-service.md` — `record_source_status()`,
  Fetch Status Read Path, `upsert_cve()`, `fetch_single_cve` orchestrator
- `docs/features/platform/cve-fetcher-infrastructure.md` — Batch Error
  Handling, `commit_and_dispatch()`, `CVENotInSource` signal,
  `get_fetch_single_fetchers()`
- `docs/features/platform/fetcher-infrastructure.md` — `catch_up()`
  interface contract, `run_catch_up` wrapper
- `docs/features/tickets/cve-sync-kev.md` — KEV fetcher algorithm
- `docs/data-model.md` — `CVESource` table, `CVESourceFetchStatus` enum

## Proposed Design

### Principle

**`CVESource` = the latest known outcome for every `(cve_id, source)`
pair that has been attempted, regardless of invocation path.**

Every path that queries an external source for a CVE writes the outcome.
The `record_source_status()` function remains the single entry point for
all writes. The semantic of each status is unchanged:

| Status | Meaning |
|--------|---------|
| `success` | Source queried, data retrieved and persisted |
| `failure` | Source queried, retrieval failed (retries exhausted) |
| `missing` | Source queried, CVE confirmed not present |

### Change 1: Batch `execute()` writes `failure` and `missing`

**Current behavior**: on error → `session.rollback()` +
`self.record_failed()` + continue. On `CVENotInSource` → rollback +
skip.

**New behavior**:

```
On error (non-CVENotInSource):
  1. session.rollback()
  2. record_source_status(session, cve_id, self.cve_source_type, "failure")
  3. session.commit()  # mini-commit for the status record
  4. self.record_failed()
  5. continue

On CVENotInSource:
  1. session.rollback()
  2. record_source_status(session, cve_id, self.cve_source_type, "missing")
  3. session.commit()  # mini-commit for the status record
  4. continue (no metric — missing is not a failure)
```

**Transaction pattern**: the rollback clears the failed/partial data,
then a fresh write + commit persists only the status record. This is the
same pattern used by the on-demand orchestrator for failure/missing.

**Metric impact**: `record_failed()` behavior unchanged (only for actual
errors, not for `CVENotInSource`). `missing` is not counted in metrics
(consistent with current on-demand behavior where `CVENotInSource` "is
not a failure condition").

**Volume assessment**:
- `failure` records: proportional to actual errors (transient). Typically
  low. Overwritten by `success` on next successful run
- `missing` records: proportional to CVEs not present in a given source.
  For stateless fetchers (Red Hat, OSV, GHSA) iterating active tickets,
  this is bounded by (active_tickets - CVEs_in_source). One-time write,
  subsequent runs update only `fetched_at`

**Fetchers affected**: only those whose `execute()` calls
`self.fetch_single()` in a loop (stateless fetchers: Red Hat, OSV, GHSA,
EPSS). Cursor-based (NVD) and git-based (MITRE, kernel) fetchers do not
encounter `CVENotInSource` in their batch path.

### Change 2: Catch-up writes `failure` and `missing`

**Current behavior**: default `catch_up()` catches `CVENotInSource` →
silent rollback. Errors propagate to `run_catch_up` wrapper for retry;
after exhaustion, task fails with no status write.

**New behavior**:

```
Default catch_up() — CVENotInSource:
  1. session.rollback()
  2. record_source_status(session, cve_id, self.cve_source_type, "missing")
  3. session.commit()

run_catch_up wrapper — after retry exhaustion:
  1. session.rollback()  (if session is dirty)
  2. record_source_status(session, cve_id, fetcher_cls.cve_source_type, "failure")
  3. session.commit()
```

**Rationale**: catch-up is best-effort data recovery on ticket
reactivation. Writing status records gives the VA visibility into what
happened during catch-up without changing catch-up's fire-and-forget
nature. The next periodic `execute()` run serves as the safety net.

### Change 3: KEV writes `missing` for CVEs not in catalog

**Current behavior**: KEV iterates catalog entries → enriches matching
local CVEs → silently skips catalog entries without a local CVE. CVEs
not in the catalog are never touched.

**New behavior**: at the end of `execute()`, after processing all
catalog entries, KEV performs a reverse pass:

```
1. Query: all CVE IDs with active tickets that do NOT have a
   CVESource record with source="kev" and status="success"
2. For each such CVE:
   record_source_status(session, cve_id, "kev", "missing")
3. Batch commit
```

**Semantics**: "We downloaded the full KEV catalog and this CVE is not
in it" = confirmed not a Known Exploited Vulnerability. This is useful
triage information for VAs.

**Volume**: if 10,000 active CVEs and 1,000 in KEV → 9,000 `missing`
records written once. Subsequent runs: upsert updates only `fetched_at`.
A single `INSERT ... ON CONFLICT UPDATE` batch completes in seconds.

**Optimization**: the query filters for CVEs without an existing
`success` record for KEV. CVEs that already have `missing` will be
re-upserted (updating `fetched_at`) — this is acceptable and avoids
complex "already processed" tracking.

### Change 4: New API endpoint `GET /api/v1/cves/{cve_id}/sources`

Dedicated endpoint for CVE source status. Replaces the inline
`sources[]` in ticket detail as the authoritative source visibility
mechanism.

**Path**: `GET /api/v1/cves/{cve_id}/sources`

**Response**:

```json
{
  "sources": [
    {
      "source": "nvd",
      "status": "success",
      "fetched_at": "2026-06-28T10:30:00Z",
      "registered": true,
      "refetchable": true
    },
    {
      "source": "mitre",
      "status": "success",
      "fetched_at": "2026-06-28T08:00:00Z",
      "registered": true,
      "refetchable": true
    },
    {
      "source": "redhat",
      "status": "not_attempted",
      "fetched_at": null,
      "registered": true,
      "refetchable": true
    },
    {
      "source": "kev",
      "status": "missing",
      "fetched_at": "2026-06-28T04:00:00Z",
      "registered": true,
      "refetchable": false
    },
    {
      "source": "legacy_source",
      "status": "success",
      "fetched_at": "2025-03-01T12:00:00Z",
      "registered": false,
      "refetchable": false
    }
  ]
}
```

**Fields**:

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | CVESourceType identifier |
| `status` | string | One of: `success`, `failure`, `missing`, `pending`, `not_attempted` |
| `fetched_at` | datetime or null | Timestamp of last fetch attempt (null if `not_attempted`) |
| `registered` | boolean | Whether the source is currently registered in the fetcher registry |
| `refetchable` | boolean | Whether on-demand refetch is supported (`supports_fetch_single`) |

**Status values**:

| Status | Derivation |
|--------|-----------|
| `success` | DB record with `status = success` |
| `failure` | DB record with `status = failure` AND no Redis pending key |
| `missing` | DB record with `status = missing` AND no Redis pending key |
| `pending` | Redis pending key exists AND (no DB record, or DB status is `failure`/`missing`) |
| `not_attempted` | No DB record AND no Redis pending key |

**Resolution algorithm**:

1. Query all `CVESource` records for the given `cve_id`
2. Load all registered CVE source types from
   `get_all_cve_source_types()` (new accessor — see Change 5)
3. For each registered source with `refetchable = true`, check Redis
   pending key `fetch_pending:{cve_id}:{source}`. Sources with
   `refetchable = false` (e.g., KEV) never have Redis pending keys and
   are resolved from DB state alone
4. Apply resolution rules (same as current Read Path, but with
   `not_attempted` instead of omission)
5. For any DB records with a `source` value NOT in the current registry
   → include with `registered: false, refetchable: false`

**Relationship to ticket detail**: the `sources[]` array in `CVEDetail`
(returned by `GET /api/v1/tickets/{ticket_id}`) can either:
- Be removed entirely (frontend calls the dedicated endpoint separately)
- Be kept as a lightweight summary (only sources with DB records, same
  as today) with the dedicated endpoint as the full-detail version

Decision deferred to implementation phase — both options work.

### Change 5: New registry accessor `get_all_cve_source_types()`

```python
def get_all_cve_source_types() -> dict[str, type[BaseCVEFetcher]]:
    """Return ALL registered CVE fetchers, regardless of fetch_single support.

    Returns a dict mapping cve_source_type -> fetcher class for all
    registered BaseCVEFetcher subclasses. Unlike get_fetch_single_fetchers(),
    this includes catalog-based fetchers (KEV) and any future fetchers
    that set supports_fetch_single = False.

    Used by: sources endpoint, Fetch Status Read Path.
    """
    return dict(_CVE_SOURCE_TYPE_MAP)
```

`get_fetch_single_fetchers()` remains unchanged — it still filters by
`supports_fetch_single = True` and is used by the on-demand dispatch
logic and refetch validation.

### Change 6: Deregistered fetcher handling

When a fetcher is removed from the codebase (no longer registered at
import time), its `CVESource` records persist in the database. The
sources endpoint handles this:

- `get_all_cve_source_types()` does NOT include the deregistered source
- The DB query in step 1 still returns its records
- Step 5 of the resolution algorithm detects the mismatch and includes
  the source with `registered: false, refetchable: false`

This gives VAs visibility: "data from this source exists in the CVE
record but the source is no longer active."

## Design Decisions and Rationale

### Why `CVESource` always tracks the latest state (no protection against overwrite)

The `success → missing` and `success → failure` transitions are
accepted:

- **`success → missing`**: means the source previously had the CVE but
  no longer does (e.g., CVE retracted). Rare but factually correct. The
  CVE data fetched previously remains in Sentinel (CVSS, references,
  etc.) — it is not deleted
- **`success → failure`**: means a source that previously worked is now
  experiencing errors. Transient — will be overwritten by `success` on
  next successful run. The VA can trigger a manual refetch

Both transitions are rare (sources rarely remove CVEs or become
persistently broken) and the VA always has the refetch option. Adding
protection logic (e.g., "never overwrite success") would add complexity
for a theoretical benefit while hiding real information (the current
state of the source).

**Catalog-based reverse pass simplification**: for fetchers that use a
reverse pass to mark absent CVEs (currently KEV), the reverse pass does
not overwrite existing `success` records. The query filters by DB status
(`WHERE NOT EXISTS CVESource with source/success`), avoiding the need to
track the in-run processed set in memory. A CVE removed from an external
catalog after a previous `success` maintains its `success` status. This
is accepted because external sources removing previously-published CVEs
is an event with near-zero historical precedent — the added algorithmic
complexity of tracking the processed set is not justified. This
simplification applies to any future catalog-based fetcher with a
reverse pass.

### Why `not_attempted` is needed as a distinct state

`not_attempted` exists for the interval between CVE creation and the
first run of each fetcher. For example: MITRE creates a CVE at 15:00,
Red Hat runs at 02:00 next day → for 11 hours, Red Hat is
`not_attempted`. After the first run, it transitions to `success`,
`missing`, or `failure`.

`not_attempted` is a transient state that resolves naturally. It differs
from `missing` (which is an active assertion: "we checked and it's not
there").

### Why `record_source_status()` remains the single entry point

All status writes — from batch, on-demand, and catch-up — go through
`record_source_status()`. This ensures:

- Consistent upsert behavior (`(cve_id, source)` key)
- Single place to add future logic (e.g., logging, event emission)
- No divergence between paths

### Why the mini-commit pattern is acceptable for batch

After a rollback in the `execute()` loop, a mini-commit for the
`CVESource` status record is safe because:

- The rollback cleared all partial data from the failed item
- The status write is a single atomic upsert on a different table
- The loop continues with the next CVE in a clean session state
- This is the same pattern the on-demand orchestrator already uses

### Why catch-up behaves identically to batch (not to on-demand)

Catch-up is a best-effort recovery mechanism, not user-initiated. Like
batch:
- No Redis pending keys (no one is watching in real time)
- No `FetcherRun` record, no metrics
- The next periodic `execute()` is the safety net

Unlike on-demand:
- No user waiting for feedback
- No Redis dedup keys needed

The only difference from the current batch design: catch-up now writes
`missing`/`failure` for visibility, same as the new batch behavior.

## Impact on Existing Specs

### Files that need modification

| File | Section | Change |
|------|---------|--------|
| `docs/features/platform/cve-fetcher-infrastructure.md` | Batch Error Handling | Add `record_source_status("failure"/"missing")` + mini-commit to error handling pattern |
| `docs/features/platform/cve-fetcher-infrastructure.md` | Default `catch_up()` Implementation | Add `record_source_status("missing")` after `CVENotInSource` catch |
| `docs/features/platform/cve-fetcher-infrastructure.md` | `fetch_single` Signaling Convention table | Add note that batch/catch-up now also write status |
| `docs/features/platform/fetcher-infrastructure.md` | `catch_up()` Error handling | Specify `record_source_status("failure")` after retry exhaustion in `run_catch_up` |
| `docs/features/tickets/cve-service.md` | CVESource Management | Note that all paths now write status (remove "batch path does not write") |
| `docs/features/tickets/cve-service.md` | Fetch Status Read Path | Update to use `get_all_cve_source_types()`, add `not_attempted`, add `registered`/`refetchable` |
| `docs/features/tickets/cve-sync-kev.md` | Algorithm | Add reverse pass for `missing` writes |
| `docs/features/tickets/tickets.md` | CVEDetail sub-schema | Update `sources[]` documentation (or remove if delegated to new endpoint) |
| `docs/data-model.md` | CVESource section | Note that all paths write status; no schema change needed |
| `docs/api-spec.md` | Endpoint registry | Add `GET /api/v1/cves/{cve_id}/sources` |
| `docs/features/platform/cve-fetcher-infrastructure.md` | Registry accessors | Add `get_all_cve_source_types()` specification |
| `docs/features/identity/rbac.md` | Endpoint Permission Map | Add `GET /api/v1/cves/{cve_id}/sources` with appropriate access level |

### Files that need a new section

| File | New content |
|------|-------------|
| `docs/features/tickets/cve-service.md` | New endpoint `GET /api/v1/cves/{cve_id}/sources` — full specification |

### No changes needed

- `docs/features/platform/git-fetcher-infrastructure.md` — git fetchers
  don't encounter `CVENotInSource` in batch (they process files, not
  per-CVE queries)
- NVD fetcher spec — cursor-based, no `CVENotInSource` in batch

## Execution Plan

Each step is independently applicable and results in a consistent spec
state. Steps should be executed in order but can span multiple sessions.

### Step 1: Update batch error handling pattern

**Target**: `docs/features/platform/cve-fetcher-infrastructure.md`

**Changes**:
1. In section "Batch Error Handling" (around line 629): replace the
   current "no explicit `record_source_status("failure")` is needed"
   text with the new pattern including mini-commit
2. Add handling for `CVENotInSource` in batch: after rollback, write
   `record_source_status("missing")` + commit
3. Update the "Distinction from the on-demand path" paragraph: remove
   the statement that batch does not write failure/missing status.
   Replace with a note that all paths now write status, with the only
   difference being Redis pending keys (on-demand only)
4. Update the `execute()` pseudocode (around line 256) to show the new
   error handling with mini-commits

**Validation**: verify no contradiction with other sections in the same
file or in `cve-service.md`.

### Step 2: Update catch-up behavior

**Target**: `docs/features/platform/cve-fetcher-infrastructure.md` and
`docs/features/platform/fetcher-infrastructure.md`

**Changes in cve-fetcher-infrastructure.md**:
1. Update the default `catch_up()` pseudocode (around line 70): after
   catching `CVENotInSource`, add `record_source_status("missing")` +
   commit instead of bare rollback
2. Add a note in the boundary conditions about the new status writes

**Changes in fetcher-infrastructure.md**:
1. In the `catch_up()` interface contract → Error handling section
   (around line 505): add a sub-point specifying that after
   `run_catch_up` retry exhaustion, the wrapper writes
   `record_source_status("failure")` + commit
2. Clarify that this applies to CVE fetchers (via the default
   `catch_up()`) — non-CVE fetcher custom overrides manage their own
   error handling

**Validation**: verify consistency with the "best-effort" catch-up
philosophy documented elsewhere in fetcher-infrastructure.md.

### Step 3: Update KEV fetcher to write `missing`

**Target**: `docs/features/tickets/cve-sync-kev.md`

**Changes**:
1. Add a new algorithm step after the catalog processing loop: "Reverse
   pass — mark non-KEV CVEs"
2. Specify the query: active CVEs without a `CVESource(source="kev",
   status="success")` record
3. Specify the batch `record_source_status("missing")` + commit
4. Update the metrics section if needed (missing writes generate no
   metric)
5. Update the "Scope" line in the fetcher definition table to reflect
   the dual nature: "Enrichment (catalog entries) + absence confirmation
   (active CVEs not in catalog)"
6. Document the reverse pass simplification: the query does not overwrite
   existing `success` records (no need to track the in-run processed
   set). A CVE removed from the KEV catalog after a previous `success`
   maintains its `success` status. This is accepted because CISA
   removing a CVE from the catalog is an event with near-zero historical
   precedent. This simplification applies to any future catalog-based
   fetcher with a reverse pass

**Validation**: verify KEV's `supports_fetch_single = False` is still
coherent with the new behavior (it is — this is a batch-only change).

### Step 4: Add `get_all_cve_source_types()` accessor

**Target**: `docs/features/platform/cve-fetcher-infrastructure.md`

**Changes**:
1. Add a new section after "Registry accessor:
   `get_fetch_single_fetchers()`" documenting
   `get_all_cve_source_types()`
2. Specify: returns ALL registered `cve_source_type` → fetcher class
   mappings (no filtering)
3. Specify: used by the sources endpoint and the updated Fetch Status
   Read Path
4. Note that `get_fetch_single_fetchers()` remains unchanged (used by
   on-demand dispatch and refetch validation)

**Validation**: verify `get_fetch_single_fetchers()` references
elsewhere don't need updating.

### Step 5: Specify new endpoint `GET /api/v1/cves/{cve_id}/sources`

**Target**: `docs/features/tickets/cve-service.md`

**Changes**:
1. Add a new section specifying the endpoint (path, method, auth,
   request, response schema, resolution algorithm)
2. Include all five status values: `success`, `failure`, `missing`,
   `pending`, `not_attempted`
3. Include `registered` and `refetchable` fields
4. Specify deregistered fetcher handling (DB records for sources not in
   current registry)
5. Specify Redis graceful degradation (same as current: if Redis
   unreachable, no `pending` shown)

**Also update**: `docs/api-spec.md` (endpoint registry),
`docs/features/identity/rbac.md` (Endpoint Permission Map — add the new
endpoint with appropriate access level).

**Validation**: run `@api-convention-reviewer` on the new endpoint
definition.

### Step 6: Update Fetch Status Read Path

**Target**: `docs/features/tickets/cve-service.md`

**Changes**:
1. Update the existing "Fetch Status Read Path" section to reference the
   new endpoint as the primary mechanism
2. Decide: keep inline `sources[]` in ticket detail (lightweight
   summary) or remove it (delegate to dedicated endpoint)
3. If keeping: update to use `get_all_cve_source_types()` and include
   `not_attempted`
4. If removing: update `CVEDetail` sub-schema in `tickets.md`
5. Remove the statement "source not attempted (omit from response)" and
   replace with the new `not_attempted` behavior

**Validation**: verify consistency between the endpoint spec (Step 5)
and the read path description.

### Step 7: Update cross-references and data model docs

**Targets**: `docs/data-model.md`, `docs/features/tickets/tickets.md`,
`docs/features/tickets/cve-tracking.md`

**Changes in data-model.md**:
1. Update the `CVESource` section prose: note that all invocation paths
   (on-demand, batch, catch-up) now write status records
2. No schema change needed (same table, same columns, same enum)

**Changes in tickets.md**:
1. Update the `CVESource` sub-schema documentation (if `sources[]`
   remains in ticket detail) or add a cross-reference to the new
   endpoint

**Changes in cve-tracking.md**:
1. If there's a Fetch Status Read Path pointer section, update to
   reference the new endpoint

**Validation**: run `@spec-coherence-reviewer` on modified files.

### Step 8: Resolve related open findings

After all spec changes are applied, the following review findings should
be re-evaluated (they may be auto-resolvable):

- **CFI-GAP-03** — "No failure-status recording on catch-up retry
  exhaustion" → directly addressed by Step 2
- **CFI-GAP-06** — "Catch-up does not record `missing` status" →
  directly addressed by Step 2

Run `/review-spec refresh cve-fetcher-infrastructure` to validate.

### Step 9: Run reviewers on modified specs

After all spec changes are applied (Steps 1–7), run the appropriate
reviewers to verify the plan was applied correctly and no new issues
were introduced:

1. **`@spec-coherence-reviewer`** on all modified specs — detect
   contradictions or terminology drift introduced by the changes:
   - `cve-fetcher-infrastructure`
   - `fetcher-infrastructure`
   - `cve-service`
   - `cve-sync-kev`
   - `tickets`
   - `cve-tracking`
2. **`@spec-gap-analyzer`** on specs with significant behavioral
   changes:
   - `cve-fetcher-infrastructure` (batch + catch-up behavior changed)
   - `cve-service` (new endpoint + read path change)
   - `cve-sync-kev` (new algorithm step)
3. **`@api-convention-reviewer`** on specs that define API endpoints:
   - `cve-service` (new `GET /api/v1/cves/{cve_id}/sources` endpoint)
4. **`@security-reviewer`** on the new endpoint (verify auth, input
   validation, information exposure)

Address any findings rated "Needs revision" before considering the
plan complete. Minor findings can be fixed in the same session.

### Step 10: Delete this draft

Once all steps are executed, all reviewers pass, and the design is
fully integrated into the authoritative specs:

```
rm docs/drafts/cvesource-visibility-redesign.md
```

This draft has served its purpose as a working document. The
authoritative specification now lives in the modified spec files.

## Open Questions

1. **Ticket detail `sources[]`**: keep as lightweight summary or remove
   entirely in favor of the dedicated endpoint? (Deferred to Step 6)
2. **KEV reverse pass optimization**: should KEV skip CVEs that already
   have `missing` with a recent `fetched_at`? Current proposal: no
   optimization, just upsert all (simple, correct, fast enough for
   PostgreSQL)
3. **EPSS and `missing`**: EPSS covers nearly all CVEs — should it
   write `missing` for the rare CVEs it doesn't cover, or is its scope
   so broad that `not_attempted` effectively never persists?
