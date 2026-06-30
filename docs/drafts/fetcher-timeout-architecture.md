# Fetcher Timeout Architecture

**Status**: Draft
**Created**: 2026-06-30
**Origin**: Finding GFI-GAP-01 analysis → expanded to BaseFetcher scope
**Affects**: `fetcher-infrastructure.md`, `git-fetcher-infrastructure.md`,
`cve-fetcher-infrastructure.md`, `cve-sync-nvd.md`, `cve-sync-redhat.md`,
`cve-sync-ghsa.md`, `cve-sync-epss.md`, `cve-sync-osv.md`, `cve-sync-kev.md`

## Problem Statement

Two related issues exist in the current fetcher timeout architecture:

### Issue 1: `SoftTimeLimitExceeded` swallowed by per-item catch (git-fetcher)

In `git-fetcher-infrastructure.md`, step 10d of the `execute()` template
method catches "any exception" during per-item processing. Since Celery's
`SoftTimeLimitExceeded` inherits from `Exception`, it is caught by the
per-item handler, logged as a single item failure, and consumed. The loop
continues to the next item with **no active timeout** — the soft time
limit signal is delivered once and, once caught, is never re-raised.

**Consequence**: the task runs indefinitely past `run_timeout` (until the
delta is exhausted or the process is killed externally), silently
defeating the timeout mechanism.

### Issue 2: No hard time limit backstop (BaseFetcher)

The current spec defines only a Celery `soft_time_limit` (derived from
`run_timeout`). There is no Celery `time_limit` (hard limit). If a task
does not respond to the soft limit (due to Issue 1, a blocking
non-interruptible operation, or any other reason), nothing forces it to
stop.

**Consequence**: a non-cooperative task stays alive past `run_timeout`.
When the next scheduled trigger fires, the concurrency check sees the
`FetcherRun` record has exceeded `run_timeout`, declares it stale, marks
it as `failure`, and starts a new run — while the old task is still
executing. This violates the single-instance invariant (line 1301 of
`fetcher-infrastructure.md`).

### Issue 3: Same `SoftTimeLimitExceeded` vulnerability in all API-based fetchers

The same "any exception" catch pattern from Issue 1 exists in
`cve-fetcher-infrastructure.md`, which defines two canonical code
templates ("Session Lifecycle for API-based CVE Fetchers") that all
API-based fetcher specs follow:

- **Pattern 1** (loop over `fetch_single()` calls): `except Exception:`
  catches all exceptions per-item, including `SoftTimeLimitExceeded`
- **Pattern 2** (paginated inline processing): same `except Exception:`
  pattern

Since individual fetcher specs (NVD, Red Hat, GHSA, EPSS, OSV, KEV)
copy or reference these canonical patterns, the vulnerability is
replicated across every API-based CVE fetcher.

**Scope of impact**: all fetchers with per-item processing loops —
not just git-based ones — are affected. The fix must be applied at
the infrastructure level (canonical patterns) and propagated to
individual specs.

### Why stale detection alone does not solve this

Stale detection is a **DB-only bookkeeping** mechanism (lines 1375-1379
of `fetcher-infrastructure.md`). It updates the orphaned `FetcherRun`
record but does not touch the OS process or the Celery task. The Celery
task ID is not stored in `FetcherRun`, so revocation is not possible.
The spec explicitly states stale detection is "a recovery mechanism for
unclean process terminations (OOM-kill, node crash, kill -9)" — it
assumes the process is already dead.

## Design Decisions

### D1: Redefine `run_timeout` as the absolute maximum (hard kill)

**Current semantics**: `run_timeout` = Celery `soft_time_limit`
**New semantics**: `run_timeout` = the absolute maximum execution time.
The task is **guaranteed dead** at `run_timeout`.

Derived values:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Celery `soft_time_limit` | `run_timeout × 0.95` | Cooperative stop signal → `run()` finalizes record |
| Celery `time_limit` (hard) | `run_timeout` | SIGKILL backstop for non-cooperative tasks |
| Stale threshold | `run_timeout + margin` | DB cleanup for orphaned records (process guaranteed dead) |

The `margin` in the stale threshold accounts for the brief window between
SIGKILL delivery and process termination (kernel scheduling). A fixed,
hardcoded margin of 60 seconds is used (not configurable per-fetcher).
SIGKILL is immediate in practice; the margin exists for clock skew in
multi-node deployments. This value is embedded in the stale detection
logic, not derived from `FetcherConfig`.

**Rationale**: `run_timeout` now means what an admin intuitively expects
("a run will never exceed this duration"). The stale threshold stays
close to `run_timeout`, minimizing recovery latency for dead processes.

### D2: Soft limit is the primary finalization mechanism

The cooperative flow (soft limit → exception propagation → `run()`
finalization) handles >99% of cases. The hard limit only fires for
non-cooperative tasks that ignore the soft signal. The architecture is:

```
soft_time_limit (0.95×)     time_limit (1.0×)     stale threshold (+ margin)
        │                         │                         │
        ▼                         ▼                         ▼
  SoftTimeLimitExceeded     SIGKILL (force)         Mark record failure
  propagates to run()       process dies            + start new run
  → record finalized        record stays "running"
  → task exits cleanly      → stale detection
                              cleans up later
```

In the cooperative case, the record is finalized **before** the hard
limit fires — stale detection never triggers. In the non-cooperative
case, the hard limit ensures the task is dead before any stale check
runs.

### D3: Schedule is best-effort cadence (no coupling to timeout)

The schedule interval and `run_timeout` are independent parameters:

- `run_timeout` = safety ceiling (worst-case protection)
- schedule = desired data freshness cadence

An admin MAY set schedule < run_timeout (e.g., every 30 min with 1h
timeout). This is handled by existing concurrency control: intermediate
ticks are discarded when a run is still active. No validation prevents
this combination.

**Soft warning** (documentation/API note): when schedule < run_timeout,
note that runs exceeding the schedule interval will cause subsequent
ticks to be skipped until the current run finishes.

### D4: No task_id column in FetcherRun (YAGNI)

With the hard limit guaranteeing task death, there is no need to revoke
tasks via `app.control.revoke()`. Adding a `celery_task_id` column is
unnecessary complexity. If a future feature requires it (observability,
manual revocation), it can be added via migration at that time.

### D5: Exception exclusion in per-item catches (all fetchers)

Every per-item processing loop across all fetcher types MUST exclude
whole-run exceptions from the catch. This affects:

- **BaseGitFetcher** step 10d (git-fetcher-infrastructure.md)
- **Canonical Pattern 1** in cve-fetcher-infrastructure.md (fetch_single loop)
- **Canonical Pattern 2** in cve-fetcher-infrastructure.md (paginated inline)
- **All individual API-based fetcher specs** that show inline code
  following these patterns

Excluded exceptions (MUST re-raise):

- `SoftTimeLimitExceeded` — whole-run timeout signal (swallowing it
  defeats the timeout mechanism)
- `MemoryError` — continuing is futile (subsequent items will also fail)

These exceptions MUST propagate to `run()` for proper finalization.

**Not excluded** (caught per-item as today):
- `GitFileError` — per-item blob download failure
- `IntegrityError`, `DataError` — per-item data issues
- Other `Exception` subclasses from `process_item()` — per-item logic
  errors

**OperationalError / database connection loss**: caught per-item (not
excluded), but all subsequent items will also fail → the all-items-failed
safety check triggers → status becomes `failure`, cursor does not
advance. This is suboptimal (wastes time iterating through doomed items)
but not dangerous — the safety check protects cursor integrity. Excluding
database errors adds complexity (distinguishing transient vs. fatal DB
errors is non-trivial) for marginal benefit. The timeout mechanism
provides the actual time bound.

### D6: Informative error message for timeout (Opzione A)

When `SoftTimeLimitExceeded` propagates to `run()`, the error message
stored in `FetcherRun.error_message` should include actionable context.
This is achieved by catching `SoftTimeLimitExceeded` at the
`execute()` level in BaseGitFetcher (above the per-item loop) and
re-raising as `FetcherError` with an enriched message.

Available context at that point:
- `run_timeout`: from FetcherConfig (accessible via `self.config.run_timeout`)
- Items processed: `self._created + self._updated + self._failed`
  (metric counters, preserved on exception)
- Total items in delta: **not available** (local variable lost on
  exception propagation)

Message format:
```
"Execution timed out after {self.config.run_timeout}s ({processed} items
processed before timeout). Consider temporarily increasing run_timeout
via FetcherConfig for fetcher '{self.name}'."
```

No `self._total_items` instance variable added — the processed count
alone provides sufficient diagnostic value for a rare scenario.

### D7: Scope — where each change lives

| Change | Spec | Rationale |
|--------|------|-----------|
| Redefine `run_timeout` semantics (D1) | `fetcher-infrastructure.md` | Applies to all fetchers |
| Hard time limit + stale threshold decoupling (D1) | `fetcher-infrastructure.md` | BaseFetcher concern |
| SoftTimeLimitExceeded convention (general principle) | `fetcher-infrastructure.md` | All fetchers should not swallow it |
| Step 10d exception exclusion list (D5) | `git-fetcher-infrastructure.md` | Concrete application in git template method |
| Canonical pattern updates (D5) | `cve-fetcher-infrastructure.md` | Authoritative source for API-based fetcher patterns |
| Individual fetcher pattern alignment (D5) | `cve-sync-{nvd,redhat,ghsa,epss,osv,kev}.md` | Inline code must match updated canonical patterns |
| Informative error message (D6) | `git-fetcher-infrastructure.md` | Git-fetcher-specific enrichment |
| Operational note (large deltas) | `git-fetcher-infrastructure.md` | Git-fetcher-specific guidance |
| SoftTimeLimitExceeded exclusion check | `.opencode/agents/fetcher-compliance-reviewer.md` | Agent must verify the new convention |
| Agent description update | `.opencode/README.md` | Keep README in sync with agent changes |

## Spec Modification Plan

### Phase 1: `fetcher-infrastructure.md`

#### 1.1 FetcherConfig `run_timeout` definition (lines 1474, 1484-1495)

**Current text** (line 1474):
```
| run_timeout | INTEGER | NOT NULL, DEFAULT 3600 | Maximum execution time
in seconds. Also used as the stale run detection threshold. 0 disables
both soft time limit and stale detection. |
```

**Replace with**:
```
| run_timeout | INTEGER | NOT NULL, DEFAULT 3600 | Maximum execution time
in seconds (hard ceiling). The task is guaranteed to be terminated at
this limit. Also used as the basis for the stale run detection threshold.
0 disables both time limits and stale detection. |
```

**Current notes** (lines 1484-1495):

**Replace with**:
```
- `run_timeout` serves three purposes:
  1. **Celery hard time limit** (`time_limit`): when > 0, the Celery
     task's `time_limit` is set to `run_timeout`. If the task exceeds
     this duration, the worker forcibly terminates the process
     (SIGKILL). This is the absolute ceiling — the task is guaranteed
     dead at this point.
  2. **Celery soft time limit** (`soft_time_limit`): when > 0, set to
     `floor(run_timeout × 0.95)`. When reached, Celery raises
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

  **Formula**: `soft_time_limit = floor(run_timeout × 0.95)`. No
  special cases. The grace window is always 5% of `run_timeout`
  (e.g., 180s for 3600s, 30s for 600s, 3s for 60s). For very small
  `run_timeout` values, the grace window is proportionally small but
  the hard limit always provides a backstop — the task is guaranteed
  dead at `run_timeout` regardless of whether the soft signal
  achieves clean finalization.
```

#### 1.2 Stale Run Detection (lines 1356-1398)

**Current text** (lines 1358-1362):
```
A run is considered **stale** when it has been in `running` status for
longer than the fetcher's `run_timeout` (from `FetcherConfig`).
```

**Replace with**:
```
A run is considered **stale** when it has been in `running` status for
longer than `run_timeout + 60` seconds (the **stale threshold**). The
60-second margin is a hardcoded constant (not configurable). It ensures
that the hard time limit (`time_limit = run_timeout`) has had time to
terminate the process before a new run is started — guaranteeing the
single-instance invariant even if the soft time limit was not honored.
```

**Add after line 1392** (after the SIGTERM paragraph):
```
**Relationship to hard time limit**: the stale threshold is
intentionally set ABOVE the hard time limit (`run_timeout + 60 > run_timeout`).
This ensures that when stale detection triggers, the process is
already dead (killed by the hard limit at `run_timeout`). The stale
detection mechanism therefore never needs to kill or revoke a task —
it only cleans up the orphaned database record left behind by a
force-killed process.
```

#### 1.3 Concurrency Control — scenario table (line 1339)

Update the `run_timeout = 0` row to reflect the new terminology:

```
| Any trigger with stale run but `run_timeout = 0` | any | any | Time
limits and stale detection disabled — treated as active run (409 or
silent discard) |
```

#### 1.4 BaseFetcher fallback — `SoftTimeLimitExceeded` convention

**Add new subsection after the generic fallback table explanation**
(after line 703 — i.e., after the `error_detail`/`error_traceback`
paragraph, before `### What constitutes infrastructure details`):

```
### `SoftTimeLimitExceeded` handling convention

`SoftTimeLimitExceeded` is a **whole-run signal**, not a per-item error.
All fetcher implementations MUST ensure this exception propagates to
`BaseFetcher.run()` for proper finalization. Specifically:

- Per-item exception handlers (e.g., try/except loops that catch
  `Exception` to isolate individual item failures) MUST exclude
  `SoftTimeLimitExceeded` from the catch. Failing to do so silently
  defeats the timeout mechanism — the exception is consumed, and the
  task continues indefinitely past the soft time limit until the hard
  limit terminates the process.
- `MemoryError` SHOULD also be excluded from per-item catches, as
  continuing after memory exhaustion is futile.

The recommended pattern for per-item exception handling:

    ```python
    from celery.exceptions import SoftTimeLimitExceeded

    for item in items:
        try:
            process(item)
        except (SoftTimeLimitExceeded, MemoryError):
            raise  # whole-run signals — never catch per-item
        except Exception as e:
            session.rollback()
            self.record_failed()
            logger.warning("Failed to process %s: %s", item, e)
            continue
    ```

Concrete fetchers MAY catch `SoftTimeLimitExceeded` at the
`execute()` level (wrapping the entire processing loop) to enrich the
error message before re-raising as `FetcherError`. This is optional —
if not caught at the `execute()` level, `run()` applies the generic
fallback message ("Execution timed out").

**Not excluded — `OperationalError` and database connection loss**:
database errors are caught per-item (not excluded from the per-item
catch). When a connection is lost, all subsequent items will also fail.
The all-items-failed safety check in `run()` then triggers, setting
status to `failure` and preventing cursor advancement. This is
suboptimal (the loop iterates through doomed items until the timeout
fires) but not dangerous — the safety check protects cursor integrity.
Excluding database errors would add complexity (distinguishing
transient vs. fatal DB errors is non-trivial) for marginal benefit,
and the timeout mechanism provides the actual time bound.
```

#### 1.5 Status determination — clarification

**Add after line 109** (after "preserved for diagnostics but do not
influence the status"), as a continuation of the same numbered item 1,
at the same indentation level (6 spaces):

```
      This includes `SoftTimeLimitExceeded` — when the soft time limit
      is reached, the exception propagates to `run()` (either directly
      or re-raised as `FetcherError` by `execute()`), resulting in
      `failure` status. The hard time limit (`time_limit`) terminates
      the process if the soft limit fails to stop execution within the
      grace window (5% of `run_timeout`).
```

### Phase 2: `git-fetcher-infrastructure.md`

#### 2.1 Step 10d — exception exclusion list

**Current text** (lines 685-688):
```
    d. If any exception is raised during steps 10a, 10c, or
       `commit_and_dispatch()`: call `session.rollback()`, log WARNING
       ("Failed to process {path}: {error}"), call `record_failed()`,
       continue to next item
```

**Replace with**:
```
    d. If `SoftTimeLimitExceeded` or `MemoryError` is raised during
       steps 10a, 10c, or `commit_and_dispatch()`: **re-raise
       immediately** (these are whole-run signals, not per-item errors;
       see "SoftTimeLimitExceeded handling convention" in
       `fetcher-infrastructure.md`).
    e. If any other exception is raised during steps 10a, 10c, or
       `commit_and_dispatch()`: call `session.rollback()`, log WARNING
       ("Failed to process {path}: {error}"), call `record_failed()`,
       continue to next item
```

**Note**: this renumbers step 10e (was 10d). Step 11 (cursor write) and
all subsequent references remain unchanged (they reference step 10 as a
whole, not sub-steps).

#### 2.2 Informative error message — new section

**Add after step 11** (after line 706, before "Infrastructure errors"):

```
**Timeout error enrichment**: `BaseGitFetcher.execute()` wraps the
processing loop (steps 5-11) in an outer try/except that catches
`SoftTimeLimitExceeded` and re-raises as `FetcherError` with
enriched context:

    ```python
    except SoftTimeLimitExceeded:
        processed = self._created + self._updated + self._failed
        timeout = self.config.run_timeout
        raise FetcherError(
            f"Execution timed out after {timeout}s "
            f"({processed} items processed before timeout). "
            f"Consider temporarily increasing run_timeout via "
            f"FetcherConfig for fetcher '{self.name}'."
        ) from None
    ```

This enriched message is stored in `FetcherRun.error_message` (via
the `FetcherError` handling path in `run()`), providing operators with
immediate diagnostic context in the dashboard.

If `SoftTimeLimitExceeded` is raised OUTSIDE the processing loop
(during clone, fetch, or delta computation), it propagates naturally
to `run()` and receives the generic fallback message ("Execution timed
out") — no enrichment is needed since the failure point is obvious
from the phase.
```

#### 2.3 Phase-based error classification — update

**Add row to the table** (after line 382):

```
| Per-item processing (step 10d) | `SoftTimeLimitExceeded` or `MemoryError` | (original exception) | **Re-raise immediately**. Not a per-item error — whole-run signal |
```

#### 2.4 Operational note — large deltas

**Add new subsection after line 325** (after the "Cursor SHA
Unreachable" subsection, immediately before `## Runtime Dependencies`
at line 327):

```
### Operational: large delta convergence

After an extended outage (weeks or longer), the recovery delta may
contain thousands of files. In a blobless clone, each file requires an
on-demand blob download. If the delta cannot be fully processed within
`run_timeout`, the soft time limit fires, the run ends as `failure`,
and the cursor does not advance.

On the next scheduled execution, the same delta is recomputed. Items
already processed produce idempotent upserts (no observable side
effects). The loop processes items until the timeout fires again. This
continues across successive runs until all items are processed —
**convergence is guaranteed** through the combination of idempotent
processing and stable cursor position.

To accelerate recovery, an admin can temporarily increase `run_timeout`
for the specific fetcher via FetcherConfig (e.g., from 3600 to 36000
for a one-time large delta), then reset it after the recovery completes.
The dashboard's `error_message` field includes the number of items
processed before timeout, allowing the admin to estimate the required
duration.

This scenario is uncommon in normal operation:
- First-run records HEAD without processing (no delta)
- Recovery delta uses `cursor_committed_at - 1 day` as boundary (not
  full repo history)
- Only extended outages (weeks+) produce deltas exceeding 1 hour of
  processing time
```

#### 2.5 "No anti-loop logic" note — update

**Current text** (lines 389-392):
```
**No anti-loop logic**: Celery task timeout limits each run's
duration. Repeated failures (e.g., corruption loop from faulty disk)
produce visible `failure` records in the fetcher dashboard for
operator intervention.
```

**Replace with**:
```
**No anti-loop logic**: the Celery hard time limit (`time_limit =
run_timeout`) guarantees each run's duration is bounded. The soft time
limit (at 95% of `run_timeout`) provides early warning for clean
shutdown. Step 10d ensures `SoftTimeLimitExceeded` propagates rather
than being caught as a per-item error. Repeated failures (e.g.,
corruption loop from faulty disk) produce visible `failure` records in
the fetcher dashboard for operator intervention.
```

### Phase 2b: `cve-fetcher-infrastructure.md`

#### 2b.1 Canonical Pattern 1 — fetch_single loop (lines 275-289)

**Current text**:
```python
async def execute(self, session: AsyncSession) -> None:
    for cve_id in scope:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
        except CVENotInSource:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "missing")
        except Exception:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "failure")
            self.record_failed()
        await asyncio.sleep(self.config.request_delay)
```

**Replace with**:
```python
async def execute(self, session: AsyncSession) -> None:
    for cve_id in scope:
        try:
            post_ingest = await self.fetch_single(cve_id, session)
            await self.commit_and_dispatch(session, post_ingest)
        except (SoftTimeLimitExceeded, MemoryError):
            raise  # whole-run signals — never catch per-item
        except CVENotInSource:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "missing")
        except Exception:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "failure")
            self.record_failed()
        await asyncio.sleep(self.config.request_delay)
```

**Add note after the code block** (after line 289):
```
Note: `SoftTimeLimitExceeded` and `MemoryError` are excluded from
per-item error isolation — they are whole-run signals that must
propagate to `BaseFetcher.run()` for proper finalization. See
"`SoftTimeLimitExceeded` handling convention" in
`fetcher-infrastructure.md`.
```

#### 2b.2 Canonical Pattern 2 — paginated inline processing (lines 298-311)

**Current text**:
```python
async def execute(self, session: AsyncSession) -> None:
    for item in source_items:
        try:
            result = await upsert_cve(session, cve_id, self.cve_source_type, payload)
            await upsert_references(session, ...)
            post_ingest = build_post_ingest_tasks(result, payload)
            await self.commit_and_dispatch(session, post_ingest)
            # record_created/record_updated based on result.action
        except Exception:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "failure")
            self.record_failed()
```

**Replace with**:
```python
async def execute(self, session: AsyncSession) -> None:
    for item in source_items:
        try:
            result = await upsert_cve(session, cve_id, self.cve_source_type, payload)
            await upsert_references(session, ...)
            post_ingest = build_post_ingest_tasks(result, payload)
            await self.commit_and_dispatch(session, post_ingest)
            # record_created/record_updated based on result.action
        except (SoftTimeLimitExceeded, MemoryError):
            raise  # whole-run signals — never catch per-item
        except Exception:
            await session.rollback()
            await self._isolated_status_commit(cve_id, "failure")
            self.record_failed()
```

#### 2b.3 `_isolated_status_commit()` helper (lines 323-336)

**No change needed**. This helper runs in an independent session after
rollback, performing a non-critical status write. Its `except Exception:`
(line 332) is a defensive catch for a fire-and-forget operation.

Technically, `SoftTimeLimitExceeded` CAN be raised inside the helper
(the signal is asynchronous and may arrive at any point in execution).
If this occurs, the helper's `except Exception:` swallows it. However,
this is acceptable because: (1) the timing window is negligibly small
(single fast DB write — microseconds), and (2) the hard time limit
(D1) guarantees process termination at `run_timeout` regardless of
whether the soft signal was honored. Adding `SoftTimeLimitExceeded`
exclusion to the helper's catch would add complexity for near-zero
practical benefit.

### Phase 2c: Individual fetcher specs

Each API-based fetcher spec that contains inline code with
`except Exception:` in a per-item processing loop MUST be updated to
add `except (SoftTimeLimitExceeded, MemoryError): raise` before the
existing `except Exception:`.

#### 2c.1 `cve-sync-nvd.md` (lines 155-165)

**Current text**:
```python
       try:
           result = await upsert_cve(session, cve_id, "nvd", payload)
           await upsert_references(session, ...)
           post_ingest = build_post_ingest_tasks(result, payload)
           await self.commit_and_dispatch(session, post_ingest)
           # record_created/record_updated based on result.action
       except Exception:
           await session.rollback()
           self.record_failed()
```

**Replace with**:
```python
       try:
           result = await upsert_cve(session, cve_id, "nvd", payload)
           await upsert_references(session, ...)
           post_ingest = build_post_ingest_tasks(result, payload)
           await self.commit_and_dispatch(session, post_ingest)
           # record_created/record_updated based on result.action
       except (SoftTimeLimitExceeded, MemoryError):
           raise  # whole-run signals — never catch per-item
       except Exception:
           await session.rollback()
           self.record_failed()
```

#### 2c.2 `cve-sync-redhat.md` (lines 270-281)

**Current text**:
```python
    async def execute(self, session: AsyncSession) -> None:
        """Periodic batch: iterate over CVEs with active tickets."""
        for cve_id in active_ticket_cve_ids:
            try:
                post_ingest = await self.fetch_single(cve_id, session)
                await self.commit_and_dispatch(session, post_ingest)
            except CVENotInSource:
                await session.rollback()  # defensive: ensure clean session state
            except Exception:
                await session.rollback()
                self.record_failed()
            await asyncio.sleep(self.config.request_delay)
```

**Replace with**:
```python
    async def execute(self, session: AsyncSession) -> None:
        """Periodic batch: iterate over CVEs with active tickets."""
        for cve_id in active_ticket_cve_ids:
            try:
                post_ingest = await self.fetch_single(cve_id, session)
                await self.commit_and_dispatch(session, post_ingest)
            except (SoftTimeLimitExceeded, MemoryError):
                raise  # whole-run signals — never catch per-item
            except CVENotInSource:
                await session.rollback()  # defensive: ensure clean session state
            except Exception:
                await session.rollback()
                self.record_failed()
            await asyncio.sleep(self.config.request_delay)
```

#### 2c.3 `cve-sync-ghsa.md`

**No change needed**. This spec uses narrative text (lines 168-171,
507-517) that references the canonical "Batch Error Handling" section
in `cve-fetcher-infrastructure.md`. It does not contain inline code
with `except Exception:`. The fix in Phase 2b propagates automatically.

#### 2c.4 `cve-sync-epss.md` (lines 228-259)

**Current text** (primary loop):
```python
    async def execute(self, session: AsyncSession) -> None:
        """Periodic batch: iterate over CVEs with active tickets."""
        cve_ids = await self._get_active_ticket_cve_ids(session)
        staleness_checked = False
        consecutive_failures = 0
        for cve_id in cve_ids:
            try:
                post_ingest = await self.fetch_single(cve_id, session)
                await self.commit_and_dispatch(session, post_ingest)
                consecutive_failures = 0
            except CVENotInSource:
                await session.rollback()
                consecutive_failures = 0  # clean skip resets counter
            except ValidationError:
                await session.rollback()
                self.record_failed()
                consecutive_failures = 0  # data-quality, not infra
            except Exception:
                await session.rollback()
                self.record_failed()
                consecutive_failures += 1
            if consecutive_failures >= 3:
                raise FetcherError(
                    "EPSS API unreachable — 3 consecutive failures"
                )
            # Staleness check: diagnostic only, never affects metrics or abort
            if not staleness_checked and self._last_assessed_date is not None:
                try:
                    self._check_staleness(self._last_assessed_date)
                except Exception:
                    pass  # staleness is purely diagnostic
                staleness_checked = True
```

**Replace with**:
```python
    async def execute(self, session: AsyncSession) -> None:
        """Periodic batch: iterate over CVEs with active tickets."""
        cve_ids = await self._get_active_ticket_cve_ids(session)
        staleness_checked = False
        consecutive_failures = 0
        for cve_id in cve_ids:
            try:
                post_ingest = await self.fetch_single(cve_id, session)
                await self.commit_and_dispatch(session, post_ingest)
                consecutive_failures = 0
            except (SoftTimeLimitExceeded, MemoryError):
                raise  # whole-run signals — never catch per-item
            except CVENotInSource:
                await session.rollback()
                consecutive_failures = 0  # clean skip resets counter
            except ValidationError:
                await session.rollback()
                self.record_failed()
                consecutive_failures = 0  # data-quality, not infra
            except Exception:
                await session.rollback()
                self.record_failed()
                consecutive_failures += 1
            if consecutive_failures >= 3:
                raise FetcherError(
                    "EPSS API unreachable — 3 consecutive failures"
                )
            # Staleness check: diagnostic only, never affects metrics or abort
            if not staleness_checked and self._last_assessed_date is not None:
                try:
                    self._check_staleness(self._last_assessed_date)
                except Exception:
                    pass  # staleness is purely diagnostic
                staleness_checked = True
```

**Staleness check block**: the `except Exception:` in the staleness
check (lines 255-258) is NOT subject to `SoftTimeLimitExceeded`
exclusion. The same reasoning as `_isolated_status_commit()` applies
(see Phase 2b.3) — the timing window is negligibly small (single
in-memory date comparison, no I/O), and the hard time limit guarantees
process termination at `run_timeout` regardless.

#### 2c.5 `cve-sync-osv.md` (lines 308-327)

**Current text**:
```python
    async def execute(self, session: AsyncSession) -> None:
        """Periodic batch: iterate over CVEs with active tickets."""
        consecutive_failures = 0
        for cve_id in active_ticket_cve_ids:
            try:
                post_ingest = await self.fetch_single(cve_id, session)
                await self.commit_and_dispatch(session, post_ingest)
                consecutive_failures = 0
            except CVENotInSource:
                await session.rollback()  # defensive: ensure clean session state
                consecutive_failures = 0  # clean skip, not failure
            except Exception:
                await session.rollback()
                self.record_failed()
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    raise FetcherError(
                        "OSV API unreachable — 3 consecutive failures"
                    )
            await asyncio.sleep(self.config.request_delay)
```

**Replace with**:
```python
    async def execute(self, session: AsyncSession) -> None:
        """Periodic batch: iterate over CVEs with active tickets."""
        consecutive_failures = 0
        for cve_id in active_ticket_cve_ids:
            try:
                post_ingest = await self.fetch_single(cve_id, session)
                await self.commit_and_dispatch(session, post_ingest)
                consecutive_failures = 0
            except (SoftTimeLimitExceeded, MemoryError):
                raise  # whole-run signals — never catch per-item
            except CVENotInSource:
                await session.rollback()  # defensive: ensure clean session state
                consecutive_failures = 0  # clean skip, not failure
            except Exception:
                await session.rollback()
                self.record_failed()
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    raise FetcherError(
                        "OSV API unreachable — 3 consecutive failures"
                    )
            await asyncio.sleep(self.config.request_delay)
```

#### 2c.6 `cve-sync-kev.md` (lines 101-115)

**Current text** (per-entry isolation note, lines 110-115):
```
**Per-entry isolation**: each entry (steps 3a–3f) is processed
independently with per-entry error isolation. A failure at entry N does
not affect entries 1..N-1. Each entry operates in its own transaction
boundary. `upsert_cve()` acquires a `FOR UPDATE` lock on the CVE row;
the lock is held until the caller commits via `commit_and_dispatch()`
after all writes for that entry complete.
```

**Replace with**:
```
**Per-entry isolation**: each entry (steps 3a–3f) is processed
independently with per-entry error isolation. A failure at entry N does
not affect entries 1..N-1. Each entry operates in its own transaction
boundary. `upsert_cve()` acquires a `FOR UPDATE` lock on the CVE row;
the lock is held until the caller commits via `commit_and_dispatch()`
after all writes for that entry complete. `SoftTimeLimitExceeded` and
`MemoryError` are NOT subject to per-entry isolation — they are
whole-run signals that must propagate immediately (see
"`SoftTimeLimitExceeded` handling convention" in
`fetcher-infrastructure.md`).
```

**Git-based fetchers** (`cve-sync-mitre.md`, `cve-sync-kernel.md`):
no changes needed — they inherit the fix from Phase 2 (BaseGitFetcher
step 10d).

**IBS fetchers** (`ibs-track-release-detection.md`,
`ibs-product-release-detection.md`): currently disabled and describe
error handling narratively without code. Will be aligned when
enabled/implemented. No action in this draft.

### Phase 3: Cross-cutting documentation

#### 3.1 `docs/data-model.md` — FetcherConfig column (line 1413)

**Current text**:
```
| run_timeout   | INTEGER     | NOT NULL, DEFAULT 3600 | Max execution time in seconds. Also used as stale run detection threshold. 0 disables both. |
```

**Replace with**:
```
| run_timeout   | INTEGER     | NOT NULL, DEFAULT 3600 | Max execution time in seconds (hard ceiling). Also used to derive the soft time limit (×0.95) and stale detection threshold (+60s). 0 disables all time limits and stale detection. |
```

#### 3.2 `docs/features/platform/fetcher-operations.md`

**3.2.1 Validation rules (lines 595-597)**

**Current text**:
```
- `run_timeout`: must be a non-negative integer. 0 disables both
  the Celery soft time limit and stale run detection (stuck runs will
  require manual resolution via the CLI). Default: 3600 (1 hour)
```

**Replace with**:
```
- `run_timeout`: must be a non-negative integer. 0 disables all
  Celery time limits (soft and hard) and stale run detection (stuck
  runs will never be forcibly terminated and will require manual
  resolution via the CLI). Default: 3600 (1 hour)
```

**3.2.2 Status column logic (lines 814-816)**

**Current text**:
```
   `started_at`. If `run_timeout > 0` and the elapsed time exceeds
   it, append `(stale?)` — e.g., `running (2h 30m elapsed, stale?)`.
   If `run_timeout = 0`, the `(stale?)` hint is never shown.
```

**Replace with**:
```
   `started_at`. If `run_timeout > 0` and the elapsed time exceeds
   `run_timeout + 60` (the stale threshold), append `(stale?)` —
   e.g., `running (1h 2m elapsed, stale?)`. This indicates the
   process was terminated by the hard limit and the orphaned record
   has not yet been cleaned up by stale detection.
   If `run_timeout = 0`, the `(stale?)` hint is never shown.
```

**3.2.3 CLI warning (line 911-914)**

**Current text**:
```
When `run_timeout` is 0, the command MUST emit a warning to stderr:

```
Warning: Stale detection disabled — stuck runs will require manual resolution.
```
```

**Replace with**:
```
When `run_timeout` is 0, the command MUST emit a warning to stderr:

```
Warning: Execution timeout disabled — runs will never be forcibly terminated and stuck runs will require manual resolution.
```
```

#### 3.3 `docs/features/platform/networking.md` — timeout hierarchy diagram (lines 122-123)

**Current text**:
```
│ FetcherConfig.run_timeout (default: 3600s)          │  ← Celery task level
│ Detects stale runs (worker crashed, deadlock)       │     (per entire run)
```

**Replace with**:
```
│ FetcherConfig.run_timeout (default: 3600s)          │  ← Celery task level
│ Hard ceiling: task killed at this limit             │     (per entire run)
```

No change needed for lines 172-174 (`SoftTimeLimitExceeded` reference
in Shutdown section) — the soft limit still exists at 95% of
`run_timeout` and the described behavior (asyncio.sleep cancellation)
remains correct.

### Phase 3b: Fetcher compliance reviewer agent

#### 3b.1 Error handling section (`.opencode/agents/fetcher-compliance-reviewer.md`, lines 79-86)

**Current text**:
```
### Error handling

- Does the `execute()` method let exceptions propagate naturally (so
  `BaseFetcher.run()` can catch them and record the failure)?
- Are there broad `except` clauses that swallow exceptions without
  re-raising? This would prevent the dashboard from showing failures.
- Is partial failure handled correctly? If some items fail but the fetcher
  continues, are failed items reported via `self.record_failed()`?
```

**Replace with**:
```
### Error handling

- Does the `execute()` method let exceptions propagate naturally (so
  `BaseFetcher.run()` can catch them and record the failure)?
- Are there broad `except` clauses that swallow exceptions without
  re-raising? This would prevent the dashboard from showing failures.
- **`SoftTimeLimitExceeded` exclusion**: do per-item `except Exception:`
  blocks in the `execute()` loop explicitly exclude
  `SoftTimeLimitExceeded` and `MemoryError` with a preceding
  `except (SoftTimeLimitExceeded, MemoryError): raise`? Catching these
  exceptions per-item silently defeats the timeout mechanism — the soft
  time limit signal is delivered once and, once consumed, is never
  re-raised. This is a "Needs revision" issue. See
  "`SoftTimeLimitExceeded` handling convention" in
  `fetcher-infrastructure.md`.
- **Exception**: fire-and-forget helper blocks with negligible timing
  windows (e.g., `_isolated_status_commit()`, diagnostic checks) are
  exempt — the hard time limit provides the backstop for these cases.
- Is partial failure handled correctly? If some items fail but the fetcher
  continues, are failed items reported via `self.record_failed()`?
```

#### 3b.2 `.opencode/README.md`

Find the `@fetcher-compliance-reviewer` row in the agents table.

**Current text**:
```
| `@fetcher-compliance-reviewer` | Reviewer | Guardrail 14 | Verifies fetchers inherit from BaseFetcher (or BaseCVEFetcher for CVE fetchers) and report metrics correctly |
```

**Replace with**:
```
| `@fetcher-compliance-reviewer` | Reviewer | Guardrail 14 | Verifies fetchers inherit from BaseFetcher (or BaseCVEFetcher for CVE fetchers), report metrics correctly, and exclude `SoftTimeLimitExceeded` from per-item catches |
```

## Impact on Existing Findings

### GFI-GAP-01 — Resolved by this draft

The finding "Large recovery/initial delta cannot converge within the task
window" is fully addressed by:
- D5: `SoftTimeLimitExceeded` no longer swallowed → timeout works
- D1: hard limit guarantees termination
- Section 2.4: operational note explains convergence through successive
  runs and manual `run_timeout` override

The original finding's proposed solution (max_items_per_run, checkpointing,
processed_paths) is replaced by a simpler approach: let the timeout work
correctly, rely on idempotent reprocessing for convergence, and provide
admin tooling for manual override.

### Potential new findings created

This change may affect other findings in `git-fetcher-infrastructure.md`:
- **GFI-GAP-07** (concurrency during delete-and-re-clone): partially
  mitigated by the hard limit guaranteeing task death before stale
  detection starts a new run — but the TOCTOU race between `fetch_single()`
  and periodic recovery remains
- **GFI-GAP-02** (partial runs advance cursor, abandoning failed items):
  not affected — this draft addresses timeout behavior, not cursor
  advancement on partial success

## Pre-Application Checklist

All items investigated and resolved:

- [x] Check if `cve-fetcher-infrastructure.md` has per-item loops —
  **YES**: two canonical patterns + individual specs. Covered in Phase
  2b and 2c
- [x] Verify no other spec references `run_timeout` as "soft time limit"
  — found in `fetcher-operations.md` (line 596) and `data-model.md`
  (line 1413). Covered in Phase 3.1 and 3.2
- [x] Verify `fetcher-operations.md` mentions of timeout semantics —
  3 locations need update (lines 595-596, 814-816, 911). Covered in
  Phase 3.2
- [x] Verify `data-model.md` FetcherConfig documentation — line 1413
  needs update. Covered in Phase 3.1
- [x] Check if `networking.md` references need updating — line 122-123
  needs update (diagram label). Lines 172-174 (SoftTimeLimitExceeded
  in Shutdown) are correct as-is. Covered in Phase 3.3
- [x] Determine exact stale threshold margin — confirmed 60s fixed.
  Sufficient for SIGKILL + NTP clock skew. Proportional margin
  rejected (unnecessary complexity)
- [x] Review soft limit floor formula — simplified to
  `floor(run_timeout × 0.95)` with no special cases. Original
  formula had a bug (`max()` instead of `min()`) and was
  over-engineered for improbable scenarios

## Post-Application Steps

### Phase 4: Verification

After all modifications from Phase 1, 2, 2b, 2c, 3, and 3b have been
applied to the specs, run the following reviewers to verify correctness:

**4.1 `spec-coherence-reviewer`** — run on each of:
- `fetcher-infrastructure.md` (verifies cross-references with
  git-fetcher-infrastructure, cve-fetcher-infrastructure,
  fetcher-operations, networking are consistent)
- `git-fetcher-infrastructure.md` (verifies cross-references with
  fetcher-infrastructure remain coherent after step 10d change)
- `cve-fetcher-infrastructure.md` (verifies canonical patterns are
  consistent with fetcher-infrastructure convention and individual
  fetcher specs)

**4.2 `spec-gap-analyzer`** — run on each of:
- `fetcher-infrastructure.md` (new 3-layer timeout behavior —
  soft/hard/stale — may have edge cases not covered by the spec)
- `git-fetcher-infrastructure.md` (modified execute() template with
  new step 10d/10e split, timeout enrichment section)

**4.3 `docs-placement-reviewer`** — run on:
- `fetcher-infrastructure.md` (new "`SoftTimeLimitExceeded` handling
  convention" subsection — verify it belongs here and does not
  duplicate or contradict other documentation)

**4.4 Mark finding GFI-GAP-01 as RESOLVED** in
`docs/reviews/git-fetcher-infrastructure.md` with resolution:
"Resolved via fetcher-timeout-architecture draft — SoftTimeLimitExceeded
exclusion from per-item catch + hard time limit backstop + operational
convergence note." Update `.tracking.json` cache and
`docs/reviews/README.md` accordingly.

**Not reviewed individually**: fetcher specs modified in Phase 2c (NVD,
Red Hat, EPSS, OSV, KEV). The changes are mechanical (2-line re-raise
addition) and their correctness is guaranteed by alignment with the
canonical patterns verified in Phase 2b.

### Phase 5: Cleanup

After all reviewers confirm no critical issues:

- Delete `docs/drafts/fetcher-timeout-architecture.md`

## Open Questions

None at this time. All design decisions have been agreed upon during
the analysis session.

## Future Consideration: Template Method for API-based Fetchers

The current approach (Strada A) relies on a documented convention: every
fetcher author must remember to exclude `SoftTimeLimitExceeded` from
per-item catches. This is enforced through canonical patterns in
`cve-fetcher-infrastructure.md` and verified by the
`@fetcher-compliance-reviewer` agent.

A future evolution (Strada B) would move the per-item exception handling
into a template method in `BaseCVEFetcher`, making the exclusion
transparent to fetcher authors — similar to how `BaseGitFetcher` already
encapsulates it in step 10d. This would require analyzing whether
API-based fetcher iteration patterns (pagination, batch, single-item)
are uniform enough to generalize into a template. This evaluation is
deferred to a separate analysis session.
