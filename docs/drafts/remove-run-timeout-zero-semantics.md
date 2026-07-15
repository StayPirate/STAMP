# Draft: Remove `run_timeout=0` Semantics

## Status

**Draft** — pending review before application to specifications.

## Summary

Remove the special sentinel value `run_timeout=0` (which disables all Celery
time limits and stale run detection for a fetcher) and replace it with a
strict positive-integer range enforced at the API level: 60–604800.

604800 seconds = 7 days (one week). This provides ample headroom for
exceptionally long-running fetchers (multi-day backfill, massive delta
recovery) while unconditionally preserving the safety mechanisms that depend
on a positive `run_timeout`.

## Motivation

### 1. Zero real consumers

No existing fetcher specification requires or recommends `run_timeout=0`.
All fetcher specs use positive values (300–3600s). The only case that
approached needing it (OSV with estimated 4.2h first-run, finding
`CSOV-DES-01`) was resolved by redesigning the fetcher algorithm — not by
disabling the timeout.

The documented pattern for long-running fetchers is to **increase
`run_timeout` to a high positive value** temporarily, then reset it
(`git-fetcher-infrastructure.md:355`, `cve-sync-kernel.md:344`).

### 2. Irrecoverable blocked state

When `run_timeout=0` and a fetcher process gets stuck (infinite loop,
deadlock, hung network socket beyond HTTP timeout):

- The hard time limit never fires → process runs forever.
- Stale detection is disabled → the `FetcherRun` record stays in `running`
  status permanently.
- The concurrency guard (`SELECT ... FOR UPDATE` on active runs) blocks ALL
  future executions of that fetcher indefinitely (schedule-triggered runs
  are silently discarded; manual triggers return 409).
- The spec promises "manual resolution via the CLI"
  (`fetcher-operations.md:621-622, 947`), but **no CLI command exists** to
  resolve a stuck run — the `sentinel fetcher` group is entirely read-only
  (`list`, `config`).

Recovery requires either:
- Direct database manipulation (`UPDATE fetcher_run SET status = 'failure'
  WHERE ...`) — an unsafe operation outside the application's control plane.
- Changing `run_timeout` back to `>0` and waiting for the *next* trigger —
  but the zombie process is still alive (time limits apply at dispatch, not
  retroactively), creating a **single-instance invariant violation** when
  the new run starts alongside the old process.

### 3. Unconditional invariant is simpler and safer

The entire fetcher infrastructure is designed around the guarantee that:
- Every run has a bounded maximum duration (hard limit).
- Every orphaned record is eventually detected and cleaned up (stale
  detection).
- At most one instance of a fetcher runs at any time (single-instance
  invariant).

Making these guarantees **unconditional** (no special-case branch) reduces
implementation complexity (~17 conditional branches across 4 documents),
testing surface, and cognitive load for operators.

### 4. High ceiling achieves the same goal safely

Any scenario where an operator would have reached for `run_timeout=0` (very
long backfill, multi-day delta processing) is equally served by setting
`run_timeout=604800` (7 days). The difference: with a positive value, if
the process truly gets stuck, it will eventually be killed (after up to 7
days) and the stale detection will clean up (after 7 days + 60s). With `0`,
it never recovers without manual DB surgery.

### 5. YAGNI — optimal timing

The project is in specification phase with no implemented code or database.
Removing the feature now costs only spec edits. Removing it later would
require a migration, code changes, and backward-compatibility handling.

## Constraint Definition

**Before**: `run_timeout INTEGER NOT NULL DEFAULT 3600` with informal
validation "must be a non-negative integer" and sentinel `0` = disable.

**After**: `run_timeout INTEGER NOT NULL DEFAULT 3600` with API-level
validation "must be an integer between 60 and 604800".

- **Minimum (60s)**: prevents nonsensically short timeouts where the grace
  window (5% = 3s) would be too tight for clean finalization. Also ensures
  the stale threshold (`run_timeout + 60` = 120s minimum) is meaningful.
- **Maximum (604800s = 7 days)**: provides extreme headroom for rare
  multi-day operations while preventing operator accidents (e.g., typo
  `36000000` that would effectively disable stale detection for over a year).
- **Default (3600s)**: unchanged — suitable for most fetchers.

**Enforcement strategy**: the valid range is enforced exclusively at the
application level (Pydantic schema validation on the PATCH endpoint). No
database-level CHECK constraint is used. This is consistent with the
project's validation convention ("Validate at the schema level, not in
endpoints or services" — `conventions.md:304`) and with how all other
numeric columns in the data model are handled.

If an invalid value is written directly to the database (bypassing the API),
the unconditional formula application provides a safe failure mode:
- `run_timeout = 0` → `soft_time_limit = max(1, floor(0)) = 1` → task
  dies after 1 second with a clear error message ("timed out after 0s")
- `run_timeout < 0` → same behavior (formula produces 1)
- `run_timeout` very large → task has a long timeout but stale detection
  still functions (threshold = value + 60s)

In all cases the failure is immediate, loud, and diagnosable — never a
silent irrecoverable state.

### Removal of existing `request_delay` CHECK

As part of this change, the existing `CHECK (>= 0 AND <= 300)` on
`request_delay` (same table) is also removed. It was an inconsistency —
no other numeric column in the data model uses a range CHECK. The
valid range continues to be enforced by the PATCH endpoint's Pydantic
validation. If bypassed, the failure mode is safe:
- Negative value → `asyncio.sleep()` raises `ValueError` immediately
- Very large value → task sleeps excessively, hits soft time limit, fails

## Impact Analysis

### Documents requiring changes

| Document | Sections affected | Nature of change |
|----------|-------------------|------------------|
| `docs/data-model.md` | FetcherConfig table (line ~1428-1429), ER diagram (line ~322) | Update `run_timeout` description (remove "0 disables..." text), remove CHECK on `request_delay` |
| `docs/features/platform/fetcher-infrastructure.md` | Options field table (~1567-1572), Redbeat propagation (~1589-1594, ~1772, ~1793), Concurrency table (~2223), Stale Run Detection (~2249-2262), FetcherConfig column (~2393-2394), SoftTimeLimitExceeded message (~748) | Remove all `if run_timeout = 0` branches, simplify to unconditional behavior, remove CHECK on `request_delay` |
| `docs/features/platform/fetcher-operations.md` | Trigger endpoint error table (~465), Trigger side effects (~483), Validation rules (~619-622), CLI status column (~849), CLI config warning (~944-948) | Remove conditional 0 logic, update validation rules |

### Documents requiring NO changes (confirmed)

| Document | Reason |
|----------|--------|
| `docs/features/tickets/cve-sync-kernel.md` | References increasing `run_timeout` (positive → higher positive). No `=0` logic. |
| `docs/features/tickets/cve-sync-mitre.md` | References `run_timeout` as a positive value. No `=0` logic. |
| `docs/features/platform/git-fetcher-infrastructure.md` | References increasing `run_timeout`. No `=0` logic. |
| `docs/features/platform/networking.md` | Timeout hierarchy is generic. No `=0` special case. |
| All other fetcher specs | Reference `run_timeout` only as a positive bounding mechanism. |

### Review findings referencing `run_timeout=0`

| Finding | Status | Impact of this change |
|---------|--------|----------------------|
| `FEI-SEC-004` (`docs/reviews/fetcher-infrastructure.md:154`) | RESOLVED | The finding's resolution ("added warning when timeout_seconds=0") becomes moot — the value is no longer accepted. No action needed on review files (they are historical records). |
| `docs/reviews/networking.md:82` | RESOLVED | References "Celery run_timeout" as defense-in-depth. Change strengthens this (it now always applies). No action needed. |

---

## Action Plan

### Step 1: Update `docs/data-model.md` — FetcherConfig table

**Location**: FetcherConfig table (~line 1428)

**Current text** (line 1428):
```
| run_timeout   | INTEGER     | NOT NULL, DEFAULT 3600 | Max execution time in seconds (hard ceiling). Also used to derive the soft time limit (×0.95) and stale detection threshold (+60s). 0 disables all time limits and stale detection. |
```

**Replace with**:
```
| run_timeout   | INTEGER     | NOT NULL, DEFAULT 3600 | Max execution time in seconds (hard ceiling). Also used to derive the soft time limit (×0.95) and stale detection threshold (+60s). Valid range: 60–604800 (enforced by API validation). |
```

**Location**: FetcherConfig table (~line 1429) — `request_delay`

**Current text** (line 1429):
```
| request_delay     | FLOAT       | NOT NULL, DEFAULT 0  | Minimum inter-request delay in seconds. 0 = no delay. CHECK (>= 0 AND <= 300). |
```

**Replace with**:
```
| request_delay     | FLOAT       | NOT NULL, DEFAULT 0  | Minimum inter-request delay in seconds. 0 = no delay. Valid range: 0–300 (enforced by API validation). |
```

**Location**: ER diagram (~line 322) — no change needed (already shows
`INTEGER run_timeout "DEFAULT 3600"` without CHECK).

### Step 2: Update `docs/features/platform/fetcher-infrastructure.md` — Options field table

**Location**: Options field table (~lines 1565-1572)

**Current text**:
```
| Key | Value | Condition |
|-----|-------|-----------|
| `time_limit` | `run_timeout` | `run_timeout > 0` |
| `soft_time_limit` | `max(1, floor(run_timeout * 0.95))` | `run_timeout > 0` |
| `queue` | fetcher's `queue` class attribute | `queue is not None` |

If `run_timeout = 0` AND `queue is None`, Options is `{}`.
If `run_timeout = 0` but `queue` is set, Options is `{"queue": "<queue>"}`.
```

**Replace with**:
```
| Key | Value | Condition |
|-----|-------|-----------|
| `time_limit` | `run_timeout` | Always |
| `soft_time_limit` | `max(1, floor(run_timeout * 0.95))` | Always |
| `queue` | fetcher's `queue` class attribute | `queue is not None` |

If `queue is None`, Options contains only `time_limit` and `soft_time_limit`.
```

### Step 3: Update `docs/features/platform/fetcher-infrastructure.md` — "Time Limits and Queue Routing" section

**Location**: ~lines 1589-1597

**Current text**:
```
- If `FetcherConfig.run_timeout > 0`: the redbeat entry's Options
  include `time_limit` and `soft_time_limit` (see Options field table
  above). Beat passes these to `apply_async()`, and the worker enforces
  them.
- If `FetcherConfig.run_timeout = 0`: no time limits are included. The
  task runs without a time ceiling.
- If the fetcher class defines `queue` (non-None): the Options include
  `"queue": "<queue>"`. Beat passes this to `apply_async()`, routing the
  task to the correct worker pool.
```

**Replace with**:
```
- The redbeat entry's Options always include `time_limit` and
  `soft_time_limit` (see Options field table above). Beat passes these
  to `apply_async()`, and the worker enforces them.
- If the fetcher class defines `queue` (non-None): the Options also
  include `"queue": "<queue>"`. Beat passes this to `apply_async()`,
  routing the task to the correct worker pool.
```

### Step 3b: Update `docs/features/platform/fetcher-infrastructure.md` — Soft time limit formula note

**Location**: ~lines 1605-1608

**Current text**:
```
**Soft time limit formula**: `max(1, floor(run_timeout * 0.95))` — same
formula defined in the `FetcherConfig` section (prevents Celery from
interpreting `soft_time_limit = 0` as "disabled" for very small
`run_timeout` values).
```

**Replace with**:
```
**Soft time limit formula**: `max(1, floor(run_timeout * 0.95))` — same
formula defined in the `FetcherConfig` section. With the minimum
`run_timeout` of 60, the soft limit is always >= 57 (the `max(1, ...)`
never activates in practice).
```

### Step 4: Update `docs/features/platform/fetcher-infrastructure.md` — Redbeat propagation table

**Location**: "Which Changes Require Redbeat Propagation" table (~line 1772)

**Current text**:
```
| `run_timeout` changed | Update the redbeat entry's Options (`time_limit`, `soft_time_limit`). If new value is 0, remove time limit keys (Options retains `queue` if present). |
```

**Replace with**:
```
| `run_timeout` changed | Update the redbeat entry's Options (`time_limit`, `soft_time_limit`) with the new derived values. |
```

### Step 5: Update `docs/features/platform/fetcher-infrastructure.md` — Propagation mechanism

**Location**: ~line 1793

**Current text**:
```
    - If `run_timeout` changed (without `enabled` change): update the
      redbeat entry's Options with the new `time_limit` and
      `soft_time_limit` values (or clear Options if the new value is 0)
```

**Replace with**:
```
    - If `run_timeout` changed (without `enabled` change): update the
      redbeat entry's Options with the new `time_limit` and
      `soft_time_limit` values
```

### Step 6: Update `docs/features/platform/fetcher-infrastructure.md` — Concurrency table

**Location**: ~line 2223

**Current text**:
```
| Any trigger with stale run but `run_timeout = 0` | any | any | Time limits and stale detection disabled — treated as active run (409 or silent discard) |
```

**Action**: Remove this entire row from the table. The scenario is no longer
possible because `run_timeout` is always positive, meaning stale detection
is always active.

### Step 7: Update `docs/features/platform/fetcher-infrastructure.md` — Stale Run Detection

**Location**: ~lines 2248-2262

**Current text**:
```
The default `run_timeout` is 3600 (1 hour), yielding a stale threshold
of 3660 seconds. If `run_timeout` is set to 0, stale detection is
disabled for that fetcher — the run is never considered stale regardless
of how long it has been running.

When a stale run is detected (by the Celery task or the API trigger
endpoint), it is resolved by updating the stale `FetcherRun`
record:

**Operational risk of `run_timeout=0`**: disabling stale detection
means a fetcher that gets stuck will block all future executions
indefinitely, requiring manual intervention. When `run_timeout` is
set to 0 via the API, the validation rules document the operational risk
(see `docs/features/platform/fetcher-operations.md`, "Update Fetcher
Config").
```

**Replace with**:
```
The default `run_timeout` is 3600 (1 hour), yielding a stale threshold
of 3660 seconds. The minimum allowed `run_timeout` is 60 seconds
(threshold: 120s); the maximum is 604800 seconds (7 days, threshold:
604860s). Stale detection is always active for every fetcher.

When a stale run is detected (by the Celery task or the API trigger
endpoint), it is resolved by updating the stale `FetcherRun`
record:
```

### Step 8: Update `docs/features/platform/fetcher-infrastructure.md` — FetcherConfig column descriptions

**Location**: ~line 2393 (`run_timeout`)

**Current text**:
```
| run_timeout | INTEGER | NOT NULL, DEFAULT 3600 | Maximum execution time in seconds (hard ceiling). The task is guaranteed to be terminated at this limit. Also used as the basis for the stale run detection threshold. 0 disables both time limits and stale detection. |
```

**Replace with**:
```
| run_timeout | INTEGER | NOT NULL, DEFAULT 3600 | Maximum execution time in seconds (hard ceiling). The task is guaranteed to be terminated at this limit. Also used as the basis for the stale run detection threshold. Valid range: 60–604800 (1 minute to 7 days; enforced by API validation). |
```

**Location**: ~line 2394 (`request_delay`)

**Current text**:
```
| request_delay | FLOAT | NOT NULL, DEFAULT 0 | Minimum inter-request delay in seconds. 0 = no delay. CHECK (>= 0 AND <= 300). Applied by the fetcher via `asyncio.sleep(self.config.request_delay)`. |
```

**Replace with**:
```
| request_delay | FLOAT | NOT NULL, DEFAULT 0 | Minimum inter-request delay in seconds. 0 = no delay. Valid range: 0–300 (enforced by API validation). Applied by the fetcher via `asyncio.sleep(self.config.request_delay)`. |
```

### Step 9: Update `docs/features/platform/fetcher-infrastructure.md` — Three purposes section

**Location**: ~lines 2405-2435

**Current text** (the section starting "- `run_timeout` serves three purposes:"):

The "when > 0" qualifiers and the "When set to 0" paragraph need to be
removed/simplified.

**Current** (~lines 2406-2425):
```
  1. **Celery hard time limit** (`time_limit`): when > 0, the Celery
     task's `time_limit` is set to `run_timeout`. If the task exceeds
     this duration, the worker forcibly terminates the process
     (SIGKILL). This is the absolute ceiling — the task is guaranteed
     dead at this point.
  2. **Celery soft time limit** (`soft_time_limit`): when > 0, set to
     `max(1, floor(run_timeout × 0.95))`. When reached, Celery raises
     `SoftTimeLimitExceeded` in the task context. This gives the task
     a grace window (5% of `run_timeout`) to finalize the `FetcherRun`
     record cleanly before the hard kill.
  3. **Stale run detection threshold**: when > 0, a `FetcherRun`
     record is considered stale if it has been in `running` status
     for longer than `run_timeout + 60` seconds. The 60-second
     margin accounts for clock skew in multi-node deployments (the
     process is guaranteed dead at `run_timeout` by the hard limit).
  When set to 0, all three mechanisms are disabled: Celery does not
  enforce any time limit, stale detection treats the run as
  indefinitely active, and no soft time limit exception is raised.
  The default of 3600 seconds (1 hour) applies when a `FetcherConfig`
  record is auto-created for a newly registered fetcher.
```

**Replace with**:
```
  1. **Celery hard time limit** (`time_limit`): the Celery task's
     `time_limit` is set to `run_timeout`. If the task exceeds this
     duration, the worker forcibly terminates the process (SIGKILL).
     This is the absolute ceiling — the task is guaranteed dead at
     this point.
  2. **Celery soft time limit** (`soft_time_limit`): set to
     `max(1, floor(run_timeout × 0.95))`. When reached, Celery raises
     `SoftTimeLimitExceeded` in the task context. This gives the task
     a grace window (5% of `run_timeout`) to finalize the `FetcherRun`
     record cleanly before the hard kill.
  3. **Stale run detection threshold**: a `FetcherRun` record is
     considered stale if it has been in `running` status for longer
     than `run_timeout + 60` seconds. The 60-second margin accounts
     for clock skew in multi-node deployments (the process is
     guaranteed dead at `run_timeout` by the hard limit).
  All three mechanisms are always active (API validation guarantees
  `run_timeout >= 60`). The default of 3600 seconds (1 hour) applies
  when a `FetcherConfig` record is auto-created for a newly registered
  fetcher. The maximum allowed value is 604800 seconds (7 days),
  providing ample headroom for long-running operations while ensuring
  eventual recovery from stuck processes.
```

### Step 10: Verify `docs/features/platform/fetcher-infrastructure.md` — SoftTimeLimitExceeded message

**Location**: ~line 748

**Current text** contains:
```
| `SoftTimeLimitExceeded` | `f"Execution timed out after {self.config.run_timeout}s ({processed} items processed before timeout). Consider increasing run_timeout via FetcherConfig for fetcher '{self.name}'."` (where `processed = self._created + self._updated + self._failed`) |
```

**Action**: No change needed — this message is already correct for positive
values. The operator discovers the upper bound (604800s) from the API
validation error if they attempt to exceed it; embedding it in the timeout
message would create a maintenance coupling without operational benefit.

### Step 11: Update `docs/features/platform/fetcher-operations.md` — Trigger endpoint error table

**Location**: ~line 465

**Current text**:
```
| 409 | `FETCHER_ALREADY_RUNNING` | Fetcher is already running (a non-stale `FetcherRun` with status `running` exists for this fetcher). If the active run is stale and `run_timeout > 0`, it is marked as `failure` and the new run proceeds (returns 202). |
```

**Replace with**:
```
| 409 | `FETCHER_ALREADY_RUNNING` | Fetcher is already running (a non-stale `FetcherRun` with status `running` exists for this fetcher). If the active run is stale, it is marked as `failure` and the new run proceeds (returns 202). |
```

### Step 12: Update `docs/features/platform/fetcher-operations.md` — Trigger side effects

**Location**: ~lines 482-483

**Current text**:
```
  soft_time_limit=soft_time_limit, queue=queue)` where `time_limit` and
  `soft_time_limit` are read from `FetcherConfig.run_timeout` using the
  same formula as the redbeat entry (see
  `docs/features/platform/fetcher-infrastructure.md`, "Celery Beat
  Schedule Synchronization — Time Limits and Queue Routing"). If
  `run_timeout = 0`, no time limits are passed. `queue` is read from the
```

**Replace with**:
```
  soft_time_limit=soft_time_limit, queue=queue)` where `time_limit` and
  `soft_time_limit` are read from `FetcherConfig.run_timeout` using the
  same formula as the redbeat entry (see
  `docs/features/platform/fetcher-infrastructure.md`, "Celery Beat
  Schedule Synchronization — Time Limits and Queue Routing"). `queue` is read from the
```

### Step 13: Update `docs/features/platform/fetcher-operations.md` — Validation rules

**Location**: ~lines 619-622

**Current text**:
```
- `run_timeout`: must be a non-negative integer. 0 disables all
  Celery time limits (soft and hard) and stale run detection (stuck
  runs will never be forcibly terminated and will require manual
  resolution via the CLI). Default: 3600 (1 hour)
```

**Replace with**:
```
- `run_timeout`: must be an integer between 60 and 604800 (1 minute
  to 7 days). Controls Celery hard/soft time limits and the stale run
  detection threshold. Default: 3600 (1 hour)
```

### Step 14: Update `docs/features/platform/fetcher-operations.md` — CLI status column

**Location**: ~lines 844-849

**Current text**:
```
1. If a `FetcherRun` with `status = running` exists for the fetcher:
   show `running ({elapsed} elapsed)` where elapsed is calculated from
   `started_at`. If `run_timeout > 0` and the elapsed time exceeds
   `run_timeout + 60` (the stale threshold), append `(stale?)` —
   e.g., `running (1h 2m elapsed, stale?)`. This indicates the
   process was terminated by the hard limit and the orphaned record
   has not yet been cleaned up by stale detection.
   If `run_timeout = 0`, the `(stale?)` hint is never shown.
```

**Replace with**:
```
1. If a `FetcherRun` with `status = running` exists for the fetcher:
   show `running ({elapsed} elapsed)` where elapsed is calculated from
   `started_at`. If the elapsed time exceeds `run_timeout + 60` (the
   stale threshold), append `(stale?)` — e.g.,
   `running (1h 2m elapsed, stale?)`. This indicates the process was
   terminated by the hard limit and the orphaned record has not yet
   been cleaned up by stale detection.
```

### Step 15: Update `docs/features/platform/fetcher-operations.md` — CLI config warning

**Location**: ~lines 944-948

**Current text**:
```
When `run_timeout` is 0, the command MUST emit a warning to stderr:

```
Warning: Execution timeout disabled — runs will never be forcibly terminated and stuck runs will require manual resolution.
```
```

**Action**: Remove this entire block (lines 944-948). The value `0` is no
longer accepted, so this warning can never trigger. No replacement needed.

### Step 16: Update `docs/features/platform/fetcher-infrastructure.md` — Propagation failure section

**Location**: ~line 1834

**Current text**:
```
- If a `run_timeout` change failed to propagate: the fetcher runs with
  the old time limits until Beat restarts. If the new limit is shorter
  (admin reduced it), the old limit is still a valid ceiling. If the new
  limit is longer (admin increased it), the task might time out
  prematurely once — recoverable on the next scheduled run after Beat
  restart.
```

**Action**: No change needed — this text is already correct for positive
values only.

### Step 17: Verify `docs/features/platform/fetcher-infrastructure.md` — soft time limit formula note

**Location**: ~lines 2427-2435

**Current text**:
```
  **Formula**: `soft_time_limit = max(1, floor(run_timeout × 0.95))`.
  The `max(1, ...)` prevents Celery from interpreting
  `soft_time_limit = 0` as "disabled" when `run_timeout` is very small
  (e.g., `run_timeout = 1` would produce `floor(0.95) = 0` without the
  `max(1, ...)`). The grace window is always 5% of `run_timeout` (e.g., 180s
  for 3600s, 30s for 600s, 3s for 60s). For very small `run_timeout`
  values (< 20), the grace window is minimal but the hard limit always
  provides a backstop — the task is guaranteed dead at `run_timeout`
  regardless of whether the soft signal achieves clean finalization.
```

**Replace with**:
```
  **Formula**: `soft_time_limit = max(1, floor(run_timeout × 0.95))`.
  The `max(1, ...)` is a safety net that prevents Celery from
  interpreting `soft_time_limit = 0` as "disabled" if an out-of-range
  value reaches the formula (API validation enforces `run_timeout >= 60`,
  yielding a minimum soft limit of 57 — the `max(1, ...)` never activates
  under normal operation). The grace window is always 5% of `run_timeout`
  (e.g., 180s for 3600s, 30s for 600s, 3s for 60s). With the minimum
  valid `run_timeout` of 60s, the grace window is 3s — tight but
  sufficient for writing a single `FetcherRun` status update, with the
  hard limit as backstop.
```

### Step 18: Verify no other documents reference `run_timeout=0` or conditional `when > 0`

Perform a final grep for `run_timeout.*0`, `timeout.*disable`, and
`when.*> 0` across `docs/` to confirm all references have been addressed.
The following are expected to remain unchanged:

- `docs/reviews/*` — historical records; no modification needed.
- `docs/features/tickets/cve-sync-kernel.md` — references increasing the
  value, not setting to 0.
- `docs/features/tickets/cve-sync-mitre.md` — same.
- `docs/features/platform/git-fetcher-infrastructure.md` — same.
- `docs/features/platform/networking.md` — generic diagram, no `=0` logic.

### Step 19: Add import-time validation for `default_request_delay` (opportunistic fix)

**Document**: `docs/features/platform/fetcher-infrastructure.md`

**Location**: "Import-time validation" section (~lines 924-955), after
point 8.

**Add** a new point 9:

```
9. `default_request_delay` MUST be a non-negative float (`>= 0`).
   Negative values are structurally invalid (delay cannot be negative).
   The operational upper bound (currently 300) is enforced exclusively
   by the PATCH endpoint's Pydantic validation, not at import time —
   keeping the upper limit defined in a single location.
```

**Rationale**: this is a pre-existing gap (not introduced by this change)
fixed opportunistically. The `default_request_delay` class attribute is
used by `bootstrap_fetcher_configs()` to set the initial value in the DB.
Without this check, a developer who accidentally sets a negative value
would introduce an invalid DB state that causes `asyncio.sleep()` to raise
`ValueError` on every run. The `>= 0` check catches this at startup (same
as other structural validations), while the operational upper bound (300)
remains solely in the Pydantic schema — if the upper limit changes in the
future, only the Pydantic schema needs updating.

### Step 20: Run reviewers on affected specifications

After applying all changes from Steps 1–19, invoke the following reviewers
to verify correctness and detect regressions:

1. **`@spec-gap-analyzer`** on `docs/features/platform/fetcher-infrastructure.md`
   — this is the primary spec with the most changes; verify no gaps were
   introduced by the simplification.

2. **`@spec-gap-analyzer`** on `docs/features/platform/fetcher-operations.md`
   — verify the API and CLI sections remain complete after removing the
   `=0` branches.

3. **`@spec-coherence-reviewer`** on `docs/features/platform/fetcher-infrastructure.md`
   — verify consistency with `data-model.md` and `fetcher-operations.md`
   after the coordinated changes.

4. **`@data-model-reviewer`** on the `FetcherConfig` table changes in
   `docs/data-model.md` — verify the updated column descriptions and
   removal of the `request_delay` CHECK are consistent with the spec.

5. **`@api-convention-reviewer`** on `docs/features/platform/fetcher-operations.md`
   — verify the PATCH endpoint validation change conforms to API
   conventions.

### Step 21: Delete this draft

After all reviewers pass without "Needs revision" findings related to this
change, delete `docs/drafts/remove-run-timeout-zero-semantics.md`.

---

## Rollback considerations

If a future requirement genuinely needs unlimited execution time (which no
current or planned fetcher does), the options are:

1. **Increase the upper bound** (e.g., from 604800 to 2592000 = 30 days) —
   trivial change to the Pydantic validation schema.
2. **Re-introduce `0` semantics** — would require re-adding all the
   conditional branches plus implementing the missing CLI recovery command
   and addressing the single-instance violation during recovery.

Option 1 is always preferable unless there is a proven need for truly
unbounded execution.
