# Implementation Plan

**Status**: Living execution roadmap — updated as implementation pieces are
merged.

**Purpose**: Define a dependency-ordered sequence for implementing the
Sentinel backend in small, independently reviewable increments. This document
is a planning aid, not a feature specification: behavior remains authoritative
only in `docs/features/` and the cross-cutting documents they reference.

The operational source of truth for execution is GitHub. This document owns
the roadmap, phase boundaries, dependency rationale, and stable piece IDs.
GitHub owns the live status of each piece, its branch, pull request, blockers,
and completion evidence.

## Contents

- [Planning Model](#planning-model)
- [Guiding Principles](#guiding-principles)
- [Piece Definition of Done](#piece-definition-of-done)
- [Specification and WIP Boundary](#specification-and-wip-boundary)
- [Release Strategy](#release-strategy)
- [Phase Overview](#phase-overview)
- [GitHub Tracking Model](#github-tracking-model)
- [Prep Effort — Image Testing Setup](#prep-effort--image-testing-setup)
- [Phase 0 — Infrastructure Completion and Validation](#phase-0--infrastructure-completion-and-validation)
- [Phase 1 — Cross-Cutting and Identity Roots](#phase-1--cross-cutting-and-identity-roots)
- [Phase 2 — Local Identity Foundation](#phase-2--local-identity-foundation)
- [Phase 3 — Generic Fetcher Platform](#phase-3--generic-fetcher-platform)
- [Phase 4 — Ticket, CVE, Product, and Package Domain Core](#phase-4--ticket-cve-product-and-package-domain-core)
- [Phase 5 — CVE Fetcher Infrastructure and Ingestion](#phase-5--cve-fetcher-infrastructure-and-ingestion)
- [Phase 6 — Domain Operational Completion](#phase-6--domain-operational-completion)
- [Phase 7+ — Integrations Requiring WIP Specifications](#phase-7--integrations-requiring-wip-specifications)
- [Open Questions to Revisit](#open-questions-to-revisit)
- [Progress Log](#progress-log)

---

## Planning Model

### Phase

A **phase** is a cohesive program increment with an outcome and an optional
release checkpoint. A phase is represented by a GitHub milestone and a parent
issue. It is never an implementation branch or a pull request.

### Piece

A **piece** is the smallest dependency-complete unit that can be implemented,
tested, reviewed, and merged independently while leaving `master` deployable.
Each piece has a stable ID (`P<phase>-<sequence>`, for example `P1-03`) and is
represented by one GitHub sub-issue.

Candidate IDs in a future, not-yet-elaborated phase remain provisional. They
become stable when progressive elaboration creates their GitHub issues.
Phase-planning work items use `PG<phase>-<sequence>`, beginning at `00`.
Documentation gates use `SG<phase>-<sequence>`; a nested leaf gate appends one
uppercase letter to its umbrella ID (for example, `SG4-05A`).

Execution follows `docs/conventions.md` (Git Conventions). The roadmap-specific
mapping is:

```
one piece = one issue = one topic branch = one pull request = one squash merge
```

This is a default rather than an artificial size rule. A small specification
may fit in one piece; a large specification may require several pieces. A
model, its migration, and its direct model tests normally remain together
because separating them would leave an unusable intermediate state.

### Completion boundary

A piece is complete only after its pull request is squash-merged into
`master`. Passing tests on an unmerged branch is not completion.

### Progressive elaboration

The roadmap lists candidate pieces for all phases, but only the next executable
phase is expanded into detailed GitHub sub-issues. Later phases are refined
when their prerequisites are nearly complete. This prevents stale issue trees
while specifications and dependencies continue to evolve.

## Guiding Principles

1. **Dependency order over document order.** Models and leaf infrastructure
   precede services; services precede API handlers and task wrappers.
2. **One domain piece at a time.** Dependent domain work is not developed in
   parallel. Independent maintenance work may continue through its own PRs.
3. **Specs first.** Every implementation piece maps to an approved and
   sufficient specification. A missing or insufficient contract is resolved
   in a separate `docs/` branch and merged before implementation starts.
4. **No planning-document behavior.** This plan may defer behavior, but it
   cannot invent a stub, fallback, or partial semantic contract absent from
   the owning specification.
5. **Incremental verification.** Every merge leaves the repository green and
   the implemented slice independently testable.
6. **No deadline-driven coupling.** Dates and estimates may be tracked for
   operational planning, but timeboxing never justifies combining unrelated
   pieces or bypassing verification.

## Piece Definition of Done

A piece is complete only when all applicable conditions below are satisfied:

1. **Tracking issue ready.** The issue identifies the stable piece ID, owning
   specification(s), direct blockers, acceptance criteria, expected artifacts,
   and applicable reviewers.
2. **Specification gate passed.** The owning specification has been re-read in
   full and any prerequisite documentation PR has already merged.
3. **Data model first.** If schema changes are required,
   `docs/data-model.md` already contains the approved contract. The piece
   includes the SQLAlchemy model, Alembic migration, and model/migration tests.
4. **Implementation and tests.** Code follows `docs/conventions.md`; tests
   cover happy paths, validation and error paths, permissions, edge cases, and
   audit atomicity where applicable.
5. **Automated verification green.** At minimum:
   - `cd backend && uv run pytest`
   - `cd backend && uv run ruff check .`
   - `cd backend && uv run ruff format --check .`
   - `cd backend && uv run alembic upgrade head && uv run alembic check`
     when database artifacts exist or change
   - security and image checks required by the owning spec or CI
6. **Applicable reviewer agents passed.** Findings rated Needs revision,
   Critical, or High are resolved before merge; applicable minor findings are
   fixed in the same PR.
7. **Manual verification addressed.** The PR satisfies the manual verification
   evidence requirement in `docs/conventions.md` (Pull Request Requirements).
8. **Image smoke coverage addressed.** Apply the Growth Rule in
   `docs/features/platform/testing-strategy.md` in the introducing piece; the
   PR records the result or why it is not applicable.
9. **PR and merge gates passed.** The PR satisfies `docs/conventions.md` and
   Guardrail 25, is squash-merged, and closes its issue. Project automation
   should then reflect Done; Project status is presentation, not an additional
   completion gate.

## Specification and WIP Boundary

Implementation readiness is a versioned roadmap decision. Historical review
records under `docs/reviews/` provide context only and MUST NOT authorize
implementation.

The groups below are currently classified as WIP by this roadmap because their
own documents contain placeholders, unresolved review findings, or missing
contracts. Their GitHub issues remain Blocked until a dedicated documentation
PR resolves those deficiencies, passes the applicable reviews, and merges.

Current blocked groups:

| Domain | Specifications |
|---|---|
| IBS integration | `ibs-integration`, `ibs-rabbitmq-integration`, `ibs-submission-tracking`, `ibs-track-release-detection`, `ibs-product-release-detection` |
| Git release detection | `git-track-release-detection`, `git-product-release-detection` |
| Products | `product-catalog`, `product-lifecycle-transitions` |
| Package ownership | `package-bugowner`, `maintainer` |
| Advanced identity | `sso-authentication`, `identity-provisioning` |

### Phase 4 specification-readiness gate (`SG4-00`)

Phase 4 requires several independent documentation corrections before domain
implementation can proceed. The former `RG-01` work item treated the
product/package/CVE boundary as one documentation PR. Reassessment after Phase
3 found that this would mix unrelated security, data-model, lifecycle,
transaction, API, and fetcher contracts into an oversized work unit.

GitHub issue #21 is therefore the `SG4-00` umbrella gate. It closes only after
all `SG4-01` through `SG4-12` documentation gates merge. `SG4-05` and
`SG4-07` are nested umbrella gates because each spans several independently
implementable contracts; their leaf sub-gates each own one coherent
specification correction and one documentation branch/PR. Every other SG4
child remains a directly executable documentation work item.

The approved planning direction for those gates is:

- Phase 4 includes the Product/ProductRepository catalog, live SMELT and AIMAAS
  synchronization, catalog readiness, package resolution, and package-tree
  mutations. Resolution of each package candidate must fail before mutation
  rather than persist an internally incomplete package/track/Product tree.
  Independently successful package candidates retain the per-package
  durability defined by the CVE ingestion contract.
- WIP IBS bugowner, maintainer, submission tracking, and release-detection
  behavior remains outside Phase 4. Package addition does not invoke those
  side effects.
- Confidential-ticket visibility in Phase 4 uses scope and explicit
  `TicketAccessGrant` records. Bugowner cache data is not an authorization
  source. This avoids granting embargoed-ticket access from a cache whose
  owning WIP contract permits long refresh intervals.
- CVE ingestion is split into a database transaction and a post-commit package
  resolution workflow. No no-op package-resolution seam is introduced.
- The generic `run_catch_up` task substrate precedes status reconciliation;
  CVE-specific fetcher registration and on-demand orchestration follow the
  source-neutral ingestion service. This removes the former dependency cycle.
- Source-specific CVE parsing and NVD applicability-tree selection move to
  Phase 5, where real upstream contracts and fixtures can be verified with
  their first consumers.

Until the applicable SG4 gate merges, its dependent implementation pieces
remain Blocked. The dependency graph below records those relationships
directly rather than imposing one global documentation barrier on independent
foundations.

### Partially implementable specifications

Some approved specifications contain functions with later domain dependencies.
Their pieces may implement only complete, independently specified functions;
they must not weaken a function's contract to make it fit an earlier phase.
Examples:

- `user_service.update_roles()` and `deactivate_user()` require Ticket models,
  Ticket audit events, and ticket unassignment logic. They are deferred from
  Phase 2 to Phase 4 rather than implemented without those side effects.
- System-setting mutation and recalculation endpoints require Ticket/CVSS
  services. `P2-14` implements persistence/startup foundations, `P2-15`
  implements the two read APIs, and mutation/recalculation behavior completes
  only in `P4-35`.
- `BaseCVEFetcher` requires CVE/Ticket models and CVE services. Only generic
  `BaseFetcher` infrastructure belongs in Phase 3.
- The IBS consumer status endpoint defined in
  `integrations/ibs-rabbitmq-integration.md` remains deferred with that WIP
  integration.

## Release Strategy

Release-please derives versions from merged Conventional Commits. The plan
does not predict version numbers. Release mechanics are authoritative in
`docs/deployment.md` (Release Process).

At the end of a deployable phase, evaluate a **release checkpoint**:

1. verify every required piece issue in the milestone is closed by a merged PR;
2. inspect the current release-please PR and its computed version;
3. follow the release and explicit merge procedures in `docs/deployment.md`
   and `AGENTS.md`.

Phase 0 is infrastructure-only and does not require a release. A phase may also
defer its release checkpoint when it contains foundations with no useful
deployable behavior; that decision is recorded in the phase parent issue.

## Phase Overview

| Phase | Focus | Status |
|---|---|---|
| Prep | Image testing setup | Completed (2026-07-29) |
| 0 | Infrastructure completion and validation | Completed (2026-07-30) |
| 1 | Cross-cutting platform foundations and identity roots | Completed (2026-08-03) |
| 2 | Local identity foundation | Completed (2026-08-14) |
| 3 | Generic fetcher platform | Completed (2026-08-24) |
| SG4-00 | Complete Phase 4 specification readiness | In progress — umbrella documentation gate |
| 4 | Ticket, CVE, Product, and package domain core | Elaborated; implementation blocked by applicable SG4 gates |
| 5 | CVE fetcher infrastructure and real ingestion | Blocked by Phase 4 |
| 6 | Domain operational completion | Blocked by Phases 4-5 |
| 7+ | WIP integrations and advanced identity | Blocked by specification work |

## GitHub Tracking Model

The roadmap uses the following operational structure:

- **Project**: `Sentinel Backend Implementation`, covering the entire plan.
- **Milestone per phase**: phases are repository milestones without artificial
  due dates unless a real external deadline exists.
- **Parent issue per phase**: owns the outcome, phase entry/exit criteria,
  candidate pieces, and release-checkpoint decision.
- **Sub-issue per piece**: owns implementation acceptance and closes through
  its PR.
- **Native dependencies**: use `blocked by` / `blocking` relationships for
  direct dependencies. Do not encode the dependency graph only in prose.
- **Label**: `implementation-plan` enables Project auto-add and focused issue
  queries.

Recommended Project statuses:

`Backlog` → `Ready` → `In progress` → `In review` → `Done`, with `Blocked` for
items that cannot advance. Built-in automation should move merged PRs/closed
issues to Done. The Project tracks issues, not a duplicate row for every linked
PR.

Initial views:

1. **Table — Execution**: grouped by milestone, sorted by piece ID.
2. **Board — Current work**: grouped by status.
3. **Blocked**: filtered to blocked issues and visible dependency relations.

A roadmap/date view is intentionally deferred until real scheduling needs
exist. Phase parent issues exist for the approved roadmap; detailed sub-issues
are created only for the next executable phase under the progressive
elaboration rule.

---

## Prep Effort — Image Testing Setup

**Status: Completed (2026-07-29).** Established black-box testing of the built
OCI image. The durable contract lives in
`docs/features/platform/testing-strategy.md` (Image / Container Smoke
Testing).

Delivered artifacts:

- `docker-compose.smoke.yml`
- `backend/tests/image/`
- `scripts/image-smoke.sh`
- blocking build → smoke → push gate in `.github/workflows/build-images.yml`

Image-suite growth follows the authoritative Growth Rule in
`docs/features/platform/testing-strategy.md`.

## Phase 0 — Infrastructure Completion and Validation

**Status: Completed (2026-07-30).** Added the runtime dependencies required by
later specifications (`structlog`, `cvss`, `celery-redbeat`) and validated the
baseline: dependency resolution, tests, lint, Alembic environment, image build,
and Python-version drift check. No domain logic or seam was introduced.

## Phase 1 — Cross-Cutting and Identity Roots

**Status: Completed (2026-08-03).** All six pieces merged: structured logging
and request correlation, the shared HTTP/TLS client, `/health` and `/ready`
with their image-smoke assertions, the identity root (`User`/`UserRole`
models, static role/capability enums, migration), `AuditEventMixin` and
`BaseAuditLog`, and the Celery application bootstrap with UTC/redbeat startup
validation. One spec gap surfaced and was resolved in-phase (`SG-01`, #28,
mixed Redis readiness failure precedence). Native `blocked by` relationships
were recorded on every piece issue and all were resolved before closure.

**Outcome**: platform leaf infrastructure is operational, and the minimum
identity root required by foreign keys and audit trails exists.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P1-01` | Structured logging and request correlation | Phase 0 | `platform/logging.md` |
| `P1-02` | Shared networking and TLS client | `P1-01` | `platform/networking.md` |
| `P1-03` | Health/readiness endpoints and image assertions | `P1-01` | `platform/health-endpoints.md` |
| `P1-04` | Identity root: User/UserRole models, role/capability enums, migration and migration image smoke | Phase 0 | `identity/rbac.md`, `data-model.md` |
| `P1-05` | AuditEventMixin and BaseAuditLog | `P1-04` | `platform/audit-trail-infrastructure.md` |
| `P1-06` | Celery application bootstrap and UTC/redbeat startup validation | `P1-01` | `platform/fetcher-infrastructure.md` startup contracts |

`P1-04` deliberately precedes audit infrastructure because
`AuditEventMixin.user_id` is an FK to `user.id`. It owns only the identity
root and static authorization types, not authentication endpoints or user
lifecycle services.

**Release checkpoint**: taken. This phase's `/health` and `/ready` endpoints
were the first observable behavior, so the pending release-please PR was
evaluated and merged, releasing **v0.3.0** (tag, GitHub Release, and image
publication all completed; see Progress Log).

## Phase 2 — Local Identity Foundation

**Status: Completed (2026-08-14).** All implementation pieces and
documentation gates merged and were released in v0.4.0.

**Outcome**: local users can authenticate, bootstrap an administrator, use API
keys, and access the ticket-independent identity management surface.

The Phase 2 documentation gates referenced below are: `SG-02` (#90, Redis
contracts and testability), `SG-03` (#105, session, lockout, and identity
logging), `SG-04` (#106, API key contracts), `SG-05` (#108, user lifecycle and
identity audit), `SG-06` (#109, CLI invocation and transactions), `SG-07`
(#107, system settings bootstrap and reads), and `SG-08` (#101, Redis I/O
ordering and rollback claims). The optional authentication contract gate is
#177.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P2-01` | Session and API key persistence | Phase 1, SG-03, SG-04 | `identity/authentication.md`, `identity/api-key-management.md` |
| `P2-02` | Identity audit persistence and service | SG-05 | `identity/identity-audit-log.md` |
| `P2-03` | JWT session service, logout, and cleanup | SG-08, `P2-01` | `identity/authentication.md` |
| `P2-04` | Local password authentication and lockout | `P2-03` | `identity/local-authentication.md` |
| `P2-05` | API key lifecycle service and audit | `P2-01`, `P2-02` | `identity/api-key-service.md` |
| `P2-06` | Unified authentication and capability dependencies | `P2-05`, `P2-03` | `identity/rbac.md`, `identity/authentication.md` |
| `P2-07` | Self-service and admin API key endpoints | `P2-06` | `identity/api-key-management.md`, `identity/api-key-service.md` |
| `P2-08` | User read functions plus `create_user`, `update_user`, and `reactivate_user`; add `email-validator` | `P2-02`, `P2-04` | `identity/user-service.md` |
| `P2-09` | `reset_password` and `unlock_user` services | `P2-03`, `P2-08` | `identity/user-service.md` |
| `P2-10` | User read, profile, and identity audit APIs | `P2-06`, `P2-08` | `identity/user-management.md`, `identity/identity-audit-log.md` |
| `P2-11` | Ticket-independent admin user APIs | `P2-09`, `P2-10` | `identity/user-management.md`, `identity/rbac.md` |
| `P2-12` | CLI infrastructure; `manage-user create`, `list`, and `show` | SG-06, `P2-08` | `platform/cli-infrastructure.md`, `identity/user-management.md` |
| `P2-13` | `manage-user set-password` and `unlock`; `api-key list` and `revoke` | `P2-05`, `P2-09`, `P2-12` | `platform/cli-infrastructure.md`, `identity/user-management.md`, `identity/api-key-management.md` |
| `P2-14` | System settings persistence and bootstrap | SG-07 | `platform/system-settings.md` |
| `P2-15` | System settings read and audit APIs | `P2-14`, `P2-06` | `platform/system-settings.md` |
| `P2-16` | Optional authentication for Public reads | `P2-06`, `P2-10`, #177 | `identity/authentication.md`, `api-spec.md`, `identity/rbac.md` |

The Phase 2 lifecycle mutation inventory is exactly `create_user`,
`update_user`, `reactivate_user`, `reset_password`, and `unlock_user`.
`P2-08` owns the first three plus the user query functions; `P2-09` owns the
last two. `P2-11` owns exactly the ticket-independent administrator endpoints
for create, update, reactivate, reset password, and unlock. It does not own role
management through `update_roles` or the `POST .../roles` endpoint, nor
deactivation. Initial roles supplied to `create_user` remain in scope.

`update_roles`, `deactivate_user`, deactivation impact, every ticket
unassignment/audit side effect, their API endpoints, and their CLI surfaces are
deferred in their entirety to Phase 4. The composite `manage-user update`
command is likewise deferred because it includes role changes; Phase 2 does
not expose a reduced option set or partially implement its workflow.

The Phase 2 CLI inventory is exactly these seven commands:

- `sentinel manage-user create`
- `sentinel manage-user list`
- `sentinel manage-user show`
- `sentinel manage-user set-password`
- `sentinel manage-user unlock`
- `sentinel api-key list`
- `sentinel api-key revoke`

`P2-08` adds the direct `email-validator` dependency because the lifecycle
service is its first consumer. `P2-12` adds the direct Click dependency,
registers `[project.scripts]` exactly as `sentinel = "app.cli:main"`, and adds
`backend/app/cli/__main__.py` for `python -m app.cli`. Dependency lock and
packaging metadata are updated with those implementation pieces; SG-06 itself
changes documentation only.

SG-05 resolves the former Phase 4 documentation gate for the identity
role-removal audit contradiction. The remaining Phase 4 pieces therefore own
implementation of the now-reconciled complete contracts, not another
documentation decision.

The image suite adds a CLI bootstrap assertion in the piece that introduces
the runnable `sentinel` command.

`P2-14` owns only the `SystemSetting` and `SettingAuditEvent` models,
`SettingAuditEventType`, their migration and idempotent seed,
`get_default_cvss_version()`, `SettingAuditLog`, lifespan bootstrap, the image
startup dependency on successful migration, and their model/migration/service/
lifespan/image tests. It does not introduce settings API routes.

`P2-15` owns only `GET /api/v1/admin/settings` and
`GET /api/v1/admin/settings/audit-log`, their schemas and read/query services,
and their permission, validation, API, audit-read, OpenAPI, and image tests. It
does not mutate settings or create audit events. The PATCH endpoint,
recalculation endpoint, Redis/Celery coordination, mutation transaction and
audit insertion, and recalculation task remain exclusively in `P4-35`.

## Phase 3 — Generic Fetcher Platform

**Status: Completed (2026-08-24).** All four documentation gates
(`SG3-01`-`SG3-04`) and all eleven implementation pieces (`P3-01`-`P3-11`)
merged, including four bug-fix issues surfaced during implementation
(queued fetcher run lifecycle, finalization-after-setup-failure,
stale-detection timeout persistence, prefork worker pool enforcement).
Released as part of **v0.5.0**.

**Outcome**: non-domain-specific fetchers can register, schedule, run, report
metrics, and be operated through generic API/CLI surfaces.

Phase 3 remains independent of CVE, Ticket, package-resolution, and real
upstream-ingestion behavior. `SG3-01` must detach CPE mapping validation from
generic worker startup and assign its implementation to `P4-05` before Phase 3
implementation begins.

The documentation gates below must merge before implementation begins:

| ID | Gate | Direct blockers | Primary contract |
|---|---|---|---|
| `SG3-01` | Detach CPE mapping from generic fetcher startup | `PG3-00` | CPE mapping, fetcher infrastructure, CVE service/NVD, and testing contracts |
| `SG3-02` | Complete generic fetcher runtime contracts | `SG3-01` | `platform/fetcher-infrastructure.md`, `architecture.md`, `conventions.md` |
| `SG3-03` | Complete fetcher operations and CLI contracts | `SG3-02` | `platform/fetcher-operations.md`, `api-spec.md`, `conventions.md` |
| `SG3-04` | Define test-only fetcher system-test contract | `SG3-03` | `platform/testing-strategy.md`, `platform/fetcher-infrastructure.md` |

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P3-01` | Fetcher persistence and audit foundation | `SG3-04` | `platform/fetcher-infrastructure.md`, `data-model.md` |
| `P3-02` | BaseFetcher lifecycle and registry | `P3-01` | `platform/fetcher-infrastructure.md`, `platform/networking.md` |
| `P3-03` | Generic fetcher task and concurrency | `P3-02` | `platform/fetcher-infrastructure.md` |
| `P3-04` | Fetcher config bootstrap and process startup | `P3-02` | `platform/fetcher-infrastructure.md`, `deployment.md` |
| `P3-05` | RedBeat scheduling and reconciliation | `P3-03`, `P3-04` | `platform/fetcher-infrastructure.md` |
| `P3-06` | Public fetcher observation API | `P3-05`, `P2-16` | `platform/fetcher-operations.md` |
| `P3-07` | Admin fetcher configuration and audit reads | `P3-04`, `P2-06` | `platform/fetcher-operations.md` |
| `P3-08` | Fetcher configuration mutation | `P3-05`, `P3-07` | `platform/fetcher-operations.md` |
| `P3-09` | Manual fetcher trigger | `P3-05`, `P3-06` | `platform/fetcher-operations.md` |
| `P3-10` | Fetcher diagnostic CLI | `P3-04`, `P2-12` | `platform/fetcher-operations.md` |
| `P3-11` | Test-only fetcher system validation | `P3-05`, `P3-06` | `platform/testing-strategy.md` |

`P3-04` extends the already-active worker and Beat image roles with
fetcher-specific bootstrap and startup assertions; it does not introduce those
roles. `P3-06` owns `GET /api/v1/fetchers`, run list/detail, and timeline.
`P3-07` owns the capability-protected config and audit-log reads. `P3-08` and
`P3-09` isolate the two mutation workflows because they have different
transaction, audit, Redis, and broker semantics. `SG3-03` owns the approved
in-flight-timeout and manual-trigger transaction decisions before either
mutation piece begins. `SG3-02` owns worker handling of supplied run records.

`P3-11` validates the complete generic pipeline without introducing
production-facing test scaffolding; `SG3-04` owns the test-harness contract.

**`P3-11` ownership boundaries**:

- The test-only `BaseFetcher` subclass, system marker, process launchers,
  harness fixtures, bounded polling, deterministic cleanup, and the happy-path
  pipeline assertion.
- Production-exclusion assertion: the shipped image and normal API output do
  not contain the test-only fetcher.
- Integration of the system suite into the pre-push hook and a separate
  blocking CI gate.
- P3-11 does NOT replace focused unit/integration tests owned by P3-02
  (BaseFetcher lifecycle), P3-03 (task and concurrency), P3-04 (bootstrap and
  startup), P3-05 (RedBeat reconciliation), or P3-06 (API serialization).
  Each introducing piece retains its own verification.

The IBS RabbitMQ consumer status endpoint remains with its owning disabled
integration in Phase 7+.

**Ownership boundaries clarified by `SG3-02`**:

- `P3-02` owns BaseFetcher lifecycle (`run()` execution, finalization, status
  determination, cursor persistence, metric helpers, error sanitization,
  detached runtime config snapshot, stored-settings validation,
  `FetcherConfigError`, lazy HTTP client lifecycle), registry
  (`FETCHER_REGISTRY`, `__init_subclass__` validation, `fetcher_discovery`),
  and the generic catch-up extension points (override-point declaration,
  `participates_in_catch_up` flag, registry accessor
  `get_catch_up_fetchers()`, import-time catch-up signature validation).
  It does NOT own the `run_catch_up` Celery task, `CVENotInSource`,
  ticket/CVE symbols, `BaseCVEFetcher`, or any production fetcher.
- `P3-03` owns the `run_fetcher` Celery task wrapper, atomic run
  acquisition (FetcherConfig-root locking, active-run evaluation,
  scheduled-run insertion, stale finalization, manual `run_id` adoption),
  and concurrency control enforcement. It delegates execution to
  `BaseFetcher.run()` after acquisition completes and the transaction
  commits.
- `P3-04` owns `bootstrap_fetcher_configs()` (caller-supplied session,
  flush without commit), worker and Beat signal handler placement under
  `app/tasks/` (not `app/core/`), engine disposal before worker fork,
  and fail-fast startup behavior across all processes.
- `P4-11` owns the generic `run_catch_up` Celery wrapper required by status
  reconciliation and the `CVENotInSource` signal that its exception handling
  consumes. `P4-30` owns `BaseCVEFetcher`, the default CVE `catch_up()`
  implementation, and CVE source registries. `P4-31` owns on-demand invocation
  and source/refetch operations. Real production CVE fetcher registrations
  remain in Phase 5.

## Phase 4 — Ticket, CVE, Product, and Package Domain Core

**Status: Elaborated.** Documentation gates must merge before their dependent
implementation pieces begin.

**Outcome**: consumers can create, inspect, and mutate Ticket, CVE, Product,
and package data through permission-tested APIs before any production CVE
fetcher begins automatic ingestion. Product catalog synchronization and real
SMELT package resolution are operational because package-tree correctness
depends on them.

### Phase boundary

Phase 4 includes:

- source-neutral CVE persistence, CVSS logic, ingestion, and CVE fetcher
  infrastructure;
- Ticket persistence, lifecycle, status gates, confidentiality through scope
  and explicit grants, references, audit, and API surfaces;
- Product and ProductRepository persistence, SMELT/AIMAAS synchronization,
  readiness, lifecycle/threshold evaluation, and the public Product API;
- package-tree persistence, SMELT resolution, package mutations, reads, and
  post-ingest resolution;
- ticket-coupled local identity operations deferred from Phase 2; and
- system-setting mutation and CVSS batch recalculation deferred from Phase 2.

Phase 4 explicitly excludes IBS bugowner persistence/resolution, maintainer
views, submission tracking, release detection, the IBS RabbitMQ consumer, and
git/SLFO package workflows. Those features remain with their WIP owning specs
in Phase 7+. Phase 4 package responses therefore contain no bugowner/member
data, and confidential visibility does not consult bugowner caches.

### Documentation gates

`SG4-00` (#21) is the top-level umbrella. `SG4-05` (#335) and `SG4-07`
(#337) are nested umbrellas whose leaf sub-gates are independently mergeable
documentation work items. All other child gates are independently mergeable
directly. Each implementation issue records only its direct leaf-gate
blockers.

| ID | Gate | Direct blockers | Primary contract |
|---|---|---|---|
| `SG4-01` | Define the package, IBS, and confidentiality boundary | `PG4-00` | package model/service, Ticket visibility, RBAC, WIP IBS specs |
| `SG4-02` | Correct Product and repository identity against live SMELT | `SG4-01` | `packages/product-catalog.md`, `data-model.md`, `data-sources.md` |
| `SG4-03` | Complete Product synchronization, readiness, and read contracts | `SG4-02` | product catalog, configuration, fetcher registry, Product API |
| `SG4-04` | Complete lifecycle, threshold, and eligibility contracts | `SG4-02` | product catalog/lifecycle, package service, Ticket audit |
| `SG4-05` | Complete package contracts | Sub-issue roll-up: `SG4-05A` through `SG4-05D` | package model/service, Ticket audit, package APIs |
| `SG4-05A` | Define package mutation foundations | `SG4-03`, `SG4-04` | package persistence, direct state mutations, locking, audit |
| `SG4-05B` | Complete exclusion, restoration, and actionability contracts | `SG4-05A` | manual hierarchical exclusion, restoration, derived actionability |
| `SG4-05C` | Complete SMELT package orchestration contracts | `SG4-05A` | SMELT resolution, package-add orchestration, typed results |
| `SG4-06` | Correct Ticket lifecycle, locking, and audit contracts | `SG4-01` | ticket service/mutations/audit, data model |
| `SG4-07` | Complete Ticket/CVE access and read contracts | Sub-issue roll-up: `SG4-07A` through `SG4-07D` | Ticket/CVE/reference APIs, RBAC, API/architecture rules |
| `SG4-07A` | Define confidential Ticket access contracts | `SG4-06` | visibility predicate, explicit grants, accessible-resource resolution |
| `SG4-05D` | Complete package read contracts | `SG4-05B`, `SG4-07A` | package queries, soft-deletion visibility, response schemas |
| `SG4-07B` | Complete Ticket and audit read contracts | `SG4-05D` | Ticket list/detail, audit reads, detail assembly |
| `SG4-07D` | Complete Ticket reference contracts | `SG4-07A` | reference reads/mutations, URL identity, locking, audit |
| `SG4-08` | Correct CVSS resolution and status-reconciliation contracts | `SG4-05B`, `SG4-06` | CVSS scoring, ticket mutations, data model |
| `SG4-09` | Complete CVE ingestion and source-status contracts | `SG4-05C`, `SG4-08` | CVE service/tracking, ticket service, package dispatch |
| `SG4-10` | Complete BaseCVEFetcher, on-demand, and catch-up contracts | `SG4-09` | CVE/generic fetcher infrastructure, CVE service |
| `SG4-07C` | Complete CVE, CVSS, and source read contracts | `SG4-07A`, `SG4-10` | CVE/CVSS/source queries, confidentiality, response schemas |
| `SG4-11` | Correct ticket-coupled identity lifecycle contracts | `SG4-06` | user service/management, RBAC, Ticket service/audit |
| `SG4-12` | Correct settings mutation and CVSS batch contracts | `SG4-08` | system settings, CVSS scoring, transaction/Redis conventions |

### Implementation pieces

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P4-01` | CVE and CVESource persistence | `SG4-09` | `tickets/cve-service.md`, `data-model.md` |
| `P4-02` | CVE enrichment persistence and ingestion DTOs | `P4-01` | CVE/CVSS specs, `data-model.md` |
| `P4-03` | Ticket, audit, access-grant, and reference persistence | `SG4-07D`, `P4-01` | Ticket specs, `data-model.md` |
| `P4-04` | Pure CVSS resolution and vector validation | `SG4-08` | `tickets/cvss-scoring.md` |
| `P4-05` | CPE mapping loader, cached resolvers, and resource validation | `SG3-01` | `packages/cpe-package-mapping.md` |
| `P4-06` | Product and ProductRepository persistence | `SG4-02` | `packages/product-catalog.md`, `data-model.md` |
| `P4-07` | CVE existence and atomic source-status primitives | `P4-01` | `tickets/cve-service.md` |
| `P4-08` | SMELT Product sync, catalog readiness, and Product API | `SG4-03`, `P4-06`, `P4-09` | `packages/product-catalog.md` |
| `P4-09` | AIMAAS lifecycle and threshold synchronization | `SG4-04`, `P4-06` | product catalog/lifecycle specs |
| `P4-10` | Ticket package-tree persistence | `SG4-05A`, `P4-03`, `P4-06` | package model/service, `data-model.md` |
| `P4-11` | Generic catch-up task and CVE absence signal | `SG4-10` | generic/CVE fetcher infrastructure |
| `P4-12` | Ticket reconciliation and CVSS recalculation chain | `P4-02`, `P4-03`, `P4-04`, `P4-10`, `P4-11` | ticket mutations, CVSS scoring |
| `P4-13` | CVSS, manual-severity, and manual-zone mutation services | `P4-12` | ticket mutations, CVSS scoring |
| `P4-14` | Package record and state mutation services/APIs | `P4-10`, `P4-12` | package model/service |
| `P4-15` | Package exclusion, restore, and actionability services/APIs | `SG4-05B`, `P4-14` | package model/service, Ticket audit |
| `P4-16` | SMELT package resolution and package-add API | `SG4-05C`, `P4-08`, `P4-09`, `P4-14` | package model/service |
| `P4-17` | Product lifecycle evaluator and eligibility sub-task | `P4-09`, `P4-11`, `P4-15` | product lifecycle transitions |
| `P4-18` | Confidential visibility and explicit access services | `SG4-07A`, `P4-03` | Ticket service, tickets, RBAC |
| `P4-19` | Ticket reference service | `SG4-07D`, `P4-03`, `P4-12` | `tickets/ticket-references.md` |
| `P4-20` | Ticket creation and CVE-association services | `P4-07`, `P4-12` | `tickets/ticket-service.md` |
| `P4-21` | Ticket assignment and lifecycle services | `P4-12`, `P4-18` | ticket service/mutations |
| `P4-22` | Package query services and read APIs | `SG4-05D`, `P4-10`, `P4-18` | package model/service |
| `P4-23` | Ticket/CVE queries, detail assembly, and audit read APIs | `SG4-07B`, `SG4-07C`, `P4-02`, `P4-18`, `P4-22` | tickets, CVE tracking, Ticket audit |
| `P4-24` | Ticket create and CVE-associate APIs | `P4-20`, `P4-23`, `P4-31` | tickets, CVE service |
| `P4-25` | CVSS assessment and manual-severity APIs | `P4-13`, `P4-18`, `P4-23` | CVSS scoring, tickets |
| `P4-26` | Ticket lifecycle and manual-zone APIs | `P4-13`, `P4-21`, `P4-23` | ticket service/mutations, tickets |
| `P4-27` | Confidentiality, explicit-access, and reference APIs | `P4-18`, `P4-19`, `P4-23` | tickets, references, RBAC |
| `P4-28` | Source-neutral CVE ingestion transaction | `P4-02`, `P4-07`, `P4-13`, `P4-20` | `tickets/cve-service.md` |
| `P4-29` | Post-ingest package-resolution task | `P4-05`, `P4-16`, `P4-28` | CVE service, package service |
| `P4-30` | BaseCVEFetcher and CVE source registries | `P4-07`, `P4-11`, `P4-28` | CVE fetcher infrastructure |
| `P4-31` | On-demand/catch-up orchestration and source/refetch APIs | `SG4-07C`, `P4-18`, `P4-29`, `P4-30` | CVE service/tracking, CVE fetcher infrastructure |
| `P4-32` | Active-ticket unassignment primitive and audit | `SG4-11`, `P4-03` | user service, Ticket service/audit |
| `P4-33` | User role mutation API and CLI | `P4-32` | identity user service/management |
| `P4-34` | User deactivation impact, API, and CLI | `P4-32` | identity user service/management |
| `P4-35` | Settings PATCH and CVSS recalculation task | `SG4-12`, `P4-12` | system settings, CVSS scoring |

Each piece owns focused tests and the image-suite Growth Rule consequences of
the artifacts it introduces. Model pieces own their migrations and migration
tests. API pieces own permission, validation, error, OpenAPI, and image
coverage. Task pieces own task registration, cross-loop engine disposal, and
sync-entry-point structural tests.

`P4-05` is the sole implementation owner for the committed mapping data,
canonical key grammar, CPE parser, package-relative loader, process cache,
both public resolvers, focused tests, and CI validation of the real file.
It normalizes the small set of escaped or truncated legacy keys in the committed
JSON before enabling canonical-file validation, ensures the resource is present
in the installed wheel and container image, and verifies parser, loader, cache,
resolver, failure, fallback, and canonical-data behavior directly. Generic
worker startup has no CPE dependency, and `P4-05` introduces no eager startup
check. If a later consumer requires eager validation, the mapping module owns
the reusable check contract and the consumer work item owns its invocation.
`P4-29` owns post-ingest integration tests without duplicating `P4-05`
contract tests. NVD applicability-tree and `vulnerable=false` selection
semantics are source-specific and belong to `P5-02`; they do not block the
source-neutral `P4-28` ingestion transaction.

## Phase 5 — CVE Fetcher Infrastructure and Ingestion

**Outcome**: verified external CVE data flows through the generic fetcher
platform and the complete CVE ingestion service.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P5-01` | BaseGitFetcher, git operations, git-worker runtime and image smoke | `P4-30` | `platform/git-fetcher-infrastructure.md` |
| `P5-02` | NVD applicability selection and fetcher | `P4-31` | `tickets/cve-sync-nvd.md` |
| `P5-03` | Red Hat fetcher | `P4-31` | `tickets/cve-sync-redhat.md` |
| `P5-04` | GHSA fetcher | `P4-31` | `tickets/cve-sync-ghsa.md` |
| `P5-05` | OSV fetcher | `P4-31` | `tickets/cve-sync-osv.md` |
| `P5-06` | EPSS fetcher | `P4-31` | `tickets/cve-sync-epss.md` |
| `P5-07` | CISA KEV fetcher | `P4-31` | `tickets/cve-sync-kev.md` |
| `P5-08` | Shared CVE 5.x record parser and live-format fixtures | `P4-02` | `platform/cve-record-parser.md` |
| `P5-09` | MITRE fetcher | `P5-01`, `P5-08`, `P4-31` | `tickets/cve-sync-mitre.md` |
| `P5-10` | Linux Kernel fetcher | `P5-01`, `P5-08`, `P4-31` | `tickets/cve-sync-kernel.md` |
| `P5-11` | CVE source failure retry fetcher | `P5-02` through `P5-07`, `P5-09`, `P5-10` | `platform/cve-source-failure-retry.md` |

Each external integration piece satisfies the mandatory external-contract
verification requirements of the implementation workflow. One fetcher per PR
keeps upstream contract risk and rollback scope isolated.

`P5-02` owns the NVD applicability-tree contract and implementation, including
nested operators, negation, `vulnerable=false` prerequisites, version ranges,
and package-candidate selection. `P5-08` owns the source-neutral CVE 5.x parser
against sanitized real MITRE/kernel formats before those two fetchers consume
it. Neither source-specific concern blocks the Phase 4 ingestion DTO or
transaction.

## Phase 6 — Domain Operational Completion

**Outcome**: cross-surface operational behavior is validated after real
ingestion, and remaining non-WIP administrative surfaces are completed.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P6-01` | Real-ingestion CVE → Ticket → package-tree E2E verification | Phase 5 | ingestion and package specs |
| `P6-02` | Fetcher-to-CVE source failure drill-down E2E verification | Phase 5, `P3-06` | fetcher operations and CVE service specs |
| `P6-03` | Full local identity/ticket interaction E2E verification | `P4-33`, `P4-34` | identity and ticket specs |
| `P6-04` | Cross-surface image smoke assertions not naturally owned by one introducing piece | `P6-01` through `P6-03` | `platform/testing-strategy.md` |
| `P6-05` | Operational release checkpoint and manual acceptance | `P6-04` | deployment and testing docs |

Every endpoint is introduced with its owning service slice in Phase 4, before
real fetchers are enabled in Phase 5. Phase 6 does not postpone endpoint smoke
coverage that belongs to an earlier introducing PR; it contains only scenarios
that genuinely span multiple already-merged surfaces.

## Phase 7+ — Integrations Requiring WIP Specifications

Each item requires specification completion and merge before implementation.
The candidate order is provisional and must be recalculated when contracts are
approved:

1. IBS bugowner and maintainer workflows;
2. IBS integration, RabbitMQ consumer, submission tracking, and release
   detection;
3. git/SLFO package and release workflows;
4. SSO authentication and external identity provisioning.

Each large integration becomes its own milestone or a later numbered phase if
its implementation requires multiple independently deployable pieces. The
single `Phase 7+` label is a roadmap placeholder, not a branch or PR scope.

## Open Questions to Revisit

- Exact Phase 7+ decomposition after its specifications are approved.
- Whether OP-6 (periodic ticket status reconciliation as drift detection)
  should become a Phase 6 operational piece or a separate later milestone.
- Whether `RoleMapping` persistence should be introduced only with external
  provisioning or earlier for a demonstrated dependency. No schema-only
  forward-compatibility table is created without a current consumer.

## Progress Log

- **2026-07-29 — Prep Effort completed.** Added image smoke compose,
  black-box tests, runner, and blocking build/publish gate. No seam introduced.
- **2026-07-30 — Phase 0 completed.** Added `structlog`, `cvss`, and
  `celery-redbeat`; validated tests, lint, Alembic, image build, and Python 3.13
  drift checks. No domain code or seam introduced.
- **2026-07-30 — Planning workflow realignment started.** Replaced phase-wide
  execution assumptions with piece-level issue/branch/PR boundaries and
  corrected cross-phase dependencies. GitHub tracking objects are created only
  after this documentation change is merged.
- **2026-08-03 — Phase 1 completed.** All six pieces (`P1-01`-`P1-06`) merged;
  `SG-01` (mixed Redis readiness failure precedence) resolved in-phase. Release
  checkpoint taken: release-please PR merged, releasing **v0.3.0** (tag,
  GitHub Release, and image build/smoke/publish all succeeded).
- **2026-08-14 — Phase 2 completed.** All implementation pieces and
  documentation gates, including `SG-02` (#90), merged. Release checkpoint
  taken: release-please PR #152 merged, releasing **v0.4.0**; tag, GitHub
  Release, image build, smoke gate, and publication all succeeded.
- **2026-08-14 — Phase 3 elaboration started.** Replaced the stale seven-piece
  candidate list with documentation gates and dependency-complete work units.
  Moved CPE mapping implementation to candidate `P4-05`, its first-consumer
  phase, shifting the former candidate `P4-05` through `P4-27` IDs to `P4-06`
  through `P4-28`. `SG3-01` owns the required detachment of CPE validation from
  generic worker startup before Phase 3 implementation begins.
- **2026-08-24 — Phase 3 completed.** All four documentation gates
  (`SG3-01`-`SG3-04`) and all eleven implementation pieces (`P3-01`-`P3-11`)
  merged, including four bug-fix issues surfaced during implementation
  (queued fetcher run lifecycle, finalization-after-setup-failure,
  stale-detection timeout persistence, prefork worker pool enforcement).
  Release checkpoint taken: release-please PR #258 merged, releasing
  **v0.5.0**; tag, GitHub Release, image build, smoke gate, and publication
  all succeeded.
- **2026-08-24 — Phase 4 elaboration started.** Replaced the stale
  product/package/CVE umbrella gate and 28 candidate pieces with twelve
  specification gates and 35 dependency-complete implementation work units.
  Product catalog synchronization remains in Phase 4 because complete package
  resolution depends on it; WIP IBS bugowner, maintainer, submission,
  release-detection, and git/SLFO behavior remains in Phase 7+.
  Source-specific CVE parsing and NVD applicability selection moved to their
  first real consumers in Phase 5.
- **2026-08-24 — Phase 4 gate decomposition refined.** Converted `SG4-05` and
  `SG4-07` into nested umbrellas with four independently mergeable leaf gates
  each, then rewired dependent SG4 and P4 work items to their smallest direct
  specification prerequisites.
