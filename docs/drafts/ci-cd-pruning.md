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
| `.github/workflows/ci.yml` | Remove frontend jobs; reactivate triggers |
| `.github/workflows/build-images.yml` | Remove frontend image job; reactivate triggers |
| `.github/workflows/deploy-api-docs.yml` | Reactivate triggers |
| `docs/deployment.md` | Add note about deploy workflows being deferred |
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
```

Remove the NOTE comment about specification phase.

3.2. **Remove frontend jobs**: Delete the following job blocks entirely:
- `frontend-lint` (lines ~69–84)
- `frontend-test` (lines ~86–100)
- `frontend-build` (lines ~102–116)
- `frontend-security` (lines ~135–150)

3.3. The remaining jobs are: `backend-lint`, `backend-test`,
`backend-security`. These stay unchanged.

### Step 4 — Modify build-images.yml

4.1. **Reactivate triggers**: Replace the current `on:` block (lines 1–9)
with:

```yaml
name: Build Docker Images

on:
  push:
    branches: [master]
    tags: ["v*"]
```

Remove the NOTE comment about specification phase.

4.2. **Remove frontend image job**: Delete the entire `build-frontend` job
block (lines ~48–79).

4.3. The remaining job is `build-backend`. It stays unchanged.

### Step 5 — Modify deploy-api-docs.yml

5.1. **Reactivate triggers**: Replace the current `on:` block (lines 1–13)
with:

```yaml
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

6.1. In the **Staging Deployment** section (around line ~149), after
"### Staging-Specific Notes", add a note:

```markdown
**Note**: the automated staging deployment workflow (`deploy-staging.yml`)
has been deferred until the deployment target (Kubernetes, Docker Compose
on VM, or cloud service) is decided. The current process is manual. When
the target is known, a deployment workflow will be created via the `@cicd`
agent.
```

6.2. In the **Production Deployment** section (around line ~237), in
"### Production-Specific Notes", change:
"Production is deployed manually from version tags (`v*`)"
to:
"Production is deployed manually from version tags (`v*`). An automated
deployment workflow will be added when the infrastructure target is decided."

### Step 7 — Verify remaining workflow references

7.1. Grep `.github/workflows/` to confirm only three files remain:
- `ci.yml`
- `build-images.yml`
- `deploy-api-docs.yml`

7.2. Grep `AGENTS.md` and `docs/` for references to `deploy-staging`,
`deploy-prod`, or the deleted workflow names. Update any stale references
found.

7.3. Verify that `ci.yml` no longer references `frontend/` in any path,
working-directory, or cache-dependency-path.

### Step 8 — Run reviewers

Invoke the following reviewers:

- `@cicd` agent to verify the modified workflows are correct and follow
  GitHub Actions best practices (trigger configuration, job dependencies,
  service containers)
- `@docs-reviewer` on `docs/deployment.md` to verify the added notes are
  coherent with the rest of the document

### Step 9 — Delete this draft

Delete `docs/drafts/ci-cd-pruning.md`.

---

## Decision Record

- **Deployment workflows**: deferred until infrastructure target is decided;
  will be created via `@cicd` agent at that time
- **CI triggers**: reactivated on push to `master` and on pull requests
- **Build triggers**: reactivated on push to `master` and on version tags
- **API docs**: reactivated on push to `master` when backend code changes
- **Versioning strategy**: confirmed as unified SemVer via git tags (`v*`);
  a single tag produces the backend image. Frontend image build is deferred
  to the future UI repository
- **docker-compose.yml and dev-env.sh**: NOT modified — they are correct
  and useful as-is (Postgres + Redis for local dev)
