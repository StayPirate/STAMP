# RFC: Remove CELERY_RESULT_BACKEND from Sentinel Configuration

## Status

Draft — pending review before application to specs.

## Summary

Remove the `CELERY_RESULT_BACKEND` environment variable and all
references to Celery result storage from Sentinel's specifications.
Add explicit `task_ignore_result = True` as a fixed Celery application
setting. No spec currently describes a consumer of task results; the
authoritative source for task outcomes is the `FetcherRun` PostgreSQL
table.

## Motivation

Sentinel's specifications define three Redis URLs:

| Env Var | DB | Purpose |
|---------|----|---------|
| `REDIS_URL` | `/0` | Application-level (session cache, login lockout, deduplication, distributed locking, heartbeat) |
| `CELERY_BROKER_URL` | `/1` | Celery message broker (task enqueue/dequeue) + celery-redbeat schedule storage (`redbeat:` prefix) |
| `CELERY_RESULT_BACKEND` | `/2` | Celery result backend (task return values) |

**Problem**: no specification describes any workflow that reads task
results from Redis. The result backend is configured but never consumed:

1. No `AsyncResult`, `.ready()`, `.get()`, or task-state polling
   patterns exist anywhere in `docs/features/**/*.md` or cross-cutting
   documents.
2. Every `apply_async()` call is fire-and-forget. The trigger endpoint
   (`fetcher-operations.md`) returns a `FetcherRun` UUID (`run_id`),
   not a Celery `task_id`.
3. `FetcherRun` (PostgreSQL) is explicitly documented as the
   authoritative source for task execution state (status, item counts,
   error message, timing).
4. `celery-redbeat` stores its dynamic schedule under `broker_url`
   with the `redbeat:` key prefix — it has zero dependency on the
   result backend (verified against official redbeat documentation).
5. No monitoring tool (Flower, etc.) is part of the deployment stack.

Keeping the setting creates:
- A false premise that task results are consumed somewhere, potentially
  leading implementers to build AsyncResult-based patterns.
- An extra env var to provision/manage in every environment for zero
  functional benefit.
- Unnecessary memory usage in Redis db2 if `task_ignore_result` is not
  set (Celery writes results with a default 24h TTL).

## Decision

**Remove** `CELERY_RESULT_BACKEND` and **add** `task_ignore_result = True`
as a fixed Celery app setting.

### Rationale for `task_ignore_result = True`

With no backend configured, Celery uses `DisabledBackend` whose
`store_result()` is a silent no-op — the flag is not strictly required
to prevent writes. However, setting it explicitly:

1. **Documents intent**: makes "we deliberately don't use results"
   explicit in the app configuration.
2. **Future-proofs**: if a backend is ever reintroduced (local dev
   override, a future chord requirement), tasks still won't silently
   write results to Redis.
3. **Minor optimization**: skips the `store_result` call path in
   Celery's task trace, avoiding the no-op overhead entirely.

## Reversibility

Re-introducing a result backend later is a **configuration-only change**:
add the env var and set `task_ignore_result = False` (or per-task
`ignore_result = False`). No schema migration, no code refactoring.

## Future Scenarios Considered

| Scenario | Probability | Impact of removal |
|----------|-------------|-------------------|
| Task chaining (task B reads task A's return) | Low — current pattern passes IDs via kwargs and re-reads from DB | None — return values are `None` |
| Canvas (chord/group with callback) | Low — no spec uses barriers; catch-up dispatches per-ticket individually | Re-add backend if ever needed (config-only) |
| Flower / Celery monitoring | Low — not in deployment stack; fetcher dashboard covers monitoring needs | Flower reads broker events regardless |
| Development debugging of results | Negligible — FetcherRun carries full outcome; all tasks return `None` | Dev can set env var locally |

## Risks

### Of removing

- **(Low)** A future chord/canvas requirement forces re-adding the
  backend — mitigated: config-only change, reversibility documented.
- **(Low)** Loss of "task FAILED in result backend" observability crumb
  in two edge cases — mitigated: worker logs + FetcherRun already cover
  it completely; no consumer of the backend state exists.

### Of keeping (status quo risks — for comparison)

- **(Medium)** Implementers may assume task results are consumed and
  build AsyncResult-based patterns.
- **(Medium)** Without `task_ignore_result`, every task writes an
  unused result to Redis db2 (unbounded key growth with 24h TTL).
- **(Low)** Extra env var to provision in every environment for no
  functional benefit.

## Impact on `/ready` Endpoint

The readiness endpoint (`health-endpoints.md`) discovers Redis instances
by extracting `host:port` from all configured Redis URLs. After this
change:

- Discovery source: 3 URLs → 2 URLs (`REDIS_URL`, `CELERY_BROKER_URL`).
- **Standard single-instance deployment** (all URLs point to same host):
  zero functional change — deduplication still produces a single PING.
- **Split deployment**: the (unused) db2 instance is no longer probed —
  correct behavior, since the API server has no dependency on it.
- Response schema: unchanged.

---

## Action Plan

### Prerequisites

None. The project is in specification phase — no code implementation,
no database migrations, no running services to update.

### Step 1 — Update `docs/features/platform/fetcher-infrastructure.md`

This is the **authoritative** location for Celery application
configuration (already owns the "Timezone enforcement" paragraph).

**1a.** After the "Timezone enforcement" paragraph (after current line
1351), insert a new paragraph:

> **Result handling**: the Celery application is configured with
> `task_ignore_result = True` and **no result backend**. Task return
> values are never stored or read — all fetcher tasks return `None`,
> and execution state (status, item counts, error message, timing) is
> persisted in the `FetcherRun` table, the authoritative source for
> task outcomes. `celery-redbeat` stores its dynamic schedule under the
> broker URL (`redbeat:` key prefix) and has no dependency on a result
> backend.

**1b.** Line 158 — replace:

```
and the Celery result backend (task marked as FAILED). Recovery happens at
```

with:

```
and Celery worker logs (task failure traceback). Recovery happens at
```

**1c.** Lines 175-176 — replace:

```
In both cases, visibility is provided by: application logs and the Celery
result backend (task marked as FAILED). No explicit Celery retry is configured.
```

with:

```
In both cases, visibility is provided by: application logs and Celery
worker logs (task failure traceback). No explicit Celery retry is configured.
```

### Step 2 — Update `docs/architecture.md`

Line 92 — replace:

```
- **Result backend**: Redis
```

with:

```
- **Result backend**: disabled (`task_ignore_result = True`) — task
  outcomes are tracked in PostgreSQL (`FetcherRun`), not in Redis. See
  `docs/features/platform/fetcher-infrastructure.md` for rationale.
```

### Step 3 — Update `docs/configuration.md`

**3a.** Delete the table row at line 28:

```
| `CELERY_RESULT_BACKEND` | string | `redis://localhost:6379/2` | Celery result backend URL | `docs/architecture.md` |
```

**3b.** Replace the prose block (lines 30-36):

```
All application-level Redis operations (session caching, login lockout,
deduplication, distributed locking) use `REDIS_URL`. Celery broker and
result backend are configured separately and managed by the Celery
framework — application code never accesses these databases directly.
Different database numbers (`/0`, `/1`, `/2`) ensure namespace isolation
within a single Redis instance; in production, these URLs may point to
separate instances without code changes.
```

with:

```
All application-level Redis operations (session caching, login lockout,
deduplication, distributed locking) use `REDIS_URL`. The Celery broker
is configured separately and managed by the Celery framework —
application code never accesses this database directly. Sentinel does
not configure a Celery result backend (see Celery Worker Configuration
below). Different database numbers (`/0`, `/1`) ensure namespace
isolation within a single Redis instance; in production, these URLs may
point to separate instances without code changes.
```

**3c.** After line 52 (end of "Startup validation" paragraph), insert:

```
Additionally, `task_ignore_result = True` is a fixed Celery application
setting — task return values are never stored. Task outcomes are tracked
in PostgreSQL (`FetcherRun`). See
`docs/features/platform/fetcher-infrastructure.md` (Result handling).
```

### Step 4 — Update `docs/features/platform/health-endpoints.md`

**4a.** Lines 59-66 — replace:

```
**Redis instance discovery**: the readiness check extracts `host:port`
from all three Redis configuration URLs (`REDIS_URL`,
`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`), deduplicates by
`host:port`, and PINGs each unique instance in parallel. In the standard
single-instance deployment (all URLs point to the same host), this
results in a single PING. In split deployments (URLs pointing to
different Redis instances), each unique instance is verified
independently.
```

with:

```
**Redis instance discovery**: the readiness check extracts `host:port`
from both Redis configuration URLs (`REDIS_URL`, `CELERY_BROKER_URL`),
deduplicates by `host:port`, and PINGs each unique instance in parallel.
In the standard single-instance deployment (both URLs point to the same
host), this results in a single PING. In split deployments (URLs
pointing to different Redis instances), each unique instance is verified
independently.
```

**4b.** Lines 141-144 — replace:

```
  the orchestrator should be aware of. The check discovers Redis instances
  dynamically from the configured URLs (`REDIS_URL`, `CELERY_BROKER_URL`,
  `CELERY_RESULT_BACKEND`) so that split deployments are automatically
  covered without spec or code changes.
```

with:

```
  the orchestrator should be aware of. The check discovers Redis instances
  dynamically from the configured URLs (`REDIS_URL`, `CELERY_BROKER_URL`)
  so that split deployments are automatically covered without spec or code
  changes.
```

### Step 5 — Update `docs/deployment.md`

Line 123 — delete:

```
CELERY_RESULT_BACKEND=redis://localhost:6379/2
```

### Step 6 — Update `backend/.env.example`

Line 9 — delete:

```
CELERY_RESULT_BACKEND=redis://localhost:6379/2
```

Note: this file is stale scaffolding (diverges from specs on multiple
fields). The deletion maintains consistency with the spec change. Full
regeneration per current specs will occur at implementation time.

### Step 7 — Update `backend/app/config.py`

Line 29 — delete:

```python
    celery_result_backend: str = "redis://localhost:6379/2"
```

Same note as Step 6: stale scaffold, maintained for consistency.

### Step 8 — Run reviewers on affected specs

After applying all changes (Steps 1-7), invoke the following reviewers
to verify correctness and detect introduced problems:

| Reviewer | Target spec | Rationale |
|----------|-------------|-----------|
| `@spec-coherence-reviewer` | `docs/features/platform/fetcher-infrastructure.md` | Primary change location — verify no contradictions with other specs |
| `@spec-coherence-reviewer` | `docs/features/platform/health-endpoints.md` | Behavioral change to Redis discovery — verify consistency |
| `@docs-reviewer` | All modified files | Verify documentation completeness and accuracy after changes |
| `@docs-placement-reviewer` | `docs/features/platform/fetcher-infrastructure.md` | New "Result handling" paragraph — verify correct placement |

If any reviewer identifies issues rated "Needs revision", resolve them
before considering this RFC applied.

### Step 9 — Delete this draft

Once all changes are applied, verified by reviewers, and any reviewer
findings resolved:

Delete `docs/drafts/remove-celery-result-backend.md`.

---

## Files Modified (Summary)

| # | File | Nature of change |
|---|------|------------------|
| 1 | `docs/features/platform/fetcher-infrastructure.md` | Add "Result handling" paragraph; remove 2x result backend references |
| 2 | `docs/architecture.md` | Update "Result backend" line |
| 3 | `docs/configuration.md` | Remove env var row; rewrite Redis prose; add `task_ignore_result` note |
| 4 | `docs/features/platform/health-endpoints.md` | 3 URLs → 2 URLs (2 locations) |
| 5 | `docs/deployment.md` | Remove env var from .env example |
| 6 | `backend/.env.example` | Remove env var line |
| 7 | `backend/app/config.py` | Remove setting field |

## Cross-references

- `docs/features/platform/fetcher-infrastructure.md` — Timezone
  enforcement, FetcherRun creation failure
- `docs/features/platform/health-endpoints.md` — Redis instance
  discovery, Design Decisions
- `docs/features/platform/fetcher-operations.md` — Trigger endpoint
  (fire-and-forget pattern; unmodified by this RFC)
- `docs/architecture.md` — Task Queue section
- `docs/configuration.md` — Required Connection Settings
- celery-redbeat documentation (external) — Configuration section
  confirming no result backend dependency
