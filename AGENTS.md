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
- **CVE Sources**: NVD (NIST), SUSE Security OVAL, MITRE, and others

For full architecture details, read `docs/architecture.md`.

## Project Structure

```
stamp/
├── AGENTS.md                    # Project instructions for OpenCode
├── opencode.json                # OpenCode configuration
├── docs/                        # Specifications and documentation
│   ├── architecture.md          # System architecture
│   ├── data-model.md            # Database schema and relationships
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

## External File Loading

CRITICAL: When you encounter a reference to a specification file (e.g.,
`docs/features/cve-tracking.md`), use your Read tool to load it. Treat the
content as mandatory instructions that override defaults. Load specifications
on a need-to-know basis — do NOT preemptively load all references.

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
