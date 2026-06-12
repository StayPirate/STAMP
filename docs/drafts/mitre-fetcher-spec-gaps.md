# MITRE Fetcher — Specification Gap Analysis

Working document for tracking specification gaps in `sync_mitre_cves`
that must be resolved before implementation begins. This document also
serves as a staging area: resolved open points include the exact content
to be inserted into the target spec files. All changes are applied
together to keep spec files in a coherent state.

## Current Specification State

The `sync_mitre_cves` fetcher (class `SyncMitreCves`) is documented
across multiple specification files:

| Aspect | Location | Status |
|--------|----------|--------|
| Properties, algorithm, error handling, metrics | `docs/features/tickets/cve-tracking.md:489-650` | Complete |
| CVE JSON 5.x field extraction (CNA + ADP + CISA) | `cve-tracking.md:527-551` | Resolved: mapping table ready (OP-5, OP-6) |
| `fetch_single()` + Git concurrency rules | `cve-tracking.md:577-635` | Complete |
| Storage, recovery, worker affinity | `cve-tracking.md:614-636` | Resolved: shared in fetcher-infrastructure.md (OP-1, OP-2, OP-8) |
| Integration with `cve_service.upsert_cve()` | `docs/features/tickets/cve-service.md` | Complete |
| Integration with reference_service | `docs/features/tickets/ticket-references.md:197-201` | Complete |
| Data model (populated tables) | `docs/data-model.md` | Complete |
| Fetcher Registry entry | `docs/data-sources.md:927` | Complete |
| Security review | `docs/reviews/cve-service.md` (CVES-SEC-03) | Resolved |
| Custom settings | `cve-tracking.md:500` | Resolved: none needed (OP-3) |
| Configuration env vars | `docs/configuration.md` | Resolved: Change 5 ready (OP-2) |
| Git library / runtime dependency | `fetcher-infrastructure.md` | Resolved: raw subprocess, git >= 2.25 (OP-7) |
| Deployment (git worker, volume) | `docs/deployment.md` | Resolved: Change 11 ready (OP-7) |

---

## Resolved Open Points

### OP-1 — State Persistence Mechanism for Commit SHA — RESOLVED

**Decision**: Add a `cursor` JSONB column to the `FetcherRun` table.

**Rationale**: Each successful run stores its checkpoint (e.g.,
`{"sha": "abc123..."}`) in its own `cursor` column. The next run queries
the cursor of the last successful run for the same fetcher. This
provides:

- Audit trail for free (SHA progression visible in run history)
- No new table needed
- Clean semantics: "every run produces a cursor for the next run"
- Generic: works for SHA, timestamp, offset, or any future cursor type

**Rejected alternatives**:
- Option A (`FetcherConfig.cursor`): mixes runtime state with
  admin-managed configuration
- Option B (dedicated `FetcherState` table): overhead of a new table
  for what is essentially one value per fetcher per run
- Option C (`_`-prefix in `custom_settings`): hacky convention, confuses
  admin and runtime concerns

---

### OP-2 — Git Clone Base Directory Configuration — RESOLVED

**Decision**: Environment variable `GIT_CLONE_BASE_DIR` with default
`/var/lib/sentinel/git`. Each git-based fetcher creates its own
subdirectory automatically (e.g., `cvelistV5/`, `vulns.git/`).

A new section "Git-Based Fetchers" in `configuration.md` documents this.

---

### OP-8 — Worker Affinity Mechanism — RESOLVED

**Decision**: Dedicated Celery queue (`git`). Workers with the Git
volume mounted consume from this queue. Git-based fetcher tasks are
routed to this queue via task configuration.

**Rationale**: This is a standard Celery pattern, works in
Docker/Podman (dedicated worker service) and K8s (Deployment with PVC),
and requires no hostname knowledge or dynamic routing.

---

### OP-9 — Cursor Write Policy on `partial` Status — RESOLVED

**Decision**: Write cursor on `partial` status. The cursor query changes
from `WHERE status = 'success'` to
`WHERE status IN ('success', 'partial')`.

**Rationale**: for git-based fetchers, failed files are not permanently
lost — they reappear in a future delta when upstream modifies them.
Persistent failures indicate parser bugs (require code fixes, not
infinite retry). The alternative (ever-growing reprocessing loop) is
operationally worse. Diagnostics are already covered by `items_failed`
in the dashboard + per-CVE WARNING logs in the worker output.

**Rejected alternatives**:
- Option B (threshold-based): arbitrary threshold, same fundamental
  trade-off as (A) with added complexity
- Option C (cursor + failed_items tracking): adds complexity to
  BaseFetcher for a problem that git's change-tracking resolves
  naturally
- Option D (commit-by-commit cursor): changes algorithm structure,
  has the same stuck-at-failure problem at commit granularity

---

### OP-10 — Large Delta Handling and Intermediate Checkpointing — RESOLVED

**Decision**: Two distinct strategies based on context:

- **First run** (no clone directory): clone + record HEAD only, zero
  processing (already decided in OP-4)
- **Recovery** (SHA unreachable after re-clone): process a time-bounded
  window (last 2 weeks of changes from HEAD). Implementation:
  `git rev-list -1 --before="2 weeks ago" HEAD` to find the boundary
  commit, then `git diff --name-only <boundary_sha>..HEAD` for the
  file list

**Rationale**:
- 2 weeks of MITRE changes ≈ 700–2800 files (50–200 modifications/day).
  At ~100ms/file → 70–280 seconds. Well within the 1-hour timeout
- The vast majority of upserts will be no-ops (data already ingested
  before the volume loss). `upsert_cve()` is idempotent, CVSS no-op
  short-circuit prevents spurious recalculations, and
  `ensure_ticket_operable()` blocks mutations on Ignored/Duplicated
  tickets
- The "2 weeks" window is hardcoded (not configurable) — recovery is
  an exceptional event, not an operational parameter. If a gap exceeds
  2 weeks, manual intervention is required
- With OP-9 (cursor on partial), if the recovery run has some failures,
  the cursor still advances — preventing a retry loop

**Rejected alternatives**:
- Option A (commit-by-commit): excessive complexity for normal
  operation, many more git invocations
- Option B (batch checkpointing): complex cursor format, marginal
  benefit given bounded recovery window
- Option C (longer timeout): doesn't solve OOM, ties up worker
- Option D (record HEAD only): silently loses CVEs in the gap

---

### OP-12 — Git Error Classification (Transient vs Corruption vs Per-File) — RESOLVED

**Decision**: Option (C) — conservative two-tier classification based
on the operation phase, not on stderr/exit code parsing.

Classification rules:

| Phase | Failure condition | Classification | Action |
|-------|-------------------|----------------|--------|
| `git fetch` | Any failure | **Transient** (`GitFetchError`) | Do NOT delete clone. Run fails with `FetcherError`. Next cycle retries |
| Read operations after successful fetch (`git diff`, `git rev-parse`, `git ls-tree`, `git cat-file`) | Any failure | **Corruption** (`GitCorruptionError`) | Delete clone directory. Run fails. Next cycle re-clones |
| `git show` during delta file processing | Any failure (timeout, missing blob) | **Per-file** (`GitFileError`) | `record_failed()` for that CVE. Continue to next file |

Exception hierarchy in `git_operations.py`:

```python
class GitError(Exception): ...
class GitFetchError(GitError): ...       # Transient — clone is intact
class GitCorruptionError(GitError): ...  # Delete + re-clone required
class GitFileError(GitError): ...        # Per-file — continue processing
```

Fetcher handling:

- `GitFetchError` → log WARNING, raise `FetcherError("Failed to fetch
  from remote")`. Clone not touched. Admin sees `failure` in dashboard
- `GitCorruptionError` → log WARNING with error details, delete entire
  clone directory, raise `FetcherError("Repository corruption detected,
  clone deleted")`. Next run will re-clone + apply recovery strategy
  (OP-10)
- `GitFileError` → log WARNING with CVE-ID and error, call
  `record_failed()`, continue to next file in delta

**Rationale**:

- No dependency on git error messages (unstable across versions and
  locales). Classification is purely phase-based
- The "con" (corruption undetected during a successful fetch) is
  irrelevant: the very next read operation in the same run will catch
  it — at most a few seconds delay, not a missed detection
- No anti-loop logic needed: Celery task timeout limits each run's
  duration, and repeated `failure` runs are visible in the fetcher
  dashboard for operator intervention
- If a corruption loop occurs (e.g., faulty disk causes repeated
  corruption after re-clone), the admin sees consecutive failures +
  WARNING logs and addresses the root cause (hardware/storage)

**Rejected alternatives**:
- Option A (exit code + stderr parsing): fragile, requires ongoing
  maintenance as git evolves, locale-dependent error messages
- Option B (heuristic): same fragility as (A) but with less precision

---

### OP-3 — Formal Custom Settings Table — RESOLVED (not applicable)

**Decision**: The `shallow_since_days` custom setting is no longer needed.

**Rationale**: The bare clone approach (full commit history, no
`--shallow-since`) eliminates the need for a shallow window parameter.
The first-run strategy is "record HEAD only" (no historical processing),
so there is no window to configure. The MITRE fetcher has no custom
settings — the properties table row becomes `Custom settings | No`.

---

### OP-4 — First-Run Batch Processing Strategy — RESOLVED (not applicable)

**Decision**: The first-run strategy is "record HEAD without processing
any files" (same as `sync_kernel_cves`). There is no batch processing
on first run.

**Rationale**: Sentinel starts tracking CVEs from the moment of
deployment. Historical CVEs are accessible on-demand via
`fetch_single()`. Since the first run does not process any files, the
questions about transaction boundaries, memory management, abort
conditions, and progress persistence do not apply to the first run.

For **subsequent runs**, the batch processing behavior is already
well-defined: process each changed file individually via
`cve_service.upsert_cve()`, following the common CVE fetcher error
handling pattern (continue on failure, call `record_failed()`, no abort).
The cursor (HEAD SHA) is written after `execute()` returns — regardless
of whether some individual files failed (status `partial`) or all
succeeded (status `success`). If `execute()` raises an exception (status
`failure`), no cursor is written and the next run reprocesses the entire
delta (idempotent). See OP-9 for the full rationale.

---

### OP-5 — CVE JSON 5.x Field Path Mapping — RESOLVED

**Decision**: Hybrid approach (C) — keep the existing textual algorithm
description (explains logic and intent) and add a formal field path
mapping table as a reference appendix in the same section. The table
serves as a testable contract.

**Key findings from data investigation**:

- All files in cvelistV5 are `dataVersion` 5.1 or 5.2. No older
  versions exist (the repo was created in 2022 converting all
  historical CVEs to 5.x format)
- 5.2 is backward-compatible with 5.1: adds only `packageURL`
  (optional) and `cpeApplicability`. The fields Sentinel extracts are
  structurally identical across both versions
- No schema-version branching is needed in the parser
- Legacy CVEs (pre-2020) often have `vendor: "n/a"`, `product: "n/a"`,
  no `title`, and no `metrics` — the parser must handle absent fields
  gracefully

**Sentinel value normalization rule** (applied in the fetcher parser,
before constructing `CVEIngestPayload`):

- `affected[]` entries where both `vendor` and `product` are sentinel
  values (`"n/a"` or empty string) MUST be skipped entirely (no
  `AffectedVersionEntry` created)
- Individual sentinel values in `vendor`, `product`, or `version`
  fields are normalized to NULL before storage
- This normalization addresses known legacy data from the CVE 4.0 →
  5.x migration. If additional sentinel patterns are discovered in
  production, the rule should be extended accordingly

---

### OP-6 — CISA-ADP Container Identification — RESOLVED

**Decision**:

- **Identification**: the CISA ADP container is identified by
  `title == "CISA ADP Vulnrichment"` (top-level field of the ADP
  entry in `containers.adp[]`)
- **`source_container` value**: constructed as
  `f"adp:{providerMetadata.shortName}"` → `"adp:CISA-ADP"`

**Rationale**: both `title` and `providerMetadata.shortName` are
present and stable across all CISA entries observed. Using `title` for
identification is consistent with how the spec already describes it
(line 541). Using `shortName` for the `source_container` string
produces a concise, stable identifier suitable for DB storage and
scope-keying.

**Confirmed from real data** (CVE-2024-0012, CVE-2025-31272):
- `containers.adp[0].title` = `"CISA ADP Vulnrichment"`
- `containers.adp[0].providerMetadata.shortName` = `"CISA-ADP"`
- SSVC data at: `metrics[?other.type=="ssvc"].other.content.options[]`
- KEV data at: `metrics[?other.type=="kev"].other.content`

---

### OP-7 — Git Library Choice — RESOLVED

**Decision**: Raw subprocess invocation of the system `git` binary,
via async subprocess through a shared internal helper. No Python Git
library is used.

**Minimum requirement**: `git >= 2.25` in the container image of the
worker that consumes the `git` queue.

**Hardcoded timeouts per operation category**:

| Operation | Timeout | Examples |
|---|---|---|
| Clone | 20 minutes | Initial bare clone (~300 MB download) |
| Fetch | 5 minutes | Incremental `git fetch origin` |
| Read | 30 seconds | `git show`, `git log`, `git ls-tree`, `git rev-parse` |

The helper provides typed errors distinguishing clone failures, fetch
failures, and read failures.

**Eliminated alternatives**:

- **`pygit2`** (libgit2 bindings): libgit2 cannot open repositories
  with the `extensions.partialclone` extension. Issue
  libgit2/libgit2#5564 "Support for partial clones" has been open
  since June 2020 with no progress. Issue #6880 (Sep 2024) confirms
  that libgit2 v1.7.2 errors with "unsupported extension name
  extensions.partialclone" when attempting to open any repo created
  with `--filter=blob:none`. This means pygit2 cannot even READ a
  partial clone, regardless of how it was created. Deal-breaker.

- **`GitPython`**: 8 published security advisories on GitHub, of
  which 5 are High-severity RCE/command-injection vulnerabilities
  published April–May 2026 affecting ALL platforms (not
  Windows-specific):
  - GHSA-mv93-w799-cj2w: RCE via newline injection (May 2026)
  - GHSA-v87r-6q3f-2j67: RCE via newline injection (Apr 2026)
  - GHSA-7545-fcxq-7j24: path traversal — file write/delete
    outside repo (Apr 2026)
  - GHSA-x2qx-6953-8485: unsafe option check bypass (Apr 2026)
  - GHSA-rpm5-65cw-6hj4: command injection via options (Apr 2026)
  Unacceptable for a security platform.

- **Raw subprocess**: no additional Python dependency, full access to
  all git features (partial clone, protocol v2), no additional attack
  surface. The operations are few (clone, fetch, rev-parse, diff,
  show, ls-tree) with trivially parsable output.

**Container dependency note**: the `python:3.12-slim` base image does
not include git — it must be added explicitly to the container image.

**Affected files**: `cve-tracking.md` (remove "or equivalent library
calls" ambiguity), `fetcher-infrastructure.md` (Runtime Dependencies
subsection), `deployment.md` (git in prerequisites).

---

## Open Points

### OP-11 — Cursor Preservation Across Retention Window — RESOLVED

**Status**: RESOLVED

**Decision**: The aggregation task no longer exists. `FetcherRun` records
are retained indefinitely, eliminating the cursor-loss risk entirely. For
fetchers disabled for extended periods, the cursor is preserved in their
most recent `FetcherRun` record for as long as the record exists
(forever). No additional mechanism is needed.

---

### OP-13 — Pre-Application Review Gate — OPEN

**Problem**: Changes 1–16 modify 7 specification files simultaneously.
Applying them without review risks introducing contradictions with
existing spec content, violating conventions, or missing edge cases
that the drafting process did not surface.

**Required reviews before applying changes**:

- `@spec-coherence-reviewer`: verify that the planned changes do not
  contradict existing specs (especially `fetcher-infrastructure.md`,
  `cve-tracking.md`, `cve-service.md`, `data-model.md`)
- `@docs-placement-reviewer`: verify that the shared vs. fetcher-specific
  split (Change 1 vs Change 6) places each rule in the correct location
- `@spec-gap-analyzer`: run on the final `cve-tracking.md` content (after
  Changes 6, 7, 9, 10, 12, 13, 14 are mentally applied) to identify any
  remaining functional gaps

**Execution**: run reviewers on a per-change basis or on the target
files after all changes are applied (whichever is more practical).
Address any "Needs revision" findings before considering the spec
updates final.

---

## Planned Changes

Changes to apply to spec files once all open points are resolved.
Applied in the order listed below to maintain coherence.

### Change 1: `fetcher-infrastructure.md` — Add "Git-Based Fetchers" section

**Insert after**: the "Custom Settings Schema" section (line ~1107,
before "Fetcher Documentation Requirements").

**Content**:

```markdown
## Git-Based Fetchers

Some fetchers synchronize data from external Git repositories rather
than HTTP APIs. These fetchers share common infrastructure requirements
documented in this section. Individual fetcher specs define their own
algorithm, metrics, and source-specific behavior; this section defines
only the shared operational pattern.

Current git-based fetchers: `sync_mitre_cves`, `sync_kernel_cves`.

### Bare Clone Pattern

Git-based fetchers use **bare clones without a working tree**. This
minimizes disk usage (no checkout of hundreds of thousands of files)
while providing full access to file contents via Git object store
operations.

The pattern:

1. **Clone** (first run only): `git clone --bare --single-branch <url>`
   into `$GIT_CLONE_BASE_DIR/<subdirectory>/`. For sources that support
   Git partial clone (protocol v2 with `filter` capability), add
   `--filter=blob:none` to defer blob downloads. For sources that do not
   support filtering (e.g., `git.kernel.org`), use a plain bare clone.
2. **Fetch** (subsequent runs): `git fetch origin` updates refs and
   downloads new objects. This is incremental and typically completes in
   seconds.
3. **Delta detection**: `git diff --name-only <old_sha>..<new_sha>`
   returns the exact list of changed files.
4. **File content access**: `git show <ref>:<path>` reads a single
   file's content from the object store without creating a working tree.
   For blobless clones, this triggers an on-demand blob download for
   that specific file only.
5. **First-run file enumeration**: `git ls-tree -r --name-only HEAD`
   lists all files in the repository without checkout.

No `git merge`, `git checkout`, or working tree manipulation is
performed at any point.

### Cursor Persistence

Git-based fetchers persist their checkpoint (the last successfully
processed commit SHA) in the `FetcherRun.cursor` JSONB column. After
a successful run, the fetcher writes:

```json
{"sha": "<40-char hex SHA>"}
```

The next run reads the cursor from the most recent `FetcherRun` with
`status IN ('success', 'partial')` for the same `fetcher_name`. If no
run with a cursor exists (first run), the fetcher applies its own
first-run strategy (see the individual fetcher spec — e.g., "record
HEAD only" for CVE fetchers). For recovery scenarios where a stored
SHA is unreachable, the fetcher applies its time-bounded recovery
strategy (see Recovery below).

This mechanism is generic — non-git fetchers may use `cursor` for any
checkpoint data (timestamps, offsets, page tokens). The column is
nullable; fetchers that derive their cursor from other fields (e.g.,
NVD uses `started_at`) leave it NULL.

#### Write Mechanism

Inside `execute()`, the fetcher sets `self._cursor` (a dict) with the
checkpoint data. After `execute()` returns, `run()` reads
`self._cursor` during finalization and writes it to the `FetcherRun`
row in the same transaction that sets `status` and `finished_at`.
If `self._cursor` is None (not set), no cursor is written.

This avoids giving `execute()` direct access to the `FetcherRun` row
and keeps cursor persistence as a `run()` responsibility — consistent
with how `run()` already manages metrics (`items_created`,
`items_updated`, `items_failed`).

#### Empty Delta

If `git fetch` succeeds but the delta contains zero files matching
the fetcher's filter (no CVE files changed), the run completes with
`status = success`, zero metrics, and the cursor advances to the new
HEAD SHA. This is the normal case during low-activity periods.

### Environment Configuration

| Env Var | Type | Default | Description |
|---------|------|---------|-------------|
| `GIT_CLONE_BASE_DIR` | string (path) | `/var/lib/sentinel/git` | Base directory for all git-based fetcher clones |

Each fetcher creates a subdirectory named after its repository:

```
$GIT_CLONE_BASE_DIR/
├── cvelistV5/      (sync_mitre_cves — bare clone of github.com/CVEProject/cvelistV5)
└── vulns.git/      (sync_kernel_cves — bare clone of git.kernel.org/.../vulns.git)
```

The base directory MUST be backed by persistent storage in containerized
deployments (named volume in Docker/Podman, PersistentVolumeClaim in
Kubernetes). The storage is treated as a **recoverable cache**, not as a
source of truth — if lost or corrupted, the fetcher re-clones
automatically (see Recovery below).

### Volume Requirements

| Property | Value |
|----------|-------|
| Persistence | Required across container restarts |
| Capacity | 1 GB minimum (current usage ~400 MB; provides headroom for growth and transient git operations) |
| Access mode | ReadWriteOnce (single worker pod) |
| Filesystem | Any POSIX-compliant filesystem |
| Backup | Not required (recoverable from upstream repos) |

### Worker Affinity

Git-based fetcher tasks MUST execute on a Celery worker with the Git
volume mounted. This is achieved via a dedicated Celery queue:

- **Queue name**: `git`
- **Routing**: git-based fetcher tasks declare
  `queue = "git"` in their task configuration
- **Worker configuration**: the worker process with access to the Git
  volume consumes from the `git` queue (in addition to the default
  queue, if desired)
- **`fetch_single()` routing**: on-demand single-item fetches for
  git-based fetchers are also routed to the `git` queue, since they
  require object store access

In single-worker deployments (local dev, simple Docker/Podman), all
queues are consumed by the same worker process and no explicit routing
configuration is needed.

### Concurrency Rules

These rules apply to ALL git-based fetchers sharing the same volume:

1. **Only the periodic sync modifies the clone**: `git fetch`, `git gc`,
   and any other write operations are performed exclusively by the
   periodic sync task. `fetch_single()` MUST NOT run `git fetch` or any
   operation that modifies the object store or refs.
2. **`fetch_single()` reads from the object store only**: uses
   `git show <ref>:<path>` (via async subprocess) to read committed
   objects. The Git object store is append-only with atomic file
   operations — concurrent reads during a `git fetch` are safe.
3. **Stale reads are acceptable**: if `fetch_single()` reads HEAD just
   before `git fetch` updates it, a recently-published CVE might not be
   found. This is not an error — `trigger_on_demand_fetch()` dispatches
   all registered fetchers and other sources may succeed.
4. **No concurrent fetches per repo**: two periodic sync tasks for the
   same repository MUST NOT run concurrently. The fetcher infrastructure
   already enforces this via the singleton execution guarantee
   (BaseFetcher prevents overlapping runs for the same fetcher).
5. **Cross-fetcher concurrency is safe**: different git-based fetchers
   operating on distinct subdirectories within `$GIT_CLONE_BASE_DIR`
   MAY execute concurrently. The singleton constraint — no overlapping
   runs of the same fetcher — is enforced by `BaseFetcher` (see
   "BaseFetcher Base Class" above). It applies per-fetcher, not
   per-volume. A `sync_mitre_cves` run and a `sync_kernel_cves` run
   can overlap without conflict.

### Recovery

**Volume loss** (directory does not exist):

1. Re-clone the repository (same clone command as first run)
2. Read the `cursor` from the last `FetcherRun` with
   `status IN ('success', 'partial')` for this fetcher in the database
3. Check if the stored SHA exists in the new clone
   (`git cat-file -t <sha>`)
4. If reachable: normal delta processing from stored SHA to HEAD
5. If not reachable (upstream force-push, branch deletion, or SHA
   garbage-collected): apply the fetcher's time-bounded recovery
   strategy. Each fetcher defines its own recovery window and file
   filter (see the individual fetcher spec). The shared infrastructure
   provides only the detection mechanism (`git cat-file -t`) and the
   re-clone procedure; the recovery delta policy is fetcher-specific

**Corrupted clone** (git operations fail with corruption errors):

1. Log WARNING with the error details
2. Delete the entire clone directory
3. Re-clone (same as volume loss recovery)

**Git garbage collection**: `git gc --auto` as a post-fetch step. For
bare repos, this is lightweight and runs only when git determines it is
needed.

### Runtime Dependencies

Git-based fetchers require the `git` binary available in the
container image of the worker that consumes the `git` queue.

| Dependency | Minimum version | Reason |
|---|---|---|
| `git` | 2.25 | First stable release with partial clone (`--filter`) support. Required for blobless clones of cvelistV5 |

The `python:3.12-slim` base image does not include git — it must be
added explicitly to the container image.

**No Python Git library is used.** All git operations are performed
via async subprocess invocation of the system `git` binary through a
shared internal helper. This decision is based on:

- `pygit2` (libgit2 bindings): **eliminated** — libgit2 cannot open
  repositories with the `extensions.partialclone` extension
  (libgit2/libgit2#5564, open since Jun 2020; #6880 confirms the
  error persists in v1.7.2, Sep 2024). Unusable with blobless clones
- `GitPython`: **eliminated** — 8 security advisories including 5
  High-severity RCE/command-injection vulnerabilities published
  April–May 2026 affecting all platforms. Unacceptable for a security
  platform
- Raw subprocess: no additional Python dependency, full access to all
  git features (partial clone, protocol v2), no additional attack
  surface

The helper provides typed exceptions for phase-based error
classification (see "Error Classification" below), with hardcoded
timeouts per operation category:

| Operation | Timeout | Examples |
|---|---|---|
| Clone | 20 minutes | Initial bare clone (~300 MB download) |
| Fetch | 5 minutes | Incremental `git fetch origin` |
| Read | 30 seconds | `git show`, `git log`, `git ls-tree`, `git rev-parse` |

### Error Classification

Git operation failures are classified by the **phase** in which they
occur, not by parsing exit codes or stderr messages. This avoids
fragile dependencies on git's unstable error message format.

```python
class GitError(Exception): ...
class GitFetchError(GitError): ...       # Transient — clone is intact
class GitCorruptionError(GitError): ...  # Delete + re-clone required
class GitFileError(GitError): ...        # Per-file — continue processing
```

| Phase | Failure condition | Exception | Fetcher action |
|-------|-------------------|-----------|----------------|
| `git clone` / `git fetch` | Any failure (network, auth, timeout) | `GitFetchError` | Do NOT delete clone. Raise `FetcherError`. Next cycle retries |
| Read after successful fetch (`git diff`, `git rev-parse`, `git ls-tree`, `git cat-file -t`) | Any failure | `GitCorruptionError` | Delete clone directory. Raise `FetcherError`. Next cycle re-clones + applies recovery strategy |
| `git show` during delta file processing | Any failure (timeout, missing blob) | `GitFileError` | `record_failed()` for that item. Continue to next file |

**Design rationale**: classification is purely phase-based because a
successful `git fetch` proves network connectivity. If a subsequent
read operation fails, the only remaining explanation is local
corruption. No stderr parsing or exit code mapping is needed.

**No anti-loop logic**: Celery task timeout limits each run's
duration. Repeated failures (e.g., corruption loop from faulty disk)
produce visible `failure` records in the fetcher dashboard for
operator intervention.
```

---

### Change 2: `fetcher-infrastructure.md` — Add `cursor` column to FetcherRun table

**Modify**: the FetcherRun table (line ~1365-1381). Add a new row after
`triggered_by_user_id`:

```markdown
| cursor               | JSONB       | nullable                 | Fetcher-defined checkpoint for the next run. Generic: may contain a commit SHA, timestamp, offset, page token, or any structured cursor. Written on successful completion; read by the next run to determine the starting point. See "Git-Based Fetchers" for the git-specific usage pattern |
```

Also add a note after the existing notes block (line ~1391):

```markdown
- `cursor` is written at the end of a successful or partial run and
  read at the start of the next run (query: last `FetcherRun` with
  `status IN ('success', 'partial')` for the same `fetcher_name`,
  ordered by `started_at DESC`, limit 1). Fetchers that derive their
  starting point from other columns (e.g., `started_at`) leave
  `cursor` NULL.
- The cursor value must be a JSON-serializable dict. `BaseFetcher.run()`
  validates via `json.dumps()` before writing; a non-serializable value
  raises `TypeError` and the run fails without persisting a cursor.
```

---

### Change 3: `data-model.md` — Add `cursor` column to FetcherRun table

**Modify**: the FetcherRun table (line ~1352-1368). Add a new row after
`triggered_by_user_id`:

```markdown
| cursor               | JSONB       | nullable                 | Fetcher-defined checkpoint for the next run (e.g., `{"sha": "..."}` for git-based fetchers). Written on successful completion; read by the next run to determine starting point. NULL for fetchers that derive cursors from other fields |
```

---

### Change 4: `data-model.md` — Add `cursor` to FetcherRun Mermaid diagram

**Modify**: the FetcherRun entity in the Mermaid ER diagram (line ~325).
Add `cursor` to the entity fields.

---

### Change 5: `configuration.md` — Add "Git-Based Fetchers" section

**Insert after**: the "External APIs" section (line ~115), before
"Application".

**Content**:

```markdown
## Git-Based Fetchers

| Env Var | Type | Default | Description | Defined in |
|---------|------|---------|-------------|------------|
| `GIT_CLONE_BASE_DIR` | string (path) | `/var/lib/sentinel/git` | Base directory for persistent bare clones used by git-based fetchers (`sync_mitre_cves`, `sync_kernel_cves`). Must be backed by persistent storage in containerized deployments | `docs/features/platform/fetcher-infrastructure.md` |
```

---

### Change 6: `cve-tracking.md` — Replace duplicated sections with references

The following sections in `cve-tracking.md` are currently duplicated
between the MITRE and kernel fetcher definitions. After the shared
pattern is documented in `fetcher-infrastructure.md`, these sections
should be replaced with cross-references.

**Section: "Git Concurrency Rules" (lines 592-611)**

Replace with:

```markdown
#### Git Concurrency Rules

See `docs/features/platform/fetcher-infrastructure.md`, "Git-Based
Fetchers — Concurrency Rules". Both `sync_mitre_cves` and
`sync_kernel_cves` follow the shared rules defined there.
```

**Section: "Storage and Recovery" (lines 612-635)**

Replace with:

```markdown
#### Storage and Recovery

See `docs/features/platform/fetcher-infrastructure.md`, "Git-Based
Fetchers" for shared volume requirements, recovery procedures, and
worker affinity rules.

**Disk space estimate**: ~300 MB (`cvelistV5` blobless bare clone) +
~91 MB (`vulns.git` full bare clone) = ~400 MB total. Provision per
the 1 GB minimum specified in `fetcher-infrastructure.md` (Volume
Requirements) to allow headroom for git pack files, transient
operations, and future growth.

**Recovery strategy (SHA unreachable)**: when the stored cursor SHA is
not reachable in a fresh clone (detected via `git cat-file -t <sha>`),
both `sync_mitre_cves` and `sync_kernel_cves` process the last 2 weeks
of changes:

1. `git rev-list -1 --before="2 weeks ago" HEAD` → boundary SHA
2. `git diff --name-only <boundary_sha>..HEAD -- '<file_filter>'`
   → file list (where `<file_filter>` is `cves/` for MITRE,
   `cve/published/` for kernel)
3. Process each file via `cve_service.upsert_cve()` (idempotent —
   previously ingested CVEs produce no observable side effects)
4. Write HEAD as cursor on completion

The 2-week window is hardcoded. If the gap exceeds 2 weeks, operator
intervention is required (manual `fetch_single()` for specific CVEs
or a full re-seed via operational tooling).
```

**Algorithm step 6 "State persistence" (line 552-554)**

Update to reference the cursor mechanism:

```markdown
6. **State persistence**: the HEAD commit SHA is written to the
   `FetcherRun.cursor` column as `{"sha": "<hex>"}` after successful
   or partial processing (see
   `docs/features/platform/fetcher-infrastructure.md`, "Git-Based
   Fetchers — Cursor Persistence"). On failure (exception in
   `execute()`), no cursor is written — the next run reprocesses the
   same delta.
```

**Algorithm step 1 — clone command (line 506)**

Update to reflect bare clone with "record HEAD only" first-run strategy:

```markdown
1. **First run** (clone directory does not exist):
   - `git clone --bare --filter=blob:none --single-branch`
     of `https://github.com/CVEProject/cvelistV5.git` into
     `$GIT_CLONE_BASE_DIR/cvelistV5/`. The `--bare` flag omits the
     working tree (no checkout of 357k+ files); the
     `--filter=blob:none` flag defers blob downloads to on-demand
     access via `git show`
   - Record HEAD commit SHA in `FetcherRun.cursor` without processing
     any files. Historical CVEs are not bulk-ingested (see "Common
     First Run Behavior" above)
```

**Properties table (line ~500)**: update `Custom settings` row:

```markdown
| Custom settings | No |
```

(Previously: `shallow_since_days` (default: 7) — no longer needed with
bare clone and "record HEAD only" first-run strategy.)

**Properties table (line ~497)**: update `Source` row:

```markdown
| Source | cvelistV5 GitHub repository (bare clone + fetch) |
```

(Previously: "Git clone/pull" — inconsistent with bare clone model.)

---

### Change 7: `cve-tracking.md` — Add "Common First Run Behavior" section

**Insert after**: "Common CVE Fetcher Error Handling" (line ~379),
before the individual fetcher sections.

**Content**:

```markdown
### Common First Run Behavior

All CVE fetchers are designed for **forward-only ingestion**: they
begin tracking from the moment of deployment and do not bulk-ingest
historical CVE data. On first run:

- `sync_nvd_cves`: fetches CVEs modified in the last 7 days
  (`now - 7 days`) as a bootstrap window, then proceeds incrementally
- `sync_mitre_cves`: clones the repository and records HEAD commit SHA
  without processing any files
- `sync_kernel_cves`: clones the repository and records HEAD commit SHA
  without processing any files

After the first successful run, each fetcher proceeds incrementally —
processing only changes since its last cursor.

**Historical CVE access**: individual historical CVEs are accessible
on-demand via `fetch_single()`. When a VA associates a historical
CVE-ID with a ticket, the on-demand fetch mechanism
(`trigger_on_demand_fetch()`) retrieves it from the source (NVD API
query, MITRE bare clone object store, or kernel bare clone object
store) and ingests it with the same `cve_service.upsert_cve()` path as
batch-processed CVEs.
```

---

### Change 8: `architecture.md` — Add cross-reference to CVE Ingestion Flow

**Modify**: "CVE Ingestion Flow" section (line ~220, after step 7).

**Add at the end of the numbered list**:

```markdown
See `docs/features/tickets/cve-tracking.md` for the full CVE ingestion
specification (fetcher algorithms, error handling, first-run strategy).
```

This aligns "CVE Ingestion Flow" with the other Data Flow sections
("Package Affectedness Flow" references `package-model.md`, "Release
Tracking Flow" references `ibs-track-release-detection.md`, etc.).

---

### Change 9: `cve-tracking.md` — Add CVE JSON 5.x Field Path Mapping

**Insert after**: the processing step (step 5) in the `sync_mitre_cves`
algorithm section, as a new subsection "#### CVE JSON 5.x Field Path
Mapping" (or as a reference table within the existing step 5 content).

**Content**:

```markdown
#### CVE JSON 5.x Field Path Mapping

The following table documents the exact JSON paths in CVE Record Format
5.x files (dataVersion 5.1 and 5.2, which are structurally identical
for the fields Sentinel extracts). The parser MUST handle absent fields
gracefully — legacy CVEs (migrated from format 4.0) often lack `title`,
`metrics`, and structured `affected` data.

##### Global CVE fields (from `cveMetadata`)

| CVEIngestPayload field | JSON path | Optional | Notes |
|---|---|---|---|
| `cve_state` | `cveMetadata.state` | No | `"PUBLISHED"` or `"REJECTED"` |
| `published_date` | `cveMetadata.datePublished` | Yes | ISO 8601 datetime |
| `modified_date` | `cveMetadata.dateUpdated` | Yes | ISO 8601 datetime |

##### CNA container fields (from `containers.cna`)

| CVEIngestPayload field | JSON path | Optional | Notes |
|---|---|---|---|
| `title` | `containers.cna.title` | Yes | Absent in most legacy CVEs |
| `description` | `containers.cna.descriptions[].value` | Yes | Select first entry where `lang == "en"`; fallback to first entry regardless of `lang` |
| `cvss_assessments` | `containers.cna.metrics[].cvssV3_1.vectorString` | Yes | Each entry in `metrics[]` MAY contain one `cvssV3_1` and/or one `cvssV4_0` key. Extract `vectorString` from each. `provider_name` = CNA shortName (`containers.cna.providerMetadata.shortName`) |
| `cvss_assessments` | `containers.cna.metrics[].cvssV4_0.vectorString` | Yes | Same pattern as 3.1. Both can coexist in the same `metrics[]` array |
| `cwe_classifications` | `containers.cna.problemTypes[].descriptions[].cweId` | Yes | Filter entries where `type == "CWE"`. `source` = `"cna:{shortName}"` |
| `affected_versions` | `containers.cna.affected[]` | Yes | See affected[] extraction below |
| (references) | `containers.cna.references[]` | Yes | Each entry has `.url` and optionally `.tags[]`. Passed to `reference_service` |

##### ADP container fields (from `containers.adp[]`)

For **each** entry in the `containers.adp[]` array:

| CVEIngestPayload field | JSON path (relative to ADP entry) | Notes |
|---|---|---|
| (identification) | `.title` | Used to identify the container and construct `source_container` |
| `source_container` | `f"adp:{.providerMetadata.shortName}"` | e.g., `"adp:CISA-ADP"`, `"adp:CVE"` |
| `cvss_assessments` | `.metrics[].cvssV3_1.vectorString` / `.cvssV4_0.vectorString` | `provider_name` = `source_container` value |
| `affected_versions` | `.affected[]` | Same extraction as CNA, with ADP's `source_container` |

##### CISA-ADP specific fields

The CISA container is identified by `title == "CISA ADP Vulnrichment"`
(confirmed: `providerMetadata.shortName == "CISA-ADP"`). In addition
to the common ADP fields above:

| CVEIngestPayload field | JSON path (relative to CISA ADP entry) | Notes |
|---|---|---|
| `ssvc_assessment.exploitation` | `.metrics[?other.type=="ssvc"].other.content.options[?Exploitation].Exploitation` | Enum: `"none"`, `"poc"`, `"active"` |
| `ssvc_assessment.automatable` | `.metrics[?other.type=="ssvc"].other.content.options[?Automatable].Automatable` | Enum: `"no"`, `"yes"` |
| `ssvc_assessment.technical_impact` | `.metrics[?other.type=="ssvc"].other.content.options[?"Technical Impact"]["Technical Impact"]` | Enum: `"partial"`, `"total"` |
| `ssvc_assessment.version` | `.metrics[?other.type=="ssvc"].other.content.version` | e.g., `"2.0.3"` |
| `ssvc_assessment.assessed_at` | `.metrics[?other.type=="ssvc"].other.content.timestamp` | ISO 8601 datetime |
| `kev_data.date_added` | `.metrics[?other.type=="kev"].other.content.dateAdded` | ISO 8601 date |
| `kev_data.reference_url` | `.metrics[?other.type=="kev"].other.content.reference` | URL string |
| `cwe_classifications` | `.problemTypes[].descriptions[].cweId` | If present. `source` = `"adp:CISA-ADP"` |

##### `affected[]` extraction (CNA and ADP)

Each entry in the `affected[]` array represents one product. For each
product, iterate `versions[]` to produce one `AffectedVersionEntry` per
version range:

| AffectedVersionEntry field | JSON path (relative to `affected[]` entry) | Notes |
|---|---|---|
| `vendor` | `.vendor` | Normalize: `"n/a"` or `""` → NULL |
| `product` | `.product` | Normalize: `"n/a"` or `""` → NULL |
| `default_status` | `.defaultStatus` | Product-level fallback |
| `version` | `.versions[].version` | Normalize: `"n/a"` or `""` → NULL |
| `version_type` | `.versions[].versionType` | Open set: `"semver"`, `"git"`, `"custom"`, etc. |
| `version_end` | `.versions[].lessThan` or `.versions[].lessThanOrEqual` | Use `lessThan` if present, else `lessThanOrEqual` |
| `version_end_inclusive` | — | `true` if `lessThanOrEqual` was used, `false` if `lessThan` |
| `status` | `.versions[].status` | `"affected"`, `"unaffected"`, `"unknown"` |
| `cpe` | `.cpes[]` (first entry) | If present (5.2+ CNA-provided CPE) |
| `package_url` | `.packageURL` | 5.2 only; optional PURL identifier |
| `repo` | `.repo` | If present (e.g., Git repo URL for kernel) |
| `program_files` | `.programFiles` | If present |
| `source_container` | (inherited from parent container) | `"cna"` for CNA, `f"adp:{shortName}"` for ADP |

**Sentinel value normalization**: entries in the `affected[]` array
where both `vendor` and `product` are sentinel values (`"n/a"` or
empty string) MUST be skipped entirely (no `AffectedVersionEntry`
created). Individual sentinel values in `vendor`, `product`, or
`version` fields are normalized to NULL before storage. This
normalization addresses known legacy data from the CVE 4.0 → 5.x
migration. If additional sentinel patterns are discovered in
production, the rule should be extended accordingly.

**Schema version note**: all files in cvelistV5 use `dataVersion` 5.1
or 5.2. These versions are structurally identical for the fields
Sentinel extracts (5.2 adds `packageURL` and `cpeApplicability`, both
optional and handled above). No schema-version branching is needed in
the parser.

**Empty `versions[]` handling**: if an `affected[]` entry has no
`versions` key or an empty `versions[]` array, create one
`AffectedVersionEntry` with NULL version fields (`version`,
`version_end`, `version_type` all NULL), using the product-level
`defaultStatus` as the entry's `status`. This handles legacy CVEs
where products are declared affected without version granularity.

**CVE-ID cross-validation**: the parser extracts the CVE-ID from the
filename path (e.g., `cves/2026/0xxx/CVE-2026-0123.json` →
`CVE-2026-0123`). If `cveMetadata.cveId` in the file content differs
from the filename-derived ID, log WARNING with both values and use the
**filename-derived ID** as authoritative (the filename reflects the
canonical cvelistV5 repository structure maintained by MITRE
automation). The mismatched file is still processed — it is not
skipped.
```

---

### Change 10: `cve-tracking.md` — Clarify CISA-ADP identification in algorithm step 5

**Modify**: algorithm step 5, bullet point about CISA-ADP (line ~541).

**Replace**:
```
- CISA-ADP container (the ADP entry where
  `title == "CISA ADP Vulnrichment"`): in addition to the common
  ADP data above, extract CISA-specific enrichment:
```

**With**:
```markdown
- CISA-ADP container (the ADP entry where
  `title == "CISA ADP Vulnrichment"`;
  `providerMetadata.shortName == "CISA-ADP"`;
  `source_container = "adp:CISA-ADP"`): in addition to the common
  ADP data above, extract CISA-specific enrichment:
```

---

### Change 11: `deployment.md` — Add git dependency and git worker

**Modify**: "Software Requirements" table (line ~16). Add a new row:

```markdown
| Git | 2.25+ | Git-based CVE fetcher operations (git worker container only) |
```

**Modify**: "Process Architecture" table (line ~272). Add a new row:

```markdown
| Git worker (Celery) | Background git-based fetcher execution | No (single volume affinity) |
```

**Modify**: "Network Access" table (line ~28). Add rows for git clone
sources:

```markdown
| GitHub | `github.com` | 443 | MITRE cvelistV5 repository clone/fetch |
| git.kernel.org | `git.kernel.org` | 443 | Linux kernel vulnerability repo clone/fetch |
```

**Add after** "Process Architecture" section (line ~284), before
"Health Checks":

```markdown
### Git Worker Volume

The git worker requires a persistent volume mounted at
`$GIT_CLONE_BASE_DIR` (default: `/var/lib/sentinel/git`). This volume
stores bare clones of external git repositories used by CVE fetchers.

| Property | Value |
|----------|-------|
| Minimum capacity | 1 GB |
| Access mode | ReadWriteOnce (single worker) |
| Backup | Not required — recoverable cache (fetchers re-clone if lost) |

Bare clones have no working tree — accidental checkout expansion
(which could consume ~4 GB for cvelistV5 alone) is structurally
impossible.

See `docs/features/platform/fetcher-infrastructure.md` (Git-Based
Fetchers) for volume layout, recovery procedures, and worker affinity
configuration.
```

---

### Change 12: `cve-tracking.md` — Remove git library ambiguity

**Modify**: any occurrence of "git show / git cat-file or equivalent
library calls" in the `sync_mitre_cves` and `sync_kernel_cves`
sections.

**Replace** phrasing like:

```
git show / git cat-file or equivalent library calls
```

**With**:

```markdown
`git show` (via async subprocess invocation of the system `git` binary)
```

This removes the ambiguity about which git access method is used.
The decision rationale is documented in
`fetcher-infrastructure.md` (Git-Based Fetchers — Runtime
Dependencies).

---

### Change 13: `cve-tracking.md` — Update `sync_kernel_cves` cross-reference

After Change 6 replaces the `sync_mitre_cves` "Git Concurrency Rules"
and "Storage and Recovery" sections with a pointer to
`fetcher-infrastructure.md`, the existing `sync_kernel_cves`
cross-reference (line ~732-733) becomes a stale double-hop
(`kernel → mitre → infra`). Update it to point directly to the shared
section.

**Replace** (line ~732-733):

```
Git concurrency rules, storage/recovery strategy, and worker affinity
are shared with `sync_mitre_cves` (see above).
```

**With**:

```markdown
Git concurrency rules, storage/recovery strategy, and worker affinity
follow the shared pattern defined in
`docs/features/platform/fetcher-infrastructure.md` (Git-Based
Fetchers). Both `sync_kernel_cves` and `sync_mitre_cves` share the
same volume, queue, and concurrency constraints.
```

---

### Change 14: `cve-tracking.md` — Fix `git pull`/`git merge` references for bare clone consistency

Both fetchers use bare clones (no working tree), which means `git pull`
and `git merge` are structurally impossible. These stale references
must be corrected.

**A) `sync_mitre_cves` step 2 (line ~516)**

**Replace**:

```
2. **Subsequent runs**: `git fetch` + `git merge` (or `git pull`). Git
   transfers only the delta since the last fetch — typically a few
   hundred KB for a 6-hour window
```

**With**:

```markdown
2. **Subsequent runs**: `git fetch origin`. Git transfers only new
   objects since the last fetch — typically a few hundred KB for a
   6-hour window. Bare clones have no working tree; there is no merge
   or checkout step
```

**B) `sync_kernel_cves` step 2 (line ~687)**

**Replace**:

```
2. **Subsequent runs**: `git pull`, compute delta
   (`stored_commit.diff(HEAD)`), filter for files in
   `cve/published/YEAR/`
```

**With**:

```markdown
2. **Subsequent runs**: `git fetch origin`, compute delta
   (`git diff --name-only <stored_sha>..HEAD`), filter for files in
   `cve/published/YEAR/`
```

**C) `sync_mitre_cves` "Sync mechanism rationale" (line ~563-568)**

**Replace**:

```
**Sync mechanism rationale**: Git clone/pull is production-proven in the
SUSE ecosystem (SMASH uses the same pattern for its `CVElistV5Fetcher`)
and at scale (OSV.dev/Google). It provides maximum freshness (~7 min from
CNA publish to `git pull` availability), deterministic delta (no phantom
reads or duplicates), natural recovery after downtime, and no rate limits
or authentication. Infrastructure is shared with `sync_kernel_cves`.
```

**With**:

```markdown
**Sync mechanism rationale**: bare clone + fetch is production-proven in
the SUSE ecosystem (SMASH uses the same pattern for its
`CVElistV5Fetcher`) and at scale (OSV.dev/Google). It provides maximum
freshness (~7 min from CNA publish to fetch availability),
deterministic delta (no phantom reads or duplicates), natural recovery
after downtime, and no rate limits or authentication. Infrastructure
is shared with `sync_kernel_cves`.
```

---

### Change 15: `fetcher-infrastructure.md` — Add implementation location note

Add a brief note at the end of the "Git-Based Fetchers" section
(Change 1 content) indicating the expected code location of the shared
git subprocess helper, so implementers know where to place it.

**Append at the end of the Change 1 content** (after the "Error
Classification" subsection):

```markdown

### Implementation Location

The shared async subprocess helper for git operations lives at
`backend/app/services/git_operations.py`. All git-based fetchers
import from this module — they MUST NOT invoke `subprocess` or
`asyncio.create_subprocess_exec` for git commands directly.

The module exports:
- Async functions for each git operation category (clone, fetch, read
  operations, show)
- The exception hierarchy (`GitError`, `GitFetchError`,
  `GitCorruptionError`, `GitFileError`)
- Timeout constants per operation category
```

---

### Change 16: `cve-tracking.md` + `cve-service.md` — Fix `source_container` formula

The field path mapping table (Change 9) correctly uses
`f"adp:{providerMetadata.shortName}"` for `source_container`, but the
existing algorithm prose (line ~537) and `cve-service.md` (line ~534)
still use the incorrect formula `f"adp:{title}"`. These produce
different values (e.g., `"adp:CISA ADP Vulnrichment"` vs
`"adp:CISA-ADP"`). The `data-model.md` examples already use `shortName`
values (`"adp:CISA-ADP"`), confirming `shortName` is correct.

**A) `cve-tracking.md` algorithm step 5 (line ~536-537)**

**Replace**:

```
   - All ADP containers (`containers.adp[]`): for each entry in the
     array, extract common data using `source_container =
     f"adp:{title}"`:
```

**With**:

```markdown
   - All ADP containers (`containers.adp[]`): for each entry in the
     array, extract common data using `source_container =
     f"adp:{providerMetadata.shortName}"`:
```

**B) `cve-service.md` (line ~534)**

**Replace** any occurrence of:

```
f"adp:{title}"
```

**With**:

```markdown
f"adp:{providerMetadata.shortName}"
```

---

## Application Order

| # | Change | Target File |
|---|--------|-------------|
| 1 | Add "Git-Based Fetchers" section (incl. Runtime Dependencies) | `fetcher-infrastructure.md` |
| 2 | Add `cursor` column to FetcherRun table + notes | `fetcher-infrastructure.md` |
| 3 | Add `cursor` column to FetcherRun table | `data-model.md` |
| 4 | Add `cursor` to FetcherRun Mermaid diagram | `data-model.md` |
| 5 | Add "Git-Based Fetchers" env var section | `configuration.md` |
| 6 | Replace duplicated sections with references + update first-run algorithm | `cve-tracking.md` |
| 7 | Add "Common First Run Behavior" section | `cve-tracking.md` |
| 8 | Add cross-reference to CVE Ingestion Flow | `architecture.md` |
| 9 | Add CVE JSON 5.x field path mapping table | `cve-tracking.md` |
| 10 | Clarify CISA-ADP identification | `cve-tracking.md` |
| 11 | Add git dependency, git worker, and volume to deployment guide | `deployment.md` |
| 12 | Remove git library ambiguity | `cve-tracking.md` |
| 13 | Update `sync_kernel_cves` cross-reference (eliminate double-hop) | `cve-tracking.md` |
| 14 | Fix `git pull`/`git merge`/rationale references for bare clone consistency | `cve-tracking.md` |
| 15 | Add implementation location note for git helper | `fetcher-infrastructure.md` |
| 16 | Fix `source_container` formula (`adp:{title}` → `adp:{shortName}`) | `cve-tracking.md` + `cve-service.md` |

Changes 1-5 are independent and can be applied in any order. Change 6
depends on Change 1 (references must point to content that exists).
Changes 7-10 are independent of each other but Changes 9-10 modify
content in the same file as Change 6 (apply in sequence). Change 11
is independent. Change 12 is independent. Change 13 depends on
Change 6 (the kernel cross-reference must be updated after the MITRE
sections it references are replaced). Change 14 is independent
(addresses a pre-existing inconsistency). Change 15 depends on Change 1
(appends to the section created by Change 1). Change 16 is independent
(corrects a pre-existing inconsistency in two files).

---

## Scope Exclusions

- **`docker-compose.yml`**: the local development compose file provides
  only PostgreSQL and Redis. Celery workers (including the git worker)
  are not part of the local dev stack — developers run workers manually
  or via separate commands. No compose changes are needed for this spec.
  The git worker deployment is documented in `deployment.md` (Change 11)
  which covers Docker/Podman and Kubernetes targets.

---

## Remaining Work

**Open point OP-13 requires resolution before implementation.** Changes
1–16 can be applied to spec files now that OP-11 is resolved. OP-13
(review gate) must pass after changes are applied.

| OP | Blocking? | Rationale |
|----|-----------|-----------|
| OP-9 | ~~Yes~~ Resolved | Cursor written on `partial` — Changes 1 and 2 updated |
| OP-10 | ~~Yes~~ Resolved | Time-bounded recovery (2 weeks) — Changes 1 and 6 updated |
| OP-11 | ~~Yes~~ Resolved | Aggregation removed entirely — `FetcherRun` retained indefinitely, no cursor-loss risk |
| OP-12 | ~~Yes~~ Resolved | Phase-based error classification — Changes 1 and 15 updated |
| OP-13 | Yes (post-apply) | Reviewer gate: spec-coherence, docs-placement, spec-gap-analyzer must pass before implementation |

---

## Verification Checklist

After all changes are applied, verify:

- [ ] `sync_mitre_cves` satisfies ALL 5 sections of the Fetcher
      Documentation Requirements minimum template
- [ ] No TBD values remain in the fetcher spec
- [ ] `configuration.md` lists all env vars needed by Git-based
      fetchers
- [ ] `data-model.md` reflects the `cursor` column on `FetcherRun`
- [ ] `fetcher-infrastructure.md` has the "Git-Based Fetchers" section
      (including Runtime Dependencies, Volume Requirements, Worker
      Affinity, Concurrency Rules, Recovery, Error Classification,
      Implementation Location, Cursor Write Mechanism, Empty Delta)
- [ ] `cve-tracking.md` references shared sections instead of
      duplicating them
- [ ] `cve-tracking.md` contains the CVE JSON 5.x field path mapping
      table with normalization rules, empty-versions handling, and
      CVE-ID cross-validation
- [ ] `cve-tracking.md` has no "or equivalent library calls" phrasing
      (replaced with explicit subprocess reference)
- [ ] `cve-tracking.md` has no `git pull` or `git merge` references
      in either fetcher section (bare clones use `git fetch origin`
      only)
- [ ] `cve-tracking.md` "Sync mechanism rationale" uses "bare clone +
      fetch" (no "Git clone/pull")
- [ ] `cve-tracking.md` Properties table Source says "bare clone +
      fetch" (not "Git clone/pull")
- [ ] `sync_kernel_cves` cross-reference points directly to
      `fetcher-infrastructure.md` (no double-hop through
      `sync_mitre_cves`)
- [ ] `source_container` formula is consistently
      `f"adp:{providerMetadata.shortName}"` across `cve-tracking.md`
      and `cve-service.md` (no `f"adp:{title}"` remnants)
- [ ] `deployment.md` includes `git` in Software Requirements, "Git
      worker" in Process Architecture, and the Git Worker Volume section
- [ ] `architecture.md` has a cross-reference from CVE Ingestion Flow
      to `cve-tracking.md`
- [ ] The git subprocess helper location is documented
      (`backend/app/services/git_operations.py`)
- [ ] Error classification is phase-based (documented in "Error
      Classification" subsection of "Git-Based Fetchers") with
      `GitFetchError`, `GitCorruptionError`, `GitFileError` hierarchy
- [ ] Cursor JSONB validation documented (must be dict, validated via
      `json.dumps()`)
- [ ] Cursor query uses `status IN ('success', 'partial')` consistently
      across `fetcher-infrastructure.md` and `data-model.md`
- [ ] Recovery strategy in `cve-tracking.md` specifies the 2-week
      time-bounded window with `git rev-list` + `git diff` commands
- [ ] Disk space estimate in `cve-tracking.md` references the 1 GB
      minimum from `fetcher-infrastructure.md` (no conflicting value)
- [ ] An implementer can write the fetcher using ONLY the specs (no
      undocumented decisions remain)
- [ ] `docs/data-sources.md` Fetcher Registry entry is still accurate
- [ ] No additional Python dependency is needed (raw subprocess uses
      stdlib only)
- [ ] OP-9 through OP-12 are resolved with decisions documented in the
      Resolved Open Points section
- [ ] OP-13 reviewers executed and findings addressed:
      - [ ] `@spec-coherence-reviewer` — no contradictions
      - [ ] `@docs-placement-reviewer` — correct shared/specific split
      - [ ] `@spec-gap-analyzer` — no high-severity gaps remain
