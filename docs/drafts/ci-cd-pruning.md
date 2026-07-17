# Draft: CI/CD Pruning

## Summary

Remove speculative deployment workflows that contain only placeholder logic,
remove all frontend CI jobs (aligned with the frontend removal in the
companion draft), and reactivate the backend CI pipeline so it runs
automatically on push/PR. Keep the genuinely useful workflows (`ci.yml`
backend jobs, `build-images.yml` backend image, `deploy-api-docs.yml`).

## Rationale

1. **Deploy workflows are fiction**: `deploy-staging.yml` and
   `deploy-prod.yml` contain only `echo "TBD"` — the deployment target is
   explicitly undecided (`docs/architecture.md`: "The deployment target is
   not fixed at this stage"). Maintaining placeholder workflows creates
   false confidence and maintenance overhead
2. **Frontend jobs have no subject**: with the frontend directory removed
   (companion draft), CI jobs referencing `frontend/` would fail
   unconditionally
3. **Backend CI is the safety net**: it is well-structured (lint, test with
   real Postgres/Redis, coverage, security scan) and must be active from
   day one of implementation
4. **API docs deployment is real**: it generates the OpenAPI schema from
   actual code and publishes it — this is the API-first contract publication
   mechanism and should remain active
5. **Backend image build is real**: it produces the OCI image that will be
   used for deployment. Keeping it (with reactivated triggers) ensures
   images are built on every merge to master and on version tags

## Scope of Changes

### Files to DELETE

| Path | Reason |
|------|--------|
| `.github/workflows/deploy-staging.yml` | Placeholder only — `echo "TBD"` |
| `.github/workflows/deploy-prod.yml` | Placeholder only — `echo "TBD"` |

### Files to MODIFY

| Path | Nature of change |
|------|-----------------|
| `.github/workflows/ci.yml` | Remove frontend jobs; reactivate triggers (incl. `workflow_dispatch`) |
| `.github/workflows/build-images.yml` | Remove frontend image job; reactivate triggers as sequential with CI (`workflow_run`) |
| `.github/workflows/deploy-api-docs.yml` | Reactivate triggers |
| `docs/deployment.md` | Rewrite staging auto-deploy bullet as deferred; add note to production |
| `.opencode/agents/cicd.md` | Update pipeline chain (remove deleted workflow references) |
| `AGENTS.md` | No change needed (deployment docs already reference `docs/deployment.md`) |

---

## Action Plan

### Step 1 — Delete deploy-staging.yml

Delete `.github/workflows/deploy-staging.yml`.

### Step 2 — Delete deploy-prod.yml

Delete `.github/workflows/deploy-prod.yml`.

### Step 3 — Modify ci.yml

3.1. **Reactivate triggers**: Replace the current `on:` block (lines 1–11)
with:

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]
  workflow_dispatch:
```

Remove the NOTE comment about specification phase.

`workflow_dispatch` allows manual trigger via the GitHub UI or
`gh workflow run` CLI — useful for re-running CI on arbitrary branches
or retrying after transient failures.

3.2. **Remove frontend jobs**: Delete the following job blocks entirely:
- `frontend-lint` (lines ~69–84)
- `frontend-test` (lines ~86–100)
- `frontend-build` (lines ~102–116)
- `frontend-security` (lines ~135–150)

3.3. The remaining jobs are: `backend-lint`, `backend-test`,
`backend-security`. These stay unchanged.

### Step 4 — Modify build-images.yml

4.1. **Reactivate triggers (sequential with CI)**: Replace the current
`on:` block (lines 1–9) with:

```yaml
name: Build Docker Images

on:
  workflow_run:
    workflows: ["CI"]
    branches: [master]
    types: [completed]
  push:
    tags: ["v*"]
  workflow_dispatch:
```

Remove the NOTE comment about specification phase.

This makes the image build sequential with CI:

- **Push to master**: CI runs first; when CI completes successfully,
  `build-images.yml` starts automatically. If CI fails, the image is
  not built
- **Version tags (`v*`)**: build starts directly (tags are created from
  master which has already passed CI)
- **Manual dispatch**: build starts directly (operator decides
  consciously)

4.2. **Add CI success gate**: Add an `if` condition to the
`build-backend` job so it only runs when CI passed (or when triggered
by tag push / manual dispatch):

```yaml
jobs:
  build-backend:
    name: Build Backend Image
    if: >
      github.event_name != 'workflow_run' ||
      github.event.workflow_run.conclusion == 'success'
```

This is necessary because `workflow_run` with `types: [completed]`
fires on both CI success and CI failure. Without this condition, a
failed CI run would still trigger an image build.

4.3. **Remove frontend image job**: Delete the entire `build-frontend`
job block (lines ~48–79).

4.4. The remaining job is `build-backend`. Apart from the `if` condition
added in 4.2, it stays unchanged.

### Step 5 — Modify deploy-api-docs.yml

5.1. **Reactivate triggers**: Replace the current `on:` block (lines 1–13)
with:

```yaml
name: Deploy API Docs

on:
  push:
    branches: [master]
    paths:
      - "backend/app/**"
      - "backend/scripts/generate_openapi.py"
      - ".github/workflows/deploy-api-docs.yml"
  workflow_dispatch:
```

Remove the NOTE comment about specification phase.

5.2. The rest of the workflow stays unchanged (it is already correct and
functional for a backend-only project).

### Step 6 — Update docs/deployment.md

6.1. In the **Staging Deployment** section, under
"### Staging-Specific Notes", replace:

```
- Staging is auto-deployed from the `master` branch
```

with:

```
- Staging auto-deployment from the `master` branch is deferred until
  the deployment target (Kubernetes, Docker Compose on VM, or cloud
  service) is decided. The current process is manual. When the target
  is known, a deployment workflow will be created via the `@cicd` agent
```

6.2. In the **Production Deployment** section (around line ~231), in
"### Production-Specific Notes", change:
"Production is deployed manually from version tags (`v*`)"
to:
"Production is deployed manually from version tags (`v*`). An automated
deployment workflow will be added when the infrastructure target is decided."

### Step 7 — Update .opencode/agents/cicd.md

7.1. In the "Before making changes" section, update the pipeline dependency
chain from:

```
`ci.yml` → `build-images.yml` → `deploy-staging.yml` → `deploy-prod.yml`
```

to:

```
`ci.yml` → `build-images.yml`
```

7.2. In the "Conventions" section, update "Staging deploys automatically on
master merge" and "Production deploys require manual trigger and approval" to
indicate these are deferred until the deployment target is decided.

7.3. In the "Environments" section, update:
- `**staging**: auto-deploy from master` → indicate deferred
- `**prod**: manual deploy from tags \`v*\`` → stays as-is (production manual
  deploy from tags remains the intended model regardless of workflow existence)

### Step 8 — Verify remaining workflow references

8.1. Grep `.github/workflows/` to confirm only three files remain:
- `ci.yml`
- `build-images.yml`
- `deploy-api-docs.yml`

8.2. Grep `AGENTS.md`, `docs/`, and `.opencode/` for references to
`deploy-staging`, `deploy-prod`, or the deleted workflow names. Update any
stale references found.

8.3. Verify that `ci.yml` no longer references `frontend/` in any path,
working-directory, or cache-dependency-path.

### Step 9 — Run reviewers

Invoke the following reviewers:

- `@cicd` agent to verify the modified workflows are correct and follow
  GitHub Actions best practices (trigger configuration, job dependencies,
  service containers)
- `@docs-reviewer` on `docs/deployment.md` to verify the added notes are
  coherent with the rest of the document

### Step 10 — Delete this draft

Delete `docs/drafts/ci-cd-pruning.md`.

---

## Decision Record

- **Deployment workflows**: deferred until infrastructure target is decided;
  will be created via `@cicd` agent at that time
- **CI triggers**: reactivated on push to `master`, on pull requests, and
  via `workflow_dispatch` (manual)
- **Build triggers**: sequential with CI via `workflow_run` (only builds
  when CI passes); also triggered directly on version tags (`v*`) and via
  `workflow_dispatch` (manual)
- **API docs**: reactivated on push to `master` when backend code changes
- **Versioning strategy**: confirmed as unified SemVer via git tags (`v*`);
  a single tag produces the backend image. Frontend image build is deferred
  to the future UI repository
- **docker-compose.yml and dev-env.sh**: NOT modified — they are correct
  and useful as-is (Postgres + Redis for local dev)
- **docs/architecture.md**: NOT modified — the Environments section (line
  417: "Staging: auto-deployed from master branch") describes the intended
  deployment model, not a claim about a working workflow. The design intent
  has not changed (staging will be auto-deployed when the infrastructure
  target is decided). Operational reality is documented in
  `docs/deployment.md` (Step 6 adds an explicit "deferred" note there)
