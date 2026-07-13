# Draft: Reverse Cursor Advancement on Partial Runs

**Status**: Draft — pending review before application
**Created**: 2026-07-13
**Scope**: Specification-only change (no code, no migrations)

## Summary

This document describes the change from **"partial advances the cursor"**
to **"partial does NOT advance the cursor"** across all cursor-based
fetchers. After this change, the cursor advances **only on `success`**;
both `partial` and `failure` preserve the previous cursor, causing the
next run to reprocess the same window/delta.

The `partial` status itself is unchanged — it remains a valid run outcome
visible on the dashboard. Only its relationship to cursor persistence
changes.

## Motivation

The current design optimizes for steady-state operation (where `partial`
is caused by permanently malformed upstream data). However, the system
is in active development where parsing bugs are the dominant cause of
per-item failures. Under the current policy:

1. **Bug latency**: a code bug that fails on item A causes a `partial`
   run; the cursor advances; A exits the processing window; subsequent
   runs return `success`; the bug becomes invisible on the dashboard
   while remaining latent in the codebase
2. **Silent data loss**: item A is abandoned without automated retry.
   Recovery depends on upstream re-modification (not guaranteed) or
   manual operator intervention via `fetch_single()` — but the
   dashboard signal that would prompt investigation has already
   disappeared
3. **No auto-heal after fix**: when the bug is eventually fixed, the
   fix does not retroactively recover the abandoned items — they are
   already outside the processing window

Under the new policy:

1. **Persistent visibility**: the fetcher remains `partial` on the
   dashboard until the bug is fixed and all items succeed
2. **Automatic retry**: failed items remain in the window and are
   reprocessed on every subsequent run (idempotent upserts make
   already-processed items no-ops)
3. **Self-healing**: once the bug is fixed, the next run reprocesses
   the failed items and transitions to `success` automatically — no
   manual intervention required

**Cost accepted**: the processing window/delta grows while `partial`
persists. This is bounded by operational intervention (expected within
days) and, for temporal fetchers, by existing stale-cursor guards
(NVD: 120 days, GHSA: 30 days) which act as ultimate backstops. For
git fetchers, the soft time limit prevents unbounded execution. The
cost of idempotent reprocessing (API calls + DB no-ops) is low relative
to the cost of silent CVE data loss in a security platform.

## Affected Specifications

| File | Type of change |
|------|----------------|
| `docs/features/platform/fetcher-infrastructure.md` | Canonical rule definition (consolidation target) |
| `docs/features/platform/git-fetcher-infrastructure.md` | Rule references + design note rewrite |
| `docs/features/tickets/cve-sync-nvd.md` | Rule references + paragraph rewrite |
| `docs/features/tickets/cve-sync-ghsa.md` | Rule references + paragraph adjustments |
| `docs/data-model.md` | Column description update |
| `docs/reviews/git-fetcher-infrastructure.md` | Historical annotation |

**NOT affected** (stateless fetchers without cursors): `cve-sync-osv.md`,
`cve-sync-redhat.md`, `cve-sync-epss.md`, `cve-sync-kev.md`,
`cve-sync-mitre.md` (inherits from BaseGitFetcher, but its cursor
references delegate to the generic infrastructure spec),
`cve-sync-kernel.md` (same as MITRE).

## Consolidation Strategy

Currently, the rule "which statuses advance the cursor" is stated
independently in 5+ locations. This change consolidates the rule into
a single canonical definition in `fetcher-infrastructure.md`. Other
specs reference it instead of restating it. This eliminates future
drift risk.

**Canonical location**: `fetcher-infrastructure.md`, section
"BaseFetcher Base Class", bullet "Cursor persistence" (line 98).

**Reference pattern**: other specs use a parenthetical reference such as
`(see "Cursor Advancement Rule" in fetcher-infrastructure.md)` after
their cursor derivation/query statements.

---

## Action Plan

### Step 1. Define canonical rule in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: Lines 98-106 (Cursor persistence bullet)

**Current text** (lines 98-106):

```
   - **Cursor persistence**: if `execute()` returns normally, the final
     status is `success` or `partial`, and `self._cursor` is set (a
     dict), `run()` writes it to the `FetcherRun.cursor` column in the
     same transaction that sets `status` and `finished_at`. Cursor is
     NOT written when: `self._cursor` is None (not set), `execute()`
     raised an exception (failure path), or the all-items-failed safety
     check triggers (status set to `failure` despite normal return). See
     `docs/features/platform/git-fetcher-infrastructure.md` (Cursor Persistence) for the full mechanism
     and query pattern
```

**Replace with**:

```
   - **Cursor persistence** (Cursor Advancement Rule): if `execute()`
     returns normally, the final status is `success`, and `self._cursor`
     is set (a dict), `run()` writes it to the `FetcherRun.cursor`
     column in the same transaction that sets `status` and `finished_at`.
     Cursor is NOT written when: `self._cursor` is None (not set),
     `execute()` raised an exception (failure path), the
     all-items-failed safety check triggers (status set to `failure`
     despite normal return), or the final status is `partial`. On
     `partial`, the cursor is intentionally not advanced — failed items
     remain in the processing window for automatic retry on the next run
     (idempotent upserts make already-processed items no-ops).

     For fetchers that use a **derived cursor** (query on `started_at`
     of a previous run rather than the JSONB column — e.g., NVD, GHSA),
     the same predicate applies: only runs with `status = 'success'`
     are eligible as cursor sources. A `partial` run's `started_at` is
     not used as cursor origin.

     **Rationale**: in a security data platform, silent abandonment of
     failed items (which may represent missed CVEs) is a worse outcome
     than reprocessing cost. A `partial` run indicates at least one item
     could not be processed; preserving the cursor ensures the fetcher
     retries those items automatically and remains visibly degraded on
     the dashboard until the underlying issue is resolved.

     **Bounding**: the processing window/delta grows while `partial`
     persists. Practical bounds: (1) operational intervention within
     days; (2) for temporal fetchers, existing stale-cursor guards
     (NVD: 120 days, GHSA: 30 days) reset the cursor as an ultimate
     backstop; (3) for git fetchers, the soft time limit prevents
     unbounded execution (a delta that cannot converge within
     `run_timeout` becomes a `failure`).

     See `docs/features/platform/git-fetcher-infrastructure.md` (Cursor
     Persistence) for the full write mechanism and query pattern.
```

---

### Step 2. Update cursor column notes in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: Lines 2320, 2331-2336

**Current text** (line 2320, column description):

```
| cursor | JSONB | nullable | Fetcher-defined checkpoint for the next run. Generic: may contain a commit SHA, timestamp, offset, page token, or any structured cursor. Written when the final run status is `success` or `partial`; read by the next run to determine the starting point. See `docs/features/platform/git-fetcher-infrastructure.md` (Cursor Persistence) for the git-specific usage pattern |
```

**Replace with**:

```
| cursor | JSONB | nullable | Fetcher-defined checkpoint for the next run. Generic: may contain a commit SHA, timestamp, offset, page token, or any structured cursor. Written only when the final run status is `success`; not written on `partial` or `failure` (see Cursor Advancement Rule above). Read by the next run to determine the starting point. See `docs/features/platform/git-fetcher-infrastructure.md` (Cursor Persistence) for the git-specific usage pattern |
```

**Current text** (lines 2331-2336):

```
- `cursor` is written at the end of a successful or partial run and
  read at the start of the next run (query: last `FetcherRun` with
  `status IN ('success', 'partial')` for the same `fetcher_name`,
  ordered by `started_at DESC`, limit 1). Fetchers that derive their
  starting point from other columns (e.g., `started_at`) leave
  `cursor` NULL.
```

**Replace with**:

```
- `cursor` is written at the end of a successful run (not on `partial`
  or `failure`) and read at the start of the next run (query: last
  `FetcherRun` with `status = 'success'` for the same `fetcher_name`,
  ordered by `started_at DESC`, limit 1). Fetchers that derive their
  starting point from other columns (e.g., `started_at`) leave
  `cursor` NULL. See Cursor Advancement Rule (above) for full
  semantics.
```

---

### Step 3. Update `data-model.md` column description

**File**: `docs/data-model.md`
**Location**: Line 1402

**Current text**:

```
| cursor               | JSONB       | nullable                 | Fetcher-defined checkpoint for the next run (e.g., `{"sha": "...", "committed_at": "..."}` for git-based fetchers). Written when the final run status is `success` or `partial`; read by the next run to determine starting point. NULL for fetchers that derive cursors from other fields |
```

**Replace with**:

```
| cursor               | JSONB       | nullable                 | Fetcher-defined checkpoint for the next run (e.g., `{"sha": "...", "committed_at": "..."}` for git-based fetchers). Written only when the final run status is `success` (not on `partial` or `failure`); read by the next run to determine starting point. NULL for fetchers that derive cursors from other fields. See `docs/features/platform/fetcher-infrastructure.md` (Cursor Advancement Rule) |
```

---

### Step 4. Update `git-fetcher-infrastructure.md` — Cursor Persistence section

**File**: `docs/features/platform/git-fetcher-infrastructure.md`
**Location**: Lines 81-108 (Cursor Persistence section)

**Current text** (lines 83-94):

```
Git-based fetchers persist their checkpoint (the last successfully
processed commit SHA) in the `FetcherRun.cursor` JSONB column. After
a run completes with `success` or `partial` status, the fetcher writes:

```json
{"sha": "<40-char hex SHA>", "committed_at": "<ISO 8601 date>"}
```

The next run reads the cursor from the most recent `FetcherRun` with
`status IN ('success', 'partial')` for the same `fetcher_name`:

- `sha`: the HEAD commit SHA at the end of a `success` or `partial` run
- `committed_at`: the committer date of that commit (ISO 8601
  format). Used as the recovery boundary when the cursor SHA becomes
  unreachable (see "Cursor SHA Unreachable" below)
```

**Replace with**:

```
Git-based fetchers persist their checkpoint (the last successfully
processed commit SHA) in the `FetcherRun.cursor` JSONB column. After
a run completes with `success` status, the fetcher writes:

```json
{"sha": "<40-char hex SHA>", "committed_at": "<ISO 8601 date>"}
```

The cursor is NOT written on `partial` or `failure` — see Cursor
Advancement Rule in `fetcher-infrastructure.md`.

The next run reads the cursor from the most recent `FetcherRun` with
`status = 'success'` for the same `fetcher_name`:

- `sha`: the HEAD commit SHA at the end of a `success` run
- `committed_at`: the committer date of that commit (ISO 8601
  format). Used as the recovery boundary when the cursor SHA becomes
  unreachable (see "Cursor SHA Unreachable" below)
```

---

### Step 5. Update `git-fetcher-infrastructure.md` — Write Mechanism section

**File**: `docs/features/platform/git-fetcher-infrastructure.md`
**Location**: Lines 110-120 (Write Mechanism section)

**Current text** (lines 112-120):

```
Inside `execute()`, the fetcher sets `self._cursor` (a dict) with the
checkpoint data. After `execute()` returns, `run()` determines the
final status (see "Status determination precedence" in
`fetcher-infrastructure.md`) and then, only if
the final status is `success` or `partial`, reads `self._cursor` and
writes it to the `FetcherRun` row in the same transaction that sets
`status` and `finished_at`. If `self._cursor` is None (not set), or
the final status is `failure` (including the all-items-failed case),
no cursor is written.
```

**Replace with**:

```
Inside `execute()`, the fetcher sets `self._cursor` (a dict) with the
checkpoint data. After `execute()` returns, `run()` determines the
final status (see "Status determination precedence" in
`fetcher-infrastructure.md`) and then, only if
the final status is `success`, reads `self._cursor` and writes it to
the `FetcherRun` row in the same transaction that sets `status` and
`finished_at`. If `self._cursor` is None (not set), or the final
status is `partial` or `failure` (including the all-items-failed
case), no cursor is written. See Cursor Advancement Rule in
`fetcher-infrastructure.md`.
```

---

### Step 6. Update `git-fetcher-infrastructure.md` — Recovery section

**File**: `docs/features/platform/git-fetcher-infrastructure.md`
**Location**: Lines 255-256

**Current text**:

```
2. Read the `cursor` from the last `FetcherRun` with
   `status IN ('success', 'partial')` for this fetcher in the database
```

**Replace with**:

```
2. Read the `cursor` from the last `FetcherRun` with
   `status = 'success'` for this fetcher in the database
```

---

### Step 7. Update `git-fetcher-infrastructure.md` — all-items-failed note

**File**: `docs/features/platform/git-fetcher-infrastructure.md`
**Location**: Lines 952-960

**Current text** (lines 956-960):

```
`status = failure` directly after `execute()` returns. Since the cursor
is only persisted on `success` or `partial`, the previous cursor is
preserved and the next run retries the same delta. This applies to all
fetcher subclasses uniformly, not just git-based fetchers.
```

**Replace with**:

```
`status = failure` directly after `execute()` returns. Since the cursor
is only persisted on `success` (see Cursor Advancement Rule in
`fetcher-infrastructure.md`), the previous cursor is preserved and the
next run retries the same delta. This applies to all fetcher subclasses
uniformly, not just git-based fetchers.
```

---

### Step 8. Update `git-fetcher-infrastructure.md` — Status Determination table

**File**: `docs/features/platform/git-fetcher-infrastructure.md`
**Location**: Lines 962-986

**Current text** (lines 967-986):

```
| Scenario | Status | Cursor advances? |
|----------|--------|-----------------|
| First run (no processing) | `success` | Yes (step 3e) |
| Empty delta (HEAD unchanged) | `success` | Yes (step 11) |
| All items succeed | `success` | Yes (step 11) |
| Some items fail, some succeed | `partial` | Yes (step 11) |
| All items fail | `failure` | No (`BaseFetcher.run()` safety check) |
| Infrastructure error | `failure` | No (propagates) |

**Design note — cursor advancement on `partial` (commit `a7e2632`):**
advancing the cursor on `partial` runs is an intentional trade-off.
Failed items are not automatically retried — they reappear naturally
when upstream modifies them (git's change-tracking provides recovery).
Persistent failures indicate code bugs requiring human intervention,
not automated retry; the alternative (not advancing the cursor) creates
an ever-growing reprocessing loop that is operationally worse. Failed
items are identified in WARNING logs by CVE-ID (step 10e:
`"Failed to process item %s: %s", cve_id, e`), enabling operators to invoke
`fetch_single()` for targeted recovery. See also: rejected alternatives
(threshold-based cursor, per-item retry tracking) were rejected during initial design.
```

**Replace with**:

```
| Scenario | Status | Cursor advances? |
|----------|--------|-----------------|
| First run (no processing) | `success` | Yes (step 3e) |
| Empty delta (HEAD unchanged) | `success` | Yes (step 11) |
| All items succeed | `success` | Yes (step 11) |
| Some items fail, some succeed | `partial` | No (Cursor Advancement Rule) |
| All items fail | `failure` | No (`BaseFetcher.run()` safety check) |
| Infrastructure error | `failure` | No (propagates) |

**Design note — cursor non-advancement on `partial`:** the cursor is
intentionally NOT advanced on `partial` runs. Failed items remain in
the delta and are automatically retried on the next run (idempotent
upserts make already-processed items no-ops). This ensures:

- **Bug visibility**: the fetcher remains `partial` on the dashboard
  until the issue is resolved — the signal is persistent and
  proportional to the problem
- **Automatic recovery**: once a parsing bug is fixed, the next run
  reprocesses and recovers the previously-failed items without manual
  intervention
- **No silent data loss**: in a security data platform, abandoning a
  failed CVE item without automated retry is a worse outcome than
  reprocessing cost

**Cost**: the delta (cursor..HEAD) grows while `partial` persists. This
is bounded by: (a) operational intervention within days, (b) the soft
time limit — a delta that cannot converge within `run_timeout` triggers
a `SoftTimeLimitExceeded` exception, resulting in `failure` status
(loud, forces investigation). Failed items are identified in WARNING
logs by CVE-ID (step 10e: `"Failed to process item %s: %s", cve_id, e`);
`fetch_single()` remains available for targeted recovery of individual
items.

**History**: the initial design (commit `a7e2632`) advanced the cursor
on `partial` and rejected threshold-based and per-item retry
alternatives. This was reversed based on the analysis that during
active development, parsing bugs are the dominant cause of `partial`
runs, making automatic retry and persistent visibility more valuable
than steady-state operational convenience.
```

---

### Step 9. Update `cve-sync-nvd.md` — Algorithm (cursor derivation)

**File**: `docs/features/tickets/cve-sync-nvd.md`
**Location**: Lines 107-111

**Current text**:

```
1. Derive `last_sync` from the `started_at` timestamp of the most
   recent `FetcherRun` with `status IN ('success', 'partial')` for
   `sync_nvd_cves`. If no such run exists → first run: terminate with
   `status = success`, zero records. The `started_at` of this run
   becomes the cursor for future runs. (See "First Run and >120-day Gap
   Handling" below)
```

**Replace with**:

```
1. Derive `last_sync` from the `started_at` timestamp of the most
   recent `FetcherRun` with `status = 'success'` for `sync_nvd_cves`
   (see Cursor Advancement Rule in `fetcher-infrastructure.md`). If no
   such run exists → first run: terminate with `status = success`, zero
   records. The `started_at` of this run becomes the cursor for future
   runs. (See "First Run and >120-day Gap Handling" below)
```

---

### Step 10. Update `cve-sync-nvd.md` — Cursor Mechanism section

**File**: `docs/features/tickets/cve-sync-nvd.md`
**Location**: Lines 546-562

**Current text** (lines 546-562):

```
The NVD fetcher uses a **derived cursor** (not an explicit one):

- Cursor value = `started_at` of the most recent `FetcherRun` with
  `status IN ('success', 'partial')` for `sync_nvd_cves`
- The `FetcherRun.cursor` JSONB column remains `NULL` for NVD runs
- No explicit cursor management in `execute()`

This is appropriate because the NVD checkpoint is purely temporal
("I have seen all modifications up to time X"), unlike git-based
fetchers whose checkpoint is a commit SHA.

**`partial` status and cursor advancement**: a run with `status =
partial` (some CVEs failed processing) correctly advances the cursor
because all pages were fully scanned. The per-CVE failures represent
unparseable data (not missed data). Those CVEs will be re-encountered
if/when NVD modifies them again, or can be fetched on-demand via
`fetch_single()`.
```

**Replace with**:

```
The NVD fetcher uses a **derived cursor** (not an explicit one):

- Cursor value = `started_at` of the most recent `FetcherRun` with
  `status = 'success'` for `sync_nvd_cves` (see Cursor Advancement
  Rule in `fetcher-infrastructure.md`)
- The `FetcherRun.cursor` JSONB column remains `NULL` for NVD runs
- No explicit cursor management in `execute()`

This is appropriate because the NVD checkpoint is purely temporal
("I have seen all modifications up to time X"), unlike git-based
fetchers whose checkpoint is a commit SHA.

**`partial` status and cursor non-advancement**: a run with `status =
partial` (some CVEs failed processing) does NOT advance the cursor.
The next run reprocesses the same time window — already-processed CVEs
produce idempotent no-ops, while previously-failed CVEs are retried.
This ensures failed items are automatically recovered once the
underlying issue is resolved, and the fetcher remains visibly degraded
on the dashboard until then. Items that cannot be recovered
automatically (permanent upstream malformation) are identifiable via
`CVESource.status = 'failure'` records and recoverable on-demand via
`fetch_single()`. The stale-cursor guard (120 days) acts as an
ultimate backstop if `partial` persists beyond operational intervention
time.
```

---

### Step 11. Update `cve-sync-ghsa.md` — Algorithm (cursor derivation)

**File**: `docs/features/tickets/cve-sync-ghsa.md`
**Location**: Lines 95-100

**Current text**:

```
2. Derive `last_sync` from the `started_at` timestamp of the most
   recent `FetcherRun` with `status IN ('success', 'partial')` for
   `sync_ghsa_advisories`. If no such run exists → first run:
   terminate with `status = success`, zero records. The `started_at`
   of this run becomes the cursor for future runs. (See "First Run
   Behavior" below)
```

**Replace with**:

```
2. Derive `last_sync` from the `started_at` timestamp of the most
   recent `FetcherRun` with `status = 'success'` for
   `sync_ghsa_advisories` (see Cursor Advancement Rule in
   `fetcher-infrastructure.md`). If no such run exists → first run:
   terminate with `status = success`, zero records. The `started_at`
   of this run becomes the cursor for future runs. (See "First Run
   Behavior" below)
```

---

### Step 12. Update `cve-sync-ghsa.md` — Cursor Mechanism section

**File**: `docs/features/tickets/cve-sync-ghsa.md`
**Location**: Lines 249-261

**Current text** (lines 249-261):

```
### Cursor Mechanism

Derived cursor (NVD-style):

- **Source**: `FetcherRun.started_at` of the most recent run with
  `status IN ('success', 'partial')` for `sync_ghsa_advisories`
- **Computation**: `started_at` - 15 minutes = `window_start`
- **No explicit cursor storage**: the cursor is derived from run
  history, not stored as a separate value

This ensures that a failed run (which does not update `started_at`)
results in the next successful run re-processing the entire failed
window — no data is permanently missed.
```

**Replace with**:

```
### Cursor Mechanism

Derived cursor (NVD-style):

- **Source**: `FetcherRun.started_at` of the most recent run with
  `status = 'success'` for `sync_ghsa_advisories` (see Cursor
  Advancement Rule in `fetcher-infrastructure.md`)
- **Computation**: `started_at` - 15 minutes = `window_start`
- **No explicit cursor storage**: the cursor is derived from run
  history, not stored as a separate value

This ensures that a failed or partial run (which does not advance the
cursor) results in the next run re-processing the entire affected
window — no data is permanently missed. Already-processed advisories
produce idempotent no-ops; only previously-failed items are
effectively retried.
```

---

### Step 13. Update `cve-sync-ghsa.md` — Stale Cursor Handling rationale

**File**: `docs/features/tickets/cve-sync-ghsa.md`
**Location**: Lines 224-228

**Current text**:

```
This prevents: (a) pagination volumes that could exceed the Celery
task timeout, causing an infinite retry loop (the cursor never
advances on failure); (b) rate limit exhaustion during recovery; (c)
processing a backlog of advisories that have likely already been
ingested from NVD/MITRE during the downtime period.
```

**Replace with**:

```
This prevents: (a) pagination volumes that could exceed the Celery
task timeout, causing an infinite retry loop (the cursor never
advances on failure or partial); (b) rate limit exhaustion during
recovery; (c) processing a backlog of advisories that have likely
already been ingested from NVD/MITRE during the downtime period.
```

---

### Step 14. Update `fetcher-infrastructure.md` — OperationalError note

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: Lines 793-794

**Current text**:

```
The all-items-failed safety check in `run()` then triggers, setting
status to `failure` and preventing cursor advancement. This is
```

**No change needed** — the text already says `failure` prevents cursor
advancement, which remains true. This step is a verification-only check
(no edit required).

---

### Step 15. Annotate review finding GFI-GAP-02

**File**: `docs/reviews/git-fetcher-infrastructure.md`
**Location**: Lines 22-24

**Current text**:

```
### GFI-GAP-02 — `partial` runs advance the cursor and abandon failed items (Medium)

**Status**: RESOLVED — Documented as intentional trade-off per OP-9 design decision (commit `a7e2632`). Failed items are identified in WARNING logs by file path; `fetch_single()` provides manual recovery. Design note added to Status Determination section. (2026-06-30)
```

**Replace with**:

```
### GFI-GAP-02 — `partial` runs advance the cursor and abandon failed items (Medium)

**Status**: SUPERSEDED (2026-07-13) — The original resolution (advance on partial as intentional trade-off) has been reversed. `partial` runs no longer advance the cursor. Failed items remain in the processing window for automatic retry. See the Cursor Advancement Rule in `fetcher-infrastructure.md` and the updated Design Note in the Status Determination section of this spec.
```

---

### Step 16. Verification — run reviewers on affected specs

After all edits from Steps 1-15 are applied, invoke the following
reviewers in independent sessions to verify correctness:

1. **`@spec-coherence-reviewer`** — one session per affected spec:
   - `docs/features/platform/fetcher-infrastructure.md`
   - `docs/features/platform/git-fetcher-infrastructure.md`
   - `docs/features/tickets/cve-sync-nvd.md`
   - `docs/features/tickets/cve-sync-ghsa.md`

2. **`@spec-gap-analyzer`** — one session per spec with behavioral
   changes:
   - `docs/features/platform/fetcher-infrastructure.md`
   - `docs/features/platform/git-fetcher-infrastructure.md`

3. **`@docs-placement-reviewer`** — verify the consolidation is
   correctly placed (canonical rule in fetcher-infrastructure.md,
   references from the others).

4. **`@fetcher-compliance-reviewer`** — verify the updated fetcher
   specs still satisfy the documentation requirements.

5. **`@docs-reviewer`** — verify documentation completeness and
   coherence across the affected set.

If any reviewer identifies issues rated "Needs revision", address
them before considering the change complete.

---

### Step 17. Delete this draft

Once all edits are applied and verified by reviewers (Step 16), delete
this file:

```
docs/drafts/partial-cursor-advancement-reversal.md
```

---

## Implementation Contract (for future code)

When `BaseFetcher` is implemented, the following test assertions MUST
hold:

| Run outcome | `FetcherRun.cursor` written? | Next run's cursor source |
|-------------|------------------------------|--------------------------|
| `success` | Yes (if `self._cursor` set) | This run |
| `partial` | **No** | Previous `success` run |
| `failure` | No | Previous `success` run |

For derived-cursor fetchers (NVD, GHSA): the `started_at` query uses
`status = 'success'` (not `IN ('success', 'partial')`).

---

## Coherence Notes

- **NVD overlap buffer** (line 527): currently says
  `last_successful_run.started_at`. This was already inconsistent with
  the cursor mechanism (which said `success|partial`). After this
  change, both align on `success`-only — the inconsistency is resolved.
- **GHSA first-run / stale-cursor logic**: unchanged. Both terminate
  with `status = success`, which correctly advances the cursor.
- **Status determination precedence**: unchanged. `partial` remains a
  valid status for runs where `items_failed > 0` and at least one item
  succeeded.
- **all-items-failed safety check**: unchanged. Produces `failure`
  (which already did not advance). The change only affects `partial`.
- **Stateless fetchers** (OSV, Red Hat, EPSS, KEV): unaffected. They
  process their full scope every run with no cursor.
- **`catch_up()` mechanism**: unaffected. It is a sub-operation with no
  `FetcherRun` record and no cursor.
- **Dashboard/API**: `partial` remains a filterable status in
  `fetcher-operations.md`. The drill-down link to
  `GET /api/v1/cve-sources?...&status=failure` continues to work.
  No endpoint changes needed.
- **MITRE and kernel specs**: reference git-fetcher-infrastructure for
  cursor behavior. The updated references there cover them transitively.
  No direct edits needed in `cve-sync-mitre.md` or `cve-sync-kernel.md`.
