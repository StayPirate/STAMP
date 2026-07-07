# Draft: Remove Blobless Clone from MITRE Fetcher

**Status**: Draft — awaiting review before application  
**Created**: 2026-07-07  
**Motivation**: CSMT-DES-01 finding + design simplification  
**Scope**: Specification-only (no code exists)

## Context

The MITRE CVE fetcher (`sync_mitre_cves`) currently specifies
`clone_filter = "blob:none"` (blobless bare clone). This means:

- `git fetch` downloads only tree/commit objects (fast, ~300 MB on disk)
- `show_file()` for each item triggers an **on-demand blob download**
  from GitHub (network I/O per item during processing)

This design introduces a failure mode where GitHub CDN degradation
after a successful `git fetch` causes `GitFileError` on `show_file()`
calls. When some items succeed and others fail, the run ends as
`partial`, the cursor advances (per OP-9), and failed items are
**permanently lost** unless upstream modifies them later. The MITRE
spec's Error Handling section incorrectly states "all per-CVE failures
are local" — this is factually false for blobless clones.

The kernel fetcher already uses `clone_filter = None` (plain bare clone)
because `git.kernel.org` does not support the `filter` capability.
With a plain bare clone, `show_file()` reads from the local object store
with zero network I/O — making processing a deterministic function of
local state.

## Decision

Switch the MITRE fetcher from blobless to plain bare clone:

- `clone_filter = None` (plain bare clone, all blobs local)
- Retain `clone_filter` as an optional `BaseGitFetcher` parameter
  (default `None`) for future extensibility
- Remove blobless-specific prose from the base class documentation
  where it describes mechanics that no consumer exercises

## Tradeoff Summary

| Aspect | Blobless (current) | Plain bare (proposed) |
|--------|-------------------|---------------------|
| Disk | ~300 MB | ~2.3 GB |
| Processing network I/O | Per-item on-demand | Zero (all local) |
| CDN degradation risk | Partial item loss | None |
| Catch-up (large delta) | N sequential downloads | Bulk pack transfer |
| Recovery from volume loss | ~300 MB re-download | ~2.3 GB re-download |
| Base-class complexity | High (dual-mode docs) | Low (single mode) |
| First-run download | ~300 MB (trees only) | ~2.3 GB (all objects) |

The 2 GB disk cost is trivial in any deployment. Processing robustness
and spec simplicity are more valuable than disk savings.

## Detailed Action Plan

### Prerequisites

- No code implementation exists — all changes are specification updates
- The MITRE cvelistV5 repository plain bare clone is approximately
  2.3 GB (measured via GitHub API: repository size 2,338.8 MB)
- The kernel vulns.git plain bare clone is approximately 91 MB
  (unchanged)
- Combined volume usage: ~2.4 GB (up from ~400 MB)

---

### Step 1: Update `docs/features/tickets/cve-sync-mitre.md`

#### 1a. Fetcher Definition properties table

Change the `clone_filter` row (currently line ~35):

**Before:**
```
| `clone_filter` | `"blob:none"` (server supports partial clone protocol v2) |
```

**After:**
```
| `clone_filter` | `None` (plain bare clone — all blobs local after fetch) |
```

#### 1b. Disk space estimate

Change the disk estimate section (currently line ~316):

**Before:**
```
**Disk space estimate**: ~300 MB (`cvelistV5` blobless bare clone) + ~91 MB (`vulns.git` full bare clone) = ~400 MB total. Provision per the 1 GB minimum specified in git-fetcher-infrastructure.md (Volume Requirements) to allow headroom for git pack files, transient operations, and future growth.
```

**After:**
```
**Disk space estimate**: ~2.3 GB (`cvelistV5` plain bare clone) + ~91 MB (`vulns.git` plain bare clone) = ~2.4 GB total. Provision per the 8 GB minimum specified in git-fetcher-infrastructure.md (Volume Requirements) to allow headroom for git repack operations, transient objects during fetch, and future growth.
```

#### 1c. Error Handling — Abort threshold section

The current text (lines ~343-347) states:

> After a successful git pull, all per-CVE failures are local (parse
> errors, upsert failures). There is no abort threshold — a single
> parse failure should not halt processing of the remaining delta.

This statement is **now correct** with the plain bare clone design (no
on-demand network I/O during processing). However, the section should
be expanded slightly for completeness:

**Replace with:**
```
After a successful `git fetch`, all per-CVE failures are local (parse
errors, JSON schema violations, database constraint failures). There is
no abort threshold — a single parse failure should not halt processing
of the remaining delta. The all-items-failed safety check
(BaseFetcher) protects against complete infrastructure failure (e.g.,
PostgreSQL unreachable) by setting the run to `failure` without
advancing the cursor.
```

#### 1d. Any remaining `blobless` references

Search the file for any remaining mentions of "blobless", "blob:none",
"on-demand blob", or "partial clone". Remove or replace them with
appropriate full-clone equivalents. Known instances:

- If the Capacity line in a volume section references "blobless",
  update to reflect plain bare clone sizing.

---

### Step 2: Update `docs/features/tickets/cve-sync-kernel.md`

#### 2a. Disk space cross-reference

The kernel spec cross-references MITRE's size (currently line ~315-316):

**Before:**
```
Combined with `cvelistV5` (~300 MB blobless), total git volume usage is ~400 MB.
```

**After:**
```
Combined with `cvelistV5` (~2.3 GB plain bare clone), total git volume usage is ~2.4 GB.
```

#### 2b. clone_filter row (if present)

The kernel already has `clone_filter = None`. Verify the description
note. Currently:

```
| `clone_filter` | `None` (server does not advertise `filter` capability) |
```

This is correct and needs no change. The kernel's reason is
server-capability; MITRE's new reason is design choice. Both result
in `None`.

---

### Step 3: Update `docs/features/platform/git-fetcher-infrastructure.md`

This is the largest set of changes. The approach is: keep `clone_filter`
as an optional parameter for extensibility, but remove blobless as the
**default** and remove blobless-specific operational prose that no
consumer exercises.

#### 3a. Class Attributes table — `clone_filter` default

**Before (line ~782):**
```
| `clone_filter` | `str \| None` | `"blob:none"` | Value for `--filter=`. `None` = no filter (plain bare clone) |
```

**After:**
```
| `clone_filter` | `str \| None` | `None` | Git `--filter=` value. `None` = plain bare clone (recommended). Set to `"blob:none"` only if the source requires deferred blob downloads for operational reasons. No current fetcher uses a non-None value. |
```

#### 3b. Responsibility Separation — domain defaults

**Before (line ~505):**
```
Domain defaults (bare=True, filter=blob:none, single-branch=True) live on BaseGitFetcher class attributes.
```

**After:**
```
Domain defaults (bare=True, filter=None, single-branch=True) live on BaseGitFetcher class attributes.
```

#### 3c. Clone step — "Bare Clone Pattern" section (lines ~42-55)

Remove the optional blobless clause in step 1 (lines 51-54). Currently
contains (within the step 1 list item):

> For sources that support Git partial clone (protocol v2 with `filter`
> capability), add `--filter=blob:none` to defer blob downloads. For
> sources that do not support filtering (e.g., `git.kernel.org`), use a
> plain bare clone.

**Replace with** (maintaining list-item indentation):
```
All git-based fetchers use plain bare clones. The `clone_filter`
attribute is available for sources that require deferred blob downloads
(partial clone), but no current fetcher uses it. All blobs are
downloaded during `git clone` and `git fetch`, making subsequent
`show_file()` calls purely local.
```

Note: this text lives inside list item 1 (indented continuation). The
resulting step 1 reads: `git clone --bare --single-branch -- <url>
<dest>` into `$GIT_CLONE_BASE_DIR/...`. [replacement text]. **Validity
check**: ...

#### 3c-bis. Delta detection — blobless rationale (line ~69)

In the same "Bare Clone Pattern" section, step 3 (Delta detection,
lines 63-71), the text currently says:

> Rename detection is explicitly disabled (`--no-renames`) so that the
> diff operates exclusively on local tree/commit objects — no blob
> content is needed, guaranteeing zero network access even in blobless
> clones.

**Replace with:**
```
Rename detection is explicitly disabled (`--no-renames`) so that the
diff operates exclusively on local tree/commit objects — no blob
content comparison is needed, ensuring deterministic output regardless
of clone type.
```

#### 3d. File content access (lines ~72-78)

Currently:
> `git show -- <ref>:<path>` reads a single file's content from the
> object store without creating a working tree. For blobless clones,
> this triggers an on-demand blob download for that specific file only.

**Replace with:**
```
`git show -- <ref>:<path>` reads a single file's content from the
object store without creating a working tree. All blobs are present
locally after `git fetch`, so this operation requires no network access.
```

Also remove the subsequent line:
> No `git merge`, `git checkout`, or working tree manipulation is
> performed at any point.

Keep it — it's general and still valid.

#### 3e. `show_file()` function documentation (lines ~700-706)

Two changes in the `show_file()` section:

**3e-i. Step 4 condition (line ~700):**

Currently:
> 4. If exit code indicates a different failure (network error in blobless
>    clone, corrupt object, timeout): raise `GitFileError` with stderr
>    content

**Replace with:**
```
4. If exit code indicates a different failure (corrupt object, timeout):
   raise `GitFileError` with stderr content
```

**3e-ii. Post-behavior paragraph (lines ~704-706):**

Currently:
> In blobless clones, step 1 triggers an on-demand blob download from
> the remote — requires network access. If the remote is unreachable,
> step 4 fires.

**Replace with:**
```
In a plain bare clone, blob content is already present in the local
object store. Network access is not required. If the blob is
unexpectedly absent (corrupt pack file), step 4 fires.
```

#### 3f. "Bare and Blobless Compatibility" section (lines ~718-747)

**Replace the entire section** with a shorter "Bare Clone Compatibility"
note:

```
### Bare Clone Compatibility

All git operations in the function catalog are designed for bare
repositories (no working tree). Every operation accesses the git object
store directly:

- **Commit/tree operations** (`get_head_sha`, `get_commit_date`,
  `is_clone_valid`, `check_sha_reachable`, `diff_names`,
  `rev_list_before`): read commit and tree objects only.
- **Blob operations** (`show_file`): read file content from the local
  object store via `git show`. All blobs are present locally after
  the initial clone and subsequent fetches.

The `--no-renames` flag on `diff_names` ensures diffs operate
exclusively on tree objects without comparing blob content. This
avoids expensive similarity computation on large repositories and
produces deterministic output (renames appear as separate delete + add
pairs).

Note: the `clone_filter` class attribute supports partial clones
(`--filter=blob:none`) for sources where deferred blob downloads are
operationally required. No current fetcher uses this mode. If enabled,
`show_file()` would trigger on-demand blob downloads requiring network
access during processing — introducing per-item network failure risk.
This mode is retained for future extensibility only.
```

#### 3f-bis. Recovery section cross-reference (line ~308)

Step 3f renames "Bare and Blobless Compatibility" to "Bare Clone
Compatibility". Line 308 (Recovery section, step 4) references the old
section name. Currently:

> (same `--no-renames` flag as normal delta — guarantees local-only
> operation in blobless clones; see "Bare and Blobless Compatibility")

**Replace with:**
```
(same `--no-renames` flag as normal delta — guarantees local-only
operation; see "Bare Clone Compatibility")
```

#### 3g. Error classification table (line ~434)

The `git show` row currently says:
> Any failure (timeout, missing blob, network error in blobless clone)
> | GitFileError

**Replace with:**
```
Any failure (timeout, corrupt/missing blob in local store) | GitFileError
```

#### 3h. Error classification design note (lines ~448-452)

Remove or simplify the blobless clause:
> diff_names uses --no-renames, ensuring it operates exclusively on
> local tree/commit objects (no blob content needed). This means
> read-phase failures in diff_names are genuinely storage-related, not
> network-related — reinforcing the corruption classification after
> retry exhaustion.

This text is **still valid** regardless of clone type. Keep it as-is.

#### 3i. Large-delta convergence note (lines ~340-344)

Currently:
> After an extended outage (weeks or longer), the recovery delta may
> contain thousands of files. In a blobless clone, each file requires
> an on-demand blob download. If the delta cannot be fully processed
> within run_timeout, the soft time limit fires, the run ends as
> failure, and the cursor does not advance.

**Replace with:**
```
After an extended outage (weeks or longer), the recovery delta may
contain thousands of files. Since all blobs are present locally after
`git fetch`, processing speed is bounded only by database throughput
and `process_item()` complexity — not network latency. If the delta
cannot be fully processed within `run_timeout`, the soft time limit
fires, the run ends as `failure`, and the cursor does not advance.
Convergence is guaranteed through idempotent reprocessing across
successive runs.
```

#### 3j. All-items-failed safety check example (line ~953)

Currently:
> e.g., network drops after fetch in a blobless clone, making every
> `show_file()` fail

**Replace with:**
```
e.g., local storage failure making every `show_file()` fail, or
database connection loss causing every `process_item()` to raise
```

#### 3k. `fetch_single()` — GitFileError handling (lines ~1084-1092)

The log messages and RuntimeError text reference "Blob download failed".
These should be generalized:

**Before:**
```
log WARNING ('Blob download failed for {path} — skipping candidate')
```
```
raise RuntimeError ('Blob download failed for item {item_id} — source temporarily not queryable')
```

**After:**
```
log WARNING ('File read failed for {path} — skipping candidate')
```
```
raise RuntimeError ('File read failed for all candidate paths for item {item_id}')
```

#### 3l. `fetch_single()` exceptions documentation (lines ~1125-1127)

Remove "blob download failed in a blobless clone" from the RuntimeError
description. Replace with "local object store failure for all candidate
paths".

#### 3m. git version dependency (line ~374)

Currently:
> git | 2.25 | First stable release with partial clone (`--filter`)
> support. Required for blobless clones of cvelistV5.

**Replace with:**
```
git | 2.25 | Minimum version for protocol v2, improved bare-clone performance, and `--filter` support (retained for future extensibility).
```

#### 3n. pygit2 elimination rationale (lines ~383-392)

Currently the primary rationale is:
> libgit2 cannot open repositories with the extensions.partialclone
> extension — Unusable with blobless clones.

Since blobless is no longer used, rewrite the rationale to frame the
elimination as forward-looking while preserving issue traceability:

**Replace with:**
```
pygit2 (libgit2 bindings): eliminated — libgit2 cannot open
repositories with the `extensions.partialclone` extension
(libgit2/libgit2#5564, open since Jun 2020; #6880 confirms the
error persists in v1.7.2, Sep 2024). While no current fetcher uses
partial clones, this blocks future extensibility. Additionally,
libgit2 lacks a direct `git show` equivalent for bare repository
blob access, requiring workaround code
```

The GitPython elimination (RCE/security) remains unchanged — it's
independent.

#### 3o. Volume Requirements — Capacity (line ~187)

**Before:**
```
| Capacity | 1 GB minimum (current usage ~400 MB; provides headroom for growth and transient git operations) |
```

**After:**
```
| Capacity | 8 GB minimum (current usage ~2.4 GB; provides headroom for git repack operations — which temporarily require old + new pack coexistence (~4.7 GB peak) — plus future growth at ~150 MB/year) |
```

#### 3p. Clone timeout — value and reference (line ~401)

The Clone row in the timeout table must be updated for both the
increased timeout (20→30 min) and the larger download size:

**Before:**
```
| Clone | 20 minutes | 0 | Initial bare clone (~300 MB download) |
```

**After:**
```
| Clone | 30 minutes | 0 | Initial bare clone (~2.3 GB download for cvelistV5) |
```

**Rationale**: 30 minutes accommodates 2.3 GB at ≥1.3 MB/s sustained
throughput. This is adequate for all realistic deployments (typical
GitHub CDN throughput is 50-100 MB/s; even restrictive corporate proxies
deliver >5 MB/s). The cost of the extra 10 minutes is negligible — it
only adds detection latency in the rare scenario where `git clone`
hangs without producing an error (git's own TCP/HTTP timeouts fire
first in all normal network failure scenarios).

Also update the `clone()` function step 2 reference (line ~610):

**Before:**
```
   clone timeout (20 minutes)
```

**After:**
```
   clone timeout (30 minutes)
```

#### 3q. Re-clone cost reference (line ~412)

**Before:**
```
vastly cheaper than a false-positive re-clone of ~300 MB
```

**After:**
```
vastly cheaper than a false-positive re-clone of ~2.3 GB
```

#### 3r. `clone()` function example (line ~608)

If the example shows `--filter=blob:none` in the command:

**Before:**
```
["git", "clone", "--bare", "--filter=blob:none", "--single-branch", "--", url, str(dest)]
```

**After (show the plain case as default, blobless as optional):**
```
["git", "clone", "--bare", "--single-branch", "--", url, str(dest)]
# If clone_filter is set: ["git", "clone", "--bare", "--filter=<value>", "--single-branch", "--", url, str(dest)]
```

#### 3s. `diff_names` blobless rationale (line ~678)

Currently mentions "guarantee zero network access in blobless clones".

**Replace with:**
```
Rename detection is disabled (`--no-renames`) to ensure deterministic
diff output and avoid expensive blob-content similarity computation on
large repositories. Renames appear as separate delete + add pairs.
```

---

### Step 4: Update `docs/deployment.md`

#### 4a. Git Worker Volume section (lines ~332-350)

Update the minimum capacity:

**Before:**
```
| Minimum capacity | 1 GB |
```

**After:**
```
| Minimum capacity | 8 GB |
```

If there's a note about "~400 MB current usage" or "blobless", update
accordingly.

#### 4b. Outbound connectivity table

The entry for `github.com:443` ("MITRE cvelistV5 repository
clone/fetch") remains correct — network is still needed for clone/fetch.
The difference is that per-item processing no longer needs the network.
No change needed here.

---

### Step 5: Resolve CSMT-DES-01 finding

After applying Steps 1-4, the MITRE spec's statement "all per-CVE
failures are local" becomes **factually correct**. The finding's premise
(that `show_file()` triggers network I/O) no longer applies.

#### 5a. Update `docs/reviews/cve-sync-mitre.md`

Mark CSMT-DES-01 as RESOLVED using compact format:

```
### CSMT-DES-01 — Blobless clone network failures incorrectly characterized as 'local' in abort threshold rationale (Medium)

**Status**: RESOLVED — Design changed: MITRE fetcher switched from blobless to plain bare clone; show_file() is now purely local I/O, making the "all failures are local" statement correct (2026-07-07)
```

Remove the `**Category**` line and description body.

#### 5b. Update `docs/reviews/.tracking.json`

For the `cve-sync-mitre` entry:
- Decrement `DES.M` by 1 (from current value)
- Increment `resolved` by 1

#### 5c. Update `docs/reviews/README.md`

Recalculate the cve-sync-mitre row and Total row per the standard
layout rules (open count decreases by 1, DES column updates).

---

### Step 6: Resolve CSMT-DES-02 and CSMT-DES-03

Review findings CSMT-DES-02 and CSMT-DES-03 are **unrelated** to
blobless and remain OPEN. No action needed for this draft.

---

### Step 7: Verify internal consistency

After all changes, perform a full-text search across the modified files
for any remaining occurrences of:
- "blob:none" (should only appear in the "retained for extensibility"
  note in git-fetcher-infrastructure.md, section "Bare Clone
  Compatibility")
- "blobless" (same — only in the extensibility note)
- "~300 MB" referencing cvelistV5 (should be updated to ~2.3 GB)
- "~400 MB" referencing total git volume (should be ~2.4 GB)
- "1 GB minimum" referencing volume capacity (should be 8 GB)
- "20 minutes" referencing clone timeout (should be 30 minutes)
- "on-demand blob download" outside the extensibility note (should be
  removed)
- "Bare and Blobless Compatibility" (old section name — should be
  replaced by "Bare Clone Compatibility" everywhere)
- "full bare clone" (should be "plain bare clone" — standardized
  terminology)

---

### Step 8: Run reviewers on affected specs

Launch the following reviewers to verify correctness after applying
the plan:

| Spec | Reviewers | Rationale |
|------|-----------|-----------|
| `cve-sync-mitre` | `spec-gap-analyzer`, `spec-coherence-reviewer` | Design change affects error handling, disk sizing, cross-references |
| `git-fetcher-infrastructure` | `spec-gap-analyzer`, `spec-coherence-reviewer`, `design-reviewer` | Substantial rewrite of multiple sections; architectural simplification |
| `cve-sync-kernel` | `spec-coherence-reviewer` | Cross-reference to MITRE disk size changed |
| `deployment` | `spec-coherence-reviewer` | Volume sizing changed; must be consistent with feature specs |

Launch reviewers in parallel per spec. Address any High-severity
findings before considering the change complete.

---

### Step 9: Delete this draft

After all changes are applied, reviewed, and findings addressed, delete
this file:

```
rm docs/drafts/remove-blobless-mitre.md
```

---

## Files Modified (Summary)

| File | Nature of change |
|------|-----------------|
| `docs/features/tickets/cve-sync-mitre.md` | `clone_filter`, disk estimate, abort threshold text |
| `docs/features/tickets/cve-sync-kernel.md` | Cross-reference to MITRE disk size |
| `docs/features/platform/git-fetcher-infrastructure.md` | Default value, clone timeout 20→30 min, volume 1→8 GB, ~20 sections updated/simplified |
| `docs/deployment.md` | Volume capacity minimum 1→8 GB |
| `docs/reviews/cve-sync-mitre.md` | CSMT-DES-01 resolved |
| `docs/reviews/.tracking.json` | DES M count decremented |
| `docs/reviews/README.md` | Counts updated |

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Initial clone takes longer (~2.3 GB vs ~300 MB) | One-time cost; clone timeout increased to 30 minutes (adequate for ≥1.3 MB/s sustained). Recovery from volume loss is infrequent (volume is persistent, backed by recoverable-cache pattern). |
| Disk growth over time (new CVEs add blobs) | 8 GB minimum provides ~70% headroom over peak repack usage (~4.7 GB). `git gc` repacks periodically. Growth rate: ~30k CVEs/year × ~5 KB avg = ~150 MB/year. |
| Future fetcher needs blobless | `clone_filter` parameter retained (default None). Re-enabling requires only setting the attribute and re-adding operational documentation for that fetcher. |
| `git fetch` downloads more data per run | In practice, incremental fetches download only new/modified blobs in the delta (typically 1-144 files × ~5 KB = negligible). The bulk cost is the initial clone, not incremental fetches. |

## Non-Changes (explicitly out of scope)

- `docs/architecture.md` — no blobless-specific content; unchanged
- `docs/configuration.md` — `GIT_CLONE_BASE_DIR` description is generic; unchanged
- `docs/data-model.md` — no blobless references; unchanged
- `docs/data-sources.md` — MITRE entry says "bare clone + fetch" (already correct); unchanged
- `docs/features/platform/cve-fetcher-infrastructure.md` — no blobless-specific content; unchanged
- Other fetcher specs (NVD, Red Hat, GHSA, KEV, EPSS, OSV) — API-based, not git-based; unchanged
