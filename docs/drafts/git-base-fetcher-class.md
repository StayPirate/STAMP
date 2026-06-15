# BaseGitFetcher Class Design

## Purpose

This document describes the design of a `BaseGitFetcher` intermediate
abstract class that encapsulates all git-specific fetcher lifecycle
logic (clone management, delta detection, recovery strategy, first-run
detection) as a reusable base for all git-based fetchers.

**Goal**: eliminate duplicated git lifecycle code between fetchers by
providing a Template Method base class that handles the state machine
(cursor/clone management) and delegates per-item processing to
concrete subclasses.

---

## Motivation

### 1. Empirically identical flow

Both `sync_mitre_cves` and `sync_kernel_cves` follow the same state
machine:

```
check cursor → clone/fetch → verify SHA reachability → delta detection
→ filter files → process items → record cursor
```

The only differences are parametric:

| Parameter | MITRE | Kernel |
|-----------|-------|--------|
| Clone filter | `blob:none` | None (plain bare) |
| Delta path prefix | `cves/` | `cve/` |
| Recovery path prefix | `cves/` | `cve/` |
| Per-item processing | Parse cvelistV5 JSON (CNA+ADP) | Parse vulns.git JSON (kernel-specific) |

### 2. Conventions WI-10 describe a class interface

The First-Run Detection truth table (WI-10 Part A) and Recovery
Strategy algorithm (WI-10 Part B) are detailed enough to be
pseudo-code. They map directly to a `execute()` template method.

### 3. Clear ownership of state machine

With a utility module alone, the developer must compose functions in
the right order within each fetcher's `execute()`. With
`BaseGitFetcher.execute()`, the state machine lives in one place —
concrete fetchers only implement the per-item hook.

### 4. Zero migration cost

The project is in specification phase. No existing code needs
refactoring, no database migrations, no test rewrites. This is the
optimal time to establish the class hierarchy.

### 5. `fetch_single()` works naturally

Git utility methods (`show_file()`, `check_sha_reachable()`) are
available as `self.*` in both `execute()` and `fetch_single()` without
extra imports or manual composition.

### 6. Better test separation

With a utility-module-only approach, testing each fetcher requires
exercising the full state machine (6+ decision points) in every test
suite — effectively re-testing the same orchestration logic N times.

With `BaseGitFetcher`:
- The state machine is tested **once** against `BaseGitFetcher.execute()`
  with mocked hooks
- Each concrete `process_item()` is tested in isolation with pure
  input/output (receives `path` + `bytes` + `session`, calls metric helpers)
- `git_operations.py` functions are tested independently

Tests become more focused, faster, and failures pinpoint the exact
layer (orchestration vs. business logic vs. subprocess handling).

---

## Class Hierarchy

```
BaseFetcher (generic: lifecycle, metrics, FetcherRun, cursor, registry)
  └── BaseGitFetcher (git-specific: clone, fetch, delta, recovery, SHA ops)
        ├── SyncMitreCves (per-item: cvelistV5 JSON, CNA+ADP containers)
        └── SyncKernelCves (per-item: vulns.git JSON, kernel-specific mapping)
```

Future git-based fetchers (e.g., OSV if ever git-sourced) inherit from
`BaseGitFetcher` and implement only their per-item logic.

### When NOT to Use `BaseGitFetcher`

`BaseGitFetcher` is NOT a requirement for all fetchers that interact
with git repositories. It is the correct choice only for fetchers that
follow the standard delta-based flow (clone → fetch → SHA reachability
→ delta detection → per-item processing → cursor advance).

A future git-based fetcher MUST inherit from `BaseFetcher` directly
(using `git_operations.py` as a utility module) when:

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

In these cases, `BaseFetcher` + `git_operations.py` provides the same
subprocess utilities without imposing a fixed execution order.

---

## Interface Specification

### Class Attributes

Concrete subclasses declare the configurable attributes as class-level
values. The fixed attribute is set by `BaseGitFetcher` and inherited
automatically.

#### Configurable (declared by subclasses)

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `repo_url` | `str` | (required) | Git remote URL |
| `clone_dir_name` | `str` | (required) | Directory name under `$GIT_CLONE_BASE_DIR` |
| `clone_bare` | `bool` | `True` | Whether to use `--bare` |
| `clone_filter` | `str \| None` | `"blob:none"` | Value for `--filter=`. `None` = no filter (plain bare clone) |
| `clone_single_branch` | `bool` | `True` | Whether to use `--single-branch` |
| `recovery_path_prefix` | `str` | (required) | Path prefix for recovery delta (`-- '<prefix>'`) |
| `delta_path_prefix` | `str` | (required) | Path prefix for normal delta detection |

#### Fixed (set by `BaseGitFetcher`, not overridable)

| Attribute | Value | Description |
|-----------|-------|-------------|
| `queue` | `"git"` | Celery queue for worker affinity. Ensures tasks execute on the worker with the git volume mounted. Inherited from `BaseFetcher` interface (default `None`), overridden at the `BaseGitFetcher` level |

These configurable attributes are also exposed in each fetcher's
properties table in its specification document. The fixed `queue`
attribute is inherited automatically and does not appear in
per-fetcher properties tables.

### Template Method: `execute()`

The `execute()` method implements the full git-based fetcher state
machine. Concrete subclasses MUST NOT override `execute()` (they
implement hooks instead).

Implements the First-Run Detection truth table and Recovery Strategy
algorithm from `fetcher-infrastructure.md`.

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
    c. Call `process_item(path, content, session)`
    d. If any exception is raised during steps 10a or 10c: log WARNING
       ("Failed to process {path}: {error}"), call `record_failed()`,
       continue to next item

    **Transaction boundaries**: each iteration of the processing loop
    operates in its own transaction boundary. After `process_item()`
    returns successfully or raises an exception (caught by step 10d),
    the session is committed or rolled back respectively before
    proceeding to the next item. This ensures that a failure in one
    item does not corrupt the session or affect the processing of
    subsequent items, and that Phase 2 side effects (enqueued
    post-commit by `cve_service.upsert_cve()`) are triggered per-item.

11. **Safety check**: if `items_failed > 0` AND
    `items_created + items_updated == 0`, raise `RuntimeError` ("All
    {N} items failed — cursor not advanced for safety"). This prevents
    cursor advance when every item failed (e.g., network drops in
    blobless clone making every `show_file()` fail). Note: items
    skipped in step 10b (file not at HEAD) do not increment any
    counter and do not contribute to the safety check trigger
12. Set cursor to `{"sha": head_sha, "committed_at": head_date}`

**Infrastructure errors**: exceptions from clone, fetch, HEAD read, or
delta computation propagate naturally — `BaseFetcher.run()` catches them
and records a failed run without advancing the cursor. The template
method does NOT catch infrastructure-level exceptions. On the next
scheduled run, the First-Run Detection truth table re-evaluates the
clone state and applies appropriate recovery.

#### Error Handling Strategy

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

The **safety check** (step 11) prevents a dangerous edge case: if all
items fail (e.g., network drops after fetch in a blobless clone, making
every `show_file()` fail), the cursor must NOT advance — otherwise those
items are permanently lost. The `RuntimeError` causes `BaseFetcher.run()`
to record `status = failure` and preserve the previous cursor, so the
next run retries the same delta.

#### Status Determination

`BaseGitFetcher` relies entirely on `BaseFetcher`'s existing status
mechanism — no additional logic is needed:

| Scenario | Status | Cursor advances? |
|----------|--------|-----------------|
| First run (no processing) | `success` | Yes (step 3e) |
| Empty delta (HEAD unchanged) | `success` | Yes (step 12) |
| All items succeed | `success` | Yes (step 12) |
| Some items fail, some succeed | `partial` | Yes (step 12) |
| All items fail (safety check) | `failure` | No (step 11) |
| Infrastructure error | `failure` | No (propagates) |

### Hook Methods (Override Points)

These are the extension points for concrete subclasses:

#### Hooks for `execute()`

| Method | Required? | Default | Purpose |
|--------|-----------|---------|---------|
| `process_item(path, content, session)` | **Yes** (abstract) | — | Process a single file from the delta. Calls `self.record_created()` or `self.record_updated()` on success |
| `filter_delta_files(file_list)` | No | Return all | Filter raw delta output to relevant files (e.g., only `.json` in specific dirs) |
| `deduplicate_items(file_list)` | No | No-op | Deduplicate items before processing (e.g., same CVE-ID in both `published/` and `rejected/`) |

#### Hooks for `fetch_single()`

| Method | Required? | Default | Purpose |
|--------|-----------|---------|---------|
| `_construct_candidate_paths(item_id)` | **Yes** (abstract) | — | Return ordered list of candidate file paths for local clone lookup |

#### `process_item(path: str, content: bytes, session: AsyncSession) -> None`

The core extension point. Receives:
- `path`: relative path within the repository (e.g., `cve/published/2024/CVE-2024-50055.json`)
- `content`: raw file content as bytes (from `git show`)
- `session`: the database session for the current execution (same
  `AsyncSession` instance passed to `execute()` by `BaseFetcher.run()`)

The hook is responsible for:
1. Parsing the content and applying business logic (upsert, etc.)
2. Calling `self.record_created()` or `self.record_updated()` to report
   the outcome (same pattern as non-git `BaseFetcher` subclasses)
3. Returning `None` if the item was skipped (already up-to-date) —
   no metric is recorded, which is the correct behavior

Raises any exception on failure → caught by `execute()`, logged,
`record_failed()` called.

**Phase 2 side effects**: hooks that call `cve_service.upsert_cve()`
trigger Phase 2 processing (package resolution, notifications)
automatically via Celery task enqueue after the Phase 1 transaction
commits. No post-processing batch hook is needed — Phase 2 is per-item
and self-contained.

#### `filter_delta_files(file_list: list[str]) -> list[str]`

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

#### `deduplicate_items(file_list: list[str]) -> list[str]`

Optional override. Receives the filtered file list. Returns a
deduplicated list resolving conflicts (e.g., if same CVE appears in
both `published/` and `rejected/`, keep only the `rejected/` entry).

Default implementation returns the list unchanged.

### Inherited Utility Methods

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
| `_get_commit_date(path, ref)` | Returns commit date as ISO 8601 string. Delegates to `git_operations.get_commit_date()` |
| `_is_clone_valid(path)` | Returns bool. Delegates to `git_operations.is_clone_valid()` |
| `_check_sha_reachable(path, sha)` | Returns bool. Delegates to `git_operations.check_sha_reachable()` |
| `_compute_delta(path, from_sha, to_sha)` | Returns file list from `git diff` with `delta_path_prefix`. Delegates to `git_operations.diff_names()` |
| `_compute_recovery_delta(repo_path, head_sha, cursor_committed_at)` | Applies recovery using stored commit date minus 1 day + `recovery_path_prefix`. See detailed behavior below |
| `_show_file(path, ref, file_path)` | Returns file content or None. Delegates to `git_operations.show_file()` |
| `_delete_if_exists(path)` | Deletes directory if it exists. Delegates to `git_operations.delete_clone()` |

#### `_compute_recovery_delta()`

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

The run then completes normally: zero items processed (step 3 case) or
processes items from the recovery range (step 4 case). In both cases,
cursor advances to HEAD.

### `fetch_single()` Integration

`BaseGitFetcher` provides a default `fetch_single()` implementation
that concrete subclasses inherit automatically (no override needed).

#### Registry Detection Predicate Update

The existing `get_fetch_single_fetchers()` and
`get_catch_up_fetchers()` registry accessors use `'fetch_single' in
cls.__dict__` as the detection predicate — which only finds methods
defined directly on the class, not inherited ones. Since
`BaseGitFetcher` subclasses inherit `fetch_single()` without
overriding it, the predicate must be updated to walk the MRO (Method
Resolution Order):

```python
any(
    'fetch_single' in klass.__dict__
    for klass in cls.__mro__
    if klass is not BaseFetcher and klass is not object
)
```

This detects `fetch_single()` defined on any intermediate class
(e.g., `BaseGitFetcher`) while still excluding `BaseFetcher` itself
(which may declare an abstract stub in the future). The same update
applies to the `catch_up()` detection logic in
`get_catch_up_fetchers()` and the guard in `BaseFetcher.catch_up()`
(`if 'fetch_single' not in type(self).__dict__`).

**No false positives**: fetchers that inherit directly from
`BaseFetcher` without `fetch_single()` anywhere in their MRO
(excluding `BaseFetcher` and `object`) are correctly excluded.

**Behavior**:

1. Resolve repository path from `$GIT_CLONE_BASE_DIR / clone_dir_name`
2. Check if clone is valid at `repo_path`. If NOT valid: raise
   `RuntimeError` ("Clone not available at {repo_path} for single-item
   lookup")
3. Call `_construct_candidate_paths(item_id)` to obtain an ordered list
   of candidate file paths
4. For each `path` in the candidate list:
   a. Read file content via `show_file(repo_path, "HEAD", path)`
   b. If content is not `None` (file found): call
      `process_item(path, content, session)`, then return
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

#### `_construct_candidate_paths(item_id: str) -> list[str]`

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

---

## Internal Architecture (Utility Module)

`BaseGitFetcher` methods delegate to a stateless utility module for
actual subprocess execution:

```
BaseGitFetcher._show_file(repo_path, ref, path)
    → await git_operations.show_file(repo_path, ref, path)
        → asyncio.create_subprocess_exec("git", "show", f"{ref}:{path}", ...)
```

The utility module (`backend/app/services/git_operations.py`):

- Contains pure functions — no state, no database, no business logic
- Maps subprocess failures to typed exceptions:
  - `GitFetchError`: fetch/clone fails due to network (transient)
  - `GitFileError`: `git show` fails (missing blob, corrupt object)
  - `GitCorruptionError`: repository structure is invalid
- Provides a clean mock boundary for unit tests
- Can be used independently by code that needs git operations without
  the full `BaseGitFetcher` lifecycle (e.g., one-off scripts)

### Responsibility Separation

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

### Function Catalog

The following table defines the complete public interface of
`git_operations.py`. These are the functions that `BaseGitFetcher`
delegates to and that any `BaseFetcher`-direct subclass may also call.

#### Clone Operations

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

#### Fetch Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `fetch_origin` | `async def fetch_origin(repo_path: Path) -> None` | `None` | Fetch (5 min) | `GitFetchError` |

Semantics: runs `git fetch origin` in the specified repository.
Incremental — only new objects are transferred.

#### Read Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `get_head_sha` | `async def get_head_sha(repo_path: Path) -> str` | 40-char hex SHA | Read (30 sec) | `GitCorruptionError` |
| `get_commit_date` | `async def get_commit_date(repo_path: Path, ref: str) -> str` | ISO 8601 date string (e.g., `2025-06-01T10:30:00+00:00`) | Read (30 sec) | `GitCorruptionError` |
| `is_clone_valid` | `async def is_clone_valid(repo_path: Path) -> bool` | `bool` | Read (30 sec) | Never (returns `False` on any failure) |
| `check_sha_reachable` | `async def check_sha_reachable(repo_path: Path, sha: str) -> bool` | `bool` | Read (30 sec) | `GitCorruptionError` (only for unexpected failures; unreachable SHA returns `False`) |
| `diff_names` | `async def diff_names(repo_path: Path, from_sha: str, to_sha: str, *, path_filter: str \| None = None) -> list[str]` | List of file paths | Read (30 sec) | `GitCorruptionError` |
| `rev_list_before` | `async def rev_list_before(repo_path: Path, before_date: str) -> str \| None` | 40-char hex SHA or `None` | Read (30 sec) | `GitCorruptionError` |

Semantics:

- **`get_head_sha`**: returns the commit SHA that HEAD points to
  (`git rev-parse HEAD`)
- **`get_commit_date`**: returns the committer date of the specified ref
  as an ISO 8601 string (`git log -1 --format=%cI <ref>`). Used to
  store `committed_at` in the cursor for recovery boundary computation
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

#### Show Operations

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

#### Filesystem Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `delete_clone` | `async def delete_clone(path: Path) -> None` | `None` | N/A | `OSError` (filesystem errors) |

Semantics: recursively deletes the directory at `path` if it exists.
No-op if the path does not exist. This is NOT a git operation — it is
included in the module for co-location with clone lifecycle management.

### Bare and Blobless Compatibility

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

---

## Application Plan

This draft covers specification changes only. All modifications happen
in documentation files. No implementation code is written as part of
this plan — the updated specifications will serve as the unambiguous
reference for a future implementer.

**Pre-requisite applied**: the "Function Specification Completeness"
convention has already been added to `docs/conventions.md`. This draft's
content follows the convention: `execute()` uses the fetcher algorithm
exclusion (Properties + Algorithm + Error handling); hook methods use
the abstract contract exclusion (Signature + Contract semantics);
`fetch_single()` answers Category A questions (Q1-Q6);
`_compute_recovery_delta()` answers Category B questions (Q1/Q3/Q6);
`git_operations.py` functions use the consolidated-groups pattern
(signature table + semantics/behavior).

### Step 1: Add `BaseGitFetcher` class specification

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Position**: new subsection `### BaseGitFetcher Class` after
"Implementation Location" (currently ending at line 1476), before
"Fetcher Documentation Requirements."

**Content to add**:

1. Purpose paragraph: Template Method intermediate class for fetchers
   that follow the standard delta-based git flow. Eliminates duplicated
   state machine code.
2. Class hierarchy diagram:
   ```
   BaseFetcher → BaseGitFetcher → SyncMitreCves / SyncKernelCves
   ```
3. File location: `backend/app/services/base_git_fetcher.py`
4. Class attributes table (from this draft's "Interface Specification —
   Class Attributes" section — reproduce in full). Note:
   `recovery_window` has been removed; recovery uses the `committed_at`
   field stored in the cursor
5. Template Method contract:
   - `execute()` is NOT overridable by concrete subclasses
   - The method implements the state machine defined in "First-Run
     Detection" and "Cursor SHA Unreachable" (this same spec)
   - Cursor format: `{"sha": "<hex>", "committed_at": "<ISO 8601>"}`
   - Numbered-step Behavior of `execute()` (from this draft's "Template
     Method" section — uses the fetcher algorithm documentation template
     per the exclusion in Function Specification Completeness), including:
     - Error handling strategy table (infrastructure errors propagate)
     - Safety check for "all items failed" → cursor not advanced
     - Status determination table
6. Hook methods table and contracts (from this draft's "Hook Methods"
   section — `process_item`, `filter_delta_files`,
   `deduplicate_items`). Note: `process_item()` receives `session` as
   an explicit parameter; calls metric helpers directly (no `ItemResult`
   return type). Include the "two-level filtering" rationale (coarse
   git-level `delta_path_prefix` vs. fine-grained Python
   `filter_delta_files()` hook) in the `filter_delta_files()` contract
   description
7. Default `fetch_single()` numbered-step Behavior with
   `_construct_candidate_paths()` hook (from this draft), including
   exception semantics (`RuntimeError` vs `CVENotInSource`)
8. Inherited utility methods table (from this draft), including
   `_compute_recovery_delta()` numbered-step Behavior
9. "When NOT to Use" criteria (from this draft's "When NOT to Use
   BaseGitFetcher" section — reproduce in full)

### Step 2: Add `git_operations.py` function catalog

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Position**: expand the existing "Implementation Location" subsection
(lines 1447-1476) with a new "#### Function Catalog" sub-subsection
after the "Design Principles" paragraph.

**Content to add**:

1. Responsibility separation note (from this draft's "Responsibility
   Separation" section — reproduce verbatim)
2. Function catalog tables with full signatures, timeouts, and
   exceptions (from this draft's "Function Catalog" section —
   reproduce all categories: Clone, Fetch, Read, Show, Filesystem)
3. Numbered-step Behavior sections for functions with branching logic
   (`clone`, `check_sha_reachable`, `show_file`) — these answer Q1/Q3/Q6
   per Category B. Functions with a single execution path use the
   consolidated-groups pattern (signature table + semantics paragraph)
4. Bare and blobless compatibility note (from this draft's "Bare and
   Blobless Compatibility" section — reproduce in full)

This expands the currently vague "Async functions for each git
operation category" description into a concrete, implementable
interface specification.

### Step 3: Update "Implementation Location" and "Design Principles"

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Changes** (lines 1461-1476):

Replace:

> Is independent of `BaseFetcher` lifecycle — fetchers compose these
> utilities within their `execute()` method

With:

> Is consumed by `BaseGitFetcher` methods, which delegate subprocess
> execution to this module. Can also be used independently by code that
> needs git operations without the `BaseGitFetcher` lifecycle (e.g.,
> fetchers inheriting from `BaseFetcher` directly)

Replace:

> Each fetcher retains full control over its execution flow, using the
> utility functions as building blocks.

With:

> Fetchers that inherit from `BaseGitFetcher` delegate execution flow
> to the template method — they implement only processing hooks.
> Fetchers that inherit from `BaseFetcher` directly retain full control
> over their execution flow, using the utility functions as building
> blocks.

### Step 4: Update First-Run Detection and replace Recovery algorithm

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Change 1** — at the end of "First-Run Detection" section (after
line 1226), add:

> For `BaseGitFetcher` subclasses, this decision matrix is implemented
> by `BaseGitFetcher.execute()` — concrete fetchers do not reimplement
> it. See "BaseGitFetcher Class" below.

**Change 2** — **replace** the "Cursor SHA Unreachable" subsection
(lines 1334-1377) in its entirety. The new content describes the
date-based recovery algorithm as the primary mechanism:

> #### Cursor SHA Unreachable
>
> When a git-based fetcher's stored cursor SHA is not reachable in the
> local clone (detected via `git cat-file -t <sha>` returning
> non-zero), it applies a date-based recovery strategy using the
> `committed_at` field stored in the cursor. This situation occurs
> when:
>
> - The clone was rebuilt (row 4 of the First-Run Detection table)
> - The upstream repository was force-pushed or rebased (rare for
>   published CVE/advisory repos)
> - Git garbage collection pruned unreachable objects (should not
>   happen for commits reachable from HEAD, but possible with
>   corrupted state)
>
> **Algorithm**:
>
> 1. Compute `before_date` as `cursor_committed_at` minus 1 day (the
>    1-day margin ensures no items are missed around the boundary —
>    reprocessing is idempotent)
> 2. Determine boundary SHA:
>    `git rev-list -1 --before="<before_date>" HEAD`
> 3. If no commit exists before `before_date` (empty output — the
>    repository history does not extend that far back): log WARNING
>    ("Recovery boundary not found — treating as first-run"), return
>    empty delta. Cursor advances to HEAD
> 4. Compute delta:
>    `git diff --name-only --diff-filter=AMCR <boundary_sha>..HEAD
>    -- '<recovery_path_prefix>'`
> 5. Apply the fetcher's normal file filtering and per-item processing
>    logic (MUST be idempotent — previously ingested items produce no
>    observable side effects on re-processing)
> 6. Write HEAD as new cursor on completion
>
> Each git-based fetcher declares this parameter in its properties
> table:
>
> | Parameter | Description | Example values |
> |---|---|---|
> | `recovery_path_prefix` | Path filter for the recovery delta
>   command | `cves/` (MITRE), `cve/` (kernel) |
>
> **Advantages over a fixed window**: the date-based approach always
> covers the exact gap regardless of how long the fetcher was offline.
> Reprocessing overlap is always ~1 day (idempotent, negligible cost).
> No configurable `recovery_window` parameter is needed.
>
> **Normal case after re-clone**: when a clone is rebuilt from the
> same remote (row 4 of First-Run Detection), the cursor SHA is
> almost always reachable because git history is preserved. In this
> case, normal delta detection proceeds — no recovery is needed. The
> recovery strategy is a fallback for the rare case where the SHA
> truly does not exist in the fresh clone.
>
> For `BaseGitFetcher` subclasses, this recovery algorithm is
> implemented by `BaseGitFetcher.execute()` — concrete fetchers only
> declare `recovery_path_prefix` as a class attribute. See
> "BaseGitFetcher Class" below.

**Change 3** — in the "Cursor Persistence" section (around line 1166),
update the cursor format example:

Replace:

> ```json
> {"sha": "<40-char hex SHA>"}
> ```

With:

> ```json
> {"sha": "<40-char hex SHA>", "committed_at": "<ISO 8601 date>"}
> ```
>
> The next run reads the cursor from the most recent `FetcherRun` with
> `status IN ('success', 'partial')` for the same `fetcher_name`:
>
> - `sha`: the HEAD commit SHA at the end of a successful run
> - `committed_at`: the committer date of that commit (ISO 8601
>   format). Used as the recovery boundary when the cursor SHA becomes
>   unreachable (see "Cursor SHA Unreachable" below)

### Step 5: Add naming convention

**File**: `docs/conventions.md`

**Position**: after the existing fetcher naming bullet (line 185), add
a new bullet:

```markdown
- **Git-based fetchers (delta-flow)**: inherit from `BaseGitFetcher`
  (`backend/app/services/base_git_fetcher.py`). Only implement
  `process_item()` and optionally `filter_delta_files()` /
  `deduplicate_items()`. Do NOT override `execute()`
```

### Step 6: Update registry detection predicates

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Change 1** — in `get_fetch_single_fetchers()` (around line 489),
replace the detection predicate description:

Replace:

> `'fetch_single' in cls.__dict__` checks for a concrete
> implementation on the class itself, not inherited methods. This
> prevents false positives if `BaseFetcher` ever declares
> `fetch_single` as abstract or raising `NotImplementedError`.
> Consequence: concrete subclasses that inherit `fetch_single()` from
> a parent class without overriding it are NOT returned by this
> accessor and will NOT be dispatched for on-demand fetches.

With:

> The detection predicate walks the class's MRO (Method Resolution
> Order), checking for `fetch_single` in each class's `__dict__`,
> excluding `BaseFetcher` and `object`:
>
> ```python
> any(
>     'fetch_single' in klass.__dict__
>     for klass in cls.__mro__
>     if klass is not BaseFetcher and klass is not object
> )
> ```
>
> This detects `fetch_single()` defined on the class itself OR on any
> intermediate class (e.g., `BaseGitFetcher`). `BaseFetcher` is
> excluded to prevent false positives if it ever declares
> `fetch_single` as abstract or raising `NotImplementedError`.
> Consequence: concrete subclasses that inherit `fetch_single()` from
> an intermediate class (like `BaseGitFetcher`) ARE correctly returned
> by this accessor and WILL be dispatched for on-demand fetches.

**Change 2** — in `get_catch_up_fetchers()` (around line 607),
replace the `fetch_single` detection check with the same MRO-based
predicate. Update the description:

Replace:

> `'fetch_single' in cls.__dict__` — fetcher implements
> `fetch_single()`, which means it inherits the default `catch_up()`

With:

> MRO-based `fetch_single` detection (same predicate as
> `get_fetch_single_fetchers()`) — fetcher implements
> `fetch_single()` at any level in its hierarchy (excluding
> `BaseFetcher`), which means it inherits the default `catch_up()`

**Change 3** — in the default `catch_up()` implementation (around
line 557), update the guard:

Replace:

> ```python
> if 'fetch_single' not in type(self).__dict__:
>     return  # no fetch_single, no default catch_up
> ```

With:

> ```python
> if not any(
>     'fetch_single' in klass.__dict__
>     for klass in type(self).__mro__
>     if klass is not BaseFetcher and klass is not object
> ):
>     return  # no fetch_single in hierarchy, no default catch_up
> ```

### Step 7: Simplify MITRE fetcher specification

**File**: `docs/features/tickets/cve-tracking.md`

**Change 1** — extend properties table (after line 527) with
`BaseGitFetcher` attributes, and rename existing recovery-related rows
to match the new attribute names
(`Recovery file filter` → `recovery_path_prefix`):

```markdown
| Inherits | `BaseGitFetcher` |
| `repo_url` | `https://github.com/CVEProject/cvelistV5.git` |
| `clone_dir_name` | `cvelistV5` |
| `clone_bare` | `True` |
| `clone_filter` | `"blob:none"` (server supports partial clone protocol v2) |
| `clone_single_branch` | `True` |
| `delta_path_prefix` | `cves/` |
| `recovery_path_prefix` | `cves/` |
```

**Change 2** — replace algorithm steps 1-3 and step 6 (clone, fetch,
delta detection, state persistence) with:

> Clone management, fetch, SHA reachability check, delta detection,
> recovery strategy, and cursor persistence are handled by
> `BaseGitFetcher.execute()` — see
> `docs/features/platform/fetcher-infrastructure.md` (BaseGitFetcher
> Class). The following documents only the hook implementations
> specific to this fetcher.

**Change 3** — renumber remaining algorithm steps:

1. **File filtering** (`filter_delta_files()` hook) — current step 4
   text preserved unchanged
2. **Processing** (`process_item()` hook) — current step 5 text
   preserved unchanged (CNA container, ADP containers, CISA-ADP,
   upsert_cve, upsert_references)
3. **Phase 2 side effects** — current step 7 text preserved unchanged

**Preserved sections** (no changes): CVE JSON 5.x Field Path Mapping,
`fetch_single()` Implementation, Git Concurrency Rules, Storage and
Recovery, Error Handling. These sections document behavior specific to
this fetcher's hooks or already reference the shared infrastructure.

### Step 8: Simplify kernel fetcher specification

**File**: `docs/features/tickets/cve-tracking.md`

**Change 1** — extend properties table (after line 827) with
`BaseGitFetcher` attributes, remove the existing `| Queue | "git" |`
row (now inherited from `BaseGitFetcher`), and rename existing
recovery-related rows to match the new attribute names
(`Recovery file filter` → `recovery_path_prefix`):

```markdown
| Inherits | `BaseGitFetcher` |
| `repo_url` | `https://git.kernel.org/pub/scm/linux/security/vulns.git` |
| `clone_dir_name` | `vulns.git` |
| `clone_bare` | `True` |
| `clone_filter` | `None` (server does not advertise `filter` capability) |
| `clone_single_branch` | `True` |
| `delta_path_prefix` | `cve/` |
| `recovery_path_prefix` | `cve/` |
```

**Change 2** — replace algorithm steps 1-3 and step 8 (clone, fetch,
delta detection, store cursor) with the same reference paragraph as
Step 7:

> Clone management, fetch, SHA reachability check, delta detection,
> recovery strategy, and cursor persistence are handled by
> `BaseGitFetcher.execute()` — see
> `docs/features/platform/fetcher-infrastructure.md` (BaseGitFetcher
> Class). The following documents only the hook implementations
> specific to this fetcher.

**Change 3** — renumber remaining algorithm steps:

1. **File filtering** (`filter_delta_files()` hook) — current step 4
   text preserved unchanged
2. **Processing per CVE** (`process_item()` + `deduplicate_items()`
   hooks) — current step 5 text preserved unchanged (deduplicate,
   derive cve_state from path, parse JSON, set resolved_packages,
   upsert_cve, construct reference URL, upsert_references)
3. **Phase 2 side effects** — current step 6 text preserved unchanged
4. **Batch error handling** — current step 7 text preserved unchanged

**Preserved sections** (no changes): Why a Dedicated Fetcher,
Rejection Handling, Key Differences from MITRE, `fetch_single()`
Implementation, field mapping tables.

### Step 9: Verify with reviewers

Invoke the following reviewers to verify the applied changes are
correct and coherent:

1. `@spec-coherence-reviewer` on
   `docs/features/platform/fetcher-infrastructure.md` — verify the new
   BaseGitFetcher subsection does not contradict existing sections
   (BaseFetcher contract, First-Run Detection, Recovery, Concurrency
   Rules)
2. `@spec-coherence-reviewer` on
   `docs/features/tickets/cve-tracking.md` — verify the simplified
   fetcher algorithms remain unambiguous and consistent with the
   referenced BaseGitFetcher specification
3. `@docs-placement-reviewer` — verify that the BaseGitFetcher class
   specification is in the correct location
   (fetcher-infrastructure.md, not cve-tracking.md or conventions.md)
4. `@spec-gap-analyzer` on
   `docs/features/platform/fetcher-infrastructure.md` — verify the
   new class specification has no functional gaps (missing edge cases,
   undefined behavior, ambiguous contracts)

### Step 10: Delete this draft

Remove `docs/drafts/git-base-fetcher-class.md` from the repository.
The draft's content has been fully incorporated into the approved
specifications.

---

## Design Decisions

### D1: `execute()` is final (no override)

Concrete subclasses MUST NOT override `execute()`. If a future
git-based fetcher needs a fundamentally different flow, it MUST inherit
from `BaseFetcher` directly (see "When NOT to Use BaseGitFetcher"
above). Allowing `execute()` overrides defeats the purpose of the
Template Method and makes the state machine unpredictable.

**Enforcement**: Python has no `final` keyword for methods. The
constraint is enforced through:
- Class docstring explicitly stating the prohibition
- `fetcher-infrastructure.md` documenting the contract
- Code review (structural invariant check)

### D2: File location

`backend/app/services/base_git_fetcher.py` — parallel to
`backend/app/services/base_fetcher.py`. This keeps fetcher base classes
together and separate from concrete implementations in
`backend/app/tasks/`.

### D3: `_clone_repo()` is not overridable

Clone options are fully configurable via class attributes (`clone_bare`,
`clone_filter`, `clone_single_branch`). If a future source needs truly
custom clone behavior (e.g., sparse checkout), it is different enough to
warrant direct `BaseFetcher` inheritance — see applicability criteria
above.

### D4: Async subprocess calls

The utility module functions are async (using
`asyncio.create_subprocess_exec`), consistent with the async
architecture defined in `fetcher-infrastructure.md`. Since
`BaseFetcher.run()` and `execute()` are `async def`, the event loop is
available — `BaseGitFetcher` awaits git operations normally.

This aligns with the existing specification: "All git operations are
performed via async subprocess invocation of the system git binary
through a shared internal helper." The async pattern avoids blocking
the event loop during long-running operations (20-minute clone
timeout) while keeping the code straightforward with `await`.

### D5: No `ItemResult` enum — imperative metric calls

`process_item()` calls `self.record_created()` / `self.record_updated()`
directly rather than returning a typed result. This matches the
`BaseFetcher` convention where all fetchers (git-based or not) use
the same imperative metric helpers. Adding an enum would introduce a
git-specific abstraction that diverges from the standard pattern for
no practical benefit.

### D6: No circuit breaker / abort threshold

The template method processes all items in the delta regardless of
failure count. Rationale:

- Deltas are typically small (hundreds of files, not thousands)
- If the database is down, each `process_item()` fails fast
- If the network is down in blobless clones, `show_file()` fails fast
- The "all items failed" safety check prevents cursor advance, so no
  data is lost
- Adding a configurable abort threshold is premature complexity without
  operational evidence of need

If future experience reveals that large deltas with cascading failures
cause operational problems, an abort threshold can be added as a class
attribute at that time.

### D7: No dedicated exception class for clone unavailable

`fetch_single()` raises `RuntimeError` (not a custom
`CloneUnavailableError`) when the clone is missing. Rationale:

- The `trigger_on_demand_fetch()` dispatch system catches
  `CVENotInSource` specifically and treats everything else as "source
  failed, try next" — no dedicated exception class is needed for
  correct routing
- The scenario is rare (only happens if `fetch_single()` is called
  before the first periodic sync)
- Adding a new exception class for a single `raise` site is unnecessary
  indirection

If future fetchers introduce other "prerequisite unavailable" scenarios
that need differentiated handling, a common base class can be introduced
at that time.

### D8: Date-based recovery instead of fixed window

When the cursor SHA is unreachable, recovery uses the stored
`committed_at` date (minus 1 day margin) to find the boundary commit,
rather than a fixed time window (e.g., "2 weeks").

**Advantages over a fixed window**:

- **Complete gap coverage**: the recovery always covers the exact gap
  regardless of how long the fetcher was offline (3 days or 3 months).
  A fixed window would lose data for gaps exceeding the window size
- **No "window exceeded" edge case**: the problematic scenario (gap >
  window → data loss → what status to report?) is eliminated entirely
- **No configurable parameter**: `recovery_window` is removed as a
  class attribute — one fewer design decision for concrete subclasses
- **Proportional overlap**: reprocessing is always ~1 day (idempotent,
  negligible cost), not a fixed window that could be wastefully large

**Edge case**: if no commit exists before `committed_at - 1 day`
(requires complete repository recreation with new dates — virtually
impossible for CVE repos), the run is treated as a first-run: cursor
advances to HEAD, zero items processed, WARNING logged. Manual recovery
via `fetch_single()` is available for specific items.

**Cursor format change**: `{"sha": "..."}` becomes
`{"sha": "...", "committed_at": "..."}`. The additional field requires
one extra git operation per run (`git log -1 --format=%cI HEAD`) — a
read-category operation completing in milliseconds.

---

## Cross-References

- Fetcher infrastructure: `docs/features/platform/fetcher-infrastructure.md`
- BaseFetcher contract: `docs/features/platform/fetcher-infrastructure.md` (section "Base Class Interface")
- CVE tracking (MITRE + kernel): `docs/features/tickets/cve-tracking.md`
- Code conventions: `docs/conventions.md`
- Function specification completeness: `docs/conventions.md` (section "Function Specification Completeness")

---

## Open Items

All items resolved. Draft ready for Application Plan execution.
