# CLI Fetcher Run: Lifecycle Delegation to BaseFetcher.run()

## Status

**Draft** — not yet implemented.

## Origin

Review finding FEO-GAP-06 (Low): "CLI fetcher run: unhandled exception
exit code ambiguity — Exit code 2 for unhandled exception, but unclear
if the FetcherRun record is updated to failure before exiting."

Investigation revealed that FEO-GAP-06 is a symptom of a deeper
architectural issue: the CLI `sentinel fetcher run` command reimplements
the `BaseFetcher.run()` lifecycle manually instead of delegating to it.

## Problem Statement

The CLI command `sentinel fetcher run <name>` currently:

1. Creates a `FetcherRun` record manually (step 3)
2. Calls `execute()` directly (step 4)
3. Updates the `FetcherRun` record with final status/metrics (step 5)
4. Registers SIGINT/SIGTERM handlers that update the record on
   interruption

This duplicates responsibilities that `BaseFetcher.run()` already
handles:

| Responsibility | `run()` handles? | CLI reimplements? |
|---|---|---|
| Create `FetcherRun` record | Yes | Yes (duplicated) |
| Call `execute()` | Yes | Yes (duplicated) |
| Catch exceptions → mark `failure` + populate error fields | Yes | **Not specified** (the finding) |
| Finalize record (timestamps, metrics, duration) | Yes | Yes (duplicated) |
| Concurrency check | Yes (task level) | Yes (duplicated) |
| Reset metric counters | Yes | Not mentioned |

Every other mutation CLI command (`manage-user create`, `update`,
`deactivate`, `unlock`) delegates to the service layer via
`asyncio.run()`. The `fetcher run` command is the only CLI command that
reimplements a service-layer lifecycle.

## Root Cause

The spec explains why the CLI does not use Celery ("self-contained —
works even when Celery workers are not running"), but it does **not**
explain why it calls `execute()` directly instead of calling `run()`
in-process. Not using Celery does not imply not using `run()`.

A secondary complication: `BaseFetcher.run()` currently contains an
enabled check (item 4 in its contract) that the CLI explicitly wants to
bypass. This creates the impression that `run()` cannot be called from
the CLI without modification. However, the enabled check serves a
different purpose (race condition handling for the API trigger path) and
can be relocated.

## Proposed Solution

### Principle

`run()` = pure lifecycle management (create record, execute, finalize).
Caller = policy (enabled check, interactive stale resolution, signal
handling, exit codes).

### Change 1: Remove enabled check from `BaseFetcher.run()`

**File**: `docs/features/platform/fetcher-infrastructure.md`

**Current** (lines 57-68):

```markdown
4. **Enabled check**: before executing, `run()` checks `FetcherConfig` for
   the fetcher. If `enabled` is `false`:
   - If a pre-existing `FetcherRun` record was passed via `run_id` (manual
     trigger case), `run()` updates it to `status = failure`,
     `error_message = 'Fetcher disabled between trigger and execution'`,
     `finished_at = now()`, `duration_seconds = 0`, then returns. This
     prevents the record from remaining in `running` status indefinitely
   - Otherwise (scheduled run, no pre-existing record), the run is
     skipped — no `FetcherRun` record is created, the task returns
     immediately
   In both cases, a DEBUG-level log is emitted:
   `logger.debug("Fetcher '%s' is disabled — skipping run", self.name)`
```

**Proposed**: remove entirely. Replace with a brief note:

```markdown
`run()` does not check the `enabled` flag — this is the caller's
responsibility. If `run()` is invoked, it executes unconditionally.
```

**Rationale**: the enabled check inside `run()` was designed to handle
the race condition where an admin triggers a fetcher via API (creating a
`FetcherRun` record) and another admin disables the fetcher before the
Celery worker picks up the task. This scenario requires:

- Two admins operating on the same fetcher
- Within seconds of each other (between API enqueue and worker pickup)
- Without communication

In practice, Sentinel will have 1-2 admins. And even if the race
occurs, stale detection (default timeout 3600s) resolves the orphaned
record automatically. The enabled check inside `run()` is a redundant
defense for a near-impossible scenario that already has automatic
recovery.

### Change 2: Move enabled check to `run_fetcher` Celery task

**File**: `docs/features/platform/fetcher-infrastructure.md`

Add enabled check as the first step in the `run_fetcher` task, before
the concurrency check:

```markdown
1. **Enabled check**: query `FetcherConfig.enabled`. If `false`:
   - If `run_id` was passed (manual trigger via API): update the
     pre-existing `FetcherRun` to `status = failure`,
     `error_message = "Fetcher disabled between trigger and execution"`,
     `finished_at = now()`, `duration_seconds = 0`. Return.
   - Otherwise (scheduled run): skip silently, return immediately.
     Log at DEBUG level.
2. **Concurrency check**: (existing behavior, unchanged)
3. **Invoke run()**: call `fetcher.run(triggered_by=triggered_by,
   triggered_by_user_id=user_id, run_id=run_id)`. The task passes all
   its parameters through to `run()`.
```

This preserves the race condition handling in the exact place where it
matters (the Celery task, which is the only code path where the
trigger-then-disable race can occur) without burdening `run()` with
caller-specific policy.

### Change 3: Simplify `run_id` parameter documentation

**File**: `docs/features/platform/fetcher-infrastructure.md`

The `run_id` parameter remains (needed for API trigger case) but its
description simplifies:

```markdown
When `run_id` is passed, `run()` updates the existing `FetcherRun`
record instead of creating a new one. When `run_id` is `None`, `run()`
creates a new `FetcherRun` record.
```

No more coupling between `run_id` and the enabled check.

### Change 4: Rewrite CLI `sentinel fetcher run` Execution model

**File**: `docs/features/platform/fetcher-operations.md` (lines 927-953)

**Current steps**:

```
1. Validate <name> in FETCHER_REGISTRY
2. Concurrency check (interactive stale resolution)
3. Create FetcherRun record with status=running, triggered_by=manual
4. Call execute()
5. Update FetcherRun with final status, metrics, timestamps
6. Print summary
```

**Proposed steps**:

```
1. Validate <name> in FETCHER_REGISTRY (unchanged)
2. Concurrency check with interactive stale resolution (unchanged)
3. Check enabled flag; if disabled:
   - If stdin is a TTY: prompt the operator on stderr:
     "Warning: fetcher '<name>' is currently disabled.
     Proceed anyway? [y/N]: "
     If the operator answers 'y' or 'Y', proceed. Otherwise, print
     "Aborted." to stderr and exit with code 1.
   - If stdin is not a TTY (non-interactive/scripted): print error to
     stderr: "Error: fetcher '<name>' is disabled. Enable it before
     running in non-interactive mode." and exit with code 1.
4. result = asyncio.run(fetcher.run(triggered_by="manual"))
5. Map result.status to exit code and print summary to stdout
   (using result.duration_seconds, result.items_created, etc.)
```

Key differences:

- Steps 3-5 (old) collapse into a single `run()` call (step 4 new)
- The CLI no longer manages the FetcherRun record lifecycle
- `run()` returns a `FetcherRunResult` dataclass — the CLI uses it
  directly for the summary output without querying the database
- `triggered_by_user_id` is `None` (no user context in CLI) — omitted
  from the call (default)
- `run_id` is `None` — omitted from the call (default); `run()` creates
  a new `FetcherRun` record internally
- The enabled check follows the same interactive/non-interactive pattern
  as the stale run resolution (step 2): TTY → prompt, non-TTY → error
  exit
- Follows the same `asyncio.run(service_method())` pattern as all
  `manage-user` commands

Add notes:

```markdown
This follows the same delegation pattern as `manage-user` commands,
which call service-layer functions via `asyncio.run()`. The CLI handles
pre-conditions (validation, concurrency, enabled warning) and
post-conditions (exit code, output formatting); the service layer
handles execution lifecycle.

**Logging behavior**: when `run()` executes in-process via the CLI,
application-level log messages (from `run()` and `execute()`) are
emitted to the default Python logging destination. Log configuration
for the CLI context is outside the scope of this change.
```

### Change 5: Simplify signal handling

**File**: `docs/features/platform/fetcher-operations.md` (lines
1005-1024)

**Current**: the CLI registers SIGINT/SIGTERM handlers that directly
update the FetcherRun record (set status=failure, error_message,
finished_at, duration_seconds).

**Proposed**: the CLI registers signal handlers, but `run()` handles
the FetcherRun cleanup through its generic exception handling:

```markdown
#### Signal handling

The CLI process MUST register handlers for `SIGINT` (Ctrl+C) and
`SIGTERM`:

1. On signal received: raise `KeyboardInterrupt` (SIGINT) or
   `SystemExit` (SIGTERM) to interrupt the running `asyncio.run()` call
2. `BaseFetcher.run()` catches the resulting exception through its
   standard exception handling and marks the FetcherRun as `failure`
   with `error_message = "Interrupted by operator (SIGINT)"` (or
   SIGTERM)
3. Control returns to the CLI, which maps the signal to exit code
   (130 for SIGINT, 143 for SIGTERM) and prints:
   `"\nInterrupted. Run marked as failed."` to stderr
```

This eliminates the duplicated FetcherRun cleanup logic. The signal
handler's only job is to interrupt execution and set the exit code —
`run()` handles the record.

**SIGKILL** section remains unchanged — stale detection is the recovery.

### Change 6: Update race condition note in API trigger section

**File**: `docs/features/platform/fetcher-operations.md` (lines
471-478)

**Current**:

```markdown
**Note on trigger-then-disable race condition**: if an admin triggers a
fetcher (passing the enabled check in this endpoint) and another admin
disables the fetcher before the Celery worker picks up the task,
`BaseFetcher.run()` detects the pre-existing `FetcherRun` record and
updates it to `status = failure` with
`error_message = 'Fetcher disabled between trigger and execution'`
instead of exiting silently. See `fetcher-infrastructure.md`, "Enabled
check" for the full contract.
```

**Proposed**:

```markdown
**Note on trigger-then-disable race condition**: if an admin triggers a
fetcher (passing the enabled check in this endpoint) and another admin
disables the fetcher before the Celery worker picks up the task, the
`run_fetcher` Celery task checks the enabled flag before calling
`run()`. If disabled and a `run_id` was passed, it updates the
pre-existing `FetcherRun` record to `status = failure` with
`error_message = 'Fetcher disabled between trigger and execution'`.
See `fetcher-infrastructure.md`, Celery task section.
```

### Change 7: Update exit codes table

**File**: `docs/features/platform/fetcher-operations.md` (lines
1026-1040)

Exit code 2 description changes from:

```
| 2 | System error: database unreachable, unhandled exception in execute() |
```

To:

```
| 2 | System error: database unreachable, run() raised an unrecoverable error |
```

Since `run()` now handles all exceptions from `execute()`, the only
scenario where the CLI itself encounters an unrecoverable error is if
`run()` fails at a higher level (e.g., cannot create the FetcherRun
record because the database is unreachable).

### Change 8: Update `BaseFetcher.run()` exception handling for signals

**File**: `docs/features/platform/fetcher-infrastructure.md`

Add `KeyboardInterrupt` and `SystemExit` to the exception types that
`run()` catches. These represent operator interruption (from CLI signal
handlers) and should result in `status = failure` with descriptive
error messages:

```markdown
- `KeyboardInterrupt` → `error_message = "Interrupted by operator
  (SIGINT)"`, status `failure`
- `SystemExit` → `error_message = "Interrupted by operator (SIGTERM)"`,
  status `failure`
```

These are added to the exception handling section alongside
`FetcherError` (Tier 1) and generic exceptions (Tier 2), as a distinct
tier for controlled interruptions.

### Change 9: Resolve FEO-GAP-06 and update tracking

**File**: `docs/reviews/fetcher-operations.md`

Mark FEO-GAP-06 as RESOLVED:

```markdown
### FEO-GAP-06 — CLI fetcher run: unhandled exception exit code ambiguity (Low)

**Status**: RESOLVED — Fixed: CLI now delegates to BaseFetcher.run() which handles all exception/cleanup scenarios (2026-05-29)
```

**File**: `docs/reviews/.tracking.json`

Update cache: GAP open L: 7→6, resolved: 13→14.

**File**: `docs/reviews/README.md`

Update fetcher-operations row counts.

### Change 10: Formalize `run()` and `run_fetcher` signatures, fix `run_id` transport gap

**Origin**: the specs describe `BaseFetcher.run()` receiving a `run_id` to
update an existing `FetcherRun` record (API trigger flow), but:

1. `run()` has no documented signature — its parameters are only implied
2. The `run_fetcher` Celery task signature omits `run_id` entirely
3. The conditional lifecycle behavior (create new record vs. update
   existing) is implicit in the enabled check section but never declared
   in the lifecycle description
4. The API trigger section says `run()` "detects" the existing record
   "matched by `run_id`" without explaining how `run_id` is transported
   from the API handler through the Celery task to `run()`

This change formalizes all of the above.

#### 10a — Define `FetcherRunResult` dataclass

**File**: `docs/features/platform/fetcher-infrastructure.md`

Add after the BaseFetcher Base Class section (before Abstract Interface):

```markdown
### Return type

`run()` returns a `FetcherRunResult` dataclass containing the final
state of the execution. This decouples callers from the ORM model and
avoids returning a database-session-bound object:

```python
@dataclass(frozen=True)
class FetcherRunResult:
    run_id: UUID
    status: str              # "success" | "partial" | "failure"
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    items_created: int
    items_updated: int
    items_failed: int
    error_message: str | None
```

The CLI uses this to print the run summary. The Celery task ignores the
return value. The `error_message` field contains the sanitized public
message (see "Error Message Sanitization"); `error_detail` and
`error_traceback` are not included because they are already emitted to
application logs during execution.
```

#### 10b — Define formal `run()` signature

**File**: `docs/features/platform/fetcher-infrastructure.md`

Add after "a `run()` method (not meant to be overridden) that wraps the
fetcher's `execute()` method with:" (line 33):

```markdown
Signature:

```python
async def run(
    self,
    *,
    triggered_by: str = "schedule",
    triggered_by_user_id: UUID | None = None,
    run_id: UUID | None = None,
) -> FetcherRunResult:
```

`run()` manages its own database sessions internally — callers do not
pass a session. Each database operation (record creation, finalization)
uses a short-lived session. The connection is not held open during
`execute()`.
```

#### 10c — Document conditional lifecycle behavior

**File**: `docs/features/platform/fetcher-infrastructure.md`

Replace the current line 34:

```
- Creation of a `FetcherRun` record with status `running`
```

With:

```markdown
- **FetcherRun record acquisition**:
  - When `run_id` is `None` (scheduled runs, CLI): creates a new
    `FetcherRun` record with `status = running`, `triggered_by` and
    `triggered_by_user_id` set from the corresponding parameters
  - When `run_id` is provided (API trigger): retrieves the existing
    `FetcherRun` record (created synchronously by the API trigger
    endpoint). The record already has `status = running` and its
    `triggered_by`/`triggered_by_user_id` fields already set. `run()`
    continues its lifecycle without creating a new record
```

#### 10d — Add `run_id` to `run_fetcher` Celery task signature

**File**: `docs/features/platform/fetcher-infrastructure.md` (lines
654-656)

Replace:

```python
@celery_app.task(bind=True)
def run_fetcher(self, fetcher_name: str, triggered_by: str = "schedule",
                user_id: str | None = None) -> None:
    """Run a fetcher by name."""
    ...
```

With:

```python
@celery_app.task(bind=True)
def run_fetcher(self, fetcher_name: str, triggered_by: str = "schedule",
                user_id: str | None = None,
                run_id: str | None = None) -> None:
    """Run a fetcher by name.

    Args:
        fetcher_name: registry key identifying the fetcher
        triggered_by: "schedule" (Beat) or "manual" (API/CLI)
        user_id: UUID of the user who triggered (None for scheduled runs)
        run_id: UUID of a pre-created FetcherRun record (API trigger
                flow). When provided, run() updates this record instead
                of creating a new one. When None, run() creates a new
                record.
    """
    ...
```

**File**: `docs/features/platform/fetcher-operations.md` (line 762)

Replace:

```
| Parameters | `fetcher_name` (str), `triggered_by` (str), `user_id` (str, optional) |
```

With:

```
| Parameters | `fetcher_name` (str), `triggered_by` (str), `user_id` (str, optional), `run_id` (str, optional) |
```

#### 10e — Explicit `run_id` transport in API trigger section

**File**: `docs/features/platform/fetcher-operations.md` (lines 453-457)

Replace:

```markdown
- Creates a `FetcherRun` record **synchronously** (before enqueuing the
  Celery task) with `status = running` and `triggered_by = manual`. This
  ensures the `run_id` is available in the API response. The
  `BaseFetcher.run()` method detects the existing `FetcherRun` record
  (matched by `run_id`) and updates it rather than creating a new one
```

With:

```markdown
- Creates a `FetcherRun` record **synchronously** (before enqueuing the
  Celery task) with `status = running` and `triggered_by = manual`. This
  ensures the `run_id` is available in the API response
- Passes `run_id` to the Celery task via `run_fetcher.apply_async(kwargs=
  {"fetcher_name": name, "triggered_by": "manual", "user_id": str(user.id),
  "run_id": str(run.id)})`. The task forwards it to
  `fetcher.run(run_id=run_id, ...)`, which updates the existing record
  instead of creating a new one
```

### Change 11: CLI creates `FetcherAuditEvent` and relax `user_id` validation

**Origin**: the API trigger endpoint creates a `FetcherAuditEvent` with
`event_type = triggered` (fetcher-operations.md:452), but the CLI does
not. This is an audit trail gap — a manual execution via CLI leaves
only a `FetcherRun` record, with no entry in the audit log.

Other audit trails (`TicketAuditEvent`, `IdentityAuditEvent`) already
use `user_id = NULL` for system-initiated actions, rendered as
`"system"` in the UI. The fetcher audit trail should follow the same
pattern.

#### 11a — Remove `user_id` presence validation from `FetcherAuditLog`

**File**: `docs/features/platform/fetcher-infrastructure.md` (line 866)

Replace:

```
| user_id | UUID | FK(user.id), nullable | Inherited from AuditEventMixin. Admin who performed the action. Nullable at DB level; `FetcherAuditLog.log_event()` validates presence (all fetcher admin actions are human-initiated) |
```

With:

```
| user_id | UUID | FK(user.id), nullable | Inherited from AuditEventMixin. Actor who performed the action. NULL for CLI-initiated actions (rendered as "system" in the UI) |
```

#### 11b — Add `FetcherAuditEvent` creation to CLI execution model

**File**: `docs/features/platform/fetcher-operations.md` (CLI section)

In the proposed execution model (Change 4, step 4), add audit event
creation as part of the execution flow. Insert between step 3 (enabled
check) and step 4 (run()):

```markdown
3b. Create a `FetcherAuditEvent` with:
    - `event_type = triggered`
    - `user_id = NULL`
    - `detail = {"source": "cli"}`
    - `old_value = NULL`, `new_value = NULL`
```

This aligns with the API trigger path (which also creates a
`FetcherAuditEvent` before invoking the run) and ensures every manual
execution — regardless of entry point — is recorded in the audit log.

#### 11c — Update Event Field Values table

**File**: `docs/features/platform/fetcher-infrastructure.md` (line 891)

Replace:

```
| `triggered` | `null` | `null` | `null` |
```

With:

```
| `triggered` | `null` | `null` | `null` (API trigger) or `{"source": "cli"}` (CLI trigger) |
```

---

## Verification Checklist

After implementation, verify:

- [ ] `BaseFetcher.run()` contract in `fetcher-infrastructure.md` has no
  enabled check
- [ ] `BaseFetcher.run()` has a formal signature with `triggered_by`,
  `triggered_by_user_id`, and `run_id` parameters (all keyword-only)
- [ ] `BaseFetcher.run()` returns `FetcherRunResult` (dataclass defined
  in the spec)
- [ ] `run()` lifecycle documents the conditional behavior: `run_id`
  provided → update existing record; `run_id` absent → create new record
- [ ] `run()` manages its own database sessions (no `db` parameter)
- [ ] `run_fetcher` Celery task in `fetcher-infrastructure.md` has the
  enabled check with race condition handling
- [ ] `run_fetcher` Celery task signature includes `run_id` parameter
  (both `fetcher-infrastructure.md` and `fetcher-operations.md`)
- [ ] `run_fetcher` task passes all parameters through to `run()`
- [ ] CLI `sentinel fetcher run` in `fetcher-operations.md` calls
  `asyncio.run(fetcher.run(triggered_by="manual"))` instead of
  `execute()` directly
- [ ] CLI uses the returned `FetcherRunResult` for the summary output
- [ ] CLI signal handling delegates cleanup to `run()` instead of
  updating FetcherRun directly
- [ ] The race condition note in the API trigger section references the
  Celery task, not `run()`
- [ ] API trigger section explicitly documents `run_id` transport
  (handler → apply_async → task → run())
- [ ] `run()` exception handling covers `KeyboardInterrupt` and
  `SystemExit` for signal propagation
- [ ] Exit code 2 description updated
- [ ] `FetcherAuditLog.log_event()` no longer validates `user_id`
  presence (nullable without application-level constraint)
- [ ] CLI creates a `FetcherAuditEvent` with `event_type = triggered`,
  `user_id = NULL`, `detail = {"source": "cli"}`
- [ ] Event Field Values table shows `detail` differentiation for
  `triggered` (null for API, `{"source": "cli"}` for CLI)
- [ ] FEO-GAP-06 marked as RESOLVED
- [ ] No other findings are invalidated by this change (verify
  FEO-SEC-04 still applies — CLI enabled bypass remains intentional)

## Impact on Other Findings

- **FEO-SEC-04** (CLI bypasses enabled check without audit event):
  partially resolved — the enabled bypass now requires explicit operator
  confirmation (interactive prompt), and a `FetcherAuditEvent` with
  `event_type = triggered` is created (with `user_id = NULL`,
  `detail = {"source": "cli"}`). The bypass is still intentional but is
  now audited. In non-interactive mode, disabled fetchers cannot be
  executed at all (error exit). Whether this fully resolves FEO-SEC-04
  depends on whether the finding's concern was the lack of audit event
  (now fixed) or the bypass itself (still present by design).
- **FEO-GAP-06**: resolved (this change).
- All other findings: not impacted.

## Open Points

Findings from pre-application review (2026-05-29, 5 reviewers: design,
coherence ×2, gap analysis ×2). These must be resolved before applying
the changes in this draft.

### High Severity

**H1 — `KeyboardInterrupt` propagation through `asyncio.run()` is
unreliable**

Source: Design reviewer (W1), Gap-Infra (Gap 4, Gap 9)

The draft assumes: signal handler raises `KeyboardInterrupt` → `run()`
catches it → marks record as failure → returns `FetcherRunResult`. In
practice, `asyncio.run()` intercepts `KeyboardInterrupt` internally,
cancels running tasks, then re-raises — `run()`'s exception handler
may never execute. The `FetcherRun` record could remain in `running`
status.

Suggested resolution: two-layer defense — `run()` is the primary
handler, CLI has a `finally` block that checks whether the `FetcherRun`
is still `running` and marks it as `failure` if `run()` didn't get to.
Alternatively, use `loop.add_signal_handler()` inside `run()` to set a
cancellation flag.

---

**H2 — `run()` return vs re-raise ambiguity**

Source: Design reviewer (R2), Gap-Infra (Gap 11), Gap-Ops (Gap 1, Gap 9)

The draft never explicitly states whether `run()`:
- (a) catches exceptions from `execute()`, marks failure, and returns a
  `FetcherRunResult` with `status="failure"`, or
- (b) catches exceptions, marks failure, and re-raises

This determines the entire CLI error handling architecture:
- If (a): CLI maps `result.status` to exit code, but cannot distinguish
  signal interruptions from normal failures (both have `status=failure`)
- If (b): CLI catches the re-raised exception for exit code mapping,
  but `FetcherRunResult` is unavailable on failure

The same ambiguity applies to signal exceptions: if `run()` swallows
`KeyboardInterrupt` and returns normally, exit code 130 is lost.

---

**H3 — Missing `FetcherConfig` record during bootstrap**

Source: Gap-Ops (Gap 2)

The CLI is designed for bootstrap scenarios where Celery workers haven't
started. But `FetcherConfig` is auto-created on worker startup
(`fetcher-infrastructure.md` line 822), and `FetcherRun.fetcher_name`
has a FK to `FetcherConfig.fetcher_name`. Calling `run()` without an
existing `FetcherConfig` row causes a FK constraint violation — exactly
in the scenario the CLI command targets.

Resolution options:
- `run()` auto-creates `FetcherConfig` if missing (INSERT ON CONFLICT)
- CLI creates it before calling `run()`
- Document that Celery workers must have started at least once

---

**H4 — Contradiction with `audit-trail-infrastructure.md`**

Source: Coherence-Infra (§2.5, §5.1), Coherence-Ops (§2a)

Change 11a removes `user_id` presence validation from
`FetcherAuditLog`, but `audit-trail-infrastructure.md` (lines 253-256)
explicitly lists `FetcherAuditLog` as a subclass that MUST validate
`user_id` presence. Direct contradiction between two specs.

Additionally, `data-model.md` (line 1414) says "service validates
presence" for `FetcherAuditEvent.user_id`.

Resolution: add a Change 12 to update `audit-trail-infrastructure.md`
(remove `FetcherAuditLog` from the validation-required list) and
update `data-model.md` accordingly.

---

**H5 — `run_id` pointing to non-existent or already-finalized record**

Source: Gap-Infra (Gap 2)

When `run_id` is provided but the record has been finalized by stale
detection (or doesn't exist due to data corruption), `run()` behavior
is unspecified. Could overwrite stale detection results or raise an
unhandled exception.

Resolution: specify that `run()` validates the record state — if
`status != running`, it raises `FetcherError` (or returns failure
immediately). If the record doesn't exist, it raises.

---

**H6 — Exception-to-exit-code mapping for non-signal exceptions**

Source: Gap-Ops (Gap 1)

If the DB is unreachable during `FetcherRun` creation, `run()`
re-raises (per "FetcherRun creation failure" section, lines 70-82).
The CLI receives a raw exception, not a `FetcherRunResult`. The draft
doesn't specify the CLI's catch structure for this case. This
reintroduces the same ambiguity as FEO-GAP-06.

Resolution: specify the CLI's try/except structure:
```
try:
    result = asyncio.run(fetcher.run(...))
except (KeyboardInterrupt, SystemExit) as e:
    → exit 130/143
except Exception:
    → print error to stderr, exit 2
```

### Medium Severity

**M1 — "FetcherRun creation failure" section uses Celery-specific
language**

Source: Coherence-Infra (§2.2)

Lines 70-82 say "the task MUST" and reference "Celery result backend"
— incorrect for the CLI path after Change 10b. Should be rewritten in
caller-agnostic terms.

---

**M2 — Concurrency Control section says "before invoking `execute()`"**

Source: Coherence-Infra (§3.1)

Line 678 references `execute()` directly. After the draft, the task
invokes `run()` (which internally invokes `execute()`). Should say
"before invoking `run()`".

---

**M3 — `triggered_by_user` API response field**

Source: Coherence-Ops (§2b)

`fetcher-operations.md` lines 284-286 state `triggered_by_user` is
populated "when `triggered_by` is `manual`". False for CLI-triggered
runs where `triggered_by_user_id = NULL`. Should note the exception.

---

**M4 — Audit log `actor` filter missing `"system"` value**

Source: Coherence-Ops (§3a)

The `GET .../audit-log` endpoint's `actor` filter parameter doesn't
document the `"system"` special value for NULL-user events. Other
audit trail endpoints do.

---

**M5 — `FetcherConfigError` raise vs return ambiguity**

Source: Coherence-Infra (§5.3), Gap-Infra (Gap 3)

Lines 454-458 say `run()` "raises a `FetcherConfigError`" on custom
settings validation failure. This contradicts the `FetcherRunResult`
return contract. Unclear whether `run()` raises or returns on config
validation failure.

---

**M6 — `data-model.md` `user_id` description**

Source: Coherence-Infra (§5.2)

Line 1414 says "service validates presence" — contradicts Change 11a.
Must be updated alongside.

---

**M7 — Session management for CLI pre-`run()` steps**

Source: Gap-Ops (Gap 6)

Steps 2-3b (concurrency check, enabled check, audit event) need DB
access. The draft doesn't specify whether these use synchronous
sessions (current CLI pattern) or are wrapped in a separate
`asyncio.run()`.

---

**M8 — Signal outside `asyncio.run()` window**

Source: Gap-Ops (Gap 4)

If a signal arrives before `asyncio.run()` starts (during prompts) or
after it returns (during summary printing), exit code 130/143 may not
be produced — Python's default `KeyboardInterrupt` handling exits
with code 1.

Resolution: wrap the entire command in
`try/except KeyboardInterrupt: sys.exit(130)`.

---

**M9 — `run_id` type mismatch (str in task, UUID in `run()`)**

Source: Coherence-Infra (§4.1), Gap-Infra (Gap 1)

The Celery task receives `run_id: str` (JSON serialization), `run()`
expects `UUID`. The conversion responsibility is not documented.

---

**M10 — Enabled-check-before-concurrency ordering**

Source: Coherence-Infra (§5.4)

Change 2 places the enabled check before the concurrency check in the
task. A disabled fetcher with a stale run won't have the stale run
resolved until re-enabled. This is a behavioral change — should be
documented as intentional or reordered.

---

**M11 — "Enabled check bypass" subsection survives alongside new
step 3**

Source: Coherence-Ops (§3c)

`fetcher-operations.md` lines 992-1003 describe unconditional bypass.
Change 4 introduces interactive prompt/non-TTY error (step 3). The
draft doesn't explicitly state that lines 992-1003 are replaced.

---

**M12 — Caller responsibility for single-instance invariant**

Source: Gap-Ops (Gap 7)

After the draft, `run()` has neither enabled check nor concurrency
check. Any future caller that calls `run()` directly without its own
concurrency check would violate the single-instance invariant. This
should be documented as a caller responsibility.

### Low Severity

**L1 — `FetcherRunResult.status` is `str`, DB uses ENUM**

Source: Gap-Infra (Gap 6)

The exclusion of `running` from valid result statuses is implicit (only
in a comment). Should be explicit.

---

**L2 — `FetcherAuditEventType.triggered` says "by an admin"**

Source: Coherence-Ops (§2c)

Incompatible with CLI events where `user_id = NULL`. Should say
"manually triggered (via API or CLI)".

---

**L3 — Audit log response example missing NULL actor case**

Source: Coherence-Ops (§3b)

Other audit trails show `"actor": null` examples. The fetcher audit log
has no equivalent.

---

**L4 — `SystemExit` as SIGTERM proxy is fragile**

Source: Design reviewer (W3)

`SystemExit` is not exclusively a signal — `sys.exit()`, failed
imports, etc. also raise it. Catching `SystemExit` and labeling it
"SIGTERM" could misattribute. Alternative: signal handler sets a flag
+ raises `KeyboardInterrupt` for both signals; `run()` checks the flag
for the error message.

---

**L5 — `FetcherRunResult.started_at` semantics for `run_id` path**

Source: Gap-Infra (Gap 5)

When `run_id` is provided, the record's `started_at` was set by the
API handler (includes queue wait time). The result's `duration_seconds`
would include Celery queue delay, which may be misleading.

---

**L6 — No `--force` flag for non-interactive disabled fetcher
execution**

Source: Design reviewer (R3)

The draft's Change 4 rejects disabled fetchers in non-interactive mode
(exit 1). This is a breaking change for automation scripts that
currently (per spec) bypass the enabled check. A `--force` flag would
preserve the opt-in behavior for scripts.

---

## Disposition

This draft is **superseded** by
`docs/drafts/remove-cli-fetcher-run.md`, which proposes removing
`sentinel fetcher run` entirely (eliminating all findings above) and
migrates the independently useful changes (Changes 10b-10e →
signature formalization and `run_id` transport fix) to the new draft.

Changes 7-10 of the new draft correspond to Changes 10b-10e here.

## Cross-references

- `docs/features/platform/fetcher-infrastructure.md` — BaseFetcher
  contract, run_fetcher task
- `docs/features/platform/fetcher-operations.md` — CLI section, API
  trigger section
- `docs/conventions.md` — CLI conventions (service delegation pattern)
- `docs/cli-reference.md` — command inventory
- `docs/drafts/remove-cli-fetcher-run.md` — successor draft

## Post-Application Steps

After all changes have been applied to the specification files:

1. **Run reviewers** to verify the changes are consistent and complete:
   - `@spec-coherence-reviewer` on `fetcher-infrastructure.md` — verify
     no contradictions introduced between the updated `run()` contract,
     Celery task, and the rest of the spec
   - `@spec-coherence-reviewer` on `fetcher-operations.md` — verify the
     CLI section, API trigger section, and background tasks section are
     mutually consistent after the changes
   - `@spec-gap-analyzer` on `fetcher-infrastructure.md` — verify no
     new gaps were introduced by the signature formalization and
     lifecycle changes
   - `@spec-gap-analyzer` on `fetcher-operations.md` — verify the
     updated CLI execution model has no unspecified error paths or
     boundary conditions
   - `@docs-reviewer` — verify documentation completeness across
     the modified specs
2. **Delete this draft file**
   (`docs/drafts/cli-fetcher-run-lifecycle-delegation.md`) once all
   changes are applied and reviewers confirm no issues
