# Draft: Remove Frontend Scaffolding and UI Design System

## Summary

Remove all frontend implementation scaffolding from this repository. The
Sentinel frontend will be developed in a dedicated repository in the future,
once all backend specifications are implemented and tested. This repository
remains the canonical home for all product specifications (including future
UI specs), while implementation code for the frontend will live elsewhere.

## Rationale

1. **API-first**: the REST API is the primary interface. The UI is a consumer
   that will be developed against the published OpenAPI contract
2. **Nothing to preserve**: the frontend directory contains only empty
   scaffolding (`main.tsx`, `App.tsx`, `.gitkeep` files) with no components,
   no tests, and no `package-lock.json`
3. **Maintenance cost**: carrying an unused React app creates dependency
   drift, security alert noise, and CI complexity with zero value
4. **Spec ownership stays here**: product specifications (including future
   UI feature specs in `docs/features/ui/`) remain in this repository. Only
   the implementation moves to a separate repo
5. **`ui-design-system.md`**: contains exclusively visual/rendering concerns
   (shadcn/ui component guidance, color palette, typography, spacing,
   responsive breakpoints). None of this information is consumed by the
   backend or by any current specification. It will be recreated in the UI
   repository when the time comes, potentially with updated technology choices

## Scope of Changes

### Files to DELETE

| Path | Reason |
|------|--------|
| `frontend/` (entire directory) | Empty scaffolding — no implementation to preserve |
| `docs/ui-design-system.md` | Purely visual/rendering concerns; no backend relevance |
| `.opencode/agents/ui-reviewer.md` | Reviewer for non-existent frontend code |

### Files to MODIFY

| Path | Nature of change |
|------|-----------------|
| `AGENTS.md` | Remove frontend from structure, commands, content type map; remove Guardrail 7; rework Guardrail 12; remove `@ui-reviewer` from subagent table; add repository scope note |
| `docs/conventions.md` | Remove "TypeScript (Frontend)" section; update timestamp convention to remove frontend-specific line |
| `docs/architecture.md` | Remove "Frontend (React SPA)" section; rework "Frontend And API Routing" to "API Routing"; add "Repository Scope" section; update system overview; remove "frontend" prose references in "Container Images", "Deployment Target", and "Configuration And Secrets" sections |
| `docs/deployment.md` | Remove Node.js prerequisite; remove frontend dev server command |
| `docs/configuration.md` | Update CORS_ORIGINS description (remove "for the frontend") |
| `docs/system-map.md` | Remove frontend subgraph from System Components diagram |
| `.opencode/README.md` | Remove `@ui-reviewer` from subagent table; update `@api-parity-reviewer` description; update `/run-tests` description |
| `.opencode/skills/new-feature/SKILL.md` | Remove Step 4 (Implement the frontend); remove frontend test step; remove `@ui-reviewer` invocation |
| `.opencode/commands/run-tests.md` | Remove frontend test and lint steps; update description |
| `.opencode/commands/check-spec.md` | Remove "frontend components" from file list |
| `.opencode/prompts/code.md` | Remove `frontend/` from scope; remove `@ui-reviewer` from reviewer list |
| `.opencode/agents/api-parity-reviewer.md` | Rework from "API-UI parity" to "API completeness"; remove hardcoded `frontend/` paths |
| `.opencode/agents/docs-reviewer.md` | Remove `frontend/src/pages/` reference |
| `.opencode/agents/docs-placement-reviewer.md` | Remove `docs/ui-design-system.md` from reference list |
| `.opencode/agents/test-reviewer.md` | Remove `cd frontend && npm test` permission; remove "Frontend:" checklist item |
| `.opencode/agents/design-reviewer.md` | Remove `@ui-reviewer` reference; update "API-UI parity" to "API completeness" in exclusion list |
| `.opencode/agents/cicd.md` | Remove `frontend/Dockerfile` from permissions and references |
| `.opencode/agents/security-reviewer.md` | Remove "Frontend security" checklist section (React-specific checks for non-existent code) |
| `.opencode/agents/spec-coherence-reviewer.md` | Update "API-UI parity" to "API completeness" in exclusion list |
| `.opencode/agents/spec-gap-analyzer.md` | Update "API-UI parity" to "API completeness" in exclusion list |
| `.opencode/agents/api-convention-reviewer.md` | Update "UI-API parity" to "API completeness" in exclusion list |
| `.opencode/prompts/spec.md` | Remove "UI design system" from editable file list |
| `docs/features/platform/fetcher-infrastructure.md` | Replace "dashboard frontend" with "dashboard" |
| `docs/drafts/open-points.md` | Remove or annotate frontend-related open points |
| `docs/reviews/ticket-references.md` | Update stale reference to `ui-design-system.md` (line 127) |

### Files NOT touched (context)

Feature specifications use "the frontend" as shorthand for "any API consumer
client". These references describe expected client behavior at the protocol
level and are not tied to the deleted frontend scaffolding. They remain valid
for any future client implementation and are intentionally left unchanged.

| Path | Reason left unchanged |
|------|----------------------|
| `docs/features/identity/authentication.md` | "Frontend session behavior" section documents API contract behavior (HttpOnly cookie, redirect logic) — this is client integration protocol, not UI implementation |
| `docs/features/identity/sso-authentication.md` | "Frontend flow" section documents the OAuth2 redirect sequence — this is protocol specification, not UI implementation |
| `docs/features/identity/local-authentication.md` | "Frontend behavior" section references parent spec — same rationale as above |
| `docs/features/tickets/cve-sync-epss.md` | "enables the frontend" — describes data availability for any API consumer |
| `docs/features/tickets/cve-service.md` | "frontend calls", "frontend builds source links" — describes API consumption patterns |
| `docs/features/identity/user-management.md` | "the frontend to display" — describes expected client behavior |
| `docs/features/integrations/ibs-integration.md` | "the frontend" — generic client reference |
| `docs/features/packages/ibs-submission-tracking.md` | "the frontend can" — generic client capability note |
| `docs/data-model.md` | "UI display note" is a data consumption hint for any client; kept |
| `docs/api-spec.md` | No frontend-specific content |
| `docs/reviews/*.md` (except ticket-references.md line 127) | Review findings are historical records — not modified |

---

## Action Plan

### Step 1 — Delete frontend directory

Delete the entire `frontend/` directory tree:
- `frontend/src/` (main.tsx, App.tsx, assets/, all .gitkeep subdirs)
- `frontend/tests/`
- `frontend/package.json`
- `frontend/tsconfig.json`
- `frontend/vite.config.ts`
- `frontend/index.html`
- `frontend/Dockerfile`
- `frontend/.dockerignore`

### Step 2 — Delete UI design system

Delete `docs/ui-design-system.md`.

### Step 3 — Delete UI reviewer agent

Delete `.opencode/agents/ui-reviewer.md`.

### Step 4 — Update AGENTS.md

4.1. **Overview section** (line ~10): Change
`**Stack**: FastAPI (Python) + React (TypeScript) + PostgreSQL + Celery + Redis`
to
`**Stack**: FastAPI (Python) + PostgreSQL + Celery + Redis`

4.2. **Overview section** (after the Stack line): Add a repository scope note:
```
This repository contains backend implementation and all product specifications.
The frontend will be developed in a dedicated repository.
```

4.3. **Project Structure** (lines ~42-86): Remove ALL frontend-related entries:
- Remove `│   ├── ui-design-system.md      # UI design system`
- Remove `│   │   └── ui/                  # UI features`
- Remove the entire `├── frontend/` subtree (lines 73-85)

4.4. **Commands section** (lines ~93-95): Remove:
- `- **Frontend tests**: ...`
- `- **Frontend lint**: ...`
- `- **Frontend build**: ...`

4.5. **Guardrail 1** (line ~150): Change "Before writing or modifying ANY
implementation code (in `backend/` or `frontend/`)" to "Before writing or
modifying ANY implementation code (in `backend/`)".

4.6. **Content Type table** (lines ~181-198): Remove these rows:
- `| UI design system           | docs/ui-design-system.md          |`
- `| Reusable UI components     | frontend/src/components/ui/       |`
- `| Page-specific components   | frontend/src/components/          |`
- `| Page components            | frontend/src/pages/               |`
- `| React hooks                | frontend/src/hooks/               |`
- `| TypeScript types           | frontend/src/types/               |`
- `| API client code            | frontend/src/api/                 |`
- `| Frontend tests             | frontend/tests/                   |`

4.7. **Guardrail 5** (line ~234): Change "When modifying backend or frontend
dependencies" to "When modifying backend dependencies".

4.8. **Guardrail 6** (lines ~248, ~251): Remove frontend test references:
- Remove `- Frontend: vitest tests co-located with components or in frontend/tests/`
- Remove `- Frontend: cd frontend && npm test`

4.9. **Guardrail 7 (UI consistency)** — REMOVE ENTIRELY (lines ~270-287).
Renumber subsequent guardrails is NOT needed (guardrails are referenced by
number across all specs and prompts — changing numbers would break hundreds
of references). Instead, replace the content with a tombstone:

```markdown
### 7. [Reserved — UI consistency]

This guardrail will be reinstated when a frontend implementation exists.
The UI will be developed in a dedicated repository.
```

4.10. **Guardrail 12 (API-UI parity)** (lines ~416-443): Rework to remove
UI-specific language while preserving the API completeness principle.
Replace the current content with:

```markdown
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
```

4.11. **Guardrail 21 cross-cutting document mapping table** (line ~721):
Remove the row `| Cross-cutting UI/UX rules | docs/ui-design-system.md |`
entirely (no replacement — the document no longer exists).

### Step 5 — Update docs/conventions.md

5.1. **General section, API-first bullet** (line ~10): Change "The web UI is
a consumer of the API. Every operation available through the UI must be
achievable through the API alone" to "Every operation must be achievable
through the API alone". Remove "The API may expose additional capabilities
not present in the UI, but the reverse is a defect".

5.2. **Timestamps & Timezones section** (lines ~180-183): Remove the
frontend-specific lines:
- Remove `- **Frontend**: the UI converts UTC timestamps...`
- Remove `  user's local timezone at display time (using Intl.DateTimeFormat or`
- Remove `  equivalent). When submitting datetime values to the API, the frontend`
- Remove `  converts local time to UTC before sending`

Replace with a single implementation-agnostic line:
`- **API consumers**: convert UTC timestamps to local timezone at display time. When submitting datetime values to the API, convert local time to UTC before sending`

5.3. **TypeScript (Frontend) section** (lines ~453 to end of that section):
REMOVE the entire section (everything from `## TypeScript (Frontend)` through
the end of the "Testing Conventions" subsection under it). This includes:
- Style subsection
- Naming subsection
- React Conventions subsection
- Component Structure subsection
- Testing Conventions subsection

### Step 6 — Update docs/architecture.md

6.1. **High-Level Architecture diagram** (lines ~9-52): Remove the
`React SPA` box and the `SPA -->|"REST API (HTTP)"| API` connection from
the ASCII diagram.

6.2. **Components section**: Remove the entire `### Frontend (React SPA)`
subsection (lines ~56-63).

6.3. **"Frontend And API Routing" section** (lines ~370-385): Rename to
`### API Routing` and rework to remove frontend container references:

Replace with:
```markdown
### API Routing

API endpoints are served under the `/api` path prefix. In production, a
reverse proxy or ingress routes `/api` requests to the backend service.
The API must remain independent of any specific frontend hosting strategy.
```

6.4. **"Deployment Target" section** (line ~301): Change
"not in backend or frontend implementation code"
to
"not in backend implementation code".

6.5. **"Container Images" section** (line ~306): Change
"Backend and frontend builds produce standard OCI-compatible images."
to
"Backend builds produce standard OCI-compatible images."

6.6. **"Configuration And Secrets" section** (line ~341): Change
"database and Redis connection strings, CORS settings, frontend API base
configuration, authentication settings"
to
"database and Redis connection strings, CORS settings, authentication
settings".

6.7. **Add "Repository Scope" section** after the reworked "API Routing"
section (or before "Health And Readiness"):

```markdown
### Repository Scope

This repository contains:
- All product specifications (including future UI specs in `docs/features/ui/`)
- The backend implementation (FastAPI, Celery workers, migrations)
- CI/CD pipelines for the backend

The frontend implementation will be developed in a dedicated repository
against the published OpenAPI contract once backend specifications are
implemented and tested. UI specifications remain here as the single
source of truth for product requirements.
```

### Step 7 — Update docs/deployment.md

7.1. **Prerequisites table** (line ~22): Remove the Node.js row:
`| Node.js | 20+ | Frontend build (development only) |`

7.2. **Local Development Quick Start** (lines ~108-109): Remove:
```
# Start the frontend dev server (separate terminal)
cd frontend && npm install && npm run dev
```

### Step 8 — Update docs/configuration.md

8.1. Find the `CORS_ORIGINS` row (line ~196) and change the description from
"Allowed CORS origins for the frontend" to "Allowed CORS origins for API
consumers".

### Step 9 — Update docs/system-map.md

9.1. **System Components diagram** (lines ~28-85): Remove the `frontend`
subgraph and the `SPA -->|"REST API (HTTP)"| API` edge. Remove the
`style frontend` line.

### Step 10 — Update .opencode/README.md

10.1. Remove `@ui-reviewer` row from the Subagents table (line ~53).

10.2. Update `@api-parity-reviewer` row in the Subagents table (line ~40):
change "Ensures the REST API provides at least the same operability as the
web UI" to "Verifies the REST API exposes all operations defined in feature
specifications".

10.3. Update `/run-tests` description in the Commands table (line ~64):
change "Run the full test suite (backend + frontend tests and linting)"
to "Run the full test suite (backend tests and linting)".

### Step 11 — Update .opencode/skills/new-feature/SKILL.md

11.1. Remove the entire `### Step 4: Implement the frontend` section
(lines 74-80).

11.2. In `### Step 5: Write tests` (which becomes Step 4), remove line 85:
`2. Frontend tests co-located with components or in frontend/tests/`

11.3. In `### Step 6: Review` (which becomes Step 5), remove line 91:
`2. If frontend changes were made, invoke @ui-reviewer`

11.4. Renumber the remaining steps (Step 5 -> Step 4, Step 6 -> Step 5).

11.5. In Step 1 (Write the specification), remove the `UI Requirements`
bullet from the "MUST include" list (line ~34):
`- **UI Requirements**: pages, components, user interactions`

11.6. In Step 1, in the domain list (line ~28), remove `ui` from the
comma-separated list of domains.

### Step 12 — Update .opencode/commands/run-tests.md

12.1. Change the description (line 2) from "Run the full test suite for
backend and frontend" to "Run the full test suite for the backend".

12.2. Remove step 2 (frontend tests):
```
2. Run frontend tests:
   cd frontend && npm test
```

12.3. Remove step 4 (frontend linting):
```
4. Run frontend linting:
   cd frontend && npm run lint
```

12.4. Renumber the remaining steps (3 -> 2, 5 -> 3).

### Step 13 — Update .opencode/commands/check-spec.md

13.1. Line 11: Change "frontend components" to remove the frontend
reference. Replace the full line:
`    frontend components)`
with:
`    tasks)`

### Step 14 — Update .opencode/prompts/code.md

14.1. **Scope section** (line ~17): Change
`- **Implementation files** (`backend/`, `frontend/`, `.github/`, `Dockerfile`,`
to
`- **Implementation files** (`backend/`, `.github/`, `Dockerfile`,`

14.2. **Reviewer Invocation section** (line ~117): Remove the line:
`- **New UI components** -> suggest `@ui-reviewer``

### Step 14b — Update .opencode/prompts/spec.md

14b.1. **Scope section** (line ~18): Change
`architecture, configuration, data sources, UI design system, deployment`
to
`architecture, configuration, data sources, deployment`

### Step 15 — Update .opencode/agents/ (multiple files)

15.1. **`docs-reviewer.md`** (line ~48): Change
`backend/app/services/ or page components in frontend/src/pages/`
to
`backend/app/services/`

15.2. **`docs-placement-reviewer.md`** (line ~35): Remove the line:
`- `docs/ui-design-system.md` — UI/UX rules`

15.3. **`test-reviewer.md`** (line ~11): Remove the permission line:
`"cd frontend && npm test *": allow`

15.4. **`test-reviewer.md`** (line ~41): Remove the checklist item:
`- Frontend: are components tested for rendering, user interaction, and edge cases?`

15.5. **`design-reviewer.md`** (lines ~147-148): Remove the `@ui-reviewer`
reference. Change the surrounding text to remove the parenthetical mention
(e.g., change "UI consistency: component usage and design system compliance
(covered by `@ui-reviewer`)" to "UI consistency: component usage and design
system compliance").

15.6. **`design-reviewer.md`** (lines ~144-145): Update the exclusion list
entry from "API-UI parity: whether the API matches UI capabilities (covered
by `@api-parity-reviewer`)" to "API completeness (covered by
`@api-parity-reviewer`)".

15.7. **`cicd.md`** (line ~12): Remove the permission line:
`"frontend/Dockerfile": allow`

15.8. **`cicd.md`** (line ~29): Remove `frontend/Dockerfile` from the
reference list of files the agent should check.

15.9. **`security-reviewer.md`** (lines ~125-134): Remove the entire
`### Frontend security` section (5 React-specific checklist items:
`dangerouslySetInnerHTML`, `localStorage` token storage, URL validation,
content escaping, SRI). These check non-existent frontend code.

15.10. **`spec-coherence-reviewer.md`** (line ~154): Change
`- API-UI parity (covered by @api-parity-reviewer)`
to
`- API completeness (covered by @api-parity-reviewer)`

15.11. **`spec-gap-analyzer.md`** (lines ~237-238): Change
`- **API-UI parity**: whether the API matches UI capabilities (covered by @api-parity-reviewer)`
to
`- **API completeness**: whether the API exposes all spec-defined operations (covered by @api-parity-reviewer)`

15.12. **`api-convention-reviewer.md`** (line ~135): Change
`- UI-API parity (that is for @api-parity-reviewer)`
to
`- API completeness (that is for @api-parity-reviewer)`

### Step 16 — Update .opencode/agents/api-parity-reviewer.md

16.1. Rework the agent description and role to align with the reworked
Guardrail 12. Change from "API-UI parity" language to "API completeness"
language. The agent now verifies that the API exposes all operations defined
in specifications (rather than comparing against a non-existent UI). Its
trigger changes from "after adding or modifying UI pages" to "after adding
or modifying API endpoints or feature specs that define operations".

Specifically:
- Frontmatter description: change "Reviews API-UI parity to ensure the
  REST API provides at least the same level of operability as the web UI"
  to "Reviews API completeness to ensure the REST API exposes all
  operations defined in feature specifications. Verifies that no operation
  is available only via CLI or background task without an API surface."
- Role section: replace "web UI" references with "feature specifications"
- Guiding principle: rework to focus on API-as-primary-interface without
  UI comparison

16.2. **Remove hardcoded frontend paths** (lines ~39, ~41): Remove or
rework the lines that reference `frontend/src/pages/`,
`frontend/src/components/`, and `frontend/src/api/`. These were used to
discover UI operations; replace with specification-based discovery (e.g.,
"Read all feature specs in `docs/features/` to identify defined operations
and verify each has an API endpoint").

16.3. **Remove frontend behavior check** (line ~90): Remove or rework the
check about "backed by documented API contracts, not left as implicit
frontend behavior".

### Step 17 — Update docs/features/platform/fetcher-infrastructure.md

17.1. Line ~17: Change "For the monitoring dashboard (API endpoints, frontend
pages, CLI diagnostics)" to "For the monitoring dashboard (API endpoints,
CLI diagnostics)".

17.2. Line ~1357: Change "The dashboard frontend (indirectly, via the list
endpoint)" to "The dashboard (indirectly, via the list endpoint)".

### Step 18 — Update docs/drafts/open-points.md

18.1. In OP-2 (Rate Limiting, lines ~109-110): Change "nginx already planned for
frontend/API routing" to "nginx or reverse proxy for API routing".

18.2. In OP-2 (lines ~123-124): Change "nginx is already in the stack for
frontend serving — may be sufficient" to "nginx or a reverse proxy is
already planned for API routing — may be sufficient".

### Step 19 — Update docs/reviews/ticket-references.md

19.1. Line 127: Change
`frontend rendering security is a cross-cutting concern to be addressed in ui-design-system.md during UI implementation per Guardrail 21 placement rules`
to
`frontend rendering security is a cross-cutting concern to be addressed in the UI repository's design system during UI implementation`

### Step 20 — Review and verify

20.1. Grep the entire `docs/`, `.opencode/` (including `prompts/`,
`commands/`, and all `agents/`), and project root for remaining references
to:
- `frontend/` (as a path — should find zero matches)
- `ui-design-system` (should find zero matches)
- `@ui-reviewer` (should find zero matches)
- `Guardrail 7` (should find only the tombstone in AGENTS.md)
- `API-UI parity` and `UI-API parity` (should find zero matches — all
  occurrences should have been updated to "API completeness")
- `\bfrontend\b` as a word in `docs/architecture.md`, `docs/deployment.md`,
  `docs/conventions.md`, `docs/configuration.md`, and `docs/system-map.md`
  (should find zero matches — catches prose references like "frontend
  implementation code" or "frontend builds" that are not path-formatted)

Note: `.github/workflows/` is intentionally excluded from this verification —
frontend references in CI workflow files are handled by the companion draft
`ci-cd-pruning.md`.

20.2. Verify that no broken cross-references exist (e.g., links to deleted
files).

20.3. Verify that the `new-feature` skill still reads coherently after step
renumbering.

20.4. Verify that the `run-tests` command still reads coherently after step
removal.

### Step 21 — Run reviewers

Invoke the following reviewers on the modified specs:

- `@spec-coherence-reviewer` on `AGENTS.md` (verifies guardrail consistency)
- `@docs-reviewer` on modified documentation files (`architecture.md`,
  `deployment.md`, `conventions.md`, `configuration.md`)
- `@docs-placement-reviewer` on any cross-cutting content that was
  consolidated or moved

### Step 22 — Delete this draft

Delete `docs/drafts/remove-frontend-scaffolding.md`.

---

## Decision Record

- **Frontend implementation**: will live in a dedicated repository (TBD)
- **UI specs**: remain in this repository under `docs/features/ui/` when written
- **Repository scope**: explicitly documented in `docs/architecture.md`
  (Repository Scope section) and `AGENTS.md` (Overview)
- **API-UI parity principle**: preserved as "API completeness" (Guardrail 12)
- **UI design system**: removed; will be recreated in the UI repository
- **`@ui-reviewer`**: removed; will be recreated in the UI repository
- **`@api-parity-reviewer`**: kept but reworked to verify API completeness
  against specifications (not against a UI implementation)
- **Semantic "frontend" references in feature specs**: intentionally kept
  unchanged — they describe client protocol/behavior, not implementation

## Execution Ordering

This draft is independent of the other two companion drafts:

- **`ci-cd-pruning.md`**: can be applied before, after, or simultaneously.
  It removes the frontend CI jobs independently of whether the frontend
  directory still exists. Note: if only this draft is applied without
  `ci-cd-pruning.md`, the `build-frontend` job in
  `.github/workflows/build-images.yml` will reference a deleted directory.
  Since all workflows are currently `workflow_dispatch`-only, this is a
  latent issue rather than a CI failure, but the companion draft should be
  applied in the same PR or immediately after
- **`tooling-external-contract-verification.md`**: fully independent (touches
  different files). Both drafts modify `.opencode/README.md` but in
  different table rows (removal of `@ui-reviewer` vs. addition of
  `@external-contract-verifier`)
