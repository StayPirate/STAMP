# OP-17 Resolution: IBS RabbitMQ Consumer Startup Specification

## Summary

This draft resolves OP-17 (IBS RabbitMQ Consumer Startup Specification
Gaps) by adding a complete process startup specification to
`docs/features/integrations/ibs-rabbitmq-integration.md`.

**Problem**: the consumer spec covers runtime behavior (message
processing, reconnection, heartbeat) thoroughly but leaves four startup
behaviors unspecified, forcing implementers to make autonomous design
decisions.

**Scope**: specification-only changes. No code, migrations, or
implementation artifacts are affected.

---

## Decisions

| Gap | ID | Decision | Rationale |
|-----|----|----------|-----------|
| Celery app sharing | Gap 1 | The consumer imports the Celery app module. It is NOT a Celery worker (does not consume tasks from a queue). Remove "(or standalone process)" ambiguity. | Three other specs already assume this (`configuration.md:63`, `fetcher-infrastructure.md:2414`, `fetcher-infrastructure.md:1648`). The consumer needs the Celery app to enqueue tasks via `.delay()`. |
| `IBS_RABBITMQ_ENABLED=false` | Gap 2 | Process exits immediately with code 0 and an INFO-level log message. | Standard pattern for optional services in container orchestration. Docker `restart: on-failure` and Kubernetes `restartPolicy` respect exit(0) as intentional stop. |
| DB/Redis connectivity at startup | Gap 3 | Fail-fast: exit with non-zero code if PostgreSQL or Redis is unreachable at startup (5-second timeout each, checked sequentially PG then Redis). | Aligns with `deployment.md` assertion ("Each process fails fast if infrastructure dependencies are unreachable"). Consistent with Beat's fail-fast pattern. |
| Fetcher module imports | Gap 4 | FETCHER_REGISTRY populates as a side-effect of importing the Celery app module (which imports `fetcher_discovery`). The consumer does NOT use the registry and does NOT run `bootstrap_fetcher_configs()`. | The consumer doesn't list, schedule, or execute fetchers. Running bootstrap would add an unnecessary PG write at startup. The registry population is harmless (negligible memory, milliseconds of import time). |

### Additional Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| PG/Redis connectivity timeout | 5 seconds each | Generous enough for normal startup jitter; short enough to fail fast when infrastructure is genuinely down |
| Check order | PostgreSQL first, then Redis | PG is needed first (codestream set). If PG is unreachable, testing Redis is pointless |
| Exit code for disabled | 0 | Intentional stop, not an error |
| Exit code for infrastructure failure | 1 | User/system error (infrastructure unreachable) |
| `bootstrap_fetcher_configs()` | NOT executed by the consumer | Consumer doesn't need FetcherConfig records. Only worker, Beat, and API server run bootstrap |

---

## Action Plan

### Step 1: Rewrite the "Deployment" subsection in `ibs-rabbitmq-integration.md`

**File**: `docs/features/integrations/ibs-rabbitmq-integration.md`

**Location**: lines 128-141 (section `#### Deployment` under `### Component: IBSEventConsumer`)

**Current text** (to be replaced in full):

```markdown
#### Deployment

The consumer runs as a **dedicated Celery worker process** (or standalone
process) — separate from the periodic task workers. This ensures the
persistent AMQP connection is not disrupted by Celery task execution
mechanics.

Configuration:
- `IBS_RABBITMQ_URL`: broker URL (default: `amqps://suse:suse@rabbit.suse.de`)
- `IBS_RABBITMQ_ENABLED`: boolean to enable/disable the consumer
  (default: `true`)
- `IBS_RABBITMQ_ROUTING_KEYS`: comma-separated routing keys (default:
  `suse.obs.package.commit,suse.obs.request.create,suse.obs.request.state_change`)
```

**Replacement text**:

```markdown
#### Deployment

The consumer runs as a **standalone process** — a dedicated long-running
service with its own entrypoint, separate from Celery workers and Celery
Beat. It is NOT a Celery worker: it does not consume tasks from any
Celery queue.

The consumer imports the **Celery app module**
(`backend/app/celery_app.py`) at startup. This provides:

- Access to the configured Celery application for task enqueueing
  (`create_ticket_from_detection.delay(...)`)
- Automatic timezone validation (UTC check) inherited from the app
  factory
- Automatic lock sentinel validation inherited from the app factory
  (innocuous — the consumer does not use redbeat)
- `FETCHER_REGISTRY` population via `fetcher_discovery` import
  (side-effect of the Celery app module; the consumer does not use the
  registry)

The consumer does **NOT** execute `bootstrap_fetcher_configs()` at
startup. It does not read or write `FetcherConfig` records — that
responsibility belongs to the API server, Celery workers, and Beat.

Configuration:
- `IBS_RABBITMQ_URL`: broker URL (default: `amqps://suse:suse@rabbit.suse.de`)
- `IBS_RABBITMQ_ENABLED`: boolean to enable/disable the consumer
  (default: `true`). See [Process Startup](#process-startup) for the
  process-level behavior when disabled.
- `IBS_RABBITMQ_ROUTING_KEYS`: comma-separated routing keys (default:
  `suse.obs.package.commit,suse.obs.request.create,suse.obs.request.state_change`)
```

---

### Step 2: Add new "### Process Startup" section in `ibs-rabbitmq-integration.md`

**File**: `docs/features/integrations/ibs-rabbitmq-integration.md`

**Location**: insert immediately after the `#### Deployment` subsection
(after Step 1's replacement text ends) and before
`### Processing Pipeline` (currently at line 142). This places the
startup specification between "how the process is deployed" and "what
it does with messages" — the natural chronological order a reader
expects.

**New section to insert**:

```markdown
### Process Startup

#### Complete Startup Sequence

```
1. Celery app module imported (backend/app/celery_app.py)
   -> Celery app factory runs
   -> Timezone validation (RuntimeError if CELERY_TIMEZONE != UTC)
   -> Lock sentinel validation (RuntimeError if redbeat lock disabled)
   -> import app.services.fetcher_discovery (populates FETCHER_REGISTRY — unused by consumer)

2. Read IBS_RABBITMQ_ENABLED from configuration
   -> If false: log INFO, exit with code 0 (see "Disabled Mode" below)

3. Infrastructure connectivity check (fail-fast)
   -> PostgreSQL: execute SELECT 1 with 5-second timeout
      - If unreachable: log CRITICAL, exit with code 1
   -> Redis: execute PING with 5-second timeout
      - If unreachable: log CRITICAL, exit with code 1

4. Build monitored codestream set (initial load from PostgreSQL)
   -> Query TicketPackageTrack records for active tickets
   -> Cache result in memory (subsequent refreshes every 5 minutes)

5. Connect to RabbitMQ broker
   -> If unreachable: log ERROR, retry with exponential backoff
      (existing reconnection behavior — see Lifecycle above)

6. Declare exclusive queue, bind routing keys

7. Begin consume loop
```

#### Disabled Mode (`IBS_RABBITMQ_ENABLED=false`)

When the `IBS_RABBITMQ_ENABLED` configuration setting is `false`, the
consumer process performs only steps 1-2 of the startup sequence:

1. Import the Celery app module (validates timezone and lock sentinel
   configuration — ensures a misconfigured Celery app is detected even
   when the consumer is disabled)
2. Read the `IBS_RABBITMQ_ENABLED` setting and detect `false`
3. Log at INFO level: `"IBS RabbitMQ consumer disabled
   (IBS_RABBITMQ_ENABLED=false). Exiting."`
4. Exit with code 0

The process does NOT check PostgreSQL or Redis connectivity when
disabled — infrastructure checks are skipped because the consumer will
not operate.

**Orchestrator interaction**: with `restart: on-failure` (Docker Compose)
or the equivalent Kubernetes restart policy, exit code 0 is not treated
as a failure — the container is not restarted. This allows operators to
disable the consumer via environment variable without removing it from
the deployment manifest.

#### Startup Failure: PostgreSQL Unreachable

If PostgreSQL is unreachable during the startup connectivity check
(step 3):

- The consumer logs at CRITICAL level: `"CRITICAL: IBS RabbitMQ consumer
  startup failed — cannot connect to PostgreSQL: {error}. The monitored
  codestream set cannot be built. Consumer will not start."`
- Exit with code 1
- The orchestrator (Docker/Kubernetes) restarts the container according
  to its restart policy. On the next attempt, if PostgreSQL is
  reachable, the consumer proceeds normally.

**Rationale**: without the monitored codestream set, the consumer cannot
determine which events are relevant. Processing all events
indiscriminately would cause unnecessary IBS API calls (diff requests)
for unmonitored codestreams. Failing fast is safer than operating
blindly.

#### Startup Failure: Redis Unreachable

If Redis is unreachable during the startup connectivity check (step 3,
after PostgreSQL succeeded):

- The consumer logs at CRITICAL level: `"CRITICAL: IBS RabbitMQ consumer
  startup failed — cannot connect to Redis: {error}. Cannot enqueue
  tasks or write heartbeat. Consumer will not start."`
- Exit with code 1
- The orchestrator restarts the container. On the next attempt, if Redis
  is reachable, the consumer proceeds normally.

**Rationale**: without Redis, the consumer cannot enqueue Celery tasks
(`create_ticket_from_detection`) or write its heartbeat. It would
consume and acknowledge messages without producing any downstream
effect — silently discarding events. Failing fast is preferable.

**Contrast with runtime Redis unavailability**: the heartbeat section
(Redis Heartbeat above) specifies that runtime Redis failures for
heartbeat writes are non-fatal (log WARNING, continue operating). This
is different from startup: at runtime, the consumer is already
processing events and the inability to write heartbeat is a monitoring
gap, not a functional failure. Task enqueue failures at runtime are
handled per-event (the event's downstream processing fails, but the
consumer itself continues receiving other events). At startup, however,
total Redis unavailability means the consumer cannot perform ANY of its
downstream responsibilities.
```

---

### Step 3: Update the "Error Handling" table in `ibs-rabbitmq-integration.md`

**File**: `docs/features/integrations/ibs-rabbitmq-integration.md`

**Location**: the Error Handling table (lines 297-305)

**Add two new rows** at the end of the existing table (after the
"Active codestream set refresh fails" row):

```markdown
| PostgreSQL unreachable at startup | Log CRITICAL, exit with code 1. Orchestrator restarts the container. Consumer cannot build the monitored codestream set without PostgreSQL |
| Redis unreachable at startup | Log CRITICAL, exit with code 1. Orchestrator restarts the container. Consumer cannot enqueue tasks or write heartbeat without Redis |
```

---

### Step 4: Update `docs/drafts/open-points.md` — Summary Table

**File**: `docs/drafts/open-points.md`

**Location**: the Summary table (lines 7-27)

**Change the OP-17 row** from:

```markdown
| OP-17 | IBS RabbitMQ Consumer Startup Gaps | Cross-Process Startup | Open |
```

to:

```markdown
| OP-17 | IBS RabbitMQ Consumer Startup Gaps | — | Resolved |
```

---

### Step 5: Update `docs/drafts/open-points.md` — Remove Open Section

**File**: `docs/drafts/open-points.md`

**Location**: lines 603-651 (the full `### OP-17` section under
"Open — Cross-Process Startup")

**Action**: remove the entire `### OP-17. IBS RabbitMQ Consumer Startup
Specification Gaps` section (from `### OP-17.` through the end of its
content before the next `---` separator).

**Additionally**: if OP-16 is the only remaining item under
"## Open — Cross-Process Startup", the section header remains (OP-16 is
still open).

---

### Step 6: Update `docs/drafts/open-points.md` — Add Archive Entry

**File**: `docs/drafts/open-points.md`

**Location**: at the end of the "## Archive — Resolved" section (after
the last resolved OP entry, currently OP-19)

**Add**:

```markdown
---

### OP-17. IBS RabbitMQ Consumer Startup Specification Gaps — RESOLVED (2026-07-23)

**Resolution**: all four startup gaps have been specified in
`docs/features/integrations/ibs-rabbitmq-integration.md` (section
"Process Startup"):

1. **Celery app sharing**: the consumer imports the Celery app module
   (inherits timezone and lock sentinel validation). It is explicitly
   NOT a Celery worker. The "(or standalone process)" ambiguity has been
   removed.
2. **`IBS_RABBITMQ_ENABLED=false`**: process exits immediately with
   code 0 and an INFO log. Orchestrator does not restart (exit 0 is not
   a failure).
3. **DB/Redis connectivity at startup**: fail-fast (exit 1) if
   PostgreSQL or Redis is unreachable (5-second timeout each, checked
   sequentially). Consistent with `deployment.md` assertion and Beat's
   fail-fast pattern.
4. **Fetcher module imports**: FETCHER_REGISTRY populates as side-effect
   of Celery app import (unused). Consumer does NOT run
   `bootstrap_fetcher_configs()`.

See `docs/features/integrations/ibs-rabbitmq-integration.md` (Process
Startup) for the complete startup sequence.
```

---

### Step 7: Verify cross-spec consistency

After applying Steps 1-6, verify that no contradiction exists between
the modified consumer spec and the following documents:

| Document | What to verify | Expected result |
|----------|---------------|-----------------|
| `docs/deployment.md` (Startup Ordering, line 465-469) | "The IBS RabbitMQ consumer connects to RabbitMQ with retry semantics — it operates independently of Beat and workers." and "Each process fails fast if infrastructure dependencies (PostgreSQL, Redis) are unreachable" | Both statements are now supported by the consumer spec. No change needed. |
| `docs/configuration.md` (line 63) | "Since every Celery-based process (worker, Beat, IBS RabbitMQ consumer) imports the app object" | Consistent. The consumer spec now explicitly states it imports the Celery app module. No change needed. |
| `docs/features/platform/fetcher-infrastructure.md` (line 1648) | "workers and the IBS consumer import the same Celery app but never emit `beat_init`" | Consistent. The consumer imports the Celery app but does not emit `beat_init`. No change needed. |
| `docs/features/platform/fetcher-infrastructure.md` (Multi-Process Coordination, "Who Writes Where") | `bootstrap_fetcher_configs()` lists "all processes: worker, Beat, API server" — consumer is absent | Consistent. The consumer spec now explicitly states it does NOT run bootstrap. No change needed. |
| `docs/architecture.md` (Container Images) | Lists "IBS RabbitMQ consumer (singleton)" as a runtime process | Consistent with "standalone process" language. No change needed. |

**Action**: read each document at the referenced location and confirm no
update is required. If a contradiction is found, add a corrective step
before proceeding to Step 8.

---

### Step 8: Run reviewers on modified specifications

After all spec modifications are applied, invoke the following reviewers
to verify correctness and completeness:

1. **`@spec-gap-analyzer`** on
   `docs/features/integrations/ibs-rabbitmq-integration.md` — verify the
   new Process Startup section has no uncovered edge cases (e.g., what
   happens if PG becomes unreachable AFTER the initial check but BEFORE
   the codestream set query completes)

2. **`@spec-coherence-reviewer`** on
   `docs/features/integrations/ibs-rabbitmq-integration.md` — verify no
   contradictions with `deployment.md`, `configuration.md`, and
   `fetcher-infrastructure.md`

3. **`@docs-placement-reviewer`** on
   `docs/features/integrations/ibs-rabbitmq-integration.md` — verify
   that the startup specification belongs in this spec (not in a
   cross-cutting document)

If reviewers identify issues rated "Needs revision", address them before
proceeding. Minor issues should be fixed inline.

---

### Step 9: Delete this draft

After all modifications are applied, verified by reviewers, and any
issues resolved:

**Delete** `docs/drafts/op17-ibs-consumer-startup.md` (this file).

The draft has served its purpose as a review artifact. The authoritative
specification now lives in
`docs/features/integrations/ibs-rabbitmq-integration.md`.

---

## Files Modified (Summary)

| File | Action |
|------|--------|
| `docs/features/integrations/ibs-rabbitmq-integration.md` | Rewrite "Deployment" subsection + add "Process Startup" section + add 2 rows to Error Handling table |
| `docs/drafts/open-points.md` | Move OP-17 from Open to Archive (Resolved) |
| `docs/drafts/op17-ibs-consumer-startup.md` | Created (this file) then deleted at end |

## Files NOT Modified (With Justification)

| File | Why no change needed |
|------|---------------------|
| `docs/deployment.md` | Already asserts fail-fast for all processes — consumer spec now conforms |
| `docs/configuration.md` | Already states consumer imports Celery app — no contradiction |
| `docs/features/platform/fetcher-infrastructure.md` | Already states consumer imports Celery app but skips `beat_init` — consistent |
| `docs/architecture.md` | Already lists consumer as singleton runtime process — consistent |
