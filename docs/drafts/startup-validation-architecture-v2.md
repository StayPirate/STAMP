# Startup Validation Architecture (v2)

Decision record for consolidating and rationalizing process startup
validations across all Sentinel runtime processes. Resolves OP-16 (CPE
Mapping Fail-Fast Asymmetry).

**This document supersedes `startup-validation-architecture.md` (v1).**
It adopts a reduced design that keeps v1's sound core (a shared
validation module + per-process fail-fast) and discards v1's pre-flight
validation job as the OP-16 mechanism, resolving OP-16 instead with a
worker-side `worker_init` fail-fast guard. See "Why v2 Supersedes v1"
below for the full rationale.

## Why v2 Supersedes v1

v1 proposed three things to resolve OP-16 and rationalize startup
validation:

1. A shared validation module (`startup_checks`) defining every check as
   a pure primitive returning a `CheckResult`.
2. A new one-shot **pre-flight validation job** (`sentinel preflight`)
   that runs after migrations and before runtime processes, executing an
   ordered suite of six checks (settings, logging, celery, CPE mapping,
   PostgreSQL, Redis).
3. **Removing** the CPE mapping validation from the FastAPI `lifespan`
   event, delegating fail-fast entirely to the pre-flight job.

Before propagating v1 to the formal spec and its ~11 dependent
documents, the decision was pre-validated with the `@design-reviewer`.
The review returned a **"Reconsider design"** verdict. Two of its three
critical findings were confirmed directly against the current specs:

**Finding 1 — The pre-flight job does not resolve OP-16's actual edge
cases.** OP-16 is about the *non-same-file* scenario: a misconfigured
volume mount or a stale image on the **worker** specifically
(`open-points.md`, OP-16, "Impact"). The pre-flight job runs in a
**separate container** from the worker, so it validates its own
image-baked file — not the file the worker actually loads. The exact
scenario OP-16 enumerates (broken file mounted on the worker only, or
image skew during a rolling update) slips through. The pre-flight
resolves OP-16 *only* under the same-image/no-mount assumption — which
is precisely the assumption under which OP-16 was already "low-risk."

**Finding 2 — Removing the lifespan CPE check regresses local and
direct-run deployments (confirmed).** The project's own Quick Start
(`deployment.md:100-107`) starts `uvicorn` and `celery` directly, with
no pre-flight job. After v1's removal, a hand-edited malformed
`cpe-package-mapping.json` would no longer be caught at boot in the
environment where the file is edited most often. It also weakens
`cve-service.md`'s Phase 2 reasoning (`cve-service.md:488-497`, "an
exception ... indicates a programming bug"), because in a direct-run or
skewed deployment *neither* the API nor the worker would validate.

**Finding 3 — Two-to-three pre-flight checks cannot deliver their
specified behavior inside the CLI harness (confirmed).** The pre-flight
was a Click subcommand of the root `sentinel` group. Per
`cli-infrastructure.md:77-82`, the root group loads `Settings` and, on
failure, exits with **code 2 (system error) before dispatching to any
subcommand**. Because `LOG_LEVEL`/`LOG_FORMAT` and the JWT/session
bounds are `Settings` fields, `check_settings()` and
`check_logging_config()` could never reach their intended `✗` output or
their exit-code-1 contract — the process would already have died with
exit 2.

Additional minor findings: `CheckResult` (a bool) is too lossy to
preserve `/ready`'s three-value output (`ok`/`unreachable`/`timeout`)
and its worst-across-instances rule; the "two classes of validation"
taxonomy silently omitted the SUSE CA trust store (a Class 1 resource
that degrades silently if missing); the Kubernetes realization of the
pre-flight forced a contradiction between "validate once, centrally" and
the OP-18 order-independence invariant; and the v1 diagram incorrectly
showed the API server as not importing the Celery app (it does, to
enqueue on-demand fetches).

### The decision

Adopt the reviewer's **reduced path**:

- **Keep** v1's sound core: the shared validation module (Component 1)
  and per-process fail-fast built on it (Component 3).
- **Resolve OP-16** with option (b) from the open point itself — a
  Celery `worker_init` fail-fast handler that validates the CPE mapping
  **in the worker process**, mirroring the already-accepted `beat_init`
  pattern (OP-19). **Retain** the FastAPI lifespan CPE guard as
  defense-in-depth. This validates the worker's own file in every
  deployment model and *strengthens* `cve-service.md`'s reasoning
  instead of weakening it.
- **Demote** the pre-flight validation job to an optional future
  follow-up, justified solely by its one legitimate merit (a
  consolidated deploy-time report) — never as the OP-16 fix, and never
  as a replacement for any always-on guard.

### Accepted trade-off

The reduced path fully resolves OP-16 (issue #1) and the duplicated
connectivity primitives (issue #3). It **deliberately** forgoes the
uniform deploy-time smoke test that v1's pre-flight job provided for
issues #2 (mechanism fragmentation) and #4 (implicit vs. explicit infra
checks). This is a conscious choice: the pre-flight *job* introduced
more defects (Findings 1–3) than the value it added for #2/#4, and #2/#4
are lower-severity "rationalization" concerns rather than correctness
bugs. Should the consolidated deploy-time report prove valuable in
operation, the pre-flight job can be added later as an **additive**
component (see "Deferred" below).

Net effect: OP-16 is resolved more correctly, no environment loses
fail-fast, the CLI-harness contradiction disappears, and the
propagation shrinks from ~11 documents to ~6.

## Problem Statement

Sentinel runs five runtime processes from the same Docker image (API
server, Celery worker, git worker, Celery Beat, IBS RabbitMQ consumer)
plus a one-shot migration job. Startup validations are performed through
several mechanisms with inconsistent coverage. Two concrete problems
motivate this decision record:

1. **OP-16 — CPE mapping fail-fast asymmetry**: the CPE-to-package
   mapping (`cpe-package-mapping.json`) is validated at boot only in the
   API server (via the FastAPI `lifespan` event), but its actual
   consumers are **Celery worker** tasks (`resolve_ticket_packages`,
   `fetch_single_cve`), which load it lazily (`lru_cache`) on first use.
   A corrupted or missing file mounted on the worker would be detected at
   API boot but surface in the worker only when the first CVE ingestion
   task runs — potentially hours later.

2. **Duplicated connectivity primitives**: the PostgreSQL `SELECT 1`
   check and the Redis `PING` check are independently specified in at
   least two places — the IBS consumer startup sequence
   (`ibs-rabbitmq-integration.md`, Process Startup, step 3) and the
   `/ready` readiness endpoint (`health-endpoints.md`, Readiness) — each
   with its own timeout and error handling. There is no single
   definition of "how to check PostgreSQL/Redis reachability."

The full inventory of startup validations and the broader "mechanism
fragmentation" analysis are documented in v1's Problem Statement and are
not repeated here; v2 addresses the two correctness-relevant problems
above and consciously defers the broader rationalization (see "Accepted
trade-off").

## Design Principles

### Two classes of validation

Startup validations fall into two categories with fundamentally
different properties:

**Class 1 — Static resource validation** (image-baked or env-derived,
deterministic): validates resources embedded in the Docker image or
derived from environment variables. Deterministic: if the check passes
once for a given image + environment, it passes for every process
started from the same image with the same environment.

Examples: CPE mapping file loadable, configuration bounds (JWT key
length, expiry range, session lifetime), Celery timezone, logging
format, redbeat lock config, and the SUSE CA trust store
(`SUSE_CA_CERT_PATH`).

**Class 2 — Dynamic infrastructure validation** (runtime-dependent,
transient): validates connectivity to external services that can become
available or unavailable at any time. A check at T0 does not guarantee
availability at T0+30s. These checks MUST remain per-process because a
process restarted by the orchestrator after a crash needs to revalidate
independently, infrastructure may be temporarily down during rolling
restarts, and the "let it crash + restart" model depends on each process
failing fast on its own.

Examples: PostgreSQL reachability, Redis reachability, RabbitMQ
connectivity.

**Scope note (validations vs. mutations)**: this two-class model frames
read-only *validations* only. Startup **mutations / state-building** —
`system_settings` seeding, `bootstrap_fetcher_configs()`, Beat
reconciliation, and the IBS consumer's monitored-codestream-set build —
are a separate concern owned by per-process **orchestration** (below),
not by this validation model. They are neither Class 1 nor Class 2.

**Class 1 coverage note (SUSE CA)**: by the definition above, the SUSE
CA trust store is a Class 1 resource, and arguably a more critical one
than the CPE mapping — if `SUSE_CA_CERT_PATH` is missing, the combined
trust store silently falls back to system CAs only
(`networking.md`), after which every `*.suse.de` fetcher (IBS, SMELT,
AIMAAS, RabbitMQ) fails TLS at runtime. This decision record does **not**
add a SUSE CA startup check (it is out of scope for resolving OP-16), but
records the gap explicitly so a future iteration can address it with the
same worker-side fail-fast pattern used here for the CPE mapping. See
"Deferred" and "Known gaps."

### Separation of concern: primitive vs. reaction vs. orchestration

The design rests on a clean three-way separation that determines what
can be shared and what must stay per-process:

| Concern | Question it answers | Ownership |
|---------|--------------------|-----------|
| **Check primitive** | *How* is a single check performed? (e.g., "PostgreSQL reachable" = `SELECT 1` with a timeout, returning a structured result) | **Shared** — one definition, reused everywhere |
| **Reaction** | What does a caller do when a check fails? (exit 1, return HTTP 503, log and continue) | **Per-caller** — differs by context |
| **Orchestration** | Which checks does a process run, in what order, and what state does it build afterward? (Beat reconciles, IBS consumer builds the monitored set, workers build nothing) | **Per-process** — genuinely different needs |

The key insight: check **primitives** are duplicated today (the
PostgreSQL/Redis connectivity checks) and can be safely unified, because
a check primitive is a self-contained "how-to-perform-the-check"
function that returns a result and takes no action. **Reaction** and
**orchestration** are genuinely context-specific and MUST stay
per-process. Consolidating only the primitives eliminates duplication
without forcing unrelated processes to share orchestration logic.

**Primitive scope (deliberately narrow)**: only checks that resolve a
*real* duplication become shared primitives — the connectivity checks
(`check_postgres`, `check_redis`) and the CPE mapping check
(`check_cpe_mapping`, which will have multiple call sites after this
change: the worker `worker_init` handler and the API lifespan guard).
Configuration-bounds validation (`Settings` Pydantic validators),
logging-enum validation, and Celery-config validation are **not** turned
into shared primitives: they are single-caller, they already fire
correctly via their existing mechanisms (Pydantic on `Settings()`
instantiation; the Celery app factory on import), and — as Finding 3
showed — wrapping them as pre-flight primitives inside the CLI harness
would misreport their exit codes. Generalizing them would be
generalization without a duplication problem.

### Not a gatekeeper, and not a new process role

The solution does NOT introduce a coordinator/gatekeeper process, and —
unlike v1 — it does NOT introduce a new one-shot process role. Process
sequencing remains delegated to the orchestrator's native primitives
(`depends_on` in Compose, init containers/Jobs in Kubernetes) exactly as
today. The OP-18 startup-ordering invariant (`deployment.md`, Startup
Ordering) is **unchanged** by this decision — no second sequential gate
is added.

## Solution

### Component 1: Shared Validation Module

A new module that defines each shared startup check as a **primitive
function** returning a structured result — never taking action on
failure (the caller reacts).

**Location**: `backend/app/core/startup_checks.py`

**Result type**: each primitive returns a small structured result. Unlike
v1's bool-only `CheckResult`, the status is a three-way enum so that
`/ready` can map it directly to its documented output values and its
worst-across-instances rule (resolving v1's lossiness finding):

```python
class CheckStatus(StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"  # connectivity: refused / non-timeout error
    TIMEOUT = "timeout"          # connectivity: no response within timeout
    INVALID = "invalid"          # static resource: missing / malformed

@dataclass(frozen=True)
class CheckResult:
    name: str            # e.g. "PostgreSQL reachable"
    status: CheckStatus
    detail: str          # human-readable success or failure detail

    @property
    def ok(self) -> bool:
        return self.status == CheckStatus.OK
```

`CheckStatus` is a **classification enum** (Category B per the Enum
Storage Strategy) — no database column, no CHECK constraint, defined in
`app/core/enums.py` per the convention. It is referenced here for
completeness.

**Primitives provided**:

| Primitive | Class | What it does | Non-OK result |
|-----------|-------|--------------|---------------|
| `check_postgres(timeout)` | 2 | Execute `SELECT 1` with the given timeout | `TIMEOUT` (no response in time), `UNREACHABLE` (refused / other error) |
| `check_redis(timeout)` | 2 | Execute `PING` with the given timeout | `TIMEOUT`, `UNREACHABLE` |
| `check_cpe_mapping()` | 1 | Call `resolve_cpe_packages()` with a dummy CPE (`cpe:2.3:a:test:test:*:*:*:*:*:*:*:*`) | `INVALID` (loader raises: file missing / malformed) |

**Design contract**:

- A primitive **performs the check and returns a result**. It NEVER
  calls `sys.exit()`, NEVER raises for an expected failure condition (it
  captures the failure in `CheckResult` with a non-OK status + `detail`),
  and NEVER logs on the caller's behalf. Reaction is the caller's
  responsibility.
- Expected failures captured (not raised): for the connectivity
  primitives, connection-refused / timeout / command errors; for
  `check_cpe_mapping`, the loader's file-missing / malformed-JSON errors.
  Only genuinely unexpected exceptions (e.g., `MemoryError`) propagate.
- Connectivity primitives accept a `timeout` parameter so each caller
  sets a context-appropriate value (IBS consumer: 5s; `/ready`: 2s).
- `check_cpe_mapping()` centralizes the "dummy CPE" idiom so its single
  owner is this module (both the worker `worker_init` handler and the API
  lifespan guard call it — resolving v1's NH-2 drift concern).

**Consumers**:

| Consumer | Uses | Reaction on failure |
|----------|------|---------------------|
| Worker `worker_init` handler (Component 2) | `check_cpe_mapping()` | `sys.exit(1)` |
| API server lifespan guard (Component 2) | `check_cpe_mapping()` | raise → uvicorn aborts startup |
| IBS consumer startup (Component 3) | `check_postgres(5)`, `check_redis(5)` | `sys.exit(1)` |
| `/ready` endpoint (Component 3) | `check_postgres(2)`, `check_redis(2)` | HTTP 503 (maps `CheckStatus` to response) |

Beat's connectivity validation remains implicit (it fails during
reconciliation if PostgreSQL/Redis are unavailable) and is not
refactored to call the primitives — see Component 3.

### Component 2: OP-16 Resolution — Worker-Side Fail-Fast + Retained Lifespan Guard

OP-16 is resolved by validating the CPE mapping **in the process that
consumes it** (the worker), while retaining the always-on API guard as
defense-in-depth.

**Worker `worker_init` handler (NEW)**:

- **Location**: `backend/app/core/worker_init.py`
- **Registration**: connected to the Celery `worker_init` signal at
  module import time (`@worker_init.connect`), imported by the Celery app
  module — mirroring the existing `beat_init` handler
  (`fetcher-infrastructure.md`, Startup Reconciliation).
- **Behavior**: calls `check_cpe_mapping()`. If the result is not OK, it
  logs the failure detail at ERROR level and calls `sys.exit(1)`
  (explicit fail-fast), preventing the worker from accepting any task.
  If OK, the worker proceeds normally (the `lru_cache` is now warm for
  the parent process).
- **Scope**: the handler validates only the CPE mapping. It does NOT
  duplicate the Celery-config validation, which is already inherited by
  every Celery-based process via Celery app import (see the coherence
  note for `fetcher-infrastructure.md` in the Action Plan).

**Why `worker_init` and not Beat/IBS consumer**: the CPE mapping is
consumed exclusively by worker tasks. Beat and the IBS consumer never
call `resolve_cpe_packages()`, so they need no CPE guard. This targets
exactly the consumers OP-16 identified.

**Retained API lifespan guard**:

- The FastAPI `lifespan` event **keeps** its CPE fail-fast guard, now
  implemented via `check_cpe_mapping()` from the shared module (instead
  of an inline dummy-CPE call). This preserves fail-fast for the API
  server and for local/direct-run deployments (Quick Start), addressing
  v1 Finding 2.

**Effect on OP-16**: the asymmetry disappears. The worker validates its
own file, in its own process, in every deployment model
(COPY-in-image, volume mount, image skew during rolling updates). The
`cve-service.md` reasoning ("runtime exception = programming bug") is
*strengthened*: the worker has validated the exact file it will use,
before accepting any task.

### Component 3: Per-Process Fail-Fast (Class 2)

Each process retains its own dynamic infrastructure checks; where a
process performs an explicit connectivity check, it now calls the shared
module's primitive instead of duplicating the logic.

**IBS consumer**: retains its explicit PostgreSQL + Redis connectivity
checks (`ibs-rabbitmq-integration.md`, Process Startup, step 3), now
implemented via `check_postgres(5)` / `check_redis(5)`. Reaction
(`sys.exit(1)`) and subsequent orchestration (building the monitored
codestream set) are unchanged.

**`/ready` endpoint**: refactored to call `check_postgres(2)` /
`check_redis(2)`. Observable behavior is unchanged — same checks, same
2-second timeout, same 503 reaction, same per-instance Redis discovery,
same worst-across-instances aggregation. The `CheckStatus` enum maps
directly onto the endpoint's documented three-value output
(`ok`/`unreachable`/`timeout`), so no information is lost (resolving v1's
lossiness finding). This is an internal consolidation that removes the
duplicated primitive definition.

**Beat**: retains its existing implicit infrastructure validation
(PostgreSQL read + Redis write during reconciliation, fail-fast via
`sys.exit(1)` in the `beat_init` handler). Not refactored to call the
primitives, because its validation is a side effect of reconciliation
(it must read `FetcherConfig` and write redbeat entries anyway) — an
explicit pre-check would be redundant.

**Workers**: gain the `worker_init` CPE guard (Component 2). No explicit
connectivity check is added — a worker with unreachable PostgreSQL fails
the first task (within seconds of deployment) and Celery retries when the
worker restarts. Adding a `worker_init` connectivity handler would add
complexity for marginal benefit.

**API server**: retains the FastAPI `lifespan` event for
`system_settings` seeding (defense-in-depth) and
`bootstrap_fetcher_configs()`, plus the retained CPE guard (Component 2).

### Deferred: Pre-Flight Validation Job (optional future follow-up)

A one-shot pre-flight job that runs a consolidated validation suite at
deploy time (before runtime processes) is **not** part of this decision.
Its only legitimate merit is operator UX: a single consolidated
deploy-time `✓`/`✗` report. It is recorded here as a possible future
addition, with mandatory constraints derived from the v1 review:

1. **Additive only**: it MUST NOT remove any per-process or lifespan
   guard. It is an early-warning convenience, never a correctness
   mechanism.
2. **Exclude Settings/logging checks**: `Settings`/`LOG_LEVEL`/
   `LOG_FORMAT` validation is owned by the CLI bootstrap
   (`cli-infrastructure.md:77-82`), which exits with code 2 before any
   subcommand body runs. A pre-flight Click subcommand cannot report
   these as `✗` + exit 1. Either run the pre-flight as a standalone
   `__main__` script (not a root-group subcommand) or drop those checks.
3. **Choose one Kubernetes model and own its trade-off**: a per-pod init
   container preserves order-independence but runs N times (contradicting
   "validate once"); a separate Job validates once but reintroduces a
   cross-process ordering dependency (the very thing OP-18 removed).
4. **Add `check_suse_ca`** for Class 1 consistency (see the SUSE CA note
   above).

## What Does NOT Change

To bound the change explicitly:

- **OP-18 startup ordering invariant**: unchanged. No new sequential gate
  is introduced.
- **CPE lazy-load pattern** (`lru_cache`): unchanged. The `worker_init`
  and lifespan guards warm the cache; they do not replace it.
- **`deployment.md` Process Architecture / Container Images**: unchanged.
  No new process role.
- **`cli-reference.md`**: unchanged. No new CLI command.
- **Celery-config-via-import validation**: unchanged. Still inherited by
  every Celery-based process; the new `worker_init` handler adds only the
  CPE check.

## Coverage: issue → resolution

| Issue (from v1) | Resolution in v2 |
|-------|-------------|
| #1 OP-16 CPE asymmetry | Component 2 — `worker_init` validates the worker's own file in every deployment model; lifespan guard retained as defense-in-depth |
| #3 Duplicated connectivity primitives | Component 1 — one definition of `check_postgres`/`check_redis`, reused by the IBS consumer and `/ready` |
| #2 Mechanism fragmentation | Partially addressed (connectivity primitives unified). Broader unification deliberately deferred — see "Accepted trade-off" |
| #4 Implicit vs. explicit infra checks | Per-process checks retained where genuinely needed (Component 3), now on shared primitives. Uniform deploy-time coverage deferred to the optional pre-flight job |

## Known gaps (recorded, not resolved here)

- **SUSE CA startup validation**: the SUSE CA trust store is a Class 1
  resource that degrades silently if missing (`networking.md`). Not
  validated at startup today, and not addressed by this change. Candidate
  for a future `worker_init`/consumer-side guard using the same pattern
  as the CPE mapping.
- **Consolidated deploy-time report**: no single-shot operator report
  exists; each process fails fast independently with a specific error.
  Addressed only if the optional pre-flight job is later implemented.

## Impact on OP-16

OP-16 asked: "should the CPE mapping be validated in the worker, in the
API server, or everywhere?" This decision answers: **validate it in the
worker (its consumer) via a `worker_init` guard, and retain the API
lifespan guard as defense-in-depth** — option (b) from the open point.
The asymmetry disappears because the worker validates the exact file it
will use, in its own process, before accepting any task, regardless of
deployment model. OP-16 status changes from Open to Resolved with a
reference to this decision record.

---

## Action Plan

Prescriptive list of spec modifications. This project is in the
specification phase — there is no implementation code or database to
migrate. All changes are to specifications only. Each step specifies the
exact file, section, and nature of the change.

### Step 1: Create the startup validation specification

**File**: `docs/features/platform/startup-validation.md` (new file)

This spec owns the shared validation module and the worker-side CPE
fail-fast contract. It is the single source of truth for *how* each
shared check is performed and for the two-class / primitive-reaction-
orchestration model.

**Content**:

- Summary and purpose
- Cross-references to: `docs/conventions.md` (Function Specification
  Completeness; Enum Storage Strategy), `docs/deployment.md`,
  `docs/architecture.md`, `docs/features/platform/health-endpoints.md`,
  `docs/features/integrations/ibs-rabbitmq-integration.md`,
  `docs/features/platform/fetcher-infrastructure.md`,
  `docs/features/packages/cpe-package-mapping.md`,
  `docs/features/tickets/cve-service.md`,
  `docs/features/platform/networking.md`
- **Two classes of validation** (Class 1 static / Class 2 dynamic),
  including the validations-vs-mutations scope note and the SUSE CA
  Class 1 note
- **Separation of concern** (primitive / reaction / orchestration) and
  the deliberately narrow primitive scope
- **Shared validation module** (`backend/app/core/startup_checks.py`):
  - The `CheckStatus` enum and `CheckResult` result type
  - Each primitive (`check_postgres`, `check_redis`,
    `check_cpe_mapping`) documented per the Function Specification
    Completeness convention. Because primitives capture expected failures
    in `CheckResult` (rather than raising) and mutate no state, document
    for each: Q1 (inputs, incl. `timeout` where applicable), Q3
    (behavior in every case, including how each failure mode maps to a
    `CheckStatus`), Q6 (only unexpected exceptions propagate). Q2/Q4/Q5
    are N/A
  - The design contract (return results, never react, never exit, never
    log on the caller's behalf)
  - The consumers table (worker `worker_init`, API lifespan, IBS
    consumer, `/ready`) with per-caller timeout and reaction
- **Worker-side CPE fail-fast** (`worker_init` handler): location,
  registration (`@worker_init.connect`), behavior (`check_cpe_mapping()`
  → `sys.exit(1)` on non-OK), scope (CPE only), mirroring `beat_init`
- **Per-process fail-fast** (Class 2): summary of which processes run
  which checks and how they react, referencing the owning specs
  (IBS consumer → `ibs-rabbitmq-integration.md`; Beat →
  `fetcher-infrastructure.md`; `/ready` → `health-endpoints.md`) as the
  authoritative sources for each process's orchestration
- **Deferred: pre-flight validation job** — a short section recording the
  optional future follow-up and its four mandatory constraints
- **Known gaps** — SUSE CA startup validation; consolidated deploy-time
  report

### Step 2: Update `docs/features/packages/cpe-package-mapping.md`

**Section**: Resolution Function, "Loading" (lines ~236-262)

- **Retain** the fail-fast-at-boot property. Reformulate the lifespan
  paragraph so the guard calls `check_cpe_mapping()` from the shared
  module (`backend/app/core/startup_checks.py`) instead of an inline
  dummy-CPE call.
- **Add** the worker-side guard: a Celery `worker_init` handler validates
  the mapping in the worker process (the actual consumer), resolving
  OP-16. State that this validates the worker's own file in every
  deployment model. Reference
  `docs/features/platform/startup-validation.md` for the primitive and
  handler definitions.
- Retain the lazy-init test-ergonomics rationale and the "Operational
  semantics" paragraph (read-once-per-process unchanged).

### Step 3: Update `docs/features/tickets/cve-service.md`

**Section**: Phase 2 error handling (lines ~488-497)

- Update the passage that says the mapping "is loaded and validated once
  at application startup via the lifespan event" to state it is validated
  at startup in **both** the API lifespan **and** the worker
  (`worker_init`) — so the worker has validated the exact file it will
  use. The semantic argument (runtime exception = programming bug) is
  unchanged and, in fact, strengthened. Reference
  `docs/features/platform/startup-validation.md`.

### Step 4: Update `docs/features/integrations/ibs-rabbitmq-integration.md`

**Section**: Process Startup → Complete Startup Sequence, step 3
(Infrastructure connectivity check, lines ~174-177)

- State that the PostgreSQL and Redis checks are performed via the shared
  `check_postgres(5)` / `check_redis(5)` primitives from
  `backend/app/core/startup_checks.py`. The reaction (`sys.exit(1)`) and
  the subsequent monitored-codestream-set build are unchanged. Reference
  `docs/features/platform/startup-validation.md` for the primitive
  definitions.

### Step 5: Update `docs/features/platform/health-endpoints.md`

**Section**: Readiness — GET /ready, "Checks performed" (lines ~51-102)

- Add a note that the PostgreSQL and Redis checks are performed via the
  shared `check_postgres(2)` / `check_redis(2)` primitives from
  `backend/app/core/startup_checks.py`, and that the `CheckStatus` enum
  maps directly to the endpoint's three-value output
  (`ok`/`unreachable`/`timeout`) and worst-across-instances rule.
  Observable behavior is unchanged (same checks, 2-second timeout, 503
  reaction, per-instance Redis discovery). Reference
  `docs/features/platform/startup-validation.md`.

### Step 6: Update `docs/features/platform/fetcher-infrastructure.md`

**Section**: Celery app factory validation note (lines ~2414-2418)

- The current note states that "no per-process signal handlers
  (`worker_init`, `beat_init`) are needed for these validations"
  (referring specifically to the **Celery-config** validation, which is
  inherited via Celery app import). Add a one-sentence clarification that
  a `worker_init` handler **is** introduced separately for **CPE mapping**
  validation (a different concern; see
  `docs/features/platform/startup-validation.md`), and that it does not
  affect the Celery-config-via-import mechanism. This keeps the two
  statements coherent so a reader does not perceive a contradiction when
  they encounter the `worker_init` handler in the startup-validation
  spec.

### Step 7: Update `docs/drafts/open-points.md`

**Section**: OP-16 entry + summary table

- Move OP-16 from "Open — Cross-Process Startup" to "Archive — Resolved."
- Add resolution text:

  > **Resolution**: resolved by the Startup Validation Architecture
  > (shared validation module + worker-side `worker_init` CPE fail-fast).
  > The CPE mapping is validated in the worker process (its actual
  > consumer) via a `worker_init` handler mirroring `beat_init`, in every
  > deployment model; the FastAPI lifespan guard is retained as
  > defense-in-depth. This is option (b) from the open point. The
  > connectivity checks (`check_postgres`/`check_redis`) are consolidated
  > into a shared module reused by the IBS consumer and `/ready`. See
  > `docs/features/platform/startup-validation.md`.

- Update the summary table: change OP-16 status from `Open` to
  `Resolved`.
- **Confirm OP-18 is NOT modified** (v2 introduces no new startup gate;
  the order-independence invariant is unchanged).

### Step 8: Run reviewers on affected specs

After applying steps 1–7:

1. **`@spec-gap-analyzer`** on
   `docs/features/platform/startup-validation.md` — verify no functional
   gaps (missing error paths, boundary conditions, concurrency
   scenarios).
2. **`@design-reviewer`** on
   `docs/features/platform/startup-validation.md` — verify the reduced
   design (shared module + `worker_init` + retained lifespan +
   per-process fail-fast) is sound and that the v1 findings are fully
   addressed.
3. **`@spec-coherence-reviewer`** on each of the following (one
   invocation per spec, independent sessions):
   - `docs/features/platform/startup-validation.md`
   - `docs/features/packages/cpe-package-mapping.md`
   - `docs/features/tickets/cve-service.md`
   - `docs/features/integrations/ibs-rabbitmq-integration.md`
   - `docs/features/platform/health-endpoints.md`
   - `docs/features/platform/fetcher-infrastructure.md`
4. **`@docs-placement-reviewer`** — verify the shared module is
   documented in one place (the new spec) and that references from the
   other specs do not duplicate the primitive definitions.
5. **`@docs-reviewer`** — verify documentation completeness and coherence
   across all modified files.

Address any "Needs revision" findings before considering the change
complete; fix "Minor issues" in the same pass.

### Step 9: Register the new spec in the review tracking

Add the new spec to the review-tracking artifacts so it is tracked as
**enabled** but **not yet manually reviewed** via the `/review-spec`
command. The `@`-agent reviewers run in Step 8 are unrelated to the
manual `/review-spec` pipeline recorded here. The changes follow the
tracking conventions in
`.opencode/commands/review-spec/tracking-format.md` and
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

**File 2** — `docs/reviews/README.md`: insert the two-row block (main row
+ empty severity sub-row) in the main table, in alphabetical position
(between the `rbac`/`sso-authentication` region and the `system-settings`
row — place it immediately before `system-settings`):

```
| [startup-validation](startup-validation.md) | — | — | — | — | — | 0/0 |  | — |
|  |  |  |  |  |  |  |  |  |
```

- Reviewer columns (GAP/COH/DES/SEC/API): `—` (never reviewed)
- Open: `0/0`; Last Review: empty; Stale: `—`
- The **Total** row is unchanged; the spec is NOT added to the "Disabled
  specs" list

### Step 10: Delete the drafts

After all spec changes are applied and reviewers confirm no outstanding
issues, delete **both** draft files:

- `docs/drafts/startup-validation-architecture.md` (v1, superseded)
- `docs/drafts/startup-validation-architecture-v2.md` (this file)

---

## Cross-References

- `docs/drafts/startup-validation-architecture.md` — v1 (superseded by
  this document); full startup-validation inventory and the pre-flight
  job design that this decision record discards
- `docs/drafts/open-points.md` — OP-16 (CPE Mapping Fail-Fast Asymmetry,
  resolved by this change), OP-18 (Cross-Process Startup Ordering —
  unchanged), OP-19 (`beat_init` wiring — the pattern mirrored by
  `worker_init`)
- `docs/features/packages/cpe-package-mapping.md` — CPE mapping loading,
  lifespan guard, and (new) worker-side fail-fast
- `docs/features/tickets/cve-service.md` — Phase 2 error handling
  referencing startup validation
- `docs/features/platform/fetcher-infrastructure.md` — Celery app factory
  validations, `beat_init` handler (pattern), Celery-config-via-import
  note
- `docs/features/integrations/ibs-rabbitmq-integration.md` — IBS consumer
  startup sequence and connectivity checks
- `docs/features/platform/health-endpoints.md` — `/ready` connectivity
  checks and three-value output
- `docs/features/platform/networking.md` — SUSE CA trust store
  (`SUSE_CA_CERT_PATH`) silent-degradation behavior
- `docs/features/platform/cli-infrastructure.md` — root-group bootstrap
  (why Settings/logging checks cannot be pre-flight subcommands)
- `docs/features/identity/authentication.md` — JWT configuration bounds
  (Settings validators)
- `docs/features/platform/logging.md` — LOG_LEVEL/LOG_FORMAT validation
- `docs/deployment.md` — startup ordering invariant (unchanged), process
  architecture
- `docs/architecture.md` — container images and process roles (unchanged)
- `docs/conventions.md` — Function Specification Completeness; Enum
  Storage Strategy
