# Implementation Plan

**Status**: Living document — updated as each phase/piece is completed.
**Purpose**: Define a progressive, dependency-ordered, incrementally-tested
implementation sequence for the Sentinel backend. Implementation proceeds
one piece at a time within each phase; a piece is not considered done
until it passes the Definition of Done (below) and is verified jointly
(automated tests + manual verification).

This document is a planning aid, not a specification. It does not define
behavior — it sequences the implementation of specs that already exist
under `docs/features/`. When a phase or piece is completed, update its
status in the tracking tables below.

**Prerequisite**: the image-testing prep effort — which established the
mechanism for testing built Docker images (compose file, pytest suite,
CI gate) that this plan's phases extend incrementally — is **complete**
(2026-07-29). The durable convention now lives in
`docs/features/platform/testing-strategy.md` (Image / Container Smoke
Testing). See the [Prep Effort](#prep-effort--image-testing-setup)
section below for the completed deliverables and the phase-by-phase
growth model that subsequent phases follow.

## Contents

- [Guiding Principles](#guiding-principles)
- [Definition of Done](#definition-of-done)
- [WIP Boundary](#wip-boundary)
- [Release Strategy](#release-strategy)
- [Phase Overview](#phase-overview)
- [Prep Effort — Image Testing Setup](#prep-effort--image-testing-setup)
- [Phase 0 — Infrastructure Completion and Validation](#phase-0--infrastructure-completion-and-validation)
- [Phase 1 — Cross-Cutting Platform Foundations](#phase-1--cross-cutting-platform-foundations)
- [Phase 2 — Local Identity and Admin Platform](#phase-2--local-identity-and-admin-platform)
- [Phase 3 — Fetcher Framework](#phase-3--fetcher-framework)
- [Phase 4 — CVE/Ticket Domain Core (Phase-1 Ingestion, No WIP)](#phase-4--cveticket-domain-core-phase-1-ingestion-no-wip)
- [Phase 5 — CVE Fetchers (Real Ingestion)](#phase-5--cve-fetchers-real-ingestion)
- [Phase 6 — Ticket/CVE API and Package Gate Logic (No SMELT)](#phase-6--ticketcve-api-and-package-gate-logic-no-smelt)
- [Phase 7+ — Unblocked Only After WIP Specs Are Completed](#phase-7--unblocked-only-after-wip-specs-are-completed)
- [Open Questions to Revisit](#open-questions-to-revisit)
- [Progress Log](#progress-log)

---

## Guiding Principles

1. **Layered, dependency-ordered implementation.** Infrastructure before
   frameworks, frameworks before features. No piece is started before
   its dependencies (models, services, infrastructure) are implemented
   and verified.
2. **One piece at a time.** Within a phase, implement and fully verify
   one spec/module before starting the next. Do not parallelize domain
   logic across unrelated pieces in the same working session.
3. **Test before proceeding.** Every piece must satisfy the
   [Definition of Done](#definition-of-done) — automated tests plus a
   joint manual verification — before the next piece begins.
4. **No implementation without a specification** (Guardrail 1). Every
   piece implemented in this plan maps to an existing, `enabled` spec
   under `docs/features/`. Where an enabled spec is itself incomplete
   (see [WIP Boundary](#wip-boundary)), the incomplete portion is
   deferred, not implemented against guesswork.
5. **Seam/stub strategy for WIP boundaries.** Where a fully-specified
   piece has an optional or asynchronous dependency on a WIP spec, the
   boundary is implemented as an explicit, self-healing stub (e.g., a
   no-op dispatch) rather than skipped silently or blocked entirely.
   This keeps the system correct and allows the WIP dependency to be
   "plugged in" later without revisiting the completed piece. Every
   stub introduced this way MUST be recorded in the
   [Progress Log](#progress-log) with a pointer to the spec that will
   replace it.
6. **Timeboxing is not a goal.** Phases may take days or weeks. Correct
   sequencing and verification take priority over speed.

## Definition of Done

A "piece" (a spec, a service module, a fetcher, an API surface) is done
only when ALL of the following are satisfied:

1. **Spec re-read.** The owning specification under `docs/features/`
   has been (re-)read in full immediately before implementation
   (Guardrail 1).
2. **Data model first.** If the piece introduces or modifies database
   schema, `docs/data-model.md` is updated *before* the SQLAlchemy
   model is implemented (Guardrail 8), and an Alembic migration is
   created and verified (`alembic upgrade head`, `alembic check` — no
   autogenerate drift).
3. **Implementation + tests.** The code is implemented per
   `docs/conventions.md`, with tests added under `backend/tests/`
   mirroring the `app/` structure, using the correct markers (`unit`,
   `integration`, `e2e`) per `docs/features/platform/testing-strategy.md`.
   Audit trail coverage is included wherever the piece touches a
   mutation covered by an audit trail (Guardrail 6).
4. **Automated verification green.**
   - `cd backend && uv run pytest` — all tests pass, coverage ≥ 85%
   - `cd backend && uv run ruff check . && uv run ruff format --check .`
   - `cd backend && uv run alembic upgrade head && uv run alembic check`
   - `cd backend && uvx bandit -r app/` (for security-sensitive pieces)
5. **Relevant reviewer agents invoked**, per the guardrails triggered by
   the change (non-exhaustive, evaluate per piece):
   `@data-model-reviewer`, `@security-reviewer`, `@test-reviewer`,
   `@ticket-integrity-reviewer`, `@identity-integrity-reviewer`,
   `@fetcher-compliance-reviewer`, `@api-convention-reviewer`,
   `@api-parity-reviewer`, `@docs-reviewer`. Issues rated "Needs
   revision" (or High/Critical severity) are fixed before moving on.
6. **Joint manual verification.** The stack is run locally
   (`./scripts/dev-env.sh up`, API server, Celery worker/Beat as applicable);
   the new behavior is exercised manually (API call, CLI command, or
   fetcher run) and the resulting database state is inspected together.
7. **Image smoke tests extended where applicable.** If the piece
   introduces new container-observable behavior (a new endpoint, a new
   process role, a new startup validation, a new runtime dependency
   such as the `git` binary), the corresponding assertion is added to
   `backend/tests/image/` per the growth model in the
   [Prep Effort](#prep-effort--image-testing-setup) section below (which
   restates the durable Growth Rule from
   `docs/features/platform/testing-strategy.md`, Image / Container Smoke
   Testing), and `scripts/image-smoke.sh` passes locally. If the piece has no
   container-observable effect (e.g., a pure function), this step is
   a no-op — state so explicitly in the Progress Log entry.
8. **Progress log updated.** The [Progress Log](#progress-log) records
   what was completed, the date, and any stub/seam introduced.
9. **No dangling reference to this plan.** Because this document is
   temporary and will be deleted once all phases are complete, no file
   created or modified by this piece may reference
   `docs/drafts/implementation-plan.md` (by path or by a concept that
   lives only here, e.g. phase numbers or the phase→assertion growth
   model). Any durable convention was first migrated to its owning
   permanent document (per the cross-cutting mapping in `AGENTS.md`,
   Guardrail 21) and only that permanent location is referenced. Verify
   with a repository-wide search for the draft path.

Only after all nine conditions are met does the next piece begin.

## WIP Boundary

The following specs are `enabled: false` in `docs/reviews/.tracking.json`
and are **out of scope** until they are completed and reviewed:

| Domain | Specs |
|---|---|
| IBS integration | `ibs-integration`, `ibs-rabbitmq-integration`, `ibs-submission-tracking`, `ibs-track-release-detection`, `ibs-product-release-detection` |
| Git-based release detection | `git-track-release-detection`, `git-product-release-detection` |
| Products | `product-catalog`, `product-lifecycle-transitions` |
| Package ownership | `package-bugowner`, `maintainer` |
| Advanced identity | `sso-authentication`, `identity-provisioning` |

**Additional case — `package-service`** (`enabled: true` but treated as
partially WIP): as of the last review (2026-06-03) it has 2 open
Medium-severity gap findings, and its orchestration function
(`add_package_to_ticket`) hard-depends on SMELT product resolution,
which in turn depends on the disabled `product-catalog` spec. Decision
(confirmed with the user): **split** —
- The gate/mutation portion (`set_track_status`, `set_track_delivery_status`,
  `set_product_eligibility`, `set_product_released_at`, soft-delete/restore
  functions, orphan cleanup invariants, query functions) does **not**
  depend on SMELT or any WIP spec and is implemented in **Phase 6**.
- The orchestration portion (`add_package_to_ticket`, SMELT query,
  bugowner resolution, submission discovery enqueue) and the fix for the
  2 open gaps are deferred to **Phase 7**, after `product-catalog` is
  completed and reviewed.

**Rule for any other enabled spec found to be similarly stale/incomplete
during implementation**: stop, report the finding to the user, and
propose scheduling the affected piece after the spec is revised —
following the same pattern as `package-service`.

## Release Strategy

Sentinel is pre-1.0 (`0.x.y`, currently `0.1.0` per
`.release-please-manifest.json`). Per `docs/conventions.md`
(Versioning, Pre-1.0 Rules), the API is not considered stable and
breaking changes may occur in minor bumps — this is compatible with,
and does not require changing, the plan's phase-by-phase approach.

**Cadence**: one minor release (`0.x.0`) at the end of each deployable
phase (Phases 1 through 6; Phase 0 and Phase 7+ are handled
differently — see below). A patch release (`0.x.y`) MAY be cut between
phases if a fix needs to be validated as a tagged, semver-addressable
image (e.g., to test a hotfix against a specific environment) — this is
the exception, not the rule.

**Mechanism**: release-please (`.github/workflows/release-please.yml`)
already maintains an up-to-date Release PR automatically, driven by
Conventional Commits on `master`. "Cutting a release" at the end of a
phase means: merge the open Release PR. No manual version bumping,
tagging, or changelog editing.

**Phase 0 exception**: the work in Phase 0 (adding dependencies,
validating existing infrastructure) is expected to land as `chore:` /
`build:` / `ci:` commits, which do not trigger a version bump on their
own (per `docs/conventions.md`, Versioning). No release is expected at
the end of Phase 0 — the `latest` image tag (produced on every green CI
run on `master`, per `build-images.yml`) is sufficient at that stage.

**Release checkpoint procedure** (referenced at the end of each
applicable phase below): merge the release-please Release PR → confirm
the resulting `v0.x.0` tag triggers `build-images.yml` → confirm the
semver-tagged image passes the image smoke test gate (see
`docs/features/platform/testing-strategy.md`, Image / Container Smoke
Testing) before it is published.

**Prerequisite status**: the `RELEASE_TOKEN` repository secret
(required for the `v*` tag to trigger `build-images.yml` — the default
`GITHUB_TOKEN` does not trigger downstream workflows on tags it
creates) has been confirmed present.

**1.0.0 graduation**: unaffected by this plan — the criteria in
`docs/conventions.md` (1.0.0 Graduation Criteria) require a production
deployment and are evaluated separately, likely well after Phase 7+
work begins.

## Phase Overview

| Phase | Focus | Status |
|---|---|---|
| Prep | Image testing setup (mechanism + minimal assertion) | Completed (2026-07-29) |
| 0 | Infrastructure completion and validation | Not started |
| 1 | Cross-cutting platform foundations | Not started |
| 2 | Local identity and admin platform | Not started |
| 3 | Fetcher framework | Not started |
| 4 | CVE/Ticket domain core (Phase-1 ingestion, no WIP) | Not started |
| 5 | CVE fetchers (real ingestion) | Not started |
| 6 | Ticket/CVE API and package gate logic (no SMELT) | Not started |
| 7+ | Unblocked only after WIP specs are completed | Blocked (pending spec work) |

---

## Prep Effort — Image Testing Setup

**Status: Completed (2026-07-29).** This prep effort established the
mechanism for testing built Docker images as a black-box artifact. It
was executed once, before Phase 0. The durable convention is documented
in `docs/features/platform/testing-strategy.md` (Image / Container Smoke
Testing) — the authoritative home for this cross-cutting testing
convention.

Delivered artifacts:

- `docker-compose.smoke.yml` — self-contained full-stack (own
  `postgres`/`redis` with no host ports; `api` published on
  `IMAGE_SMOKE_PORT`, default 18000). All five application services are
  represented; `api` and `migrate` are active, `worker`/`beat`/
  `git-worker` are commented out and uncommented by the phase that
  introduces them.
- `backend/tests/image/` — black-box pytest suite (marker `image`,
  excluded from the default run and from coverage).
- `scripts/image-smoke.sh` — single runtime-agnostic runner
  (build → `up --wait` → `pytest -m image` → teardown), used identically
  locally and in CI.
- `.github/workflows/build-images.yml` — blocking CI gate
  (build once → load → smoke test → push the same image digest).
- `docs/features/platform/testing-strategy.md` — new "Image / Container
  Smoke Testing" section (durable convention + Growth Rule).

### Image Smoke Test Growth Model

The suite started with a single minimal assertion and grows alongside
this plan. Each phase that introduces new container-observable behavior
uncomments its compose service **and** adds the corresponding smoke
assertion together, as part of that phase's Definition of Done (item 7).
This mapping is indicative — the owning phase decides the exact
assertions when it is implemented:

| Phase | New assertion(s) added to `backend/tests/image/` |
|---|---|
| Prep (done) | `test_image_build.py`: image builds, `api` container starts, no crash |
| Phase 1 | `test_api_image.py`: `GET /health` and `GET /ready` return 200 |
| Phase 2 | `test_cli_image.py`: a `sentinel manage-user ...` command runs inside the container and exits 0 |
| Phase 3 | `test_worker_image.py`: `worker` and `beat` containers start and stay up; log lines confirm UTC/redbeat validation passed |
| Phase 4 | `test_migrations_image.py`: `migrate` one-shot service runs `alembic upgrade head` against the real schema and exits 0 |
| Phase 5 | `test_git_worker_image.py`: `git-worker` container has the `git` binary available and can clone a throwaway repository |

---

## Phase 0 — Infrastructure Completion and Validation

**Goal**: a solid, green, fully verified infrastructure baseline. No
domain logic in this phase.

**Current state** (as of last audit): Docker Compose (PostgreSQL 16 +
Redis 7), `backend/Dockerfile` (multi-stage, non-root, SUSE CA), CI
(`ci.yml`: lint + test + security scan, Python-version drift check),
`release-please` + `build-images` + `deploy-api-docs` workflows,
`app/config.py` (Settings), `app/database.py` (async engine/session),
`app/main.py` (FastAPI + CORS), `conftest.py` (async session with
savepoint rollback, e2e client fixture) are already in place.

**Gaps to close**:

1. Add missing dependencies to `backend/pyproject.toml` (and update
   `uv.lock`): `structlog` (required by `logging.md`), `cvss` (CVSS
   vector parsing, required by `cvss-scoring.md`), `celery-redbeat`
   (Beat scheduler, required by `fetcher-infrastructure.md`).
2. Validate the existing infrastructure end-to-end:
   - `./scripts/dev-env.sh up` → PostgreSQL + Redis healthy
   - `cd backend && uv sync` → environment resolves cleanly
   - `uv run pytest` → baseline green (including the `xfail` on `/health`)
   - `uv run ruff check . && uv run ruff format --check .` → clean
   - Build `backend/Dockerfile` → succeeds; Python-version drift check passes
   - `uv run alembic upgrade head` (no-op, no migrations yet) → Alembic
     environment works
3. Report status and any issues found before writing any domain logic.

**Definition of Done for this phase**: all checks above pass; no domain
code has been written.

---

## Phase 1 — Cross-Cutting Platform Foundations

**Goal**: leaf infrastructure with zero WIP dependencies, needed by
every subsequent phase.

**Order**:

1. `docs/features/platform/logging.md` — structlog → stdlib pipeline,
   correlation ID (`X-Request-ID`) middleware, Celery task binders.
2. `docs/features/platform/networking.md` — shared HTTP client factory,
   TLS trust store, retry/backoff classification helpers.
3. `docs/features/platform/health-endpoints.md` — `GET /health`,
   `GET /ready`. Removes the `xfail` marker on `test_health.py`.
4. Core cross-cutting modules: `app/core/enums.py`, `app/core/errors.py`,
   `app/core/identifiers.py` (CVE-ID pattern, etc.) — introduced as
   needed by the specs above and by
   `docs/conventions.md` (Enum Storage Strategy).
5. `docs/features/platform/audit-trail-infrastructure.md` —
   `AuditEventMixin` (`app/models/mixins.py`) and `BaseAuditLog`
   (`app/services/base_audit_log.py`). Requires the `User` model to
   exist as an FK target for the mixin — the minimal `User`/`UserRole`
   tables are introduced here if not already present from Phase 2
   planning (coordinate with Phase 2's first piece).
6. Celery app factory (`app/celery_app.py`) — UTC/`enable_utc`
   validation at import time, `task_ignore_result = True`, `redbeat`
   scheduler configuration, lock sentinel. No fetchers registered yet.

**WIP dependency**: none.

**Release checkpoint**: see [Release Strategy](#release-strategy)
(`0.2.0` — first release with observable behavior: `/health`, `/ready`).

---

## Phase 2 — Local Identity and Admin Platform

**Goal**: full local-user identity stack (login, sessions, API keys,
RBAC, audit, CLI bootstrap), with the `external_id` branch of every
module built as a guarded, dormant seam (no SSO, no external
provisioning yet).

**Order** (topological, per dependency analysis):

1. `User` / `UserRole` models (data model authoritative in
   `docs/data-model.md`; described in `rbac.md`).
2. `docs/features/identity/authentication.md` (core) — `Session` and
   `ApiKey` models, `session_service`, JWT issuance, `get_current_user`.
3. `docs/features/identity/rbac.md` (core) — `Capability`/`Role`/`Scope`
   enums, `require_capability()`. External-provisioning-only endpoints
   (role-mapping) left as documented-but-unimplemented seams.
4. `docs/features/identity/identity-audit-log.md` — `IdentityAuditEvent`
   model + `IdentityAuditLog` (`BaseAuditLog` subclass). All 14 event
   types buildable now; externally-sourced event values simply remain
   unemitted until Phase 7+.
5. `docs/features/identity/api-key-service.md` — `create_key`,
   `revoke_key`, `revoke_all_user_keys`.
6. `docs/features/identity/local-authentication.md` — login endpoint,
   bcrypt hashing, Redis-backed lockout.
7. `docs/features/identity/user-service.md` — local operations
   (`create_user`, `update_user`, `update_roles`, `deactivate_user`,
   `reactivate_user`, `reset_password`, `unlock_user`).
   `sync_role_mapping()` / `delete_role_mapping_roles()` implemented as
   dormant (no callers until Phase 7+ per the spec itself).
8. `docs/features/platform/cli-infrastructure.md` — Click root group,
   exit-code mapping, signal handling, one-`asyncio.run()` DB bridge.
9. `docs/features/identity/user-management.md` — `manage-user` CLI +
   admin/public user API endpoints (local-user operations only).
10. `docs/features/platform/system-settings.md` — `SystemSetting`,
    `SettingAuditEvent`, settings service (`default_cvss_version`),
    admin endpoints.

**Unlocks**: admin bootstrap (`sentinel manage-user create --role admin`),
login, API keys, `require_capability()` for every future endpoint,
`default_cvss_version` resolution for CVSS scoring (Phase 4).

**WIP kept out**: SSO endpoints, external provisioning sync, and the
`RoleMapping` CRUD API are not implemented — only guarded seams are
left where the spec explicitly anticipates them.

**Release checkpoint**: see [Release Strategy](#release-strategy).

---

## Phase 3 — Fetcher Framework

**Goal**: the generic background-task infrastructure that all data
fetchers (CVE and, later, product/IBS) build on.

**Order**:

1. `docs/features/platform/fetcher-infrastructure.md` — `BaseFetcher`,
   `FetcherConfig`/`FetcherRun`/`FetcherAuditEvent` models, registry,
   `run()` lifecycle, error-message sanitization, custom settings
   schema, Beat schedule reconciliation, `bootstrap_fetcher_configs()`.
2. `docs/features/platform/cve-fetcher-infrastructure.md` —
   `BaseCVEFetcher` (CVE source type identity, `fetch_single()`,
   default `catch_up()`).
3. `docs/features/platform/git-fetcher-infrastructure.md` —
   `BaseGitFetcher`, `git_operations.py` (requires git worker + volume
   — validate locally with a throwaway repo before wiring real
   fetchers).
4. `docs/features/platform/fetcher-operations.md` — monitoring
   dashboard (API endpoints + CLI diagnostics).

**Validation**: implement one throwaway/no-op test fetcher end-to-end
(Beat schedule → `run()` → `FetcherRun` row → dashboard visibility)
before moving to Phase 4.

**WIP dependency**: none.

**Release checkpoint**: see [Release Strategy](#release-strategy).

---

## Phase 4 — CVE/Ticket Domain Core (Phase-1 Ingestion, No WIP)

**Goal**: "CVE fetched → stored → severity computed → ticket
auto-created" working end-to-end, with the package-resolution
sub-flow (SMELT-dependent) stubbed as a self-healing no-op.

**Models**: `CVE` + child tables (`CVESource`, `CVECVSSAssessment`,
`CVEExternalIdentifier`, `CVEAffectedVersion`, `CVECWE`,
`CVESSVCAssessment`, `CVEKEVEntry`, `CVEEPSSScore`); `Ticket`,
`TicketAuditEvent`, `TicketReference`; `TicketPackage`,
`TicketPackageTrack`, `TicketPackageProduct` (created but populated
later); `Product` (created empty — sole FK target, no sync logic yet).

**Order**:

1. `docs/features/tickets/cvss-scoring.md` (pure resolution functions
   in `app/services/cvss.py`: `resolve_severity_score`,
   `resolve_eligibility_score`, vector validation).
2. `docs/features/platform/cve-record-parser.md` (pure CVE JSON 5.x
   parsing, consumed by MITRE/kernel fetchers in Phase 5).
3. `docs/features/tickets/ticket-audit-log.md` (`TicketAuditEvent`
   model + event type contract).
4. `docs/features/tickets/ticket-mutations.md` — subset needed for
   ingestion: `upsert_cvss_assessment()`, `recalculate_cvss_chain()`,
   `reconcile_ticket_status()`, `auto_assign_actor()`,
   `ensure_ticket_operable()`. (Full mutation surface — package gates,
   duplicate handling — completes in Phase 6.)
5. `docs/features/tickets/ticket-service.md` — `create_ticket()`.
6. `docs/features/tickets/ticket-references.md` — `reference_service`
   (automatic reference creation from fetchers).
7. `docs/features/tickets/cve-service.md` — `upsert_cve()`,
   `ensure_cve_exists()`. **Phase 1** (synchronous: CVE + ticket +
   severity, same transaction) fully implemented.
   **Phase 2** (`resolve_ticket_packages` — CPE/package resolution via
   SMELT) is stubbed: `commit_and_dispatch()` is implemented, but the
   dispatched task is a documented no-op until Phase 7. Record this
   stub explicitly in the [Progress Log](#progress-log).

**Boundary respected**: `reconcile_ticket_status()` operates correctly
with an empty package set (gate conditions evaluate against zero
tracks/products; tickets remain in `New`/`Analysis` as expected — no
special-casing needed, this is the natural behavior of the gate logic).

**WIP dependency**: none (the empty `Product` table is a schema-only
touchpoint with `product-catalog`, not a logic dependency).

**Release checkpoint**: see [Release Strategy](#release-strategy)
(first release where `migrate` one-shot image smoke test applies).

---

## Phase 5 — CVE Fetchers (Real Ingestion)

**Goal**: real CVE data flowing into the system via the fetcher
framework (Phase 3) and the ingestion core (Phase 4).

**Order** (REST fetchers first, git fetchers require worker + volume):

1. `docs/features/tickets/cve-sync-nvd.md`
2. `docs/features/tickets/cve-sync-redhat.md`
3. `docs/features/tickets/cve-sync-ghsa.md`
4. `docs/features/tickets/cve-sync-osv.md`
5. `docs/features/tickets/cve-sync-epss.md`
6. `docs/features/tickets/cve-sync-kev.md`
7. `docs/features/tickets/cve-sync-mitre.md` (git-based — validate git
   worker + persistent volume setup first)
8. `docs/features/tickets/cve-sync-kernel.md` (git-based, shares infra
   with MITRE)
9. `docs/features/platform/cve-source-failure-retry.md`

**Per-fetcher verification**: run the fetcher against the real external
source (or a recorded fixture), inspect the resulting `CVE`/`Ticket`/
`CVESource` rows, confirm severity computation and audit events.
Consider `@external-contract-verifier` for at least one REST and one
git-based fetcher to confirm payload assumptions against the live
service.

**WIP dependency**: none for ingestion. Package resolution remains
stubbed per Phase 4.

**Release checkpoint**: see [Release Strategy](#release-strategy)
(first release where the `git-worker` image smoke test applies, once
the MITRE/kernel fetchers land).

---

## Phase 6 — Ticket/CVE API and Package Gate Logic (No SMELT)

**Goal**: a usable API surface for vulnerability analysts, plus the
package-domain gate/mutation logic that does not require SMELT.

**Order**:

1. `docs/features/tickets/tickets.md` — full ticket API (list, detail,
   ignore/reopen, assign, mark/revert duplicate, associate-cve,
   severity, confidentiality, audit-log endpoint).
2. `docs/features/tickets/ticket-mutations.md` — complete the mutation
   surface not covered in Phase 4 (duplicate handling, confidentiality
   grants, remaining gate logic).
3. `docs/features/packages/package-model.md` — track/product concepts,
   hierarchical exclusion model, gate contribution, API endpoints for
   read/manual operations.
4. `docs/features/packages/package-service.md` — **gate/mutation
   portion only**: `set_track_status`, `set_track_delivery_status`,
   `set_product_eligibility`, `set_product_released_at`,
   soft-delete/restore functions, orphan cleanup invariants, query
   functions (`get_ticket_packages`, `search_packages`).
   `add_package_to_ticket` (SMELT orchestration) explicitly excluded —
   deferred to Phase 7.
5. `docs/features/packages/cpe-package-mapping.md` — pure lookup
   module built and unit-tested now (no live consumer until Phase 7
   re-enables Phase 2 CVE ingestion dispatch).

**Known limitation at the end of this phase**: packages cannot yet be
*added* to a ticket through the normal orchestration flow (no SMELT).
Existing tickets have empty package trees except where test data is
seeded directly. This is expected and resolved in Phase 7.

**WIP dependency**: none (by construction — SMELT-dependent pieces
excluded).

**Release checkpoint**: see [Release Strategy](#release-strategy).

---

## Phase 7+ — Unblocked Only After WIP Specs Are Completed

Each item below requires: (a) the owning spec completed to the
"insufficiency test" standard, (b) `@spec-gap-analyzer` and
`@spec-coherence-reviewer` passes, (c) then implementation following
the same Definition of Done as every other phase.

Proposed order (subject to revision once the specs are actually
completed — dependencies may shift):

1. `docs/features/packages/product-catalog.md` — complete the "TBD"
   fetcher algorithms (`sync_smelt_products`, `sync_aimaas_lifecycle`,
   `sync_aimaas_thresholds`), then implement.
2. `docs/features/packages/package-service.md` — remaining portion:
   `add_package_to_ticket()` (SMELT orchestration), fix for the 2 open
   Medium-severity gap findings from the 2026-06-03 review.
3. Un-stub Phase 4's `cve-service.md` Phase-2 dispatch
   (`resolve_ticket_packages`) now that SMELT resolution is real.
4. IBS domain: `docs/features/integrations/ibs-integration.md`,
   `docs/features/integrations/ibs-rabbitmq-integration.md`,
   `docs/features/packages/ibs-submission-tracking.md`,
   `docs/features/packages/ibs-track-release-detection.md`,
   `docs/features/packages/ibs-product-release-detection.md`.
5. Git-based release detection: `docs/features/packages/git-track-release-detection.md`,
   `docs/features/packages/git-product-release-detection.md`.
6. `docs/features/packages/product-lifecycle-transitions.md`,
   `docs/features/packages/package-bugowner.md`,
   `docs/features/packages/maintainer.md`.
7. Advanced identity: `docs/features/identity/sso-authentication.md`,
   `docs/features/identity/identity-provisioning.md` (activates the
   dormant seams left in Phase 2: `sync_role_mapping`, external-user
   guards, `RoleMapping` CRUD API).

---

## Open Questions to Revisit

- Exact sequencing within Phase 7+ may change once the WIP specs are
  actually revised — this section is a placeholder, not a commitment.
- Whether a dedicated OP (open point) should be filed for "periodic
  ticket status reconciliation as drift detection" (OP-6 in
  `docs/drafts/open-points.md`) as part of Phase 6 or deferred further.
- Confirm whether `RoleMapping` table should be created (empty, schema
  only) during Phase 2 as an FK-target/forward-compat measure, similar
  to the `Product` table treatment in Phase 4, or deferred entirely to
  Phase 7. Not yet decided — evaluate when Phase 2 starts.

**Resolved**:

- ~~Whether the `RELEASE_TOKEN` repository secret is configured~~ —
  confirmed present (verified manually, see Release Strategy).
- ~~Whether/how to test built Docker images~~ — addressed by the
  image-testing prep effort (completed 2026-07-29, see the
  [Prep Effort](#prep-effort--image-testing-setup) section); the durable
  convention lives in `docs/features/platform/testing-strategy.md`
  (Image / Container Smoke Testing).

## Progress Log

Record completed pieces here, in chronological order, including any
stub/seam introduced and the spec that will eventually replace it.

- **2026-07-29 — Prep Effort (Image Testing Setup) completed.**
  Delivered `docker-compose.smoke.yml`, `backend/tests/image/`
  (marker `image`, excluded from default run and coverage),
  `scripts/image-smoke.sh`, and the blocking build → smoke → push gate
  in `.github/workflows/build-images.yml`. The durable convention was
  added to `docs/features/platform/testing-strategy.md` (Image /
  Container Smoke Testing). No stubs/seams introduced. See the
  [Prep Effort](#prep-effort--image-testing-setup) section for the
  phase-by-phase growth model that subsequent phases follow.
