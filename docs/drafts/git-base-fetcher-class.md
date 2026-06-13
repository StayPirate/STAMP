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
  input/output (receives `path` + `bytes`, calls metric helpers)
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
| `recovery_window` | `str` | `"2 weeks"` | Look-back period for recovery reprocessing |
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

```python
async def execute(self, session: AsyncSession) -> None:
    """Git-based fetcher lifecycle — Template Method.
    
    Implements WI-10 Part A (First-Run Detection) and
    WI-10 Part B (Recovery Strategy) from fetcher-infrastructure.md.
    
    Infrastructure errors (_clone_repo, _fetch_origin, _get_head_sha,
    _compute_delta) propagate naturally — BaseFetcher.run() catches them
    and records a failed run without advancing the cursor.
    """
    repo_path = self._repo_path()
    cursor_sha = self._get_last_cursor_sha()
    
    # === First-Run Detection (WI-10 Part A truth table) ===
    if cursor_sha is None:
        # No cursor exists — first run
        if not await self._is_clone_valid(repo_path):
            await self._delete_if_exists(repo_path)
            await self._clone_repo(repo_path)
        # else: clone exists and is valid — skip clone
        head = await self._get_head_sha(repo_path)
        self._cursor = {"sha": head}
        return
    
    # === Subsequent Run ===
    if not await self._is_clone_valid(repo_path):
        # Cursor exists but clone is invalid — rebuild
        logger.warning("Clone invalid but cursor exists — rebuilding")
        await self._delete_if_exists(repo_path)
        await self._clone_repo(repo_path)
    else:
        await self._fetch_origin(repo_path)
    
    head = await self._get_head_sha(repo_path)
    
    # === SHA Reachability Check (WI-10 Part B) ===
    if not await self._check_sha_reachable(repo_path, cursor_sha):
        logger.warning("Cursor SHA %s unreachable — applying recovery", cursor_sha)
        file_list = await self._compute_recovery_delta(repo_path, head)
    else:
        file_list = await self._compute_delta(repo_path, cursor_sha, head)
    
    # === File Filtering (hook) ===
    filtered = self.filter_delta_files(file_list)
    
    # === Deduplication (hook) ===
    filtered = self.deduplicate_items(filtered)
    
    # === Per-Item Processing (hook) ===
    for path in filtered:
        try:
            content = await self._show_file(repo_path, "HEAD", path)
            if content is None:
                self.record_failed()
                continue
            await self.process_item(path, content)
        except Exception as e:
            logger.warning("Failed to process %s: %s", path, str(e))
            self.record_failed()
    
    # === Safety Check: prevent cursor advance on total failure ===
    if self._items_failed > 0 and (self._items_created + self._items_updated) == 0:
        raise RuntimeError(
            f"All {self._items_failed} items failed — cursor not advanced for safety"
        )
    
    self._cursor = {"sha": head}
```

#### Error Handling Strategy

The template method does NOT catch infrastructure-level exceptions.
This is intentional — `BaseFetcher.run()` already provides the correct
behavior:

| Infrastructure failure | Exception | BaseFetcher behavior |
|------------------------|-----------|---------------------|
| Clone fails (network) | `GitFetchError` | `status = failure`, cursor not advanced |
| Fetch fails (network) | `GitFetchError` | `status = failure`, cursor not advanced |
| HEAD unreadable (corruption) | `GitCorruptionError` | `status = failure`, cursor not advanced |
| Delta computation fails | `GitCorruptionError` | `status = failure`, cursor not advanced |

On the next scheduled run, the First-Run Detection truth table
re-evaluates the clone state and applies the appropriate recovery
(row "Cursor exists + Clone invalid" → re-clone).

The **safety check** at the end of the loop prevents a dangerous edge
case: if all items fail (e.g., network drops after fetch in a blobless
clone, making every `show_file()` fail), the cursor must NOT advance —
otherwise those items are permanently lost. The `RuntimeError` causes
`BaseFetcher.run()` to record `status = failure` and preserve the
previous cursor, so the next run retries the same delta.

#### Status Determination

`BaseGitFetcher` relies entirely on `BaseFetcher`'s existing status
mechanism — no additional logic is needed:

| Scenario | Status | Cursor advances? |
|----------|--------|-----------------|
| First run (no processing) | `success` | Yes |
| Empty delta (HEAD unchanged) | `success` | Yes |
| All items succeed | `success` | Yes |
| Some items fail, some succeed | `partial` | Yes |
| All items fail (safety check) | `failure` | No |
| Infrastructure error | `failure` | No |

**Note**: the above is illustrative pseudo-code showing the flow. The
actual implementation may differ in details (async patterns, error
propagation) while preserving the same state machine semantics.

### Hook Methods (Override Points)

These are the extension points for concrete subclasses:

#### Hooks for `execute()`

| Method | Required? | Default | Purpose |
|--------|-----------|---------|---------|
| `process_item(path, content)` | **Yes** (abstract) | — | Process a single file from the delta. Calls `self.record_created()` or `self.record_updated()` on success |
| `filter_delta_files(file_list)` | No | Return all | Filter raw delta output to relevant files (e.g., only `.json` in specific dirs) |
| `deduplicate_items(file_list)` | No | No-op | Deduplicate items before processing (e.g., same CVE-ID in both `published/` and `rejected/`) |

#### Hooks for `fetch_single()`

| Method | Required? | Default | Purpose |
|--------|-----------|---------|---------|
| `_construct_candidate_paths(item_id)` | **Yes** (abstract) | — | Return ordered list of candidate file paths for local clone lookup |

#### `process_item(path: str, content: bytes) -> None`

The core extension point. Receives:
- `path`: relative path within the repository (e.g., `cve/published/2024/CVE-2024-50055.json`)
- `content`: raw file content as bytes (from `git show`)

The hook is responsible for:
1. Parsing the content and applying business logic (upsert, etc.)
2. Calling `self.record_created()` or `self.record_updated()` to report
   the outcome (same pattern as non-git `BaseFetcher` subclasses)
3. Returning `None` if the item was skipped (already up-to-date) —
   no metric is recorded, which is the correct behavior

Raises any exception on failure → caught by `execute()`, logged,
`record_failed()` called.

**Database access**: the `session: AsyncSession` parameter received by
`execute()` is stored as `self._session` and available to hooks. This
follows the same pattern as `BaseFetcher` (session injected by `run()`
and accessible during execution).

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

| Method | Purpose |
|--------|---------|
| `_get_last_cursor_sha()` | Reads the `"sha"` field from the previous `FetcherRun.cursor` (via `BaseFetcher`). Returns `None` if no prior successful run exists |
| `_repo_path()` | Returns `Path($GIT_CLONE_BASE_DIR / clone_dir_name)` |
| `_clone_repo(path)` | Clones the repository with configured options (bare, filter, single-branch) |
| `_fetch_origin(path)` | Runs `git fetch origin` |
| `_get_head_sha(path)` | Returns current HEAD SHA |
| `_is_clone_valid(path)` | Returns bool (checks `git rev-parse --git-dir`) |
| `_check_sha_reachable(path, sha)` | Returns bool (checks `git cat-file -t`) |
| `_compute_delta(path, from_sha, to_sha)` | Returns file list from `git diff` with `delta_path_prefix` |
| `_compute_recovery_delta(path, head)` | Applies recovery window + `recovery_path_prefix`. Logs WARNING and returns empty list if window exceeded (boundary == HEAD) |
| `_show_file(path, ref, file_path)` | Returns file content or None (from `git show`) |
| `_delete_if_exists(path)` | Deletes directory if it exists |
| `_find_boundary_sha(path, before)` | Returns SHA from `git rev-list --before` |

#### Recovery Window Exceeded

When `_compute_recovery_delta()` detects that the boundary SHA equals
HEAD (no commits exist before the recovery window — the gap exceeds
the window), it:

1. Logs `WARNING: "Recovery window exceeded — cursor SHA was
   unreachable and recovery window cannot cover the gap. Some items
   may have been missed. Use fetch_single() for manual recovery of
   specific items."`
2. Returns an empty list

The run then completes normally: zero items processed, cursor advances
to HEAD, `status = success`. The operator monitors logs for this
WARNING. This scenario is extremely rare (requires a force-push on a
public CVE repository combined with a gap exceeding the recovery
window). Manual recovery via `fetch_single()` is available for
specific items.

### `fetch_single()` Integration

`BaseGitFetcher` provides a default `fetch_single()` implementation
that concrete subclasses can use or override:

```python
async def fetch_single(self, item_id: str) -> None:
    """Default fetch_single: look up item in local clone (read-only).
    
    Subclasses override _construct_candidate_paths() and
    process_item() to customize behavior.
    """
    repo_path = self._repo_path()
    if not await self._is_clone_valid(repo_path):
        raise RuntimeError(
            f"Clone not available at {repo_path} for single-item lookup"
        )
    
    for path in self._construct_candidate_paths(item_id):
        content = await self._show_file(repo_path, "HEAD", path)
        if content is not None:
            await self.process_item(path, content)
            return
    
    raise CVENotInSource(item_id)
```

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

Semantics: clones a git repository into `dest` with the specified
options. If `bare=True`, uses `--bare`. If `filter_spec` is set, uses
`--filter=<filter_spec>`. If `single_branch=True`, uses
`--single-branch`. The function does NOT apply domain-specific defaults
— the caller passes explicit values.

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
| `is_clone_valid` | `async def is_clone_valid(repo_path: Path) -> bool` | `bool` | Read (30 sec) | Never (returns `False` on any failure) |
| `check_sha_reachable` | `async def check_sha_reachable(repo_path: Path, sha: str) -> bool` | `bool` | Read (30 sec) | `GitCorruptionError` (only for unexpected failures; unreachable SHA returns `False`) |
| `diff_names` | `async def diff_names(repo_path: Path, from_sha: str, to_sha: str, *, path_filter: str \| None = None) -> list[str]` | List of file paths | Read (30 sec) | `GitCorruptionError` |
| `rev_list_before` | `async def rev_list_before(repo_path: Path, before_date: str) -> str` | 40-char hex SHA | Read (30 sec) | `GitCorruptionError` |

Semantics:

- **`get_head_sha`**: returns the commit SHA that HEAD points to
  (`git rev-parse HEAD`)
- **`is_clone_valid`**: returns `True` if `repo_path` is a valid git
  repository (`git rev-parse --git-dir` succeeds). Returns `False` if
  the directory does not exist, is not a git repository, or the check
  fails for any reason. NEVER raises — used as a guard condition
- **`check_sha_reachable`**: returns `True` if the given SHA exists in
  the local object store and is a valid git object
  (`git cat-file -t <sha>` succeeds). Returns `False` if the SHA is
  not reachable. Raises `GitCorruptionError` only on unexpected
  failures (e.g., repository structure is broken)
- **`diff_names`**: returns the list of added, modified, copied, and
  renamed files between two commits
  (`git diff --name-only --diff-filter=AMCR <from>..<to>`). If
  `path_filter` is set, appends `-- '<path_filter>'` to restrict
  results. Deleted files are excluded
- **`rev_list_before`**: returns the most recent commit SHA on HEAD
  before the specified date
  (`git rev-list -1 --before="<before_date>" HEAD`). Used for
  recovery window boundary detection

#### Show Operations

| Function | Signature | Returns | Timeout | Raises |
|----------|-----------|---------|---------|--------|
| `show_file` | `async def show_file(repo_path: Path, ref: str, file_path: str) -> bytes \| None` | File content as `bytes`, or `None` if path does not exist | Read (30 sec) | `GitFileError` (for errors other than "path not found") |

Semantics: reads a single file's content from the git object store
(`git show <ref>:<file_path>`). Returns `None` if the file does not
exist at the given ref (distinguishes "file not found" from "git
error"). In blobless clones, this triggers an on-demand blob download
from the remote — requires network access.

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

- **Local-only operations** (`get_head_sha`, `is_clone_valid`,
  `check_sha_reachable`, `diff_names`, `rev_list_before`): access only
  commit and tree objects, which are always present locally in both
  plain and blobless clones. No network access required
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
   Class Attributes" section — reproduce in full)
5. Template Method contract:
   - `execute()` is NOT overridable by concrete subclasses
   - The method implements the state machine defined in "First-Run
     Detection" and "Cursor SHA Unreachable" (this same spec)
   - Pseudo-code of `execute()` (from this draft's "Template Method"
     section), including:
     - Error handling strategy table (infrastructure errors propagate)
     - Safety check for "all items failed" → cursor not advanced
     - Status determination table
6. Hook methods table and contracts (from this draft's "Hook Methods"
   section — `process_item`, `filter_delta_files`,
   `deduplicate_items`). Note: `process_item()` calls metric helpers
   directly (no `ItemResult` return type)
7. Default `fetch_single()` implementation with
   `_construct_candidate_paths()` hook (from this draft), including
   exception semantics (`RuntimeError` vs `CVENotInSource`)
8. Inherited utility methods table (from this draft), including
   "Recovery Window Exceeded" behavior for `_compute_recovery_delta()`
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
2. Function catalog tables with full signatures, semantics, timeouts,
   and exceptions (from this draft's "Function Catalog" section —
   reproduce all categories: Clone, Fetch, Read, Show, Filesystem)
3. Bare and blobless compatibility note (from this draft's "Bare and
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

### Step 4: Annotate First-Run Detection and Recovery sections

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Change 1** — at the end of "First-Run Detection" section (after
line 1226), add:

> For `BaseGitFetcher` subclasses, this decision matrix is implemented
> by `BaseGitFetcher.execute()` — concrete fetchers do not reimplement
> it. See "BaseGitFetcher Class" below.

**Change 2** — at the end of "Cursor SHA Unreachable" algorithm
(after line 1371), add:

> For `BaseGitFetcher` subclasses, this recovery algorithm is
> implemented by `BaseGitFetcher.execute()` — concrete fetchers only
> declare `recovery_window` and `recovery_path_prefix` as class
> attributes. See "BaseGitFetcher Class" below.

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

### Step 6: Simplify MITRE fetcher specification

**File**: `docs/features/tickets/cve-tracking.md`

**Change 1** — extend properties table (after line 527) with
`BaseGitFetcher` attributes, and rename existing recovery-related rows
to match the new attribute names (`Recovery window` → `recovery_window`,
`Recovery file filter` → `recovery_path_prefix`):

```markdown
| Inherits | `BaseGitFetcher` |
| `repo_url` | `https://github.com/CVEProject/cvelistV5.git` |
| `clone_dir_name` | `cvelistV5` |
| `clone_bare` | `True` |
| `clone_filter` | `"blob:none"` (server supports partial clone protocol v2) |
| `clone_single_branch` | `True` |
| `delta_path_prefix` | `cves/` |
| `recovery_path_prefix` | `cves/` |
| `recovery_window` | `2 weeks` |
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

### Step 7: Simplify kernel fetcher specification

**File**: `docs/features/tickets/cve-tracking.md`

**Change 1** — extend properties table (after line 827) with
`BaseGitFetcher` attributes, remove the existing `| Queue | "git" |`
row (now inherited from `BaseGitFetcher`), and rename existing
recovery-related rows to match the new attribute names
(`Recovery window` → `recovery_window`,
`Recovery file filter` → `recovery_path_prefix`):

```markdown
| Inherits | `BaseGitFetcher` |
| `repo_url` | `https://git.kernel.org/pub/scm/linux/security/vulns.git` |
| `clone_dir_name` | `vulns.git` |
| `clone_bare` | `True` |
| `clone_filter` | `None` (server does not advertise `filter` capability) |
| `clone_single_branch` | `True` |
| `delta_path_prefix` | `cve/` |
| `recovery_path_prefix` | `cve/` |
| `recovery_window` | `2 weeks` |
```

**Change 2** — replace algorithm steps 1-3 and step 8 (clone, fetch,
delta detection, store cursor) with the same reference paragraph as
Step 6:

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

### Step 8: Verify with reviewers

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

### Step 9: Delete this draft

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

### D8: Recovery window exceeded — simple WARNING, no forced status

When the recovery window cannot cover the gap (boundary == HEAD), the
run completes normally with `status = success` and zero items processed.
The operator is alerted via a WARNING in logs. Rationale:

- The scenario is extremely rare (force-push + gap > recovery window)
- Adding a `_mark_partial()` mechanism or status override complicates
  the template method for a case that may never occur in practice
- The operator has `fetch_single()` for manual recovery of specific
  items
- The WARNING in logs is sufficient for operational awareness

---

## Cross-References

- Fetcher infrastructure: `docs/features/platform/fetcher-infrastructure.md`
- BaseFetcher contract: `docs/features/platform/fetcher-infrastructure.md` (section "Base Class Interface")
- CVE tracking (MITRE + kernel): `docs/features/tickets/cve-tracking.md`
- Code conventions: `docs/conventions.md`

---

## Open Items

- [ ] Execute reviewers on this draft to validate the design before
  applying the Application Plan: `@spec-gap-analyzer` (check for
  functional gaps in the BaseGitFetcher design) and
  `@spec-coherence-reviewer` (verify the draft does not contradict
  existing content in `fetcher-infrastructure.md`)
