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
| `AGENTS.md` | Remove frontend from structure, commands, content type map; remove Guardrail 7; rework Guardrail 12; remove `@ui-reviewer` from subagent table |
| `docs/conventions.md` | Remove "TypeScript (Frontend)" section; update timestamp convention to remove frontend-specific line |
| `docs/architecture.md` | Remove "Frontend (React SPA)" section; rework "Frontend And API Routing" to "API Routing"; update system overview |
| `docs/deployment.md` | Remove Node.js prerequisite; remove frontend dev server command |
| `docs/configuration.md` | Update CORS_ORIGINS description (remove "for the frontend") |
| `docs/system-map.md` | Remove frontend subgraph from System Components diagram |
| `opencode.json` | No change needed (does not reference frontend) |
| `.opencode/README.md` | Remove `@ui-reviewer` from subagent table |
| `.opencode/skills/new-feature/SKILL.md` | Remove Step 4 (Implement the frontend); remove frontend test step; remove `@ui-reviewer` invocation |
| `docs/drafts/open-points.md` | Remove or annotate frontend-related open points |

### Files NOT touched (context)

| Path | Reason left unchanged |
|------|----------------------|
| `docs/features/identity/authentication.md` | "Frontend session behavior" section documents API contract behavior (HttpOnly cookie, redirect logic) — this is API specification, not UI implementation |
| `docs/features/identity/sso-authentication.md` | "Frontend flow" section documents the OAuth2 redirect sequence — this is protocol specification, not UI implementation. Also currently WIP/disabled |
| `docs/features/identity/local-authentication.md` | "Frontend behavior" section references parent spec — same rationale as above |
| `docs/features/platform/fetcher-infrastructure.md` | Mentions "dashboard frontend" — will be reworded to "dashboard" (implementation-agnostic) |
| `docs/reviews/*.md` | Review findings are historical records; not modified |
| `docs/data-model.md` | "UI display note" is a data consumption hint for any client; kept |
| `docs/api-spec.md` | No frontend-specific content |

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

4.1. **Overview section** (line ~15): Change
`**Stack**: FastAPI (Python) + React (TypeScript) + PostgreSQL + Celery + Redis`
to
`**Stack**: FastAPI (Python) + PostgreSQL + Celery + Redis`

4.2. **Project Structure** (lines ~42–86): Remove ALL frontend-related entries:
- Remove `│   ├── ui-design-system.md      # UI design system`
- Remove `│   │   └── ui/                  # UI features`
- Remove the entire `├── frontend/` subtree (lines 73–85)

4.3. **Commands section** (lines ~93–95): Remove:
- `- **Frontend tests**: ...`
- `- **Frontend lint**: ...`
- `- **Frontend build**: ...`

4.4. **Guardrail 1** (line ~151): Change "Before writing or modifying ANY
implementation code (in `backend/` or `frontend/`)" to "Before writing or
modifying ANY implementation code (in `backend/`)".

4.5. **Content Type table** (lines ~181–198): Remove these rows:
- `| UI design system           | docs/ui-design-system.md          |`
- `| Reusable UI components     | frontend/src/components/ui/       |`
- `| Page-specific components   | frontend/src/components/          |`
- `| Page components            | frontend/src/pages/               |`
- `| React hooks                | frontend/src/hooks/               |`
- `| TypeScript types           | frontend/src/types/               |`
- `| API client code            | frontend/src/api/                 |`
- `| Frontend tests             | frontend/tests/                   |`

4.6. **Guardrail 5** (line ~234): Change "When modifying backend or frontend
dependencies" to "When modifying backend dependencies".

4.7. **Guardrail 6** (lines ~248, ~251): Remove frontend test references:
- Remove `- Frontend: vitest tests co-located with components or in frontend/tests/`
- Remove `- Frontend: cd frontend && npm test`

4.8. **Guardrail 7 (UI consistency)** — REMOVE ENTIRELY (lines ~270–287).
Renumber subsequent guardrails is NOT needed (guardrails are referenced by
number across all specs and prompts — changing numbers would break hundreds
of references). Instead, replace the content with a tombstone:

```markdown
### 7. [Reserved — UI consistency]

This guardrail will be reinstated when a frontend implementation exists.
The UI will be developed in a dedicated repository.
```

4.9. **Guardrail 12 (API-UI parity)** (lines ~416–443): Rework to remove
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

4.10. **Guardrail 21 cross-cutting document mapping table**: Change the row
`| Cross-cutting UI/UX rules | docs/ui-design-system.md |` to remove it
entirely (no replacement — the document no longer exists).

### Step 5 — Update docs/conventions.md

5.1. **General section, API-first bullet** (line ~10): Change "The web UI is
a consumer of the API. Every operation available through the UI must be
achievable through the API alone" to "Every operation must be achievable
through the API alone". Remove "The API may expose additional capabilities
not present in the UI, but the reverse is a defect".

5.2. **Timestamps & Timezones section** (lines ~180–182): Remove the two
frontend-specific lines:
- Remove `- **Frontend**: the UI converts UTC timestamps...`
- Remove `  equivalent). When submitting datetime values to the API, the frontend...`

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

6.1. **High-Level Architecture diagram** (lines ~23–85): Remove the
`React SPA` box and the `SPA -->|"REST API (HTTP)"| API` connection from
the ASCII diagram.

6.2. **Components section**: Remove the entire `### Frontend (React SPA)`
subsection (lines ~56–63).

6.3. **"Frontend And API Routing" section** (lines ~370–383): Rename to
`### API Routing` and rework to remove frontend container references:

Replace with:
```markdown
### API Routing

API endpoints are served under the `/api` path prefix. In production, a
reverse proxy or ingress routes `/api` requests to the backend service.
The API must remain independent of any specific frontend hosting strategy.
```

### Step 7 — Update docs/deployment.md

7.1. **Prerequisites table** (line ~22): Remove the Node.js row:
`| Node.js | 20+ | Frontend build (development only) |`

7.2. **Local Development Quick Start** (lines ~108–109): Remove:
```
# Start the frontend dev server (separate terminal)
cd frontend && npm install && npm run dev
```

### Step 8 — Update docs/configuration.md

8.1. Find the `CORS_ORIGINS` row (line ~196) and change the description from
"Allowed CORS origins for the frontend" to "Allowed CORS origins for API
consumers".

### Step 9 — Update docs/system-map.md

9.1. **System Components diagram** (lines ~28–85): Remove the `frontend`
subgraph and the `SPA -->|"REST API (HTTP)"| API` edge. Remove the
`style frontend` line.

### Step 10 — Update .opencode/README.md

10.1. Remove `@ui-reviewer` row from the Subagents table (line ~53).

### Step 11 — Update .opencode/skills/new-feature/SKILL.md

11.1. Remove the entire `### Step 4: Implement the frontend` section
(lines 74–80).

11.2. In `### Step 5: Write tests` (which becomes Step 4), remove line 85:
`2. Frontend tests co-located with components or in frontend/tests/`

11.3. In `### Step 6: Review` (which becomes Step 5), remove line 91:
`2. If frontend changes were made, invoke @ui-reviewer`

11.4. Renumber the remaining steps (Step 5 → Step 4, Step 6 → Step 5).

11.5. In Step 1 (Write the specification), remove the `UI Requirements`
bullet from the "MUST include" list (line ~34):
`- **UI Requirements**: pages, components, user interactions`

11.6. In Step 1, in the domain list (line ~28), remove `ui` from the
comma-separated list of domains.

### Step 12 — Update docs/drafts/open-points.md

12.1. In OP-2 (Rate Limiting, line ~110): Change "nginx already planned for
frontend/API routing" to "nginx or reverse proxy for API routing".

12.2. In OP-2 (line ~124): Change "nginx is already in the stack for
frontend serving — may be sufficient" to "nginx or a reverse proxy is
already planned for API routing — may be sufficient".

### Step 13 — Update docs/features/platform/fetcher-infrastructure.md

13.1. Line ~17: Change "For the monitoring dashboard (API endpoints, frontend
pages, CLI diagnostics)" to "For the monitoring dashboard (API endpoints,
CLI diagnostics)".

13.2. Line ~1357: Change "The dashboard frontend (indirectly, via the list
endpoint)" to "The dashboard (indirectly, via the list endpoint)".

### Step 14 — Update .opencode/agents/api-parity-reviewer.md

14.1. Rework the agent description and role to align with the reworked
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

### Step 15 — Review and verify

15.1. Grep the entire `docs/` and `.opencode/` directories for remaining
references to:
- `frontend/` (as a path)
- `ui-design-system`
- `@ui-reviewer`
- `Guardrail 7` (should find only the tombstone)

15.2. Verify that no broken cross-references exist (e.g., links to deleted
files).

15.3. Verify that the `new-feature` skill still reads coherently after step
renumbering.

### Step 16 — Run reviewers

Invoke the following reviewers on the modified specs:

- `@spec-coherence-reviewer` on `AGENTS.md` (verifies guardrail consistency)
- `@docs-reviewer` on modified documentation files (`architecture.md`,
  `deployment.md`, `conventions.md`, `configuration.md`)
- `@docs-placement-reviewer` on any cross-cutting content that was
  consolidated or moved

### Step 17 — Delete this draft

Delete `docs/drafts/remove-frontend-scaffolding.md`.

---

## Decision Record

- **Frontend implementation**: will live in a dedicated repository (TBD)
- **UI specs**: remain in this repository under `docs/features/ui/` when written
- **API-UI parity principle**: preserved as "API completeness" (Guardrail 12)
- **UI design system**: removed; will be recreated in the UI repository
- **`@ui-reviewer`**: removed; will be recreated in the UI repository
- **`@api-parity-reviewer`**: kept but reworked to verify API completeness
  against specifications (not against a UI implementation)

## Execution Ordering

This draft is independent of the other two companion drafts:

- **`ci-cd-pruning.md`**: can be applied before, after, or simultaneously.
  It removes the frontend CI jobs independently of whether the frontend
  directory still exists
- **`tooling-external-contract-verification.md`**: fully independent (touches
  different files). Both drafts modify `.opencode/README.md` but in
  different table rows (removal of `@ui-reviewer` vs. addition of
  `@external-contract-verifier`)
