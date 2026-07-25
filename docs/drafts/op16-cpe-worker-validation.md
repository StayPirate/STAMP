# OP-16 Resolution: CPE Mapping Worker Validation

Decision record for resolving OP-16 (CPE Mapping Fail-Fast Asymmetry).

## Problem

The CPE-to-package mapping (`cpe-package-mapping.json`) is validated at
boot only in the API server (FastAPI `lifespan` event), but its actual
consumers are Celery worker tasks (`resolve_ticket_packages`,
`fetch_single_cve`), which load it lazily (`lru_cache`) on first use. A
corrupted or missing file on the worker surfaces only when the first CVE
ingestion task runs — potentially hours after deployment.

## Decision

Validate the CPE mapping **only in the worker process** via a
`celeryd_after_setup` handler. Remove the existing API lifespan guard
(which was the only validation point before this change).

**Why not validate in the API server**: the API does not consume the CPE
mapping — it never calls `resolve_cpe_packages()` or
`resolve_vendor_product()`. Validating in a process that does not use
the resource provides no real guarantee for the worker process that
does: in practice, API and worker containers may run different image
versions (rolling updates, configuration errors) or have different
volume mounts (Kubernetes ConfigMaps). A passing check on the API gives
a false sense of security about the worker's state.

**Why not validate in Beat or IBS consumer**: neither process consumes
the CPE mapping, even indirectly. The IBS consumer enqueues Celery tasks
that run on workers; Beat only schedules tasks. Each process validates
only the resources it uses.

This is option (b) from OP-16. A broader startup-validation architecture
(shared connectivity primitives, pre-flight job) was evaluated and
rejected as disproportionate — the rationale is preserved in the OP-16
discussion history.

## Solution

### Shared CPE validation function

**Location**: `backend/app/core/startup_checks.py`

```python
def check_cpe_mapping() -> None:
    """Validate the CPE mapping by forcing a load through the cached loader.

    Raises CPEMappingLoadError if the file exists, is non-empty, but
    structurally invalid. On success (including file-absent or
    file-empty cases), the lru_cache is warmed for subsequent use by
    worker tasks.

    If the file is absent or empty, logs WARNING and warms the cache
    with an empty mapping — package resolution degrades gracefully
    (all lookups fall through to the raw product name fallback).
    """
```

The function invokes `resolve_cpe_packages()` with the fixed dummy CPE
`cpe:2.3:a:test:test:*:*:*:*:*:*:*:*`. The loader handles all three
cases (valid file, absent/empty file, invalid file) internally. If
loading raises `CPEMappingLoadError`, the function propagates it to the
caller.

This is a simple wrapper — it performs no I/O beyond what the loader
already does, creates no audit events, and has no side effects beyond
cache warming.

**Why a separate module** (`startup_checks.py`): separates *what* to
verify from *when and how to react* (which is the worker handler's
responsibility in `worker_startup.py`). Currently has a single consumer,
but the separation keeps the validation logic independently testable
and avoids coupling it to Celery signal mechanics.

### CPE loader runtime validation contract

The CPE mapping spec (`cpe-package-mapping.md`) currently defines
validation rules only for CI. This decision requires adding a **runtime
validation contract** to that spec — the loader itself must enforce
correctness, independent of whether CI has run.

Package resolution is a **best-effort** mechanism (see
`cpe-package-mapping.md` § Match rate expectations and `cve-service.md`
§ Phase 2). The platform functions correctly without a CPE mapping —
tickets are created normally, and VAs can add packages manually. This
informs the loader's error handling: absence or emptiness of the file
is a degraded-but-operational state, not a fatal error.

**Exception**: `CPEMappingLoadError` — a `RuntimeError` subclass defined
beside the loader in `backend/app/services/cpe_mapping.py`. Message
format: `CPE mapping load failed at {path}: {reason}`. `{reason}`
identifies the failed rule and, for structural failures, the offending
key or zero-based array index; it never includes file contents or
package values.

**Loader behavior by file state**:

| Condition | Behavior | Rationale |
|-----------|----------|-----------|
| File does not exist | Log WARNING `cpe_mapping_absent`; return empty dict | Best-effort degradation — resolution disabled, platform operational |
| File exists, zero bytes or whitespace-only | Log WARNING `cpe_mapping_absent`; return empty dict | Equivalent to absent — no meaningful content to parse |
| File exists, content is `{}` (empty JSON object) | Log WARNING `cpe_mapping_empty`; return empty dict | Explicit empty mapping — resolution disabled, platform operational |
| File exists, non-empty, structurally valid | Return populated dict | Normal operation |
| File exists, non-empty, structurally invalid | Raise `CPEMappingLoadError` | Corrupted file = deployment bug; partial/wrong mappings are worse than no mappings |

"Non-empty" for the purpose of validation means: the file contains at
least one non-whitespace character AND the content is not the empty
JSON object `{}`. Files that are zero bytes, whitespace-only, or
contain exactly `{}` are treated as graceful-degradation cases (no
validation rules applied, no error raised).

**Validation rules** (applied only when file exists and is non-empty;
checked in order, first failure raises `CPEMappingLoadError`):

1. The file is readable and valid UTF-8.
2. The content is syntactically valid JSON.
3. The root value is an object (not array, string, null, etc.).
4. No duplicate keys exist. The loader MUST use a pair-preserving
   decoder hook because a standard dict silently retains only the last
   duplicate.
5. Every key is lowercase with exactly one literal `:` separating
   non-empty `vendor` and `product` components. Key and both components
   must equal their whitespace-trimmed forms. Components MUST NOT
   contain internal whitespace (only `[a-z0-9._-]` characters are
   permitted) — keys with spaces would never be matched by the
   resolution functions, which produce underscore-separated keys from
   CPE data.
6. Every value is a non-empty array of strings. Every string is
   non-empty after trimming and must equal its trimmed form.

**Not enforced at runtime** (CI-only): alphabetical key ordering.
Unsorted but otherwise valid JSON loads successfully.

**Cache behavior**: the loader uses `@lru_cache(maxsize=1)` and returns
`dict[str, tuple[str, ...]]` (immutable tuples for package lists). Both
successful loads (populated or empty dict) are cached. A load that
raises `CPEMappingLoadError` stores no entry — the next call retries a
full read and revalidation. After one successful load, subsequent calls
return the cached mapping without file I/O. There is no hot-reload or
cache invalidation; a mapping update requires a new deployment and
process restart.

**File I/O errors** (`PermissionError`, `IsADirectoryError`, and any
other `OSError` subclass raised during file access) are wrapped in
`CPEMappingLoadError` with the underlying OS error as `{reason}`.
These indicate deployment/mount issues that prevent determining file
state.

**Non-`OSError` exceptions** (`MemoryError`, `KeyboardInterrupt`,
etc.) propagate unchanged — they are not wrapped in
`CPEMappingLoadError`.

### Unified worker startup handler

**Location**: `backend/app/core/worker_startup.py`

**Registration**: connected to Celery's `celeryd_after_setup` signal
with a stable `dispatch_uid`. The Celery app module imports the handler
module.

**Why `celeryd_after_setup`**: emitted after worker logging and queue
setup but before the consumer starts accepting tasks. The handler can
log and abort before any task runs. `worker_process_init` is unsuitable
(runs in every pool child, 4-second blocking limit).

**Sequence**:

1. Call `check_cpe_mapping()` (synchronous — no event loop needed).
   - If `CPEMappingLoadError` is raised: log CRITICAL
     `worker_startup_failed` with `stage="cpe_mapping"`,
     `error_type=type(exc).__name__`, and `error=str(exc)`; call
     `sys.exit(1)`.
   - (Note: file-absent and file-empty do NOT raise — they log WARNING
     inside the loader and return normally.)
2. Call `asyncio.run(worker_async_bootstrap())` where the async
   function performs:
   - `await bootstrap_fetcher_configs()`
   - `await engine.dispose()` (closes the parent's pooled connections
     before Celery forks worker children — must happen inside the event
     loop because `AsyncEngine.dispose()` is a coroutine)
   - If raises: log CRITICAL `worker_startup_failed` with
     `stage="fetcher_config_bootstrap"`, `error_type`, and
     `error=str(exc)`; call `sys.exit(1)`.
3. If all steps succeed: log INFO `worker_startup_completed`; return.

**Catch-all**: the entire sequence (steps 1–2) is wrapped in a
`try/except Exception` that catches any exception not already handled
by the step-specific catches above, logs CRITICAL
`worker_startup_failed` with the appropriate `stage` (derived from
which step was executing), `error_type=type(exc).__name__`, and
`error=str(exc)`, then calls `sys.exit(1)`. This ensures that
exceptions not wrapped in `CPEMappingLoadError` by the loader (e.g.,
`PermissionError` from a misconfigured volume mount, or OS-level
failures) still abort the worker instead of being silently swallowed
by Celery's signal dispatcher.

This follows the same pattern as the Beat `beat_init` handler, which
wraps its entire bootstrap + reconciliation sequence in a catch-all
with `sys.exit(1)` on failure.

**Exit mechanism**: the handler calls `sys.exit(1)` on any failure.
This is required because Celery's signal dispatcher catches ordinary
`Exception` from receivers; only `SystemExit` (a `BaseException`)
propagates through it to abort the process.

**Logging rationale**: exception messages are included in the log
(`error=str(exc)`) because startup errors on local files contain only
filesystem paths and validation rule descriptions — no PII or secrets.
This gives operators actionable diagnostic information without
requiring reproduction of the failure.

**Worker-role scope**: runs for both general workers and Git workers.
All workers run from the same image; validating a small static file is
negligible. Beat and the IBS consumer do not emit `celeryd_after_setup`.

**Single-owner rule**: this handler owns the complete Sentinel startup
sequence for Celery workers. There MUST NOT be separate handlers for
CPE and bootstrap, because Celery does not guarantee ordering between
independent receivers of the same signal.

## What Is Removed

- **API lifespan CPE guard**: the existing `resolve_cpe_packages()`
  call in the FastAPI `lifespan` event is removed. The API server does
  not consume the CPE mapping, and validating it there provided no
  guarantee for worker processes (which may run a different image
  version or have a different volume mount). The worker now validates
  its own dependency directly.

## What Does NOT Change

- **Connectivity checks** (IBS consumer, `/ready`): unchanged; no
  shared connectivity primitives are introduced.
- **OP-18 startup ordering invariant**: unchanged; no new sequential
  gate.
- **CPE lazy-load pattern** (`lru_cache`): unchanged; the startup
  guard warms the cache, it does not replace it.
- **Process architecture**: no new process role or container.
- **Beat startup** (`beat_init`): unchanged.

## Action Plan

### Step 1: Update `docs/features/packages/cpe-package-mapping.md`

#### 1a. Replace the "Loading" paragraph and lifespan guard (lines 236-252)

**Remove** the entire block from `**Loading**: the mapping dict is
loaded lazily...` through `...without requiring the data file).`
(includes both the Loading paragraph and the lifespan guard paragraph).

**Insert** in its place:

```markdown
**Loading**: the mapping dict is loaded lazily on first call to
`resolve_cpe_packages()`, cached for subsequent calls via
`functools.lru_cache(maxsize=1)` on the internal loader function.
The file path is `backend/app/data/cpe-package-mapping.json`.

The lazy-init pattern avoids coupling all modules that transitively
import `cpe_mapping` to the existence of the JSON file, improving
test ergonomics (tests that don't exercise CPE resolution can import
the module freely without requiring the data file).

**Runtime validation contract**:

Package resolution is a best-effort mechanism — the platform functions
correctly without a CPE mapping (tickets are created normally, VAs can
add packages manually). This informs the loader's error handling:
absence or emptiness of the file is a degraded-but-operational state,
not a fatal error.

**Exception**: `CPEMappingLoadError` — a `RuntimeError` subclass
defined beside the loader in `backend/app/services/cpe_mapping.py`.
Message format: `CPE mapping load failed at {path}: {reason}`.
`{reason}` identifies the failed rule and, for structural failures,
the offending key or zero-based array index; it never includes file
contents or package values.

**Loader behavior by file state**:

| Condition | Behavior | Rationale |
|-----------|----------|-----------|
| File does not exist | Log WARNING `cpe_mapping_absent`; return empty dict | Best-effort degradation — resolution disabled, platform operational |
| File exists, zero bytes or whitespace-only | Log WARNING `cpe_mapping_absent`; return empty dict | Equivalent to absent — no meaningful content to parse |
| File exists, content is `{}` (empty JSON object) | Log WARNING `cpe_mapping_empty`; return empty dict | Explicit empty mapping — resolution disabled, platform operational |
| File exists, non-empty, structurally valid | Return populated dict | Normal operation |
| File exists, non-empty, structurally invalid | Raise `CPEMappingLoadError` | Corrupted file = deployment bug; partial/wrong mappings are worse than no mappings |

"Non-empty" for the purpose of validation means: the file contains at
least one non-whitespace character AND the content is not the empty
JSON object `{}`. Files that are zero bytes, whitespace-only, or
contain exactly `{}` are treated as graceful-degradation cases (no
validation rules applied, no error raised).

**Validation rules** (applied only when file exists and is non-empty;
checked in order, first failure raises `CPEMappingLoadError`):

1. The file is readable and valid UTF-8.
2. The content is syntactically valid JSON.
3. The root value is an object (not array, string, null, etc.).
4. No duplicate keys exist. The loader MUST use a pair-preserving
   decoder hook because a standard dict silently retains only the last
   duplicate.
5. Every key is lowercase with exactly one literal `:` separating
   non-empty `vendor` and `product` components. Key and both components
   must equal their whitespace-trimmed forms. Components MUST NOT
   contain internal whitespace (only `[a-z0-9._-]` characters are
   permitted) — keys with spaces would never be matched by the
   resolution functions, which produce underscore-separated keys from
   CPE data.
6. Every value is a non-empty array of strings. Every string is
   non-empty after trimming and must equal its trimmed form.

**Not enforced at runtime** (CI-only): alphabetical key ordering.
Unsorted but otherwise valid JSON loads successfully.

**Cache behavior**: the loader uses `@lru_cache(maxsize=1)` and
returns `dict[str, tuple[str, ...]]` (immutable tuples for package
lists). Both successful loads (populated or empty dict) are cached. A
load that raises `CPEMappingLoadError` stores no entry — the next
call retries a full read and revalidation. After one successful load,
subsequent calls return the cached mapping without file I/O. There is
no hot-reload or cache invalidation; a mapping update requires a new
deployment and process restart.

File I/O errors (`PermissionError`, `IsADirectoryError`, and any
other `OSError` subclass raised during file access) are wrapped in
`CPEMappingLoadError` with the underlying OS error as `{reason}`.
These indicate deployment/mount issues that prevent determining file
state.

Non-`OSError` exceptions (`MemoryError`, `KeyboardInterrupt`, etc.)
propagate unchanged — they are not wrapped in `CPEMappingLoadError`.

**Worker startup guard**: Celery workers validate the CPE mapping at
process startup via `check_cpe_mapping()` in the unified
`celeryd_after_setup` handler, before accepting any task. This
ensures a corrupted mapping file is detected at boot time — not hours
later when the first ingestion task runs. See
`docs/features/platform/fetcher-infrastructure.md` (Worker Startup
Handler) for the full handler contract.

The API server, Beat, and IBS consumer do not validate the CPE
mapping — they never call `resolve_cpe_packages()` or
`resolve_vendor_product()`.

**`check_cpe_mapping()`**:

- **Location**: `backend/app/core/startup_checks.py`
- **Signature**: `def check_cpe_mapping() -> None`
- **Behavior**: invokes `resolve_cpe_packages()` with the fixed dummy
  CPE `cpe:2.3:a:test:test:*:*:*:*:*:*:*:*`. The loader handles all
  three cases (valid file, absent/empty file, invalid file) internally.
  On success (including file-absent or file-empty), the `lru_cache` is
  warmed for subsequent use by worker tasks. If loading raises
  `CPEMappingLoadError`, propagates it to the caller.
- **Side effects**: cache warming only. No I/O beyond what the loader
  performs, no audit events.
- **Exceptions**: propagates `CPEMappingLoadError` from the loader.
  Unexpected exceptions from the loader propagate unchanged.
```

#### 1b. Modify "Operational semantics" (lines 254-262)

**Replace** the sentence:

```
After a deployment with an updated mapping, all processes
(API server, Celery workers, Beat scheduler) start fresh with the new
version.
```

**With**:

```
After a deployment with an updated mapping, Celery workers (the only
consumers) start fresh with the new version. The API server, Beat,
and IBS consumer do not load the mapping.
```

#### 1c. Modify "Security" section (line 368-369)

**Replace**:

```
- **No external I/O**: the resolution function reads from an in-memory
  dict loaded at startup. No network calls, no database queries
```

**With**:

```
- **No external I/O**: the resolution function reads from an in-memory
  dict loaded lazily at first use (in worker processes). No network
  calls, no database queries
```

---

### Step 2: Update `docs/features/tickets/cve-service.md`

#### 2a. Replace Phase 2 CPE error handling paragraph (lines 488-497)

**Remove**:

```
A runtime exception from `resolve_cpe_packages()` or
`resolve_vendor_product()` has different semantics. Given that the
CPE-to-package mapping is loaded and validated once at application
startup via the lifespan event (fail-fast guard specified in
`docs/features/packages/cpe-package-mapping.md`), an exception from
either resolution function at task runtime indicates a programming bug,
not a transient infrastructure failure. Such an exception MUST be
propagated (raised, not caught) so Celery marks the task as `FAILED`
and records the full traceback. It MUST NOT be silently swallowed or
treated like a per-package SMELT warning.
```

**Insert**:

```
A runtime exception from `resolve_cpe_packages()` or
`resolve_vendor_product()` has different semantics. Given that the
CPE-to-package mapping is loaded and validated at worker process
startup via the `celeryd_after_setup` handler (fail-fast guard
specified in `docs/features/packages/cpe-package-mapping.md` and
`docs/features/platform/fetcher-infrastructure.md`, Worker Startup
Handler), an exception from either resolution function at task
runtime indicates a programming bug, not a transient infrastructure
failure. Such an exception MUST be propagated (raised, not caught) so
Celery marks the task as `FAILED` and records the full traceback. It
MUST NOT be silently swallowed or treated like a per-package SMELT
warning.
```

---

### Step 3: Update `docs/features/platform/fetcher-infrastructure.md`

#### 3a. Add new subsection `#### Worker Startup Handler`

**Insert** after the last paragraph of `### Startup Validation`
(after line 2427 — the implicit-validation bullet list ending with
"`FETCHER_REGISTRY` population...") and before `## Concurrency
Control` (line 2429):

```markdown
#### Worker Startup Handler

**Location**: `backend/app/core/worker_startup.py`

**Registration**: connected to Celery's `celeryd_after_setup` signal
with a stable `dispatch_uid`. The Celery app module imports the
handler module.

**Why `celeryd_after_setup`**: emitted after worker logging and queue
setup but before the consumer starts accepting tasks. The handler can
log and abort before any task runs. `worker_process_init` is
unsuitable (runs in every pool child, 4-second blocking limit).
`worker_ready` is unsuitable (fires after the consumer starts —
tasks could already be executing).

**Sequence**:

1. Call `check_cpe_mapping()` (synchronous — no event loop needed).
   - If `CPEMappingLoadError` is raised: log CRITICAL
     `worker_startup_failed` with `stage="cpe_mapping"`,
     `error_type=type(exc).__name__`, and `error=str(exc)`; call
     `sys.exit(1)`.
   - (Note: file-absent and file-empty do NOT raise — they log
     WARNING inside the loader and return normally.)
2. Call `asyncio.run(worker_async_bootstrap())` where the async
   function performs:
   - `await bootstrap_fetcher_configs()`
   - `await engine.dispose()` (closes the parent's pooled connections
     before Celery forks worker children — must happen inside the
     event loop because `AsyncEngine.dispose()` is a coroutine)
   - If raises: log CRITICAL `worker_startup_failed` with
     `stage="fetcher_config_bootstrap"`, `error_type`, and
     `error=str(exc)`; call `sys.exit(1)`.
3. If all steps succeed: log INFO `worker_startup_completed`; return.

**Catch-all**: the entire sequence (steps 1–2) is wrapped in a
`try/except Exception` that catches any exception not already handled
by the step-specific catches above, logs CRITICAL
`worker_startup_failed` with the appropriate `stage` (derived from
which step was executing), `error_type=type(exc).__name__`, and
`error=str(exc)`, then calls `sys.exit(1)`. This ensures that
exceptions not wrapped in `CPEMappingLoadError` by the loader (e.g.,
`PermissionError` from a misconfigured volume mount, or OS-level
failures) still abort the worker instead of being silently swallowed
by Celery's signal dispatcher.

This follows the same pattern as the Beat `beat_init` handler, which
wraps its entire bootstrap + reconciliation sequence in a catch-all
with `sys.exit(1)` on failure.

**Exit mechanism**: the handler calls `sys.exit(1)` on any failure.
This is required because Celery's signal dispatcher catches ordinary
`Exception` from receivers; only `SystemExit` (a `BaseException`)
propagates through it to abort the process.

**Single-owner rule**: this handler owns the complete Sentinel startup
sequence for Celery workers. There MUST NOT be separate handlers for
CPE and bootstrap, because Celery does not guarantee ordering between
independent receivers of the same signal.

**Logging rationale**: exception messages are included in the log
(`error=str(exc)`) because startup errors on local files contain only
filesystem paths and validation rule descriptions — no PII or secrets.

**Worker-role scope**: runs for both general workers and Git workers.
All workers run from the same image; validating a small static file is
negligible. Beat and the IBS consumer do not emit
`celeryd_after_setup`.
```

#### 3b. Modify FetcherConfig bootstrap timing parenthetical (lines 2617-2620)

**Replace**:

```
- Runs as the first operation within each process's startup sequence
  (Beat: first inside `beat_init` handler, before reconciliation; API:
  during FastAPI startup event, before serving requests; worker: at
  process init, before consuming tasks)
```

**With**:

```
- Runs as the first operation within each process's startup sequence
  (Beat: first inside `beat_init` handler, before reconciliation; API:
  during FastAPI startup event, before serving requests; worker:
  second step in `celeryd_after_setup` handler, after CPE mapping
  validation — see Worker Startup Handler)
```

#### 3c. Modify "Sync callers" bullet (lines 2610-2614)

**Replace**:

```
- **Sync callers**: worker and Beat startup invoke this function via
  the sync-to-async bridging pattern (`docs/conventions.md`) — a
  single `asyncio.run()` wrapping the extracted async startup
  function. The API server calls it with `await` during the FastAPI
  startup event.
```

**With**:

```
- **Sync callers**: worker and Beat startup invoke this function via
  the sync-to-async bridging pattern (`docs/conventions.md`) — a
  single `asyncio.run()` wrapping the extracted async startup
  function (for workers, this function also contains
  `engine.dispose()` after bootstrap — see Worker Startup Handler).
  The API server calls it with `await` during the FastAPI startup
  event.
```

#### 3d. Modify "Celery workers" bullet list (lines 2298-2301)

**Replace**:

```
**Celery workers** do NOT write to redbeat. They only:

- Run the bootstrap (shared with Beat and API)
- Read `FetcherConfig` during task execution
```

**With**:

```
**Celery workers** do NOT write to redbeat. They only:

- Validate the CPE mapping (`check_cpe_mapping()` — first step)
- Run the bootstrap (shared with Beat and API — second step)
- Read `FetcherConfig` during task execution
```

#### 3e. No change to Beat bootstrap bullet (lines 1706-1708)

The bullet "`bootstrap_fetcher_configs()` runs as the first operation
inside the signal handler..." refers to Beat's `beat_init` handler,
where bootstrap IS the first operation (no CPE validation in Beat).
No modification needed.

#### 3f. Qualify FetcherConfig introductory sentence (line 2599-2601)

**Replace**:

```
routine that runs in every Celery-based process (worker, Beat, API
server) as the first startup operation in each process.
```

**With**:

```
routine that runs in every Celery-based process (worker, Beat, API
server) during startup. In Beat and API it is the first operation; in
workers it is the second step, after CPE mapping validation (see
Worker Startup Handler).
```

**Rationale**: after the worker startup handler is introduced,
bootstrap is no longer the first operation in workers. The replacement
avoids calling it "first" globally (which would contradict Step 3b's
"worker: second step" in the timing bullet below). The idempotency
guarantee (`INSERT ... ON CONFLICT DO NOTHING`) is an intrinsic
property of the routine and does not depend on execution ordering
relative to other startup operations.

---

### Step 4: Update `docs/drafts/open-points.md`

#### 4a. Update summary table (line 19)

**Replace**:

```
| OP-16 | CPE Mapping Fail-Fast Asymmetry | Cross-Process Startup | Open |
```

**With**:

```
| OP-16 | CPE Mapping Fail-Fast Asymmetry | — | Resolved |
```

#### 4b. Remove the `## Open — Cross-Process Startup` section and move OP-16 to Archive

OP-16 is the only item in the `## Open — Cross-Process Startup`
section. After resolution, the entire section is empty and must be
removed — not just the entry.

**Remove** lines 426-474 (from the `## Open — Cross-Process Startup`
heading through the `---` separator after the OP-16 content). This
includes: the section heading (line 426), the blank line (427), the
full OP-16 entry (428-472), the trailing blank line (473), and the
`---` separator (474). The `---` on line 424 and blank line 425 are
retained as the separator before `## Archive — Resolved`.

**Insert** in the `## Archive — Resolved` section (after line 476,
before the first existing archived entry), following the established
format:

```markdown
### OP-16. CPE Mapping Fail-Fast Asymmetry — RESOLVED

**Resolution**: resolved via `celeryd_after_setup` worker handler
that validates the CPE mapping in the worker process before accepting
tasks. API lifespan guard removed (API does not consume the mapping).
Worker-specific startup handler contract documented in
`docs/features/platform/fetcher-infrastructure.md` (Worker Startup
Handler). Runtime validation contract added to
`docs/features/packages/cpe-package-mapping.md`.

---
```

---

### Step 5: Run reviewers

- `@spec-gap-analyzer` on `docs/features/packages/cpe-package-mapping.md`
  (substantive change to Loading section)
- `@spec-coherence-reviewer` on `cpe-package-mapping.md`,
  `cve-service.md`, and `fetcher-infrastructure.md`

### Step 6: Delete this draft

After all spec changes are applied and reviewers pass, delete
`docs/drafts/op16-cpe-worker-validation.md`.

## Cross-References

- `docs/drafts/open-points.md` — OP-16
- `docs/features/packages/cpe-package-mapping.md` — CPE mapping spec
- `docs/features/tickets/cve-service.md` — Phase 2 error handling
- `docs/features/platform/fetcher-infrastructure.md` — worker bootstrap
