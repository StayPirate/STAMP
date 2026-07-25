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

Validate the CPE mapping **in the worker process** via a
`celeryd_after_setup` handler. Retain the API lifespan guard as
defense-in-depth. Extract the validation logic into a shared function
so both call sites use the same check.

This is option (b) from OP-16. A broader startup-validation architecture
(shared connectivity primitives, pre-flight job) was evaluated and
rejected as disproportionate — see
`docs/drafts/startup-validation-architecture-v2.md` for the full
rationale.

## Solution

### Shared CPE validation function

**Location**: `backend/app/core/startup_checks.py`

```python
def check_cpe_mapping() -> None:
    """Validate the CPE mapping by forcing a load through the cached loader.

    Raises CPEMappingLoadError if the file is missing, unreadable, or
    structurally invalid. On success, the lru_cache is warmed for subsequent
    use by worker tasks.
    """
```

The function invokes `resolve_cpe_packages()` with the fixed dummy CPE
`cpe:2.3:a:test:test:*:*:*:*:*:*:*:*`. The loader validates the
complete file before returning. If loading fails, `CPEMappingLoadError`
propagates to the caller.

This is a simple wrapper — it performs no I/O beyond what the loader
already does, creates no audit events, and has no side effects beyond
cache warming. It raises on failure rather than returning a result
object; the caller decides the reaction.

### CPE loader runtime validation contract

The CPE mapping spec (`cpe-package-mapping.md`) currently defines
validation rules only for CI. This decision requires adding a **runtime
validation contract** to that spec — the loader itself must enforce
correctness, independent of whether CI has run. The following rules
apply at load time:

**Exception**: `CPEMappingLoadError` — a `RuntimeError` subclass defined
beside the loader in `backend/app/services/cpe_mapping.py`. Message
format: `CPE mapping load failed at {path}: {reason}`. `{reason}`
identifies the failed rule and, for structural failures, the offending
key or zero-based array index; it never includes file contents or
package values.

**Validation rules** (checked in order, first failure aborts):

1. The file is readable and valid UTF-8.
2. The content is syntactically valid JSON.
3. The root value is an object (not array, string, null, etc.).
4. No duplicate keys exist. The loader MUST use a pair-preserving
   decoder hook because a standard dict silently retains only the last
   duplicate.
5. Every key is lowercase with exactly one literal `:` separating
   non-empty `vendor` and `product` components. Key and both components
   must equal their whitespace-trimmed forms.
6. Every value is a non-empty array of strings. Every string is
   non-empty after trimming and must equal its trimmed form.

**Not enforced at runtime** (CI-only): alphabetical key ordering.
Unsorted but otherwise valid JSON loads successfully.

**Cache behavior**: the loader uses `@lru_cache(maxsize=1)` and returns
`dict[str, tuple[str, ...]]` (immutable tuples for package lists). Only
successful returns are cached. A failed load stores no entry — the next
call retries a full read and revalidation. After one successful load,
subsequent calls return the cached mapping without file I/O. There is no
hot-reload or cache invalidation; a mapping update requires a new
deployment and process restart.

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

1. Call `check_cpe_mapping()`.
2. If `CPEMappingLoadError` (or any other exception) is raised: log
   CRITICAL `worker_startup_failed` with `stage="cpe_mapping"` and
   `error_type=type(exc).__name__` (no raw exception text); call
   `sys.exit(1)`.
3. Run `bootstrap_fetcher_configs()` via one `asyncio.run()` call
   (existing requirement, now explicitly ordered after CPE validation).
4. If bootstrap fails: log CRITICAL `worker_startup_failed` with
   `stage="fetcher_config_bootstrap"` and `error_type`; call
   `sys.exit(1)`.
5. After bootstrap, call `engine.dispose()` to close the parent's
   pooled connections before Celery forks worker children.
6. If both succeed: log INFO `worker_startup_completed`; return.

**Exit mechanism**: the handler catches exceptions and calls
`sys.exit(1)`. This is required because Celery's signal dispatcher
catches ordinary `Exception` from receivers; only `SystemExit`
(a `BaseException`) propagates through it to abort the process.

**Worker-role scope**: runs for both general workers and Git workers.
All workers run from the same image; validating a small static file is
negligible. Beat and the IBS consumer do not emit `celeryd_after_setup`.

**Single-owner rule**: this handler owns the complete Sentinel startup
sequence for Celery workers. There MUST NOT be separate handlers for
CPE and bootstrap, because Celery does not guarantee ordering between
independent receivers of the same signal.

### Retained API lifespan guard

The FastAPI `lifespan` event retains its CPE validation, now calling
`check_cpe_mapping()` from the shared module instead of an inline
dummy-CPE call. If the function raises, lifespan logs CRITICAL and
raises `RuntimeError`; uvicorn aborts startup.

This preserves fail-fast for the API server and for direct-run
deployments (`uvicorn` without orchestration).

## What Does NOT Change

- **Connectivity checks** (IBS consumer, `/ready`): unchanged; no
  shared connectivity primitives are introduced.
- **OP-18 startup ordering invariant**: unchanged; no new sequential
  gate.
- **CPE lazy-load pattern** (`lru_cache`): unchanged; the startup
  guards warm the cache, they do not replace it.
- **Process architecture**: no new process role or container.
- **Beat startup** (`beat_init`): unchanged.

## Action Plan

### Step 1: Update `docs/features/packages/cpe-package-mapping.md`

- **Add runtime validation contract** to the Loading section: define
  `CPEMappingLoadError`, the 6 validation rules (UTF-8, valid JSON,
  object root, no duplicate keys, key format, non-empty string arrays),
  the CI-only exclusion (alphabetical ordering), and the `lru_cache`
  failed-load behavior (not cached, next call retries). These rules
  currently exist only as CI checks — the spec must state that the
  loader enforces them at runtime too.
- Define `check_cpe_mapping()` as a shared function in
  `app/core/startup_checks.py` that forces a load via the dummy CPE.
- Reformulate the lifespan paragraph: the guard now calls
  `check_cpe_mapping()` instead of an inline dummy-CPE call.
- Add the `celeryd_after_setup` worker guard, referencing the handler
  contract above.
- Correct the process list in "Operational semantics": API and Celery
  workers load/validate the mapping at startup; Beat and IBS consumer
  do not.

### Step 2: Update `docs/features/tickets/cve-service.md`

- Phase 2 error handling: update "loaded and validated once at
  application startup via the lifespan event" → validated at startup in
  **both** the API lifespan **and** the worker (`celeryd_after_setup`).
  The semantic argument (runtime exception = programming bug) is
  unchanged and strengthened.

### Step 3: Update `docs/features/platform/fetcher-infrastructure.md`

- In the Worker Startup / FetcherConfig section: state that
  `bootstrap_fetcher_configs()` is preceded by CPE validation in the
  unified `celeryd_after_setup` handler, and that both run inside one
  `asyncio.run()` bridge. Add `engine.dispose()` after bootstrap as a
  pre-fork requirement.
- Correct: bootstrap's DB access is the worker's effective PostgreSQL
  fail-fast (no separate `SELECT 1` needed).

### Step 4: Update `docs/drafts/open-points.md`

- Move OP-16 from Open to Resolved.
- Resolution text: "Resolved via `celeryd_after_setup` worker handler
  that validates the CPE mapping in the worker process before accepting
  tasks. API lifespan guard retained as defense-in-depth. See
  `docs/features/packages/cpe-package-mapping.md`."

### Step 5: Run reviewers

- `@spec-gap-analyzer` on `cpe-package-mapping.md` (substantive change)
- `@spec-coherence-reviewer` on `cpe-package-mapping.md`,
  `cve-service.md`, and `fetcher-infrastructure.md`

### Step 6: Delete this draft and archive v2

After all spec changes are applied and reviewers pass, delete both
`docs/drafts/op16-cpe-worker-validation.md` and
`docs/drafts/startup-validation-architecture-v2.md`.

## Cross-References

- `docs/drafts/open-points.md` — OP-16
- `docs/drafts/startup-validation-architecture-v2.md` — rejected broader
  architecture (preserved for historical rationale)
- `docs/features/packages/cpe-package-mapping.md` — CPE mapping spec
- `docs/features/tickets/cve-service.md` — Phase 2 error handling
- `docs/features/platform/fetcher-infrastructure.md` — worker bootstrap
