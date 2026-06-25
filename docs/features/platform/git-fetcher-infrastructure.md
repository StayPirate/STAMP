# Git-Based Fetcher Infrastructure

## Purpose

Shared infrastructure for fetchers that synchronize data from external
Git repositories. Defines the BaseGitFetcher template-method class, the
git_operations.py utility module, and operational requirements (clone
pattern, cursor, recovery, worker affinity).

Current consumers: `sync_mitre_cves`, `sync_kernel_cves`.

Note: `git_operations.py` is independently usable by non-BaseGitFetcher
fetchers that need git operations without the template-method lifecycle
(see "When NOT to Use BaseGitFetcher" section).

## Position in Hierarchy

```
fetcher-infrastructure.md
  BaseFetcher (lifecycle, metrics, FetcherRun, cursor, registry)
  │
  │  cve-fetcher-infrastructure.md
  └── BaseCVEFetcher (cve_source_type, fetch_single, catch_up)
      │
      │  git-fetcher-infrastructure.md (this document)
      └── BaseGitFetcher (clone, fetch, delta, recovery, SHA ops,
          │               queue="git", template method execute(),
          │               default fetch_single implementation)
          ├── SyncMitreCves
          └── SyncKernelCves
```


Some fetchers synchronize data from external Git repositories rather
than HTTP APIs. These fetchers share common infrastructure requirements
documented in this section. Individual fetcher specs define their own
algorithm, metrics, and source-specific behavior; this section defines
only the shared operational pattern.

Current git-based fetchers: `sync_mitre_cves`, `sync_kernel_cves`.

## Bare Clone Pattern

Git-based fetchers use **bare clones without a working tree**. This
minimizes disk usage (no checkout of hundreds of thousands of files)
while providing full access to file contents via Git object store
operations.

The pattern:

1. **Clone** (first run only — clone directory does not exist OR is not
   a valid bare git repository): `git clone --bare --single-branch <url>`
   into `$GIT_CLONE_BASE_DIR/<subdirectory>/`. For sources that support
   Git partial clone (protocol v2 with `filter` capability), add
   `--filter=blob:none` to defer blob downloads. For sources that do not
   support filtering (e.g., `git.kernel.org`), use a plain bare clone.
   **Validity check**: before deciding "first run vs. subsequent run",
   verify the directory is a valid bare git repository via
   `git rev-parse --git-dir`. If the directory exists but the check
   fails (partially-initialized clone from a previous interrupted
   attempt), delete the directory and proceed with a fresh clone.
2. **Fetch** (subsequent runs): `git fetch origin` updates refs and
   downloads new objects. This is incremental and typically completes in
   seconds.
3. **Delta detection**: `git diff --name-only --diff-filter=AMCR
   <old_sha>..<new_sha>` returns the list of Added, Modified, Copied,
   and Renamed files. Deleted files are excluded — they do not represent
   CVE data that needs processing.
4. **File content access**: `git show <ref>:<path>` reads a single
   file's content from the object store without creating a working tree.
   For blobless clones, this triggers an on-demand blob download for
   that specific file only.
5. **First-run file enumeration**: `git ls-tree -r --name-only HEAD`
   lists all files in the repository without checkout.

No `git merge`, `git checkout`, or working tree manipulation is
performed at any point.

## Cursor Persistence

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

If no run with a cursor exists (first run), the fetcher applies its own
first-run strategy (see the individual fetcher spec — e.g., "record
HEAD only" for CVE fetchers). For recovery scenarios where a stored
SHA is unreachable, the fetcher applies the date-based recovery
strategy (see "Cursor SHA Unreachable" below).

This mechanism is generic — non-git fetchers may use `cursor` for any
checkpoint data (timestamps, offsets, page tokens). The column is
nullable; fetchers that derive their cursor from other fields (e.g.,
NVD uses `started_at`) leave it NULL.

## Write Mechanism

Inside `execute()`, the fetcher sets `self._cursor` (a dict) with the
checkpoint data. After `execute()` returns, `run()` determines the
final status (see "Status determination precedence") and then, only if
the final status is `success` or `partial`, reads `self._cursor` and
writes it to the `FetcherRun` row in the same transaction that sets
`status` and `finished_at`. If `self._cursor` is None (not set), or
the final status is `failure` (including the all-items-failed case),
no cursor is written.

This avoids giving `execute()` direct access to the `FetcherRun` row
and keeps cursor persistence as a `run()` responsibility — consistent
with how `run()` already manages metrics (`items_created`,
`items_updated`, `items_failed`).

## Empty Delta

If `git fetch` succeeds but the delta contains zero files matching
the fetcher's filter (no CVE files changed), the run completes with
`status = success`, zero metrics, and the cursor advances to the new
HEAD SHA. This is the normal case during low-activity periods.

## First-Run Detection

A git-based fetcher determines "first run" by the absence of a
`FetcherRun` record with a cursor — NOT by the presence or absence of
the clone directory. The clone directory state is a sub-condition of
the first-run logic:

| Cursor exists? | Clone valid? | Action |
|---|---|---|
| No | No (absent or invalid) | If directory exists but is invalid (fails `git rev-parse --git-dir`): delete entirely. Clone repository. Record HEAD without processing |
| No | Yes | Skip clone (previous attempt succeeded but cursor was not persisted). Record HEAD without processing |
| Yes | Yes | Subsequent run: fetch + delta detection from cursor |
| Yes | No (absent or invalid) | Delete invalid directory if present. Re-clone. Then apply cursor reachability check (see Recovery Strategy below) |

"Invalid" means: the directory exists but `git rev-parse --git-dir`
fails (corrupted pack files, incomplete clone from interrupted
previous attempt, filesystem corruption, etc.).

The cursor-based approach ensures correctness when the first run
clones successfully but fails before persisting the cursor. In that
scenario, a clone-state-based check would incorrectly conclude
"subsequent run" and attempt delta detection without a stored SHA.
The cursor-based check correctly identifies this as a first run and
records HEAD without processing.

For `BaseGitFetcher` subclasses, this decision matrix is implemented
by `BaseGitFetcher.execute()` — concrete fetchers do not reimplement
it. See "BaseGitFetcher Class" below.

## Environment Configuration

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

## Volume Requirements

| Property | Value |
|----------|-------|
| Persistence | Required across container restarts |
| Capacity | 1 GB minimum (current usage ~400 MB; provides headroom for growth and transient git operations) |
| Access mode | ReadWriteOnce (single worker pod) |
| Filesystem | Any POSIX-compliant filesystem |
| Backup | Not required (recoverable from upstream repos) |

## Worker Affinity

Git-based fetcher tasks MUST execute on a Celery worker with the Git
volume mounted. This is achieved via a dedicated Celery queue:

- **Queue name**: `git`
- **Routing**: `BaseGitFetcher` sets `queue = "git"` as a fixed class
  attribute — all concrete subclasses inherit this value automatically.
  Fetchers that inherit from `BaseFetcher` directly and need git queue
  affinity set it in their own class body
- **`queue` class attribute on BaseFetcher**: `BaseFetcher` defines a
  `queue: str | None = None` class attribute (default = default Celery
  queue). `BaseGitFetcher` overrides it to `"git"` for the entire
  git-fetcher hierarchy. Non-git fetchers that omit it are routed
  normally — safe by default
- **Worker configuration**: the worker process with access to the Git
  volume consumes from the `git` queue (in addition to the default
  queue, if desired)
- **`fetch_single()` routing**: `trigger_on_demand_fetch()` reads
  `fetcher_cls.queue` when dispatching via `.apply_async(queue=...)`.
  If `None`, no queue parameter is passed and Celery uses default
  routing. This ensures on-demand fetches for git-based fetchers
  reach the worker with the volume mounted

In single-worker deployments (local dev, simple Docker/Podman), all
queues are consumed by the same worker process and no explicit routing
configuration is needed.

## Concurrency Rules

These rules apply to ALL git-based fetchers sharing the same volume:

1. **Only the periodic sync modifies the clone**: `git fetch` and any
   other write operations are performed exclusively by the periodic
   sync task. `fetch_single()` MUST NOT run `git fetch` or any
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
   `docs/features/platform/fetcher-infrastructure.md`,
   BaseFetcher Base Class). It applies per-fetcher, not
   per-volume. A `sync_mitre_cves` run and a `sync_kernel_cves` run
   can overlap without conflict.

## Recovery

**Volume loss** (directory does not exist):

1. Re-clone the repository (same clone command as first run)
2. Read the `cursor` from the last `FetcherRun` with
   `status IN ('success', 'partial')` for this fetcher in the database
3. Check if the stored SHA exists in the new clone
   (`git cat-file -t <sha>`)
4. If reachable: normal delta processing from stored SHA to HEAD
5. If not reachable (upstream force-push, branch deletion, or SHA
   garbage-collected): apply the date-based recovery strategy (see
   "Cursor SHA Unreachable" below). For `BaseGitFetcher` subclasses
   this is handled automatically by `execute()` — only
   `recovery_path_prefix` varies per fetcher

**Corrupted clone** (git operations fail with corruption errors):

1. Log WARNING with the error details
2. Delete the entire clone directory
3. Re-clone (same as volume loss recovery)

## Cursor SHA Unreachable

When a git-based fetcher's stored cursor SHA is not reachable in the
local clone (detected via `git cat-file -t <sha>` returning non-zero),
it applies a date-based recovery strategy using the `committed_at`
field stored in the cursor. This situation occurs when:

- The clone was rebuilt (row 4 of the First-Run Detection table)
- The upstream repository was force-pushed or rebased (rare for
  published CVE/advisory repos)
- Git garbage collection pruned unreachable objects (should not
  happen for commits reachable from HEAD, but possible with
  corrupted state)

**Algorithm**:

1. Compute `before_date` as `cursor_committed_at` minus 1 day (the
   1-day margin ensures no items are missed around the boundary —
   reprocessing is idempotent)
2. Determine boundary SHA:
   `git rev-list -1 --before="<before_date>" HEAD`
3. If no commit exists before `before_date` (empty output — the
   repository history does not extend that far back): log WARNING
   ("Recovery boundary not found — treating as first-run"), return
   empty delta. Cursor advances to HEAD
4. Compute delta:
   `git diff --name-only --diff-filter=AMCR <boundary_sha>..HEAD
   -- '<recovery_path_prefix>'`
5. Apply the fetcher's normal file filtering and per-item processing
   logic (MUST be idempotent — previously ingested items produce no
   observable side effects on re-processing)
6. Write HEAD as new cursor on completion

Each git-based fetcher declares this parameter in its properties
table:

| Parameter | Description | Example values |
|---|---|---|
| `recovery_path_prefix` | Path filter for the recovery delta command | `cves/` (MITRE), `cve/` (kernel) |

**Advantages over a fixed window**: the date-based approach always
covers the exact gap regardless of how long the fetcher was offline.
Reprocessing overlap is always ~1 day (idempotent, negligible cost).
No configurable `recovery_window` parameter is needed.

**Normal case after re-clone**: when a clone is rebuilt from the
same remote (row 4 of First-Run Detection), the cursor SHA is
almost always reachable because git history is preserved. In this
case, normal delta detection proceeds — no recovery is needed. The
recovery strategy is a fallback for the rare case where the SHA
truly does not exist in the fresh clone.

For `BaseGitFetcher` subclasses, this recovery algorithm is
implemented by `BaseGitFetcher.execute()` — concrete fetchers only
declare `recovery_path_prefix` as a class attribute. See
"BaseGitFetcher Class" below.

## Runtime Dependencies

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

## Error Classification

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

## Implementation Location

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

## Design Principles

The git operations module is NOT a "service" in the Sentinel
service-layer sense:

- Contains stateless utility functions (no database interaction, no
  business logic)
- Centralizes subprocess error handling and maps git failures to the
  typed exception hierarchy
- Is consumed by `BaseGitFetcher` methods, which delegate subprocess
  execution to this module. Can also be used independently by code that
  needs git operations without the `BaseGitFetcher` lifecycle (e.g.,
  fetchers inheriting from `BaseFetcher` directly)
- Provides a clean mocking boundary for unit tests (mock one function
  instead of `subprocess.run`)

Fetchers that inherit from `BaseGitFetcher` delegate execution flow
to the template method — they implement only processing hooks.
Fetchers that inherit from `BaseFetcher` directly retain full control
over their execution flow, using the utility functions as building
blocks.

## Responsibility Separation

The utility module is **policy-free** — it executes git commands with
the parameters it receives. It does not apply domain-specific defaults.

- **Domain defaults** (bare=True, filter=blob:none, single-branch=True)
  live on `BaseGitFetcher` class attributes
- **`BaseGitFetcher` methods** read `self.*` attributes and pass them as
  explicit parameters to `git_operations` functions
- **Concrete subclasses** override class attributes to change behavior
  (e.g., kernel sets `clone_filter = None`)

This separation ensures `git_operations.py` remains general-purpose and
independently usable.

## Function Catalog

The following table defines the complete public interface of
`git_operations.py`. These are the functions that `BaseGitFetcher`
delegates to and that any `BaseFetcher`-direct subclass may also call.

## Clone Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `clone` | `async def clone(url: str, dest: Path, *, bare: bool = False, filter_spec: str \| None = None, single_branch: bool = False) -> None` | `None` | Clone (20 min) | `GitFetchError` |

**Behavior**:

1. Build the base command: `["git", "clone", url, str(dest)]`
2. If `bare` is `True`: append `--bare`
3. If `filter_spec` is not `None`: append `--filter=<filter_spec>`
4. If `single_branch` is `True`: append `--single-branch`
5. Execute the command via `asyncio.create_subprocess_exec` with the
   clone timeout (20 minutes)
6. If the process exits with non-zero code: raise `GitFetchError` with
   stderr content

## Fetch Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `fetch_origin` | `async def fetch_origin(repo_path: Path) -> None` | `None` | Fetch (5 min) | `GitFetchError` |

Semantics: runs `git fetch origin` in the specified repository.
Incremental — only new objects are transferred.

## Read Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `get_head_sha` | `async def get_head_sha(repo_path: Path) -> str` | 40-char hex SHA | Read (30 sec) | `GitCorruptionError` |
| `get_commit_date` | `async def get_commit_date(repo_path: Path, ref: str) -> str` | ISO 8601 date string in UTC (e.g., `2025-06-01T18:00:00+00:00`) | Read (30 sec) | `GitCorruptionError` |
| `is_clone_valid` | `async def is_clone_valid(repo_path: Path) -> bool` | `bool` | Read (30 sec) | Never (returns `False` on any failure) |
| `check_sha_reachable` | `async def check_sha_reachable(repo_path: Path, sha: str) -> bool` | `bool` | Read (30 sec) | `GitCorruptionError` (only for unexpected failures; unreachable SHA returns `False`) |
| `diff_names` | `async def diff_names(repo_path: Path, from_sha: str, to_sha: str, *, path_filter: str \| None = None) -> list[str]` | List of file paths | Read (30 sec) | `GitCorruptionError` |
| `rev_list_before` | `async def rev_list_before(repo_path: Path, before_date: str) -> str \| None` | 40-char hex SHA or `None` | Read (30 sec) | `GitCorruptionError` |

Semantics:

- **`get_head_sha`**: returns the commit SHA that HEAD points to
  (`git rev-parse HEAD`)
- **`get_commit_date`**: returns the committer date of the specified ref
  as an ISO 8601 string normalized to UTC
  (`git log -1 --format=%cI <ref>`, then converted to UTC; or
  equivalently, executed with `TZ=UTC` environment to produce UTC
  output directly). Follows the project's "UTC everywhere" convention
  (`docs/conventions.md`, Timestamps & Timezones). Used to store
  `committed_at` in the cursor for recovery boundary computation
- **`is_clone_valid`**: returns `True` if `repo_path` is a valid git
  repository (`git rev-parse --git-dir` succeeds). Returns `False` if
  the directory does not exist, is not a git repository, or the check
  fails for any reason. NEVER raises — used as a guard condition
- **`check_sha_reachable`**: determines whether a given SHA exists in
  the local object store as a valid git object
  (`git cat-file -t <sha>`).

  **Behavior**:

  1. Execute `git cat-file -t <sha>` in the repository
  2. If exit code is 0: return `True` (object exists and is valid)
  3. If exit code is 1 and stderr indicates "not a valid object name" or
     similar: return `False` (SHA not reachable — expected condition)
  4. If exit code indicates a different failure (I/O error, repository
     corruption): raise `GitCorruptionError`

- **`diff_names`**: returns the list of added, modified, copied, and
  renamed files between two commits
  (`git diff --name-only --diff-filter=AMCR <from>..<to>`). If
  `path_filter` is set, appends `-- '<path_filter>'` to restrict
  results. Deleted files are excluded
- **`rev_list_before`**: returns the most recent commit SHA on HEAD
  before the specified date
  (`git rev-list -1 --before="<before_date>" HEAD`). Returns `None` if
  no commit exists before the specified date (empty output from git).
  Used for recovery boundary detection

## Show Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `show_file` | `async def show_file(repo_path: Path, ref: str, file_path: str) -> bytes \| None` | File content as `bytes`, or `None` if path does not exist | Read (30 sec) | `GitFileError` (for errors other than "path not found") |

**Behavior**:

1. Execute `git show <ref>:<file_path>` in the repository
2. If exit code is 0: return stdout as `bytes` (file content)
3. If exit code is 128 and stderr contains "does not exist in" or
   "path not found": return `None` (file does not exist at this ref —
   expected condition, not an error)
4. If exit code indicates a different failure (network error in blobless
   clone, corrupt object, timeout): raise `GitFileError` with stderr
   content

In blobless clones, step 1 triggers an on-demand blob download from the
remote — requires network access. If the remote is unreachable, step 4
fires.

## Filesystem Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `delete_clone` | `async def delete_clone(path: Path) -> None` | `None` | N/A | `OSError` (filesystem errors) |

Semantics: recursively deletes the directory at `path` if it exists.
No-op if the path does not exist. This is NOT a git operation — it is
included in the module for co-location with clone lifecycle management.

## Bare and Blobless Compatibility

All git operations in the function catalog are compatible with both
plain bare clones and blobless bare clones (`--filter=blob:none`):

- **Local-only operations** (`get_head_sha`, `get_commit_date`,
  `is_clone_valid`, `check_sha_reachable`, `diff_names`,
  `rev_list_before`): access only commit and tree objects, which are
  always present locally in both plain and blobless clones. No network
  access required
- **On-demand operations** (`show_file`): access blob content. In
  blobless clones, this triggers an on-demand blob download from the
  remote for the specific file requested. This is the intended access
  pattern — blobs are fetched individually rather than bulk-downloaded
  during clone or fetch
- **`check_sha_reachable` on commit SHAs**: `BaseGitFetcher` uses this
  exclusively on commit SHAs (the cursor). Commit objects are always
  present locally, even in blobless clones. The function would also
  work on tree SHAs (present locally) but NOT reliably on blob SHAs
  (may be absent in blobless clones)

No special handling is needed per clone type — the git binary
transparently handles both modes. The only operational difference is
that `show_file` requires network access in blobless clones (and may
raise `GitFileError` if the remote is unreachable at that moment).

## BaseGitFetcher Class

Template Method intermediate class for fetchers that follow the standard
delta-based git flow (clone → fetch → SHA reachability → delta detection
→ per-item processing → cursor advance). Eliminates duplicated state
machine code by providing a single `execute()` implementation that
delegates per-item processing to concrete subclasses via hook methods.

**Class hierarchy**:

```
BaseFetcher (generic: lifecycle, metrics, FetcherRun, cursor, registry)
  └── BaseCVEFetcher (CVE-specific: cve_source_type, fetch_single (opt-out), catch_up)
        └── BaseGitFetcher (git-specific: clone, fetch, delta, recovery, SHA ops)
              ├── SyncMitreCves (per-item: cvelistV5 JSON, CNA+ADP containers)
              └── SyncKernelCves (per-item: vulns.git JSON, kernel-specific mapping)
```

**File location**: `backend/app/services/base_git_fetcher.py`

## Class Attributes

Concrete subclasses declare the configurable attributes as class-level
values. The fixed attributes are set by `BaseGitFetcher` and inherited
automatically.

**Configurable (declared by subclasses)**:

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `repo_url` | `str` | (required) | Git remote URL |
| `clone_dir_name` | `str` | (required) | Directory name under `$GIT_CLONE_BASE_DIR` |
| `clone_bare` | `bool` | `True` | Whether to use `--bare` |
| `clone_filter` | `str \| None` | `"blob:none"` | Value for `--filter=`. `None` = no filter (plain bare clone) |
| `clone_single_branch` | `bool` | `True` | Whether to use `--single-branch` |
| `recovery_path_prefix` | `str` | (required) | Path prefix for recovery delta (`-- '<prefix>'`) |
| `delta_path_prefix` | `str` | (required) | Path prefix for normal delta detection |

**Fixed (set by `BaseGitFetcher`, not overridable)**:

| Attribute | Value | Description |
|-----------|-------|-------------|
| `abstract` | `True` | Prevents registration in `FETCHER_REGISTRY` (intermediate class, not a concrete fetcher). Both `BaseCVEFetcher` and `BaseGitFetcher` set `abstract = True` (both are intermediate classes). Concrete subclasses do not set `abstract` in their own class body; `__init_subclass__` checks `cls.__dict__.get('abstract', False)` and proceeds with registration when the attribute is absent from the subclass's own namespace |
| `queue` | `"git"` | Celery queue for worker affinity. Ensures tasks execute on the worker with the git volume mounted. Inherited from `BaseFetcher` interface (default `None`), overridden at the `BaseGitFetcher` level |

These configurable attributes are also exposed in each fetcher's
properties table in its specification document. The fixed `queue`
attribute is inherited automatically and does not appear in
per-fetcher properties tables.

## Template Method: `execute()`

The `execute()` method implements the full git-based fetcher state
machine. Concrete subclasses MUST NOT override `execute()` (they
implement hooks instead).

Implements the First-Run Detection truth table and Recovery Strategy
algorithm from the sections above.

**Behavior**:

1. Resolve repository path from `$GIT_CLONE_BASE_DIR / clone_dir_name`
2. Read last cursor from the previous successful `FetcherRun.cursor`
   (via `BaseFetcher`). Extract `cursor_sha` and `cursor_committed_at`
   from the stored dict. If no prior successful run exists, both are
   `None`
3. **First-run branch** — if `cursor_sha` is `None`:
   a. If clone is NOT valid at `repo_path`: delete directory if it
      exists, then clone repository with configured options
   b. If clone IS valid: skip clone (reuse existing)
   c. Read HEAD SHA from the repository
   d. Read HEAD commit date via `get_commit_date(repo_path, "HEAD")`
   e. Set cursor to `{"sha": head_sha, "committed_at": head_date}`
   f. Return — first-run complete, no items processed
4. **Subsequent-run branch** — `cursor_sha` exists:
   a. If clone is NOT valid: log WARNING ("Clone invalid but cursor
      exists — rebuilding"), delete directory if exists, clone repository
   b. If clone IS valid: fetch origin (incremental update)
5. Read HEAD SHA from the repository
6. Read HEAD commit date via `get_commit_date(repo_path, "HEAD")`
7. **SHA reachability check**:
   a. If `cursor_sha` is NOT reachable in the local object store: log
      WARNING ("Cursor SHA unreachable — applying recovery"), compute
      recovery delta via
      `_compute_recovery_delta(repo_path, head_sha, cursor_committed_at)`
   b. If `cursor_sha` IS reachable: compute normal delta via
      `diff_names(repo_path, cursor_sha, head_sha)` with
      `delta_path_prefix` as path filter
8. Apply `filter_delta_files()` hook on the file list
9. Apply `deduplicate_items()` hook on the filtered list
10. **Per-item processing loop** — for each `path` in the deduplicated
    list:
    a. Read file content via `show_file(repo_path, "HEAD", path)`
    b. If content is `None` (file not found at HEAD — file was added
       then deleted/renamed between cursor and HEAD): log WARNING
       ("File {path} in delta but not at HEAD — skipping"), continue
       to next item. No metric is recorded
    c. Call `post_ingest = process_item(path, content, session)` →
       returns `PostIngestTasks | None`. On successful return, call
       `self.commit_and_dispatch(session, post_ingest)`
    d. If any exception is raised during steps 10a, 10c, or
       `commit_and_dispatch()`: call `session.rollback()`, log WARNING
       ("Failed to process {path}: {error}"), call `record_failed()`,
       continue to next item

    **Transaction boundaries**: each iteration of the processing loop
    operates in its own transaction boundary. `process_item()` returns
    `PostIngestTasks | None`; after a successful return, the template
    calls `self.commit_and_dispatch(session, post_ingest)` which
    commits the session and dispatches Phase 2 tasks if `post_ingest`
    is not `None`. On exception (caught by step 10d), the template
    calls `session.rollback()` before `record_failed()`. This ensures
    that a failure in one item does not corrupt the session or affect
    the processing of subsequent items.

11. Set cursor to `{"sha": head_sha, "committed_at": head_date}`

Note: the all-items-failed safety check (preventing cursor advance when
every item fails) is handled by `BaseFetcher.run()` after `execute()`
returns — see "Status determination precedence" in the BaseFetcher
section. Items skipped in step 10b (file not at HEAD) do not increment
any counter and do not trigger the safety check.

**Infrastructure errors**: exceptions from clone, fetch, HEAD read, or
delta computation propagate naturally — `BaseFetcher.run()` catches them
and records a failed run without advancing the cursor. The template
method does NOT catch infrastructure-level exceptions. On the next
scheduled run, the First-Run Detection truth table re-evaluates the
clone state and applies appropriate recovery.

**Error Handling Strategy**:

Infrastructure failures and their outcomes:

| Infrastructure failure | Exception | BaseFetcher behavior |
|------------------------|-----------|---------------------|
| Clone fails (network) | `GitFetchError` | `status = failure`, cursor not advanced |
| Fetch fails (network) | `GitFetchError` | `status = failure`, cursor not advanced |
| HEAD unreadable (corruption) | `GitCorruptionError` | `status = failure`, cursor not advanced |
| Delta computation fails | `GitCorruptionError` | `status = failure`, cursor not advanced |

On the next scheduled run, the First-Run Detection truth table
re-evaluates the clone state and applies the appropriate recovery
(row "Cursor exists + Clone invalid" → re-clone).

The **all-items-failed safety check** is now handled by
`BaseFetcher.run()` (see "Status determination precedence" in the
BaseFetcher section). If all items fail (e.g., network drops after fetch
in a blobless clone, making every `show_file()` fail), `run()` sets
`status = failure` directly after `execute()` returns. Since the cursor
is only persisted on `success` or `partial`, the previous cursor is
preserved and the next run retries the same delta. This applies to all
fetcher subclasses uniformly, not just git-based fetchers.

**Status Determination**:

`BaseGitFetcher` relies entirely on `BaseFetcher`'s existing status
mechanism — no additional logic is needed:

| Scenario | Status | Cursor advances? |
|----------|--------|-----------------|
| First run (no processing) | `success` | Yes (step 3e) |
| Empty delta (HEAD unchanged) | `success` | Yes (step 11) |
| All items succeed | `success` | Yes (step 11) |
| Some items fail, some succeed | `partial` | Yes (step 11) |
| All items fail | `failure` | No (`BaseFetcher.run()` safety check) |
| Infrastructure error | `failure` | No (propagates) |

## Hook Methods (Override Points)

These are the extension points for concrete subclasses:

**Hooks for `execute()`**:

| Method | Required? | Default | Purpose |
|--------|-----------|---------|---------|
| `process_item(path, content, session)` | **Yes** (abstract) | — | Process a single file from the delta. Calls `self.record_created()` or `self.record_updated()` on success |
| `filter_delta_files(file_list)` | No | Return all | Filter raw delta output to relevant files (e.g., only `.json` in specific dirs) |
| `deduplicate_items(file_list)` | No | No-op | Deduplicate items before processing (e.g., same CVE-ID in both `published/` and `rejected/`) |

**Hooks for `fetch_single()`**:

| Method | Required? | Default | Purpose |
|--------|-----------|---------|---------|
| `_construct_candidate_paths(item_id)` | **Yes** (abstract) | — | Return ordered list of candidate file paths for local clone lookup |

## `process_item(path: str, content: bytes, session: AsyncSession) -> PostIngestTasks | None`

The core extension point. Receives:
- `path`: relative path within the repository (e.g., `cve/published/2024/CVE-2024-50055.json`)
- `content`: raw file content as bytes (from `git show`)
- `session`: the database session for the current execution (same
  `AsyncSession` instance passed to `execute()` by `BaseFetcher.run()`)

The hook is responsible for:
1. Parsing the content and applying business logic (upsert, etc.)
2. Calling `self.record_created()` or `self.record_updated()` to report
   the outcome (same pattern as non-git `BaseFetcher` subclasses)
3. Returning `PostIngestTasks` if post-ingest dispatch is needed, or
   `None` in two cases: (a) the item was skipped (already up-to-date,
   no work done — no metric is recorded), or (b) the item was
   processed but no post-ingest tasks are needed (e.g.,
   enrichment-only upsert with no ticket or no CPE data — metric IS
   recorded). Both `None` cases result in
   `commit_and_dispatch(session, None)` — the template commits without
   dispatching Phase 2 tasks

Raises any exception on failure → caught by `execute()`, logged,
`record_failed()` called.

**Phase 2 side effects**: hooks that call `cve_service.upsert_cve()`
return `PostIngestTasks` containing the Phase 2 task arguments. The
`BaseGitFetcher` template dispatches these tasks via
`commit_and_dispatch()` after committing the per-item transaction.
No post-processing batch hook is needed — Phase 2 is per-item and
self-contained.

## `filter_delta_files(file_list: list[str]) -> list[str]`

Optional override. Receives the file list from `git diff` (after
applying `delta_path_prefix` path restriction at the git level).
Returns only the files that should be processed.

The two-level filtering design:
- **`delta_path_prefix`** (class attribute): coarse path-prefix
  filtering at the git subprocess level (`-- '<prefix>'`). Reduces the
  diff output before it reaches Python
- **`filter_delta_files()`** (hook): fine-grained filtering in Python
  (e.g., regex matching, extension checks, directory logic)

Example (kernel): keep only `.json` files matching
`cve/{published,rejected}/YEAR/CVE-YEAR-ID.json`.

## `deduplicate_items(file_list: list[str]) -> list[str]`

Optional override. Receives the filtered file list. Returns a
deduplicated list resolving conflicts (e.g., if same CVE appears in
both `published/` and `rejected/`, keep only the `rejected/` entry).

Default implementation returns the list unchanged.

## Default `fetch_single()` Implementation

`BaseGitFetcher` provides a concrete implementation of `fetch_single()`
that overrides the `BaseCVEFetcher` default (which raises `RuntimeError`).
Concrete subclasses inherit it automatically (no override needed).

**Behavior**:

1. Resolve repository path from `$GIT_CLONE_BASE_DIR / clone_dir_name`
2. Check if clone is valid at `repo_path`. If NOT valid: raise
   `RuntimeError` ("Clone not available at {repo_path} for single-item
   lookup")
3. Call `_construct_candidate_paths(item_id)` to obtain an ordered list
   of candidate file paths
4. For each `path` in the candidate list:
   a. Read file content via `show_file(repo_path, "HEAD", path)`
   b. If content is not `None` (file found): return the result of
      `process_item(path, content, session)` (`PostIngestTasks | None`)
5. If no candidate path produced content: raise
   `CVENotInSource(item_id)`

**Audit events**: none created directly. Side effects (DB mutations,
audit events) are delegated entirely to `process_item()` — the
concrete subclass's hook determines what is recorded.

**Re-invocation**: calling `fetch_single()` with the same `item_id`
multiple times is safe. Idempotency depends on the concrete
`process_item()` implementation — both current subclasses
(`SyncMitreCves`, `SyncKernelCves`) delegate to `cve_service.upsert_cve()`
which is idempotent (no-op if data unchanged, update if changed).

**Exceptions**:

- `RuntimeError` — clone not available (step 2)
- `CVENotInSource` — item not found in any candidate path (step 5)
- Exceptions from `process_item()` propagate uncaught to the caller

The two exception types serve different purposes for the caller
(`trigger_on_demand_fetch()`):

- **`RuntimeError`** — "source not queryable right now" (clone missing,
  corrupt, or not yet created by the first periodic run). The dispatch
  system logs a WARNING and tries the next fetcher. The clone will be
  available after the next scheduled sync.
- **`CVENotInSource`** — "item does not exist in this source"
  (authoritative negative). The dispatch system logs INFO and tries the
  next fetcher.

## `_construct_candidate_paths(item_id: str) -> list[str]`

Abstract method. Returns an ordered list of candidate file paths where
the item might exist in the repository. The default `fetch_single()`
tries each path in order via `git show` until one succeeds.

The ordering is significant: the first match wins. If the same item
could exist in multiple locations (e.g., `published/` vs. `rejected/`),
place the most likely or authoritative path first.

```python
# Kernel example:
def _construct_candidate_paths(self, cve_id: str) -> list[str]:
    year = cve_id.split("-")[1]  # CVE-YYYY-NNNNN → YYYY
    return [
        f"cve/published/{year}/{cve_id}.json",
        f"cve/rejected/{year}/{cve_id}.json",
    ]
```

## Inherited Utility Methods

These methods are available to concrete subclasses (e.g., for use in
`fetch_single()`). They are NOT hook methods — subclasses call them
but do not override them.

All methods in this table propagate exceptions from the corresponding
`git_operations` function they delegate to. No method creates audit
events or mutates database state — they are pure infrastructure
operations.

| Method | Purpose |
|--------|---------|
| `_get_last_cursor_sha()` | Reads the `"sha"` field from the previous `FetcherRun.cursor` (via `BaseFetcher`). Returns `None` if no prior successful run exists |
| `_get_last_cursor_committed_at()` | Reads the `"committed_at"` field from the previous `FetcherRun.cursor` (via `BaseFetcher`). Returns `None` if no prior successful run exists or if the field is absent |
| `_repo_path()` | Returns `Path($GIT_CLONE_BASE_DIR / clone_dir_name)` |
| `_clone_repo(path)` | Clones the repository with configured options (bare, filter, single-branch). Delegates to `git_operations.clone()` |
| `_fetch_origin(path)` | Runs `git fetch origin`. Delegates to `git_operations.fetch_origin()` |
| `_get_head_sha(path)` | Returns current HEAD SHA. Delegates to `git_operations.get_head_sha()` |
| `_get_commit_date(path, ref)` | Returns commit date as ISO 8601 string in UTC. Delegates to `git_operations.get_commit_date()` |
| `_is_clone_valid(path)` | Returns bool. Delegates to `git_operations.is_clone_valid()` |
| `_check_sha_reachable(path, sha)` | Returns bool. Delegates to `git_operations.check_sha_reachable()` |
| `_compute_delta(path, from_sha, to_sha)` | Returns file list from `git diff` with `delta_path_prefix`. Delegates to `git_operations.diff_names()` |
| `_compute_recovery_delta(repo_path, head_sha, cursor_committed_at)` | Applies recovery using stored commit date minus 1 day + `recovery_path_prefix`. See detailed behavior below |
| `_show_file(path, ref, file_path)` | Returns file content or None. Delegates to `git_operations.show_file()` |
| `_delete_if_exists(path)` | Deletes directory if it exists. Delegates to `git_operations.delete_clone()` |

## `_compute_recovery_delta()`

Computes the file delta using the stored cursor commit date when the
cursor SHA is unreachable (force-push, history rewrite, or clone
rebuild).

`async def _compute_recovery_delta(repo_path: Path, head_sha: str, cursor_committed_at: str) -> list[str]`

**Behavior**:

1. Compute `before_date` as `cursor_committed_at` minus 1 day (the
   1-day margin ensures no items are missed around the boundary —
   reprocessing is idempotent)
2. Call `rev_list_before(repo_path, before_date)` to find the boundary
   SHA — the most recent commit before `before_date`
3. If `rev_list_before` returns `None` (no commit exists before
   `before_date` — the repository history does not extend that far
   back):
   a. Log WARNING: "Recovery boundary not found — repository may have
      been completely rewritten. Treating as first-run. Cursor reset to
      HEAD. Use fetch_single() to recover specific items if needed."
   b. Return empty list
4. Call `diff_names(repo_path, boundary_sha, head_sha)` with
   `recovery_path_prefix` as path filter
5. Return the file list

**Edge case rationale (step 3)**: this scenario requires that ALL
commits reachable from HEAD have dates more recent than the stored
`cursor_committed_at - 1 day`. For repositories like cvelistV5
(history since 2022) or vulns.git (history since 2024), this is
virtually impossible — it would require complete repository recreation
with new commit dates. The first-run treatment (cursor advances to
HEAD, zero items processed) is the correct response: the fetcher
restarts cleanly and `fetch_single()` is available for manual recovery
of specific items.

**Exceptions**: propagates `GitCorruptionError` from `rev_list_before()`
(step 2) and `diff_names()` (step 4).

## Registry Detection Predicate Update

The `get_fetch_single_fetchers()` and `get_catch_up_fetchers()` registry
accessors use `_CVE_SOURCE_TYPE_MAP` and `BaseCVEFetcher` subclass
detection respectively (see
`docs/features/platform/cve-fetcher-infrastructure.md`). Since `BaseGitFetcher` inherits
from `BaseCVEFetcher`, its concrete subclasses are automatically
included in both accessors — they declare their own `cve_source_type`
via the `BaseCVEFetcher.__init_subclass__` chain.

## When NOT to Use `BaseGitFetcher`

`BaseGitFetcher` is NOT a requirement for all fetchers that interact
with git repositories. It is the correct choice only for fetchers that
follow the standard delta-based flow (clone → fetch → SHA reachability
→ delta detection → per-item processing → cursor advance).

A future git-based fetcher MUST inherit from `BaseCVEFetcher` directly
(using `git_operations.py` as a utility module) when it is a CVE
fetcher but:

- It requires multi-branch tracking (the template assumes
  single-branch, single HEAD)
- It uses sparse checkout or non-standard clone strategies not
  expressible via the class attributes
- Its delta detection is not commit-range based (e.g., full tree scan,
  tag-based comparison)
- It needs non-linear traversal (e.g., walking merge commits
  individually)
- Its processing flow has steps between delta detection and per-item
  processing that cannot be expressed as a filter hook

A non-CVE git-based fetcher inherits from `BaseFetcher` directly
(using `git_operations.py` as a utility module).

In these cases, `BaseCVEFetcher` (or `BaseFetcher`) +
`git_operations.py` provides the same subprocess utilities without
imposing a fixed execution order.


## Cross-references

- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher base
  class
- `docs/features/platform/cve-fetcher-infrastructure.md` — BaseCVEFetcher
  class (parent)
- `docs/features/tickets/cve-sync-mitre.md` — MITRE CVE fetcher
  (consumer)
- `docs/features/tickets/cve-sync-kernel.md` — Kernel CVE fetcher
  (consumer)
- `docs/features/platform/networking.md` — Shared HTTP client (used by
  fetch_single blob download)
