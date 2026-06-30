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

**Category**: Data lifecycle / Error path
**Status**: OPEN

A single `show_file()` blob download timeout (transient network blip) →
`record_failed()`, run ends `partial`, cursor advances to HEAD. The failed
CVE file is now behind the cursor; the next delta starts at the new HEAD,
so the item is never reprocessed unless the upstream file changes again.
The only recovery is a manual `fetch_single()`. No automatic retry/requeue
for items failed during a `partial` run is specified.

### GFI-GAP-03 — Recovery with a cursor that has `sha` but missing `committed_at` (Medium)

**Category**: Boundary / Error path
**Status**: OPEN

If a cursor contains `sha` but no `committed_at` (earlier code path, or a
manually-edited/partial cursor) and that `sha` later becomes unreachable,
recovery is triggered and `_compute_recovery_delta` step 1 attempts "minus
1 day" on `None`. The spec does not define this case — the implementer
must guess (fall back to first-run treatment, or raise).

### GFI-GAP-04 — Default `fetch_single()` does not handle `GitFileError` from `show_file()` (Medium)

**Category**: Function completeness / Error path
**Status**: OPEN

In a blobless clone, an on-demand `fetch_single()` for a CVE whose blob is
not local triggers a network download that can time out → `GitFileError`.
Step 4 only branches on `None` vs. not-`None` content; it does not say what
happens when `show_file` raises, and the exception is absent from the
documented exception set. Two implementers could choose to (a) propagate,
(b) treat as not-found and try the next candidate, or (c) map to
`RuntimeError`. The caller behaves very differently per choice.

### GFI-GAP-05 — `_construct_candidate_paths()` exceptions on malformed `item_id` unspecified (Medium)

**Category**: Function completeness
**Status**: OPEN

`fetch_single()` invoked with an `item_id` not in `CVE-YYYY-NNNN` form
(e.g., a free-form identifier from another source) makes the kernel hook's
`split("-")[1]` raise `IndexError`. The fetch_single exceptions list does
not cover hook-construction exceptions, leaving dispatch behavior undefined
(crash vs. skip-and-try-next).

### GFI-GAP-06 — Directory deletion failure (`OSError`) during re-clone/recovery unclassified (Medium)

**Category**: Error path / Temporal (loop risk)
**Status**: OPEN

Corrupted-clone recovery (or first-run cleanup of an invalid directory)
calls delete, but the filesystem may reject it (read-only mount,
permission, busy handle on a network volume). `OSError` is not in the
phase-based classification table (which covers only Git* exceptions). It
would propagate as an unclassified exception → `failure`, and every
subsequent run re-attempts delete on the same un-deletable directory → a
permanent failure loop with no operator guidance distinct from the
corruption case.

### GFI-GAP-07 — Concurrency rules don't cover the delete-and-re-clone window (Medium)

**Category**: Concurrency
**Status**: OPEN

The append-only/atomic safety argument justifies `fetch_single()` reads
during a periodic `git fetch`, but the corruption-recovery and "cursor
exists + clone invalid" paths delete the entire directory — not an
append-only operation. A `fetch_single()` that passes the `is_clone_valid()`
guard and then calls `show_file()` while the periodic task is mid-delete
hits a TOCTOU race with undefined result. The safety reasoning explicitly
does not extend to destructive operations.

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

**Status**: OPEN

The consumer fetchers (`sync_mitre_cves`, `sync_kernel_cves`) are listed in
Purpose ("Current consumers") and again in the intro ("Current git-based
fetchers"). Harmless duplication; consolidating would tighten the intro.
(Low-value; left open rather than fixed to avoid churn.)

### GFI-DOC-06 — "Function Catalog" section has no table (Low)

**Status**: OPEN

"Function Catalog" introduces "the following table" but the actual function
tables live in sibling H2 sections (Clone Operations, Fetch Operations,
…), leaving the Function Catalog section itself with only an intro. These
would be more coherent as H3 subsections under Function Catalog. Purely
structural; no content is missing. (Low-value; left open rather than
fixed.)
