# Draft: Non-Fetcher Periodic Tasks — Celery Beat Scheduling Mechanism

**Status**: Draft — pending review
**Type**: Specification fix (bug in spec, no implementation exists yet)
**Affected specs**: `docs/features/platform/fetcher-infrastructure.md`,
`docs/features/identity/authentication.md`,
`docs/features/tickets/tickets.md`, `docs/deployment.md`

## 1. Problem Statement

Sentinel has exactly two periodic background tasks that are explicitly
documented as **not** being `BaseFetcher` subclasses:

| Task | Owning spec | Schedule | Current wording |
|------|-------------|----------|------------------|
| `cleanup_sessions` | `docs/features/identity/authentication.md:285-306` | Sunday 03:00 UTC (fixed, not configurable) | "This is a maintenance task, not a `BaseFetcher` subclass" |
| `cleanup_stale_ticket_access_grants` | `docs/features/tickets/tickets.md:1007-1027` | Sunday 04:00 UTC | "Plain Celery Beat task (NOT a `BaseFetcher`...)" |

Neither spec states **how** the task is registered with Celery Beat.
This matters because Sentinel's Beat scheduler is
`redbeat.RedBeatScheduler` (`docs/configuration.md:73-77`,
`docs/features/platform/fetcher-infrastructure.md:1516-1524`), and the
fetcher infrastructure spec defines a **startup reconciliation**
procedure (`fetcher-infrastructure.md`, "Startup Reconciliation",
step 4, lines ~1682-1694) that:

> enumerates all scheduled entries via the redbeat scheduler API. For
> each entry whose `fetcher_name` (extracted from the entry's kwargs)
> is NOT present in `FETCHER_REGISTRY`: delete the entry.

This step carries an explicit self-flagged assumption:

> **Assumption**: this step assumes that all entries in the redbeat
> schedule are fetcher entries (created by this reconciliation or by
> runtime propagation). If Sentinel introduces non-fetcher periodic
> tasks managed via redbeat in the future, this step must be revised
> to avoid interference with those entries.

Sentinel **has already introduced** two such non-fetcher periodic
tasks (in spec form) — the assumption is violated by the project's own
specs, not just a hypothetical future case.

### 1.1 Why this is an infrastructural bug, not just a doc gap

Beat is a **singleton** process configured with a single scheduler
class (`beat_scheduler = 'redbeat.RedBeatScheduler'`). There is only
one Celery Beat schedule in the system — every periodic task, fetcher
or not, becomes a **redbeat entry** the moment it is registered,
because redbeat is the only scheduler Sentinel runs. There is no way
to have "some tasks in redbeat, some tasks elsewhere" — the choice is
only about **which redbeat lifecycle mechanism manages a given
entry**, not whether the entry lives in redbeat at all.

Consequence: whatever mechanism is chosen to register
`cleanup_sessions` and `cleanup_stale_ticket_access_grants`, their
entries will appear in the same redbeat keyspace that step 4 scans.
Unless step 4 is scoped to fetcher entries only, it will delete these
two entries at every Beat startup — including the first one, before
they ever get a chance to run (a newly created redbeat entry with no
`last_run_at` is immediately due, so the exact outcome depends on
timing, but the entries are deleted deterministically regardless of
timing). Net effect: `cleanup_sessions` and
`cleanup_stale_ticket_access_grants` would never execute reliably,
causing unbounded growth of the `Session` and `TicketAccessGrant`
tables — precisely the failure mode these tasks exist to prevent.

This is a **High-severity latent bug in the specification**: if
implemented exactly as currently written, the system would silently
fail to perform two maintenance functions with no error, no log
(beyond the existing "Removed redbeat entry for deregistered fetcher"
INFO line, which would be misleadingly logged for tasks that are not
fetchers at all), and no operator-visible symptom other than slow,
delayed database growth.

### 1.2 Technical clarification: static schedules ARE redbeat entries

A plausible reading of the original finding is "choose between a
redbeat entry and a static `beat_schedule` entry," implying these are
mutually exclusive storage mechanisms. This is incorrect and must not
guide the fix.

`celery-redbeat`'s `RedBeatScheduler.setup_schedule()` (library
behavior, not Sentinel code) natively loads `app.conf.beat_schedule`
(the standard Celery static schedule dict) into Redis, using the same
key prefix (`redbeat:`) and the same `RedBeatSchedulerEntry.save()`
persistence path used by dynamically-managed entries. It additionally
tracks static entry names in a dedicated `redbeat::statics` Redis SET,
and on each Beat startup removes any previously-tracked static entry
whose name no longer appears in the current `beat_schedule` dict —
i.e., redbeat already provides a complete, self-contained lifecycle
for statically declared periodic tasks, symmetrical to (but
independent of) Sentinel's custom reconciliation for fetchers.

Therefore, the real design question is not "redbeat vs. something
else" — it is: **"which of the two coexisting redbeat lifecycle
managers (redbeat's native static-entry handling, or Sentinel's
custom fetcher reconciliation) owns a given entry, and how do we keep
them from stepping on each other's entries?"**

## 2. Decision

Adopt the following design (this section is the resolved decision;
Section 3 is the prescriptive change plan that implements it):

1. **Non-fetcher periodic tasks are declared as static entries in
   `app.conf.beat_schedule`** (the standard Celery configuration dict,
   set on the Celery app object during app construction). This is a
   pure code-level declaration — no database record, no
   `FetcherConfig` row, no admin-configurable schedule.
2. **Redbeat's native static-entry handling owns these entries.** No
   Sentinel code writes, updates, or deletes them; they are entirely
   managed by `RedBeatScheduler.setup_schedule()` at every Beat
   startup (install/refresh matching current `beat_schedule` dict;
   remove entries whose static declaration disappeared).
3. **Sentinel's fetcher startup-reconciliation step 4 is scoped to
   fetcher entries only.** The discriminator is the Celery task name:
   every fetcher entry has `task == "run_fetcher"` (per the Redbeat
   Entry Structure table already in `fetcher-infrastructure.md`).
   Step 4 must only consider — and only delete — entries whose `task`
   is `"run_fetcher"` and whose `fetcher_name` kwarg is absent from
   `FETCHER_REGISTRY`. Entries with any other `task` value (i.e., the
   two non-fetcher tasks, and Celery framework internals if any) are
   left untouched.
4. **Wiring/ordering is irrelevant to this fix.** The scoping in point
   3 above (delete only `task == "run_fetcher"` entries) means the
   fetcher reconciliation cannot interfere with static entries
   regardless of whether it runs before or after redbeat's native
   static-entry installation, and regardless of the mechanism used to
   invoke the reconciliation at Beat startup. This change therefore
   does **not** introduce a custom scheduler subclass and does **not**
   change the configured scheduler class
   (`beat_scheduler = 'redbeat.RedBeatScheduler'` stays as-is).
   **Separately**, the current spec never states how or where the
   fetcher reconciliation procedure itself is invoked at Beat process
   startup — it describes *what* the reconciliation does but not its
   invocation point. This is a genuine, pre-existing gap, but it is
   independent of the bug this draft fixes (confirmed above: the fix
   works regardless of the wiring mechanism), so resolving it is
   **out of scope** for this change. It is tracked separately as
   OP-19 in `docs/drafts/open-points.md` (already recorded — see
   note after the Action Plan, "Already-Applied Side Finding").
5. **These two tasks remain permanently outside** `FetcherConfig`,
   `FETCHER_REGISTRY`, the fetcher dashboard, and PostgreSQL-based
   schedule sourcing. Their schedule's source of truth is the code
   (the `beat_schedule` dict), consistent with them being fixed,
   non-configurable schedules — this does not weaken the existing
   "PostgreSQL is authoritative" invariant, because that invariant is
   explicitly scoped to fetcher schedules
   (`fetcher-infrastructure.md`, "Architecture: PostgreSQL-master,
   Redbeat-slave").

### 2.1 Alternatives considered and rejected

| Option | Why rejected |
|--------|--------------|
| Convert both tasks into `BaseFetcher` subclasses | Both specs explicitly and correctly state they are not fetchers (no external source, no create/update/fail metrics that make sense for a DELETE operation, fixed non-configurable schedule contradicts the `FetcherConfig` model). Guardrail 14 scopes `BaseFetcher` to external-source ingestion. Would pollute the fetcher dashboard with meaningless entries. |
| New parallel infrastructure (e.g., `BasePeriodicTask` base class, its own registry/reconciliation) | Substantial new machinery (registry, startup reconciliation, failure semantics, documentation) to manage exactly two fire-and-forget tasks. Duplicates functionality `celery-redbeat` already provides natively for static schedules. Revisit only if the platform later accumulates many more non-fetcher periodic maintenance tasks that need dashboard visibility or admin configurability. |
| Extend Sentinel's custom reconciliation to also manage these two tasks (hardcoded list, still going through Sentinel's own upsert/delete logic) | Reinvents what redbeat's native static-entry handling already does for free; introduces a second hardcoded list to keep in sync with `beat_schedule` declarations; no benefit over just using `beat_schedule` directly. |
| Defer the decision (original recommendation from the prior analysis) | The mechanism decision is not actually open — the fixed-schedule requirement of both tasks makes `beat_schedule` the obviously correct mechanism. Deferring leaves a self-flagged, High-severity latent bug live in the infrastructure spec with no tracking beyond an inline comment. Since the project is spec-only right now, the cost of resolving immediately is limited to editing three documents. |
| Bundle the Beat reconciliation wiring specification (how/where reconciliation is invoked at startup) into this change | Considered and rejected during drafting: the interference fix (point 3 in Section 2) works correctly regardless of the wiring mechanism chosen, so bundling it would be unrelated scope creep. Tracked independently as OP-19 in `docs/drafts/open-points.md`. |

## 3. Action Plan

This plan is prescriptive: each step names the exact file, exact
section/anchor, and the exact nature of the edit. All changes are to
specification documents only — no code or database exists to migrate.

### Step 1 — `docs/features/platform/fetcher-infrastructure.md`: fix Startup Reconciliation step 4

**Location**: "Startup Reconciliation" → numbered steps list (currently
step 4, immediately followed by the "Assumption" paragraph).

**Edit**:

1. Replace the step 4 body text. Current wording enumerates "all
   scheduled entries" and deletes any whose `fetcher_name` is not in
   `FETCHER_REGISTRY`. New wording must:
   - Restrict the enumeration/consideration to entries whose `task`
     attribute equals `"run_fetcher"` (cross-reference the existing
     "Redbeat Entry Structure" table, which already documents `Task |
     run_fetcher` for every fetcher entry).
   - The `task` check MUST act as a pre-filter, applied strictly before
     any kwargs inspection: entries whose `task` is not `"run_fetcher"`
     are skipped entirely, without attempting to extract `fetcher_name`
     from their kwargs. State this ordering explicitly — a non-fetcher
     static entry has no `fetcher_name` kwarg, so an implementation
     that extracts it unconditionally (e.g., via `kwargs.get()`) before
     checking `task` would silently treat the missing value as "not in
     `FETCHER_REGISTRY`" and delete the entry, reintroducing the exact
     bug this step is meant to fix.
   - For each entry that passes the `task` pre-filter, keep the
     existing logic: delete if `fetcher_name` (from kwargs) is not in
     `FETCHER_REGISTRY`.
   - Explicitly state that entries whose `task` is not `"run_fetcher"`
     are left untouched by this step (they are owned by redbeat's own
     static-entry handling — forward-reference the new subsection
     added in Step 2 below).
2. Remove the existing "**Assumption**: this step assumes..." paragraph
   entirely — it documented a known limitation that this change
   resolves. Do not replace it with a new caveat; the scoping rule
   above removes the ambiguity that the assumption was flagging.
3. Update the reconciliation summary log line if it currently implies
   "all entries" were considered (check working text around "Beat
   schedule reconciliation complete: %d entries written, %d disabled
   removed, %d deregistered removed" — no change needed here since the
   counts already refer only to fetcher entries, but re-read the
   surrounding prose to confirm no other sentence implies a
   whole-keyspace scan).

**Insufficiency check**: after this edit, an implementer must be able
to write the exact filter condition without guessing. Verify the final
wording states literally: "entries where `task == 'run_fetcher'`" (or
equivalent unambiguous phrasing) — not a vaguer "fetcher-related
entries."

### Step 2 — `docs/features/platform/fetcher-infrastructure.md`: document non-fetcher periodic tasks

**Location**: new subsection immediately after "### Reconciliation
and Divergence Recovery" and its subsections, and before "### Multi-
Process Coordination" (line 2051) — i.e., placed alongside the other
Beat/redbeat mechanism subsections within "## Celery Beat Schedule
Synchronization", not buried inside "Startup Reconciliation" (that
section is about the fetcher reconciliation algorithm specifically;
this new content is about a parallel, independent mechanism that the
reconciliation algorithm must coexist with).
Suggested heading: "### Non-Fetcher Periodic Tasks".

**Edit**: add a new top-level section with the following content
(not verbatim prose — the author drafting the real edit should adapt
tone/style to match the surrounding document, but must cover every
point below):

- **Purpose**: Sentinel has a small number of periodic maintenance
  tasks (garbage collection / cleanup) that are not `BaseFetcher`
  subclasses because they do not fetch data from an external source.
  These tasks still need a Celery Beat schedule, and since Beat runs
  a single scheduler (`redbeat.RedBeatScheduler`, per the "Redbeat
  Configuration" table above — unchanged by this section), their
  entries necessarily live in the same redbeat keyspace as fetcher
  entries.
- **Mechanism**: such tasks are declared as static entries in
  `app.conf.beat_schedule` (the Celery configuration dict), set once
  at Celery app construction time (code-level, not runtime-mutable).
  This assignment MUST happen before the Celery Beat scheduler is
  instantiated — `setup_schedule()` reads `app.conf.beat_schedule`
  during scheduler initialization, so if the dict is populated later
  (e.g., via a `beat_init` signal handler or a module imported after
  scheduler construction), the entries are not installed and any
  previously-tracked static entries are removed as if they had been
  deleted from the codebase. Redbeat's own `setup_schedule()` (native,
  unmodified library behavior — Sentinel does not subclass or override
  it) installs, refreshes, and removes these entries automatically at
  every Beat startup, tracking them via its internal `redbeat::statics`
  bookkeeping. Sentinel code never directly creates, updates, or
  deletes these entries.
- **Out of scope note**: this section does not specify how or where
  Sentinel's fetcher startup-reconciliation procedure (see "Startup
  Reconciliation" above) is itself invoked at Beat process startup —
  that is a separate, pre-existing gap tracked as OP-19 in
  `docs/drafts/open-points.md`. It does not need to be resolved here
  because the scoping rule in step 4's revised wording (this document,
  above) makes the two mechanisms safe to coexist regardless of
  invocation order or wiring mechanism.
- **Explicit non-goals / boundaries** (state all of these to prevent
  future ambiguity):
  - These tasks are **never** registered in `FETCHER_REGISTRY` and
    **never** have a `FetcherConfig` row.
  - These tasks **do not appear** in the fetcher dashboard
    (`GET /api/v1/fetchers` and related endpoints) — they are outside
    the fetcher subsystem entirely.
  - Their schedule is **fixed in code**. There is no admin-facing way
    to change their schedule (no PATCH endpoint, no
    `schedule_override`). If a future requirement needs
    admin-configurable scheduling for a maintenance task, that task
    should be reconsidered as a `BaseFetcher` subclass (with
    `custom_settings`/`FetcherConfig`) rather than extending this
    mechanism — this mechanism is intentionally minimal and must stay
    that way; do not add configurability to it without revisiting this
    decision.
  - The "PostgreSQL is the authoritative source of schedules" invariant
    (see "Architecture: PostgreSQL-master, Redbeat-slave") applies
    **only** to fetcher entries. For non-fetcher periodic tasks, the
    **code** (the `beat_schedule` declaration) is authoritative. State
    this explicitly to avoid contradicting the existing invariant
    language, which currently reads as a blanket statement about "the
    redbeat schedule" without this carve-out.
  - The task discriminator used by fetcher reconciliation (Step 1 of
    this plan: `task == "run_fetcher"`) means any future non-fetcher
    periodic task automatically coexists safely with fetcher
    reconciliation **as long as it does not use the task name
    `"run_fetcher"`**. State this explicitly as the compatibility
    contract for any future addition to `beat_schedule`.
  - **Entry name collision constraint**: `beat_schedule` dict keys
    (which redbeat uses as the entry identifier, and therefore as the
    Redis key) MUST NOT match any name in `FETCHER_REGISTRY`. The
    `task` discriminator above only protects fetcher reconciliation's
    deletion step (step 4) from touching non-fetcher entries — it does
    NOT protect against a write-path collision: fetcher reconciliation
    step 2 performs an unconditional upsert of every enabled fetcher's
    entry by name, and redbeat's `setup_schedule()` performs the same
    unconditional upsert for static entries by name. If a future
    non-fetcher task's `beat_schedule` key happened to equal an
    existing or future fetcher's name, both mechanisms would write to
    the same Redis key on every Beat startup, each overwriting the
    other's data non-deterministically. State this explicitly as a
    second, independent compatibility constraint (name uniqueness, in
    addition to the task-name constraint above) for any future
    addition to `beat_schedule`. No runtime validation is introduced
    for this constraint — it is a documented naming rule for whoever
    adds a new entry.
  - **Behavior on Redis data loss**: redbeat preserves each static
    entry's `last_run_at` metadata across a normal Beat restart (Redis
    data intact), so a task does not fire early merely because Beat
    restarted. However, if Redis loses its data (restart without
    persistence, or `FLUSHALL` — see `docs/deployment.md`, "Redis
    Durability, Memory, and Persistence"), the `last_run_at` metadata
    is lost along with everything else in the keyspace. On the next
    Beat startup (triggered by the existing lock-sentinel recovery
    mechanism — see "Runtime: Redis Data Loss" elsewhere in this
    document), redbeat reinstalls each static entry with no prior
    `last_run_at`, which makes it evaluate as due immediately: each
    non-fetcher periodic task fires **once**, shortly after the
    Beat restart that follows the data-loss event, ahead of its
    normal weekly schedule. State this explicitly as expected,
    accepted behavior — not a bug — for the following reason: both
    `cleanup_sessions` and `cleanup_stale_ticket_access_grants` are
    idempotent deletion queries with static, time-based filter
    conditions (e.g., `updated_at < now() - interval '14 days'`);
    running one extra time ahead of schedule deletes only rows that
    were already eligible for deletion and has no correctness impact.
    Contrast this explicitly with fetcher entries, whose custom
    reconciliation computes `due_at` from the cron schedule relative
    to current time specifically to avoid retroactively firing missed
    runs (see "Startup Reconciliation" step 2) — the two mechanisms
    have deliberately different behavior here, and this difference is
    acceptable only because non-fetcher periodic tasks are restricted
    by design to idempotent maintenance operations (see the "no
    admin-facing configurability" boundary above). If a future
    non-fetcher periodic task is NOT idempotent with respect to an
    extra unscheduled run, it must not use this mechanism as-is
    without re-evaluating this behavior.
- **Current inventory**: list the two existing tasks by name, with a
  cross-reference to their owning specs (do not duplicate their
  schedules or deletion logic here — those remain owned by
  `authentication.md` and `tickets.md` respectively; this section only
  owns the *mechanism*, not each task's business logic):
  - `cleanup_sessions` — see
    `docs/features/identity/authentication.md` (Session cleanup)
  - `cleanup_stale_ticket_access_grants` — see
    `docs/features/tickets/tickets.md` (Stale Access Grant Cleanup)

**Coherence check for this step**: re-read the full
"Celery Beat Schedule Synchronization" top-level section after adding
this subsection to confirm no other sentence in that section makes a
blanket claim like "every redbeat entry is a fetcher entry" or "the
redbeat schedule contains only fetcher entries" — if such a sentence
exists elsewhere (e.g., in "Reconciliation and Divergence Recovery" or
"Multi-Process Coordination"), it must be qualified or corrected in
the same edit pass, otherwise the new subsection contradicts it.

### Step 3 — `docs/features/identity/authentication.md`: cross-reference the mechanism

**Location**: "Session cleanup" subsection, immediately after the
existing sentence "This is a maintenance task, not a `BaseFetcher`
subclass (it does not fetch data from external sources)."

**Edit**: append one sentence: the task is registered as a static
`beat_schedule` entry and its Beat registration mechanism is fully
specified in `docs/features/platform/fetcher-infrastructure.md`
("Non-Fetcher Periodic Tasks"). Do not restate the mechanism details
here — this is a pure cross-reference, per the project's
cross-cutting placement convention (Guardrail 21 / `conventions.md`
information placement rules): the mechanism is owned by
fetcher-infrastructure.md, this spec only needs to point to it.

### Step 4 — `docs/features/tickets/tickets.md`: cross-reference the mechanism

**Location**: "Stale Access Grant Cleanup" subsection, in the bullet
list that currently has "**Type**: Plain Celery Beat task (NOT a
`BaseFetcher`, as it does not fetch external data)."

**Edit**: modify that bullet (or add an adjacent one) to state that
registration uses a static `beat_schedule` entry, with the mechanism
fully specified in
`docs/features/platform/fetcher-infrastructure.md` ("Non-Fetcher
Periodic Tasks"). Same non-duplication rule as Step 3.

### Step 5 — `docs/deployment.md`: qualify the "reconstructible from PostgreSQL" claim

**Origin**: discovered as a side finding during the coherence analysis
of this draft (not caused by this change, but exposed by it).

**Problem**: `docs/deployment.md` ("Redis Durability, Memory, and
Persistence" → "Persistence is Disabled by Design", point 1) states:

> "No durable data lives solely in Redis. PostgreSQL is the source of
> truth for all persistent state (sessions, schedules, task outcomes,
> mutation serialization). Every Redis key is either TTL-bounded and
> self-healing, or fully reconstructible from PostgreSQL via Beat's
> startup reconciliation."

After this change, non-fetcher static entries are a third category:
they are neither TTL-bounded nor reconstructible from PostgreSQL — they
are reconstructible from **code** (the `beat_schedule` declaration) via
redbeat's native `setup_schedule()`, a different mechanism than
Sentinel's custom startup reconciliation. The sentence's universal
claim ("every Redis key is either X or Y") becomes inaccurate for this
category, even though the operational conclusion it supports (Redis
persistence can be safely disabled; everything recovers automatically
at Beat startup) remains correct and unaffected.

**Fix**: qualify the sentence to name both reconstruction sources, e.g.:
"...or fully reconstructible at Beat startup — from PostgreSQL (fetcher
schedules, via Sentinel's startup reconciliation) or from code
(non-fetcher static entries, via redbeat's native `setup_schedule()`)."
Cross-reference `fetcher-infrastructure.md` ("Non-Fetcher Periodic
Tasks", added in Step 2) for the mechanism detail — do not duplicate it
here.

**Scope note**: this is a one-sentence qualification, not a rewrite of
the surrounding rationale. Points 2 and 3 of the same "Rationale" list
(lock sentinel recovery, persistence undermining the lock sentinel)
remain accurate as-is and require no change.

### Step 6 — Internal coherence pass across the four edited documents

After Steps 1-5 are applied, perform a single read-through pass
checking specifically for:

1. **No duplicated mechanism description**: the *how* (static
   `beat_schedule`, redbeat native handling, task-name discriminator,
   entry-name uniqueness constraint) must appear only in
   `fetcher-infrastructure.md`. `authentication.md`, `tickets.md`, and
   `deployment.md` must contain only a cross-reference (the latter via
   the qualified sentence from Step 5, not a full restatement).
2. **No contradiction with "PostgreSQL-master, Redbeat-slave"**:
   confirm the carve-out language added in Step 2 is consistent with
   every other mention of that architecture principle in the document.
3. **Terminology consistency**: use "non-fetcher periodic task"
   consistently (matches the heading proposed in Step 2) rather than
   introducing synonyms like "maintenance task" as a formal term —
   "maintenance task" may remain as informal prose (it is already used
   in `authentication.md`) but the formal mechanism section heading
   and cross-references should consistently say "non-fetcher periodic
   task[s]".
4. **No accidental scheduler-class change**: confirm no edit introduced
   a reference to a custom scheduler subclass or changed the
   `beat_scheduler` configuration value — this change intentionally
   keeps the stock `redbeat.RedBeatScheduler` (see Section 2, point 4).

### Step 7 — `docker-compose.yml`: align local dev Redis with the no-persistence invariant

**Origin**: discovered as a side finding while investigating this
change (Redis data-loss recovery behavior for static entries, Step 2 /
Step 6 above), not caused by this change. Recorded here because it was
found during this investigation and is cheap to fix in the same pass;
it is otherwise unrelated to the non-fetcher periodic task mechanism.
This is a separate finding from Step 5 above (`deployment.md` wording
qualification) — Step 5 fixes a textual inaccuracy in a spec, this step
fixes an actual infrastructure configuration drift.

**Problem**: `docs/deployment.md` ("Redis Durability, Memory, and
Persistence" → "Persistence is Disabled by Design") states that Redis
persistence (RDB and AOF) **MUST be disabled in all environments**,
with the explicit configuration `save ""` / `appendonly no`. The
rationale given there is load-bearing for this draft's Step 2 analysis
(Redis data loss must always be "clean" — i.e., it must always wipe the
`redbeat::lock` key — for the Beat lock-sentinel recovery mechanism to
fire reliably; see `deployment.md`, point 3, "Persistence would
undermine the lock sentinel").

The repository's local development `docker-compose.yml` (top-level
`redis:` service) currently:

- uses the `redis:7` image without any command override or config file
  supplying `save ""` / `appendonly no` — the upstream image's default
  RDB snapshotting (`save 3600 1 300 100 60 10000`) remains active, and
- mounts a named volume (`redis_data:/data`) that persists across
  container restarts.

This means a local Redis container restart in the dev environment can
reload a recent RDB snapshot, potentially restoring the
`redbeat::lock` key before it would have naturally expired — exactly
the scenario `deployment.md` point 3 identifies as undermining the
lock sentinel. This is a **pre-existing inconsistency** between the
deployment spec and the local dev environment configuration, not
something introduced by this change; it is being fixed here purely
because it was surfaced during this investigation and the fix is
small.

**Scope note**: `docker-compose.yml` is deployment/infrastructure
configuration, not a specification document under `docs/`. Per the
project's agent scope rules, this file must be edited by a session
with `docker-compose.yml`/CI-CD editing scope (e.g., the `@cicd` agent
or a direct implementation session) — **not** as part of the spec-only
edits in Steps 1-6 above, and not by this drafting session. This step
is recorded here for completeness of the investigation, to be executed
as a small, independent follow-up alongside (or after) the spec edits.

**Fix**: add an explicit command override to the `redis:` service in
`docker-compose.yml` disabling persistence, matching
`docs/deployment.md`'s mandated configuration, e.g.:

```yaml
redis:
  image: redis:7
  command: ["redis-server", "--save", "", "--appendonly", "no"]
  ...
```

Additionally, evaluate whether the `redis_data` named volume and its
mount (`redis_data:/data`) should be removed from the `redis:` service
now that persistence is explicitly disabled — an unused volume for a
non-persistent service is misleading to a reader of the compose file,
even though it is functionally inert once `save ""` takes effect
(nothing will ever be written to it). Removing it is preferred for
clarity, but keeping it is not a correctness bug — decide based on
reviewer input during Step 8.

**Verification**: after the fix, confirm via `redis-cli CONFIG GET
save` and `redis-cli CONFIG GET appendonly` against the local
`dev-env.sh up` stack that the running container reflects `save ""`
and `appendonly no`.

> **Already-Applied Side Finding — OP-19**: while drafting this plan,
> we identified that the Beat reconciliation wiring gap referenced in
> Section 2, point 4 (how/where the fetcher reconciliation is invoked
> at Beat startup — a pre-existing gap, unrelated to the correctness
> of the fix in this draft) is worth tracking. Rather than staging it
> as an Action Plan step, **it was recorded directly** as **OP-19** in
> `docs/drafts/open-points.md` (summary table row + detail subsection,
> placed after OP-18 in "Open — Cross-Process Startup") during the
> drafting of this document. No further action is needed for OP-19 as
> part of this change — it is listed here only so the reader knows
> where the reference in Section 2, point 4 leads, and so Step 8
> (reviewers) knows it does not need to verify an unapplied step for
> it.

### Step 8 — Invoke reviewers

Once Steps 1-7 are complete and applied (spec edits from Steps 1-6,
`docker-compose.yml` fix from Step 7; OP-19 already recorded in
`docs/drafts/open-points.md` per the note above), invoke the
following reviewers, each scoped to the specs actually modified by
this change (`fetcher-infrastructure.md`, `authentication.md`,
`tickets.md`, `deployment.md`):

1. `@spec-coherence-reviewer` — once per modified spec (per the
   project convention of one independent session per spec when doing
   a multi-spec review), to verify no contradictions were introduced
   between the four documents or with other specs that reference
   Celery Beat / redbeat (e.g., `fetcher-operations.md`,
   `configuration.md`, `architecture.md`).
2. `@spec-gap-analyzer` — on `fetcher-infrastructure.md` only (the
   spec receiving the substantial new content: Steps 1-2), to verify
   the revised reconciliation algorithm and the new "Non-Fetcher
   Periodic Tasks" section are functionally complete (state
   transitions, error paths, boundary conditions all covered per the
   Function Specification Completeness convention).
3. `@docs-placement-reviewer` — to verify the cross-cutting placement
   decision made in this draft (mechanism owned by
   `fetcher-infrastructure.md`, referenced by the two feature specs
   and by `deployment.md`) is correctly applied and that no
   duplication slipped through Step 6.
4. `@docs-reviewer` — general completeness/coherence pass across all
   four modified documents, since this change spans multiple
   documentation files in the same set of edits.
5. `@cicd` — to verify the `docker-compose.yml` fix from Step 7 is
   correctly applied and does not introduce any other drift against
   `docs/deployment.md`.

If any reviewer flags a "Needs revision" issue, resolve it before
proceeding to Step 9. Minor issues should be fixed in the same editing
pass.

### Step 9 — Delete this draft

Once Steps 1-8 are complete (all spec edits applied and all reviewers
have signed off with no outstanding "Needs revision" issues), delete
this file
(`docs/drafts/non-fetcher-periodic-tasks-beat-scheduling.md`). This
draft's sole purpose is to stage the reviewable plan; it must not
persist as a permanent artifact once the change is fully applied and
verified, per the project's draft-document lifecycle (drafts are
working documents, not archival records — the resolved content lives
in the target specs themselves after this change).

## 4. Summary of File-Level Impact

| File | Nature of change |
|------|-------------------|
| `docs/features/platform/fetcher-infrastructure.md` | Fix step 4 of Startup Reconciliation (scope to `task == "run_fetcher"`, with explicit task-check-before-kwargs-extraction ordering); remove the now-resolved "Assumption" caveat; add new "Non-Fetcher Periodic Tasks" top-level section (including the Redis-data-loss "fires once" behavior, the entry-name uniqueness constraint, and the `beat_schedule` construction-time timing requirement, Step 2). Does NOT change the configured scheduler class or introduce a scheduler subclass — the stock `redbeat.RedBeatScheduler` is unaffected |
| `docs/features/identity/authentication.md` | One sentence added to "Session cleanup", cross-referencing the mechanism |
| `docs/features/tickets/tickets.md` | One bullet modified/added in "Stale Access Grant Cleanup", cross-referencing the mechanism |
| `docs/deployment.md` | One sentence qualified in "Persistence is Disabled by Design" (point 1) to account for non-fetcher static entries as a third, code-reconstructible category (Step 5) |
| `docker-compose.yml` | Add `command: ["redis-server", "--save", "", "--appendonly", "no"]` (or equivalent) to the `redis:` service to align local dev with `docs/deployment.md`'s no-persistence invariant; evaluate removing the now-inert `redis_data` volume (Step 7). Pre-existing inconsistency, unrelated to the core mechanism change, fixed opportunistically in the same pass |
| `docs/drafts/open-points.md` | New entry OP-19 (Beat Reconciliation Wiring Mechanism Not Specified) — summary table row + detail subsection. **Already applied** during the drafting of this document (see the note after Step 7); not a pending Action Plan step |
| `docs/drafts/non-fetcher-periodic-tasks-beat-scheduling.md` (this file) | Deleted at the end of the process (Step 9) |

## 5. Non-Goals of This Change

- Does not implement any code (no code exists yet for this project).
- Does not introduce configurability for the two existing non-fetcher
  tasks — their schedules remain fixed, as already specified.
- Does not create a new abstraction/base-class framework for
  non-fetcher periodic tasks (Option rejected in Section 2.1) — the
  mechanism is intentionally the minimal one (native redbeat static
  handling).
- Does not change the business logic, audit behavior, or deletion
  criteria of `cleanup_sessions` or `cleanup_stale_ticket_access_grants`
  — only their Beat registration mechanism is being specified.
- Does not introduce a custom Celery Beat scheduler subclass or change
  the `beat_scheduler` configuration value. An earlier version of this
  draft proposed this (to guarantee deterministic ordering between
  static-entry installation and fetcher reconciliation); further
  investigation established that the scoping fix in Step 1 makes such
  ordering unnecessary for correctness, so the subclass was dropped
  from this plan. The separate, pre-existing question of how
  reconciliation is wired at Beat startup is tracked as OP-19
  (already recorded in `docs/drafts/open-points.md`, see the note
  after Step 7) and explicitly deferred.
- The `docker-compose.yml` fix (Step 7) is an opportunistic, pre-existing
  finding unrelated to the mechanism itself — it does not touch any
  application code and does not depend on Steps 1-6 being applied
  first (it may be executed independently, but is bundled into this
  plan since it was discovered during this investigation).
