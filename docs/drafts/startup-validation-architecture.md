# Startup Validation Architecture

Decision record for consolidating and rationalizing process startup
validations across all Sentinel runtime processes. Resolves OP-16
(CPE Mapping Fail-Fast Asymmetry) as a consequence.

## Problem Statement

Sentinel runs five runtime processes from the same Docker image (API
server, Celery worker, git worker, Celery Beat, IBS RabbitMQ consumer)
plus a one-shot migration job. Each process performs startup validations
through different mechanisms, at different layers, with inconsistent
coverage:

| Validation | Mechanism | Where it fires | Which processes benefit |
|------------|-----------|----------------|------------------------|
| JWT key length (>=32 chars) | Pydantic `model_validator` on `Settings` | `Settings()` instantiation | All (every process imports `config.py`) |
| JWT expiry >= 1 | Pydantic `model_validator` on `Settings` | `Settings()` instantiation | All |
| Session lifetime >= 1 | Pydantic `model_validator` on `Settings` | `Settings()` instantiation | All |
| LOG_LEVEL / LOG_FORMAT valid | Startup check (spec: `logging.md`) | Process init, pre-structlog | All (spec says "the process") |
| Celery timezone == UTC | Celery app factory (`celery_app.py`) | Module import | Celery-based processes only (worker, Beat, IBS consumer) |
| Redbeat lock enabled | Celery app factory (`celery_app.py`) | Module import | Celery-based processes only |
| CPE mapping loadable | FastAPI `lifespan` event | API server startup | **API server only** — but consumers are workers |
| `system_settings` seed | FastAPI `lifespan` event | API server startup | API server (defense-in-depth; primary is Alembic) |
| `bootstrap_fetcher_configs` | Per-process startup code | Process init | Worker, Beat, API server (NOT IBS consumer) |
| Beat schedule reconciliation | `beat_init` signal handler | Beat startup | Beat only |
| PostgreSQL reachability | Explicit `SELECT 1` check | Process init | IBS consumer, `/ready` endpoint |
| Redis reachability | Explicit `PING` check | Process init | IBS consumer, `/ready` endpoint |
| Monitored codestream set | PostgreSQL query | Process init | IBS consumer only |

### Identified issues

1. **OP-16 — CPE mapping fail-fast asymmetry**: the CPE mapping is
   validated at boot in the API server (which never uses it) but not in
   workers (which are the actual consumers). A corrupted or missing
   mapping file would be detected at API startup but surface in workers
   only when the first CVE ingestion task runs — potentially hours later.

2. **Mechanism fragmentation**: five different validation mechanisms
   (Pydantic validators, Celery app factory, FastAPI lifespan, Celery
   signal handlers, explicit connectivity checks) with no unified
   pattern. Each process assembles its own ad-hoc subset.

3. **Duplicated connectivity primitives**: the PostgreSQL `SELECT 1`
   check and the Redis `PING` check are independently specified in at
   least two places (the IBS consumer startup sequence and the `/ready`
   readiness endpoint), each with its own timeout and error handling.
   There is no single definition of "how to check PostgreSQL/Redis
   reachability."

4. **Implicit vs. explicit infrastructure checks**: Beat validates
   PostgreSQL/Redis implicitly (they fail if reconciliation can't
   read/write). Workers validate neither — a worker with an unreachable
   PostgreSQL would accept Celery tasks and fail at the first database
   query. The IBS consumer and `/ready` are the only surfaces with
   explicit connectivity checks.

## Design Principles

### Two classes of validation

Startup validations fall into two categories with fundamentally
different properties:

**Class 1 — Static resource validation** (image-baked, deterministic):
validates resources embedded in the Docker image or derived from
environment variables. These checks are **deterministic**: if they pass
once, they pass for every process started from the same image with the
same environment. A single centralized check is sufficient.

Examples: CPE mapping file loadable, configuration bounds (JWT key
length, expiry range, session lifetime), Celery timezone, logging
format, redbeat lock config.

**Class 2 — Dynamic infrastructure validation** (runtime-dependent,
transient): validates connectivity to external services that can become
available or unavailable at any time. A check at T0 does not guarantee
availability at T0+30s. These checks MUST remain per-process because:

- A process restarted by the orchestrator after a crash needs to
  revalidate independently
- Infrastructure may be temporarily down during rolling restarts
- The "let it crash + restart" model depends on each process failing
  fast on its own

Examples: PostgreSQL reachability, Redis reachability, RabbitMQ
connectivity.

### Separation of concern: primitive vs. reaction vs. orchestration

The design rests on a clean three-way separation that determines what
can be shared and what must stay per-process:

| Concern | Question it answers | Ownership |
|---------|--------------------|-----------|
| **Check primitive** | *How* is a single check performed? (e.g., "PostgreSQL reachable" = `SELECT 1` with a timeout, returning a structured pass/fail result) | **Shared** — one definition, reused everywhere |
| **Reaction** | What does a caller do when a check fails? (exit 1, return HTTP 503, log and continue) | **Per-caller** — differs by context |
| **Orchestration** | Which checks does a process run, in what order, and what state does it build afterward? (Beat reconciles, IBS consumer builds the monitored set, workers build nothing) | **Per-process** — genuinely different needs |

The key insight: check **primitives** are duplicated today (issue #3)
and can be safely unified, because a check primitive is a self-contained
"how-to-perform-the-check" function that returns a result and takes no
action. **Reaction** and **orchestration** are genuinely
context-specific and MUST stay per-process. Consolidating only the
primitives eliminates duplication without forcing unrelated processes to
share orchestration logic.

### Design: not a gatekeeper, but a shared foundation

The solution does NOT introduce a coordinator process that validates and
then launches other processes. That pattern conflicts with container
orchestration models (Docker Compose, Kubernetes) where each workload is
independently managed. Process sequencing is delegated to the
orchestrator's native primitives (`depends_on` in Compose, init
containers or Jobs in Kubernetes).

Instead, the solution rests on three pillars:

1. **A shared validation module** (`startup_checks`) that defines every
   check primitive once — the single source of truth for *how* each
   check is performed. Imported by the pre-flight job, the per-process
   Class 2 checks, and the `/ready` endpoint.

2. **A pre-flight validation job** (one-shot, like the migration job)
   that runs the full Class 1 suite plus an infrastructure smoke test at
   deploy time — catching configuration and image-level issues before
   any runtime process starts. Built entirely on the shared module.

3. **Per-process fail-fast for Class 2**, retained for each process that
   genuinely needs it, now built on the shared module's primitives
   rather than on ad-hoc duplicated code.

## Solution

### Component 1: Shared Validation Module

A new module that defines every startup check as a **primitive
function** returning a structured result — never taking action on
failure (the caller reacts).

**Location**: `backend/app/core/startup_checks.py`

**Result type**: each primitive returns a small structured result, e.g.:

```python
@dataclass(frozen=True)
class CheckResult:
    name: str        # e.g. "PostgreSQL reachable"
    ok: bool
    detail: str      # human-readable success or failure detail
```

**Primitives provided**:

| Primitive | Class | What it does | Returns `ok=False` when |
|-----------|-------|--------------|-------------------------|
| `check_settings()` | 1 | Instantiate `Settings()`, triggering all Pydantic validators (JWT key length, JWT expiry, session lifetime) | `ValidationError` raised |
| `check_logging_config()` | 1 | Validate `LOG_LEVEL` / `LOG_FORMAT` against allowed enums | Value not in allowed set |
| `check_celery_config()` | 1 | Import the Celery app module, triggering timezone + redbeat lock sentinel validation | `RuntimeError` raised at import |
| `check_cpe_mapping()` | 1 | Call `resolve_cpe_packages()` with a dummy CPE (`cpe:2.3:a:test:test:*:*:*:*:*:*:*:*`) | Mapping file missing/malformed (loader raises) |
| `check_postgres(timeout)` | 2 | Execute `SELECT 1` with the given timeout | Connection refused, timeout, or query error |
| `check_redis(timeout)` | 2 | Execute `PING` with the given timeout | Connection refused, timeout, or command error |

**Design contract**:

- A primitive **performs the check and returns a result**. It NEVER
  calls `sys.exit()`, NEVER raises for an expected failure condition
  (it captures the failure in `CheckResult.ok=False` + `detail`), and
  NEVER logs at the caller's behalf. Reaction is the caller's
  responsibility.
- Connectivity primitives (`check_postgres`, `check_redis`) accept a
  `timeout` parameter so each caller sets a context-appropriate value
  (pre-flight: 5s; `/ready`: 2s).
- The module answers issue #3 (duplicated primitives): there is now
  exactly one definition of "how to check PostgreSQL/Redis reachability."
- The module does NOT own orchestration. It does not decide which checks
  a process runs, in what order, or what a process does after checks
  pass (e.g., building the monitored codestream set). That stays
  per-process.

**Consumers**:

| Consumer | Uses | Reaction on failure |
|----------|------|---------------------|
| Pre-flight job (Component 2) | All primitives | Exit code 1 |
| IBS consumer startup | `check_postgres(5)`, `check_redis(5)` | `sys.exit(1)` |
| `/ready` endpoint | `check_postgres(2)`, `check_redis(2)` | HTTP 503 |

Beat's connectivity validation remains implicit (it fails during
reconciliation if PostgreSQL/Redis are unavailable) and is not refactored
to call the primitives — see Component 3.

### Component 2: Pre-Flight Validation Job

A new one-shot job (same image, different entrypoint) that runs after
Alembic migrations and before runtime processes. It runs the full
Class 1 suite plus an infrastructure connectivity smoke test (Class 2),
using the shared module's primitives.

**Checks performed (ordered, fail-fast)**:

1. `check_settings()` — configuration bounds
2. `check_logging_config()` — logging enums
3. `check_celery_config()` — Celery timezone + redbeat lock sentinel
4. `check_cpe_mapping()` — CPE mapping loadable. **This directly
   resolves OP-16**: the mapping is validated once, centrally, before
   any worker starts. Since the file is `COPY`-ed into the image (not
   volume-mounted), every process from the same image is guaranteed to
   have the same file.
5. `check_postgres(5)` — PostgreSQL smoke test
6. `check_redis(5)` — Redis smoke test

Checks 5–6 are technically Class 2 (dynamic, transient). They are
included as an **early warning** that catches deployment-time
configuration errors (wrong connection string, network policy blocking
access) before any runtime process starts. They do NOT replace
per-process fail-fast checks (see Component 3) — a process restarted by
the orchestrator after a crash still validates connectivity
independently.

**Location**: `backend/app/cli/preflight.py` — a Click command
registered as `sentinel preflight` in the console scripts entry point
(per CLI conventions).

**Entrypoint** (Docker):

```
sentinel preflight
```

**Exit codes**:

| Code | Meaning |
|------|---------|
| 0 | All checks passed |
| 1 | One or more checks failed (error details on stderr) |
| 2 | System error (unhandled exception during check execution) |

**Idempotency**: idempotent — safe to re-run; produces the same result
for the same image + environment. It performs no mutations (it does not
seed `system_settings` or `FetcherConfig`).

**Output format**: multi-step reporting per the CLI Output Contract
(`docs/conventions.md`, Multi-Step Reporting). Each check produces a
`✓`/`✗`/`—` line on stdout; errors go to stderr with the `Error:`
prefix. The first failure aborts remaining checks.

Success:

```
✓ Settings validation passed
✓ Logging configuration valid (level=INFO, format=auto)
✓ Celery app configuration valid (timezone=UTC, lock=enabled)
✓ CPE mapping loaded (2453 entries, 2810 package mappings)
✓ PostgreSQL reachable
✓ Redis reachable
```

Failure:

```
✓ Settings validation passed
✓ Logging configuration valid (level=INFO, format=auto)
✓ Celery app configuration valid (timezone=UTC, lock=enabled)
✗ CPE mapping: file not found at backend/app/data/cpe-package-mapping.json
— PostgreSQL connectivity not attempted (aborted due to previous error)
— Redis connectivity not attempted (aborted due to previous error)
```

**Orchestrator integration**:

Docker Compose:

```yaml
services:
  preflight:
    image: sentinel:latest
    command: ["sentinel", "preflight"]
    depends_on:
      migrations:
        condition: service_completed_successfully
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  api:
    depends_on:
      preflight:
        condition: service_completed_successfully
  worker:
    depends_on:
      preflight:
        condition: service_completed_successfully
  # ... same for beat, git-worker, ibs-consumer
```

Kubernetes: implemented as an init container on each Deployment/Job, or
as a separate Job with dependency ordering via Helm hooks or Argo
Workflows.

### Component 3: Per-Process Fail-Fast (Class 2)

Each process retains its own dynamic infrastructure checks — the
pre-flight job does NOT replace them. The pre-flight catches deploy-time
issues; per-process checks catch runtime issues (process restart,
infrastructure outage, rolling update). Where a process performs an
explicit connectivity check, it now calls the shared module's primitive
(Component 1) instead of duplicating the logic.

**Workers** (general and git): no explicit infrastructure check is
added. Workers accept tasks from the Celery broker and fail at the first
database query if PostgreSQL is unreachable. This is acceptable because:

- Workers are stateless task consumers; they do not build initial state
  from the database before accepting tasks
- A worker with unreachable PostgreSQL fails the first task (within
  seconds of deployment), and Celery retries when the worker restarts
- The pre-flight already caught configuration errors at deploy time;
  runtime failures are transient and self-recovering via orchestrator
  restarts
- Adding a `worker_init` connectivity handler would add complexity for
  marginal benefit

**Beat**: retains its existing implicit infrastructure validation
(PostgreSQL read + Redis write during reconciliation, fail-fast via
`sys.exit(1)` in the `beat_init` handler). Beat is not refactored to
call the primitives because its validation is a side effect of
reconciliation (it needs to read `FetcherConfig` and write redbeat
entries anyway) — an explicit pre-check would be redundant.

**IBS consumer**: retains its explicit PostgreSQL + Redis connectivity
checks, now implemented via `check_postgres(5)` / `check_redis(5)` from
the shared module. Reaction (`sys.exit(1)`) and subsequent orchestration
(building the monitored codestream set) are unchanged.

**API server**: retains the FastAPI `lifespan` event for
`system_settings` seeding (defense-in-depth) and
`bootstrap_fetcher_configs()`. The CPE mapping validation in the
`lifespan` event is **removed** — now handled by the pre-flight job (see
Component 4).

**`/ready` endpoint**: refactored to call `check_postgres(2)` /
`check_redis(2)` from the shared module. Observable behavior is
unchanged (same checks, same 2-second timeout, same 503 reaction, same
per-instance Redis discovery) — this is an internal consolidation that
removes the duplicated primitive definition.

### Component 4: Cleanup of Redundant Validations

With the shared module and pre-flight job in place:

1. **CPE mapping validation in FastAPI lifespan**: **removed**. The
   pre-flight job validates the mapping before any process starts. The
   lazy-load pattern (`lru_cache`) in `resolve_cpe_packages()` is
   unchanged — it still loads on first use in the worker. Only the
   *fail-fast at boot* responsibility moves from the API lifespan to the
   pre-flight job.

2. **`cve-service.md` reasoning about lifespan validation**: **updated**
   to reference the pre-flight job. The semantic argument is unchanged:
   an exception from `resolve_cpe_packages()` at task runtime indicates a
   programming bug because the mapping was already validated at boot.

3. **Celery app factory validations**: **retained** in the factory
   (imported by every Celery-based process). The pre-flight also
   triggers them via `check_celery_config()`. Redundant but harmless —
   the factory validation is the per-process safety net for runtime
   restarts.

4. **Settings instantiation per-process**: `Settings()` is instantiated
   in every process anyway (via `from app.config import settings`). The
   pre-flight validates it first via `check_settings()`; per-process
   instantiation is redundant but harmless (Pydantic validators are
   idempotent). No change needed.

### Diagram: Startup Flow After Change

```
                    ┌─────────────────────┐
                    │  Alembic Migrations  │  one-shot job
                    │  (schema + seed)     │
                    └──────────┬──────────┘
                               │ success
                    ┌──────────▼──────────┐
                    │  Pre-Flight Job      │  one-shot job (NEW)
                    │  sentinel preflight  │
                    │                     │
                    │  uses startup_checks:│
                    │  1 check_settings   │
                    │  2 check_logging    │
                    │  3 check_celery     │
                    │  4 check_cpe_mapping│
                    │  5 check_postgres(5)│
                    │  6 check_redis(5)   │
                    └──────────┬──────────┘
                               │ success
              ┌────────────────┼─────────────────┐
              │                │                  │
    ┌─────────▼──────┐ ┌──────▼───────┐ ┌───────▼────────┐
    │   API Server   │ │ Celery Worker│ │  Celery Beat   │
    │                │ │              │ │                │
    │ Celery import: │ │ Celery import│ │ Celery import  │
    │  (n/a)         │ │ · tz check   │ │ · tz check     │
    │ lifespan:      │ │ · lock check │ │ · lock check   │
    │ · settings seed│ │              │ │                │
    │ · bootstrap_fc │ │ bootstrap_fc │ │ beat_init:     │
    │ /ready uses    │ │              │ │ · bootstrap_fc │
    │  startup_checks│ │              │ │ · reconcile    │
    └────────────────┘ └──────────────┘ │  (implicit PG/ │
                                        │   Redis check) │
                                        └────────────────┘
    ┌────────────────┐ ┌──────────────────────────────┐
    │  Git Worker    │ │       IBS Consumer            │
    │                │ │                              │
    │ (same as       │ │ Celery import: tz + lock     │
    │  Celery worker)│ │ check_postgres(5) ┐ shared   │
    │                │ │ check_redis(5)    ┘ module   │
    │                │ │ build monitored codestream   │
    │                │ │ set (per-process)            │
    └────────────────┘ └──────────────────────────────┘

Legend: startup_checks = shared validation module (Component 1)
```

### Invariant Update

The OP-18 startup ordering invariant (`deployment.md`, Startup Ordering)
is **refined**, not broken:

- **Current**: "After Alembic migrations complete, all runtime processes
  MAY start in any order."
- **Updated**: "After Alembic migrations and the pre-flight validation
  job complete, all runtime processes MAY start in any order."

The pre-flight job is a second sequential gate (like migrations) — it
runs after migrations and before runtime processes. Once it completes,
the order-independence property holds unchanged.

## Coverage: issue → resolution

Every identified issue maps to a solution component:

| Issue | Resolved by |
|-------|-------------|
| #1 OP-16 CPE asymmetry | Component 2 (pre-flight validates CPE once, centrally) + Component 4 (remove lifespan CPE check) |
| #2 Mechanism fragmentation | Component 1 (shared primitives) + Component 2 (single ordered Class 1 suite) |
| #3 Duplicated connectivity primitives | Component 1 (one definition of `check_postgres`/`check_redis`, reused by pre-flight, IBS consumer, `/ready`) |
| #4 Implicit vs. explicit infra checks | Component 2 (pre-flight covers the deploy-time gap uniformly for all processes) + Component 3 (per-process runtime checks retained where genuinely needed, built on shared primitives) |

## Impact on OP-16

OP-16 asked: "should the CPE mapping be validated in the worker, in the
API server, or everywhere?"

This decision resolves the question differently: **validate it once in a
dedicated pre-flight job, before any process starts**. The asymmetry
disappears because:

- The pre-flight validates the mapping from the same image that all
  processes use
- Workers no longer need a `worker_init` signal handler for CPE
  validation
- The API server lifespan no longer carries the CPE validation
  responsibility
- The lazy-load pattern (`lru_cache`) is unchanged — it handles runtime
  loading, not validation

OP-16 status changes from Open to Resolved with a reference to this
decision record.

---

## Action Plan

Prescriptive list of spec modifications. This project is in the
specification phase — there is no implementation code or database to
migrate. All changes are to specifications only. Each step specifies the
exact file, section, and nature of the change.

### Step 1: Create the startup validation specification

**File**: `docs/features/platform/startup-validation.md` (new file)

This spec owns BOTH the shared validation module and the pre-flight job.

**Content**:

- Summary and purpose
- Cross-references to: `docs/conventions.md` (CLI Output Contract,
  Multi-Step Reporting; Function Specification Completeness),
  `docs/deployment.md`, `docs/architecture.md`,
  `docs/features/platform/cli-infrastructure.md`,
  `docs/features/platform/health-endpoints.md`,
  `docs/features/packages/cpe-package-mapping.md`
- **Two classes of validation** (Class 1 static / Class 2 dynamic) — the
  conceptual foundation
- **Separation of concern** (primitive / reaction / orchestration) — the
  rule that determines what is shared vs. per-process
- **Shared validation module** (`backend/app/core/startup_checks.py`):
  - The `CheckResult` result type
  - Each primitive documented per the Function Specification
    Completeness convention. Because primitives capture expected
    failures in `CheckResult` (rather than raising) and mutate no state,
    document for each: Q1 (inputs, incl. `timeout` where applicable),
    Q3 (behavior in every case, including how each failure mode is
    captured in `CheckResult`), and Q6 (only unexpected/system
    exceptions propagate; expected failures do not). Q2/Q4/Q5 are not
    applicable (no guards that reject before mutation, no audit events,
    idempotent with no state mutation)
  - The design contract (primitives return results, never react, never
    exit, never log on the caller's behalf)
  - The consumers table (pre-flight, IBS consumer, `/ready`) with
    per-caller timeout and reaction
- **Pre-flight job** (`sentinel preflight`):
  - CLI command definition: parameters (none), the ordered checklist
    (checks 1–6), behavior, exit codes (0/1/2), idempotency declaration
    (idempotent; no mutations), output channels (stdout `✓`/`✗`/`—`,
    stderr `Error:`), fail-fast semantics
  - Orchestrator integration (Compose `depends_on`, Kubernetes init
    containers/Jobs)
- **Per-process fail-fast** (Class 2): summary of which processes run
  which checks and how they react, referencing the owning specs
  (IBS consumer → `ibs-rabbitmq-integration.md`; Beat →
  `fetcher-infrastructure.md`; `/ready` → `health-endpoints.md`) as the
  authoritative sources for each process's orchestration
- **Startup ordering** cross-reference to `deployment.md`

### Step 2: Update `docs/deployment.md`

**Section**: Startup Ordering

- Update the invariant text: "After Alembic migrations **and the
  pre-flight validation job** complete, all runtime processes [...] MAY
  start in any order."
- Add a bullet describing the pre-flight job's role (validates Class 1
  static resources: configuration bounds, Celery config, CPE mapping)
- Add a note that the pre-flight also performs an early PostgreSQL/Redis
  smoke test, which does NOT replace per-process fail-fast checks

**Section**: Process Architecture

- Add the pre-flight job to the process table as a one-shot job
  (alongside the Alembic migration job)
- Update the note that currently says "Alembic migration jobs are
  one-shot processes" to mention both one-shot jobs

**Section**: Database Migrations

- Add a paragraph noting that a pre-flight validation job runs between
  migrations and runtime processes. Reference
  `docs/features/platform/startup-validation.md`

### Step 3: Update `docs/architecture.md`

**Section**: Container Images (One-shot jobs list)

- Add "Pre-flight validation job" to the one-shot jobs list, alongside
  the Alembic migration job

### Step 4: Update `docs/features/packages/cpe-package-mapping.md`

**Section**: Resolution Function, Loading

- Remove the paragraph mandating the FastAPI `lifespan` event to call
  `resolve_cpe_packages()` with a dummy CPE string
- Replace with: the fail-fast property is ensured by the pre-flight
  validation job (`sentinel preflight`), which calls
  `resolve_cpe_packages()` with a dummy CPE string before any runtime
  process starts. Reference
  `docs/features/platform/startup-validation.md`
- Retain the lazy-init test-ergonomics rationale (still applies)
- Retain the "Operational semantics" paragraph (read-once-per-process is
  unchanged)

### Step 5: Update `docs/features/tickets/cve-service.md`

**Section**: Phase 2 error handling

- Update the passage that says the CPE mapping "is loaded and validated
  once at application startup via the lifespan event" to reference the
  pre-flight validation job instead. The semantic argument (runtime
  exception = programming bug) is unchanged; only the validation locus
  changes. Reference `docs/features/platform/startup-validation.md`

### Step 6: Update `docs/features/platform/fetcher-infrastructure.md`

**Section**: Multi-Process Coordination → Startup Ordering

- Update the cross-reference text from "after Alembic migrations
  complete" to "after Alembic migrations and the pre-flight validation
  job complete"

### Step 7: Update `docs/features/integrations/ibs-rabbitmq-integration.md`

**Section**: Process Startup, Complete Startup Sequence

- Add a note that the pre-flight validation job has already validated
  static configuration (Celery timezone, lock sentinel, CPE mapping,
  Settings bounds) by the time the consumer starts
- Update step 3 (Infrastructure connectivity check) to state that the
  PostgreSQL and Redis checks are performed via the shared
  `check_postgres(5)` / `check_redis(5)` primitives from
  `backend/app/core/startup_checks.py`. The reaction (`exit 1`) and the
  subsequent monitored-codestream-set build (step 4) are unchanged.
  Reference `docs/features/platform/startup-validation.md` for the
  primitive definitions

### Step 8: Update `docs/features/platform/health-endpoints.md`

**Section**: Readiness — GET /ready, Checks performed

- Add a note that the PostgreSQL and Redis checks are performed via the
  shared `check_postgres(2)` / `check_redis(2)` primitives from
  `backend/app/core/startup_checks.py`. Observable behavior is unchanged
  (same checks, same 2-second timeout, same 503 reaction, same
  per-instance Redis discovery). Reference
  `docs/features/platform/startup-validation.md` for the primitive
  definitions

### Step 9: Update `docs/cli-reference.md`

- Add the `sentinel preflight` command: command name, purpose,
  parameters (none), exit codes (0/1/2), and a reference to
  `docs/features/platform/startup-validation.md`

### Step 10: Update `docs/configuration.md`

**Section**: Notes for Operators (Startup validation note)

- Update the startup validation note to mention that a dedicated
  pre-flight validation job (`sentinel preflight`) consolidates all
  static validations (configuration bounds, Celery config, CPE mapping)
  plus an infrastructure smoke test, and runs before any runtime process
  starts. Reference `docs/features/platform/startup-validation.md`

### Step 11: Update `docs/drafts/open-points.md`

**Section**: OP-16 entry + summary table

- Move OP-16 from "Open — Cross-Process Startup" to "Archive — Resolved"
- Add resolution text:

  > **Resolution**: resolved by the Startup Validation Architecture
  > (shared validation module + pre-flight validation job). The CPE
  > mapping is validated once by `sentinel preflight` before any runtime
  > process starts, eliminating the asymmetry between API server
  > (validator) and worker (consumer). The FastAPI lifespan event no
  > longer validates the CPE mapping. Per-process fail-fast checks remain
  > for dynamic infrastructure dependencies, now built on shared
  > primitives. See `docs/features/platform/startup-validation.md`.

- Update the summary table: change OP-16 status from `Open` to
  `Resolved`

### Step 12: Run reviewers on affected specs

After applying steps 1–11:

1. **`@spec-gap-analyzer`** on
   `docs/features/platform/startup-validation.md` — verify no functional
   gaps (missing error paths, boundary conditions, concurrency
   scenarios) in the new spec

2. **`@design-reviewer`** on
   `docs/features/platform/startup-validation.md` — verify the
   architectural decision (shared module + pre-flight job + per-process
   fail-fast, with the primitive/reaction/orchestration separation) is
   sound

3. **`@spec-coherence-reviewer`** on each of the following (one
   invocation per spec, independent sessions):
   - `docs/features/platform/startup-validation.md`
   - `docs/features/packages/cpe-package-mapping.md`
   - `docs/features/tickets/cve-service.md`
   - `docs/features/platform/fetcher-infrastructure.md`
   - `docs/features/integrations/ibs-rabbitmq-integration.md`
   - `docs/features/platform/health-endpoints.md`

4. **`@docs-placement-reviewer`** — verify the shared validation module
   is documented in the right place (single owner: the new spec) and
   that references from `ibs-rabbitmq-integration.md` and
   `health-endpoints.md` do not duplicate the primitive definitions

5. **`@docs-reviewer`** — verify documentation completeness and
   coherence across all modified files

Address any "Needs revision" findings before considering the change
complete; fix "Minor issues" in the same pass.

### Step 13: Register the new spec in the review tracking

Add the new spec to the review-tracking artifacts so it is tracked as
**enabled** but **not yet manually reviewed** via the `/review-spec`
command. The `@`-agent reviewers run in Step 12 are unrelated to the
manual `/review-spec` pipeline recorded here — so the spec is correctly
registered as never manually reviewed. The changes follow the tracking
conventions in `.opencode/commands/review-spec/tracking-format.md` and
`.opencode/commands/review-spec/readme-layout.md`.

**File 1** — `docs/reviews/.tracking.json`: insert the following entry in
alphabetical position (between `sso-authentication` and
`system-settings`):

```json
"startup-validation": {
  "enabled": true,
  "abbr": "SVAL",
  "cache": null
}
```

- `enabled: true` — the spec is tracked (auto-discovery would otherwise
  add a newly discovered spec as `enabled: false`)
- No `path` field — it is an ordinary feature spec under
  `docs/features/**/`, so its path is derived automatically
- `cache: null` — never manually reviewed yet; this is the
  representation that renders as `—` in the README (per
  `readme-layout.md`, Stale column: "`cache` is `null` (never reviewed)
  → `—`")

**File 2** — `docs/reviews/README.md`: insert the two-row block (main row
+ empty severity sub-row) in the main table, in alphabetical position
(between the `rbac` row and the `system-settings` row):

```
| [startup-validation](startup-validation.md) | — | — | — | — | — | 0/0 |  | — |
|  |  |  |  |  |  |  |  |  |
```

- Reviewer columns (GAP/COH/DES/SEC/API): `—` (never reviewed)
- Open: `0/0`
- Last Review: empty (no review date yet)
- Stale: `—` (never reviewed)
- The **Total** row is unchanged (the entry contributes 0 findings)
- The spec is NOT added to the "Disabled specs" list (it is enabled)

### Step 14: Delete this draft

After all spec changes are applied and reviewers confirm no outstanding
issues, delete this file
(`docs/drafts/startup-validation-architecture.md`).

---

## Cross-References

- `docs/drafts/open-points.md` — OP-16 (CPE Mapping Fail-Fast
  Asymmetry), OP-18 (Cross-Process Startup Ordering — resolved, refined
  by this change)
- `docs/features/packages/cpe-package-mapping.md` — CPE mapping loading
  and fail-fast guard
- `docs/features/tickets/cve-service.md` — Phase 2 error handling
  referencing startup validation
- `docs/features/platform/fetcher-infrastructure.md` — Celery app
  factory validations, Beat startup sequence, startup ordering
- `docs/features/integrations/ibs-rabbitmq-integration.md` — IBS
  consumer startup sequence and connectivity checks
- `docs/features/platform/health-endpoints.md` — `/ready` connectivity
  checks
- `docs/features/identity/authentication.md` — JWT configuration bounds
- `docs/features/platform/logging.md` — LOG_LEVEL/LOG_FORMAT validation
- `docs/features/platform/system-settings.md` — `system_settings`
  seeding (unchanged; remains an Alembic + lifespan responsibility)
- `docs/features/platform/cli-infrastructure.md` — CLI shared
  implementation mechanism
- `docs/deployment.md` — startup ordering invariant, process
  architecture
- `docs/architecture.md` — container images and process roles
- `docs/configuration.md` — startup validation notes
- `docs/cli-reference.md` — CLI command reference
- `docs/conventions.md` — CLI Output Contract (Multi-Step Reporting,
  Exit Codes); Function Specification Completeness
