# Review: git-fetcher-infrastructure

**Spec**: `docs/features/platform/git-fetcher-infrastructure.md`
**Last reviewed**: 2026-06-25
**Reviewers**: Gap Analysis, Documentation

> Post-split review (fetcher-infrastructure split, Phase 4m). The document
> was created by extracting the `BaseGitFetcher` base class + `git_operations`
> content from the former monolithic `fetcher-infrastructure.md`. The split
> was content-preserving; the gap findings below are pre-existing
> ambiguities, recorded here for future hardening rather than fixed as part
> of the split exercise. Coherence, Design, Security and API Conventions
> reviewers were not run in this round.

---

## Gap Analysis

### GFI-GAP-01 — Large recovery/initial delta cannot converge within the task window (High)

**Category**: Boundary / Temporal
**Status**: RESOLVED

**Resolution** (2026-06-30): Resolved via fetcher-timeout-architecture —
`SoftTimeLimitExceeded` exclusion from per-item catch (step 10d) + hard
time limit backstop (`time_limit = run_timeout`) + operational
convergence note documenting guaranteed convergence through idempotent
reprocessing. See `fetcher-infrastructure.md` ("`SoftTimeLimitExceeded`
handling convention") and `git-fetcher-infrastructure.md` ("Operational:
large delta convergence").

After an extended outage, `_compute_recovery_delta` (or a long catch-up
delta) can yield tens of thousands of files. In a blobless clone each
`show_file()` triggers an on-demand network blob download (30s timeout
each). The Celery task timeout kills the run mid-loop. Because the cursor
is only written after `execute()` returns, no progress is persisted —
every retry restarts the same enormous delta and is killed again, never
converging. The spec defines no intra-run batching, checkpointing, or
partial-cursor mechanism for deltas larger than one task window.

### GFI-GAP-02 — `partial` runs advance the cursor and abandon failed items (Medium)

**Status**: RESOLVED — Documented as intentional trade-off per OP-9 design decision (commit `a7e2632`). Failed items are identified in WARNING logs by file path; `fetch_single()` provides manual recovery. Design note added to Status Determination section. (2026-06-30)

### GFI-GAP-03 — Recovery with a cursor that has `sha` but missing `committed_at` (Medium)

**Status**: RESOLVED — Guard added in execute() step 7a: cursor_committed_at None triggers ERROR log and empty delta (first-run treatment) (2026-07-01)

### GFI-GAP-04 — Default `fetch_single()` does not handle `GitFileError` from `show_file()` (Medium)

**Status**: RESOLVED — GitFileError handling added to fetch_single() step 4: catch per-candidate, aggregate to RuntimeError if all fail (2026-07-01)

### GFI-GAP-05 — `_construct_candidate_paths()` exceptions on malformed `item_id` unspecified (Medium)

**Status**: RESOLVED — ValueError contract added to _construct_candidate_paths() hook; fetch_single() catches ValueError → ERROR log + CVENotInSource (2026-07-01)

### GFI-GAP-06 — Directory deletion failure (`OSError`) during re-clone/recovery unclassified (Medium)

**Status**: RESOLVED — OSError classified in error table; execute() steps 3a/4a log distinct ERROR with path+errno and propagate as FetcherError; infrastructure failures table updated (2026-07-01)

### GFI-GAP-07 — Concurrency rules don't cover the delete-and-re-clone window (Medium)

**Status**: RESOLVED — Accepted risk: the TOCTOU race during delete-and-re-clone is mitigated by GitFileError catch in fetch_single() step 4b (graceful degradation to RuntimeError); locking not warranted for a rare event with safe outcome (2026-07-01)

### GFI-GAP-08 — Transient read errors forced into the "corruption" class (Low)

**Category**: Error path
**Status**: OPEN

The premise "a successful `git fetch` proves connectivity; a subsequent
read failure can only be local corruption" omits transient storage faults.
On networked storage (NFS/PVC), a transient I/O hiccup during `git
diff`/`git rev-parse` is classified as `GitCorruptionError` → deletes a
healthy clone and triggers a full re-clone. No retry-before-delete is
specified.

### GFI-GAP-09 — First-run enumeration inconsistency (Low)

**Category**: Function completeness (clarity)
**Status**: OPEN

The Bare Clone Pattern says "First-run file enumeration: `git ls-tree -r
--name-only HEAD` lists all files", but `execute()` first-run branch
records HEAD and processes nothing. The `ls-tree`/enumeration is never
invoked by the template, leaving its stated purpose ambiguous (utility-only,
or for non-template fetchers).

### GFI-GAP-10 — Per-item processing order unspecified (Low)

**Category**: Boundary
**Status**: OPEN

Processing order equals raw `git diff` output order. The spec relies
implicitly on `process_item` idempotency/order-independence but never
states that order is insignificant — relevant if a future hook has
ordering-sensitive side effects (obvious implicit resolution today, hence
Low).

---

## Documentation

### GFI-DOC-01 — Inbound references to a non-existent "Git-Based Fetchers" section (Medium)

**Status**: RESOLVED — Multiple documents referenced a "Git-Based Fetchers"
section that does not exist in the standalone spec (its sub-topics are now
top-level H2 sections). Updated the dangling pointers in `architecture.md`,
`deployment.md`, `cve-sync-mitre.md`, and `cve-sync-kernel.md` to name the
precise sections (Recovery, Concurrency Rules, Volume Requirements, Worker
Affinity) (2026-06-25)

### GFI-DOC-02 — Stale "this section" phrasing in the intro (Low)

**Status**: RESOLVED — Replaced "this section" with "this document" in the
introductory paragraph (leftover from the monolithic spec) (2026-06-25)

### GFI-DOC-03 — Cross-document references to "Status determination precedence" did not name the document (Low)

**Status**: RESOLVED — The references to "Status determination precedence …
in the BaseFetcher section" now name `fetcher-infrastructure.md` explicitly,
since that concept moved to a separate document (2026-06-25)

### GFI-DOC-04 — "Recovery Strategy" section name did not match any heading (Low)

**Status**: RESOLVED — References to "Recovery Strategy" now point to the
actual headings "Recovery" and "Cursor SHA Unreachable" (2026-06-25)

### GFI-DOC-05 — Redundant consumer listing (Low)

**Status**: RESOLVED — Removed redundant consumer listing at line 40; consumers already named in Purpose section and hierarchy diagram (2026-07-01)

### GFI-DOC-06 — "Function Catalog" section has no table (Low)

**Status**: RESOLVED — Restructured Function Catalog: changed "table" to "sections" in intro, promoted Clone/Fetch/Read/Show/Filesystem Operations and Bare and Blobless Compatibility from H2 siblings to H3 subsections under Function Catalog (2026-07-01)
