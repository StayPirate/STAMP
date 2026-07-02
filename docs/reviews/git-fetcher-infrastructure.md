# Review: git-fetcher-infrastructure

**Spec**: `docs/features/platform/git-fetcher-infrastructure.md`
**Last reviewed**: 2026-07-02
**Reviewers**: Gap Analysis, Coherence, Design, Security, API Conventions, Documentation

> Post-split review (fetcher-infrastructure split, Phase 4m). The document
> was created by extracting the `BaseGitFetcher` base class + `git_operations`
> content from the former monolithic `fetcher-infrastructure.md`. The split
> was content-preserving; the gap findings below are pre-existing
> ambiguities, recorded here for future hardening rather than fixed as part
> of the split exercise.

---

## Gap Analysis

### GFI-GAP-01 — Large recovery/initial delta cannot converge within the task window (High)

**Status**: RESOLVED — Resolved via fetcher-timeout-architecture — `SoftTimeLimitExceeded` exclusion from per-item catch (step 10d) + hard time limit backstop (`time_limit = run_timeout`) + operational convergence note documenting guaranteed convergence through idempotent reprocessing. See `fetcher-infrastructure.md` ("`SoftTimeLimitExceeded` handling convention") and `git-fetcher-infrastructure.md` ("Operational: large delta convergence"). (2026-06-30)

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

**Status**: RESOLVED — Spec updated: added --no-renames to eliminate blobless network calls in diff, added bounded retry (2 retries, 1s/2s backoff) before GitCorruptionError, resolved table/template inconsistency (execute() now catches GitCorruptionError and deletes clone explicitly), documented fetch_single() degradation window (2026-07-02)

### GFI-GAP-09 — First-run enumeration inconsistency (Low)

**Status**: RESOLVED — Removed ls-tree from Bare Clone Pattern steps; moved as utility note to "When NOT to Use BaseGitFetcher" section (2026-07-02)

### GFI-GAP-10 — Per-item processing order unspecified (Low)

**Status**: RESOLVED — Added explicit order-independence requirement to process_item() contract (2026-07-02)

---

## Coherence

No issues identified.

---

## Design

### GFI-DES-01 — SHA format validation and end-of-options separator (Medium)

**Status**: RESOLVED — Added Module Invariants subsection to Function Catalog with SHA format validation rule (regex check in check_sha_reachable) and end-of-options separator rule; updated check_sha_reachable (step 0 validation), show_file, and clone commands to use `--`; updated all prose references (2026-07-02)

### GFI-DES-02 — LC_ALL=C for git subprocess invocations (Medium)

**Status**: RESOLVED — Added Rule 3 (Subprocess environment) to Module Invariants mandating LC_ALL=C, GIT_TERMINAL_PROMPT=0, TZ=UTC via _git_subprocess_env() for all git subprocess calls (2026-07-02)

---

## Security

### GFI-SEC-01 — No file content size limit on show_file output (Medium)

**Status**: RESOLVED — Accepted risk: trusted upstream sources (MITRE, kernel.org), 30s subprocess timeout, Celery OOM recovery, and cursor safety provide adequate protection for current threat model (2026-07-02)

---

## API Conventions

No API endpoints defined in this spec.

---

## Documentation

### GFI-DOC-01 — Inbound references to a non-existent "Git-Based Fetchers" section (Medium)

**Status**: RESOLVED — Multiple documents referenced a "Git-Based Fetchers" section that does not exist in the standalone spec (its sub-topics are now top-level H2 sections). Updated the dangling pointers in `architecture.md`, `deployment.md`, `cve-sync-mitre.md`, and `cve-sync-kernel.md` to name the precise sections (Recovery, Concurrency Rules, Volume Requirements, Worker Affinity) (2026-06-25)

### GFI-DOC-02 — Stale "this section" phrasing in the intro (Low)

**Status**: RESOLVED — Replaced "this section" with "this document" in the introductory paragraph (leftover from the monolithic spec) (2026-06-25)

### GFI-DOC-03 — Cross-document references to "Status determination precedence" did not name the document (Low)

**Status**: RESOLVED — The references to "Status determination precedence … in the BaseFetcher section" now name `fetcher-infrastructure.md` explicitly, since that concept moved to a separate document (2026-06-25)

### GFI-DOC-04 — "Recovery Strategy" section name did not match any heading (Low)

**Status**: RESOLVED — References to "Recovery Strategy" now point to the actual headings "Recovery" and "Cursor SHA Unreachable" (2026-06-25)

### GFI-DOC-05 — Redundant consumer listing (Low)

**Status**: RESOLVED — Removed redundant consumer listing at line 40; consumers already named in Purpose section and hierarchy diagram (2026-07-01)

### GFI-DOC-06 — "Function Catalog" section has no table (Low)

**Status**: RESOLVED — Restructured Function Catalog: changed "table" to "sections" in intro, promoted Clone/Fetch/Read/Show/Filesystem Operations and Bare and Blobless Compatibility from H2 siblings to H3 subsections under Function Catalog (2026-07-01)
