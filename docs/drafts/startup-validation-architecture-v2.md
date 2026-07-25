# Startup Validation Architecture

Decision record for consolidating and rationalizing process startup
validations across all Sentinel runtime processes. Resolves OP-16 (CPE
Mapping Fail-Fast Asymmetry).

This document records the reduced design selected after rejecting an
earlier pre-flight-job proposal. It keeps that proposal's sound core (a
shared validation module plus per-process fail-fast), but resolves OP-16
inside each Celery worker rather than through a separate container. See
"Why the Pre-Flight Design Was Rejected" below for the full rationale.

## Why the Pre-Flight Design Was Rejected

The rejected design proposed three things to resolve OP-16 and
rationalize startup validation:

1. A shared validation module (`startup_checks`) defining every check as
   a non-reacting primitive returning a `CheckResult`.
2. A new one-shot **pre-flight validation job** (`sentinel preflight`)
   that runs after migrations and before runtime processes, executing an
   ordered suite of six checks (settings, logging, celery, CPE mapping,
   PostgreSQL, Redis).
3. **Removing** the CPE mapping validation from the FastAPI `lifespan`
   event, delegating fail-fast entirely to the pre-flight job.

Before propagating that design to the formal spec and its dependent
documents, it was pre-validated with the `@design-reviewer`. The review
returned a **"Reconsider design"** verdict. Its three critical findings
were confirmed directly against the current specs:

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
(`deployment.md`, Quick Start) starts `uvicorn` and `celery` directly,
with no pre-flight job. Under that removal, a hand-edited malformed
`cpe-package-mapping.json` would no longer be caught at boot in the
environment where the file is edited most often. It also weakens
`cve-service.md`'s Phase 2 error-handling reasoning ("an exception ...
indicates a programming bug"), because in a direct-run or skewed
deployment *neither* the API nor the worker would validate.

**Finding 3 — Two-to-three pre-flight checks cannot deliver their
specified behavior inside the CLI harness (confirmed).** The pre-flight
was a Click subcommand of the root `sentinel` group. Per
`cli-infrastructure.md` (Root Command Group & Bootstrap), the root group
loads `Settings` and, on failure, exits with **code 2 (system error)
before dispatching to any subcommand**. Because `LOG_LEVEL`/`LOG_FORMAT`
and the JWT/session bounds are `Settings` fields, `check_settings()` and
`check_logging_config()` could never reach their intended `✗` output or
their exit-code-1 contract — the process would already have died with
exit 2.

Additional findings: the boolean-only `CheckResult` was too lossy to
preserve `/ready`'s three-value output (`ok`/`unreachable`/`timeout`)
and its worst-across-instances rule; the Kubernetes realization forced a
contradiction between "validate once, centrally" and the OP-18
order-independence invariant; and the design diagram incorrectly showed
the API server as not importing the Celery app (it does, to enqueue
on-demand fetches). The review also identified the SUSE CA trust store as
a static resource. Further analysis confirmed that a missing SUSE CA is
an explicitly accepted warning-plus-system-CA fallback in `networking.md`,
not an unspecified validation gap; changing that behavior requires a
separate networking-policy decision.

### The decision

Adopt the reviewer's **reduced path**:

- **Keep** the sound core: the shared validation module (Component 1)
  and per-process fail-fast built on it (Component 3).
- **Resolve OP-16** with option (b) from the open point itself — a
  Celery worker-startup fail-fast handler that validates the CPE mapping
  **in the worker process**. **Retain** the FastAPI lifespan CPE guard as
  defense-in-depth. This validates the worker's own file in every
  deployment model and *strengthens* `cve-service.md`'s reasoning instead
  of weakening it.
- **Demote** the pre-flight validation job to an optional future
  follow-up, justified solely by its one legitimate merit (a
  consolidated deploy-time report) — never as the OP-16 fix, and never
  as a replacement for any always-on guard.

### Accepted trade-off

The reduced path fully resolves OP-16 (issue #1) and the duplicated
connectivity primitives (issue #3). It **deliberately** forgoes the
uniform deploy-time smoke test that the pre-flight job provided for
issues #2 (mechanism fragmentation) and #4 (implicit vs. explicit infra
checks). This is a conscious choice: the pre-flight *job* introduced
more defects (Findings 1–3) than the value it added for #2/#4, and #2/#4
are lower-severity "rationalization" concerns rather than correctness
bugs. Should the consolidated deploy-time report prove valuable in
operation, the pre-flight job can be added later as an **additive**
component (see "Deferred" below).

Net effect: OP-16 is resolved more correctly, no environment loses
fail-fast, the CLI-harness contradiction disappears, and the
propagation remains focused on the specs that own the affected checks and
startup sequences.

## Problem Statement

Sentinel runs five runtime processes from the same Docker image (API
server, Celery worker, Git worker, Celery Beat, IBS RabbitMQ consumer)
plus a one-shot migration job. Startup validations are performed through
several mechanisms with inconsistent coverage. The architecture review
identified four issues; #1 and #3 are correctness-relevant, while #2 and
#4 are lower-severity rationalization concerns:

1. **OP-16 — CPE mapping fail-fast asymmetry**: the CPE-to-package
   mapping (`cpe-package-mapping.json`) is validated at boot only in the
   API server (via the FastAPI `lifespan` event), but its actual
   consumers are **Celery worker** tasks (`resolve_ticket_packages`,
   `fetch_single_cve`), which load it lazily (`lru_cache`) on first use.
   A corrupted or missing file mounted on the worker would be detected at
   API boot but surface in the worker only when the first CVE ingestion
   task runs — potentially hours later.

2. **Mechanism fragmentation**: settings, logging, Celery configuration,
   static files, infrastructure connectivity, and startup state building
   use different lifecycle hooks. This is not inherently incorrect—the
   hooks reflect different process lifecycles—but there is no shared
   model distinguishing validation primitives, caller reactions, and
   process orchestration.

3. **Duplicated connectivity primitives**: the PostgreSQL `SELECT 1`
   check and the Redis `PING` check are independently specified in at
   least two places — the IBS consumer startup sequence
   (`ibs-rabbitmq-integration.md`, Process Startup, step 3) and the
   `/ready` readiness endpoint (`health-endpoints.md`, Readiness) — each
   with its own timeout and error handling. There is no single
   definition of "how to check PostgreSQL/Redis reachability."

4. **Implicit vs. explicit infrastructure checks**: Beat validates
   PostgreSQL and Redis as a side effect of required reconciliation, the
   worker validates PostgreSQL through FetcherConfig bootstrap, the IBS
   consumer has explicit probes, and API readiness performs continuous
   probes. The difference is partly justified by process ownership, but
   it was not expressed as a deliberate design.

For completeness, the current mechanisms relevant to this decision are:

| Validation or startup operation | Current mechanism | Processes |
|---------------------------------|-------------------|-----------|
| Settings bounds | Pydantic validation during `Settings()` construction | Every process that imports settings |
| Logging enums | Pre-structlog startup validation | All runtime processes |
| Celery timezone and redbeat lock | Celery app factory at import | API, worker, Git worker, Beat, IBS consumer |
| CPE mapping | FastAPI lifespan only | API only (the OP-16 asymmetry) |
| FetcherConfig bootstrap | Per-process startup flow | API, worker, Git worker, Beat |
| Beat reconciliation | `beat_init` handler | Beat only |
| PostgreSQL/Redis connectivity | Explicit checks | IBS consumer and `/ready`; Beat validates implicitly during reconciliation |

This decision fully addresses #1 and #3, establishes a conceptual model
for #2, and documents the per-process rationale for #4 without adding a
uniform deployment gate (see "Accepted trade-off").

## Design Principles

### Two classes of validation

Startup validations fall into two categories with fundamentally
different properties:

**Class 1 — Static resource validation** (image-baked, mounted, or
env-derived; deterministic within one process instance): validates
resources embedded in the Docker image, supplied by a process-specific
mount, or derived from environment variables. If the check passes once,
the result remains valid for that process while its immutable resource
and environment are unchanged. It does **not** prove that another
container sees the same file or environment; that distinction is why the
CPE check must run in the consuming worker rather than a pre-flight
container.

Examples: CPE mapping integrity, configuration bounds (JWT key length,
expiry range, session lifetime), Celery timezone, logging format, and
redbeat lock config. The SUSE CA trust store is also a static resource,
but its missing-file behavior is an intentional networking policy rather
than a fail-fast invariant; see the policy note below.

**Class 2 — Dynamic infrastructure validation** (runtime-dependent,
transient): validates connectivity to external services that can become
available or unavailable at any time. A check at T0 does not guarantee
availability at T0+30s. A correctness-relevant dynamic check cannot be
delegated to a different process: every caller that specifies such a
check performs it from its own network context and applies its own
reaction. This does not require every process to probe every dependency;
for example, API dependency health is continuously represented by
`/ready`, while Celery owns worker broker connectivity.

Examples: PostgreSQL reachability, Redis reachability, RabbitMQ
connectivity.

**Scope note (validations vs. mutations)**: this two-class model frames
read-only *validations* only. Startup **mutations / state-building** —
`system_settings` seeding, `bootstrap_fetcher_configs()`, Beat
reconciliation, and the IBS consumer's monitored-codestream-set build —
are a separate concern owned by per-process **orchestration** (below),
not by this validation model. They are neither Class 1 nor Class 2.

**Static resource vs. fail-fast policy (SUSE CA)**: classification as a
static resource does not itself imply that absence must abort startup.
`networking.md` deliberately specifies that a missing
`SUSE_CA_CERT_PATH` emits a warning and builds a system-CA-only context,
so public-source fetchers remain functional while SUSE-internal TLS
connections fail visibly at use time. This architecture does not change
that accepted policy. A future proposal to make the SUSE CA mandatory
must be evaluated as a separate networking-policy change, including its
effect on workers that consume only public-source queues; it is not a
startup-validation gap owned by this decision.

### Separation of concern: primitive vs. reaction vs. orchestration

The design rests on a clean three-way separation that determines what
can be shared and what must stay per-process:

| Concern | Question it answers | Ownership |
|---------|--------------------|-----------|
| **Check primitive** | *How* is a single check performed? (e.g., "PostgreSQL reachable" = `SELECT 1` with a timeout, returning a structured result) | **Shared** — one definition, reused everywhere |
| **Reaction** | What does a caller do when a check fails? (exit 1, return HTTP 503, log and continue) | **Per-caller** — differs by context |
| **Orchestration** | Which checks does a process run, in what order, and what state does it build afterward? (Beat reconciles schedules, the IBS consumer builds the monitored set, workers validate CPE then bootstrap FetcherConfig) | **Per-process** — genuinely different needs |

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
change: the worker startup handler and the API lifespan guard).
Configuration-bounds validation (`Settings` Pydantic validators),
logging-enum validation, and Celery-config validation are **not** turned
into shared primitives: they are single-caller, they already fire
correctly via their existing mechanisms (Pydantic on `Settings()`
instantiation; the Celery app factory on import), and — as Finding 3
showed — wrapping them as pre-flight primitives inside the CLI harness
would misreport their exit codes. Generalizing them would be
generalization without a duplication problem.

### Not a gatekeeper, and not a new process role

The solution does NOT introduce a coordinator/gatekeeper process or a
new one-shot process role. Process sequencing remains delegated to the
orchestrator's native primitives
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

**Result type**: each primitive returns a small structured result. The
status enum preserves `/ready`'s three connectivity values and adds
`INVALID` for static resources:

```python
class CheckStatus(StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"  # connectivity: refused / non-timeout error
    TIMEOUT = "timeout"          # connectivity: no response within timeout
    INVALID = "invalid"          # static resource: missing / malformed

@dataclass(frozen=True)
class CheckResult:
    name: str            # caller-safe label; never a URL or credential
    status: CheckStatus
    detail: str          # internal diagnostic; never returned by /ready

    @property
    def ok(self) -> bool:
        return self.status == CheckStatus.OK
```

`CheckStatus` is a **classification enum** (Category B per the Enum
Storage Strategy) — no database column, no CHECK constraint, defined in
`app/core/enums.py` per the convention. It is referenced here for
completeness.

**Primitives provided**:

```python
async def check_postgres(
    engine: AsyncEngine,
    *,
    timeout: float,
) -> CheckResult:
    ...

async def check_redis(
    redis_url: str,
    *,
    timeout: float,
    name: str,
) -> CheckResult:
    ...

def check_cpe_mapping() -> CheckResult:
    ...
```

| Primitive | Validation class | What it does | Non-OK result |
|-----------|------------------|--------------|---------------|
| `check_postgres(engine, timeout=...)` | 2 | Acquire one async connection and execute `SELECT 1` within the operation deadline, then settle cancellation and release or invalidate the connection before returning | `TIMEOUT` (operation deadline or SQLAlchemy pool timeout), `UNREACHABLE` (other SQLAlchemy connection/query/cleanup error) |
| `check_redis(redis_url, timeout=..., name=...)` | 2 | Create an owned zero-retry async Redis probe client for exactly `redis_url`, execute one `PING`, require the normal success response, and close it | `TIMEOUT` (deadline or Redis timeout), `UNREACHABLE` (other `RedisError`, unexpected PING response) |
| `check_cpe_mapping()` | 1 | Force the cached CPE loader through `resolve_cpe_packages()` with the fixed dummy CPE `cpe:2.3:a:test:test:*:*:*:*:*:*:*:*`; the loader validates the complete mapping before returning | `INVALID` (`CPEMappingLoadError`) |

**Connectivity input and resource ownership**:

- `timeout` is a finite number greater than zero, expressed in seconds.
  A non-finite or non-positive value raises `ValueError`; it is a caller
  programming error, not an infrastructure result.
- Both connectivity functions are async. They MUST NOT call
  `asyncio.run()` and therefore work inside FastAPI, the async IBS
  consumer startup sequence, and independently testable async flows.
- The caller owns the injected PostgreSQL `AsyncEngine`. The primitive
  MUST NOT derive a target from global settings.
- `check_redis()` receives one explicit configured URL and owns the
  short-lived client it creates from that URL. It MUST NOT read global
  settings or perform target discovery. Client construction uses the URL
  exactly as configured (scheme, credentials, host, port, logical DB,
  and supported query options) with retries forcibly overridden to zero
  (a redis-py async `Retry` using zero retries and a no-delay backoff, or
  the version-equivalent public API). It issues exactly one `PING` and
  always calls `aclose()` before returning or propagating. It never
  returns, stores, or logs the URL.
- `name` is a fixed non-secret diagnostic label; it MUST NOT contain a
  URL, hostname, username, password, or other connection material.
- For PostgreSQL, `timeout` covers connection-pool acquisition and
  `SELECT 1`. Connection release is a separate mandatory cleanup phase
  outside that deadline. After operation timeout or outer cancellation,
  the operation task is cancelled and awaited to settlement; the
  connection is then normally closed, or invalidated if SQLAlchemy marks
  it unusable. The primitive MUST NOT return while its operation task
  still owns a checked-out connection. Cleanup has no independent hard
  deadline: resource safety takes precedence over a strict total response
  bound. An expected SQLAlchemy cleanup error changes an otherwise `OK`
  result to `UNREACHABLE`; it does not replace a primary `TIMEOUT` result.
  No retry occurs.
- For Redis, the operation timeout encloses client connection and the
  single `PING` await. The client uses a zero-retry policy. `OK` requires
  the normal successful redis-py PING response (`True` after response
  decoding); any other returned value is `UNREACHABLE`. The primitive
  cancels the await on deadline, awaits it to settlement, and then awaits
  `aclose()` before returning. Cleanup has no independent hard deadline;
  the primitive MUST NOT return while an owned client has an active
  command. An expected `RedisError` during cleanup changes an otherwise
  `OK` result to `UNREACHABLE`; a primary `TIMEOUT` remains `TIMEOUT`. No
  retry or follow-up Redis command occurs.
- On outer cancellation, each primitive records the cancellation as the
  primary outcome, creates/continues its cleanup operation as an explicit
  task, and awaits that task through `asyncio.shield()` until settlement
  before re-raising cancellation. Repeated cancellation requests do not
  skip cleanup. Caller orchestrators apply the same rule while settling
  sibling checks.

**Connectivity outcome mapping**:

| Condition | PostgreSQL | Redis |
|-----------|------------|-------|
| Operation and mandatory cleanup complete successfully | `OK` | `OK` |
| Operation deadline expires | `TIMEOUT` | `TIMEOUT` |
| Built-in/asyncio deadline or SQLAlchemy pool timeout | `TIMEOUT` | N/A |
| Redis library timeout | N/A | `TIMEOUT` |
| Redis URL/client construction `ValueError` | N/A | `UNREACHABLE` |
| Expected library error during cleanup after a successful operation | `UNREACHABLE` | `UNREACHABLE` |
| Connection refused, DNS/connect failure, protocol/command/query error in the library's documented base exception | `UNREACHABLE` | `UNREACHABLE` |
| Exception outside the documented library failure families | Propagate | Propagate |

For PostgreSQL, built-in `TimeoutError` (including the exception raised
by `asyncio.timeout`) and `sqlalchemy.exc.TimeoutError` map to `TIMEOUT`;
other `SQLAlchemyError` subclasses map to `UNREACHABLE`. Timeout classes
are caught before their broader families. For Redis,
`redis.exceptions.TimeoutError` maps to `TIMEOUT`; other `RedisError`
subclasses map to `UNREACHABLE`, consistent with
`docs/conventions.md` (Redis Error Handling). `/ready` retains its
existing final safety net: if an unexpected exception propagates, the
endpoint logs it and reports that check as `unreachable` rather than
returning HTTP 500.

**Result field contract**:

| Primitive/outcome | `name` | `detail` |
|-------------------|--------|----------|
| PostgreSQL `OK` | `postgresql` | `SELECT 1 succeeded` |
| PostgreSQL `TIMEOUT` | `postgresql` | `PostgreSQL did not respond within {timeout} seconds` |
| PostgreSQL `UNREACHABLE` | `postgresql` | `PostgreSQL check failed ({exception_class})` |
| Redis `OK` | Exact safe `name` input | `PING succeeded` |
| Redis `TIMEOUT` | Exact safe `name` input | `{name} did not respond within {timeout} seconds` |
| Redis `UNREACHABLE` | Exact safe `name` input | `{name} PING failed ({exception_class})` |
| Redis unexpected PING response | Exact safe `name` input | `{name} returned an unexpected PING response` |
| Redis invalid URL/client configuration | Exact safe `name` input | `{name} configuration is invalid (ValueError)` |
| CPE `OK` | `cpe-mapping` | `CPE mapping loaded and validated` |
| CPE `INVALID` | `cpe-mapping` | Sanitized `CPEMappingLoadError` message |

`{timeout}` uses Python's general numeric format (`:g`) so integral
values render as `2`, not `2.0`. `{exception_class}` is only
`type(exc).__name__`; raw connectivity exception text is not copied into
`detail`, because it may contain connection parameters. `name` is
validated before network I/O and must be one of this fixed set:
`"application-redis"` or `"celery-broker"`. Callers assign
`"application-redis"` to `REDIS_URL` and `"celery-broker"` to
`CELERY_BROKER_URL`. Arbitrary or externally supplied labels are not
accepted.

**CPE mapping runtime integrity contract**:

The module-private cached loader in
`backend/app/services/cpe_mapping.py` has this contract:

```python
@lru_cache(maxsize=1)
def _load_cpe_mapping() -> dict[str, tuple[str, ...]]:
    ...
```

It validates the complete file before returning a dict whose package
collections are immutable tuples:

1. The file is readable UTF-8 and contains syntactically valid JSON.
2. The root value is an object.
3. Duplicate object keys are rejected during JSON decoding; validation
   MUST use a pair-preserving decoder hook because a normal dict decoder
   would silently retain only the last duplicate.
4. Every key is lowercase and has exactly one literal `:` separating
   non-empty `vendor` and `product` components. The key and both
   components must equal their whitespace-trimmed forms.
5. Every value is a non-empty array of strings. Every package string is
   non-empty after trimming and must equal its trimmed form. Duplicate
   package names inside one array are not newly prohibited by this
   change; existing mapping semantics remain authoritative.

`CPEMappingLoadError` is a `RuntimeError` subclass defined beside the
loader. Its message format is
`CPE mapping load failed at {path}: {reason}`. `{reason}` identifies the
failed rule and, for structural failures, the offending key or zero-based
array index; it never includes file contents or a package value. Missing
or unreadable files, UTF-8/decode errors, duplicate keys, and structural
violations all use this exception. Unexpected process failures such as
`MemoryError` propagate unchanged. Alphabetical key ordering remains a
CI/review maintainability check only: unsorted but otherwise valid JSON
is safe at runtime and does not abort a process. This makes the startup
guard and normal first-use loading enforce the same correctness-critical
schema; the guard does not rely on CI having run.

Only successful returns are cached. If loading raises
`CPEMappingLoadError` or another exception, `lru_cache` stores no entry;
the next call rereads and revalidates the file. This matters for isolated
tests and diagnostics, although normal API/worker startup reacts to the
first failure by terminating the process. After one successful load,
later calls in that process use the same cached mapping and do not reread
post-start file changes.

**Design contract**:

- A primitive **performs the check and returns a result**. It NEVER
  calls `sys.exit()`, NEVER raises for an expected failure condition (it
  captures the failure in `CheckResult` with a non-OK status + `detail`),
  and NEVER logs on the caller's behalf. Reaction is the caller's
  responsibility.
- `CheckResult.detail` is for internal logs and tests. It MUST be
  sanitized according to `logging.md`: no credentials, connection URLs,
  query parameters, or personal data. `/ready` serializes only `status`,
  never `name` or `detail`.
- Connectivity callers set context-appropriate deadlines (IBS consumer:
  5 seconds; `/ready`: 2 seconds).
- `check_cpe_mapping()` centralizes the "dummy CPE" idiom so its single
  owner is this module (both the worker startup handler and the API
  lifespan guard call it).

**Function Specification Completeness classification**: all three
primitives are Category A functions because they perform I/O;
`check_cpe_mapping()` additionally warms the loader cache. Their common
Q4 answer is **no audit events**: these checks do not mutate auditable
business data. The remaining completeness answers are:

| Function | Q1 inputs | Q2 refusal guards | Q3 behavior | Q5 re-invocation | Q6 propagated exceptions |
|----------|-----------|-------------------|-------------|------------------|--------------------------|
| `check_postgres` | Injected `AsyncEngine`; keyword-only finite positive `timeout` | Invalid timeout → `ValueError` before connection acquisition | Acquire one connection, run one `SELECT 1` under operation deadline, settle the task, close/invalidate before return, map documented failures | Not cached; performs a fresh check every call; no retry within a call | `ValueError`; exceptions outside the documented timeout/`SQLAlchemyError` families; cancellation initiated by the caller rather than the function's own deadline |
| `check_redis` | Explicit Redis URL string; keyword-only finite positive `timeout`; safe `name` | Invalid timeout or name outside the fixed safe-label set → `ValueError` before client construction | Validate URL is a non-empty string; create owned zero-retry client, run exactly one `PING` under deadline, require `True`, settle the task and close client before return, map malformed URL/client configuration to `UNREACHABLE` | Not cached; creates a fresh probe client and performs a fresh check every call; no retry within a call | Guard `ValueError`; exceptions outside documented construction/timeout/`RedisError` families; caller-initiated cancellation after shielded cleanup |
| `check_cpe_mapping` | None | None | Invoke the authoritative loader through the fixed dummy CPE; return `OK` or map `CPEMappingLoadError` to `INVALID` | First successful call warms the cache; later calls use it without file I/O. A failed load is not cached, so the next invocation retries one full read and validation | Exceptions other than `CPEMappingLoadError` |

The function's own operation deadline expiring is returned as `TIMEOUT`.
Cancellation imposed by an outer caller (for example, orchestrator
shutdown) MUST propagate as cancellation and MUST NOT be converted to a
health result; cleanup rules above still apply. Therefore each primitive
has a bounded operation phase but no guaranteed total-duration bound:
mandatory settlement and cleanup may extend beyond `timeout`.

**Consumers**:

| Consumer | Uses | Reaction on failure |
|----------|------|---------------------|
| Worker `celeryd_after_setup` handler (Component 2) | `check_cpe_mapping()` | Log CRITICAL and `sys.exit(1)` |
| API server lifespan guard (Component 2) | `check_cpe_mapping()` | raise → uvicorn aborts startup |
| IBS consumer startup (Component 3) | `check_postgres(..., timeout=5)` plus independent `check_redis()` calls for `REDIS_URL` and `CELERY_BROKER_URL` (5 seconds each) | Log CRITICAL and `sys.exit(1)` |
| `/ready` endpoint (Component 3) | `check_postgres(..., timeout=2)` plus independent `check_redis()` calls for `REDIS_URL` and `CELERY_BROKER_URL` (2 seconds each) | HTTP 503 (maps `CheckStatus` to response) |

Beat's connectivity validation remains implicit (it fails during
reconciliation if PostgreSQL/Redis are unavailable) and is not
refactored to call the primitives — see Component 3.

### Component 2: OP-16 Resolution — Unified Worker Startup + Retained Lifespan Guard

OP-16 is resolved by validating the CPE mapping **in the process that
consumes it** (the worker), while retaining the always-on API guard as
defense-in-depth.

**Unified worker startup handler (NEW)**:

- **Location**: `backend/app/core/worker_startup.py`.
- **Registration**: one handler is connected to Celery's
  `celeryd_after_setup` signal at module import time using
  `@celeryd_after_setup.connect(dispatch_uid="sentinel.worker_startup")`,
  and the Celery app module imports the handler module. The stable
  `dispatch_uid` prevents duplicate receiver registration if an import
  path is reloaded. The handler accepts `**kwargs` for forward
  compatibility with future Celery signal arguments.
- **Why this signal**: Celery's public signal contract specifies that
  `celeryd_after_setup` is emitted after worker logging and queue setup,
  but before the worker calls its run sequence. The handler can therefore
  emit a normal structured CRITICAL log and abort before the consumer
  accepts any task. `worker_process_init` is explicitly unsuitable: it
  runs in every pool child, imposes a four-second blocking limit, and
  would repeat process-wide startup work.
- **Single-owner rule**: this handler owns the complete Sentinel-specific
  startup sequence for every Celery worker. There MUST NOT be a separate
  handler for CPE validation and another for
  `bootstrap_fetcher_configs()`, because Celery does not provide a
  project-level ordering contract between independent receivers of the
  same signal.
- **Exit mechanism**: the handler MUST catch each ordinary `Exception`
  from its startup flow and call `sys.exit(1)`. Celery's signal dispatcher
  catches ordinary receiver exceptions and returns them as signal
  responses; allowing an exception to escape would therefore not provide
  a reliable fail-fast guarantee. `SystemExit` is a `BaseException`, not
  an `Exception`, and propagates through the dispatcher to abort startup.
- **Supported entrypoint scope**: the exit guarantee applies to
  Sentinel's supported `celery -A app.celery_app worker` process role.
  Celery's CLI observes the worker controller's non-zero exit code and
  terminates the process. Programmatically embedded `app.Worker().start()`
  inside another host process is not a supported Sentinel process role
  and is outside this contract.

**Signatures**:

```python
def handle_worker_startup(
    sender: str | None = None,
    instance: Worker | None = None,
    **kwargs: object,
) -> None:
    ...

async def bootstrap_worker(
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    *,
    timeout: float = 5.0,
) -> None:
    ...
```

`sender`, `instance`, and additional signal arguments are accepted for
Celery compatibility but are not used to select behavior: all Sentinel
workers follow the same sequence. The signal registration passes the
application’s `async_session_factory` to `bootstrap_worker()` through the
single sync-to-async bridge and passes the matching `engine`; the async
helper does not read a second global session factory or engine.

The handler executes this exact sequence:

1. Call `check_cpe_mapping()` synchronously.
2. If the result is not `OK`, log CRITICAL event
   `worker_startup_failed` with `stage="cpe_mapping"`,
   `status=result.status`, and the sanitized `detail`; then call
   `sys.exit(1)`. Do not open a database session.
3. If `check_cpe_mapping()` raises an unexpected exception, log
   `worker_startup_failed` with `stage="cpe_mapping"` and
   `error_type=type(exc).__name__`, without raw exception text or
   traceback; then call `sys.exit(1)`. Do not open a database session.
4. If the mapping is valid, invoke exactly one `asyncio.run()` around an
   extracted `bootstrap_worker(async_session_factory, engine,
   timeout=5)` flow. The helper creates an explicit task that opens the
   async database session, calls `bootstrap_fetcher_configs()`, commits
   according to the existing bootstrap contract, and exits the session
   context. It waits at most five seconds for that task. No retry occurs.
5. If the operation deadline expires or outer cancellation arrives,
   `bootstrap_worker()` cancels the operation task and awaits it to
   settlement through `asyncio.shield()` so session rollback and close
   can run. Repeated cancellation does not skip settlement. It MUST NOT
   proceed to disposal or return while the task still owns a checked-out
   connection. Cleanup has no separate hard deadline; resource safety
   takes precedence over a strict total startup duration.
6. In a `finally` block, `bootstrap_worker()` calls
   and shield-awaits `engine.dispose()` after the operation task has
   settled, regardless of bootstrap success, timeout, cancellation, or
   error.
   This closes the parent's pooled asyncpg connections and replaces the
   pool before Celery forks its worker children. The children must inherit
   an empty pool, never a socket associated with the temporary event loop
   created by `asyncio.run()`. Disposal is awaited to completion and has
   no independent hard deadline. A disposal error is a startup failure
   when there is no primary failure; when bootstrap already failed, the
   primary exception remains authoritative and disposal failure is logged
   as a secondary sanitized CRITICAL event. The five-second deadline
   bounds bootstrap work, not mandatory settlement and disposal.
7. If bootstrap or disposal raises any exception, log CRITICAL event
   `worker_startup_failed` with
   `stage="fetcher_config_bootstrap"` and
   `error_type=type(exc).__name__`, without raw exception text or
   traceback; then call `sys.exit(1)`. Celery must not begin its consumer
   bootstep.
8. If both operations succeed, log INFO event
   `worker_startup_completed` and return, allowing Celery bootsteps to
   start.

The extracted async flow is independently testable; synchronous handler
tests call the handler as a normal `def` test and verify the single
`asyncio.run()` bridge, per `testing-strategy.md` (Sync Entry-Point
Tests). Neither startup operation creates audit events. Re-invocation in
the same process is safe: CPE loading is cached and fetcher bootstrap is
an idempotent `INSERT ... ON CONFLICT DO NOTHING`. A failed invocation
terminates the process, so normal execution never invokes the handler a
second time in that process.

**Completeness contract**:

- **Guards**: neither function has a caller-level refusal guard.
  `handle_worker_startup()` treats a non-OK CPE result or any ordinary
  `Exception` from CPE validation, bootstrap, or pool disposal as a
  startup failure. `bootstrap_worker()` rejects a non-finite or
  non-positive timeout with `ValueError` before opening a session.
- **Audit events**: none. Cache warming and FetcherConfig bootstrap are
  operational initialization, not audited business mutations.
- **Re-invocation**: `handle_worker_startup()` is conditionally
  idempotent as described above; `bootstrap_worker()` is idempotent
  because its delegate uses `ON CONFLICT DO NOTHING`.
- **Exceptions**: `bootstrap_worker()` propagates `ValueError`, its own
  deadline `TimeoutError`, outer cancellation, and all exceptions from
  session acquisition, `bootstrap_fetcher_configs()`, commit, close, and
  engine disposal according to the primary/secondary precedence above.
  `handle_worker_startup()` converts ordinary exceptions and non-OK CPE
  results to `SystemExit(1)`. Existing `SystemExit`, `KeyboardInterrupt`,
  and other `BaseException` subclasses are not intercepted and propagate
  unchanged.

**Worker-role scope**: the handler runs for both general workers and Git
workers. This is intentional even when a Git worker is configured to
consume only the `git` queue:

- queue routing is deployment configuration and may change without a
  code change; the `git` worker is explicitly permitted to consume the
  default queue in simple deployments;
- all workers run from the same release image, and validating a <200 KB
  immutable static resource once is negligible compared with worker
  startup;
- conditional validation based on the currently selected queue would
  couple an image-integrity invariant to mutable routing configuration.

The guard runs once in the worker parent. Under Sentinel's Linux worker
deployment, pool children inherit the warmed immutable mapping cache
from that parent. The mapping's existing read-once-per-process contract
means post-start file changes are unsupported; a mapping update requires
a new deployment and worker restart. Beat and the IBS consumer import
the Celery app but do not emit `celeryd_after_setup`, so they do not run
this handler.

**Retained API lifespan guard**:

- The FastAPI `lifespan` event **keeps** its CPE fail-fast guard, now
  implemented via `check_cpe_mapping()` from the shared module (instead
  of an inline dummy-CPE call). This preserves fail-fast for the API
  server and for local/direct-run deployments (Quick Start), addressing
  the rejected design's local-deployment regression.
- If `check_cpe_mapping()` returns `INVALID`, lifespan logs CRITICAL event
  `api_startup_validation_failed` with `check="cpe_mapping"`,
  `status="invalid"`, and the sanitized detail, then raises
  `RuntimeError("CPE mapping startup validation failed")`; uvicorn aborts
  startup. Unexpected exceptions from the primitive are logged with
  `error_type` only (no raw text or traceback) and propagate unchanged.

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
implemented via the shared primitives. The owning consumer spec keeps
the orchestration and executes this sequence after confirming
`IBS_RABBITMQ_ENABLED=true`:

1. Call `check_postgres(engine, timeout=5)`. Any non-OK
   result or propagated unexpected exception produces the existing
   CRITICAL startup-failure reaction and exit code 1.
2. Run two independent primitives concurrently:
   `check_redis(settings.redis_url, timeout=5,
   name="application-redis")` and
   `check_redis(settings.celery_broker_url, timeout=5,
   name="celery-broker")`. They are never deduplicated, even when both
   URLs target the same server, because scheme, credentials, logical DB,
   and URL options are part of the dependency contract. Each primitive
   owns and closes its zero-retry probe client.
3. A probe client for `CELERY_BROKER_URL` is a narrowly scoped exception
   to the normal rule that application code does not access the Celery
   broker directly: it sends only `PING`, performs no key access, and
   exists solely for startup liveness verification.
4. The orchestrator creates explicit tasks. It either awaits all results
   or, after any propagated unexpected exception or outer cancellation,
   cancels every unfinished sibling and awaits all tasks to settlement
   before reacting. No client is abandoned with an active `PING`. If any
   result is non-OK, log one CRITICAL event identifying only the safe
   label and status, then exit 1. If a primitive propagates an unexpected
   exception, log its type at CRITICAL and exit 1.
5. Only after all dependencies pass, build the monitored codestream set
   and continue the existing startup sequence.

This checks the consumer's complete configured Redis dependency set. A
consumer with healthy heartbeat Redis but an unreachable separate Celery
broker must not start and consume events that it cannot enqueue. The
change requires an explicit probe-only clarification in
`configuration.md`; it does not authorize any other direct reads or
writes through `CELERY_BROKER_URL`. A successful PING validates the Redis
URL's connection, TLS (for `rediss://`), authentication, logical DB
selection, and basic command execution. Celery remains responsible for
its own transport connection and broker protocol lifecycle.

**`/ready` endpoint**: refactored to call
`check_postgres(engine, timeout=2)`,
`check_redis(settings.redis_url, timeout=2,
name="application-redis")`, and
`check_redis(settings.celery_broker_url, timeout=2,
name="celery-broker")`. The endpoint no longer deduplicates the URLs:
each configured dependency is checked independently, even when both
share `host:port`. This intentionally strengthens readiness by detecting
broker-only credential, TLS, logical-DB, or URL-option failures.

The endpoint launches all three checks concurrently using explicit tasks
and applies the same all-task-settlement rule as the IBS consumer before
serializing or handling an unexpected exception. The public response
schema is unchanged: the two Redis results are aggregated into the
existing single `checks.redis` field; safe labels and details remain
internal.

The `CheckStatus` enum maps directly onto the endpoint's documented
three-value output (`ok`/`unreachable`/`timeout`). When the two configured
Redis URL probes produce different outcomes, aggregation uses the fixed
severity order `TIMEOUT > UNREACHABLE > OK`; `INVALID` is impossible for
connectivity primitives. This makes the existing "worst result" rule
deterministic. `CheckResult.detail` is never included in the public
response. This is an internal consolidation that removes the duplicated
check implementation without moving endpoint orchestration into the
shared module.

Each check has a 2-second operation deadline. Mandatory task settlement
and resource cleanup are awaited beyond that deadline and have no hard
bound. Consequently the endpoint no longer promises a strict two-second
total response maximum. The orchestrator probe timeout remains an
operational bound: if cleanup stalls beyond it, the HTTP probe times out
and readiness fails by non-response. The existing recommendation of at
least 5 seconds remains the minimum, not a guarantee that every handler
path returns within five seconds.

**Beat**: retains its existing implicit infrastructure validation
(PostgreSQL read + Redis write during reconciliation, fail-fast via
`sys.exit(1)` in the `beat_init` handler). Not refactored to call the
primitives, because its validation is a side effect of reconciliation
(it must read `FetcherConfig` and write redbeat entries anyway) — an
explicit pre-check would be redundant.

**Workers**: gain the unified `celeryd_after_setup` sequence from
Component 2. No standalone PostgreSQL probe is added because
`bootstrap_fetcher_configs()` already opens a database session and
performs a write before the consumer starts; a database connectivity
failure therefore aborts worker startup. Adding `SELECT 1` immediately
before that required operation would duplicate work without improving
the guarantee. Workers do not explicitly probe Redis: establishing and
maintaining the Celery broker connection remains Celery's responsibility.

This startup guarantee does not imply runtime retry behavior. If
PostgreSQL becomes unavailable after startup, a top-level fetcher task
logs and fails without automatic Celery retry; recovery occurs at the
next scheduled cycle, per `fetcher-infrastructure.md`. The worker process
normally remains alive unless Celery or the orchestrator independently
terminates it. Sub-operation tasks retain their own documented retry
contracts.

**API server**: retains the FastAPI `lifespan` event for
`system_settings` seeding (defense-in-depth) and
`bootstrap_fetcher_configs()`, plus the retained CPE guard (Component 2).

### Deferred: Pre-Flight Validation Job (optional future follow-up)

A one-shot pre-flight job that runs a consolidated validation suite at
deploy time (before runtime processes) is **not** part of this decision.
Its only legitimate merit is operator UX: a single consolidated
deploy-time `✓`/`✗` report. It is recorded here as a possible future
addition, with mandatory constraints derived from the rejected-design
review:

1. **Additive only**: it MUST NOT remove any per-process or lifespan
   guard. It is an early-warning convenience, never a correctness
   mechanism.
2. **Exclude Settings/logging checks**: `Settings`/`LOG_LEVEL`/
   `LOG_FORMAT` validation is owned by the CLI bootstrap
   (`cli-infrastructure.md`, Root Command Group & Bootstrap), which exits
   with code 2 before any subcommand body runs. A pre-flight Click
   subcommand cannot report these as `✗` + exit 1. Either run the
   pre-flight as a standalone `__main__` script (not a root-group
   subcommand) or drop those checks.
3. **Choose one Kubernetes model and own its trade-off**: a per-pod init
   container preserves order-independence but runs N times (contradicting
   "validate once"); a separate Job validates once but reintroduces a
   cross-process ordering dependency (the very thing OP-18 removed).
4. **Do not add `check_suse_ca` implicitly**: the warning-plus-system-CA
   fallback is an accepted networking policy. Any change to fail-fast
   requires a separate policy decision owned by `networking.md`.

## What Does NOT Change

To bound the change explicitly:

- **OP-18 startup ordering invariant**: unchanged. No new sequential gate
  is introduced.
- **CPE lazy-load pattern** (`lru_cache`): unchanged. The worker-startup
  and lifespan guards warm the cache; they do not replace it.
- **`deployment.md` Process Architecture / Container Images**: unchanged.
  No new process role.
- **`cli-reference.md`**: unchanged. No new CLI command.
- **Celery-config-via-import validation**: unchanged. Still inherited by
  every Celery-based process; the new worker-startup handler adds CPE
  validation and owns the already-required worker FetcherConfig
  bootstrap.

## Coverage: issue → resolution

| Issue | Resolution |
|-------|-------------|
| #1 OP-16 CPE asymmetry | Component 2 — the unified `celeryd_after_setup` handler validates the worker's own file in every deployment model; lifespan guard retained as defense-in-depth |
| #3 Duplicated connectivity primitives | Component 1 — one definition of `check_postgres`/`check_redis`, reused by the IBS consumer and `/ready` |
| #2 Mechanism fragmentation | Partially addressed (connectivity primitives unified). Broader unification deliberately deferred — see "Accepted trade-off" |
| #4 Implicit vs. explicit infra checks | Per-process checks retained where genuinely needed (Component 3), now on shared primitives. Uniform deploy-time coverage deferred to the optional pre-flight job |

## Deferred Operational Enhancement

- **Consolidated deploy-time report**: no single-shot operator report
  exists; each process fails fast independently with a specific error.
  Addressed only if the optional pre-flight job is later implemented.

## Impact on OP-16

OP-16 asked: "should the CPE mapping be validated in the worker, in the
API server, or everywhere?" This decision answers: **validate it in the
worker (its consumer) via the unified `celeryd_after_setup` startup
handler, and retain the API lifespan guard as defense-in-depth** — option
(b) from the open point.
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

This spec owns the shared validation module and the unified worker
startup contract. It is the single source of truth for *how* each shared
check is performed and for the two-class / primitive-reaction-
orchestration model. The CPE mapping file schema remains owned by
`cpe-package-mapping.md`; the startup spec references that schema rather
than duplicating it.

**Content**:

- Summary and purpose
- Cross-references to: `docs/conventions.md` (Function Specification
  Completeness; Enum Storage Strategy), `docs/deployment.md`,
  `docs/architecture.md`, `docs/features/platform/health-endpoints.md`,
  `docs/features/integrations/ibs-rabbitmq-integration.md`,
  `docs/features/platform/fetcher-infrastructure.md`,
  `docs/features/packages/cpe-package-mapping.md`,
  `docs/features/tickets/cve-service.md`,
  `docs/features/platform/networking.md`,
  `docs/features/platform/logging.md`,
  `docs/features/platform/testing-strategy.md`, and
  `docs/configuration.md`
- **Two classes of validation** (Class 1 static / Class 2 dynamic),
  including the validations-vs-mutations scope note and the distinction
  between static-resource classification and fail-fast policy
- **Separation of concern** (primitive / reaction / orchestration) and
  the deliberately narrow primitive scope
- **Shared validation module** (`backend/app/core/startup_checks.py`):
  - The `CheckStatus` enum and `CheckResult` result type
  - The exact async signatures for `check_postgres()` and
    `check_redis()`, including resource ownership, safe labels, positive
    finite operation-timeout validation, mandatory unbounded settlement
    and cleanup, and the complete expected-exception-to-status mapping
  - The synchronous `check_cpe_mapping()` contract, which invokes the
    authoritative loader and maps only `CPEMappingLoadError` to
    `INVALID`
  - Each primitive documented as Category A per Function Specification
    Completeness: Q1 inputs, Q2 guards, Q3 complete behavior, Q4 no audit
    events, Q5 re-invocation, and Q6 propagated exceptions
  - The design contract (return results, never react, never exit, never
    log on the caller's behalf, never expose secrets in `detail`)
  - The consumers table (worker startup, API lifespan, IBS consumer,
    `/ready`) with per-caller timeout and reaction
- **Unified worker startup** (`worker_startup.py`):
  `celeryd_after_setup` registration with stable `dispatch_uid`; one
  handler owning both CPE validation and FetcherConfig bootstrap; exact
  ordering; ordinary-exception-to-`SystemExit(1)` conversion; one
  `asyncio.run()` bridge; exit-1 behavior; re-invocation semantics;
  parent-process execution; and intentional general/Git-worker scope
- **Per-process fail-fast** (Class 2): summary of which processes run
  which checks and how they react, referencing the owning specs
  (IBS consumer → `ibs-rabbitmq-integration.md`; Beat →
  `fetcher-infrastructure.md`; `/ready` → `health-endpoints.md`) as the
  authoritative sources for each process's orchestration
- **Deferred: pre-flight validation job** — a short section recording the
  optional future follow-up and its four mandatory constraints
- **Deferred operational enhancement** — consolidated deploy-time report

**Mandatory test scenarios documented by the new spec**:

- All three primitives: success, every expected non-OK classification,
  propagated unexpected exception, timeout boundary validation, and
  sanitized details
- PostgreSQL: operation task settles and connection closes/invalidates on
  success, expected failure, timeout, and cancellation; no retry occurs
- Redis: the exact URL input is used to create one owned zero-retry
  client; fixed-label validation happens before I/O; `True` is required;
  malformed URL maps to sanitized `UNREACHABLE`; shielded cleanup occurs
  on every path including repeated cancellation; no key operation or
  retry occurs
- CPE mapping: missing/unreadable file, malformed JSON, non-object root,
  duplicate keys, invalid/lowercase key format, empty arrays, non-string
  or empty package names, unsorted-but-valid acceptance, and no file
  contents in errors
- Worker startup: CPE failure prevents DB access; bootstrap timeout or
  failure exits 1; once DB bootstrap is entered, the parent engine pool
  is disposed before prefork on every path; disposal failure exits 1;
  success performs the fixed order with one `asyncio.run()`; both general
  and Git workers execute the handler; Beat and IBS consumer do not;
  re-invocation remains safe; a real `celery ... worker` subprocess exits
  non-zero on failure
- API lifespan: `INVALID` is logged and converted to the fixed startup
  `RuntimeError`; unexpected exceptions propagate
- Aggregation: mixed Redis outcomes follow
  `TIMEOUT > UNREACHABLE > OK`
- IBS consumer: disabled mode performs no probes; both configured Redis
  URLs are always checked independently, including when they share a
  server; all tasks settle before reaction; any failed or unexpected
  result prevents monitored-set construction
- `/ready`: both configured Redis URLs are always checked independently;
  all tasks settle; mixed results aggregate deterministically; neither
  safe labels nor diagnostic details enter the response body; operation
  deadline is 2 seconds but total duration has no hard bound because
  cleanup is mandatory

### Step 2: Update `docs/features/packages/cpe-package-mapping.md`

**Sections**: Static Mapping File → CI validation; Resolution Function →
`resolve_cpe_packages()` → Loading

- Define `CPEMappingLoadError` and the complete runtime integrity
  algorithm: UTF-8/readability, JSON object root, pair-preserving
  duplicate-key rejection, lowercase `vendor:product` keys with exactly
  one separator and non-empty components, and non-empty arrays of
  non-empty strings. State that ordering remains CI-only and unsorted
  valid files load at runtime.
- Define `_load_cpe_mapping() -> dict[str, tuple[str, ...]]`, successful-
  return-only `lru_cache` behavior, and the exact failed-load
  re-invocation contract.
- State that both startup validation and normal lazy first use invoke
  this same loader, so neither path relies exclusively on CI validation.
- **Retain** fail-fast at boot. Reformulate the lifespan paragraph so
  the guard calls `check_cpe_mapping()` from the shared module instead of
  an inline dummy-CPE call.
- **Add** the unified `celeryd_after_setup` worker guard, which validates
  the worker's own mapping in every deployment model. Reference
  `docs/features/platform/startup-validation.md` for the primitive and
  handler definitions.
- Retain the lazy-init test-ergonomics rationale and the "Operational
  semantics" paragraph, but correct its process list: API and Celery
  workers load the mapping at startup; Beat and the IBS consumer do not.

### Step 3: Update `docs/features/tickets/cve-service.md`

**Section**: Phase 2 error handling

- Update the passage that says the mapping "is loaded and validated once
  at application startup via the lifespan event" to state it is validated
  at startup in **both** the API lifespan **and** the worker
  (`celeryd_after_setup`) — so the worker has validated the exact file it
  will use. The semantic argument (runtime exception = programming bug)
  is unchanged and strengthened. Reference
  `docs/features/platform/startup-validation.md`.

### Step 4: Update `docs/features/platform/fetcher-infrastructure.md`

**Sections**: Startup Validation; FetcherConfig; Multi-Process
Coordination; add a Worker Startup subsection adjacent to Beat Startup
Reconciliation

- Preserve Celery timezone/redbeat-lock validation at app import and
  clarify that it requires no signal handler of its own.
- Replace the currently unspecified worker "process init" wiring with
  the authoritative unified `celeryd_after_setup` sequence owned by
  `startup-validation.md`: CPE check followed by
  `bootstrap_fetcher_configs()` inside one async bridge.
- Replace the generic statement that bootstrap is the worker's "first
  startup operation": CPE validation now precedes it in workers, while it
  remains first inside Beat/API startup flows as already specified.
- Give worker bootstrap a five-second operation deadline and require
  cancellation settlement to completion followed by
  `engine.dispose()` in `finally` before prefork. State explicitly that
  mandatory cleanup has no hard deadline. Define primary/secondary
  failure precedence, state that children inherit an empty pool, and
  require disposal failure to abort startup when no primary failure
  already exists.
- State that bootstrap's real DB operation is the worker's startup
  PostgreSQL fail-fast and that a preceding `SELECT 1` would be
  redundant.
- Correct runtime recovery language: top-level fetcher DB failures do
  not restart the worker or retry automatically; recovery is the next
  scheduled cycle.
- Retain Beat's independent `beat_init` flow unchanged.

### Step 5: Update `docs/features/integrations/ibs-rabbitmq-integration.md`

**Section**: Process Startup → Complete Startup Sequence, step 3
(Infrastructure connectivity check)

- Use `check_postgres(engine, timeout=5)`.
- Always run independent `check_redis()` calls for `REDIS_URL`
  (`application-redis`) and `CELERY_BROKER_URL` (`celery-broker`), even
  when they share `host:port`; each primitive owns a zero-retry client.
- Require explicit task settlement: on unexpected error or cancellation,
  cancel and await every unfinished sibling before reacting. Specify
  non-OK and unexpected-exception exit-1 reactions and that the
  monitored-set build begins only after all checks pass.
- Update Startup Failure and Error Handling tables so broker-only
  unavailability is explicitly covered.
- Reference `startup-validation.md` for primitive internals instead of
  duplicating their exception mapping.

### Step 6: Update `docs/features/platform/health-endpoints.md`

**Section**: Readiness — GET /ready, Checks performed

- State that the endpoint injects resources into
  `check_postgres(engine, timeout=2)` and runs independent
  `check_redis()` calls for both configured URLs with timeout 2.
- Keep concurrent orchestration, all-task settlement,
  unexpected-exception safety net, Redis aggregation, and serialization
  in this endpoint spec.
- Define worst-result precedence as
  `TIMEOUT > UNREACHABLE > OK`; state that `detail` is never serialized.
- Preserve status codes and body schema. Replace the strict 2-second
  endpoint-maximum claim with: each operation has a 2-second deadline,
  but mandatory settlement/cleanup has no hard bound; orchestrator
  timeout therefore remains the outer readiness failure bound. Retain the
  existing >=5-second recommendation as a minimum.

### Step 7: Update `docs/configuration.md`

**Section**: Required Connection Settings

- Preserve the rule that application code never accesses Celery broker
  keys directly.
- Add the narrow infrastructure-probe exception: IBS-consumer startup and
  API `/ready` may use a short-lived zero-retry client to issue only
  `PING` to the exact `CELERY_BROKER_URL` Redis URL, with no key access or
  broker protocol manipulation. Reference the owning integration,
  health-endpoint, and startup-validation specs.
- Clarify that `host:port` deduplication is not used: both full URLs are
  always probed independently. Celery still owns its broker connection
  and protocol lifecycle.

### Step 8: Update `docs/drafts/open-points.md`

**Sections**: OP-16 entry + summary table; archived OP-17 resolution

- Move OP-16 from "Open — Cross-Process Startup" to "Archive — Resolved."
- Add resolution text:

  > **Resolution**: resolved by the Startup Validation Architecture
  > (shared validation module + unified worker-startup fail-fast). The CPE
  > mapping is fully schema-validated in the worker process via a
  > `celeryd_after_setup` handler in every deployment model; the same
  > handler then performs the existing FetcherConfig bootstrap. The
  > FastAPI lifespan guard is retained as
  > defense-in-depth. This is option (b) from the open point. The
  > connectivity checks (`check_postgres`/`check_redis`) are consolidated
  > into a shared module reused by the IBS consumer and `/ready`. See
  > `docs/features/platform/startup-validation.md`.

- Update the summary table: change OP-16 status from `Open` to
  `Resolved`.
- **Confirm OP-18 is NOT modified** (the design introduces no new
  cross-process startup gate;
  the order-independence invariant is unchanged).
- Update archived OP-17 item 3: PostgreSQL remains first and sequential;
  the two configured Redis URLs are then probed concurrently and
  independently via the shared primitive. Remove the stale singular
  Redis-check wording while preserving OP-17's resolved status.

### Step 9: Run reviewers on affected specs

After applying steps 1–8:

1. **`@spec-gap-analyzer`** on
   `docs/features/platform/startup-validation.md` — verify no functional
   gaps (missing error paths, boundary conditions, concurrency
   scenarios).
2. **`@design-reviewer`** on
   `docs/features/platform/startup-validation.md` — verify the shared
   primitives, unified `celeryd_after_setup` worker sequence, retained
   lifespan guard, and per-process orchestration are sound.
3. **`@spec-coherence-reviewer`** on each of the following (one
   invocation per spec, independent sessions):
   - `docs/features/platform/startup-validation.md`
   - `docs/features/packages/cpe-package-mapping.md`
   - `docs/features/tickets/cve-service.md`
   - `docs/features/integrations/ibs-rabbitmq-integration.md`
   - `docs/features/platform/health-endpoints.md`
   - `docs/features/platform/fetcher-infrastructure.md`
   - `docs/configuration.md`
4. **`@docs-placement-reviewer`** — verify the shared module is
   documented in one place (the new spec) and that references from the
   other specs do not duplicate the primitive definitions.
5. **`@docs-reviewer`** — verify documentation completeness and coherence
   across all modified files.

Address any "Needs revision" findings before considering the change
complete; fix "Minor issues" in the same pass.

### Step 10: Register the new spec in the review tracking

Add the new spec to the review-tracking artifacts so it is tracked as
**enabled** but **not yet manually reviewed** via the `/review-spec`
command. The `@`-agent reviewers run in Step 9 are unrelated to the
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

### Step 11: Delete this draft

After all spec changes are applied and reviewers confirm no outstanding
issues, delete
`docs/drafts/startup-validation-architecture-v2.md`.

---

## Cross-References

- `docs/drafts/open-points.md` — OP-16 (CPE Mapping Fail-Fast Asymmetry,
  resolved by this change), OP-18 (Cross-Process Startup Ordering —
  unchanged), OP-19 (independent `beat_init` wiring)
- `docs/features/packages/cpe-package-mapping.md` — CPE mapping loading,
  lifespan guard, and (new) worker-side fail-fast
- `docs/features/tickets/cve-service.md` — Phase 2 error handling
  referencing startup validation
- `docs/features/platform/fetcher-infrastructure.md` — Celery app factory
  validations, worker FetcherConfig bootstrap, and independent
  `beat_init` handler
- `docs/features/integrations/ibs-rabbitmq-integration.md` — IBS consumer
  startup sequence and connectivity checks
- `docs/features/platform/health-endpoints.md` — `/ready` connectivity
  checks and three-value output
- `docs/features/platform/networking.md` — SUSE CA trust store
  (`SUSE_CA_CERT_PATH`) intentional warning-plus-system-CA fallback
- `docs/features/platform/cli-infrastructure.md` — root-group bootstrap
  (why Settings/logging checks cannot be pre-flight subcommands)
- `docs/features/identity/authentication.md` — JWT configuration bounds
  (Settings validators)
- `docs/features/platform/logging.md` — LOG_LEVEL/LOG_FORMAT validation
- `docs/features/platform/testing-strategy.md` — sync entry-point,
  integration-resource, and subprocess testing conventions
- `docs/deployment.md` — startup ordering invariant (unchanged), process
  architecture
- `docs/configuration.md` — Redis/Celery broker ownership and the narrow
  infrastructure-probe exception
- `docs/architecture.md` — container images and process roles (unchanged)
- `docs/conventions.md` — Function Specification Completeness; Enum
  Storage Strategy
