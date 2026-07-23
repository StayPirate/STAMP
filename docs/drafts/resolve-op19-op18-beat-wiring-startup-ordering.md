# Resolution: OP-19 (Beat Reconciliation Wiring) + OP-18 (Startup Ordering)

## Status

Draft — pending review and approval before applying changes to specs.

## Scope

This draft resolves two related open points:

- **OP-19** — specifies *how* the Beat startup reconciliation is invoked
  (the "wiring mechanism")
- **OP-18** — formalizes the cross-process startup ordering as an
  explicit architectural invariant

Both are specification-only changes. No implementation code, database
migrations, or test files exist yet — all modifications target
documentation under `docs/`.

---

## Part 1: OP-19 — Beat Reconciliation Wiring Mechanism

### Decision

**Option A: `beat_init` signal handler.**

The fetcher startup reconciliation is invoked via a Celery `beat_init`
signal handler registered in the Celery app module.

### Rationale

1. **Decoupling from redbeat internals**: `setup_schedule()` is an
   internal method of `celery-redbeat`. Overriding it couples Sentinel
   to redbeat's internal API, which could change between library
   versions. The `beat_init` signal is a stable, documented Celery
   public API.

2. **Separation of responsibilities**: redbeat handles its own native
   responsibilities (installing static `beat_schedule` entries via
   `setup_schedule()`); Sentinel handles its own (reconciling fetcher
   entries from PostgreSQL). The two mechanisms remain independent with
   a clean boundary — each can be understood, tested, and maintained
   in isolation.

3. **Deterministic ordering**: Celery's Beat startup sequence is:
   `Scheduler.__init__()` → `setup_schedule()` → `beat_init` signal.
   The reconciliation runs AFTER `setup_schedule()` has installed the
   static entries. This ordering is correct because:
   - Reconciliation step 4 (remove deregistered fetchers) uses the
     `task != "run_fetcher"` pre-filter, which protects static entries
     regardless of ordering
   - Static entries are already present when reconciliation inspects
     the redbeat keyspace — no timing gap
   - `bootstrap_fetcher_configs()` runs as the first operation inside
     the signal handler, satisfying the precondition "FetcherConfig
     records exist before reconciliation begins"

4. **Explicit fail-fast**: the signal handler wraps the entire
   bootstrap + reconciliation sequence in a `try/except` with
   `sys.exit(1)` on failure. This makes the fail-fast behavior
   explicit and documented in our code, rather than relying on
   exception propagation through third-party code paths.

5. **No configuration change**: the `beat_scheduler` Celery setting
   remains `'redbeat.RedBeatScheduler'` (stock, unmodified). No custom
   scheduler subclass is introduced.

### Complete Beat Startup Sequence (after this change)

```
1. Celery app module imported
   → Celery app factory runs
   → Timezone validation (UTC check)
   → Lock sentinel validation (redbeat_lock_key, redbeat_lock_timeout)
   → import app.services.fetcher_discovery  (populates FETCHER_REGISTRY)

2. RedBeatScheduler.__init__()
   → Acquires distributed lock (redbeat::lock)
     - Retries every max_interval (60s) if lock held by stale instance
     - Immediate if lock absent (common case: fresh start or Redis data loss)

3. RedBeatScheduler.setup_schedule()
   → Installs/refreshes non-fetcher static entries from app.conf.beat_schedule
     (cleanup_sessions, cleanup_stale_ticket_access_grants)
   → Removes static entries that were deleted from beat_schedule since last run
   → (native redbeat behavior — Sentinel does not modify this step)

4. beat_init signal emitted by Celery
   → Sentinel's handler executes:
     4a. bootstrap_fetcher_configs()
         - INSERT ON CONFLICT DO NOTHING for every fetcher in FETCHER_REGISTRY
         - Idempotent, concurrency-safe
         - Uses asyncio.run() (sync caller context)
     4b. reconcile_beat_schedule()
         - Step 1: Read FetcherConfig from PostgreSQL
         - Step 2: Write entries for enabled registered fetchers
         - Step 3: Remove entries for disabled fetchers
         - Step 4: Remove entries for deregistered fetchers (task != "run_fetcher" pre-filter)
         - Step 5: Log reconciliation summary

5. Beat tick loop begins
   → Normal operation: fires tasks per their schedules
```

### Failure Modes (unchanged semantics, new wiring detail)

| Failure | When | Behavior |
|---------|------|----------|
| PostgreSQL unreachable | Step 4a or 4b-Step1 | Handler logs CRITICAL, calls `sys.exit(1)`. Orchestrator restarts Beat. |
| Redis error during reconciliation | Step 4b-Steps2-4 | Handler logs CRITICAL, calls `sys.exit(1)`. Fail-on-first-error — partial state corrected by next successful reconciliation. |
| `bootstrap_fetcher_configs()` fails | Step 4a | Same as PostgreSQL unreachable (the function performs a DB write). |

These failure semantics are already specified in `fetcher-infrastructure.md`
(Startup Failure sections). This change only specifies that the
`sys.exit(1)` is invoked from the signal handler's error wrapper, not
from an unhandled exception propagating through the scheduler.

### Handler Location and Registration

- **Location**: `backend/app/core/beat_init.py` (or equivalent module
  imported by the Celery app module)
- **Registration**: the handler is connected to the `beat_init` signal
  at module import time (standard Celery signal pattern:
  `@beat_init.connect`)
- **Import**: the Celery app module
  (`backend/app/celery_app.py`) imports the handler module to ensure
  registration occurs in every process that loads the Celery app.
  This is safe because `beat_init` is only emitted when the Beat
  service starts — workers and the IBS consumer import the same Celery
  app but never emit `beat_init`, so the registered handler is never
  called in those processes.

### Interaction with Non-Fetcher Periodic Tasks

The current spec (`fetcher-infrastructure.md`, Non-Fetcher Periodic
Tasks, line 2086-2095) states:

> Non-fetcher periodic tasks are declared as static entries in
> `app.conf.beat_schedule` [...] set once at Celery app construction
> time. [...] `setup_schedule()` reads `app.conf.beat_schedule` during
> scheduler initialization, so if the dict is populated later (e.g.,
> via a `beat_init` signal handler [...]) the entries are not installed.

This passage correctly warns against populating `beat_schedule` from a
`beat_init` handler. The Sentinel `beat_init` handler does NOT
populate `beat_schedule` — it runs the fetcher reconciliation, which
operates on a separate set of redbeat entries (those with
`task == "run_fetcher"`). The warning remains valid and applicable;
it is not contradicted by this change.

---

## Part 2: OP-18 — Cross-Process Startup Ordering Invariant

### Decision

**Option A: formalize the invariant explicitly.**

Document the "order-independent after migrations" property as an
explicit architectural invariant in `deployment.md`, with a
cross-reference in `fetcher-infrastructure.md` for discoverability
by spec authors.

### The Invariant

> After Alembic migrations complete, all runtime processes (API server,
> Celery worker, Git worker, Celery Beat, IBS RabbitMQ consumer) MAY
> start in any order. No inter-process startup dependency exists.

### Proof — Why Each Process Is Order-Independent

| Process | Startup dependencies | Why order-independent |
|---------|---------------------|----------------------|
| API server | PostgreSQL, Redis | Imports `fetcher_discovery`, runs `bootstrap_fetcher_configs()` (idempotent), seeds `system_settings` (idempotent `ON CONFLICT DO NOTHING`). No dependency on other application processes. |
| Celery worker | PostgreSQL, Redis | Imports `fetcher_discovery`, runs `bootstrap_fetcher_configs()` (idempotent). Consumes tasks from Redis queue — if no tasks are queued yet, it idles. No dependency on Beat or API. |
| Git worker | PostgreSQL, Redis, persistent volume | Same as Celery worker with a dedicated queue. Volume is created by the orchestrator independently. |
| Celery Beat | PostgreSQL, Redis | Imports `fetcher_discovery`, acquires redbeat lock, runs `setup_schedule()` (static entries), then runs `bootstrap_fetcher_configs()` + reconciliation via `beat_init` handler. `bootstrap_fetcher_configs()` creates `FetcherConfig` records if they don't exist — Beat does not depend on any other process having created them first. |
| IBS RabbitMQ consumer | PostgreSQL, Redis, RabbitMQ | Connects to RabbitMQ with retry-on-failure. Queries PostgreSQL for monitored codestreams (populated by migrations + CVE ingestion tasks). Enqueues tasks via Redis. No dependency on Beat, workers, or API. |

**Key mechanism**: `bootstrap_fetcher_configs()` is the function that
eliminates the potential ordering dependency. Because it runs in every
process and uses `INSERT ON CONFLICT DO NOTHING`:

- If Beat starts first → Beat creates `FetcherConfig` records, then
  reconciles from them
- If a worker starts first → worker creates `FetcherConfig` records;
  when Beat starts later, bootstrap is a no-op (records exist),
  reconciliation proceeds normally
- If all start simultaneously → first `INSERT` wins, concurrent
  duplicates are no-ops; all processes proceed correctly

### Cross-Reference in fetcher-infrastructure.md

To ensure discoverability by spec authors (who read
`fetcher-infrastructure.md` when designing tasks, not `deployment.md`),
a brief cross-reference note will be added to
`fetcher-infrastructure.md` in the "Multi-Process Coordination" section:

> **Startup ordering invariant**: after Alembic migrations complete,
> all runtime processes MAY start in any order — no inter-process
> startup dependency exists. See `docs/deployment.md` (Startup Ordering
> Invariant) for the full rationale. Any change that introduces an
> inter-process startup dependency MUST update that section.

---

## Action Plan

### Step 1 — Update `fetcher-infrastructure.md`: specify the wiring mechanism

**File**: `docs/features/platform/fetcher-infrastructure.md`

**1a. Update the "Startup Reconciliation" section (line 1630)**

Replace the current "Startup Sequence" subsection (lines 1636-1646)
which lists preconditions as abstract bullet points, with a revised
version that specifies the concrete wiring mechanism. The revised
section must:

- State that the reconciliation is invoked via a `beat_init` signal
  handler
- Describe the complete Beat startup sequence (steps 1-5 from Part 1
  above), making clear the ordering relationship with
  `setup_schedule()` (which runs BEFORE the signal)
- State that `bootstrap_fetcher_configs()` is the first operation
  inside the signal handler (satisfying the existing precondition)
- State that the handler wraps the entire sequence in error handling
  with `sys.exit(1)` on failure
- State the handler location (`backend/app/core/beat_init.py`) and
  registration mechanism (`@beat_init.connect`, imported by the Celery
  app module)

The existing reconciliation steps 1-6 (lines 1659-1723), failure
modes (lines 1725-1788), and all subsequent sections remain unchanged
— this change adds the "how" without modifying the "what."

**1b. Remove the OP-19 out-of-scope note (lines 2103-2109)**

The "Non-Fetcher Periodic Tasks" section contains an explicit
out-of-scope note referencing OP-19:

> **Out of scope**: this section does not specify how or where
> Sentinel's fetcher startup-reconciliation procedure [...] is itself
> invoked at Beat process startup — that is a separate, pre-existing
> gap tracked as OP-19 in `docs/drafts/open-points.md`.

This note must be removed since OP-19 is now resolved. Replace it with
a brief statement confirming the relationship:

> The fetcher startup reconciliation is invoked via a `beat_init`
> signal handler that runs after `setup_schedule()` completes (see
> "Startup Reconciliation" above). The two mechanisms are independent:
> `setup_schedule()` manages non-fetcher static entries;
> reconciliation manages fetcher entries. The `task != "run_fetcher"`
> pre-filter in reconciliation step 4 ensures they do not interfere
> with each other.

**1c. Add cross-reference in "Multi-Process Coordination" (after line 2255)**

After the existing "Concurrency Between Beat and API" subsection, add
the startup ordering cross-reference note described in Part 2:

> **Startup ordering invariant**: after Alembic migrations complete,
> all runtime processes MAY start in any order — no inter-process
> startup dependency exists. See `docs/deployment.md` (Startup Ordering
> Invariant) for the full rationale. Any change that introduces an
> inter-process startup dependency MUST update that section.

**1d. Update "Startup Validation" section (lines 2297-2335)**

The current text at lines 2337-2343 says:

> Additionally, the Beat startup reconciliation implicitly validates:
> - PostgreSQL connectivity (reads `FetcherConfig`)
> - Redis/redbeat connectivity (writes entries)
> - `FETCHER_REGISTRY` population (via `import
>   app.services.fetcher_discovery` at process startup)

This remains accurate — the reconciliation still validates these
implicitly. Add a note clarifying the invocation mechanism:

> These validations occur inside the `beat_init` signal handler.
> Failures cause `sys.exit(1)` — see "Startup Reconciliation" above.

### Step 2 — Update `deployment.md`: add the Startup Ordering Invariant

**File**: `docs/deployment.md`

**2a. Add "Startup Ordering" subsection after "Process Architecture" (after line 438)**

Insert a new subsection within the Process Architecture section, after
the existing "Singleton Processes" subsection (line 440-445) and
before "Git Worker Volume" (line 448). The new subsection:

**Title**: `### Startup Ordering`

**Content** (the authoritative invariant):

The section must contain:

1. A clear statement of the invariant:
   > After Alembic migrations complete, all runtime processes (API
   > server, Celery worker, Git worker, Celery Beat, IBS RabbitMQ
   > consumer) MAY start in any order. No inter-process startup
   > dependency exists.

2. A brief explanation of the mechanisms that guarantee this:
   - `bootstrap_fetcher_configs()` runs in every process (idempotent
     `INSERT ON CONFLICT DO NOTHING`) — Beat does not depend on
     workers having created `FetcherConfig` records first
   - `system_settings` seeding uses `ON CONFLICT DO NOTHING` (Alembic
     data migration is the primary mechanism; FastAPI lifespan is
     defense-in-depth)
   - The IBS RabbitMQ consumer connects to RabbitMQ with retry
     semantics — it operates independently of Beat and workers
   - Each process fails fast if infrastructure dependencies (PostgreSQL,
     Redis) are unreachable — no process silently waits for another
     application process

3. A modification clause:
   > If a future change introduces an inter-process startup dependency,
   > this section MUST be updated with the new constraint and the
   > deployment manifests (Docker Compose, Kubernetes) adjusted
   > accordingly.

4. A reference to `fetcher-infrastructure.md` for the Beat-specific
   startup sequence detail.

### Step 3 — Update `configuration.md`: confirm beat_scheduler value

**File**: `docs/configuration.md`

**3a. Verify the existing text (lines 73-83)**

The current text states:

> The scheduler class is configured in the Celery application settings
> (`beat_scheduler = 'redbeat.RedBeatScheduler'`).

This remains correct — the OP-19 resolution does not change the
scheduler class. No modification needed, but verify during
application that no contradictory text exists elsewhere in this file.

### Step 4 — Update `open-points.md`: mark OP-19 and OP-18 as Resolved

**File**: `docs/drafts/open-points.md`

**4a. Move OP-19 and OP-18 from "Open" to "Archive — Resolved"**

In the Summary table:
- Change OP-18 status from `Open` to `Resolved`
- Change OP-19 status from `Open` to `Resolved`
- Move both rows to the resolved section of the table (after OP-14)
- Clear the Domain column (use `—` like other resolved entries)

**4b. Remove the OP-18 and OP-19 detail sections from "Open — Cross-Process Startup"**

Remove the full detail sections for OP-18 (lines 655-711) and OP-19
(lines 715-764) from the Open section.

**4c. Add resolution entries in "Archive — Resolved"**

Add two new entries at the end of the Archive section:

**OP-18**:

> ### OP-18. Cross-Process Startup Ordering — RESOLVED
>
> **Resolution**: the "order-independent after migrations" property is
> now documented as an explicit architectural invariant in
> `docs/deployment.md` (Startup Ordering). The invariant is guaranteed
> by `bootstrap_fetcher_configs()` running idempotently in every
> process, `system_settings` seeding using `ON CONFLICT DO NOTHING`,
> and the IBS consumer operating independently with retry semantics. A
> cross-reference in `docs/features/platform/fetcher-infrastructure.md`
> (Multi-Process Coordination) ensures discoverability by spec authors.

**OP-19**:

> ### OP-19. Beat Reconciliation Wiring Mechanism — RESOLVED
>
> **Resolution**: the reconciliation is invoked via a `beat_init`
> signal handler (`@beat_init.connect`), registered in
> `backend/app/core/beat_init.py` and imported by the Celery app
> module. The handler runs `bootstrap_fetcher_configs()` followed by
> the reconciliation procedure, with `sys.exit(1)` on any failure
> (explicit fail-fast). The `beat_scheduler` setting remains
> `'redbeat.RedBeatScheduler'` (stock, unmodified). See
> `docs/features/platform/fetcher-infrastructure.md` (Startup
> Reconciliation) for the complete Beat startup sequence.

**4d. Check if "Open — Cross-Process Startup" section can be simplified**

After removing OP-18 and OP-19, the "Open — Cross-Process Startup"
section will contain only OP-16 and OP-17. Verify the section header
and any introductory text are still appropriate.

### Step 5 — Verify no contradictions in related specs

**Files to check** (read-only verification, no expected modifications):

| File | What to verify |
|------|---------------|
| `docs/architecture.md` (Container Images, Singleton Processes) | Confirm that no startup ordering is mentioned or implied that contradicts the new invariant. |
| `docs/features/integrations/ibs-rabbitmq-integration.md` (Lifecycle, Deployment) | Confirm that the consumer startup description is consistent with "order-independent." The existing text describes retry-on-connect for RabbitMQ — this is consistent. |
| `docs/features/platform/health-endpoints.md` (Readiness) | Confirm that the readiness checks do not imply inter-process dependencies. The existing text explicitly states "Worker prerequisites NOT in API readiness" — this is consistent. |

No modifications are expected in these files. If contradictions are
found during application, they must be resolved before proceeding.

### Step 6 — Review and cleanup

**6a. Run `@spec-coherence-reviewer`** on the two modified specs:

- `docs/features/platform/fetcher-infrastructure.md` — to verify the
  new wiring section is coherent with the rest of the spec and with
  other specs that reference Beat behavior
- `docs/deployment.md` — to verify the new invariant section is
  coherent with existing deployment instructions and process
  architecture description

**6b. Run `@spec-gap-analyzer`** on the modified spec:

- `docs/features/platform/fetcher-infrastructure.md` — the Startup
  Reconciliation section is a substantial specification addition;
  verify that no functional gaps remain (e.g., missing failure modes,
  unspecified edge cases in the signal handler lifecycle)

**6c. Delete this draft file**

Once all changes are applied and reviews pass, delete
`docs/drafts/resolve-op19-op18-beat-wiring-startup-ordering.md`.
