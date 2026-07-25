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

**Unexpected exceptions** (`MemoryError`, OS-level failures beyond
`IOError`) propagate unchanged — they are not wrapped in
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

- **Add runtime validation contract** to the Loading section: define
  `CPEMappingLoadError`, the loader behavior table (file absent → WARNING
  + empty dict; file empty → WARNING + empty dict; file invalid →
  raise), the 6 validation rules (UTF-8, valid JSON, object root, no
  duplicate keys, key format with no internal whitespace, non-empty
  string arrays), the CI-only exclusion (alphabetical ordering), and
  the `lru_cache` behavior (empty/populated dicts cached; failures not
  cached, next call retries).
- Define `check_cpe_mapping()` as a shared function in
  `app/core/startup_checks.py` that forces a load via the dummy CPE.
- **Remove the lifespan CPE guard paragraph** — the API server does not
  consume the CPE mapping and validating it on a different process
  provides no guarantee for workers (images/mounts may differ).
- Add the `celeryd_after_setup` worker guard, referencing the handler
  contract above.
- Correct the process list in "Operational semantics": only Celery
  workers load/validate the mapping at startup; API, Beat, and IBS
  consumer do not.

### Step 2: Update `docs/features/tickets/cve-service.md`

- Phase 2 error handling: update "loaded and validated once at
  application startup via the lifespan event" → loaded and validated at
  **worker process startup** (`celeryd_after_setup`). Use per-process
  terminology (avoid ambiguous "application startup"). Remove the
  reference to the lifespan event (the API no longer validates the
  mapping).
- The semantic argument (runtime `CPEMappingLoadError` = programming
  bug) is unchanged and strengthened: the worker process validates the
  mapping before consuming tasks; an exception during task execution is
  therefore unreachable under correct deployment.

### Step 3: Update `docs/features/platform/fetcher-infrastructure.md`

- In the Worker Startup / FetcherConfig section: state that
  `bootstrap_fetcher_configs()` is preceded by CPE validation (a
  synchronous step) in the unified `celeryd_after_setup` handler.
  Bootstrap and `await engine.dispose()` run together inside one
  `asyncio.run()` call; the CPE check runs synchronously before it.
- Update the "first operation" claim: bootstrap is no longer the first
  operation in the worker startup sequence — it is preceded by the
  synchronous CPE mapping validation.
- Correct: bootstrap's DB access is the worker's effective PostgreSQL
  fail-fast (no separate `SELECT 1` needed).

### Step 4: Update `docs/drafts/open-points.md`

- Move OP-16 from Open to Resolved.
- Resolution text: "Resolved via `celeryd_after_setup` worker handler
  that validates the CPE mapping in the worker process before accepting
  tasks. API lifespan guard removed (API does not consume the mapping).
  See `docs/features/packages/cpe-package-mapping.md`."

### Step 5: Run reviewers

- `@spec-gap-analyzer` on `cpe-package-mapping.md` (substantive change)
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
