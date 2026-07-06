# Draft: Celery Beat Schedule Synchronization Specification

**Status**: Draft — all open points resolved, ready for application
**Date**: 2026-07-06
**Scope**: New section in `fetcher-infrastructure.md` + minor coherence
updates to 4 other documents

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
| OP-9 | Beat imports fetcher modules | Beat imports the shared discovery module at startup, before reconciliation | Prerequisite for a populated `FETCHER_REGISTRY`; without it reconciliation destroys the schedule. |
| OP-10 | Fetcher module discovery mechanism | Central import module (`fetcher_discovery.py`) + anti-drift test | Explicit in production (aligns with `conventions.md` "prefer explicit over implicit"), zero-drift at test time via package scan, no risk of importing non-fetcher modules. |
| OP-13 | Celery timezone validation scope | App-level contract (app factory validation, fail-fast) | Validation in the Celery app factory covers all processes (worker, Beat, consumer) automatically via import-time check. No per-process signal handlers needed. Fail-fast because wrong timezone produces silently incorrect results (all schedules fire at wrong times). |
| OP-4 | Beat startup with empty FetcherConfig | Option (c') — shared idempotent bootstrap in all processes | `bootstrap_fetcher_configs()` routine (batch `INSERT ON CONFLICT DO NOTHING`) runs at startup in worker, Beat, and API server. Records always exist before any consumer needs them. Eliminates 404 window, removes OP-11 dependency, no startup ordering requirement. |
| OP-11 | `run_timeout` default as code constant | Irrelevant (superseded by OP-4 choice) | OP-4 resolved with option (c') — Beat always reads `run_timeout` from FetcherConfig (record guaranteed to exist by bootstrap). No code constant needed. |
| OP-6 | `due_at` behavior on reconciliation overwrite | Fresh recalculation (no catch-up) | Already the implicit behavior of "create or unconditionally overwrite." Avoids thundering herd on restart. Data recovery is handled at the application level by `catch_up()`, not by the scheduler. |
| OP-7 | Invalid cron expression during reconciliation | Fail-fast (uncaught exception) | `schedule_override` is validated by the PATCH endpoint; corruption requires direct DB manipulation (probability ~zero). No fallback mechanism needed — fail-fast is the clearest signal to the operator. |
| OP-8 | Manual trigger `apply_async()` missing time limits | Fix: pass `time_limit`/`soft_time_limit` from `FetcherConfig.run_timeout` | All invocations (scheduled and manual) use the same `run_timeout` from the same source. No custom time limit parameter for the admin — keeps a single source of truth for time limits. |
| OP-14 | Beat behavior when FetcherConfig table does not exist | Subsumed under OP-1 fail-fast | Any database-level error during reconciliation (connection, auth, schema missing) triggers the same fail-fast. The `{error}` placeholder provides operator diagnosis. Correct deployment ordering eliminates the scenario. |

---

## Open Points (Pending Resolution)

All open points have been resolved. Only OP-12 remains as a deferred
future extension (YAGNI).

### Dependency Graph

```
OP-9 (Beat module import) ─── RESOLVED (discovery module)
    │
    └── resolved by ──→ OP-10 (discovery mechanism) ─── RESOLVED (option a + test)
                             │
                             └── also covers ──→ _CVE_SOURCE_TYPE_MAP

OP-4 (empty FetcherConfig) ─── RESOLVED (shared bootstrap in all processes)
    ├── depends on ──→ OP-9 ✓ (resolved)
    ├── OP-11 (run_timeout constant) ──→ CLOSED (irrelevant, superseded)
    └── future extension ──→ OP-12 (default_enabled, deferred YAGNI)

OP-6 (due_at on overwrite) ─── RESOLVED (fresh recalculation, implicit behavior)
OP-7 (invalid cron) ─── RESOLVED (fail-fast, uncaught exception)
OP-8 (manual trigger time limits) ─── RESOLVED (pass from FetcherConfig.run_timeout)
OP-13 (timezone validation) ─── RESOLVED (app-level contract, fail-fast)
OP-14 (schema not migrated) ─── RESOLVED (subsumed under OP-1)
```

| # | Summary | Severity | Resolution |
|---|---------|----------|------------|
| OP-12 | No `default_enabled` class attribute for fetchers that should start disabled | Low | Deferred (YAGNI) — no current use case. Neither OP-4 nor any other resolution is precluded. See detailed section below. |

### OP-4: Beat Startup with Empty FetcherConfig

**Resolution**: RESOLVED — option (c') extended to all three processes
(worker, Beat, API server). A shared idempotent routine
`bootstrap_fetcher_configs()` (`backend/app/services/fetcher_bootstrap.py`)
executes a batch
`INSERT INTO fetcher_config (...) VALUES (...) ON CONFLICT (fetcher_name) DO NOTHING`
for every fetcher in `FETCHER_REGISTRY` at process startup. Each process
runs this routine after importing `fetcher_discovery` (which populates
the registry) and before its role-specific logic (Beat: reconciliation;
API: serving requests; worker: consuming tasks). Since the operation is
idempotent and concurrency-safe at the DB level, multiple processes
running it simultaneously produce no conflicts — the first succeeds,
the rest are no-ops. This eliminates: (a) the empty-FetcherConfig
problem on fresh deployment, (b) the 404 window for GET/PATCH config
endpoints, (c) the dependency on OP-11 (run_timeout as code constant).
The previous characterization "worker-only-writes" is corrected to:
"idempotent bootstrap creates records with defaults in all processes;
only PATCH modifies existing records."

**Context** (historical analysis): `FetcherConfig` records are created exclusively by workers
on startup (`INSERT ... ON CONFLICT DO NOTHING`). In a fresh deployment
or parallel Kubernetes restart, Beat may start before any worker,
finding zero records in `FetcherConfig`. Reconciliation step 2 requires
`enabled = true` in `FetcherConfig` — condition unsatisfiable → empty
schedule → no fetchers run until Beat is restarted.

**Impact**: on first deployment, hours of missed data ingestion if Beat
is not manually restarted after workers have initialized.

**Dependency**: OP-9 (Beat module import) is a prerequisite for ALL
options here — if `FETCHER_REGISTRY` is empty, reconciliation produces
an empty schedule regardless of whether `FetcherConfig` records exist.

**Considerations**:
- Option (a) means Beat can always produce a working schedule from the
  code registry alone. The eventual `FetcherConfig` creation by the
  worker (with identical defaults) causes no divergence. If an admin
  changes config via PATCH before the worker creates the record, the
  PATCH would 404 (no `FetcherConfig` exists) — this is already the
  documented behavior. The "record mancante → enabled with defaults"
  resolution already exists in the spec: `fetcher-operations.md:840-842`
  (CLI `fetcher list` column) and `fetcher-operations.md:563` (GET
  config endpoint `effective_schedule`). Option (a) reuses this existing
  convention in the reconciliation loop. Requires OP-11 (run_timeout as
  code constant) to be resolved first.
- Option (b) is simpler (no new logic) but requires operators to ensure
  startup ordering or accept a Beat restart.
- Option (c) violates the clean separation documented in "Who Writes
  to Redbeat" and "Multi-Process Coordination".
- Option (c') extracts the auto-creation logic into a shared idempotent
  routine (same `INSERT ... ON CONFLICT DO NOTHING`) called by both
  workers and Beat at startup. Beat runs this routine BEFORE
  reconciliation, so records always exist when needed. The "worker-only-
  writes" characterization in the draft is actually inaccurate — the API
  server already writes to `FetcherConfig` via PATCH. The real invariant
  is "idempotent bootstrap creates with defaults; only PATCH modifies
  existing records." Option (c') maintains this invariant. Unlike option
  (c) in its original framing, this does not "break" any separation —
  it shares an existing routine. This option ensures GET and PATCH
  endpoints work immediately after Beat startup (no 404 window).

### OP-9: Beat Process Does Not Import Fetcher Modules

**Severity**: Critical (prerequisite for the entire draft)

**Resolution**: RESOLVED. Beat MUST import all fetcher modules at
startup via the shared discovery module (`fetcher_discovery.py`) as a
precondition before reconciliation begins. The mechanism is specified in
OP-10. The "Fetcher Discovery (Module Import)" subsection (added to the
Registry section of `fetcher-infrastructure.md`) makes this normative.

**Context** (historical analysis): `FETCHER_REGISTRY` is a module-level dictionary
(`backend/app/services/base_fetcher.py`) populated at **import time** by
`BaseFetcher.__init_subclass__`. It is NOT populated by any runtime
registration call — it only gets entries when Python imports the module
containing a concrete `BaseFetcher` subclass.

The current spec (`cve-fetcher-infrastructure.md:675-681`) explicitly
requires two processes to import all fetcher modules:

1. **Celery workers**: "Celery workers import all task modules during
   startup, so the map is complete before any consumer reads it."
2. **FastAPI API server**: "The FastAPI application MUST also import all
   fetcher modules at startup (e.g., via an explicit import in
   `app/main.py` or a startup event)"

**Beat is not mentioned.** The Celery Beat process is a separate OS
process from both workers and the API server. It loads the Celery app
configuration and its `autodiscover_tasks()` (if used) imports **task
modules** (e.g., `app/tasks/fetchers.py` which defines the generic
`run_fetcher` task), but this does NOT automatically import the
**fetcher class modules** scattered across domain directories:

- `backend/app/services/tickets/` (CVE/CVSS fetchers)
- `backend/app/services/packages/` (product/package fetchers)
- `backend/app/services/identity/` (AD sync)
- `backend/app/services/platform/` (platform fetchers)
- `backend/app/services/integrations/` (integration fetchers)

(per `fetcher-infrastructure.md:1280-1287`, Domain Placement)

**Impact**: without these imports, Beat's `FETCHER_REGISTRY` is an empty
dict `{}`. The reconciliation loop (`for name, cls in
FETCHER_REGISTRY.items()`) iterates zero times. Step 4 (remove
deregistered entries) would remove ALL existing redbeat entries (since no
fetcher name matches an empty registry). The result is **worse than doing
nothing** — it actively destroys a working schedule.

**This blocks the entire draft**, not just OP-4. Without Beat importing
fetcher modules, reconciliation cannot function at all.

**Resolution required**: add an explicit requirement that Beat imports
all fetcher modules at startup, using the same mechanism as worker and
API server. The mechanism itself is specified in OP-10.

**Spec locations to update**:
- `fetcher-infrastructure.md`, Registry section (~line 1289): add Beat to
  the list of processes that depend on the registry
- `fetcher-infrastructure.md`, Celery Integration section (~line 1340):
  the corrected paragraph (Step 1a of the Action Plan) should reference
  the shared import mechanism
- `cve-fetcher-infrastructure.md:675-681`: extend the existing statement
  to include Beat alongside workers and the API server
- The new "Celery Beat Schedule Synchronization" section (Startup
  Reconciliation, step 1 "Read state"): add an explicit precondition
  that `FETCHER_REGISTRY` is fully populated before reconciliation begins

### OP-10: No Shared Discovery Mechanism for Fetcher Module Imports

**Severity**: Medium (maintenance fragility across 3+ entrypoints)

**Resolution**: RESOLVED — option (a) + anti-drift test. A central
import module (`backend/app/services/fetcher_discovery.py`) with one
explicit import line per concrete fetcher. All entrypoints (worker, API,
Beat) import this single module. A test uses `pkgutil.walk_packages` on
the domain directories to assert every concrete `BaseFetcher` subclass
is imported by `fetcher_discovery`, catching forgotten imports at CI
time. The package scan is confined to the test suite — production code
uses only explicit imports.

**Context** (historical analysis): the spec currently says fetcher modules must be imported by
workers, the API server, and (per OP-9) Beat. The only guidance for HOW
is `cve-fetcher-infrastructure.md:678-679`:

> "e.g., via an explicit import in `app/main.py` or a startup event"

This implies each entrypoint independently lists the imports. With 3
entrypoints (worker, API, Beat) and fetcher classes spread across 5+
domain directories, there are two failure modes:

1. **Forgotten import**: a new fetcher is added to a domain directory but
   not imported by one of the entrypoints → the fetcher is invisible to
   that process (e.g., Beat doesn't schedule it, or the API can't serve
   its config)
2. **Stale import**: a fetcher is removed from the codebase but the
   import line remains in one entrypoint → import error on startup

Both are silent bugs that only manifest at runtime.

**Impact**: not a correctness bug in isolation, but a maintenance hazard
that grows linearly with the number of fetchers and entrypoints. The
project already has a per-fetcher manual step (updating the Fetcher
Registry table in `data-sources.md` — `fetcher-infrastructure.md:1273-
1276`), so one more manual step is precedented, but 3 places to update
is fragile.

**Options**:

**(a) Central import module** (explicit, DRY):

A single module (e.g., `backend/app/services/fetcher_discovery.py`)
contains all fetcher imports:

```python
# fetcher_discovery.py — single source of truth for fetcher imports.
# Import this module to populate FETCHER_REGISTRY.
import app.services.tickets.sync_nvd_cves  # noqa: F401
import app.services.tickets.sync_redhat_cves  # noqa: F401
import app.services.packages.detect_ibs_track_releases  # noqa: F401
# ... one line per fetcher
```

All entrypoints (worker, API, Beat) do:
```python
import app.services.fetcher_discovery  # noqa: F401
```

- **Adding a fetcher**: 1 line in `fetcher_discovery.py`
- **Removing a fetcher**: -1 line in `fetcher_discovery.py`
- **Entrypoints**: never change

Pros: explicit (aligns with `conventions.md` "prefer explicit over
implicit"), trivial to understand, no edge cases.
Cons: one manual step per fetcher (but single location).

**(b) Package scan** (implicit, zero-maintenance):

A function scans domain directories at startup:

```python
def discover_fetchers():
    """Import all fetcher modules from domain directories."""
    import pkgutil
    import importlib
    domain_packages = [
        "app.services.tickets",
        "app.services.packages",
        "app.services.identity",
        "app.services.platform",
        "app.services.integrations",
    ]
    for pkg_name in domain_packages:
        pkg = importlib.import_module(pkg_name)
        for importer, name, ispkg in pkgutil.walk_packages(
            pkg.__path__, prefix=pkg.__name__ + "."
        ):
            importlib.import_module(name)
```

- **Adding a fetcher**: create the file (nothing else)
- **Removing a fetcher**: delete the file (nothing else)
- **Entrypoints**: never change

Pros: zero per-fetcher maintenance.
Cons: implicit ("magic"), imports ALL modules in domain directories
(including non-fetcher helpers, utilities, pure service modules that
happen to live alongside fetchers), error in any module blocks all
imports, harder to debug import failures.

**Recommendation noted during analysis**: option (a) was preferred for
alignment with `conventions.md` ("prefer explicit over implicit") and
simplicity. The "one import line per fetcher" cost is analogous to the
already-mandated Fetcher Registry table update in `data-sources.md`.

**Scope note**: this mechanism belongs in the **Registry section** of
`fetcher-infrastructure.md` (it's a cross-cutting concern for all
processes, not specific to Beat sync). The Beat sync section references
it as "Beat calls the shared discovery mechanism." This is a small scope
extension for the draft but fixes a pre-existing gap (worker + API
already need this but have no specified mechanism).

**Multi-registry note**: the same module-import mechanism populates
**two** independent registries:

1. `FETCHER_REGISTRY` (`base_fetcher.py`) — populated by
   `BaseFetcher.__init_subclass__`
2. `_CVE_SOURCE_TYPE_MAP` (`base_cve_fetcher.py`) — populated by
   `BaseCVEFetcher.__init_subclass__`
   (`cve-fetcher-infrastructure.md:172-190`)

Both are populated as a side effect of importing the same set of fetcher
class modules. Whichever option is chosen for OP-10, it MUST be
documented that the discovery mechanism populates both registries (and
any future import-time registries added to the fetcher hierarchy). The
`_CVE_SOURCE_TYPE_MAP` is consumed by the API server (sources endpoint,
refetch endpoint, on-demand fetch loop) and by workers (batch
`execute()`). Beat does not currently consume it, but the discovery
mechanism runs the same imports for all processes regardless — both
registries are populated as a natural consequence.

### OP-11: `run_timeout` Default Not Available as Code Constant

**Severity**: ~~Low~~ Irrelevant (OP-4 resolved with option c')

**Resolution**: IRRELEVANT. OP-4 was resolved with option (c') — the
shared bootstrap routine creates `FetcherConfig` records with the DB
column default (`run_timeout = 3600`) before any consumer reads them.
Beat always reads `run_timeout` from the DB record (guaranteed to
exist). A code constant for `run_timeout` is no longer needed.

**Context** (historical analysis): the `run_timeout` column has a database default of 3600:

> `run_timeout | INTEGER | NOT NULL, DEFAULT 3600`
> (`fetcher-infrastructure.md:1550`)

And the prose says:

> "The default of 3600 seconds (1 hour) applies when a `FetcherConfig`
> record is auto-created for a newly registered fetcher."
> (`fetcher-infrastructure.md:1579-1580`)

However, 3600 is NOT defined as a **class attribute** or **code
constant** in `BaseFetcher`. It only exists as a DB column default and
prose documentation.

**Impact**: if OP-4 resolves to option (a) (Beat uses code defaults for
missing `FetcherConfig` records), Beat needs to compute `time_limit` and
`soft_time_limit` for the redbeat entry without reading from the DB. It
needs access to the `run_timeout` default as a Python value.

If OP-4 resolves to option (c') (shared bootstrap ensures records always
exist), this point is moot — Beat always reads `run_timeout` from the
DB.

**Resolution** (if needed): define a class-level constant in
`BaseFetcher`:

```python
class BaseFetcher:
    ...
    default_run_timeout: int = 3600  # seconds
```

This mirrors the pattern of `default_schedule` and
`default_request_delay` which are already class attributes. The DB
column DEFAULT and the auto-creation logic both reference this constant,
keeping the value in one place.

**Spec locations to update** (if resolved):
- `fetcher-infrastructure.md`, Abstract Interface section (~line 182):
  add `default_run_timeout` to the class attribute list
- `fetcher-infrastructure.md`, FetcherConfig section (~line 1550): note
  that the DB default mirrors the class constant

### OP-12: No `default_enabled` Class Attribute

**Severity**: Low (YAGNI — no current use case)

**Context**: all `FetcherConfig` records are auto-created with
`enabled = true` (the DB column default). The spec has no mechanism to
declare a fetcher that should start **disabled** (e.g., an experimental
fetcher deployed to production but only activated manually at a chosen
moment).

The "missing record = enabled" convention is established in two places:
- `fetcher-operations.md:840-842`: CLI `fetcher list` enabled column
  defaults to `yes` when no `FetcherConfig` exists
- DB schema: `enabled BOOLEAN NOT NULL DEFAULT true`

This means the ONLY way to have a fetcher start disabled is:
1. Deploy the code (auto-creation sets `enabled = true`)
2. Immediately PATCH `enabled = false` before the first Beat tick fires
   the task

This is a **race condition** (Beat might fire the task between worker
auto-creation and the admin's PATCH).

**Impact**: currently no fetcher needs this behavior. This is a
forward-looking design concern — the current spec does not preclude
adding the capability later, but both OP-4 options (a) and (c') should
be aware of this when choosing their resolution:

- **If (a)**: the "missing record → enabled" resolver would need to be
  extended to check a `default_enabled` class attribute. The change is
  additive and backward-compatible (existing fetchers that don't declare
  `default_enabled` inherit `True`).
- **If (c')**: the shared bootstrap routine would read `default_enabled`
  and use it in the INSERT statement instead of hardcoding `true`. Same
  additive change.

**Decision**: defer (YAGNI). Neither option for OP-4 is precluded. If
the need arises, a `default_enabled: bool = True` class attribute can
be added following the same pattern as `default_schedule` and
`default_request_delay`. Note this as a future extension point in the
spec when resolving OP-4.

### OP-13: Celery Timezone Validation Not Specified for Beat

**Severity**: High (correctness of all scheduled executions depends on
this)

**Resolution**: RESOLVED — option (b) with app factory mechanism. The
timezone validation is reframed as an **app-level contract**: the Celery
app factory (`backend/app/celery_app.py` or equivalent) validates
`timezone == "UTC"` and `enable_utc is True` at module import time,
raising a `RuntimeError` if either is incorrect. Since every Celery-based
process (worker, Beat, IBS consumer) must import the Celery app object to
function, the validation is inherited automatically — no per-process
signal handlers needed. Behavior: fail-fast (refuse to start), not
force-UTC + warning. Rationale: wrong timezone produces silently
incorrect results (all schedules fire at wrong times); a warning log is
easily missed in production. The three existing spec locations
(`configuration.md`, `fetcher-infrastructure.md`, `conventions.md`) are
updated to remove the "worker" qualifier and specify the app factory
mechanism instead.

**Context** (historical analysis): the correctness of the entire Beat schedule
synchronization rests on the assumption that Beat interprets cron
expressions in UTC. The spec mandates `CELERY_TIMEZONE = "UTC"` and
`CELERY_ENABLE_UTC = True`, and assigns **enforcement** exclusively to
the worker process:

- `configuration.md:48-52`: *"the application MUST validate at Celery
  **worker** startup that these settings are `UTC` and `true`
  respectively. If either is overridden... the **worker** MUST refuse
  to start"*
- `fetcher-infrastructure.md:1349`: *"The **worker** validates these
  settings at startup and refuses to start if they are overridden."*
- `conventions.md:143-146`: *"the **worker** validates them at startup
  and refuses to start if they are incorrect"*

The draft's "Startup Validation" section (line 847-850) asserts *"Beat
inherits the same validation (it loads the same Celery app
configuration)"* — but this is an **assumption**, not a specification.
Loading the same config does NOT guarantee the validation hook executes.

**Technical detail**: if the timezone validation is implemented as a
Celery `worker_init` signal handler (the standard pattern for
worker-specific startup hooks), Beat does **not** emit that signal — it
emits `beat_init` instead. An implementation following the spec
literally would validate timezone only in workers, leaving Beat
unguarded.

**Impact**: if an operator sets `CELERY_TIMEZONE=Europe/Rome`:
- Workers refuse to start (correct — spec-mandated)
- Beat starts normally (no validation) and interprets all cron
  expressions in `Europe/Rome` timezone
- Every fetcher fires 1-2 hours off from its intended UTC schedule
- The mismatch is **silent** — no error, no warning, no dashboard
  indicator. The only symptom is that `last_run` timestamps in the
  dashboard drift vs. the expected schedule

**Options**:

**(a) Extend the spec to require Beat-specific validation**: Beat MUST
perform the same `CELERY_TIMEZONE`/`CELERY_ENABLE_UTC` validation at
startup (using the `beat_init` signal or equivalent Beat startup hook)
and refuse to start if the values are incorrect. This is the natural
choice — minimal spec change, closes the gap definitively.

**(b) Declare validation as an app-level contract**: reframe the spec
to say "any process loading the Celery app MUST validate these
settings", removing the "worker" qualifier. This covers Beat, workers,
and any future Celery-based process in one statement.

**Recommendation noted during analysis**: option (b) is more robust
long-term (covers the IBS RabbitMQ consumer too, if it shares the
Celery app). Option (a) is narrower but sufficient for this draft.

**Spec locations to update** (once resolved):
- `configuration.md:48-52`: change "worker startup" to "Celery process
  startup" (or list worker + Beat explicitly)
- `fetcher-infrastructure.md:1349`: same adjustment
- `conventions.md:143-146`: same adjustment
- Draft section "Startup Validation" (line 847-850): replace the
  assumption with a normative requirement
- `deployment.md:250-253`: already says "all containers MUST operate
  with UTC" — update enforcement statement to match

### OP-14: Beat Behavior When `FetcherConfig` Table Does Not Exist

**Severity**: Low (operational edge case — deployment ordering will be
specified before implementation)

**Resolution**: RESOLVED — subsumed under OP-1's fail-fast. Any
PostgreSQL error during reconciliation step 1 (connection error, query
error, schema missing) triggers the same fail-fast behavior. The
CRITICAL log message format accommodates any `{error}` string — the
operator will see "relation does not exist" and understand that
migrations need to run. No additional code branch needed.

---

## Specification Content: Fetcher Discovery (Registry Section)

The following is the complete text of the new subsection to be inserted
in `fetcher-infrastructure.md`, in the **Registry** section (after the
`abstract = True` opt-out paragraph, before `## Celery Integration`).

### — BEGIN REGISTRY SECTION TEXT —

### Fetcher Discovery (Module Import)

`FETCHER_REGISTRY` and `_CVE_SOURCE_TYPE_MAP` are module-level
dictionaries populated at **import time** by
`BaseFetcher.__init_subclass__` and `BaseCVEFetcher.__init_subclass__`
respectively. A registry entry exists only after the module defining the
concrete fetcher class has been imported **in the current process**.
Module-level state is per-process — it is NOT shared across the worker,
API server, and Beat processes.

Every process that consumes either registry MUST import all fetcher
modules at startup:

- **Celery workers**: instantiate and execute fetchers
- **FastAPI API server**: list fetchers, serve config, validate
  `Settings` schemas, run on-demand refetch
- **Celery Beat**: build the redbeat schedule from
  `default_schedule`/`FetcherConfig` (see "Celery Beat Schedule
  Synchronization")

To guarantee all three processes import an identical set of modules with
a single maintenance point, imports are centralized in a **discovery
module**:

```python
# backend/app/services/fetcher_discovery.py
# Single source of truth for fetcher module imports.
# Importing this module populates FETCHER_REGISTRY and
# _CVE_SOURCE_TYPE_MAP.
import app.services.tickets.sync_nvd_cves  # noqa: F401
import app.services.packages.sync_smelt_products  # noqa: F401
# ... one line per concrete fetcher
```

Each entrypoint imports the discovery module once at startup:

```python
import app.services.fetcher_discovery  # noqa: F401
```

Importing `fetcher_discovery` triggers all fetcher module imports as a
side effect, populating **both** registries.

**Registration is not persistence**: the registries map fetcher names to
Python class objects, which cannot be serialized to Redis or PostgreSQL.
A process that uses a fetcher (instantiate, execute, read
`default_schedule`, validate `Settings`) MUST have the module imported.
Persisting fetcher metadata would not remove this requirement and would
duplicate code-defined defaults. Mutable per-fetcher state lives in
`FetcherConfig` (PostgreSQL); code identity lives in the registries
(in-process).

**Adding a fetcher**: add one import line to `fetcher_discovery.py`.
**Removing a fetcher**: delete its import line. Entrypoints never change.

**Drift protection**: a test MUST verify that `fetcher_discovery.py`
imports every concrete `BaseFetcher` subclass present in the domain
directories. The test scans the domain packages (`app.services.tickets`,
`app.services.packages`, `app.services.identity`,
`app.services.platform`, `app.services.integrations`) using
`pkgutil.walk_packages`, collects every concrete `BaseFetcher` subclass
found, and asserts each is present in `FETCHER_REGISTRY` after importing
`fetcher_discovery`. If a fetcher module exists without a corresponding
import line, the test fails naming the missing fetcher. The package scan
is confined to the test suite — production code uses only the explicit
imports.

### — END REGISTRY SECTION TEXT —

---

## Specification Content: Celery Beat Schedule Synchronization

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

**Internal key patterns (informational)**: redbeat stores entry data
under `{key_prefix}{entry_name}` keys and internal structures (schedule
index, distributed lock) under `{key_prefix}:{structure_name}` keys
(e.g., `redbeat::schedule`, `redbeat::lock`). These patterns are
library-internal — Sentinel code MUST interact with entries exclusively
via the `RedBeatSchedulerEntry` Python API, never by constructing Redis
keys directly. The patterns are documented here only for operational
debugging (inspecting Redis with `redis-cli`).

### Redbeat Entry Structure

Each registered and enabled fetcher has one redbeat entry:

| Property | Value |
|----------|-------|
| Entry identifier | `fetcher_name` (e.g., `sync_nvd_cves`) — entries are created and accessed via the `RedBeatSchedulerEntry` API using the fetcher name. The underlying Redis key format is managed by the library. |
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

**Preconditions** (satisfied before reconciliation begins):
- `FETCHER_REGISTRY` is populated (via `import app.services.fetcher_discovery`)
- `FetcherConfig` records exist for all registered fetchers (via
  `bootstrap_fetcher_configs()` — see "Who Writes Where" below)

Steps:

1. **Read state from PostgreSQL**: query all `FetcherConfig` records.
   For each registered fetcher, compute the effective schedule
   (`schedule_override` if set, else `default_schedule` from the class
   attribute in `FETCHER_REGISTRY`).

2. **Write entries for enabled registered fetchers**: for each fetcher
   that is (a) present in `FETCHER_REGISTRY` AND (b) has `enabled = true`
   in `FetcherConfig`:
   - Create or unconditionally overwrite the redbeat entry with the
     computed effective schedule and time limit options (derived from
     `run_timeout` per the formula in "Time Limits" above)
   - This is an idempotent upsert — existing entries are updated, missing
     entries are created
   - The overwrite computes `due_at` from the cron schedule relative to
     the current time. Runs missed during Beat downtime are not
     retroactively triggered — data recovery is handled at the
     application level by each fetcher's `catch_up()` mechanism
   - If `schedule_override` cannot be parsed as a valid 5-field cron
     expression, the parsing exception propagates uncaught and prevents
     Beat from completing startup (same fail-fast semantics as a
     PostgreSQL failure). This can only occur if `schedule_override` is
     corrupted via direct database manipulation — the PATCH endpoint
     validates cron syntax before persisting

3. **Remove entries for disabled fetchers**: for each fetcher that is
   present in `FETCHER_REGISTRY` but has `enabled = false`:
   - Delete the redbeat entry if it exists
   - No-op if no entry exists

4. **Remove entries for deregistered fetchers**: enumerate all scheduled
   entries via the redbeat scheduler API. For each entry whose
   `fetcher_name` (extracted from the entry's kwargs) is NOT present in
   `FETCHER_REGISTRY`:
   - Delete the entry
   - Log at INFO level: `"Removed redbeat entry for deregistered fetcher
     '%s'", fetcher_name`

   **Assumption**: this step assumes that all entries in the redbeat
   schedule are fetcher entries (created by this reconciliation or by
   runtime propagation). If Sentinel introduces non-fetcher periodic
   tasks managed via redbeat in the future, this step must be revised to
   avoid interference with those entries.

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

**Error classes covered**: this fail-fast applies to any database-level
error during step 1: connection failures, authentication errors, missing
schema (migrations not yet applied), or query errors. The `{error}`
placeholder contains the specific exception message for operator
diagnosis.

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
   - If `enabled` changed to `false`: delete the redbeat entry. Any
     other field changes in the same PATCH are moot (a disabled fetcher
     has no entry) — skip remaining propagation steps
   - If `enabled` changed to `true`: create the redbeat entry with the
     effective schedule and time limit options (incorporating any
     `schedule_override` or `run_timeout` changes from the same PATCH)
   - If `schedule_override` changed (without `enabled` change): update
     the redbeat entry's schedule with the new effective cron expression
   - If `run_timeout` changed (without `enabled` change): update the
     redbeat entry's Options with the new `time_limit` and
     `soft_time_limit` values (or clear Options if the new value is 0)
   - Uses the `redbeat.RedBeatSchedulerEntry` API to write/delete the
     entry
   - If multiple non-enable propagation-requiring fields changed in the
     same PATCH, a single redbeat write reflects all changes atomically
     (one entry upsert)

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
2. Access pattern: the API endpoint reads the redbeat entry by fetcher
   name via the `RedBeatSchedulerEntry` API. This is an O(1) read per
   fetcher — no full schedule scan.
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

#### Who Writes Where

**Redbeat** (Redis schedule entries) — only two components write:

1. **Celery Beat process** (singleton): writes during startup
   reconciliation
2. **API server process** (potentially multiple replicas): writes during
   PATCH endpoint handling (runtime propagation)

**FetcherConfig** (PostgreSQL) — two types of writes:

1. **Bootstrap** (all processes: worker, Beat, API server):
   `bootstrap_fetcher_configs()` in
   `backend/app/services/fetcher_bootstrap.py`. Idempotent
   `INSERT ON CONFLICT DO NOTHING` at startup. Creates records with
   defaults for newly registered fetchers. Never modifies existing
   records. The function is async; sync callers (worker, Beat) use
   `asyncio.run()`.
2. **PATCH endpoint** (API server only): modifies existing records
   (schedule, enabled, run_timeout, custom_settings).

**Celery workers** do NOT write to redbeat. They only:

- Run the bootstrap (shared with Beat and API)
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

Timezone enforcement (`CELERY_TIMEZONE = UTC`, `CELERY_ENABLE_UTC = True`)
is validated at the **Celery app factory** level — the module that
constructs the `Celery()` application object (e.g.,
`backend/app/celery_app.py`). The validation occurs at module import
time: after the app is configured, the factory checks
`app.conf.timezone == "UTC"` and `app.conf.enable_utc is True`. If
either condition fails, the factory raises a `RuntimeError`:

```
"FATAL: Celery timezone must be UTC. Current value: timezone={timezone},
enable_utc={enable_utc}. All fetcher schedules assume UTC — see
docs/conventions.md."
```

Since every Celery-based process (worker, Beat, IBS RabbitMQ consumer)
MUST import the Celery app object to function, the validation is
inherited automatically — no per-process signal handlers
(`worker_init`, `beat_init`) are needed. The exception prevents any
process from completing initialization.

Additionally, the Beat startup reconciliation implicitly validates:

- PostgreSQL connectivity (reads `FetcherConfig`)
- Redis/redbeat connectivity (writes entries)
- `FETCHER_REGISTRY` population (via `import app.services.fetcher_discovery`
  at process startup — see "Fetcher Discovery (Module Import)" in the
  Registry section)

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

### Step 1b: Insert "Fetcher Discovery" subsection in Registry section

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: after line 1311 (end of the `abstract = True` opt-out
paragraph in "## Registry"), before line 1313 (start of "## Celery
Integration")
**Action**: insert the entire "Fetcher Discovery (Module Import)"
section text from the "Specification Content: Fetcher Discovery" block
above

### Step 1c: Update "Registry Maintenance" checklist

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: lines 1273-1276 (Registry Maintenance subsection)
**Current text**:

```
### Registry Maintenance

When defining a new fetcher, the Fetcher Registry table in
`docs/data-sources.md` MUST be updated with a row for the new fetcher.
```

**Replace with**:

```
### Registry Maintenance

When defining a new fetcher:

1. The Fetcher Registry table in `docs/data-sources.md` MUST be updated
   with a row for the new fetcher.
2. An import line for the fetcher's module MUST be added to
   `backend/app/services/fetcher_discovery.py` (see "Fetcher Discovery
   (Module Import)" below).

When removing a fetcher, both entries (registry table row and discovery
module import line) MUST be removed.
```

### Step 1d: Update `cve-fetcher-infrastructure.md` import requirement

**File**: `docs/features/platform/cve-fetcher-infrastructure.md`
**Location**: lines 675-681 (paragraph about import requirements for
workers and API)
**Current text**:

```
The map is fully populated after all fetcher modules have been imported.
In production, Celery workers import all task modules during startup, so
the map is complete before any consumer reads it. The FastAPI application
MUST also import all fetcher modules at startup (e.g., via an explicit
import in `app/main.py` or a startup event) — the refetch endpoint,
on-demand fetch loop, and sources endpoint all run in the API server
process and depend on a complete registry.
```

**Replace with**:

```
The map is fully populated after all fetcher modules have been imported.
All processes that consume the registry — Celery workers, the FastAPI
API server, and Celery Beat — MUST import the shared discovery module
(`import app.services.fetcher_discovery`) at startup. This single import
populates both `FETCHER_REGISTRY` and `_CVE_SOURCE_TYPE_MAP`. See
`docs/features/platform/fetcher-infrastructure.md` (Fetcher Discovery —
Module Import) for the full mechanism.
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
> property, overrides, lifecycle), registry, fetcher discovery, Celery,
> Beat schedule synchronization, concurrency, stale run detection, data
> model, retention, deregistered lifecycle, doc requirements

### Step 3: Update the Purpose paragraph

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: line 6-12 (Purpose section)
**Action**: add "fetcher discovery" and "Beat schedule synchronization"
to the list of topics. Current:

> ...the fetcher registry, Celery integration, concurrency control, the
> per-ticket `catch_up()` mechanism, custom settings schema, error message
> sanitization, BaseFetcher HTTP client integration, data model, and data
> retention.

New:

> ...the fetcher registry, fetcher discovery, Celery integration, Beat
> schedule synchronization, concurrency control, the per-ticket
> `catch_up()` mechanism, custom settings schema, error message
> sanitization, BaseFetcher HTTP client integration, data model, and data
> retention.

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

### Step 4d: Add time limits to manual trigger in `fetcher-operations.md`

**File**: `docs/features/platform/fetcher-operations.md`
**Location**: lines 473-477 (the `apply_async` call in the Trigger
Fetcher endpoint side effects)
**Current text**:

```
- Passes `run_id` to the Celery task via `run_fetcher.apply_async(kwargs=
  {"fetcher_name": name, "triggered_by": "manual", "user_id": str(user.id),
  "run_id": str(run.id)})`. The task forwards it to
  `fetcher.run(run_id=run_id, ...)`, which updates the existing record
  instead of creating a new one
```

**Replace with**:

```
- Passes `run_id` to the Celery task via `run_fetcher.apply_async(kwargs=
  {"fetcher_name": name, "triggered_by": "manual", "user_id": str(user.id),
  "run_id": str(run.id)}, time_limit=time_limit,
  soft_time_limit=soft_time_limit)` where `time_limit` and
  `soft_time_limit` are read from `FetcherConfig.run_timeout` using the
  same formula as the redbeat entry (see
  `docs/features/platform/fetcher-infrastructure.md`, "Celery Beat
  Schedule Synchronization — Time Limits"). If `run_timeout = 0`, no
  time limits are passed. The task forwards `run_id` to
  `fetcher.run(run_id=run_id, ...)`, which updates the existing record
  instead of creating a new one
```

**Rationale**: the spec states "limits MUST be passed per-invocation via
`apply_async()` options" (`fetcher-infrastructure.md`, Time Limits
section). This applies to ALL invocations — scheduled (via redbeat
entry Options) and manual (via this endpoint). Using
`FetcherConfig.run_timeout` as the single source of truth ensures
consistent behavior regardless of trigger mechanism.

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

### Step 5a: Add Redis Key Conventions to `conventions.md`

**File**: `docs/conventions.md`
**Location**: in the "Python (Backend)" section, after "Testing
Conventions" (end of the backend subsections)
**Action**: insert the following subsection:

> ### Redis Key Conventions
>
> Redis keys in Sentinel fall into two categories with different
> documentation rules:
>
> **Application-owned keys**: keys whose format is defined by Sentinel
> (e.g., `login_attempts:{username}`, `session_liveness:{session_id}`,
> `fetch_pending:{cve_id}:{source}`). These are accessed via the Redis
> client directly. The spec that owns the key MUST document the exact
> format, TTL, and value contract — the format IS the specification.
>
> **Library-managed keys**: keys whose format is defined by a third-party
> library (e.g., `celery-redbeat` schedule entries). Sentinel code MUST
> interact with these exclusively via the library's public API — never by
> constructing Redis keys directly. Specifications MUST describe behavior
> in terms of the library API (e.g., "create an entry via
> `RedBeatSchedulerEntry`"), not in terms of internal key formats (e.g.,
> "write to `redbeat:{name}`"). Internal key patterns may be documented
> as informational notes for operational debugging, clearly marked as
> library-internal.

### Step 5b: Update timezone validation in `configuration.md`

**File**: `docs/configuration.md`
**Location**: lines 48-52 (Startup validation paragraph in "Celery
Worker Configuration")
**Current text**:

```
**Startup validation**: the application MUST validate at Celery worker
startup that these settings are `UTC` and `true` respectively. If either
is overridden to a non-UTC value, the worker MUST refuse to start and
log an error: `"FATAL: Celery timezone must be UTC. Current value:
{value}. All fetcher schedules assume UTC — see docs/conventions.md."`
```

**Replace with**:

```
**Startup validation**: the Celery app factory
(`backend/app/celery_app.py`) MUST validate these settings at module
import time — immediately after the `Celery()` application object is
configured. If `app.conf.timezone != "UTC"` or
`app.conf.enable_utc is not True`, the factory MUST raise a
`RuntimeError` with message: `"FATAL: Celery timezone must be UTC.
Current value: timezone={timezone}, enable_utc={enable_utc}. All fetcher
schedules assume UTC — see docs/conventions.md."`

Since every Celery-based process (worker, Beat, IBS RabbitMQ consumer)
imports the app object, this validation covers all processes
automatically — no per-process signal handlers are needed. The exception
prevents any process from completing initialization.
```

### Step 5c: Update timezone enforcement in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: lines 1346-1351 (Timezone enforcement paragraph in
"Celery Integration")
**Current text**:

```
**Timezone enforcement**: the Celery application is configured with
`timezone = "UTC"` and `enable_utc = True`. All cron expressions in
`default_schedule` and `FetcherConfig.schedule` are interpreted as UTC.
The worker validates these settings at startup and refuses to start if
they are overridden. See `docs/conventions.md` (Timestamps & Timezones)
and `docs/configuration.md` (Celery Worker Configuration).
```

**Replace with**:

```
**Timezone enforcement**: the Celery application is configured with
`timezone = "UTC"` and `enable_utc = True`. All cron expressions in
`default_schedule` and `FetcherConfig.schedule_override` are interpreted
as UTC. The Celery app factory validates these settings at module import
time and raises a `RuntimeError` if they are overridden — this prevents
any Celery-based process (worker, Beat, consumer) from starting with
incorrect timezone configuration. See `docs/conventions.md` (Timestamps
& Timezones) and `docs/configuration.md` (Celery Worker Configuration).
```

### Step 5d: Update timezone enforcement in `conventions.md`

**File**: `docs/conventions.md`
**Location**: lines 143-146 (within the "Timestamps & Timezones"
section, the sentence about Celery worker validation)
**Current text**:

```
The Celery application MUST be
configured with `timezone = "UTC"` and `enable_utc = True` (the
Celery 4+ defaults). These settings MUST NOT be overridden in any
environment — the worker validates them at startup and refuses to
start if they are incorrect (see `docs/configuration.md`, Celery
Worker Configuration)
```

**Replace with**:

```
The Celery application MUST be
configured with `timezone = "UTC"` and `enable_utc = True` (the
Celery 4+ defaults). These settings MUST NOT be overridden in any
environment — the Celery app factory validates them at module import
time and refuses to start any process if they are incorrect (see
`docs/configuration.md`, Celery Worker Configuration)
```

### Step 5e: Update FetcherConfig auto-creation in `fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: lines 1538-1543 (FetcherConfig section, paragraph about
auto-creation)
**Current text**:

```
Per-fetcher configuration, managed by admins. A record is created
automatically when a fetcher is first registered (on worker startup) if
one does not already exist. The auto-creation MUST use an idempotent
operation (`INSERT ... ON CONFLICT DO NOTHING` on the PK `fetcher_name`)
to guarantee safety when multiple workers start concurrently (common in
Kubernetes multi-replica deployments).
```

**Replace with**:

```
Per-fetcher configuration, managed by admins. A record is created
automatically at process startup by `bootstrap_fetcher_configs()`
(`backend/app/services/fetcher_bootstrap.py`) — a shared idempotent
routine that runs in every Celery-based process (worker, Beat, API
server) before role-specific logic begins. The routine executes a batch
`INSERT ... ON CONFLICT DO NOTHING` (on the PK `fetcher_name`) for
every fetcher in `FETCHER_REGISTRY`, guaranteeing safety when multiple
processes start concurrently (common in Kubernetes multi-replica
deployments).

The bootstrap routine:
- **Location**: `backend/app/services/fetcher_bootstrap.py`
- **Signature**: `async def bootstrap_fetcher_configs(db: AsyncSession) -> None`
- **Sync callers**: worker and Beat startup use
  `asyncio.run(bootstrap_fetcher_configs(session))` since they operate
  outside an async event loop. The API server calls it with `await`
  during the FastAPI startup event.
- Runs AFTER `import app.services.fetcher_discovery` (which populates
  `FETCHER_REGISTRY`)
- Runs BEFORE role-specific startup (Beat: reconciliation; API: serving
  requests; worker: consuming tasks)
- Creates records with column defaults (`enabled = true`,
  `run_timeout = 3600`, `request_delay` from `default_request_delay`,
  `custom_settings = '{}'`)
- Never modifies existing records (`DO NOTHING` on conflict)
- Is concurrency-safe: multiple processes running it simultaneously
  produce no conflicts — the first insert succeeds, concurrent
  duplicates are no-ops
```

### Step 5f: Remove "exclusively by workers" from OP-4 context (if still present)

**File**: `docs/features/platform/fetcher-infrastructure.md`
**Location**: any remaining reference to "FetcherConfig records are
created exclusively by workers" or "on worker startup"
**Action**: verify after Step 5e that no stale references remain. The
`request_delay` note at line 1592-1598 says "initialized from the
fetcher's `default_request_delay` class attribute ... at auto-creation
time" — this is still correct (the bootstrap routine uses the class
attribute). No change needed there.

### Step 5g: Update FETCHER_NOT_FOUND 404 condition in `fetcher-operations.md`

**File**: `docs/features/platform/fetcher-operations.md`

**Part A — Generic condition** (lines 317, 435, 460, 581, 668, 746):

**Current condition text**:

```
No `FetcherConfig` record exists for this fetcher name
```

**Replace with**:

```
No fetcher with this name exists (not in the registry and no
`FetcherConfig` record in the database)
```

**Part B — Run Detail endpoint** (line 346 only):

This endpoint (`GET /api/v1/fetchers/{fetcher_name}/runs/{run_id}`)
returns 404 for two distinct reasons. The condition text must preserve
both.

**Current condition text**:

```
No `FetcherConfig` record exists for this fetcher name, or run not found
```

**Replace with**:

```
No fetcher with this name exists (not in the registry and no
`FetcherConfig` record in the database), or the specified run was not
found
```

**Rationale**: with `bootstrap_fetcher_configs()` running in the API
server at startup, a `FetcherConfig` record is guaranteed to exist for
every **registered** fetcher before the API serves requests. The 404 can
only occur for completely unknown names (typos, or fetcher names that
were never registered). The condition text is updated to reflect this —
it is no longer about a "missing record for a known fetcher" but about
a fully unknown identifier.

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
   `docs/features/platform/fetcher-infrastructure.md` — verify both new
   sections (Fetcher Discovery + Beat Schedule Synchronization) are
   coherent with the rest of the document and with
   `fetcher-operations.md`, `cve-fetcher-infrastructure.md`,
   `configuration.md`, `deployment.md`

2. **`@spec-gap-analyzer`** on
   `docs/features/platform/fetcher-infrastructure.md` — verify the new
   sections do not introduce gaps (all edge cases covered, no ambiguous
   behavior)

3. **`@docs-reviewer`** on the set of modified documents — verify
   cross-references are correct, no broken links, no contradictions
   between the updated documents

4. **`@docs-placement-reviewer`** on
   `docs/features/platform/fetcher-infrastructure.md` — verify the new
   content is correctly placed (Fetcher Discovery in the Registry section,
   Beat sync after Celery Integration — not misplaced or over-generalized)

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
| Timezone validation (configuration.md) — reframed as app-level contract (app factory, import-time). Steps 5b/5c/5d update three documents to remove "worker" qualifier. Beat and IBS consumer are now covered automatically | Steps 5b, 5c, 5d added |
| FetcherConfig auto-creation — reframed from "workers only" to "shared bootstrap in all processes". Steps 5e/5f update `fetcher-infrastructure.md`. Draft spec "Who Writes Where" section already reflects the corrected invariant | Steps 5e, 5f added |
| Deregistered Fetcher Lifecycle section — already says "Celery Beat does not schedule it". New section explains the mechanism (entry removed at startup). Consistent | No change needed |
| `data-model.md` FetcherConfig table — no schema changes needed (all information is already in the model) | No change needed |
| Fetcher Discovery placement: in Registry section (cross-cutting concern for all processes), not in Beat sync section. Beat sync references it. `cve-fetcher-infrastructure.md` import requirement updated to reference the shared mechanism | No change needed |
| `_CVE_SOURCE_TYPE_MAP` population: the discovery module import populates both registries as a natural side effect. Documented in the Fetcher Discovery subsection | No change needed |
| Manual trigger time limits (`fetcher-operations.md`): pre-existing gap where `apply_async()` omitted `time_limit`/`soft_time_limit`. Step 4d fixes this by reading from `FetcherConfig.run_timeout` (same formula as redbeat entry). All invocations now consistently bounded | Step 4d added |
