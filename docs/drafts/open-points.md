# Open Points

Architectural decisions pending resolution before implementation begins.

## Summary

| ID | Title | Domain | Status |
|----|-------|--------|--------|
| OP-1 | Enum Storage Strategy | Data Model | Open |
| OP-2 | Rate Limiting via Reverse Proxy | Deployment | Open |
| OP-4 | Anomaly Observer | Packages | Open |
| OP-6 | Periodic Ticket Status Reconciliation | Tickets | Open |
| OP-7 | Platform Status Monitoring | Platform | Open |
| OP-8 | Simplify Duplicate Handling | Tickets | Open |
| OP-11 | Ecosystem Prefix Mapping | Packages | Open |
| OP-12 | Fetcher Metrics Granularity | Fetcher Infrastructure | Open |
| OP-13 | CWE Accumulation | Fetcher Infrastructure | Open |
| OP-3 | Orphan CVE Re-Ticketing | — | Resolved |
| OP-5 | Response Header for Silently Ignored Parameters | — | Closed |
| OP-9 | Remove FetcherRunWeeklyAggregate | — | Resolved |
| OP-10 | Ecosystem Column on CVEAffectedVersion | — | Resolved |
| OP-14 | BaseFetcher All-Items-Failed Safety Check | — | Resolved |

---

## Open — Data Model

### OP-1. Enum Storage Strategy: PostgreSQL ENUM vs VARCHAR + Python Enum

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

## Open — Deployment

### OP-2. Rate Limiting via Dedicated Reverse Proxy

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

## Open — Tickets

### OP-6. Periodic Ticket Status Reconciliation as Drift Detection

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
subclass) that iterates over all tickets in active statuses and calls
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

### OP-8. Simplify Duplicate Handling — Eliminate Chains

**Origin**: architectural simplification review (pre-implementation
phase).

**Context**: the current duplicate handling design in `tickets.md`
supports chains of duplicates (A→B→C) with:

- `resolve_canonical_target()` — a chain resolver with 50-hop limit and
  cycle detection (`DuplicateChainDepthError`,
  `TICKET_DUPLICATE_CYCLE_DETECTED`)
- `execute_duplicate_flattening` — best-effort flattening that re-points
  intermediate tickets when a target is itself marked as duplicate.
  Requires independent transactions per ticket (separate
  `db_session_factory`), making it the **only exception** to the "module
  does not commit" invariant in `ticket-service.md`
- `duplicate_target_changed` audit event type — created during
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
- No flattening needed — if B is later marked as duplicate of C, tickets
  pointing to B now have a stale reference. Two options:
  - **(a) Reject**: block `mark_as_duplicate(B, C)` if any ticket's
    `duplicate_of_id` points to B. The VA must revert those tickets
    first. Simplest, most explicit.
  - **(b) Inline flattening**: re-point the (typically 0-1) tickets
    pointing to B within the same transaction. No separate session
    factory needed since the set is trivially small (single UPDATE).
    Less restrictive than (a) but still eliminates chains.

**What gets removed**:

- `resolve_canonical_target()` function (50-hop resolver, cycle
  detection)
- `execute_duplicate_flattening` and its special transaction handling
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

## Open — Packages

### OP-4. Anomaly Observer Replacing Static Anomaly Matrix

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
(`detect_ibs_track_releases`) would need to be extended to also scan
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

### OP-11. Ecosystem Prefix Mapping for Package Resolution

**Origin**: GHSA fetcher spec (OP-9 follow-up), Session 2 (2026-06-18)

**Context**: GHSA `resolved_packages` passes ecosystem package names
(e.g., "lodash", "requests", "github.com/openfga/openfga") to Phase 2
for best-effort SMELT resolution. Hit rate is ecosystem-dependent — high
for system-level C libraries that share names with RPM source packages
(e.g., "curl", "openssl", "zlib"), low for language-specific packages
with different naming conventions (e.g., pip `requests` → RPM
`python-requests`, npm `lodash` → no RPM equivalent).

A prefix mapping table could transform package names before SMELT
resolution (e.g., `pip:requests` → `python-requests`, `npm:node-forge` →
`nodejs-node-forge`).

**Proposed approach**: ecosystem-aware prefix/transform rules applied to
`resolved_packages` before passing to `add_package_to_ticket()`. Rules
would be a simple dict in the fetcher or a shared module.

**Prerequisite satisfied**: the `ecosystem` column on
`CVEAffectedVersion` is now in place (see OP-10 resolution in Archive
section). The ecosystem context needed for prefix mapping is available
in the data model.

**Decision needed**: is the additional hit rate worth the mapping
maintenance burden? Revisit when: (a) GHSA and OSV hit rate is measured
post-implementation and found insufficient for system-level packages, or
(b) practical experience shows which transform rules have the highest
ROI.

---

## Open — Fetcher Infrastructure

### OP-12. Fetcher Metrics — Granularity and Semantics

**Origin**: OSV fetcher spec (Session 5, 2026-06-19)

**Context**: `record_updated` is incremented for every CVE where
`upsert_cve()` succeeds, regardless of whether the data actually changed
compared to the previous run. The metric means "processed" not "updated
with new data." All CVE fetchers (NVD, MITRE, Red Hat, GHSA, OSV) use
this same convention. As the system matures and most CVEs are already
enriched, the metric loses diagnostic value (high counts even when
nothing changed).

**Proposed approach**: evaluate the feasibility of:

- `record_updated` → only when written data differs from previous state
  (change-detection pre-write comparison)
- `record_skipped` → CVE processed but no upsert performed (e.g.,
  `CVENotInSource`, completeness guard, no-change detection)
- `record_missed` → CVEs tracked by Sentinel that the fetcher does not
  cover (delta between active tickets and CVEs present in the source)

**Impact**: cross-cutting on `BaseFetcher`/`BaseCVEFetcher` and the
fetcher-operations dashboard. Must be evaluated together with the
dashboard design. Introducing change-detection pre-write would require
comparing the payload against current database state before
delete-and-reinsert — potentially doubling read I/O per CVE.

**Decision needed**: is the diagnostic improvement worth the performance
and complexity cost? Revisit when: (a) the fetcher-operations dashboard
is implemented and operators report metric ambiguity, or (b) database
size makes unnecessary writes a performance concern.

---

### OP-13. CWE Accumulation — Stale Records from Additive-Only Upsert

**Origin**: CISA KEV fetcher spec review (Session 4, 2026-06-20)

**Context**: `upsert_cve()` uses an additive-only pattern for `CVECWE`
records — the upsert key is `(cve_id, cwe_id, source)`, so new CWE
entries are inserted and existing ones are updated, but removed entries
are never deleted. If an external source corrects a CWE classification
(e.g., changes CWE-306 to CWE-79), the old record persists indefinitely
alongside the new one.

This affects any source that can revise CWE data over time: CISA KEV
(`cwes` array), NVD (problemType), MITRE CNA/ADP containers. The issue
is not specific to the KEV fetcher — it is a cross-cutting limitation of
the `upsert_cve()` infrastructure.

**Contrast with `CVEAffectedVersion`**: affected versions use
delete-and-reinsert (clear all records for the source, then reinsert the
current set), which naturally handles removals. `CVECWE` does not use
this pattern — likely because CWE corrections are rare and the data is
supplementary (VAs rely primarily on NVD/MITRE for CWE).

**Proposed approaches**:

- (a) Accept the limitation as-is — CWE data is supplementary,
  corrections are rare, and stale entries have low practical impact
- (b) Switch `CVECWE` to delete-and-reinsert per `(cve_id, source)` on
  each fetch — clean but increases write I/O for every run
- (c) Add a periodic cleanup job that compares current source data
  against stored records — complex, deferred maintenance burden

**Impact**: low for individual VAs (CWE is informational, not used for
gating or automation). Higher if CWE data is ever exposed in the UI as
authoritative or used for automated categorization.

**Decision needed**: is the theoretical data staleness worth addressing
now, or should it be deferred until practical impact is observed? Revisit
when: (a) a VA reports incorrect CWE data on a ticket, or (b) CWE
classifications are used for automated triage/routing.

---

## Open — Platform

### OP-7. Platform Status Monitoring — System Info Endpoint and Admin Page

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

## Archive — Resolved

### OP-3. Orphan CVE Re-Ticketing Mechanism — RESOLVED

**Resolution**: resolved by the `cve_service` architecture
(`docs/features/tickets/cve-service.md`). All CVE data now flows
through `cve_service.upsert_cve()`, which checks for ticket existence
on every call. Orphaned CVEs (those without a ticket due to data
inconsistencies) are automatically re-ticketed the next time any fetcher
processes them (~6 hours). This implements option (A) naturally without a
dedicated orphan scanner — the check is inherent in the `upsert_cve()`
contract and serves as a data integrity safeguard.

---

### OP-5. Response Header for Silently Ignored Parameters — CLOSED (2026-06-10)

**Resolution**: the Soft Conditional Check pattern and the associated
response header proposal have been removed from the specifications. The
only parameter that used the silent-ignore mechanism (`include_deleted`
on ticket/CVE list endpoints) was removed when ticket soft-deletion was
eliminated. With zero consumers of the pattern, the header adds
complexity without value. The entire "Conditional Capability Checks"
section in `rbac.md` and `api-spec.md` has been removed; the only
remaining field-level capability pattern (Hard Conditional / `†`) is
self-explanatory and documented inline in the Endpoint Permission Map
legend. If a similar need arises in the future, the pattern can be
re-introduced with a fresh design.

---

### OP-9. Remove FetcherRunWeeklyAggregate Table — RESOLVED

**Decision**: Accepted. Removed `FetcherRunWeeklyAggregate` and
`aggregate_fetcher_runs` entirely. `FetcherRun` records are retained
indefinitely (~20k rows/year, negligible for PostgreSQL). No cleanup task
or retention policy is needed. See
`docs/drafts/remove-fetcher-run-aggregation.md` for rationale.

---

### OP-10. Ecosystem Column on CVEAffectedVersion — RESOLVED (2026-06-19)

**Resolution**: `ecosystem VARCHAR(50)` nullable column added to
`CVEAffectedVersion` (see `docs/data-model.md`). Populated by
`sync_osv_advisories` (canonical OSV/OSSF values) and
`sync_ghsa_advisories` (normalized from GitHub names via mapping dict).
The column was justified by: (a) both OSV and GHSA fetchers populate it,
(b) it enables ecosystem-aware display and filtering in the UI,
(c) it is a prerequisite for OP-11 (Ecosystem Prefix Mapping).

See:
- `docs/features/tickets/cve-sync-osv.md` (Fetcher: `sync_osv_advisories` — Ecosystem normalization)
- `docs/features/tickets/cve-sync-ghsa.md` (Fetcher: `sync_ghsa_advisories` — Ecosystem normalization)
- `docs/data-model.md` (CVEAffectedVersion table)

---

### OP-14. BaseFetcher All-Items-Failed Safety Check — RESOLVED (2026-06-20)

**Resolution**: promoted the all-items-failed safety check from
`BaseGitFetcher.execute()` (step 11) to `BaseFetcher.run()`. When
`execute()` returns normally but all items failed (`items_failed > 0`
and `items_created + items_updated == 0`), `run()` now sets
`status = failure` directly (no `RuntimeError`). The `partial` status
is reserved for runs where at least one item succeeded. The redundant
step 11 in `BaseGitFetcher` was removed and renumbered. See
`docs/features/platform/fetcher-infrastructure.md` (Status
determination precedence).
