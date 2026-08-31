# Sentinel

## Overview

Sentinel is a platform for managing and tracking security updates for SUSE and
openSUSE-based Linux distributions. It handles CVE ingestion from multiple
sources, determines impact on maintained distributions, and coordinates the
preparation and release of security patches.

**Stack**: FastAPI (Python) + PostgreSQL + Celery + Redis

This repository contains backend implementation and all product specifications.
The frontend will be developed in a dedicated repository.

## Architecture

- **Backend**: FastAPI application in `backend/app/`
- **Database**: PostgreSQL with Alembic migrations
- **Task Queue**: Celery with Redis broker
- **Build System Integration**: Open Build Service (OBS)
- **CVE Sources**: NVD (NIST), MITRE, and others

For full architecture details, read `docs/architecture.md`.

## Project Structure

```
sentinel/
├── AGENTS.md                    # Project instructions for OpenCode
├── opencode.json                # OpenCode configuration
├── docker-compose.yml           # Local dev-infra stack (PostgreSQL + Redis)
├── docker-compose.smoke.yml     # Self-contained stack for image smoke tests
├── scripts/                     # Repo-level orchestration scripts
│   ├── dev-env.sh               # Local dev stack launcher (Podman/Docker)
│   └── image-smoke.sh           # Black-box image smoke-test runner
├── docs/                        # Specifications and documentation
│   ├── architecture.md          # System architecture
│   ├── api-spec.md              # API specifications
│   ├── cli-reference.md         # CLI commands reference
│   ├── configuration.md         # Configuration reference (env vars index)
│   ├── conventions.md           # Code conventions
│   ├── data-model.md            # Database schema and relationships
│   ├── data-sources.md          # External data sources catalog
│   ├── deployment.md            # Deployment guide
│   ├── system-map.md            # System map
│   ├── drafts/                  # WIP feature specs (not yet approved)
│   ├── features/                # Approved feature specifications
│   │   ├── identity/            # Identity & access management
│   │   ├── integrations/        # External system integrations
│   │   ├── packages/            # Package tracking
│   │   ├── platform/            # Platform infrastructure
│   │   └── tickets/             # Ticket management
│   └── reviews/                 # Review findings
├── .opencode/                   # OpenCode agents, skills, commands
│   ├── agents/                  # Subagent definitions
│   ├── commands/                # Custom slash commands
│   └── skills/                  # Skill workflows
├── .githooks/                   # Local git hooks (pre-commit, pre-push)
├── backend/                     # FastAPI backend
│   ├── Dockerfile               # Backend container image
│   ├── pyproject.toml           # Python dependencies and config
│   ├── alembic.ini              # Alembic configuration
│   ├── alembic/                 # Database migrations
│   ├── scripts/                 # Backend-specific utility scripts (import app)
│   ├── app/
│   │   ├── main.py              # FastAPI application entry point
│   │   ├── config.py            # Application configuration
│   │   ├── database.py          # Database session setup
│   │   ├── models/              # SQLAlchemy models
│   │   ├── schemas/             # Pydantic schemas (request/response)
│   │   ├── api/v1/              # API endpoints
│   │   ├── services/            # Business logic
│   │   ├── tasks/               # Celery background tasks
│   │   └── core/                # Auth, permissions, utilities
│   └── tests/                   # Backend tests
└── .github/workflows/           # CI/CD pipelines
```

## Commands

- **Install/sync dependencies**: `cd backend && uv sync` (installs Python 3.14 and creates `.venv` automatically if needed)
- **Backend tests**: `cd backend && uv run pytest`
- **Backend lint**: `cd backend && uv run ruff check . && uv run ruff format --check .`
- **DB migrations**: `cd backend && uv run alembic upgrade head`
- **New migration**: `cd backend && uv run alembic revision --autogenerate -m "description"`
- **Local dev stack**: `./scripts/dev-env.sh up` (PostgreSQL + Redis, auto-detects Podman or Docker)
- **Image smoke test**: `./scripts/image-smoke.sh` (builds the image, runs black-box container tests; see `docs/features/platform/testing-strategy.md`, Image / Container Smoke Testing)

## Local Environment

### OBS/IBS CLI (`osc`)

On this machine, the `osc` command-line tool (used to interact with OBS/IBS)
MUST be invoked through `secbox`:

- **Always use**: `secbox osc <subcommand>` (NEVER bare `osc <subcommand>`)
- **Never pass credentials**: authentication is handled automatically by
  `secbox`. Do not pass `--user`, `--pass`, or attempt to configure
  `~/.oscrc`
- **API URL (`-A`) MUST be `https://api.suse.de`**: `secbox` only has
  credentials provisioned for the `api.suse.de` host. Passing
  `-A https://build.suse.de` (the web/browsing host) fails with
  "No user configured for apiurl ..." for every subcommand, including
  read-only ones (`ls`, `api`). `build.suse.de` and `api.suse.de` are
  the same OBS/IBS instance exposed on two hostnames — always use
  `api.suse.de` with `secbox osc`, even when following a
  `build.suse.de` URL found in a ticket or IBS request link

Examples:
- `secbox osc -A https://api.suse.de ls SUSE:SLE-15-SP6:Update`
- `secbox osc -A https://api.suse.de api /source/SUSE:SLE-15-SP6:Update/kernel-default`

### GitLab CLI (`glab`)

On this machine, the `glab` command-line tool is configured and authenticated
against `gitlab.suse.de`. When you need to inspect issues, merge requests, or
repositories hosted on SUSE's internal GitLab instance (e.g., SMELT at
`tools/smelt`, SMASH at `tools/smash`):

- **Read-only usage only**: use `glab` exclusively for viewing and listing
  issues, MRs, or repositories
- **Target project**: always specify the repository explicitly using
  `-R gitlab.suse.de/<group>/<project>` (or `<group>/<project>` when the
  default host is set)
- **View issues with comments**:
  `glab issue view <id> -R gitlab.suse.de/<group>/<project> --comments`
- **View merge requests**:
  `glab mr view <id> -R gitlab.suse.de/<group>/<project>`
- **View file or repository info**:
  `glab repo view gitlab.suse.de/<group>/<project>`

## Workspace Awareness

When checking for existing files or directories, ALWAYS inspect the
actual filesystem (using `ls`, `Read`, or `Glob` tools) rather than
relying solely on git-tracked files. Files and directories listed in
`.gitignore` are not versioned but may contain important local work
products (e.g., `docs/reviews/.tracking.json`, build
artifacts, local configuration). Never assume a file does not exist
just because it is untracked by git. When overwriting a file, first
check its current content on disk to avoid losing existing data.

## External File Loading

CRITICAL: When you encounter a reference to a specification file (e.g.,
`docs/features/tickets/cve-tracking.md`), use your Read tool to load it. Treat the
content as mandatory instructions that override defaults. Load specifications
on a need-to-know basis — do NOT preemptively load all references.

## Internal Network Access

When encountering links to SUSE internal services (e.g., `build.suse.de`,
`smelt.suse.de`, `aimaas.suse.de`, `rabbit.suse.de`, or any `*.suse.de` /
`*.suse.com` host), ALWAYS attempt to fetch or access them. The machine
running OpenCode may have direct access to the SUSE internal network. Never
skip a request solely because the URL appears to be on an internal network.

---

## Guardrails - Mandatory Rules

### 1. Specs-first: NEVER implement without a specification

CRITICAL: Before writing or modifying ANY implementation code (in `backend/`), you MUST:

1. Verify that a specification exists in `docs/features/` for the feature
   involved
2. Read the specification with the Read tool
3. If the specification does not exist or does not cover the requested change,
   STOP and notify the user: "There is no specification for this feature. Would
   you like me to create one before proceeding with the implementation?"
4. If the user asks to implement directly without a specification, remind them
   that the project workflow requires specs-first and propose writing the
   specification together

Do NOT make exceptions to this rule, not even for small changes.

A specification gap exists when implementation would require inventing
product behavior, guarantees, contract semantics, security or data-integrity
requirements, or a technical decision that would establish or change an
architectural boundary. Choosing among internal technical mechanisms that all
satisfy the specification, established architecture, and conventions is
normal implementation work and is not by itself a specification gap. See
`docs/conventions.md` (Function Specification Completeness).

### 2. Correct file placement

CRITICAL: Before writing or modifying ANY file in the repository, verify that
the location is correct according to this map:

| Content Type               | Location                          |
|----------------------------|-----------------------------------|
| Feature specifications     | `docs/features/<domain>/<feature-name>.md` |
| General architecture       | `docs/architecture.md`            |
| API specifications         | `docs/api-spec.md`                |
| CLI reference              | `docs/cli-reference.md`           |
| Configuration reference    | `docs/configuration.md`           |
| Data schema                | `docs/data-model.md`              |
| External data sources      | `docs/data-sources.md`            |
| Deployment guide           | `docs/deployment.md`              |
| Code conventions           | `docs/conventions.md`             |
| SQLAlchemy models          | `backend/app/models/`             |
| Pydantic schemas           | `backend/app/schemas/`            |
| API endpoints              | `backend/app/api/v1/`             |
| Business logic             | `backend/app/services/`           |
| Background tasks           | `backend/app/tasks/`              |
| Auth and permissions       | `backend/app/core/`               |
| DB migrations              | `backend/alembic/versions/`       |
| Backend utility scripts (import `app`) | `backend/scripts/`    |
| Repo-level orchestration scripts (compose, dev/CI tooling) | `scripts/` |
| TLS certificates           | `backend/certs/`                  |
| Backend tests              | `backend/tests/`                  |
| CLI commands               | `backend/app/cli/`                |
| Draft documents            | `docs/drafts/`                    |
| Review findings            | `docs/reviews/`                   |

If the user asks to create a file in a location that does not match this map,
STOP and notify: "This file should go in [correct location] according to the
project conventions. Should I proceed with the correct location?"

### 3. Coherent spec-code updates

When modifying code that changes the behavior of a feature, verify whether the
corresponding specification needs to be updated. If it does, resolve the
specification update before the code change is considered complete. The default
is a separate documentation PR merged first (per Guardrail 25, Spec-first
sequencing). When the combined-PR exception in Guardrail 25 applies, the spec
and code changes may ship in the same PR — the spec delta must still be
authored before or concurrently with the code, never retrofitted after the
implementation is merged.

After significant documentation or code changes, evaluate whether a docs review
is needed — see Guardrail 9 for details.

### 4. Language: English only

CRITICAL: ALL content written to files in this repository MUST be in English,
regardless of the language used in the conversation. This includes:

- Documentation (docs/, AGENTS.md, README, etc.)
- Code comments and docstrings
- Commit messages
- Variable names, function names, class names
- Log messages and error messages
- API response messages
- Test descriptions

If the user communicates in a non-English language, respond in their language
but ALWAYS write file content in English. If you are about to write non-English
content to a file, STOP and rewrite it in English before proceeding.

### 5. CI/CD awareness

When modifying backend dependencies, build configuration, or Docker
setup, verify that the CI pipeline (`.github/workflows/`) does not need
corresponding updates. If it does, update the workflows in the same PR.

When modifying release-related configuration
(`release-please-config.json`, `.release-please-manifest.json`), verify
that the release-please workflow
(`.github/workflows/release-please.yml`) and the downstream
`build-images.yml` pipeline are not affected.

The authoritative source for CI/CD conventions — the workflow inventory,
action pinning, secret handling, service containers, and container build
rules — is `docs/deployment.md` (CI Pipeline).

After adding or modifying any CI/CD artifact, invoke `@cicd-reviewer`:

- `.github/workflows/**`
- `backend/Dockerfile`, `.dockerignore`
- `docker-compose*.yml`
- `.githooks/**`
- `scripts/**` when consumed by a workflow
- `release-please-config.json`, `.release-please-manifest.json`

Skip the review when the change is purely cosmetic (comment or
formatting changes with no effect on triggers, gates, permissions, or
build behavior).

### 6. Mandatory testing

CRITICAL: Every code change (new feature or modification) MUST include tests.

Before considering any implementation task complete:

1. Write tests that cover the new/modified functionality
   - Backend: pytest tests in `backend/tests/` mirroring the `app/` structure
   - Apply markers: `@pytest.mark.unit`, `@pytest.mark.integration`, or
     `@pytest.mark.e2e` per `docs/features/platform/testing-strategy.md`
2. Run the test suite and verify all tests pass
   - Backend: `cd backend && uv run pytest`
3. If tests fail, fix the code or tests until they pass
4. After all tests pass, evaluate whether a test quality review is needed:
   - New feature or new module: invoke `@test-reviewer`
   - Bug fix with regression test: invoke `@test-reviewer`
   - Minor refactor or small change to existing tested code: skip review
   - When in doubt, invoke `@test-reviewer`
5. Only THEN inform the user that the task is complete

Test requirements:

- New API endpoints: test happy path, validation errors, auth/permissions
- New models: test creation, constraints, relationships
- New services: test business logic, edge cases, error handling
- Bug fixes: add a regression test that reproduces the bug
- **Audit trail coverage**: for every mutation covered by any audit trail
  registered in the Audit Trail Index
  (`docs/features/platform/audit-trail-infrastructure.md`), tests MUST
  verify that the corresponding audit event is created with correct
  field values in the same transaction. See
  `docs/features/platform/testing-strategy.md` (Audit Trail Testing)
  for the full assertion checklist

NEVER skip tests. If the user asks to skip tests, remind them that the project
requires tests for all changes and suggest writing them.

### 7. [Reserved — UI consistency]

This guardrail will be reinstated when a frontend implementation exists.
The UI will be developed in a dedicated repository.

### 8. Data model simplicity

CRITICAL: Before considering any data model change complete (new or modified
SQLAlchemy models, new Alembic migrations, or changes to `docs/data-model.md`):

1. Ensure `docs/data-model.md` is updated BEFORE implementing model changes
2. After implementation, invoke `@data-model-reviewer` to verify:
   - The schema remains as simple as possible
   - No unnecessary tables, columns, or relationships were introduced
   - Naming conventions and structural conventions are followed
   - The implementation matches the specification
3. Minor issues flagged by the reviewer should be fixed in the same PR

The goal is to keep the database schema lean and comprehensible. Every table
and column must justify its existence.

### 9. Documentation completeness

After any significant code or documentation change, evaluate whether a docs
review is needed:

1. Invoke `@docs-reviewer` when:
   - New API endpoints are added or existing ones are modified
   - New feature specifications are created or existing ones are updated
   - Models or services are added/changed in ways that affect documented
     behavior
   - Architecture or integration changes are made
   - Multiple documentation files are modified in the same PR
2. Skip the review when:
   - The change is purely cosmetic (typo fixes, formatting)
   - Only test files are modified with no behavioral changes
   - A single inline comment or docstring is added
3. Minor issues flagged by the reviewer should be fixed in the same PR

The goal is to keep documentation accurate, complete, and in sync with the
codebase at all times.

### 10. Code security

After any code change that touches security-sensitive areas, evaluate whether
a security review is needed:

1. Invoke `@security-reviewer` when:
   - New API endpoints are added or existing ones are modified
   - Authentication or authorization logic is added or changed
     (`app/core/`, `Depends()` for auth/permissions)
   - Secret keys, tokens, credentials, or session handling are involved
   - Input handling changes (new request schemas, file uploads, query
     parameters with user-controlled data)
   - New external service integrations are added (API calls, webhooks)
   - New dependencies are added that process user input (parsers,
     serializers, template engines)
   - CORS, CSP, or other HTTP security headers are modified
2. Skip the review when:
   - The change is purely cosmetic (typo fixes, formatting, comments)
   - Only test files are modified
   - Only documentation is updated
3. Confirmed security findings rated Critical or High severity are not
   subject to the discard criteria in Guardrail 26 — they MUST be fixed
   before merging

The goal is to prevent security vulnerabilities from being introduced into
the codebase. The `@security-reviewer` agent complements the automated
security scanning in the CI pipeline (`bandit`, `pip-audit`).

### 11. Audit trail atomicity

CRITICAL: Every service operation that modifies data covered by an audit
trail registered in `BaseAuditLog` MUST create the corresponding audit
event record in the same database transaction. The absence of an audit
event for a covered mutation is a bug.

Audit events are historical evidence, not the authoritative source of current
operational state. They MUST NOT be used to determine current state or as
input to mutation, authorization, idempotency, or restoration decisions. See
`docs/features/platform/audit-trail-infrastructure.md` (Operational State
Authority) for the complete rule and permitted historical read-model uses.

See `docs/features/platform/audit-trail-infrastructure.md` for the
Audit Trail Index (which audit trails exist and what they cover),
naming conventions, and the `BaseAuditLog` / `AuditEventMixin`
contracts.

#### Ticket audit trail

Every service operation that modifies a Ticket or its related data
(status, assignee, duplicate links, packages, codestreams, products)
MUST create a `TicketAuditEvent` record with the appropriate
`event_type`. An operation that mutates a ticket without creating a
corresponding `TicketAuditEvent` is a bug.

If the change introduces a new type of ticket mutation not covered by
an existing `TicketAuditEventType`, STOP and propose an update to
`docs/data-model.md` and `docs/features/tickets/ticket-audit-log.md` before
proceeding with the implementation.

Invoke `@ticket-integrity-reviewer`:

- After implementing or modifying code in `backend/app/services/` or
  `backend/app/tasks/` that mutates tickets or their related data
- When creating or modifying a feature spec in `docs/features/` that
  describes ticket operations

See `docs/features/tickets/ticket-audit-log.md` for the event type contract.

#### Identity audit trail

Every service operation that modifies identity-related data (user
lifecycle, roles, API keys, role mappings) MUST create an
`IdentityAuditEvent` via `IdentityAuditLog.log_event()` in the same
database transaction. An operation that mutates identity data without
creating a corresponding `IdentityAuditEvent` is a bug.

If the change introduces a new type of identity mutation not covered by
an existing `IdentityAuditEventType`, STOP and propose an update to
`docs/data-model.md` and `docs/features/identity/identity-audit-log.md`
before proceeding with the implementation.

Invoke `@identity-integrity-reviewer`:

- After implementing or modifying code in `backend/app/services/` or
  `backend/app/tasks/` that mutates identity-related data
- When creating or modifying a feature spec in `docs/features/` that
  describes identity operations

See `docs/features/identity/identity-audit-log.md` for the event type
contract.

### 12. API completeness

The REST API is the primary interface of the platform. Every operation
that could be needed by any consumer (web UI, CLI, scripts, third-party
integrations) MUST be achievable through the API, with appropriate
filtering, pagination, and sorting capabilities.

When defining API endpoints in feature specifications, ensure that:
- Every data view has an API endpoint (not just internal service access)
- Every mutation has an API endpoint (not just CLI or background task)
- Filtering and sorting capabilities match what a consumer would need
- Pagination is available on all list endpoints

After adding or modifying API endpoints, evaluate whether a completeness
review is needed. The `@api-parity-reviewer` agent verifies API
completeness against specifications.

### 13. CVSS score resolution

CRITICAL: Every component of the system that needs a CVSS score MUST:

1. Resolve the CVSS version from the system-wide configuration
   (`default_cvss_version` setting) — never hardcode `"3.1"` or `"4.0"`
2. Use the **correct resolution strategy** for the consumer context —
   two distinct strategies exist and the caller MUST NOT substitute one
   for the other:
   - **Severity** (`resolve_severity_score`): 5-step cascade,
     multi-provider with cross-version fallback. Used for: severity
     derivation, display, triage
   - **Eligibility** (`resolve_eligibility_score`): 2-step cascade,
     SUSE-only, 10.0 conservative fallback. Used for: product eligibility
     threshold comparison

See `docs/features/tickets/cvss-scoring.md` (Severity Resolution Cascade
and Eligibility Score Resolution) for the authoritative definitions.

### 14. Fetcher base class compliance

CRITICAL: Every background task that fetches data from an external source
(CVE sync, CVSS sync, product sync, release detection, or any future data
ingestion) MUST:

1. Inherit from `BaseFetcher` (`backend/app/services/base_fetcher.py`).
   CVE fetchers (those that ingest or enrich CVE-related data from
   external sources) MUST inherit from `BaseCVEFetcher`
   (`backend/app/services/base_cve_fetcher.py`), which provides the
   `cve_source_type`, `fetch_single()`, and default `catch_up()`
   contracts. See `docs/features/platform/cve-fetcher-infrastructure.md`.
   Git-based CVE fetchers (delta-flow) MUST inherit from
   `BaseGitFetcher` (`backend/app/services/base_git_fetcher.py`). See
   `docs/features/platform/git-fetcher-infrastructure.md`
2. Define `name`, `description`, and `default_schedule` class attributes
3. Implement the `execute()` method with proper metric reporting via
   `self.record_created()`, `self.record_updated()`, and
   `self.record_failed()`
4. NOT bypass `BaseFetcher` (or `BaseCVEFetcher` for CVE fetchers) with
   a raw `@celery_app.task` decorator for fetching logic

**Exception — sub-operation tasks**: background tasks that fetch from
external sources as a sub-operation of an existing fetcher are exempt.
These are on-demand tasks triggered by a parent fetcher (not by Celery
Beat), with no independent schedule or dashboard presence. Example:
`create_ticket_from_detection` is enqueued by `detect_ibs_track_releases`
and fetches from NVD/SMELT, but is not a `BaseFetcher` subclass.

If there is a compelling reason to bypass `BaseFetcher` for a specific
case beyond this exception, STOP and inform the user with a detailed
explanation of why the bypass is advantageous, so the decision can be
made together. Do NOT proceed with a bypass without explicit user
approval.

After creating or modifying any fetcher, invoke
`@fetcher-compliance-reviewer` to verify correct integration with the
fetcher infrastructure.

See `docs/features/platform/fetcher-infrastructure.md` for the full
specification. Related specs: `docs/features/platform/cve-fetcher-infrastructure.md`
(BaseCVEFetcher), `docs/features/platform/git-fetcher-infrastructure.md`
(BaseGitFetcher), `docs/features/platform/networking.md` (HTTP client, TLS).

#### Fetcher documentation compliance

When defining or modifying a fetcher in a feature specification, the
documentation MUST follow the "Fetcher Documentation Requirements"
section in `docs/features/platform/fetcher-infrastructure.md`:

1. The fetcher's complete definition lives in exactly one spec (single
   source of truth)
2. The minimum documentation template is satisfied (properties table,
   algorithm, error handling, metrics)
3. The Fetcher Registry in `docs/data-sources.md` is updated
4. The classification rule is applied correctly (dedicated spec vs.
   embedded section)

If modifying a fetcher whose definition is currently fragmented across
multiple specs, consolidate it into its primary spec before proceeding
with the modification.

### 15. Specification coherence

After creating or modifying a feature specification in `docs/features/`, or
after modifying cross-cutting documents (`docs/data-model.md`,
`docs/api-spec.md`), evaluate whether a spec coherence review is needed:

1. Invoke `@spec-coherence-reviewer` when:
   - A new feature specification is created in `docs/features/`
   - An existing feature specification is modified with changes to business
     rules, data flows, statuses, or entity definitions
   - `docs/data-model.md` is modified with changes to entities, relationships,
     or constraints referenced by multiple feature specs
   - `docs/api-spec.md` is modified with changes to endpoints that span
     multiple features
2. Skip the review when:
   - The change is purely cosmetic (typo fixes, formatting, rewording without
     semantic change)
   - Only a single spec is affected and it does not reference other specs
3. Minor issues flagged by the reviewer should be fixed in the same PR
4. When performing a full review across all specs (e.g., triggered manually
   by the user), invoke `@spec-coherence-reviewer` **once per spec** in
   independent sessions. Do not combine multiple specs into a single review

### 16. Centralized ticket status evaluation

CRITICAL: Every service-layer function that modifies data relevant to
ticket status gates MUST go through the appropriate centralized module:

- **Package/track/product mutations** (`TicketPackageTrack`,
  `TicketPackageProduct`, package soft-delete/restore): `package_service`
  (`backend/app/services/package_service.py`)
- **CVSS and severity mutations** (`CVECVSSAssessment` records, manual
  severity): `ticket_mutations`
  (`backend/app/services/ticket_mutations.py`)
- **Non-gate ticket lifecycle mutations** (assignment, CVE association,
  mark-as-duplicate, confidentiality):
  `ticket_service` (`backend/app/services/ticket_service.py`)

Both `package_service` and `ticket_mutations` call
`ticket_mutations.reconcile_ticket_status()` after every gate-relevant
mutation. `ticket_service` also calls it for operations with indirect
gate effects (CVE association, assignment). Direct
modification of gate-relevant records outside the owning module is a
bug.

If there is no suitable function in the appropriate module for a new
type of gate-relevant mutation, add one before proceeding with the
implementation.

The `@ticket-integrity-reviewer` (Guardrail 11) verifies module usage
compliance after implementation.

See `docs/features/tickets/ticket-mutations.md`,
`docs/features/tickets/ticket-service.md`, and
`docs/features/packages/package-service.md` for the full
specifications.

### 17. Specification completeness

After creating or substantially modifying a feature specification in
`docs/features/`, evaluate whether a gap analysis is needed:

1. Invoke `@spec-gap-analyzer` when:
   - A new feature specification is created in `docs/features/`
   - An existing feature specification is modified with substantial
     changes to business rules, state machines, data flows, or
     operations
2. Skip the analysis when:
   - The change is purely cosmetic (typo fixes, formatting, rewording
     without semantic change)
   - Only clarifications or examples are added to an already-complete
     spec
3. If the analyzer identifies gaps rated as **High** severity (could
   cause data corruption, incorrect behavior, or system failure),
   address them in the specification before proceeding with
   implementation
4. Gaps rated as **Medium** severity (ambiguous behavior) should be
   clarified in the specification in the same PR
5. Gaps rated as **Low** severity (obvious implicit resolution) may be
   deferred at the author's discretion
6. When performing a full analysis across all specs (e.g., triggered
   manually by the user), invoke `@spec-gap-analyzer` **once per spec**
   in independent sessions. Do not combine multiple specs into a single
   analysis

The goal is to ensure that specifications are functionally complete
before implementation begins — every operation, state transition, and
user scenario should have its error paths, boundary conditions, and
concurrency considerations specified when they affect required behavior.
Gap analysis MUST NOT demand private implementation details merely to remove
legitimate technical choice; apply the specification boundary in
`docs/conventions.md` (Function Specification Completeness).

### 18. OpenCode tooling documentation

After adding, removing, or modifying any agent, command, or skill definition
in `.opencode/`, verify that `.opencode/README.md` is still accurate:

1. Update `.opencode/README.md` when:
   - A new agent is added to `.opencode/agents/`
   - An existing agent is removed or renamed
   - An agent's purpose or trigger (guardrail association) changes
   - A new command or skill is added, removed, or renamed in
     `.opencode/commands/` or `.opencode/skills/`
   - A new guardrail is added that references an agent (update the
     agent's trigger column)
2. Skip the update when:
   - The change is internal to an agent's instructions without affecting
     its name, purpose, or guardrail association

The goal is to keep `.opencode/README.md` as a reliable, up-to-date
reference for the project's OpenCode tooling.

### 19. Centralized user lifecycle operations

CRITICAL: Every operation that creates, modifies, deactivates, or
reactivates a user account — or modifies user roles — MUST go through
the `user_service` module (`backend/app/services/user_service.py`).
Direct modification of `User` or `UserRole` model fields from API
handlers, CLI commands, or Celery tasks is a bug.

This ensures that:

- Side effects (ticket reassignment, TicketAuditEvent creation, API key
  revocation, auth invalidation) are applied consistently
- Business rules (self-removal guard, self-deactivation guard) are
  enforced regardless of the entry point
- The async pattern is maintained consistently (service is async; sync
  callers use `asyncio.run()`)

The `@identity-integrity-reviewer` (Guardrail 11) verifies service
usage compliance after implementation.

See `docs/features/identity/user-service.md` for the full service contract.

### 20. API convention conformity

After creating or modifying a feature specification in `docs/features/` that
defines or modifies API endpoints, evaluate whether an API convention review
is needed:

1. Invoke `@api-convention-reviewer` when:
   - A new feature specification defines API endpoints (HTTP method, path,
     request/response schemas)
   - An existing feature specification is modified with changes to endpoint
     definitions (new endpoints, changed paths, modified error responses,
     altered pagination or filtering)
   - `docs/api-spec.md` convention sections are modified (to verify
     existing specs still conform)
2. Skip the review when:
   - The change is purely cosmetic (typo fixes, formatting, rewording
     without semantic change to endpoint definitions)
   - The specification does not define any API endpoints
   - Only implementation code is modified (pytest handles convention
     enforcement at the code level)
3. Minor issues flagged by the reviewer should be fixed in the same PR

The goal is to catch API convention violations at the specification stage —
before any implementation code is written — so that developers can implement
endpoints without design ambiguity.

### 21. Information placement — avoid misplaced or duplicated rules

Before writing a new rule, convention, pattern, or behavior in a specification
document, perform this self-check:

**A) Inter-document test (across specs or toward cross-cutting docs)**

1. **Reuse**: could this information be needed by another feature spec?
2. **Duplication**: does a similar rule already exist elsewhere?
3. **Scope**: if this feature spec were removed entirely, would the rule
   lose its meaning?

If the answers indicate that the information is not exclusive to the current
spec, STOP and propose the following options to the user:

- (a) The rule stays in the current spec because it is the natural owner;
  other specs reference it
- (b) The rule belongs in a cross-cutting document (see mapping below); the
  current spec adds a reference instead
- (c) The rule stays where it is because generalizing now would be premature
  or speculative

Cross-cutting document mapping:

| Information type | Target document |
|---|---|
| Code patterns, naming, style conventions | `docs/conventions.md` |
| API envelope format, errors, pagination, shared behaviors | `docs/api-spec.md` |
| Entities, columns, relationships, DB constraints | `docs/data-model.md` |
| External system integration (protocols, URLs, auth) | `docs/data-sources.md` |
| Configuration patterns, environment variables | `docs/configuration.md` |
| Shared business behaviors owned by no single feature | Dedicated feature spec, referenced by others |

**B) Intra-document test (within the same spec)**

When a spec repeats the same rule or behavior across multiple sections (e.g.,
the same pattern for multiple endpoints, the same logic for multiple states),
STOP and propose the following options to the user:

- (a) Extract the rule into a "General rules" or "Common behavior" section of
  the spec, so all current and future sections inherit it automatically
- (b) Keep it repeated because the variations between cases are sufficient to
  make generalization dangerous or misleading

**C) Guard against premature generalization**

Do NOT generalize when:

- The rule has been observed in only one context
- Its applicability to other contexts is speculative
- Generalizing would introduce ambiguity or artificial coupling between
  unrelated features

When in doubt, leave the rule in the specific context and flag the potential
for future generalization as a note.

**D) Mandatory user confirmation**

The agent MUST NOT proceed with any consolidation, extraction, or
generalization without explicit user confirmation. The agent presents the
analysis, proposes the options, and waits for the user's decision.

**E) Post-hoc verification**

After significant modifications to documents in `docs/`, invoke
`@docs-placement-reviewer` when:

1. A new rule, convention, or behavior has been added to a feature spec
2. An existing feature spec has been modified with content that could be
   cross-cutting (reusable patterns, generic rules, shared behaviors)
3. A feature spec repeats the same concept across multiple sections
4. Multiple feature specs are modified in the same session with related
   content

Skip the review when:

- The modification is purely cosmetic (typo fixes, formatting)
- The added content is clearly specific to the feature (business logic that
  has no meaning outside this feature)
- Only cross-cutting documents (`data-model.md`, `api-spec.md`, etc.) are
  updated without impact on feature specs

The goal is to keep each piece of information in the single most appropriate
location — avoiding both fragmentation (same rule scattered across multiple
specs) and over-centralization (feature-specific details extracted into
cross-cutting documents where they lose context).

### 22. RBAC Endpoint Permission Map maintenance

After adding, removing, or modifying an API endpoint in any feature
specification under `docs/features/`, verify that the Endpoint Permission
Map in `docs/features/identity/rbac.md` is still accurate:

1. Update the Endpoint Permission Map when:
   - A new API endpoint is added to a feature spec (add a row with method,
     path, access level, and owning spec link with anchor to the endpoint's
     definition header — e.g.,
     `[spec-name](path/to/spec.md#endpoint-header)`)
   - An existing endpoint's path, HTTP method, or access level changes
     (update the corresponding row)
   - An endpoint's definition header is renamed in the feature spec
     (update the anchor fragment in the owning spec link)
   - An endpoint is removed from a feature spec (remove the corresponding
     row)
2. Skip the update when:
   - The change is purely cosmetic (typo fixes, formatting, rewording
     without semantic change to the endpoint definition)
   - Only request/response schemas, error codes, or behavioral details
     change (these are owned by the feature spec, not by rbac.md)

The Endpoint Permission Map is the **single cross-cutting index** of all
API endpoints — the authoritative source for each endpoint's access level
is the owning feature specification. The map exists for cross-referencing
convenience; it must never contradict the owning spec.

### 23. No real personal data in repository

CRITICAL: ALL content written to files in this repository MUST use
fictional placeholder data for personal identifiers. This applies to:

- Feature specifications with API response examples
- Test fixtures and seed data
- Documentation snippets illustrating external system responses
- Comments and inline examples

Specifically FORBIDDEN:

- Real email addresses of individuals (e.g., `firstname.lastname@suse.com`)
- Real IBS/OBS userids that map to actual employees
- Real names from AD, IBS, Bugzilla, or any external system
- Real Distinguished Names containing personal information
- Any identifier copied verbatim from an external API response that
  identifies a real person

ALLOWED:

- Obviously fictional names (`John Doe`, `Alice Smith`, `jdoe`, `asmith`)
- Organizational/team addresses (`security-team@suse.de`) only when they
  are also sanitized to generic equivalents
- Service hostnames (`build.suse.de`, `smelt.suse.de`) — these are not PII

Before inserting any API response snippet or data sample from an external
system into a specification or documentation file:

1. Identify all personal identifiers in the snippet
2. Replace each with a fictional equivalent from the approved placeholder
   list (see `docs/conventions.md`, "Example Data in Documentation")
3. Verify that no real personal data remains before saving the file

If you are unsure whether a value constitutes PII, treat it as PII and
replace it.

### 24. Dimension orthogonality

CRITICAL: The package tracking model defines three orthogonal
dimensions — Affectedness, Eligibility, and Delivery — as specified
in `docs/features/packages/package-model.md` (Three Orthogonal
Dimensions). Each dimension MUST be independently computable: its
value depends only on its own inputs, never on the current state of
another dimension.

Before introducing any dependency where one dimension's computation,
filtering, or mutation depends on the state of another dimension,
STOP and:

1. Verify whether the coupling is necessary for business correctness
   (e.g., the Resolved gate inherently combines all three dimensions)
   or is an accidental optimization or shortcut
2. If necessary, document the justification in the relevant
   specification with an explicit note: "This is a deliberate
   cross-dimensional dependency because [reason]"
3. If avoidable, restructure the logic to use only the dimension's
   own inputs

Allowed cross-dimensional combinations:

- **Observation points**: gates, anomaly detection, and presentation
  views may read multiple dimensions to produce decisions or display
  — but they must not modify any dimension as a side effect
- **Post-mutation hooks**: calling `reconcile_ticket_status()` after a
  mutation is acceptable because the evaluator reads dimensions but
  does not modify them

Forbidden patterns:

- Filtering dimension A's computation scope by dimension B's state
  (e.g., "only recalculate eligibility when status is AFFECTED")
- Skipping dimension A's update because dimension B is in a
  particular state (e.g., "skip CVSS recalculation for final-status
  products")
- Setting dimension A as a side effect of dimension B's mutation
  (e.g., "set delivery_status = RELEASED when setting status = FIXED")

Note: **intra-dimensional scope optimizations** — where dimension A's
computation is skipped for records where the result is provably
inconsequential within the same dimension (e.g., skipping release
detection for tracks already in `FIXED` status) — are not
cross-dimensional couplings and do not trigger this guardrail.

### 25. Git operations safety

CRITICAL: agents MUST NOT perform any of the following operations
without explicit user authorization in the current conversation:

- Push to the default branch (`master`).
- Execute `gh pr merge` or any operation that merges a PR.
- Create or push Git tags.
- Force-push any branch (`--force`, `--force-with-lease`, `-f`).
- Execute `git reset --hard`, `git clean -fd`, or any destructive Git
  operation.
- Use `--no-verify` to bypass Git hooks.

**Merge gate procedure**: when all PR requirements are satisfied
(CI green, reviewers completed, no blocking findings), the agent
MUST present the following to the user and wait for explicit
authorization:

1. PR number and title.
2. Summary of CI status (all checks passing).
3. Summary of reviewer results (which reviewers ran, outcome).
4. Any unresolved items or known risks.

The agent proceeds with the merge ONLY after the user responds with
an explicit instruction referencing the PR (e.g., "merge PR #12",
"esegui il merge della #12"). Implicit or assumed approval is not
sufficient.

**Branch workflow**: agents work exclusively on topic branches.
`master` is never checked out for editing. After merge, agents
perform local cleanup:

1. Synchronize and prune: `git fetch origin --prune && git branch -f
   master origin/master` (updates local master and removes stale
   remote-tracking references in one step).
2. Delete the merged topic branch: `git branch -D <merged-branch>`.
   Note: `-D` (force) is required because squash merges produce a new
   commit SHA on `master` — git's `-d` (safe) does not recognize the
   branch as merged. This is safe because the agent only runs this
   step immediately after a successful `gh pr merge --squash`,
   confirming the work is on `master`.

**Commit discipline**: agents may commit and push to topic branches
without per-commit approval. Before the first push of a new branch,
the agent reports the branch name and initial scope. Before opening
a PR, the agent reports the intended title and description.

**GitHub body formatting**: when composing issue bodies or PR
descriptions via `gh`, do NOT hard-wrap lines at any fixed column
width (72, 80, or any other). Write each paragraph as a single
unwrapped line — GitHub's web UI renders HTML and handles wrapping
automatically. See `docs/conventions.md` (Issue and PR Body
Formatting).

**GitHub issue and PR reading**: when reading a GitHub issue or pull
request for any purpose (tracking issue context, deferral basis
search, scope resolution, implementation notes, or general context
gathering), always include the comments — they often contain
important decisions, clarifications, and notes added during the work.
The `gh` CLI separates description and comments into distinct views:
`gh issue view <n>` (or `gh pr view <n>`) shows metadata and
description but no comments, while `--comments` shows only comments
without the description. Always run both commands (they can be
parallel tool calls) to get the complete picture. Reading only one of
the two is insufficient; treat comments as part of the issue/PR
content.

**Automatic workflow initiation**: when the user issues a concrete
modification request, the agent MUST autonomously:

1. Fetch `origin/master`.
2. Verify a clean worktree (or stash/report conflicts).
3. Verify the owning spec exists and covers the request (Guardrail 1).
4. Search open issues in this repository. Reuse a suitable issue only if it
   meets the reuse criteria in `docs/conventions.md` (Issues and work
   units). Otherwise create a new issue via the "Work item" issue form,
   unless the request qualifies for one of the exemptions listed there.
5. Create a topic branch from `origin/master` with the appropriate
   naming prefix.
6. Proceed with implementation.

No dedicated command or explicit "create an issue" or "create a branch"
instruction is required. The agent does NOT create an issue or a branch
for exploratory requests (questions, analysis, brainstorming, spec review
without implementation intent).

**Spec-first sequencing**: if the spec is absent or insufficient:

1. The agent stops implementation intent.
2. After the user approves the proposed specification fix, the agent
   creates or reuses a documentation issue, then creates its `docs/<name>`
   branch for the spec work.
3. After the spec PR is approved and merged, creates a new implementation
   issue (if none suitable exists) and a new implementation branch from
   the updated `origin/master`.
4. Never mixes unmerged spec changes with implementation on the
   same branch — unless the combined-PR exception below applies.

**Combined spec-code PR exception**: a single PR MAY include both
specification changes and implementation when ALL of the following
conditions hold:

1. **Co-evolution**: the spec change is a refinement, correction, or
   incremental addition discovered during implementation — not a new
   feature or contract that was absent when the work started.
2. **Limited scope**: the spec delta is small relative to the
   implementation (e.g., adding an error code, clarifying a boundary
   condition, documenting a field). Substantial new contracts (new
   state machines, new entities, new security models) still require a
   separate PR.
3. **Same logical unit**: the spec change and the code change are
   incomprehensible in isolation — reviewing one without the other
   would leave the reviewer without context.
4. **No upstream dependents**: no other in-flight branch or component
   depends on the spec change landing first to proceed with its own
   work.

When the exception applies, the agent proceeds on the implementation
branch (not a `docs/` branch). The PR title uses the implementation
type prefix (e.g., `feat:`, `fix:`). The PR description explicitly
notes which spec files were modified and why the combined approach was
chosen.

When the user explicitly requests a combined PR and the agent believes
the conditions above are not met, the agent MUST state which condition
fails and ask for confirmation rather than refusing outright. The user's
explicit instruction overrides the default separation after
acknowledgment of the trade-off.

### 26. Reviewer proportionality and design simplicity

CRITICAL: Reviewer findings are inputs to engineering judgment, not an
automatic mandate to add code or documentation. Correctness, security, and
explicit specification requirements remain mandatory, but their resolution
MUST use the smallest change that fully addresses the real problem.

#### Reviewer pre-filter (before reporting)

Before reporting a finding, every reviewer MUST apply this filter:

1. **Real problem**: Is there a concrete, realistic scenario in which the
   issue causes incorrect behavior, data loss, a security vulnerability, an
   explicit contract violation, or a meaningful maintenance problem? If not,
   omit the finding.
2. **Necessary documentation**: Would the proposed resolution only document
   behavior that is already unambiguous and that an implementer does not need
   in order to choose a correct implementation? If so, omit the finding.
3. **Necessary resolution**: Is a change required for correctness, security,
   data integrity, an explicit specification or guardrail, or a realistic
   operational need? If not, do not recommend it merely for theoretical
   completeness or future flexibility.
4. **Proportionality**: Is the implementation and maintenance cost
   proportionate to the likelihood and impact of the problem? If not, discard
   the finding as disproportionate. It does not affect the review verdict.
5. **Structural complexity**: Would the resolution add a table, state,
   abstraction, dependency, configuration option, exception hierarchy,
   workflow branch, or substantial specification machinery? If so, the agent
   MUST NOT apply it autonomously. Present the finding, smallest viable
   resolution, cost, and recommendation to the user and wait for a decision.

Reviewers MUST prefer removal, reuse, and simplification over adding new
mechanisms. A discarded finding is not a deferred requirement and MUST NOT be
implemented. This filter does not permit ignoring a confirmed defect, concrete
vulnerability, or explicit specification/guardrail violation.

#### Finding evaluation procedure (after receiving)

Reviewer findings are hypotheses, not mandates. The agent that invoked a
reviewer MUST independently evaluate every finding before acting on it,
regardless of the finding's severity rating (including "Needs revision").

1. **Verify independently**: re-read the relevant code or specification to
   confirm the problem actually exists. Do not assume the reviewer's
   characterization is correct — reviewers can misread context, overlook
   existing handling, or flag already-covered behavior.

2. **Apply discard criteria** — discard the finding if ANY of the following
   hold:
   - **Not a real problem**: the scenario is already handled (implicitly
     or explicitly), or does not apply given actual system constraints
   - **Over-documentation**: the resolution would add information that is
     obvious, trivially derivable, or whose absence causes no ambiguity
     for a competent implementer
   - **Speculative**: the risk has no plausible path to manifesting given
     the architecture and usage patterns
   - **Disproportionate**: the fix cost exceeds the problem's realistic
     impact

3. **Evaluation standard**: "Would a competent implementer actually get
   this wrong or be blocked by the absence of what the finding requests?"
   If no, discard.

4. **Disposition**:
   - **Discard**: do not implement. Mention materially important discards
     in the PR summary; omit minor ones
   - **Accept**: implement using the smallest change that addresses the
     real problem
   - **Escalate**: if the finding recommends structural complexity,
     present to the user with options before acting

Never implement a finding without independently verifying it as a real
problem. "The reviewer said so" is not sufficient justification. A
discarded finding is not a deferred requirement and MUST NOT be
implemented.

#### Mandatory design review

Invoke `@design-reviewer` when:

- A new feature specification is created in `docs/features/`
- An existing feature specification receives substantial changes to business
  rules, state machines, data flows, operations, architecture, or component
  boundaries

Skip the review for cosmetic edits, clarifications that do not alter design,
and examples added to an already reviewed design. The reviewer evaluates
whether the design solves the present requirement with the fewest justified
moving parts and identifies opportunities to remove unnecessary complexity.

Design findings that recommend adding complexity follow the filter above.
Purely stylistic simplifications and other unnecessary recommendations are
omitted. Existing complexity is a finding only when it causes a concrete
maintenance or operational problem that passes the filter. A structural
simplification that would change specified behavior or scope MUST be presented
to the user before the specification is changed.
