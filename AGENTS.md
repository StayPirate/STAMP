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
├── dev-env.sh                   # Local dev stack launcher (Podman/Docker)
├── docker-compose.yml           # Local development environment
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
│   └── reviews/                 # Review findings (untracked)
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
│   ├── scripts/                 # Utility scripts
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

- **Install/sync dependencies**: `cd backend && uv sync` (installs Python 3.13 and creates `.venv` automatically if needed)
- **Backend tests**: `cd backend && uv run pytest`
- **Backend lint**: `cd backend && uv run ruff check . && uv run ruff format --check .`
- **DB migrations**: `cd backend && uv run alembic upgrade head`
- **New migration**: `cd backend && uv run alembic revision --autogenerate -m "description"`
- **Local dev stack**: `./dev-env.sh up` (PostgreSQL + Redis, auto-detects Podman or Docker)

## Local Environment

### OBS/IBS CLI (`osc`)

On this machine, the `osc` command-line tool (used to interact with OBS/IBS)
MUST be invoked through `secbox`:

- **Always use**: `secbox osc <subcommand>` (NEVER bare `osc <subcommand>`)
- **Never pass credentials**: authentication is handled automatically by
  `secbox`. Do not pass `--user`, `--pass`, or attempt to configure
  `~/.oscrc`
- The API URL (`-A`) must still be specified as usual when targeting a
  specific OBS/IBS instance (e.g., `-A https://build.suse.de`)

Examples:
- `secbox osc -A https://build.suse.de ls SUSE:SLE-15-SP6:Update`
- `secbox osc -A https://build.suse.de api /source/SUSE:SLE-15-SP6:Update/kernel-default`

## Workspace Awareness

When checking for existing files or directories, ALWAYS inspect the
actual filesystem (using `ls`, `Read`, or `Glob` tools) rather than
relying solely on git-tracked files. Files and directories listed in
`.gitignore` are not versioned but may contain important local work
products (e.g., review findings in `docs/reviews/`, build
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
| Utility scripts            | `backend/scripts/`                |
| TLS certificates           | `backend/certs/`                  |
| Backend tests              | `backend/tests/`                  |
| Draft documents            | `docs/drafts/`                    |
| Review findings            | `docs/reviews/`                   |

If the user asks to create a file in a location that does not match this map,
STOP and notify: "This file should go in [correct location] according to the
project conventions. Should I proceed with the correct location?"

### 3. Coherent spec-code updates

When modifying code that changes the behavior of a feature, verify whether the
corresponding specification needs to be updated. If it does, propose the
specification update BEFORE modifying the code.

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

For CI/CD-specific changes, delegate to the `@cicd` subagent.

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
3. If the reviewer identifies complexity concerns or issues rated as
   "Needs revision", address them before considering the task complete
4. Minor issues flagged by the reviewer should be fixed in the same PR

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
3. If the reviewer identifies issues rated as "Needs revision", address them
   before considering the task complete
4. Minor issues flagged by the reviewer should be fixed in the same PR

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
3. If the reviewer identifies vulnerabilities rated as "Needs revision",
   address them before considering the task complete
4. Issues rated as Critical or High severity MUST be fixed before merging

The goal is to prevent security vulnerabilities from being introduced into
the codebase. The `@security-reviewer` agent complements the automated
security scanning in the CI pipeline (`bandit`, `pip-audit`).

### 11. Audit trail atomicity

CRITICAL: Every service operation that modifies data covered by an audit
trail registered in `BaseAuditLog` MUST create the corresponding audit
event record in the same database transaction. The absence of an audit
event for a covered mutation is a bug.

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
3. If the reviewer identifies issues rated as "Needs revision" (contradictory
   rules or incompatible flows between specs), resolve them before considering
   the task complete
4. Issues rated as "Minor issues" should be fixed in the same PR
5. When performing a full review across all specs (e.g., triggered manually
   by the user), invoke `@spec-coherence-reviewer` **once per spec** in
   independent sessions. Do not combine multiple specs into a single review

### 16. Centralized ticket status evaluation

CRITICAL: Every service-layer function that modifies data relevant to
ticket status gates MUST go through the appropriate centralized module:

- **Package/track/product mutations** (`TicketPackageTrack`,
  `TicketPackageProduct`, package soft-delete/restore): `package_service`
  (`backend/app/services/package_service.py`)
- **CVSS and severity mutations** (`CVECVSSAssessment` records, severity
  override): `ticket_mutations`
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
concurrency considerations explicitly specified.

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
   - `docs/api-spec.md` General Conventions section is modified (to verify
     existing specs still conform)
2. Skip the review when:
   - The change is purely cosmetic (typo fixes, formatting, rewording
     without semantic change to endpoint definitions)
   - The specification does not define any API endpoints
   - Only implementation code is modified (pytest handles convention
     enforcement at the code level)
3. If the reviewer identifies issues rated as "Needs revision" (violations
   that would cause implementation ambiguity or client incompatibility),
   address them in the specification before proceeding with implementation
4. Issues rated as "Minor issues" should be fixed in the same PR

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
| External system integration (protocols, URLs, auth) | `docs/data-sources.md` / `docs/architecture.md` |
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

If the reviewer identifies issues rated as "Needs revision", propose the
relocation/consolidation options to the user before considering the task
complete.

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
