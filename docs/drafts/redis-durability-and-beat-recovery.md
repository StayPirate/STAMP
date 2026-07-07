# Draft: Redis Durability, Memory Configuration, and Beat Recovery

**Status**: Draft — pending review before application to specs  
**Scope**: Documentation/specification changes only (no code exists yet)  
**Files affected**: `docs/deployment.md`, `docs/configuration.md`,
`docs/conventions.md`, `docs/features/platform/fetcher-infrastructure.md`,
`docs/features/identity/authentication.md`,
`docs/features/identity/local-authentication.md`,
`docs/features/integrations/ibs-rabbitmq-integration.md`

---

## 1. Summary

This change introduces Redis operational requirements and corrects an
inaccuracy in the Beat recovery specification. It covers:

1. **Redis persistence policy**: persistence (RDB/AOF) is explicitly
   disabled by design — all Redis state is volatile, self-healing, or
   reconstructible from PostgreSQL.
2. **Redis memory configuration**: `maxmemory` + `noeviction` policy
   with container resource limits (QoS Guaranteed).
3. **Beat runtime Redis loss** (spec gap): documents that Beat
   terminates via the redbeat lock sentinel mechanism when Redis loses
   data at runtime — recovery is automatic via orchestrator restart.
4. **Correction to "Redis Flush Recovery"**: the current spec states
   that a `FLUSHALL` leaves Beat alive with an empty schedule requiring
   manual restart. This is inaccurate — the lock sentinel causes Beat to
   crash at the next tick, triggering automatic orchestrator recovery.
5. **`beat_max_loop_interval = 60`**: reduces worst-case recovery
   latency from ~25 min to ~5 min (lock timeout derivation).
6. **`RedisError` exception handling convention**: all application-owned
   Redis handlers must catch `RedisError` (base class) to cover both
   connection failures and OOM rejections under `noeviction`.
7. **Operational monitoring recommendation**: external alert on stalled
   fetcher activity via `GET /api/v1/fetchers` as defense-in-depth.

---

## 2. Background and Analysis

### 2.1 Redis Architecture in Sentinel

Sentinel uses two logical Redis databases on a single instance (or two
separate instances in split deployments):

- **`REDIS_URL`** (db 0): application cache and coordination —
  `session_liveness:{session_id}` (60s TTL), `login_attempts:{username}`
  (10min TTL), `fetch_pending:{cve_id}:{source}` (600s TTL),
  `cvss_recalc_active` (900s TTL), `sentinel:ibs_consumer_status` (60s
  TTL).
- **`CELERY_BROKER_URL`** (db 1): Celery task queue + `celery-redbeat`
  schedule entries (`redbeat:` prefix, including `redbeat::lock` and
  `redbeat::schedule` sorted set).

### 2.2 Source of Truth

PostgreSQL is the sole source of truth for all durable data:

- Sessions → `Session` table (Redis caches liveness for 60s)
- Fetcher schedules → `FetcherConfig` table (redbeat is a derived cache)
- Task outcomes → `FetcherRun` table (no Celery result backend)
- Mutation serialization → `SELECT ... FOR UPDATE` (not Redis locks)

No application-owned Redis key is the sole source of any durable data.

### 2.3 Impact of Total Redis Data Loss

| Data (DB) | Loss impact | Recovery mechanism |
|-----------|-------------|-------------------|
| Session liveness cache (0) | No logouts; brief extra DB load (~60s) | Automatic (PostgreSQL authoritative) |
| Login lockout counters (0) | Brute-force budgets reset; locked accounts unlock early | Automatic (transient, TTL-based) |
| Fetch dedup locks (0) | Possible duplicate on-demand fetch tasks | Automatic (upsert idempotent; FOR UPDATE + UNIQUE prevent duplicates) |
| CVSS recalc lock (0) | Possible concurrent batch | Automatic (idempotent; hard task timeout = TTL) |
| IBS consumer heartbeat (0) | Status shows `unreachable` briefly | Automatic (rewritten within 30s) |
| Celery task queue (1) | Queued/in-flight tasks lost | Next periodic sync covers; on-demand fetches re-triggerable |
| redbeat schedule entries (1) | Schedule stops firing | Automatic: lock loss → Beat crash → orchestrator restart → reconciliation from PostgreSQL |
| redbeat distributed lock (1) | Lock lost | Triggers Beat crash → recovery (see §2.4) |

**Corruption risk**: none. All downstream safety is enforced by
PostgreSQL constraints and row locks. Losing Redis deduplication locks
causes at most redundant (but idempotent) work.

### 2.4 Lock Sentinel Mechanism (Key Discovery)

Analysis of upstream sources (redbeat 2.4.0, redis-py, celery beat)
reveals that the redbeat distributed lock (`redbeat::lock`) acts as an
**automatic sentinel** for Redis data loss:

**Chain of events** (Redis restart or `FLUSHALL`):

1. Redis loses all data (including `redbeat::lock`)
2. Beat wakes at the next tick (within `beat_max_loop_interval`, ≤60s)
3. `tick()` calls `self.lock.extend(int(self.lock_timeout))` as its
   first operation (`redbeat/schedulers.py:544-547`)
4. The Lua script (`LUA_EXTEND_TO_SCRIPT`) executes `GET` on the lock
   key → key absent → returns `0`
5. `redis-py` `Lock.do_extend()` receives `0` → raises
   `LockNotOwnedError` (`redis/lock.py:315`)
6. `LockNotOwnedError` is not `RuntimeError` (not caught by
   `schedulers.py:555`) and not `KeyboardInterrupt`/`SystemExit` (not
   caught by `celery/beat.py:652`)
7. Exception propagates → Beat process terminates with non-zero exit
8. Orchestrator restarts Beat → startup reconciliation rebuilds
   schedule from PostgreSQL → normal operation resumes

**This mechanism covers both scenarios**:
- Redis restart (connection broken → `ConnectionError` at step 3, OR
  reconnection succeeds but lock absent → `LockNotOwnedError`)
- `FLUSHALL` with Redis still running (connection intact, lock absent →
  `LockNotOwnedError`)

**Critical prerequisite**: the redbeat lock MUST remain enabled (the
default). Disabling it (`redbeat_lock_key = None`) removes the sentinel
and creates the silent failure mode.

**Timing**: worst-case detection latency = `beat_max_loop_interval`
(configured to 60s). After detection, the orchestrator applies its
restart backoff (Kubernetes: 10s → 20s → ... → max 300s). The new Beat
instance must then acquire the lock (which expired after
`lock_timeout` = `max_interval * 5` = 300s from last extend). In the
common case (Redis restarted empty), no stale lock exists and
acquisition is immediate.

### 2.5 Correction to Current Spec

The current text in `fetcher-infrastructure.md:1853-1870` ("Redis Flush
Recovery") states:

> "Beat continues running but with an empty schedule — it does not
> crash... There is no automatic self-healing for this scenario without
> a Beat restart."

This is **inaccurate** given the lock sentinel mechanism. The correct
behavior is: Beat crashes at the next tick due to `LockNotOwnedError`,
and the orchestrator restarts it automatically. Manual intervention is
NOT required.

The only scenarios where silent failure could occur:
1. Lock explicitly disabled (`redbeat_lock_key = None`) — not the
   default, and will be documented as prohibited.
2. Selective Redis manipulation that deletes schedule entries but
   preserves the lock — already classified as "undefined behavior" in
   the spec.

### 2.6 `beat_max_loop_interval` Rationale

The Beat tick is **dynamic**: at each cycle, `tick()` returns
`min(time until next due task, max_interval)`. When a fetcher is due
soon, Beat sleeps only until that moment (sub-second precision). The
`max_interval` (controlled by `beat_max_loop_interval`) is the
**upper bound** — the longest Beat ever sleeps when no task is
imminently due. It therefore determines worst-case detection latency
for the lock sentinel.

Setting `beat_max_loop_interval = 60` (down from default 300):

| Aspect | Default (300s) | Configured (60s) |
|--------|---------------|-----------------|
| Worst-case detection of Redis loss | ≤5 min | ≤1 min |
| `lock_timeout` (derived: `max_interval * 5`) | 1500s (25 min) | 300s (5 min) |
| Lock stale expiry after Beat crash | up to 25 min | up to 5 min |
| New Beat startup delay (lock acquisition) | up to 25 min | up to 5 min |
| Tick frequency when idle | 1 tick/5min | 1 tick/min |
| Redis overhead per idle tick | 1 lock extend + 1 zrangebyscore | same (negligible) |

The strongest argument is **lock stale expiry**: with default 300s, if
Beat crashes without releasing the lock, a replacement Beat cannot start
scheduling for up to 25 minutes. With 60s, this shrinks to ≤5 minutes.

### 2.7 `RedisError` Convention Rationale

Under `noeviction`, when Redis reaches `maxmemory`, write commands
return `OOM command not allowed when used memory > 'maxmemory'`. The
Python Redis client raises `redis.exceptions.ResponseError` (a subclass
of `RedisError`, NOT of `ConnectionError`).

If application handlers only catch `ConnectionError`, OOM errors
propagate as unhandled exceptions (HTTP 500). By catching `RedisError`
(base class), both connection failures and OOM rejections trigger the
same graceful degradation (DB fallback for sessions, fail-open for
lockout, unconditional enqueue for dedup locks).

### 2.8 Memory Configuration Rationale

Redis operates entirely in RAM. Without `maxmemory`, Redis grows until
the container's memory limit, at which point the kernel's OOM killer
terminates the process (uncontrolled crash). Setting `maxmemory` with
`noeviction` transforms this into a controlled rejection: Redis refuses
new writes but remains alive and serving reads, preserving existing data.

Container resource configuration uses Kubernetes QoS "Guaranteed"
(`requests == limits`). This prevents node-level eviction of the Redis
pod under memory pressure — critical for a broker/coordination service.

Memory sizing: Sentinel's Redis footprint is dominated by the Celery
task queue backlog (worst case ~150 MB in first-run scenarios with
thousands of enqueued tasks). Application keys total < 10 MB. Redbeat
entries are negligible. A `maxmemory` of 768 MB at 75% of a 1 GiB
container provides ample headroom for the Redis process overhead
(allocator fragmentation, client buffers, internal data structures).

---

## 3. Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | Redis persistence disabled (`save ""`, `appendonly no`) | All state is volatile/reconstructible; persistence adds operational complexity for zero correctness benefit; RDB would undermine the lock sentinel by restoring non-expired lock keys after restart, bypassing automatic recovery |
| D2 | `maxmemory 768mb`, `maxmemory-policy noeviction` | Prevents silent eviction of broker keys; transforms OOM from kernel crash into controlled error |
| D3 | Container `requests.memory = limits.memory = 1Gi` (QoS Guaranteed) | Prevents pod eviction under node pressure; `maxmemory` anchors to this single value |
| D4 | `beat_max_loop_interval = 60` (fixed in code, not env var) | Reduces lock stale expiry from 25 min to 5 min; negligible cost |
| D5 | Beat fail-fast on lock loss: MUST exit non-zero; SHOULD catch `LockNotOwnedError` + `RedisError` in tick and log CRITICAL before exiting. Native propagation (raw traceback) satisfies the MUST — recovery works identically | Transforms raw traceback into actionable operator message; consistent with existing fail-fast patterns |
| D6 | `retry_period` NOT configured for redbeat | Preserves native fail-fast; prevents Beat from reconnecting to empty Redis silently |
| D7 | All application-owned Redis handlers catch `RedisError` (base) | Covers both ConnectionError and OOM ResponseError uniformly |
| D8 | Lock MUST NOT be disabled | Lock is the recovery sentinel; disabling it creates silent failure |
| D9 | External monitoring recommended (not built into product) | Uses existing `GET /api/v1/fetchers` data; zero code added |

---

## 4. Action Plan

### Step 1: Update `docs/features/platform/fetcher-infrastructure.md`

#### 1a. Add subsection "Runtime: Redis Data Loss"

**Location**: insert as a new `####` subsection immediately BEFORE
"Redis Flush Recovery" (currently at line 1853). The final document
order will be: "Runtime: Redis Data Loss" (new) → "Redis Flush
Recovery" (rewritten per step 1b) → "Direct Redis Manipulation"
(currently at line 1889). This ordering places the detailed mechanism
first, followed by the operational summary that references it.

**Content** (to be written verbatim):

```markdown
#### Runtime: Redis Data Loss (Restart or Flush)

If Redis loses its data while Beat is running — whether due to a Redis
process restart, a `FLUSHALL` command, or any event that clears the
keyspace — Beat automatically detects the loss and terminates, enabling
orchestrator-driven recovery.

**Detection mechanism — lock sentinel**: the first operation in every
`tick()` cycle is `self.lock.extend(lock_timeout)`. When the
`redbeat::lock` key is absent (data loss), the extend Lua script
returns `0` and the Redis client raises `LockNotOwnedError`. This
exception is not caught by the scheduler's internal handlers and
propagates to terminate the process.

**Specified behavior**: when Beat detects lock loss at runtime (via
`LockNotOwnedError` or `RedisError` during the lock extend):

1. Beat MUST exit with a non-zero exit code (invariant — both
   implementation paths guarantee this)
2. Beat SHOULD log at CRITICAL level before exiting: `"CRITICAL: Celery
   Beat lost Redis lock — Redis data may have been lost (restart or
   flush). Beat will exit for orchestrator restart. Recovery:
   orchestrator restarts Beat → reconciliation rebuilds schedule from
   PostgreSQL."`

**Implementation note**: the native behavior of redbeat + celery beat
already terminates the process when `LockNotOwnedError` propagates
uncaught. The implementation SHOULD wrap the scheduler's `tick()` to
catch `LockNotOwnedError` and `RedisError`, produce the CRITICAL log
message above, and call `sys.exit(1)` — transforming a raw traceback
into an actionable operator message. If wrapping is not feasible, the
native propagation (raw traceback + non-zero exit) satisfies the MUST
requirement: the recovery mechanism works identically in both cases.

The orchestrator restarts Beat according to its restart policy
(Kubernetes: CrashLoopBackOff with exponential backoff up to 300s).
On restart, the standard startup reconciliation rebuilds the full
schedule from PostgreSQL. No manual intervention is required.

**Detection latency**: the lock extend occurs once per tick. The
worst-case time between Redis data loss and Beat termination equals
`beat_max_loop_interval` (60 seconds). If Beat is sleeping when Redis
loses data, it will not detect the loss until it wakes for the next
tick.

**Prerequisite — lock must remain enabled**: the lock sentinel mechanism
requires the redbeat distributed lock to be active (the default
configuration). Disabling the lock (`redbeat_lock_key = None` or
`redbeat_lock_timeout = None`) removes the sentinel and creates a
silent failure mode where Beat continues running with an empty schedule
after data loss. Sentinel MUST NOT disable the redbeat lock.

**`retry_period` must not be configured**: the `redbeat_redis_options`
setting `retry_period` MUST NOT be set. When unset (the default), Redis
operations that fail raise immediately without internal retries,
enabling the fail-fast behavior. If `retry_period` were set to a
positive value, redbeat would retry internally — and if Redis returned
during the retry window in a clean state, Beat would reconnect to an
empty schedule without triggering the lock sentinel (the lock might be
re-acquired transparently during retry). This would reintroduce the
silent failure mode.

**Relationship to startup failures**: this runtime behavior complements
the startup fail-fast mechanisms (PostgreSQL unreachable, Redis error
during reconciliation). The same principle applies: Beat is either
correct or stopped — never silently wrong.
```

#### 1b. Replace "Redis Flush Recovery" subsection

**Location**: the existing subsection "Redis Flush Recovery" (lines
1853-1887).

**Action**: replace the entire content of this subsection with the
following corrected text:

```markdown
#### Redis Flush Recovery

If Redis is flushed (`FLUSHALL`) or restarted without persistence while
Beat is running:

1. All fetcher schedules and the distributed lock are lost immediately
2. At the next tick (within `beat_max_loop_interval`, ≤60s), Beat
   detects the lock loss via the sentinel mechanism (see "Runtime: Redis
   Data Loss" above)
3. Beat logs CRITICAL and exits with non-zero exit code
4. The orchestrator restarts Beat
5. On startup, the full reconciliation recreates all schedule entries
   from PostgreSQL

**Recovery is automatic** — no manual intervention is required. The
orchestrator's restart policy handles the process lifecycle.

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
- **`/ready` endpoint**: continues to return 200 (Redis is reachable
  after flush/restart). The flush itself is not detectable via health
  checks — detection is handled by the lock sentinel mechanism.
```

#### 1c. Add `beat_max_loop_interval` to Celery Beat configuration

**Location**: in the "Redbeat Configuration" subsection (around line
1466), add a row to the configuration table:

**Add after the existing table** (which covers `redbeat_redis_url`,
`redbeat_key_prefix`, Scheduler class):

```markdown
Additionally, the following Celery Beat setting is configured as a
fixed application-level value (not an environment variable):

| Setting | Value | Rationale |
|---------|-------|-----------|
| `beat_max_loop_interval` | `60` | Controls the maximum sleep duration between scheduler ticks. Reduces worst-case lock sentinel detection latency from 300s (default) to 60s, and — critically — reduces `lock_timeout` (derived as `max_interval * 5`) from 1500s to 300s. This means a replacement Beat instance can acquire the lock within ≤5 minutes of a crash, rather than ≤25 minutes with the default. The tick frequency increase (1/min vs 1/5min when idle) has negligible Redis overhead (one lock extend + one sorted set range query per tick). This value MUST NOT be configurable via environment variable — it is a system-level tuning with no deployment-specific variance. |

**Derived values** (from `beat_max_loop_interval = 60`):

| Derived setting | Value | Calculation |
|-----------------|-------|-------------|
| `lock_timeout` | 300s | `max_interval * 5` (redbeat default derivation) |
| `lock.acquire(sleep=...)` | 60s | `max_interval` (retry interval when lock is held) |
| Worst-case tick latency | 60s | Determines lock sentinel detection speed |
```

#### 1d. Rewrite lock purpose paragraph and add configuration constraint

**Location**: in the "Redbeat Distributed Lock" subsection (around line
1973).

**Action 1 — replace** the existing paragraph at lines 1975-1979:

```
This is a redbeat-internal mechanism — Sentinel does not need to manage
it. If a second Beat process starts, redbeat's lock prevents it from
taking over until the first one dies or releases the lock.
```

with:

```markdown
The redbeat distributed lock serves two purposes in Sentinel:

1. **Singleton enforcement** (redbeat-internal): if a second Beat
   process starts, the lock prevents it from taking over scheduling
   until the first one dies or releases the lock.
2. **Runtime recovery sentinel** (Sentinel-specific): the lock extend
   operation at the start of every `tick()` detects Redis data loss.
   When the lock key is absent, `LockNotOwnedError` terminates Beat,
   enabling automatic orchestrator recovery (see "Runtime: Redis Data
   Loss" above).
```

**Action 2 — append** after the rewritten paragraph:

```markdown
**Configuration constraint**: Sentinel MUST NOT disable the redbeat
distributed lock. Without it, Beat cannot detect Redis data loss and
would continue running with an empty schedule (silent failure). The
following configurations are prohibited:

- Setting `redbeat_lock_key` to `None` or empty string
- Setting `redbeat_lock_timeout` to `None` or `0`

These constraints are satisfied by the default redbeat configuration
(lock enabled, key = `redbeat::lock`, timeout derived from
`max_interval * 5`).
```

#### 1e. Update "Reconciliation is Startup-Only" rationale

**Location**: in the "Reconciliation is Startup-Only" subsection
(around lines 1834-1842), the bullet list and summary sentence
reference "manual Beat restart" which is now inaccurate given the
lock sentinel mechanism.

**Action**: replace the bullet at line 1836:

```
- A Redis flush — requires manual Beat restart for recovery (see "Redis
  Flush Recovery" below)
```

with:

```
- A Redis flush — triggers automatic Beat crash via lock sentinel and
  orchestrator restart (see "Redis Flush Recovery" below)
```

And replace the summary sentence at lines 1840-1842:

```
All three are extraordinary operational events where the minor
inconvenience of a manual restart is preferable to the continuous
overhead of periodic reconciliation.
```

with:

```
All three are extraordinary operational events where restart-based
recovery (automatic for Redis flush, immediate for the others) is
preferable to the continuous overhead of periodic reconciliation.
```

---

### Step 2: Update `docs/conventions.md`

#### 2a. Add Redis error handling convention

**Location**: append to the "Redis Key Conventions" subsection (after
line 380, before the "## TypeScript (Frontend)" heading).

**Content**:

```markdown
### Redis Error Handling

All application-owned Redis operations (operations that access
`REDIS_URL` directly, as opposed to library-managed broker operations)
MUST catch **`RedisError`** (the base class from `redis.exceptions`),
not narrower subclasses like `ConnectionError` or `TimeoutError`.

**Rationale**: under `noeviction` memory policy, when Redis reaches
`maxmemory`, write commands return an OOM error. The Python client
raises `redis.exceptions.ResponseError` — a subclass of `RedisError`
but NOT of `ConnectionError`. Catching only `ConnectionError` would
leave OOM errors unhandled (resulting in HTTP 500 responses).

By catching `RedisError`, all Redis failure modes (connection loss,
timeout, OOM rejection, protocol errors) trigger the same graceful
degradation path already specified per feature:

| Feature | Degradation on `RedisError` |
|---------|----------------------------|
| Session liveness (`session_liveness:*`) | Fall back to direct PostgreSQL query |
| Login lockout (`login_attempts:*`) | Fail-open (login proceeds without rate limiting) |
| Fetch deduplication (`fetch_pending:*`) | Unconditional enqueue (idempotent) |
| CVSS recalculation lock (`cvss_recalc_active`) | Return 503 `REDIS_UNAVAILABLE` |
| IBS consumer heartbeat | Log WARNING, continue operating |

**Scope**: this convention applies to application code that directly
calls the Redis client. It does NOT apply to:

- Celery broker operations (managed by the Celery framework; errors
  surface as task publish failures with Celery's own retry logic)
- Redbeat operations (managed by the library; errors in `tick()` are
  handled by the Beat fail-fast mechanism — see
  `docs/features/platform/fetcher-infrastructure.md`, "Runtime: Redis
  Data Loss")

**Pattern**:

    try:
        redis.set(f"fetch_pending:{cve_id}:{source}", "1", nx=True, ex=600)
    except RedisError:
        # Degrade gracefully per feature spec
        logger.warning("Redis unavailable for dedup lock: %s", exc)
        # proceed without deduplication (idempotent downstream)
```

---

### Step 3: Update `docs/deployment.md`

#### 3a. Add "Redis Durability, Memory, and Persistence" section

**Location**: insert as a new top-level `##` section after "Process
Architecture" (heading at line 314; section ends at line 350) and
before "Health Checks" (line 354). This positions it logically after
the process descriptions and before the operational monitoring guidance.

**Content**:

```markdown
## Redis Durability, Memory, and Persistence

Sentinel uses Redis in two roles, addressed by two configuration URLs
(see `docs/configuration.md`):

- **Application cache/coordination** (`REDIS_URL`, db 0): session
  liveness cache, login lockout counters, on-demand fetch deduplication
  locks, CVSS recalculation lock, IBS consumer heartbeat.
- **Celery broker + scheduler** (`CELERY_BROKER_URL`, db 1): task queue
  and `celery-redbeat` schedule entries (including the distributed lock
  used as recovery sentinel).

### Persistence is Disabled by Design

Redis persistence (RDB and AOF) MUST be disabled in all environments:

```
save ""
appendonly no
```

**Rationale**:

1. **No durable data lives solely in Redis.** PostgreSQL is the source
   of truth for all persistent state (sessions, schedules, task
   outcomes, mutation serialization). Every Redis key is either
   TTL-bounded and self-healing, or fully reconstructible from
   PostgreSQL via Beat's startup reconciliation.

2. **The Beat lock sentinel provides automatic recovery.** When Redis
   loses data (restart or flush), Beat detects the missing lock within
   ≤60 seconds, terminates, and the orchestrator restarts it. The
   reconciliation rebuilds the full schedule from PostgreSQL. No manual
   intervention is required. See
   `docs/features/platform/fetcher-infrastructure.md` (Runtime: Redis
   Data Loss) for the mechanism.

3. **Persistence would undermine the lock sentinel.** If RDB restored
   the `redbeat::lock` key after a Redis restart (the snapshot is recent
   enough that the lock has not expired — the lock TTL is 300s, typically
   still valid within a restart window), Beat's `lock.extend()` would
   succeed, the sentinel would NOT fire, and Beat would continue running
   with the schedule from the snapshot — bypassing the clean crash →
   reconciliation recovery path. Expired keys are correctly discarded at
   RDB reload, so this concerns non-expired keys specifically. Volatile
   Redis guarantees the lock is always absent after data loss, ensuring
   the sentinel always fires.

4. **Task queue loss is acceptable.** Queued tasks that are lost during
   a Redis restart are recovered by the next periodic fetcher execution
   (scheduled intervals range from 6 hours to 24 hours). On-demand
   fetches can be re-triggered via the API. The `FetcherRun` table in
   PostgreSQL tracks outcomes — no Celery result backend is used.

### Memory Configuration

Redis MUST be configured with explicit memory limits and the
`noeviction` policy to prevent silent data loss through eviction:

| Setting | Value | Purpose |
|---------|-------|---------|
| `maxmemory` | `768mb` | Internal memory ceiling (~75% of container limit). When reached, Redis refuses new writes rather than evicting existing keys |
| `maxmemory-policy` | `noeviction` | Write commands return OOM error; read commands continue. Preserves all existing data (queued tasks, schedule entries, locks) |

**Container resource limits** (Kubernetes QoS Guaranteed):

| Resource | Value | Purpose |
|----------|-------|---------|
| `requests.memory` | `1Gi` | Minimum guaranteed memory (scheduler placement) |
| `limits.memory` | `1Gi` | Maximum allowed memory (kernel OOM-kill threshold) |

Setting `requests == limits` achieves QoS class "Guaranteed": the pod
is never evicted under node memory pressure. This is appropriate for
Redis as a broker/coordination service.

**Why `maxmemory` must be lower than `limits.memory`**: the container
memory limit is enforced by the kernel — exceeding it causes immediate
process termination (OOM-kill). The Redis `maxmemory` setting is an
*internal* threshold that triggers the `noeviction` policy *before* the
kernel intervenes. The ~25% gap (768 MB vs 1024 MB) provides headroom
for Redis process overhead: allocator fragmentation, client connection
buffers, internal data structures, and Lua script execution memory.

**Behavior when `noeviction` triggers**: Redis returns
`OOM command not allowed when used memory > 'maxmemory'` on write
commands. Read commands continue normally. Application code handles this
as a `RedisError` with graceful degradation (see `docs/conventions.md`,
Redis Error Handling). For the Celery broker, OOM indicates a capacity
issue — operators should investigate queue backlog growth (e.g., workers
not consuming tasks).

**If the orchestrator imposes a memory limit lower than `maxmemory`**:
the kernel OOM-kills Redis *before* the `noeviction` policy activates.
The `maxmemory` becomes ineffective. Always ensure: `maxmemory` <
container `limits.memory`.

**Memory sizing rationale**: Sentinel's Redis footprint is small.
Application keys (db 0) total < 10 MB even with thousands of active
sessions. Redbeat entries are negligible (~1 KB × ~12 fetchers). The
primary variable is the Celery task queue backlog (db 1): under normal
operation nearly empty (workers consume in real-time); under stress
(first-run with thousands of CVEs, or workers down) may grow to
~100-150 MB. The 768 MB `maxmemory` provides >5× headroom over
realistic peak usage.

### Monitoring Scheduler Liveness (Recommended)

The lock sentinel mechanism ensures automatic recovery in all standard
failure modes. As defense-in-depth for edge cases (lock accidentally
disabled, Redis manipulated selectively), operators SHOULD configure
external monitoring on scheduler activity.

**Recommended signal** (cause-agnostic — detects any cause of stalled
ingestion):

> Alert when at least one fetcher with `enabled = true` has a
> `last_run.finished_at` older than 2× its configured schedule interval,
> or has never run (`last_run = null`).

This signal is derivable from `GET /api/v1/fetchers` without any code
changes to Sentinel. It detects not only empty schedules but also dead
workers, database unavailability, or any other cause of stalled
processing.

**Why not `/health` or `/ready`**: these endpoints report API server
instance health for the load balancer. Returning non-200 for a Beat
problem would incorrectly remove healthy API instances from rotation.
Beat is a separate process — its liveness is the orchestrator's
responsibility, not the API server's.

**When the schedule is legitimately empty**: if an operator disables all
fetchers, the schedule is empty by design. The monitoring signal above
correctly handles this: with no enabled fetchers, the condition "at
least one enabled fetcher with stale last_run" is false → no alert.
```

---

### Step 4: Update `docs/configuration.md`

#### 4a. Add note to Redis connection settings section

**Location**: after the paragraph ending "...without code changes."
(line 36), add:

```markdown
**Persistence and memory**: Redis persistence (RDB/AOF) is disabled by
design — all Redis state is volatile and reconstructible. See
`docs/deployment.md` (Redis Durability, Memory, and Persistence) for
the full operational requirements including `maxmemory`, `noeviction`
policy, and container resource limits.
```

#### 4b. Add `beat_max_loop_interval` to Celery Worker Configuration

**Location**: in the "Celery Worker Configuration" section, after the
"Redbeat scheduler" paragraph (line 76) and before `## SSO
Configuration` (line 78). This places it as the last item in the Celery
section, after the existing coverage of timezone settings,
`task_ignore_result`, and redbeat scheduler. Add:

```markdown
**Beat tick interval**: `beat_max_loop_interval = 60` is a fixed
application-level setting (not an environment variable). It controls the
maximum time Beat sleeps between scheduler ticks. This value determines:

- The worst-case latency for detecting Redis data loss (≤60s)
- The derived `lock_timeout` for the redbeat distributed lock (300s =
  `max_interval × 5`), which bounds how long a stale lock persists
  after a Beat crash before a replacement can start

This setting MUST NOT be exposed as an environment variable. It is a
system-level tuning constant with no deployment-specific variance.

**`retry_period` (redbeat)**: NOT configured. When unset (the default),
Redis operations raise immediately on failure without internal retries.
This preserves the fail-fast behavior that enables automatic recovery
via the lock sentinel mechanism. Setting `retry_period` to any value
would allow Beat to silently reconnect to empty Redis after a restart,
bypassing the lock sentinel detection. See
`docs/features/platform/fetcher-infrastructure.md` (Runtime: Redis Data
Loss).
```

---

### Step 5: Update individual feature specs (alignment)

Steps 5a and 5b are minor alignment edits — each spec already describes
the correct degradation behavior in prose; these edits add the explicit
`RedisError` exception class reference for implementer clarity.

Step 5c introduces **new behavioral specification** (Redis unavailability
handling for the IBS consumer) that is not documented today. It also
requires updating the Consumer States table to reflect the expanded
meaning of `unreachable`.

#### 5a. `docs/features/identity/authentication.md`

**Location**: the "Redis unavailability" paragraph in the session
liveness section (around line 207-212).

**Action**: ensure the text specifies `RedisError` as the exception
class. If it currently says "if Redis is unreachable" without naming the
class, add parenthetical: "(any `RedisError` — including connection
failures and OOM rejections)".

#### 5b. `docs/features/identity/local-authentication.md`

**Location**: the "Redis unavailability" paragraph in the lockout
section (around line 291-296).

**Action**: same as 5a — ensure `RedisError` is named as the caught
exception class.

#### 5c. `docs/features/integrations/ibs-rabbitmq-integration.md`

**Location**: in the "Redis Heartbeat" subsection (around line 312),
after the "TTL behavior" bullet (line 334-337).

**Action 1 — add a new bullet** documenting the Redis unavailability
degradation behavior:

```markdown
- **Redis unavailability**: if Redis is unreachable (any `RedisError` —
  including connection failures and OOM rejections) when the consumer
  attempts to write the heartbeat, the consumer logs a WARNING and
  continues operating normally. Event consumption and processing are
  unaffected — the heartbeat is a status reporting mechanism, not a
  prerequisite for operation. The API will report the consumer as
  `unreachable` (missing key) until Redis becomes available and the
  next heartbeat write succeeds.
```

**Action 2 — update the Consumer States table** (around line 354).
Replace the `unreachable` row:

```
| `unreachable` | Redis key expired — consumer process is presumed dead | Key absent |
```

with:

```markdown
| `unreachable` | Redis key absent — consumer process is down, or consumer is alive but unable to write heartbeat (Redis unreachable from consumer) | Key absent |
```

---

### Step 6: Run reviewers on affected specs

After all changes from steps 1-5 are applied:

| Reviewer | Target | Rationale |
|----------|--------|-----------|
| `@spec-gap-analyzer` | `docs/features/platform/fetcher-infrastructure.md` | Substantial modification to recovery semantics; verify no new gaps introduced |
| `@spec-coherence-reviewer` | `docs/features/platform/fetcher-infrastructure.md` | Changes interact with deployment.md, configuration.md, and conventions.md; verify no contradictions |
| `@spec-coherence-reviewer` | `docs/features/integrations/ibs-rabbitmq-integration.md` | New Redis unavailability behavior added; verify consistency with fetcher-operations.md and conventions.md |
| `@docs-placement-reviewer` | All modified files | Verify new rules are placed in the correct documents (cross-cutting vs feature-specific) |
| `@docs-reviewer` | All modified files | Verify documentation completeness and accuracy |

If any reviewer identifies issues rated "Needs revision", resolve them
before considering the change complete.

---

### Step 7: Delete this draft

After all changes are applied and reviewers confirm no issues:

```
rm docs/drafts/redis-durability-and-beat-recovery.md
```

---

## 5. Risks and Caveats

| Risk | Mitigation |
|------|------------|
| Lock sentinel behavior verified from upstream source analysis only (not live-tested) | Document as "verified from source; integration test recommended during implementation" in the spec |
| `celery-redbeat` not yet in `backend/pyproject.toml` dependencies | Noted as implementation prerequisite; tangential to spec changes |
| `beat_max_loop_interval` behavior could change in future celery versions | The setting is stable (existed since celery 4.x); lock_timeout derivation is in redbeat which Sentinel pins |
| Operators might configure `retry_period` without reading the spec | The prohibition is documented in both `fetcher-infrastructure.md` and `configuration.md`; startup validation could enforce this (implementation detail) |
| Memory estimates are architectural, not measured | Noted in deployment.md; operators should monitor actual usage and adjust if needed |

---

## 6. Out of Scope

The following items were discussed but are explicitly NOT part of this
change:

- **Code implementation**: no code exists yet; this change is
  spec-only.
- **Docker-compose updates**: the existing `docker-compose.yml` is a
  legacy placeholder; its update is tracked separately.
- **In-product detection** (Beat health endpoint, /ready modification):
  decided against — external monitoring is sufficient.
- **RDB as optional/recommended**: explicitly rejected — persistence is
  disabled by design.
- **AOF**: explicitly rejected — no Redis key justifies fsync-level
  durability.
