# Open Points

Architectural decisions pending resolution before implementation begins.

---

## 1. Enum Storage Strategy: PostgreSQL ENUM vs VARCHAR + Python Enum

**Origin**: while fixing finding UMGT-API-02 (missing validation for
invalid role values in `POST /api/v1/admin/users/{user}/roles`), we
identified that the choice of enum storage strategy has broad
implications across the project.

**Context**: the project currently specifies (in `docs/data-model.md`,
line 868) that all ENUM types are PostgreSQL enums. However, 4 of the
12 enums in the system are "evolving" — their value sets grow as new
features are added:

| Enum | Current values | Growth driver |
|------|---------------|---------------|
| Role | 2 | New roles as platform matures |
| TicketAuditEventType | 24 | Every new ticket mutation type |
| CVESourceType | 2 | New data source integrations |
| FetcherAuditEventType | 4 | New admin operations on fetchers |

With PostgreSQL ENUM, adding a value requires an Alembic migration
(`ALTER TYPE ... ADD VALUE`) that must run before the new application
code is deployed. Removing a value is even more complex (requires
recreating the type). This creates deployment coupling and operational
risk for values that are expected to change.

**Proposed alternative**: a hybrid approach where stable enums (8) keep
PostgreSQL ENUM for database-level integrity, while evolving enums (4)
use VARCHAR columns with validation enforced exclusively through a
Python Enum (single source of truth in `app/core/enums.py`). Adding a
value to an evolving enum would require only a code change — no
migration.

**Why it matters before implementation**: the storage type chosen for
these columns affects model definitions, Alembic migrations, deployment
procedures, and the testing strategy. Changing this after models are
implemented would require a non-trivial migration to convert existing
PostgreSQL ENUM columns to VARCHAR.

**Partial resolution**: the CVE ingestion architecture draft
(`docs/drafts/cve-ingestion-architecture.md`, OP-7 resolution) confirms
`CVESourceType` as an evolving enum that will use VARCHAR + Python Enum.
The growth trajectory is clear: current values are NVD and MITRE, but
kernel, Red Hat, EPSS, KEV, GHSA, and OSV sources will be added as the
ingestion pipeline expands. This provides a concrete data point for the
broader decision.

**Decision still needed**: hybrid (stable=PG ENUM, evolving=VARCHAR) vs
full VARCHAR for uniformity. See conversation for detailed tradeoff
analysis.

---

## 2. Rate Limiting via Dedicated Reverse Proxy

**Origin**: SSO-SEC-02 finding during sso-authentication spec review.

**Context**: the SSO endpoints (`POST /api/v1/auth/sso/callback` and
`GET /api/v1/auth/sso/authorize`) are public and perform
cryptographic operations (HMAC verification) and outbound HTTP requests
(token exchange with IdP) on every call. Without rate limiting, an
attacker could flood these endpoints for DoS against Sentinel or to
trigger rate limiting at the IdP, blocking legitimate logins.

More broadly, rate limiting is a cross-cutting concern that applies to
multiple public endpoints (login, SSO, password reset, etc.), not just
SSO.

**Proposed approach**: deploy a dedicated reverse proxy (nginx, Traefik,
or Kubernetes ingress controller) in front of Sentinel with rate
limiting rules per endpoint. This is preferable to application-level
rate limiting because:

- Centralized configuration — applies consistently across all endpoints
- More efficient — requests are rejected before reaching the application
- Avoids per-request Redis dependency for rate limit state
- Aligns with Sentinel's architecture (nginx already planned for
  frontend/API routing)

**Recommended limits** (starting point):

| Endpoint | Limit | Window |
|----------|-------|--------|
| `GET /api/v1/auth/sso/authorize` | 20 requests per IP | 1 minute |
| `POST /api/v1/auth/sso/callback` | 10 requests per IP | 1 minute |
| `POST /api/v1/auth/login` | 10 requests per IP | 1 minute |

**When to implement**: before staging/production deployment. Not needed
for local development.

**Decision needed**: which proxy to use (nginx is already in the stack
for frontend serving — may be sufficient), and whether to add
application-level rate limiting as defense-in-depth or rely solely on
the proxy.

---

## 3. Orphan CVE Re-Ticketing Mechanism — RESOLVED

**Resolution**: resolved by the `cve_service` architecture
(`docs/features/tickets/cve-service.md`). All CVE data now flows
through `cve_service.upsert_cve()`, which checks for ticket existence
on every call. Orphaned CVEs (those without a ticket due to data
inconsistencies) are automatically re-ticketed the next time any fetcher
processes them (~6 hours). This implements option (A) naturally without a
dedicated orphan scanner — the check is inherent in the `upsert_cve()`
contract and serves as a data integrity safeguard.

---

## 4. Anomaly Observer Replacing Static Anomaly Matrix

**Origin**: dimension decoupling analysis (C9 — Anomaly matrix,
Affectedness x Delivery, observational coupling classified as KEEP).

**Context**: the anomaly matrix in `package-model.md:523-558` defines 5
anomalous combinations of affectedness and delivery as a static table.
With eligibility decoupled from affectedness, the matrix could be
extended to include eligibility-related
anomalies (7+ combinations). The spec notes these are "destined to be
integrated into the future Review Queue" but no implementation design
exists.

**Proposed approach**: replace the static matrix with an independent
Anomaly Observer service — a pure function that reads the current values
of all three dimensions (affectedness, eligibility, delivery) and
produces anomaly tags. The observer would be called as a post-mutation
hook (similar to `reconcile_ticket_status()`) and write results to a
separate table consumed by the Review Queue UI. It would NEVER modify
any dimension's state.

**Infrastructure prerequisite**: if the observer needs to detect fixes
present in codestreams where the track is in a final affectedness status
(`NOT_AFFECTED`, `WONT_FIX`, or already `FIXED`), the IBS consumer
(`IBSEventConsumer`) and the periodic fetcher
(`check_ibs_track_releases`) would need to be extended to also scan
final-status tracks. Currently, both filter their scope to tracks with
`status in (ANALYSIS, AFFECTED)` because the release detector only
transitions non-final tracks. The anomaly observer would need the raw
detection signal without the transition, requiring a broader scan scope.

**Use case — rejected automatic transitions**: when `set_track_status()`
rejects an automatic transition on a final-status track (e.g., IBS
release detection finds a fix for a track marked `NOT_AFFECTED`), this
is currently logged as a warning (see `package-service.md`,
`set_track_status()` step 5). A future Anomaly Observer could consume
these signals to surface them in the Review Queue as actionable
anomalies (e.g., "IBS detected a fix in codestream X for track Y, but
the VA marked it `NOT_AFFECTED` — review recommended"). This would
replace the passive warning log with an active notification to the VA.

**Decision needed**: (a) timing — implement alongside the Review Queue
feature or earlier as infrastructure, (b) storage — separate
`TicketAnomaly` table vs. flags on existing records, (c) whether the
observer should also detect intra-dimensional anomalies (e.g., a track
in `FIXED` status whose parent ticket has no CVE), (d) whether to
extend the IBS consumer and fetcher scan scope immediately (as part of
the decoupling work) or defer until the observer is implemented.

---

## 5. Response Header for Silently Ignored Parameters

**Origin**: design review of `docs/drafts/capability-scope-rbac.md` —
the "conditional capability checks" convention where Public/Authenticated
endpoints silently ignore privileged parameters when the caller lacks
the required capability.

**Context**: when an API endpoint silently ignores a parameter (e.g.,
`include_excluded` on `GET /api/v1/tickets/{ticket_id}/packages` when
the caller lacks `manage_packages`), there is no feedback to the API
consumer that the parameter was not applied. A misconfigured bot or
integration could operate on incomplete data without any indication. The
only diagnostic is a server-side DEBUG log, which the API consumer does
not have access to.

**Proposed approach**: introduce a response header
`X-Sentinel-Ignored-Params` that lists the names of parameters that were
present in the request but silently ignored due to insufficient
capability. Example:

```
X-Sentinel-Ignored-Params: include_excluded
```

This would apply broadly — not just to capability-gated parameters, but
to any scenario where a request parameter is accepted syntactically but
not applied (e.g., unknown filter values, unsupported sort fields). The
header preserves the "no 403 on Public endpoints" convention while
making the behavior observable to API consumers.

**Scope**: this is a cross-cutting API convention, not specific to RBAC.
It should be specified in `docs/api-spec.md` and applied uniformly
across all endpoints.

**Considerations**:
- The header MUST NOT include the parameter value (only the name) to
  avoid information leakage or log injection
- Multiple ignored parameters are comma-separated
- The header is omitted entirely when no parameters were ignored
- Clients that do not inspect headers see no change in behavior

**Decision needed**: whether to adopt this as a standard API convention,
and the exact header name (`X-Sentinel-Ignored-Params` vs. a more
generic alternative).

---

## 6. Periodic Ticket Status Reconciliation as Drift Detection

**Origin**: analysis of TKM-DES-07 (race window between
`deactivate_user` and concurrent ticket mutations) and broader
consideration of status drift scenarios.

**Context**: `reconcile_ticket_status` is called inline after every
mutation, making the system event-driven correct. However, if a bug
causes a mutation path to skip reconciliation — or if a race condition
leaves a ticket in a transiently inconsistent state that never receives
a subsequent mutation — the ticket remains silently drifted with no
mechanism to detect or correct it. Today, no one would notice.

**Proposed approach**: create a daily scheduled task (BaseFetcher
subclass) that iterates over all tickets in non-final status and calls
`reconcile_ticket_status` on each. For every ticket where
reconciliation actually changes the status (i.e., a drift was found and
corrected), the task MUST emit a prominent log entry (WARNING level)
containing:

- Ticket ID and CVE
- Previous status → new status after reconciliation
- Timestamp of last mutation on the ticket (to help identify when the
  drift was introduced)

This allows administrators to discover that a drift occurred, identify
the affected ticket(s), and investigate the root cause — potentially
uncovering a bug in a mutation path that failed to call reconciliation.

**Why logging matters**: the primary value is not the self-healing (which
is a nice side effect) but the **observability**. A silent self-heal
hides bugs. A logged self-heal surfaces them. The task effectively acts
as a continuous integration test for the event-driven reconciliation
architecture.

**Expected behavior under normal operation**: the task completes with
zero corrections on every run, confirming that all mutation paths are
correctly calling `reconcile_ticket_status`. Any non-zero correction
count is an indicator of a defect somewhere in the system.

**Decision needed**: (a) whether this should be a standalone fetcher or
integrated into an existing periodic task, (b) log level and alerting
strategy (WARNING + optional webhook notification to ops channel), (c)
whether to also emit a `TicketAuditEvent` for drift corrections (to
distinguish admin-triggered reconciliation from bug-induced drift in
the audit trail).

---

## 7. Platform Status Monitoring — System Info Endpoint and Admin Page

**Origin**: CPE-to-Package mapping v2 draft
(`docs/drafts/cpe-package-mapping-v2.md`) — the static mapping file is
loaded once at application startup with no runtime observability. An
administrator has no way to verify the mapping is correctly loaded or
how many entries it contains.

**Context**: Sentinel currently has no system info or diagnostics
surface beyond the minimal `/health` liveness endpoint. The fetcher
dashboard monitors `BaseFetcher` subclasses with execution lifecycle
(runs, metrics, schedules), but infrastructure components that are not
fetchers — such as in-memory static data, connection pools, or loaded
configuration — have no monitoring surface.

The CPE mapping dict (~2,450 entries, ~2,800 package mappings) is the
first concrete case, but the need is broader: any static data loaded at
startup, future sync diagnostics (referenced in
`ibs-product-release-detection.md`), and general platform health
indicators would benefit from a dedicated monitoring area.

**Proposed approach**: two complementary mechanisms (not mutually
exclusive):

**(A) Lightweight public endpoint** — `GET /api/v1/system/info`

A public read-only endpoint that reports the status of loaded
infrastructure components. Initial payload:

```json
{
  "data": {
    "cpe_mapping": {
      "loaded": true,
      "entry_count": 2453,
      "package_count": 2810
    }
  }
}
```

Follows the IBS consumer status pattern (`GET
/api/v1/ibs-consumer/status`) — a non-fetcher infrastructure
component reporting its state via a dedicated endpoint. Useful for
API consumers, health checks, and automated monitoring.

**(C) Dedicated System Info admin page**

A new page in the admin area (sidebar item under Admin Settings) backed
by `GET /api/v1/admin/system-info`. Shows platform status information
with admin-only details (file paths, version info, loaded module
diagnostics). Room for growth: future sync diagnostics, data freshness
indicators, dependency health.

Options A and C can coexist: A provides a public API surface for
monitoring tools and scripts, C provides a richer admin UI with
additional detail. The public endpoint exposes a subset of the
information available on the admin page.

**Decision needed**: (a) whether to implement A alone first (minimal
scope) or A+C together, (b) which additional platform components
beyond CPE mapping should be included in the initial version, (c)
capability requirement for the admin page (reuse `manage_settings`
or a new `view_system_info` capability).

---

## 8. Simplify Duplicate Handling — Eliminate Chains

**Origin**: architectural simplification review (pre-implementation
phase).

**Context**: the current duplicate handling design in `tickets.md`
supports chains of duplicates (A→B→C) with:

- `resolve_canonical_target()` — a chain resolver with 50-hop limit and
  cycle detection (`DuplicateChainDepthError`,
  `TICKET_DUPLICATE_CYCLE_DETECTED`)
- `execute_duplicate_cascade` — best-effort flattening that re-points
  intermediate tickets when a target is itself marked as duplicate.
  Requires independent transactions per ticket (separate
  `db_session_factory`), making it the **only exception** to the "module
  does not commit" invariant in `ticket-service.md`
- `duplicate_target_changed` audit event type — created during cascade
  flattening
- Cycle detection and resolution procedures (manual admin intervention)
- API response serialization that resolves the chain before returning
  `duplicate_of_id`

The specification itself acknowledges: "chains longer than two tickets
are almost nonexistent."

**Proposed simplification**: require the duplicate target to be a
non-Duplicated ticket at the time of the operation. If B is already in
Duplicated status, `mark_as_duplicate(A, B)` is rejected with a
validation error instructing the caller to use B's target instead.

Under this model:

- `mark_as_duplicate(ticket, target)` — validates `target.status !=
  Duplicated`, sets `duplicate_of_id = target.id`, transitions to
  Duplicated status
- `revert_duplicate(ticket)` — clears `duplicate_of_id`, re-enters gate
  zone
- No chain resolution needed — `duplicate_of_id` always points directly
  to a non-Duplicated ticket
- No cascade needed — if B is later marked as duplicate of C, tickets
  pointing to B now have a stale reference. Two options:
  - **(a) Reject**: block `mark_as_duplicate(B, C)` if any ticket's
    `duplicate_of_id` points to B. The VA must revert those tickets
    first. Simplest, most explicit.
  - **(b) Inline cascade**: re-point the (typically 0-1) tickets
    pointing to B within the same transaction. No separate session
    factory needed since the set is trivially small (single UPDATE).
    Less restrictive than (a) but still eliminates chains.

**What gets removed**:

- `resolve_canonical_target()` function (50-hop resolver, cycle
  detection)
- `execute_duplicate_cascade` and its special transaction handling
- `DuplicateChainDepthError` and `TICKET_DUPLICATE_CYCLE_DETECTED`
  error codes
- `duplicate_target_changed` audit event type
- Cycle resolution documentation
- API-level chain resolution in response serialization

**What stays**:

- `mark_as_duplicate` operation (with the simpler validation)
- `revert_duplicate` operation (unchanged)
- `duplicate_set` audit event type (records the raw operation)

**Decision needed**: (a) whether to adopt option (a) (reject if
dependents exist) or option (b) (inline re-point in same transaction),
and (b) whether the edge case of "target becomes Duplicated after the
operation" needs any handling beyond the stale-reference approach (API
could detect and show a warning in the UI without breaking correctness).

---

## 9. Remove FetcherRunWeeklyAggregate Table

**Origin**: architectural simplification review (pre-implementation
phase).

**Context**: the fetcher infrastructure spec
(`docs/features/platform/fetcher-infrastructure.md`) defines a
`FetcherRunWeeklyAggregate` table that stores pre-computed weekly
rollups of fetcher run metrics (total runs, successes, failures, average
duration, total records created/updated/failed). A scheduled task
aggregates individual `FetcherRun` records older than 90 days into
weekly buckets, then deletes the source rows. The aggregates are
consumed by the fetcher operations dashboard for historical chart
rendering beyond the 90-day individual-run retention window.

This introduces:

- 1 additional database table (`FetcherRunWeeklyAggregate`)
- A periodic aggregation task (weekly, processes expired runs)
- Dual-source query logic in the dashboard API (individual runs for
  recent data, aggregates for historical data, merged into a single
  timeseries response)
- Data retention lifecycle management (90-day threshold, aggregation
  window alignment, idempotency guards)

**Proposed simplification**: remove `FetcherRunWeeklyAggregate` entirely
and use a simple 90-day hard-delete retention policy on `FetcherRun`.
Dashboard charts display only the last 90 days of individual run data.

Rationale:

- The system has not been deployed yet — there is no historical data to
  preserve
- 90 days of individual runs (~15 fetchers x 1-4 runs/day x 90 days =
  ~5,400 rows maximum) is trivially small for PostgreSQL. No performance
  optimization is needed at this scale
- If historical trends beyond 90 days become valuable in the future, the
  aggregation table can be introduced then, with full knowledge of actual
  usage patterns and chart requirements
- Eliminating the dual-source query logic simplifies the dashboard API
  and removes an entire class of edge cases (partial weeks, timezone
  alignment, fetcher renames)

**What gets removed**:

- `FetcherRunWeeklyAggregate` table definition (from `data-model.md` and
  `fetcher-infrastructure.md`)
- Weekly aggregation task specification
- Dual-source chart query logic in `fetcher-operations.md`
- Data retention lifecycle (replaced by simple `DELETE WHERE created_at <
  now() - 90 days`)

**What stays**:

- `FetcherRun` table with all current columns
- 90-day retention policy (simple periodic DELETE)
- Dashboard charts using individual run data (last 90 days)

**Decision needed**: whether 90 days of granular history is sufficient
for operational needs, or whether a longer retention window (e.g., 180
days) without aggregation would be preferable.
