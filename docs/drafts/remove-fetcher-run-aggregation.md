# Remove FetcherRunWeeklyAggregate — Pure Removal

**Status**: Draft — pending approval before execution.

**Origin**: architectural simplification review. Resolves open-points.md
point 9 and mitre-fetcher-spec-gaps.md OP-11.

**Scope**: the project is in specification phase. No implementation code
or database exists. All changes described in this document are
modifications to specification files only.

---

## Summary

Remove the `FetcherRunWeeklyAggregate` table, the
`aggregate_fetcher_runs` periodic task, and the dual-source query logic
from the timeline endpoint. `FetcherRun` records persist indefinitely
with no retention policy and no cleanup task. The existing Stale Run
Detection mechanism already handles orphaned runs — no additional
recovery mechanism is needed.

---

## Motivation

### The aggregation infrastructure introduces unnecessary complexity

The current design adds:

1. **1 additional table** (`FetcherRunWeeklyAggregate`) with its own
   schema, constraints, and migration
2. **1 additional BaseFetcher** (`AggregateFetcherRuns`) with config,
   audit events, and dashboard presence
3. **Dual-source query logic** in the timeline API endpoint: individual
   runs for recent data, aggregates for older data, merged into a single
   timeseries response with transition-week deduplication
4. **Edge cases**: partial weeks at boundaries, timezone alignment for
   ISO week calculation, idempotency guards for re-runs, handling of
   deregistered fetchers' aggregates

### The scale does not justify any retention mechanism

With ~15 fetchers running 1–4 times/day:

| Time horizon | Estimated rows | PostgreSQL impact |
|--------------|---------------|-------------------|
| 1 year       | ~22,000       | Negligible        |
| 5 years      | ~110,000      | Negligible        |
| 10 years     | ~220,000      | Negligible        |

Even at 10 years of full-granularity retention, the table is trivially
small. No cleanup, aggregation, or purging is needed. A simple
`(fetcher_name, started_at)` index handles all query patterns
efficiently at this scale.

### Information loss is undesirable

The aggregation design permanently destroys:

- Error messages, error details, and tracebacks (critical for
  diagnosing recurring failures)
- Exact per-run timestamps (useful for correlating with external events)
- Per-run item counts (only totals and averages survive)
- Who triggered each run (`triggered_by`, `triggered_by_user_id`)
- Individual run durations (only min/avg/max survive)

With indefinite retention, all diagnostic data remains available
permanently.

### Orphan runs are already handled by Stale Run Detection

The aggregation task included orphan resolution as a side effect
(force-resolving runs stuck in `running` status). This is redundant
because the existing Concurrency Control and Stale Run Detection
mechanisms already cover this:

1. **Concurrency Control** (fetcher-infrastructure.md, "Concurrency
   Control"): only one instance of a given fetcher can run at a time.
   Before executing, the task checks for existing `FetcherRun` records
   with `status = running`.
2. **Stale Run Detection** (fetcher-infrastructure.md, "Stale Run
   Detection"): a run in `running` status for longer than
   `timeout_seconds` (default: 3600) is automatically marked as
   `failure` by the next trigger attempt (scheduled or manual).

This means orphaned runs are resolved within at most one schedule
interval + timeout (e.g., 24h + 1h = ~25h for daily fetchers). The
aggregation task's orphan resolution was a redundant safety net.

The only scenario where a run stays in `running` forever is when
`timeout_seconds = 0` (stale detection explicitly disabled) AND the
fetcher is never triggered again. This is documented as an
operator-accepted risk in the Stale Run Detection specification.

---

## Design Decision

| Aspect | Current (with aggregation) | After (pure removal) |
|--------|---------------------------|---------------------|
| Retention | 90 days individual + indefinite aggregates | Indefinite (no deletion, no aggregation) |
| Cleanup mechanism | `AggregateFetcherRuns` BaseFetcher (daily 03:00 UTC) | None |
| Dashboard charts | Dual-source (runs + aggregates, transition-week dedup) | Single-source (`FetcherRun` only) |
| Timeline endpoint | `from_date`/`to_date` with 365-day max, `type` field | `from_date`/`to_date` with 1825-day (5-year) max, uniform format |
| Orphan resolution | Inside aggregation task | Stale Run Detection (existing, no change) |
| Diagnostic data | Lost after 90 days | Available permanently |
| Tables | `FetcherRun` + `FetcherRunWeeklyAggregate` | `FetcherRun` only |
| Fetcher count | N fetchers + 1 infrastructure fetcher | N fetchers (no overhead) |

---

## Execution Plan

### Phase 1: Specification updates

#### Step 1.1 — Update `docs/features/platform/fetcher-infrastructure.md`

**Data Retention section** (lines ~1497–1546):

- **Remove** the entire "Data Retention" section content (aggregation
  algorithm, `FetcherRunWeeklyAggregate` model definition, transactional
  semantics, orphan resolution within aggregation)
- **Replace** with a minimal note:

  > `FetcherRun` records are retained indefinitely. At ~15 fetchers
  > with 1–4 executions per day, the table grows by approximately
  > 20,000 rows per year — negligible for PostgreSQL. No cleanup task
  > or retention policy is necessary. Orphaned runs (stuck in `running`
  > status due to unclean process termination) are resolved
  > automatically by the existing Stale Run Detection mechanism at the
  > next trigger attempt.
  >
  > **Manual purge**: if an operator needs to reduce table size for
  > operational reasons (disaster recovery, database refresh), a simple
  > time-based DELETE is sufficient:
  > `DELETE FROM fetcher_run WHERE started_at < now() - interval 'N days'`.
  > No application-level coordination is required.

**Deregistered Fetcher Lifecycle section** (lines ~1548–1609):

- **Remove** all references to `FetcherRunWeeklyAggregate` in this
  section (lines ~1553, 1574, 1579–1588, 1607)
- **Remove** the entire "Aggregation task behavior" subsection (lines
  ~1577–1591) — no longer applicable
- **Rewrite** to reflect the simplified behavior: when a fetcher is
  deregistered, its `FetcherRun` records remain in the database
  indefinitely. No aggregate preservation logic exists. The
  `FetcherConfig` record retains `registered = false` for historical
  reference. The deregistered fetcher's runs remain queryable in the
  timeline endpoint.
- **Update** the FK constraint count from "three dependent tables" to
  "two" (`FetcherRun` and `FetcherAuditEvent`) at line ~1554
- **Simplify** the manual cleanup procedure (lines ~1605–1608): remove
  `FetcherRunWeeklyAggregate` from the FK ordering. The new sequence
  is: delete `FetcherRun` records, then `FetcherAuditEvent` records,
  then the `FetcherConfig` row.

**Scattered references**:

- **Line ~197** (name length limit): remove the mention of
  `FetcherRunWeeklyAggregate` table column from the name length
  constraint rationale
- **Lines ~223, 226** (naming convention): remove the `aggregate` verb
  category from the naming convention table and the note that local
  fetchers (`evaluate`, `aggregate`) omit the source segment. Update to
  reference only `evaluate` as the local-fetcher example, or remove the
  local-fetcher naming note if `evaluate` alone does not justify it.
- **Line ~769** (Fetchers that do NOT need `catch_up()` table): remove
  the `aggregate_fetcher_runs` row
- **Line ~847** (error message sanitization exemption): remove the
  `aggregate_fetcher_runs` exemption entry

#### Step 1.2 — Update `docs/features/platform/fetcher-operations.md`

**Fetcher specification** (lines ~789–851):

- **Remove** the entire `### Fetcher: aggregate_fetcher_runs` section
  (properties table, algorithm, metrics, custom settings)

**Timeline endpoint** (lines ~345–434):

- **Rewrite** the endpoint description to single-source:
  - Remove "Automatically selects the appropriate data source" phrasing
  - Remove the `type` field from the response schema (`"individual"` /
    `"weekly_aggregate"` distinction no longer exists)
  - Remove aggregate-specific fields from the response: `run_count`,
    `success_count`, `failure_count`, `partial_count`,
    `min_duration_seconds`, `max_duration_seconds`
  - Remove the "Query strategy" paragraph (dual-source split,
    transition-week deduplication)
  - **Replace** the 365-day maximum constraint with a 1825-day (5-year)
    cap. The original motivation ("expensive scans of unbounded
    historical aggregate data") no longer applies at ~1,500 rows/year
    per fetcher, but a generous cap provides defense-in-depth against
    accidentally unbounded responses. Keep the `DATE_RANGE_TOO_WIDE`
    error code with the updated threshold (365 → 1825 days) in the
    "Error responses" table (line ~447)
  - Update the `Fields` documentation to reflect uniform format (every
    point is an individual run)
  - Keep `from_date`/`to_date` as query parameters with sensible
    defaults (e.g., `from_date` = 7 days ago, `to_date` = now)
  - Keep `disabled_periods` in the response (unchanged — derived from
    `FetcherAuditEvent`, not from aggregates)
  - Keep chronological sort order (unchanged)

**Preamble** (line ~14):

- Remove `FetcherRunWeeklyAggregate` from the data model dependencies
  list

**Deregistered fetcher note** (lines ~233–235):

- Remove or rewrite the note about `last_run` becoming `null` "for
  deregistered fetchers whose runs are fully aggregated" — `last_run`
  becomes `null` only when the fetcher is deregistered and has no
  historical runs (edge case), or remains as the timestamp of its final
  run

**CLI examples** (lines ~878, 957–965):

- Remove `aggregate_fetcher_runs` from the `sentinel fetcher list`
  example output
- Remove the `sentinel fetcher config` example for
  `aggregate_fetcher_runs` (which shows `retention_days = 90`)

#### Step 1.3 — Update `docs/data-model.md`

- **Remove** the `FetcherRunWeeklyAggregate` table definition (lines
  ~1404–1427)
- **Remove** the `FetcherRunWeeklyAggregate` entity from the ER diagram
  (lines ~332–336)
- **Remove** the relationship line `FetcherConfig ||--o{
  FetcherRunWeeklyAggregate : "has aggregates"` (line ~364)
- **Remove** the `FetcherRunWeeklyAggregate` entry from the
  `updated_at` convention exceptions list (line ~1501)
- **Add** a note to the `FetcherRun` section: "Records are retained
  indefinitely (no retention policy). Growth rate is approximately
  20,000 rows per year."
- **Add** a composite index `(fetcher_name, started_at)` to the
  `FetcherRun` table definition. This index supports timeline queries
  at any date range efficiently and is the performance basis for
  removing the dual-source aggregation logic.

#### Step 1.4 — Update `docs/data-sources.md`

- **Remove** the `aggregate_fetcher_runs` entry from the Fetcher
  Registry table (line ~937). No replacement entry is added — the
  fetcher simply ceases to exist.

#### Step 1.5 — Update `docs/drafts/open-points.md`

- **Mark point 9 as RESOLVED** with decision: "Accepted. Removed
  `FetcherRunWeeklyAggregate` and `aggregate_fetcher_runs` entirely.
  `FetcherRun` records are retained indefinitely (~20k rows/year,
  negligible for PostgreSQL). No cleanup task or retention policy is
  needed. See `docs/drafts/remove-fetcher-run-aggregation.md` for
  rationale."

#### Step 1.6 — Update `docs/drafts/mitre-fetcher-spec-gaps.md`

- **Mark OP-11 as RESOLVED** with decision: "The aggregation task no
  longer exists. `FetcherRun` records are retained indefinitely,
  eliminating the cursor-loss risk entirely. For fetchers disabled for
  extended periods, the cursor is preserved in their most recent
  `FetcherRun` record for as long as the record exists (forever). No
  additional mechanism is needed."
- **Update** the "Planned Changes" section and dependency table (lines
  ~1298–1307) to remove the OP-11 dependency on the aggregation spec

#### Step 1.7 — Update `docs/system-map.md`

- **Remove** the `FetcherRunWeeklyAggregate` entity from the ER diagram
  (lines ~350–354)
- **Remove** `FetcherRunWeeklyAggregate` from the Platform row of the
  Entity Groups table (line ~413)

### Phase 2: Review

#### Step 2.1 — Run spec coherence review

Invoke `@spec-coherence-reviewer` on:
- `docs/features/platform/fetcher-infrastructure.md`
- `docs/features/platform/fetcher-operations.md`
- `docs/system-map.md`

Verify no dangling references to the removed table, task, or
dual-source logic remain.

#### Step 2.2 — Run docs review

Invoke `@docs-reviewer` to verify cross-document consistency after the
updates.

#### Step 2.3 — Run data model review

Invoke `@data-model-reviewer` on `docs/data-model.md` to verify that
the table removal is clean and the composite index addition is
correctly specified.

### Phase 3: Cleanup

#### Step 3.1 — Delete this draft

Once all specification updates are applied and reviews pass, delete
`docs/drafts/remove-fetcher-run-aggregation.md`.

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Unbounded table growth degrades query performance over time | Negligible | ~15 fetchers at 1–4 runs/day produces ~22k rows/year. At 10 years (~220k rows), this remains trivial for PostgreSQL with proper indexing. If performance degrades in practice (unlikely), a simple time-based purge can be introduced at that point without data model changes. |
| Loss of pre-computed historical views for dashboard performance | None | The timeline endpoint queries `FetcherRun` directly via the `(fetcher_name, started_at)` index. At the expected volumes, this is faster than the dual-source merge logic it replaces. |

---

## Out of Scope

- **Pre-computed aggregate views for analytics**: if long-term trend
  dashboards (multi-year, cross-fetcher) are needed in the future, they
  can be introduced as a materialized view or reporting layer without
  affecting the operational `FetcherRun` table. This is speculative and
  not justified by current requirements.
- **Per-fetcher configuration of any kind related to retention**: all
  fetchers share the same indefinite-retention behavior. No per-fetcher
  knobs are needed.
