# Draft: Celery Beat Schedule Synchronization Specification

**Status**: Draft — pending review before application to specs
**Date**: 2026-07-05
**Scope**: New section in `fetcher-infrastructure.md` + minor coherence
updates to 3 other documents

---

## Motivation

The fetcher infrastructure specs define that Celery Beat uses
`celery-redbeat` as a dynamic scheduler and that PostgreSQL
(`FetcherConfig.schedule_override` or `BaseFetcher.default_schedule`) is
the source of truth for schedules. However, the **synchronization
mechanism** between PostgreSQL and redbeat is unspecified — an
implementer would need to make autonomous design decisions on critical
architectural aspects (startup behavior, failure recovery, propagation
semantics, concurrency).

This draft specifies the complete synchronization mechanism and provides
a prescriptive action plan for applying the changes to the existing specs.

---

## Design Decisions (Resolved)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| OP-1 | Beat startup with PostgreSQL unreachable | Fail-fast (refuse to start) | Orchestrator restarts Beat until PostgreSQL recovers. Prevents silent operation with stale schedules. |
| OP-2 | Reconciliation frequency | Startup-only (no periodic) | During operation, only the PATCH endpoint modifies redbeat. Periodic reconciliation adds complexity for a failure mode (schedule drift) that cannot occur without Redis flush or direct manipulation — both extraordinary operational events. A Redis flush requires manual Beat restart for recovery (see Redis Flush Recovery); periodic reconciliation would auto-heal this case but at the cost of continuous overhead for a rare scenario. |
| OP-3 | `next_run_at` when Redis unreachable | `null` with WARNING log, endpoint returns 200 | Rest of the response (metadata, last_run) is still valid from PostgreSQL. A 503 would make the dashboard inaccessible for a Redis blip. |

---

## Open Points (Pending Resolution)

The following points were identified by the spec-coherence-reviewer and
spec-gap-analyzer. Each requires a design decision before the draft can
be applied.

| # | Summary | Severity | Options |
|---|---------|----------|---------|
| OP-4 | Beat startup with empty `FetcherConfig` (fresh deployment) | High | (a) Beat treats missing record as "enabled with `default_schedule`" and writes a redbeat entry using code defaults — self-heals when worker creates the DB record later. (b) Beat skips fetchers without a `FetcherConfig` record and relies on a restart after workers initialize. (c) Beat creates `FetcherConfig` records itself (breaks worker-only-writes invariant). |
| OP-5 | Step 4 (deregistered cleanup) should filter by task name | Medium | (a) Only process entries whose `task` field is `"run_fetcher"` — future non-fetcher periodic entries are left untouched. (b) Keep current behavior (process all entries) and document that no non-fetcher entries may exist in redbeat. |
| OP-6 | `due_at` behavior when entries are overwritten during reconciliation | Medium | (a) `due_at` is recalculated fresh from the cron schedule (overdue runs during Beat downtime are NOT retroactively triggered). (b) If an existing entry's `due_at` is in the past, preserve it so Beat fires immediately on next tick (catch-up for missed runs). (c) Delegate to redbeat's native behavior (document which it is). |
| OP-7 | Invalid cron expression encountered during reconciliation | Medium | (a) Skip the invalid entry, log WARNING, continue with remaining fetchers (resilient). (b) Fail-fast for the single entry, mark the fetcher as needing operator intervention. (c) Fall back to `default_schedule` if `schedule_override` is unparseable. |
| OP-8 | Manual trigger `apply_async()` missing time limits | Minor | (a) Add a Step to update `fetcher-operations.md` trigger endpoint (lines 473-475) to pass `time_limit`/`soft_time_limit` in `apply_async()` options — aligns with the "MUST be passed per-invocation" statement. (b) Consider this out-of-scope for the Beat sync draft (pre-existing gap) and track separately. |

### OP-4: Beat Startup with Empty FetcherConfig

**Context**: `FetcherConfig` records are created exclusively by workers
on startup (`INSERT ... ON CONFLICT DO NOTHING`). In a fresh deployment
or parallel Kubernetes restart, Beat may start before any worker,
finding zero records in `FetcherConfig`. Reconciliation step 2 requires
`enabled = true` in `FetcherConfig` — condition unsatisfiable → empty
schedule → no fetchers run until Beat is restarted.

**Impact**: on first deployment, hours of missed data ingestion if Beat
is not manually restarted after workers have initialized.

**Considerations**:
- Option (a) means Beat can always produce a working schedule from the
  code registry alone. The eventual `FetcherConfig` creation by the
  worker (with identical defaults) causes no divergence. If an admin
  changes config via PATCH before the worker creates the record, the
  PATCH would 404 (no `FetcherConfig` exists) — this is already the
  documented behavior.
- Option (b) is simpler (no new logic) but requires operators to ensure
  startup ordering or accept a Beat restart.
- Option (c) violates the clean separation documented in "Who Writes
  to Redbeat" and "Multi-Process Coordination".

### OP-5: Step 4 Task Name Filter

**Context**: the `redbeat::schedule` sorted set may contain entries
created by future non-fetcher periodic tasks. Current step 4 processes
ALL entries, attempting to extract `fetcher_name` from kwargs.

**Impact**: if a future non-fetcher entry lacks `fetcher_name` in
kwargs, step 4 would either crash (KeyError) or incorrectly evaluate
the entry against the registry.

### OP-6: `due_at` on Reconciliation Overwrite

**Context**: when Beat restarts after a crash, overwritten entries may
have had a `due_at` in the past (i.e., the task was supposed to fire
during downtime). The choice between "recalculate" and "preserve"
determines whether missed runs are fired immediately or skipped.

**Impact**: determines operator expectations about data freshness after
Beat recovery. Both approaches are valid — "recalculate" avoids
thundering-herd (all fetchers fire simultaneously after recovery),
"preserve" avoids data gaps.

### OP-7: Invalid Cron During Reconciliation

**Context**: an operator or migration may corrupt `schedule_override`
in PostgreSQL (bypassing PATCH validation). One invalid record should
not prevent all other fetchers from being scheduled.

**Impact**: fail-fast means a single corrupted row blocks the entire
system. Skip-and-continue means the corrupted fetcher is silently
unscheduled (only visible via dashboard or logs).

### OP-8: Manual Trigger Time Limits

**Context**: the spec states "limits MUST be passed per-invocation via
`apply_async()` options." This is fully specified for Beat-scheduled
runs (via redbeat Options). For manual triggers (via the API trigger
endpoint), `fetcher-operations.md` (lines 473-475) shows
`apply_async()` without time limit options. This is a pre-existing gap
that becomes a visible contradiction after this draft is applied.

**Impact**: manual-triggered fetchers would run without Celery-level
time limits (only stale run detection provides a safety net). The
`run_timeout` enforcement documented in `FetcherConfig` implies ALL
invocations are bounded.

---

## Specification Content

The following is the complete text of the new section to be inserted in
`fetcher-infrastructure.md`.

### — BEGIN SECTION TEXT —

## Celery Beat Schedule Synchronization

PostgreSQL (`FetcherConfig`) is the source of truth for fetcher schedules.
Redis (redbeat) is the execution layer — it holds the entries that Celery
Beat actually fires. This section specifies the synchronization mechanism
that ensures redbeat always reflects the state defined in PostgreSQL.

### Architecture: PostgreSQL-master, Redbeat-slave

The synchronization follows a strict one-directional pattern:

```
PostgreSQL (FetcherConfig + FETCHER_REGISTRY)
         │
         ▼ writes (never reads from redbeat to update PostgreSQL)
Redis (redbeat entries)
```

- **PostgreSQL** owns the schedule definition:
  `FetcherConfig.schedule_override` (if set) or
  `BaseFetcher.default_schedule` (fallback from the code registry)
- **Redbeat** is a derived cache that can be reconstructed entirely from
  PostgreSQL + the in-memory `FETCHER_REGISTRY`
- The system MUST never read redbeat entries to determine what the
  "correct" schedule is — only PostgreSQL is authoritative

### Redbeat Configuration

Redbeat uses the following configuration:

| Setting | Value | Source |
|---------|-------|--------|
| `redbeat_redis_url` | Not configured explicitly | Defaults to `CELERY_BROKER_URL` (redbeat's standard behavior). A separate variable is unnecessary because Sentinel already configures the Celery broker as Redis. |
| `redbeat_key_prefix` | `redbeat:` | Default. All redbeat entries are stored under this prefix. |
| Scheduler class | `redbeat.RedBeatScheduler` | Configured in the Celery app settings (`beat_scheduler`), not via CLI flag. |

No separate environment variable for `redbeat_redis_url` is required or
supported. If a deployment uses a different Redis instance for the broker
vs. application cache (split deployment per `docs/configuration.md`),
redbeat follows the broker instance — which is correct, since redbeat is
part of the Celery subsystem.

### Redbeat Entry Structure

Each registered and enabled fetcher has one redbeat entry:

| Property | Value |
|----------|-------|
| Entry key | `redbeat:{fetcher_name}` (e.g., `redbeat:sync_nvd_cves`) — this is the Redis key (prefix + name). The redbeat API's `from_key()` accepts this value. |
| Task | `run_fetcher` |
| Schedule | Cron from effective schedule (override or default) |
| Args | `[]` |
| Kwargs | `{"fetcher_name": "<name>", "triggered_by": "schedule"}` |
| Options | `{"time_limit": <run_timeout>, "soft_time_limit": <soft_limit>}` or `{}` if `run_timeout = 0` |
| Enabled | `true` (entry only exists when fetcher is enabled) |

### Time Limits: Stored in Redbeat Entry Options

Celery's `time_limit` and `soft_time_limit` are enforced by the worker
at task dispatch time — they cannot be applied from within the task after
execution begins. Since `run_fetcher` is a generic task shared by all
fetchers (each with a potentially different `run_timeout`), the limits
MUST be passed per-invocation via `apply_async()` options.

When Beat fires a scheduled task, it uses the `options` stored in the
redbeat entry. Therefore:

- If `FetcherConfig.run_timeout > 0`: the redbeat entry stores
  `{"time_limit": run_timeout, "soft_time_limit": max(1, floor(run_timeout * 0.95))}`
  in its Options field. Beat passes these to `apply_async()`, and the
  worker enforces them.
- If `FetcherConfig.run_timeout = 0`: the Options field is `{}` (no time
  limits). The task runs without a time ceiling.

This means that a PATCH to `run_timeout` requires a redbeat entry update
(see "Which Changes Require Redbeat Propagation" below). The change takes
effect on the next scheduled execution after the entry is updated.

**Soft time limit formula**: `max(1, floor(run_timeout * 0.95))` — same
formula defined in the `FetcherConfig` section (prevents Celery from
interpreting `soft_time_limit = 0` as "disabled" for very small
`run_timeout` values).

### Startup Reconciliation

When Celery Beat starts (or restarts after a crash), it performs a full
reconciliation of the redbeat schedule against the current system state.
This happens **before** Beat begins firing any tasks.

#### Startup Sequence

1. **Read state from PostgreSQL**: query all `FetcherConfig` records and
   load the `FETCHER_REGISTRY` (already populated by import-time
   auto-discovery). For each registered fetcher, compute the effective
   schedule (`schedule_override` if set, else `default_schedule`).

2. **Write entries for enabled registered fetchers**: for each fetcher
   that is (a) present in `FETCHER_REGISTRY` AND (b) has `enabled = true`
   in `FetcherConfig`:
   - Create or unconditionally overwrite the redbeat entry with the
     computed effective schedule and time limit options (derived from
     `run_timeout` per the formula in "Time Limits" above)
   - This is an idempotent upsert — existing entries are updated, missing
     entries are created

3. **Remove entries for disabled fetchers**: for each fetcher that is
   present in `FETCHER_REGISTRY` but has `enabled = false`:
   - Delete the redbeat entry if it exists
   - No-op if no entry exists

4. **Remove entries for deregistered fetchers**: enumerate all scheduled
   entry names from redbeat's internal schedule list (the `redbeat::schedule`
   sorted set, which contains only task entry names — not internal keys
   like `redbeat::lock`). For each entry whose `fetcher_name` (extracted
   from the entry's kwargs) is NOT present in `FETCHER_REGISTRY`:
   - Delete the entry
   - Log at INFO level: `"Removed redbeat entry for deregistered fetcher
     '%s'", fetcher_name`

5. **Log reconciliation summary**: after all entries are processed, log
   at INFO level:
   `"Beat schedule reconciliation complete: %d entries written, %d
   disabled removed, %d deregistered removed", written, disabled_removed,
   deregistered_removed`

6. **Begin normal Beat operation**: after reconciliation completes, Beat
   begins its normal tick loop (firing tasks per their schedules)

#### Startup Failure: PostgreSQL Unreachable

If PostgreSQL is unreachable during startup reconciliation:

- Beat MUST NOT start with stale redbeat entries. Using outdated schedules
  is dangerous: a disabled fetcher might still have an entry from before
  the disable, or a schedule change might not be reflected.
- Beat logs a CRITICAL error:
  `"CRITICAL: Celery Beat startup failed — cannot read FetcherConfig from
  PostgreSQL: {error}. Redbeat schedule not reconciled. Beat will not
  start."`
- Beat exits with a non-zero exit code
- The orchestrator (Docker/Kubernetes) will restart Beat according to its
  restart policy. On the next attempt, if PostgreSQL is reachable,
  reconciliation succeeds normally.

**Rationale**: Beat is a singleton process. If it starts with stale data,
there is no fallback mechanism to correct the schedule until the next
restart. Failing fast ensures that the system is either correct or stopped
— never silently wrong.

#### Startup: Redis (redbeat) Unreachable

This is equivalent to Beat being unable to start at all — Beat requires
Redis for its core operation (storing schedule state). Celery Beat's own
startup logic handles this: if the broker is unreachable, Beat cannot
initialize the scheduler and exits with an error. No special handling is
needed beyond Celery's built-in behavior.

### Runtime Propagation

When an admin modifies a fetcher's configuration via
`PATCH /api/v1/fetchers/{name}/config`, changes that affect the Beat
schedule are propagated to redbeat **synchronously** within the same HTTP
request.

#### Which Changes Require Redbeat Propagation

| Change | Propagation |
|--------|-------------|
| `schedule_override` changed (new value or set to null) | Update the redbeat entry's schedule with the new effective cron |
| `enabled` changed to `false` | Remove the redbeat entry |
| `enabled` changed to `true` | Create the redbeat entry with effective schedule and time limit options |
| `run_timeout` changed | Update the redbeat entry's Options (`time_limit`, `soft_time_limit`). If new value is 0, set Options to `{}`. |
| `request_delay` changed | No propagation needed (read from DB at execution time) |
| `custom_settings` changed | No propagation needed (read from DB at execution time) |

#### Propagation Mechanism

The PATCH endpoint handler:

1. Updates `FetcherConfig` in PostgreSQL (within a transaction)
2. Commits the PostgreSQL transaction
3. Propagates to redbeat (if any propagation-requiring field changed):
   - If `schedule_override` changed: update the redbeat entry's schedule
     with the new effective cron expression
   - If `run_timeout` changed: update the redbeat entry's Options with
     the new `time_limit` and `soft_time_limit` values (or clear Options
     if the new value is 0)
   - If `enabled` changed to `false`: delete the redbeat entry
   - If `enabled` changed to `true`: create the redbeat entry with the
     effective schedule and time limit options
   - Uses the `redbeat.RedBeatSchedulerEntry` API to write/delete the
     entry
   - If multiple propagation-requiring fields changed in the same PATCH,
     a single redbeat write reflects all changes atomically (one
     entry upsert)

The PostgreSQL commit happens BEFORE the redbeat write. This ensures that
even if the redbeat write fails, the source of truth (PostgreSQL) is
correct, and the system self-heals at the next Beat restart.

#### Propagation Failure: Redis Unreachable

If the redbeat write fails (Redis unreachable, timeout, or write error):

1. The PostgreSQL change is ALREADY committed (the source of truth is
   updated)
2. The PATCH endpoint returns **200 OK** to the caller (the configuration
   change was saved successfully)
3. A WARNING-level log is emitted:
   `"WARNING: FetcherConfig for '%s' updated in PostgreSQL but redbeat
   propagation failed: %s. Schedule will be corrected at next Beat
   restart.", fetcher_name, error`
4. **No retry mechanism** — the reconciliation at next Beat startup will
   correct the redbeat state

**Rationale**: the alternative (rolling back the PostgreSQL change on
Redis failure) would make the configuration system fragile — a brief
Redis blip would prevent all fetcher configuration changes. The eventual
consistency model is safe because:

- If a schedule change failed to propagate: the fetcher runs on the old
  schedule until Beat restarts. This is a minor timing deviation, not
  data corruption.
- If a `run_timeout` change failed to propagate: the fetcher runs with
  the old time limits until Beat restarts. If the new limit is shorter
  (admin reduced it), the old limit is still a valid ceiling. If the new
  limit is longer (admin increased it), the task might time out
  prematurely once — recoverable on the next scheduled run after Beat
  restart.
- If an enable→disable failed to propagate: the fetcher's `run()` method
  checks `FetcherConfig.enabled` at execution time. Even if Beat fires
  the task, `run()` skips it (the enabled check is the safety net
  documented in the "Enabled check" section). The next Beat restart
  removes the entry.
- If a disable→enable failed to propagate: the fetcher simply doesn't
  run until Beat restarts. No data corruption.

#### Enable/Disable: Entry Lifecycle

| Action | Effect on redbeat |
|--------|-------------------|
| `enabled` → `false` | **Remove** the redbeat entry entirely. This prevents Beat from firing the task at all (no log noise, no wasted task dispatch). The `enabled` check in `BaseFetcher.run()` is a safety net for the race window between disable and an already-enqueued task. |
| `enabled` → `true` | **Create** a new redbeat entry with the effective schedule. The entry is immediately active on the next Beat tick. |
| Fetcher disabled at startup | Entry is NOT created during reconciliation (step 3 removes it if it exists from a previous state) |

### `next_run_at` Calculation

The `next_run_at` field in the `GET /api/v1/fetchers` response is
calculated by the API endpoint at request time:

1. For each registered and enabled fetcher: read the redbeat entry's
   `due_at` attribute (the timestamp of the next scheduled execution,
   maintained by redbeat automatically)
2. Access pattern: the API endpoint uses the
   `redbeat.RedBeatSchedulerEntry.from_key()` method to read the entry.
   This is a single Redis GET per fetcher — no key scan.
3. If the entry does not exist in Redis (Beat not started, Redis flushed,
   or entry lost): `next_run_at = null`
4. If the fetcher is disabled: `next_run_at = null` (no entry exists —
   see "Enable/Disable: Entry Lifecycle")
5. If the fetcher is deregistered: `next_run_at = null` (no entry exists)

**Disambiguation**: both "Beat not started" and "fetcher disabled" produce
`null`. This is intentional — both cases mean "this fetcher will not run
on a schedule." The API consumer does not need to distinguish these cases:
a disabled fetcher's `enabled` field is `false`, which provides the
distinction for UI display purposes.

**API endpoint failure handling**: if Redis is unreachable when calculating
`next_run_at` for the fetcher list:

- Individual fetcher `next_run_at` values are set to `null`
- The endpoint does NOT return an error — the rest of the response
  (fetcher metadata, last_run, etc.) is still valid from PostgreSQL
- A WARNING-level log is emitted:
  `"WARNING: Cannot read redbeat schedule state from Redis: %s.
  next_run_at will be null for all fetchers.", error`

### Reconciliation and Divergence Recovery

#### Reconciliation is Startup-Only

Reconciliation (full overwrite of redbeat from PostgreSQL) occurs
exclusively at Beat startup. There is no periodic reconciliation task
during normal operation.

**Rationale**: during normal operation, the only legitimate source of
redbeat changes is the PATCH endpoint (which updates both PostgreSQL and
redbeat). A periodic reconciliation would add complexity (timing, locking,
performance impact of scanning all entries) for a failure mode (drift
during normal operation) that cannot occur without either:

- An external actor directly modifying Redis (operator error) — handled
  by restart-based reconciliation (entry is silently overwritten)
- A Redis flush — requires manual Beat restart for recovery (see "Redis
  Flush Recovery" below)
- A Redis failure during PATCH propagation — self-heals at next restart

All three are extraordinary operational events where the minor
inconvenience of a manual restart is preferable to the continuous
overhead of periodic reconciliation.

#### Redis Flush Recovery

If Redis is flushed (all keys lost, including redbeat entries):

1. All fetcher schedules stop firing immediately (entries are gone)
2. Beat continues running but with an empty schedule — it does not
   crash (Redis is still reachable, there is simply nothing to fire)
3. **Detection**: the admin observes the anomaly in the fetcher
   dashboard — all `next_run_at` values are `null` and `last_run`
   timestamps grow stale
4. **Recovery**: restart the Beat process (kill the container or
   restart the service). On startup, the full reconciliation recreates
   all entries from PostgreSQL

There is no automatic self-healing for this scenario without a Beat
restart. This is acceptable because a Redis flush is an extraordinary
operational event (not a transient failure), and the dashboard provides
clear visibility into the anomaly.

**Operational note**: a Redis flush also affects `REDIS_URL` data
(session liveness cache, login lockout counters, deduplication locks,
distributed locks). Specific impacts:

- **Session liveness cache**: no functional impact — sessions are stored
  in PostgreSQL. Redis cache misses cause a temporary increase in
  database load (~60 seconds while caches warm up). No users are logged
  out.
- **Deduplication locks** (`fetch_pending:*`): tasks already enqueued
  may be duplicated if re-triggered before execution. This is a minor
  efficiency concern — the tasks are idempotent (upsert semantics).
- **Login lockout counters**: brute-force rate limiting resets
  temporarily.
- **`/ready` endpoint**: continues to return 200 (Redis is reachable).
  The flush is not detectable via health checks — only via the dashboard
  anomaly (stale `last_run` timestamps, `null` `next_run_at`).

#### Direct Redis Manipulation

Modifying redbeat entries directly in Redis (via `redis-cli`, RedisInsight,
or any path that bypasses the API) is **undefined behavior**:

- The change will be effective immediately (Beat reads entries from Redis)
- The change will be **silently overwritten** at the next Beat restart
  (startup reconciliation unconditionally overwrites from PostgreSQL)
- No error, no warning, no audit trail
- PostgreSQL remains unchanged — the API will show the "old" schedule
  until the admin changes it via PATCH

The spec does not attempt to detect or prevent direct Redis manipulation.
The self-healing nature of startup reconciliation makes this safe (no
permanent damage), though operationally confusing if done intentionally.

### Multi-Process Coordination

#### Who Writes to Redbeat

Only **two** components write to redbeat:

1. **Celery Beat process** (singleton): writes during startup
   reconciliation
2. **API server process** (potentially multiple replicas): writes during
   PATCH endpoint handling (runtime propagation)

**Celery workers** do NOT write to redbeat. Workers only:

- Auto-create `FetcherConfig` records in PostgreSQL on startup
  (`INSERT ON CONFLICT DO NOTHING`)
- Read `FetcherConfig` during task execution

#### Concurrency Between Beat and API

Beat startup reconciliation and API PATCH propagation can theoretically
race (Beat is restarting while an admin is changing a schedule). This is
safe because:

1. Both operations write the same authoritative source (PostgreSQL state)
   to redbeat
2. The worst case is a momentary stale overwrite: Beat writes the
   schedule from the pre-PATCH PostgreSQL state, then the PATCH writes
   the updated schedule (or vice versa). Since both read from PostgreSQL
   (which is serialized by row-level locking), the final state is always
   correct — the last writer wins, and both writers use the committed
   PostgreSQL state at their point in time
3. If Beat reconciliation reads the pre-PATCH state and writes it AFTER
   the PATCH has already written the post-PATCH state to redbeat: the
   entry is momentarily stale. The PATCH's redbeat write was
   "undone" by Beat's reconciliation. This is acceptable because it
   can only happen during the narrow window of Beat startup + concurrent
   PATCH, and the entry reflects a valid (though stale by one change)
   PostgreSQL state. The stale entry persists until the admin re-issues
   the PATCH or Beat restarts again. This is an operationally negligible
   scenario (Beat startup takes < 1 second; a concurrent PATCH during
   that exact window is rare). If it occurs, the admin observes the
   old schedule in the API (since `next_run_at` is calculated from the
   redbeat entry) and can re-issue the PATCH
4. No locking between Beat startup and API writes is required

#### Multiple API Replicas

Multiple API replicas can issue concurrent PATCH requests for different
fetchers without coordination (they write different redbeat keys). For
concurrent PATCH requests on the **same** fetcher:

- PostgreSQL serializes the `FetcherConfig` updates (standard row-level
  locking)
- The redbeat write for the same entry is a simple key SET — the last
  writer wins, which is correct since it reflects the latest committed
  PostgreSQL state

#### Redbeat Distributed Lock

Redbeat uses its own distributed lock (`redbeat::lock`) to ensure only
one Beat process is active at a time. This is a redbeat-internal mechanism
— Sentinel does not need to manage it. If a second Beat process starts,
redbeat's lock prevents it from taking over until the first one dies or
releases the lock.

### Startup Validation

The existing Celery startup validation (`CELERY_TIMEZONE = UTC`,
`CELERY_ENABLE_UTC = True` — see `docs/configuration.md`) is performed
by workers. Beat inherits the same validation (it loads the same Celery
app configuration).

Additionally, the Beat startup reconciliation implicitly validates:

- PostgreSQL connectivity (reads `FetcherConfig`)
- Redis/redbeat connectivity (writes entries)
- `FETCHER_REGISTRY` population (imports all fetcher modules)

If any of these fail, Beat does not start (see "Startup Failure"
sections above).

### — END SECTION TEXT —

---

## Action Plan

### Step 1: Insert new section in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: after line 1360 (end of the "Result handling" paragraph in
"Celery Integration"), before line 1362 (start of "## Concurrency
Control")
**Action**: insert the entire section text above (from `## Celery Beat
Schedule Synchronization` through to the end of `### Startup
Validation`)

### Step 1a: Fix existing "Celery Integration" paragraph

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: lines 1340-1344 (paragraph about Beat schedule in "Celery
Integration")
**Current text**:

```
The Celery Beat schedule is built dynamically from the registry at worker
startup, using each fetcher's effective schedule (config override or
default). When an admin modifies a fetcher's schedule via the API, the
Beat schedule MUST be updated accordingly (using `celery-redbeat` or
equivalent dynamic scheduler).
```

**Replace with**:

```
The Celery Beat schedule is built dynamically from the registry at Beat
startup, using each fetcher's effective schedule (config override or
default). When an admin modifies a fetcher's schedule via the API, the
Beat schedule MUST be updated accordingly. See "Celery Beat Schedule
Synchronization" below for the full mechanism.
```

**Additionally**, fix the typo at line 1348:

**Current text**:

```
`default_schedule` and `FetcherConfig.schedule` are interpreted as UTC.
```

**Replace with**:

```
`default_schedule` and `FetcherConfig.schedule_override` are interpreted as UTC.
```

### Step 2: Update the "Celery Integration" section header comment

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: the Related Specifications table row for "This document"
(line 36)
**Action**: add "Beat synchronization" to the content list of the "This
document" row. Current value:

> BaseFetcher base class, naming, error sanitization, custom settings,
> catch_up mechanism (generic), BaseFetcher HTTP client integration (lazy
> property, overrides, lifecycle), registry, Celery, concurrency, stale
> run detection, data model, retention, deregistered lifecycle, doc
> requirements

New value:

> BaseFetcher base class, naming, error sanitization, custom settings,
> catch_up mechanism (generic), BaseFetcher HTTP client integration (lazy
> property, overrides, lifecycle), registry, Celery, Beat schedule
> synchronization, concurrency, stale run detection, data model,
> retention, deregistered lifecycle, doc requirements

### Step 3: Update the Purpose paragraph

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: line 6-12 (Purpose section)
**Action**: add "Beat schedule synchronization" to the list of topics.
Current:

> ...the fetcher registry, Celery integration, concurrency control, the
> per-ticket `catch_up()` mechanism, custom settings schema, error message
> sanitization, BaseFetcher HTTP client integration, data model, and data
> retention.

New:

> ...the fetcher registry, Celery integration, Beat schedule
> synchronization, concurrency control, the per-ticket `catch_up()`
> mechanism, custom settings schema, error message sanitization,
> BaseFetcher HTTP client integration, data model, and data retention.

### Step 4: Add cross-reference in `fetcher-operations.md` PATCH side effects

**File**: `docs/features/platform/fetcher-operations.md`
**Location**: line 659-660 (the `schedule_override` side effect bullet
in the PATCH endpoint)
**Current text** (lines 659-660):

```
- If `schedule_override` changed: the Celery Beat schedule for this
  fetcher MUST be updated dynamically
```

**Replace with**:

```
- If `schedule_override`, `run_timeout`, or `enabled` changed: the
  redbeat schedule entry for this fetcher MUST be updated accordingly
  (see `docs/features/platform/fetcher-infrastructure.md`, "Celery Beat
  Schedule Synchronization — Runtime Propagation" for the full
  propagation mechanism, which fields trigger updates, and failure
  semantics)
```

**Note**: the existing bullet at lines 656-658 ("If `enabled` changed to
`false` and the fetcher is currently running: the current run is allowed
to complete. The next scheduled run will not start.") remains unchanged —
it describes the user-facing behavior. The new bullet above covers the
implementation mechanism (redbeat entry removal/creation).

### Step 4a: Update FetcherConfig notes in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: line 1558-1559 (FetcherConfig Notes bullet about
`schedule_override`)
**Current text**:

```
- The `schedule_override` uses standard cron syntax (5-field). When set,
  the Celery Beat schedule for this fetcher MUST be updated dynamically.
```

**Replace with**:

```
- The `schedule_override` uses standard cron syntax (5-field). When set,
  the redbeat schedule entry for this fetcher MUST be updated dynamically
  (see "Celery Beat Schedule Synchronization — Runtime Propagation"
  above).
```

### Step 4b: Update Dependencies section in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: line 1724 (Dependencies section)
**Current text**:

```
- Celery Beat with dynamic schedule support (`celery-redbeat` or
  equivalent)
```

**Replace with**:

```
- Celery Beat with `celery-redbeat` dynamic scheduler
```

### Step 4c: Add `next_run_at` cross-reference in `fetcher-operations.md`

**File**: `docs/features/platform/fetcher-operations.md`
**Location**: line 232-235 (`next_run_at` field description in List
Fetchers response)
**Current text**:

```
- `next_run_at`: calculated from the effective schedule and the Celery
  Beat state. `null` if the fetcher is disabled, deregistered, or the
  Celery Beat schedule state is unavailable (e.g., Redis flushed, Beat
  not yet started).
```

**Replace with**:

```
- `next_run_at`: calculated from the effective schedule and the Celery
  Beat state. `null` if the fetcher is disabled, deregistered, or the
  Celery Beat schedule state is unavailable (e.g., Redis flushed, Beat
  not yet started). See `docs/features/platform/fetcher-infrastructure.md`
  (Celery Beat Schedule Synchronization — `next_run_at` Calculation) for
  the computation mechanism.
```

### Step 5: Add redbeat note in `configuration.md`

**File**: `docs/configuration.md`
**Location**: after line 57 (end of the `task_ignore_result` paragraph
in "Celery Worker Configuration")
**Action**: insert the following paragraph:

> **Redbeat scheduler**: `celery-redbeat` (the dynamic Beat scheduler)
> uses the same Redis instance as the Celery broker (`CELERY_BROKER_URL`)
> by default. No separate `redbeat_redis_url` environment variable is
> needed or supported. The scheduler class is configured in the Celery
> application settings (`beat_scheduler = 'redbeat.RedBeatScheduler'`).
> Redbeat stores schedule entries under the `redbeat:` key prefix in the
> broker database. See
> `docs/features/platform/fetcher-infrastructure.md` (Celery Beat Schedule
> Synchronization) for the full synchronization mechanism between
> PostgreSQL (source of truth) and redbeat (execution layer).

### Step 6: Update Beat troubleshooting in `deployment.md`

**File**: `docs/deployment.md`
**Location**: after line 399 (end of "Celery Tasks Not Running"
troubleshooting, currently item 4)
**Action**: add two new troubleshooting items:

> ```
> 5. Check Beat logs for the reconciliation summary message ("Beat
>    schedule reconciliation complete: ..."). If absent, reconciliation
>    failed — check for PostgreSQL connectivity errors above it
> 6. If Beat exits repeatedly with "cannot read FetcherConfig from
>    PostgreSQL", ensure the database is reachable before Beat can start
>    successfully (Beat fails fast when PostgreSQL is unavailable at
>    startup)
> ```

### Step 7: Add scheduler class note in `deployment.md` local dev section

**File**: `docs/deployment.md`
**Location**: after line 105 (the `celery beat` command)
**Action**: add a comment line below the beat command:

> ```bash
> # Note: the redbeat scheduler class is configured in the Celery app
> # settings (beat_scheduler). No --scheduler CLI flag is needed.
> ```

### Step 8: Run reviewers on affected specs

After applying steps 1-7, invoke the following reviewers to verify
correctness:

1. **`@spec-coherence-reviewer`** on
   `docs/features/platform/fetcher-infrastructure.md` — verify the new
   section is coherent with the rest of the document and with
   `fetcher-operations.md`, `configuration.md`, `deployment.md`

2. **`@spec-gap-analyzer`** on
   `docs/features/platform/fetcher-infrastructure.md` — verify the new
   section does not introduce gaps (all edge cases covered, no ambiguous
   behavior)

3. **`@docs-reviewer`** on the set of modified documents — verify
   cross-references are correct, no broken links, no contradictions
   between the updated documents

4. **`@docs-placement-reviewer`** on
   `docs/features/platform/fetcher-infrastructure.md` — verify the new
   content is correctly placed (not misplaced in fetcher-infrastructure
   when it should be cross-cutting, and not over-generalized)

### Step 9: Delete this draft

After all reviewers pass and any findings are resolved:

- Delete `docs/drafts/celery-beat-sync.md`

---

## Coherence Checklist

The following cross-spec interactions have been verified and do not
require additional changes:

| Interaction | Status |
|---|---|
| `/ready` endpoint (health-endpoints.md) already covers redbeat Redis via CELERY_BROKER_URL PING | No change needed |
| FetcherAuditEvent does not need a propagation-result field (eventual consistency model, transient failure, self-heals) | No change needed |
| Concurrency Control section (`FOR UPDATE`) is unrelated to Beat sync (different mechanism, different store) | No change needed |
| Timezone validation (configuration.md) applies to Beat via shared Celery config | No change needed |
| FetcherConfig auto-creation (workers, `INSERT ON CONFLICT`) is orthogonal to Beat sync (different process, different concern) | No change needed |
| Deregistered Fetcher Lifecycle section — already says "Celery Beat does not schedule it". New section explains the mechanism (entry removed at startup). Consistent | No change needed |
| `data-model.md` FetcherConfig table — no schema changes needed (all information is already in the model) | No change needed |
