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
- [Phase 4 — Ticket, CVE, and Package Domain Core](#phase-4--ticket-cve-and-package-domain-core)
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

Implementation readiness is a versioned roadmap decision, not a property of
the local review cache in `docs/reviews/.tracking.json`. The cache's `enabled`
field controls review tooling only and MUST NOT authorize implementation.

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

### Mandatory roadmap specification gate (`RG-01`)

The previous version of this plan proposed a no-op
`resolve_ticket_packages` task so CVE ingestion could precede SMELT-backed
package resolution. That seam is not authorized by the owning specifications:
`cve-service.md` and `cve-fetcher-infrastructure.md` require real post-ingest
dispatch to `package_service.add_package_to_ticket()`.

`RG-01` is an immediate documentation work item, not a Phase 4 implementation
piece. It should be resolved after this planning PR and before Phase 4 is
elaborated into GitHub sub-issues. It must choose and specify one of these
coherent paths:

1. complete the product catalog and package orchestration prerequisites, or
2. explicitly define a supported deferred package-resolution mode and its
   recovery semantics in the owning specifications.

It must also resolve these related contract conflicts and dependencies:

- Product/ProductRepository ownership and synchronization timing;
- the `add_package_to_ticket()` ordering conflict between `package-model.md`
  and `package-service.md` (database creation before SMELT versus I/O first);
- bugowner resolution and submission-discovery side effects whose owning specs
  are WIP;
- bugowner-based visibility for confidential tickets required by `rbac.md`,
  including whether PackageBugowner persistence/resolution must precede scoped
  Ticket/CVE APIs or the visibility contract is explicitly deferred;
- whether the package-add API and real CVE ingestion can exist before a
  populated product catalog.

The implementation plan does not choose those behaviors. Until `RG-01` merges,
package orchestration, full CVE ingestion, and all real CVE fetchers remain
blocked. Independent CVE/Ticket schemas and pure domain logic are not blocked.

### Partially implementable specifications

Some approved specifications contain functions with later domain dependencies.
Their pieces may implement only complete, independently specified functions;
they must not weaken a function's contract to make it fit an earlier phase.
Examples:

- `user_service.update_roles()` and `deactivate_user()` require Ticket models,
  Ticket audit events, and ticket unassignment logic. They are deferred from
  Phase 2 to Phase 4 rather than implemented without those side effects.
- System-setting mutation and recalculation endpoints require Ticket/CVSS
  services. Phase 2 implements storage/bootstrap and read behavior; mutation
  behavior completes in Phase 4.
- `BaseCVEFetcher` requires CVE/Ticket models and CVE services. Only generic
  `BaseFetcher` infrastructure belongs in Phase 3.
- The IBS consumer status endpoint in `fetcher-operations.md` remains deferred
  with the disabled IBS RabbitMQ specification.

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
| 2 | Local identity foundation | Not started |
| 3 | Generic fetcher platform | Not started |
| RG-01 | Resolve product/package/CVE contract boundary | Not started — immediate documentation work |
| 4 | Ticket, CVE, and conditional package domain core | Not started; package orchestration blocked by `RG-01` |
| 5 | CVE fetcher infrastructure and real ingestion | Blocked by Phase 4 |
| 6 | Domain operational completion | Blocked by Phases 4-5 |
| 7+ | WIP integrations and advanced identity | Blocked by specification work |

## GitHub Tracking Model

After this plan is merged, create the following operational structure:

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
exist. Create all phase parent issues after this plan merges, but create
detailed sub-issues only for Phase 1. Elaborate Phase 2 when Phase 1 approaches
completion.

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

**Outcome**: local users can authenticate, bootstrap an administrator, use API
keys, and access the ticket-independent identity management surface.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P2-01` | Session/ApiKey models, JWT/session service, current-user dependency, session cleanup task | Phase 1 | `identity/authentication.md` |
| `P2-02` | IdentityAuditEvent and IdentityAuditLog | `P2-01`, `P1-05` | `identity/identity-audit-log.md` |
| `P2-03` | API-key service and self/admin API-key endpoints | `P2-02` | `identity/api-key-service.md`, `identity/authentication.md` |
| `P2-04` | Local login, password hashing, Redis lockout | `P2-01` | `identity/local-authentication.md` |
| `P2-05` | Ticket-independent user lifecycle functions | `P2-02`, `P2-04` | `identity/user-service.md` |
| `P2-06` | CLI infrastructure; manage-user create, set-password, unlock, list, and show | `P2-05` | `platform/cli-infrastructure.md`, `identity/user-management.md` |
| `P2-07` | Public user list/detail, self profile, and completed admin user operations | `P2-05` | `identity/user-management.md`, `identity/rbac.md` |
| `P2-08` | SystemSetting persistence/bootstrap, SettingAuditEvent/Log, settings read and audit APIs | `P2-02` | `platform/system-settings.md` |

`P2-05` includes only functions whose complete specified side effects are
available (for example user creation, field update, reactivation, password
reset, and unlock). Ticket-coupled role removal and deactivation are not
weakened; they complete in Phase 4. The composite `manage-user update` command,
role-management endpoint, and deactivation command/endpoint are deferred in
their entirety rather than exposed with a partial option set. `P2-07` must list
its exact endpoint inventory in its tracking issue before becoming Ready.

The image suite adds a CLI bootstrap assertion in the piece that introduces
the runnable `sentinel` command.

## Phase 3 — Generic Fetcher Platform

**Outcome**: non-domain-specific fetchers can register, schedule, run, report
metrics, and be operated through generic API/CLI surfaces.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P3-01` | FetcherConfig/FetcherRun/FetcherAuditEvent models and migration | Phase 2 | `platform/fetcher-infrastructure.md` |
| `P3-02` | BaseFetcher lifecycle, registry, metrics, sanitization, settings schema | `P3-01`, `P1-02` | `platform/fetcher-infrastructure.md` |
| `P3-03` | CPE package mapping loader, pure resolution, startup validation, and fixtures | `P1-02` | `packages/cpe-package-mapping.md` |
| `P3-04` | Generic task wrapper, config bootstrap, redbeat reconciliation, worker/Beat image smoke | `P3-02`, `P3-03`, `P1-06` | `platform/fetcher-infrastructure.md` |
| `P3-05` | Generic fetcher API operations | `P3-04`, `P2-03` | `platform/fetcher-operations.md` |
| `P3-06` | Fetcher diagnostic CLI | `P3-04`, `P2-06` | `platform/fetcher-operations.md` |
| `P3-07` | Test-only no-op fetcher end-to-end validation | `P3-04`, `P3-05` | `platform/testing-strategy.md` |

`P3-05` excludes the IBS RabbitMQ consumer status endpoint; that endpoint is
implemented with its disabled owning integration in Phase 7+. `P3-07` proves
schedule → run → FetcherRun → operational visibility without adding a
production no-op fetcher.

## Phase 4 — Ticket, CVE, and Package Domain Core

**Outcome**: consumers can create, inspect, and mutate tickets and CVE/package
data through permission-tested APIs before any production fetcher begins
automatic ingestion.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P4-01` | CVE/CVESource core models and migration | Phase 3 | `tickets/cve-service.md`, `data-model.md` |
| `P4-02` | CVE enrichment child models and migration | `P4-01` | CVE and CVSS specs, `data-model.md` |
| `P4-03` | Ticket, TicketAuditEvent, reference/access models and migration | `P4-01`, `P1-05` | ticket specs, `data-model.md` |
| `P4-04` | Pure CVSS resolution | `P2-08`, `P4-02` | `tickets/cvss-scoring.md` |
| `P4-05` | Pure CVE JSON record parser | `P4-02` | `platform/cve-record-parser.md` |
| `P4-06` | CVE existence and source-status primitives | `P4-01` | `tickets/cve-service.md` |
| `P4-07` | Product/package-tree persistence required by gates | `RG-01`, `P4-03` | approved product/package contracts |
| `P4-08` | Ticket audit service and pure gate predicates | `P4-03`, `P4-04`, `P4-07` | `tickets/ticket-audit-log.md`, `tickets/ticket-mutations.md` |
| `P4-09` | Status reconciliation plus CVSS mutation/recalculation chain | `P4-08` | `tickets/ticket-mutations.md`, `tickets/cvss-scoring.md` |
| `P4-10` | CVSS assessment and severity APIs | `P4-09` | `tickets/cvss-scoring.md` |
| `P4-11` | Manual-zone ticket mutations | `P4-09` | `tickets/ticket-mutations.md` |
| `P4-12` | Ticket creation and CVE-association service functions | `P4-06`, `P4-09` | `tickets/ticket-service.md` |
| `P4-13` | Ticket assignment/lifecycle service and APIs | `P4-11`, `P4-12` | ticket service/mutation specs |
| `P4-14` | Ticket confidentiality/access service, APIs, and stale-grant cleanup task | `P4-12` | `tickets/ticket-service.md`, `tickets/tickets.md` |
| `P4-15` | Internal package record creation and state mutation services/APIs | `P4-07`, `P4-09` | package model/service specs |
| `P4-16` | Package exclusion/restore services and APIs | `P4-15` | package model/service specs |
| `P4-17` | Package query services and read APIs | `P4-15` | package model/service specs |
| `P4-18` | Ticket reference service and APIs | `P4-03`, `P4-12` | `tickets/ticket-references.md` |
| `P4-19` | Ticket/CVE list/detail and audit read APIs | `RG-01`, `P4-12`, `P4-17`, `P4-18` | `tickets/tickets.md`, `tickets/cve-tracking.md` |
| `P4-20` | Full package orchestration and package-add API | `RG-01`, `P4-15` | package model/service specs plus `RG-01` resolution |
| `P4-21` | Complete CVE ingestion transaction | `P4-02`, `P4-05`, `P4-09`, `P4-12`, `P4-18`, `P4-20` | `tickets/cve-service.md` |
| `P4-22` | BaseCVEFetcher and on-demand/catch-up orchestration | `P4-21`, `P3-02` | `platform/cve-fetcher-infrastructure.md` |
| `P4-23` | Ticket create/CVE-associate APIs and CVE source/refetch APIs | `P4-19`, `P4-22` | ticket and CVE API specs |
| `P4-24` | Resolve identity role-removal audit contradiction | Phase 3 | Documentation PR for `user-service.md` / `user-management.md` |
| `P4-25` | Shared active-ticket unassignment helper and audit behavior | `P4-09`, `P2-05` | `identity/user-service.md`, `tickets/ticket-audit-log.md` |
| `P4-26` | Ticket-coupled role-removal service, commands, and APIs | `P4-24`, `P4-25` | identity service/management specs |
| `P4-27` | User deactivation/impact service, commands, and APIs | `P4-25` | identity service/management specs |
| `P4-28` | Settings PATCH/recalculation endpoints and CVSS batch task | `P4-09`, `P2-08` | `platform/system-settings.md`, `tickets/cvss-scoring.md` |

The candidate pieces above are intentionally more granular than the old
service-wide PRs. During Phase 4 elaboration, each tracking issue must enumerate
the exact functions/endpoints it owns and preserve complete contracts. Package
pieces blocked by `RG-01` remain Blocked while independent CVE/Ticket pieces
may proceed. Migration and endpoint image assertions remain with the pieces
that introduce them under the testing-strategy Growth Rule.

## Phase 5 — CVE Fetcher Infrastructure and Ingestion

**Outcome**: verified external CVE data flows through the generic fetcher
platform and the complete CVE ingestion service.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P5-01` | BaseGitFetcher, git operations, git-worker runtime and image smoke | `P4-22` | `platform/git-fetcher-infrastructure.md` |
| `P5-02` | NVD fetcher | `P4-22`, `P4-23` | `tickets/cve-sync-nvd.md` |
| `P5-03` | Red Hat fetcher | `P4-22`, `P4-23` | `tickets/cve-sync-redhat.md` |
| `P5-04` | GHSA fetcher | `P4-22`, `P4-23` | `tickets/cve-sync-ghsa.md` |
| `P5-05` | OSV fetcher | `P4-22`, `P4-23` | `tickets/cve-sync-osv.md` |
| `P5-06` | EPSS fetcher | `P4-22`, `P4-23` | `tickets/cve-sync-epss.md` |
| `P5-07` | CISA KEV fetcher | `P4-22`, `P4-23` | `tickets/cve-sync-kev.md` |
| `P5-08` | MITRE fetcher | `P5-01`, `P4-23` | `tickets/cve-sync-mitre.md` |
| `P5-09` | Linux Kernel fetcher | `P5-01`, `P4-23` | `tickets/cve-sync-kernel.md` |
| `P5-10` | CVE source failure retry fetcher | `P5-02` through `P5-09` | `platform/cve-source-failure-retry.md` |

Each external integration piece satisfies the mandatory external-contract
verification requirements of the implementation workflow. One fetcher per PR
keeps upstream contract risk and rollback scope isolated.

## Phase 6 — Domain Operational Completion

**Outcome**: cross-surface operational behavior is validated after real
ingestion, and remaining non-WIP administrative surfaces are completed.

| ID | Piece | Direct blockers | Primary contract |
|---|---|---|---|
| `P6-01` | Real-ingestion CVE → Ticket → package-tree E2E verification | Phase 5 | ingestion and package specs |
| `P6-02` | Fetcher-to-CVE source failure drill-down E2E verification | Phase 5, `P3-05` | fetcher operations and CVE service specs |
| `P6-03` | Full local identity/ticket interaction E2E verification | `P4-26`, `P4-27` | identity and ticket specs |
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

1. product catalog synchronization and lifecycle transitions;
2. remaining SMELT/package orchestration not resolved by `RG-01`;
3. IBS integration, RabbitMQ consumer, submission tracking, and release
   detection;
4. git-based track/product release detection;
5. package bugowner and maintainer workflows;
6. SSO authentication and external identity provisioning.

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
  GitHub Release, and image build/smoke/publish all succeeded). Phase 2
  (`docs/drafts/implementation-plan.md` Phase 2) remains queued for detailed
  sub-issue elaboration; `SG-02` (#90, shared Redis client infrastructure) is
  already open as its blocker for session-liveness and login-lockout pieces.
