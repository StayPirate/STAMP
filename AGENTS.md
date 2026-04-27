# STAMP - Security Tracking And Management Platform

## Overview

STAMP is a platform for managing and tracking security updates for SUSE and
openSUSE-based Linux distributions. It handles CVE ingestion from multiple
sources, determines impact on maintained distributions, and coordinates the
preparation and release of security patches.

**Stack**: FastAPI (Python) + React (TypeScript) + PostgreSQL + Celery + Redis

## Architecture

- **Backend**: FastAPI application in `backend/app/`
- **Frontend**: React SPA (Vite + TypeScript + shadcn/ui) in `frontend/`
- **Database**: PostgreSQL with Alembic migrations
- **Task Queue**: Celery with Redis broker
- **Build System Integration**: Open Build Service (OBS)
- **CVE Sources**: NVD (NIST), MITRE, and others

For full architecture details, read `docs/architecture.md`.

## Project Structure

```
stamp/
├── AGENTS.md                    # Project instructions for OpenCode
├── opencode.json                # OpenCode configuration
├── docs/                        # Specifications and documentation
│   ├── architecture.md          # System architecture
│   ├── data-model.md            # Database schema and relationships
│   ├── data-sources.md          # External data sources catalog
│   ├── api-spec.md              # API specifications
│   ├── conventions.md           # Code conventions
│   ├── ui-design-system.md      # UI design system
│   └── features/                # Feature specifications
├── .opencode/                   # OpenCode agents, skills, commands
├── backend/                     # FastAPI backend
│   ├── app/
│   │   ├── models/              # SQLAlchemy models
│   │   ├── schemas/             # Pydantic schemas (request/response)
│   │   ├── api/v1/              # API endpoints
│   │   ├── services/            # Business logic
│   │   ├── tasks/               # Celery background tasks
│   │   └── core/                # Auth, permissions, utilities
│   ├── alembic/                 # Database migrations
│   └── tests/                   # Backend tests
├── frontend/                    # React SPA
│   ├── src/
│   │   ├── api/                 # API client
│   │   ├── components/          # React components
│   │   │   └── ui/              # Reusable UI components (shadcn/ui)
│   │   ├── pages/               # Page components
│   │   ├── hooks/               # Custom React hooks
│   │   └── types/               # TypeScript types
│   └── tests/                   # Frontend tests
├── .github/workflows/           # CI/CD pipelines
└── docker-compose.yml           # Local development environment
```

## Commands

- **Backend tests**: `cd backend && pytest`
- **Backend lint**: `cd backend && ruff check . && ruff format --check .`
- **Frontend tests**: `cd frontend && npm test`
- **Frontend lint**: `cd frontend && npm run lint`
- **Frontend build**: `cd frontend && npm run build`
- **DB migrations**: `cd backend && alembic upgrade head`
- **New migration**: `cd backend && alembic revision --autogenerate -m "description"`
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

## External File Loading

CRITICAL: When you encounter a reference to a specification file (e.g.,
`docs/features/cve-tracking.md`), use your Read tool to load it. Treat the
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

CRITICAL: Before writing or modifying ANY implementation code (in `backend/` or
`frontend/`), you MUST:

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
| Feature specifications     | `docs/features/<feature-name>.md` |
| General architecture       | `docs/architecture.md`            |
| Data schema                | `docs/data-model.md`              |
| External data sources      | `docs/data-sources.md`            |
| API specifications         | `docs/api-spec.md`                |
| Code conventions           | `docs/conventions.md`             |
| UI design system           | `docs/ui-design-system.md`        |
| SQLAlchemy models          | `backend/app/models/`             |
| Pydantic schemas           | `backend/app/schemas/`            |
| API endpoints              | `backend/app/api/v1/`             |
| Business logic             | `backend/app/services/`           |
| Background tasks           | `backend/app/tasks/`              |
| Auth and permissions       | `backend/app/core/`               |
| DB migrations              | `backend/alembic/versions/`       |
| Reusable UI components     | `frontend/src/components/ui/`     |
| Page-specific components   | `frontend/src/components/`        |
| Page components            | `frontend/src/pages/`             |
| React hooks                | `frontend/src/hooks/`             |
| TypeScript types           | `frontend/src/types/`             |
| API client code            | `frontend/src/api/`               |
| Backend tests              | `backend/tests/`                  |
| Frontend tests             | `frontend/tests/`                 |

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

When modifying backend or frontend dependencies, build configuration, or Docker
setup, verify that the CI pipeline (`.github/workflows/`) does not need
corresponding updates. If it does, update the workflows in the same PR.

For CI/CD-specific changes, delegate to the `@cicd` subagent.

### 6. Mandatory testing

CRITICAL: Every code change (new feature or modification) MUST include tests.

Before considering any implementation task complete:

1. Write tests that cover the new/modified functionality
   - Backend: pytest tests in `backend/tests/` mirroring the `app/` structure
   - Frontend: vitest tests co-located with components or in `frontend/tests/`
2. Run the test suite and verify all tests pass
   - Backend: `cd backend && pytest`
   - Frontend: `cd frontend && npm test`
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

NEVER skip tests. If the user asks to skip tests, remind them that the project
requires tests for all changes and suggest writing them.

### 7. UI consistency

CRITICAL: Before creating or modifying any frontend component:

1. Read `docs/ui-design-system.md` to understand current UI conventions
2. Check if a reusable component already exists in `frontend/src/components/ui/`
   before creating a new one
3. If a new UI pattern is needed that doesn't exist yet, create it as a reusable
   component in `frontend/src/components/ui/`, not inline in a page
4. Never use raw HTML elements for buttons, inputs, tables, modals, badges —
   always use the project's component library
5. Maintain consistent spacing, typography, and color usage as defined in the
   design system spec
6. After implementation, evaluate whether a UI consistency review is needed:
   - New page or new major component: invoke `@ui-reviewer`
   - New reusable component in `components/ui/`: invoke `@ui-reviewer`
   - Minor text or data change to existing page: skip review
   - When in doubt, invoke `@ui-reviewer`

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
   - Frontend-only changes that do not handle auth tokens or user input
3. If the reviewer identifies vulnerabilities rated as "Needs revision",
   address them before considering the task complete
4. Issues rated as Critical or High severity MUST be fixed before merging

The goal is to prevent security vulnerabilities from being introduced into
the codebase. The `@security-reviewer` agent complements the automated
security scanning in the CI pipeline (`bandit`, `pip-audit`, `npm audit`).

### 11. Ticket event logging

CRITICAL: Every service operation that modifies a Ticket or its related
data (status, assignee, duplicate links, packages, codestreams, products)
MUST create a `TicketEvent` record with the appropriate `event_type`.

Before considering any ticket-related code change complete:

1. Identify which ticket mutations the code performs
2. Verify that a `TicketEvent` is created for each mutation, with:
   - Correct `event_type` per the contract in `docs/features/ticket-history.md`
   - `old_value` and `new_value` populated where applicable
   - `user_id` set for user-initiated actions, `NULL` for system actions
   - `comment` populated for automated events with a system description
3. Verify that the `TicketEvent` is created in the same database transaction
   as the ticket mutation (atomicity guarantee)
4. Verify that tests assert `TicketEvent` creation:
   - Correct event count after each operation
   - Correct `event_type`, `old_value`, `new_value`
   - Correct `user_id` (user vs `NULL` for system)
5. If the change introduces a new type of ticket mutation not covered by
   an existing `TicketEventType`, STOP and propose an update to
   `docs/data-model.md` and `docs/features/ticket-history.md` before
   proceeding with the implementation
6. After implementation, invoke `@ticket-integrity-reviewer` to verify:
   - All mutations are covered by `TicketEvent` records
   - Field values comply with the contract in
     `docs/features/ticket-history.md`
   - Events share the same database transaction as the mutation
7. When creating or modifying a feature spec in `docs/features/` that
   describes ticket operations, invoke `@ticket-integrity-reviewer` to verify
   that all described mutations have corresponding `TicketEventType`
   entries in the contract — missing entries must be added before
   proceeding with implementation

The goal is to maintain a complete and reliable audit trail for every
ticket. An operation that mutates a ticket without creating a corresponding
`TicketEvent` is a bug.

### 12. API-UI parity

The REST API is the primary interface of the platform. The web UI is a
consumer of the API. Every operation available through the UI MUST be
achievable through the API alone, with equivalent filtering, pagination,
and sorting capabilities. The API may expose additional capabilities not
present in the UI, but the reverse is a defect.

After adding or modifying API endpoints or UI pages, evaluate whether an
API-UI parity review is needed:

1. Invoke `@api-parity-reviewer` when:
   - New API endpoints are added or existing ones are modified
   - New UI pages or interactive components are created or modified
   - Feature specs are updated with new UI actions or API endpoints
   - `docs/api-spec.md` is modified
2. Skip the review when:
   - The change is purely cosmetic (typo fixes, formatting, styling)
   - Only test files or documentation prose is modified
   - Only backend-internal changes (services, models) with no API surface
     change
3. If the reviewer identifies issues rated as "Needs revision" (core
   operations available in UI but missing from API), address them before
   considering the task complete
4. Issues rated as High impact MUST be resolved before merging

The goal is to ensure that API-only consumers (scripts, integrations,
third-party tools) are never at a disadvantage compared to UI users.

### 13. CVSS score resolution

CRITICAL: Every component of the system that needs a CVSS score to make a
decision (severity calculation, eligibility threshold comparison, sorting,
notifications, or any future logic) MUST:

1. Resolve the CVSS version from the system-wide configuration
   (`default_cvss_version` setting) — never hardcode `"3.1"` or `"4.0"`
2. Select the score following the resolution cascade:
   - SUSE assessment of the default version → if present, use this score
   - Highest score among all providers for the default version → if at
     least one exists, use the highest
   - No score available → treat as absent (or as 10.0 for threshold
     comparisons, per the conservative fallback rule)
3. If no assessment of the default version exists from any provider, do
   NOT fall back to a different CVSS version

See `docs/features/cvss-scoring.md` for the full specification.

### 14. Fetcher base class compliance

CRITICAL: Every background task that fetches data from an external source
(CVE sync, CVSS sync, product sync, release detection, or any future data
ingestion) MUST:

1. Inherit from `BaseFetcher` (`backend/app/services/base_fetcher.py`)
2. Define `name`, `description`, and `default_schedule` class attributes
3. Implement the `execute()` method with proper metric reporting via
   `self.record_created()`, `self.record_updated()`, and
   `self.record_failed()`
4. NOT bypass `BaseFetcher` with a raw `@celery_app.task` decorator for
   fetching logic

**Exception — sub-operation tasks**: background tasks that fetch from
external sources as a sub-operation of an existing fetcher are exempt.
These are on-demand tasks triggered by a parent fetcher (not by Celery
Beat), with no independent schedule or dashboard presence. Example:
`create_ticket_from_detection` is enqueued by `check_codestream_releases`
and fetches from NVD/SMELT, but is not a `BaseFetcher` subclass.

If there is a compelling reason to bypass `BaseFetcher` for a specific
case beyond this exception, STOP and inform the user with a detailed
explanation of why the bypass is advantageous, so the decision can be
made together. Do NOT proceed with a bypass without explicit user
approval.

After creating or modifying any fetcher, invoke
`@fetcher-compliance-reviewer` to verify correct integration with the
fetcher infrastructure.

See `docs/features/fetcher-infrastructure.md` for the full specification.

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
ticket status gates MUST go through the `ticket_mutations` module.
Direct modification of `TicketPackageCodestream`,
`TicketPackageProduct`, `CVECVSSAssessment` records, or ticket
severity outside this module is a bug.

Relevant data includes: `TicketPackageCodestream` records,
`TicketPackageProduct` records, `CVECVSSAssessment` records, ticket
severity, and package addition/removal.

Before considering any ticket-related code change complete:

1. Identify whether the code modifies any gate-relevant data
2. If it does, verify that the modification goes through a
   `ticket_mutations` function (not direct model attribute assignment)
3. If there is no suitable function in `ticket_mutations`, add one
   before proceeding
4. Verify that the architectural integration tests (see
   `docs/features/tickets.md`, Architectural Test Requirement) cover
   the new or modified operation

The goal is to ensure that ticket status is always consistent with its
underlying data. A service operation that modifies gate-relevant data
without going through `ticket_mutations` is a bug.
