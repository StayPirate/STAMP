# Draft: Python Version Convergence

**Status**: Draft — pending review before application  
**Created**: 2026-07-20  
**Scope**: Documentation (conventions, deployment, git-fetcher-infrastructure),
repository configuration (.gitignore, .python-version, pyproject.toml,
Dockerfile, CI workflows)

---

## Summary

This change converges all Python version references in the project from
the current inconsistent state (3.12 expressed in ~9 different styles
and locations) to a single authoritative source of truth targeting
**Python 3.13**, with a documented policy and bump checklist.

## Motivation

1. **Stale baseline**: Python 3.12 entered security-only maintenance in
   October 2025. It will receive no new bugfixes — only security
   patches until October 2028. Starting a greenfield project on a
   post-bugfix version wastes support runway.
2. **Semantic mismatch**: `requires-python = ">=3.12"` and the
   `docs/deployment.md` prerequisite "3.12+" claim compatibility with
   3.13/3.14, but CI only tests 3.12 and images only ship 3.12.
   Untested support is a latent bug.
3. **No single source**: the literal `3.12` is hand-copied into 9
   locations; a version bump requires editing all of them with the risk
   of drift.
4. **No governing specification**: no document owns the decision of
   which Python version the project targets or how to upgrade it. This
   is a spec gap for a specs-first project.

## Target Version Decision

**Python 3.13** — the most recent minor version with full dependency
support from all critical libraries.

| Dependency | Latest version | Declared Python support |
|---|---|---|
| celery | 5.6.3 | up to 3.13 (no 3.14 classifier) |
| pydantic | 2.13.4 | up to 3.14 |
| SQLAlchemy | 2.0.35+ | up to 3.13+ |
| FastAPI | 0.115+ | up to 3.13+ |
| asyncpg | 0.30+ | up to 3.13+ |

Python 3.14 (latest stable, EOL 2030) is blocked by Celery until it
officially declares support. The policy includes a "re-evaluate" trigger
for when that happens.

## Current State (before)

| Location | Current value |
|---|---|
| `backend/pyproject.toml:5` | `requires-python = ">=3.12"` |
| `backend/pyproject.toml:42` | `target-version = "py312"` |
| `backend/Dockerfile:2` | `FROM python:3.12-slim AS builder` |
| `backend/Dockerfile:12` | `FROM python:3.12-slim AS runtime` |
| `backend/.python-version` | `3.12.13` (gitignored — not committed) |
| `.github/workflows/ci.yml:28` | `python-version: "3.12"` |
| `.github/workflows/ci.yml:73` | `python-version: "3.12"` |
| `.github/workflows/ci.yml:93` | `python-version: "3.12"` |
| `.github/workflows/deploy-api-docs.yml:27` | `python-version: "3.12"` |
| `docs/deployment.md:22` | `Python \| 3.12+ \| Backend runtime (development only)` |
| `docs/features/platform/git-fetcher-infrastructure.md:378` | `The \`python:3.12-slim\` base image does not include git` |

## Target State (after)

| Location | New value | Role |
|---|---|---|
| `backend/.python-version` | `3.13` | **Single source of truth** (committed) |
| `backend/pyproject.toml:5` | `requires-python = ">=3.13"` | Compatibility floor (aligned) |
| `backend/pyproject.toml` | *(line removed)* | `target-version` removed — ruff infers from `requires-python` |
| `backend/Dockerfile` | `ARG PYTHON_VERSION=3.13` + parametric `FROM` | Reads from build-arg; default as fallback |
| `.github/workflows/ci.yml` | `python-version-file: backend/.python-version` (×3) | Reads source of truth |
| `.github/workflows/ci.yml` | New drift-check step | Guards Dockerfile default ↔ source of truth sync |
| `.github/workflows/deploy-api-docs.yml` | `python-version-file: backend/.python-version` | Reads source of truth |
| `.github/workflows/build-images.yml` | Reads `.python-version`, passes `build-args` | Feeds Dockerfile from source of truth |
| `docs/deployment.md:22` | `Python \| 3.13+ \| ...` | Updated prerequisite |
| `docs/features/platform/git-fetcher-infrastructure.md:378` | Version-agnostic reference | No hardcoded version in prose |
| `docs/conventions.md` | New "Runtime Version" subsection under `## Python (Backend)` | Policy + bump checklist |
| `.gitignore:15` | Negation pattern for `backend/.python-version` | Allows committing the source of truth file |

---

## Prescriptive Action Plan

Each step specifies the exact file, the exact text to find (old), and
the exact text to write (new). Steps are ordered by dependency — later
steps may depend on earlier ones.

---

### Step 1 — Add Python Runtime Version policy to `docs/conventions.md`

**File**: `docs/conventions.md`  
**Location**: Insert a new `### Runtime Version` subsection at the end
of the `## Python (Backend)` section — **after** the existing
`### Redis Error Handling` block (which ends at line 452) and **before**
`## CLI Conventions` (line 454).

**Text to insert** (between line 452 and line 454):

````markdown

### Runtime Version

Sentinel targets a single Python minor version for all runtime
components. The version is chosen based on: (1) active bugfix
maintenance status from python.org, and (2) declared support from all
critical dependencies (Celery in particular historically lags new
Python releases by 6–12 months).

**Current target**: Python **3.13** (bugfix maintenance, EOL 2029-10).

#### Source of Truth

The file `backend/.python-version` is the single source of truth for
the Python runtime version used across all environments:

| Consumer | How it reads the source of truth |
|---|---|
| Local development (pyenv, uv, mise) | Reads `backend/.python-version` natively |
| CI (`actions/setup-python`) | `python-version-file: backend/.python-version` |
| Dockerfile | `ARG PYTHON_VERSION=<value>` default; CI passes `--build-arg` from source of truth |
| ruff `target-version` | Inferred from `requires-python` in `pyproject.toml` (no explicit `target-version`) |

The `requires-python` field in `backend/pyproject.toml` MUST be kept
aligned with the source of truth (`>=3.<minor>` matching the minor in
`.python-version`). It serves as the package metadata floor — not as
the authoritative pin.

The `.python-version` file uses **minor-version granularity** (e.g.,
`3.13`, not `3.13.7`). This ensures the same value works directly as a
Docker image tag suffix, a `setup-python` specifier, and a pyenv/uv
prefix match. Patch-level reproducibility is captured by Docker image
digests and lockfiles, not by the version pin.

#### Dockerfile Convention

All Dockerfiles in the repository MUST use a global `ARG` for the
Python version:

```dockerfile
ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim AS builder
...
FROM python:${PYTHON_VERSION}-slim AS runtime
```

The default value MUST match `backend/.python-version`. A CI drift-check
step verifies this automatically — a mismatch fails the build.

#### Version Bump Checklist

When upgrading to a new Python minor version:

1. **Verify dependency support**: check PyPI classifiers and changelog
   for all critical dependencies. Priority order (historically slowest
   to adopt):
   - `celery` / `kombu` / `billiard` (task queue stack)
   - `asyncpg` (C extension, needs wheel)
   - `pydantic-core` (Rust extension, needs wheel)
   - `bcrypt` (C extension)
   - All other dependencies with C/Rust extensions
2. **Update the source of truth**: change `backend/.python-version` to
   the new minor (e.g., `3.14`).
3. **Align `requires-python`**: update `backend/pyproject.toml`
   `requires-python` to `>=3.<new-minor>`.
4. **Update Dockerfile default**: change `ARG PYTHON_VERSION=...` in
   `backend/Dockerfile` to match. (The drift-check will catch this if
   forgotten.)
5. **Run the full test suite locally** on the new interpreter. Pay
   attention to `DeprecationWarning` output.
6. **Temporary CI matrix** (optional but recommended): for the PR that
   bumps the version, add the old version alongside the new one in CI
   to confirm no regressions. Remove the old version after merge.
7. **Update documentation**: change the "Current target" line in this
   section and the prerequisite in `docs/deployment.md` (Software
   Requirements table).
8. **Update prose references**: search for hardcoded version strings in
   `docs/` (e.g., `python:3.13-slim` in spec prose) and update or make
   version-agnostic.
9. **Rebuild and test images**: build the Docker image with the new
   base, run smoke tests against it.
10. **Deploy**: staging first, observe for one cycle of all fetchers,
    then production.

#### Forward Compatibility (recommended, deferred)

To detect breakage early before the next bump:

- A scheduled/manual CI workflow (`workflow_dispatch` + weekly cron)
  that runs the test suite against the **next** Python version
  (including pre-releases). Failures are informational, not blocking.
- Periodic pytest runs with `-W error::DeprecationWarning` to surface
  deprecated API usage before it becomes a hard break.
- Renovate or Dependabot configured to auto-PR Docker base image
  updates, GitHub Actions version bumps, and pip dependency updates.

These are deferred to a future PR and not part of the current change.
````

**Rationale**: the `## Python (Backend)` section owns all Python-related
conventions (style, naming, frameworks, testing, Redis). Runtime version
policy naturally belongs here as a peer of those subsections — not under
`## Git Conventions > ### Versioning`, which is exclusively about the
application's SemVer lifecycle and git tags.

---

### Step 2 — Update `docs/deployment.md` prerequisite

**File**: `docs/deployment.md`  
**Line**: 22

**Old text**:
```
| Python | 3.12+ | Backend runtime (development only) |
```

**New text**:
```
| Python | 3.13+ | Backend runtime (development only; version policy in `docs/conventions.md`) |
```

---

### Step 3 — Update `docs/features/platform/git-fetcher-infrastructure.md` prose reference

**File**: `docs/features/platform/git-fetcher-infrastructure.md`  
**Line**: 378

**Old text**:
```
The `python:3.12-slim` base image does not include git — it must be
added explicitly to the container image.
```

**New text**:
```
The `python:<version>-slim` base image (where `<version>` is the
project's Python target — see `docs/conventions.md`, Python Runtime
Version) does not include git — it must be added explicitly to the
container image.
```

---

### Step 4 — Un-gitignore `backend/.python-version`

**File**: `.gitignore`  
**Line**: 15

**Old text**:
```
.python-version
```

**New text**:
```
.python-version
!backend/.python-version
```

**Explanation**: the generic `.python-version` pattern remains to ignore
any accidental `.python-version` files elsewhere (e.g., root of repo).
The negation `!backend/.python-version` explicitly allows committing
the SoT file.

---

### Step 5 — Create/update `backend/.python-version`

**File**: `backend/.python-version`

**Old content** (currently gitignored, local only):
```
3.12.13
```

**New content** (to be committed):
```
3.13
```

---

### Step 6 — Update `backend/pyproject.toml`

**File**: `backend/pyproject.toml`

**Change A** — Update `requires-python` (line 5):

Old:
```
requires-python = ">=3.12"
```

New:
```
requires-python = ">=3.13"
```

**Change B** — Remove `target-version` from ruff config (line 42):

Old:
```toml
[tool.ruff]
line-length = 88
target-version = "py312"
```

New:
```toml
[tool.ruff]
line-length = 88
```

**Explanation**: when `target-version` is absent, ruff infers it from
`project.requires-python`. This eliminates one more duplicated version
literal.

---

### Step 7 — Update `backend/Dockerfile`

**File**: `backend/Dockerfile`

**Old content** (full file):
```dockerfile
# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir hatchling

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install SUSE internal CA certificate for TLS connections to internal
# services (IBS, SMELT, AIMAAS, RabbitMQ)
COPY certs/SUSE_Trust_Root.crt /usr/local/share/ca-certificates/SUSE_Trust_Root.crt
RUN update-ca-certificates

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home --no-log-init appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**New content** (full file):
```dockerfile
# Python version — must match backend/.python-version (CI drift-check enforces this)
ARG PYTHON_VERSION=3.13

# Stage 1: Build dependencies
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir hatchling

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

WORKDIR /app

# Install SUSE internal CA certificate for TLS connections to internal
# services (IBS, SMELT, AIMAAS, RabbitMQ)
COPY certs/SUSE_Trust_Root.crt /usr/local/share/ca-certificates/SUSE_Trust_Root.crt
RUN update-ca-certificates

# Copy installed dependencies from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Create non-root user
RUN useradd --create-home --no-log-init appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### Step 8 — Update `.github/workflows/ci.yml`

**File**: `.github/workflows/ci.yml`

**Change A** — `backend-lint` job, replace `python-version` with
`python-version-file` (lines 27-28):

Old:
```yaml
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/pyproject.toml
```

New:
```yaml
      - uses: actions/setup-python@v6
        with:
          python-version-file: backend/.python-version
          cache: pip
          cache-dependency-path: backend/pyproject.toml
```

**Change B** — `backend-test` job, same replacement (lines 71-72):

Old:
```yaml
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/pyproject.toml
```

New:
```yaml
      - uses: actions/setup-python@v6
        with:
          python-version-file: backend/.python-version
          cache: pip
          cache-dependency-path: backend/pyproject.toml
```

**Change C** — `backend-security` job, same replacement (lines 91-92):

Old:
```yaml
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/pyproject.toml
```

New:
```yaml
      - uses: actions/setup-python@v6
        with:
          python-version-file: backend/.python-version
          cache: pip
          cache-dependency-path: backend/pyproject.toml
```

**Change D** — Add a drift-check step to the `backend-lint` job, after
the checkout step and before `setup-python`:

Insert after `- uses: actions/checkout@v7` (line 25) and before
`- uses: actions/setup-python@v6`:

```yaml
      - name: Verify Dockerfile Python version matches .python-version
        run: |
          EXPECTED=$(cat backend/.python-version)
          ACTUAL=$(grep -oP '(?<=ARG PYTHON_VERSION=)\S+' backend/Dockerfile)
          if [ "$EXPECTED" != "$ACTUAL" ]; then
            echo "::error::Dockerfile ARG PYTHON_VERSION=$ACTUAL does not match backend/.python-version=$EXPECTED"
            exit 1
          fi
```

---

### Step 9 — Update `.github/workflows/deploy-api-docs.yml`

**File**: `.github/workflows/deploy-api-docs.yml`

**Old** (lines 25-28):
```yaml
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/pyproject.toml
```

**New**:
```yaml
      - uses: actions/setup-python@v6
        with:
          python-version-file: backend/.python-version
          cache: pip
          cache-dependency-path: backend/pyproject.toml
```

---

### Step 10 — Update `.github/workflows/build-images.yml`

**File**: `.github/workflows/build-images.yml`

**Old** (lines 50-59):
```yaml
      - uses: docker/setup-buildx-action@v4

      - uses: docker/build-push-action@v7
        with:
          context: backend
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

**New**:
```yaml
      - name: Read Python version from .python-version
        id: python-version
        run: echo "version=$(cat backend/.python-version)" >> "$GITHUB_OUTPUT"

      - uses: docker/setup-buildx-action@v4

      - uses: docker/build-push-action@v7
        with:
          context: backend
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          build-args: PYTHON_VERSION=${{ steps.python-version.outputs.version }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

---

### Step 11 — Verification: run linter and tests

After all file changes are applied:

1. Run `ruff check .` and `ruff format --check .` in `backend/` to
   verify that removing `target-version` does not change ruff's
   behavior (it should infer `py313` from `requires-python = ">=3.13"`).
2. Run `pytest` in `backend/` to verify no test breakage.
3. Verify `docker build backend/` succeeds with the new Dockerfile
   (requires Python 3.13 base image to be pullable).

---

### Step 12 — Review: invoke relevant reviewers

After all changes from Steps 1–10 are applied and Step 11 passes,
invoke the following reviewers on the modified files:

| Reviewer | Target | Purpose |
|---|---|---|
| `@docs-placement-reviewer` | `docs/conventions.md` | Verify the new "Python Runtime Version" subsection is correctly placed and does not belong elsewhere (Guardrail 21) |
| `@docs-reviewer` | `docs/conventions.md`, `docs/deployment.md`, `docs/features/platform/git-fetcher-infrastructure.md` | Verify documentation completeness and coherence after the changes (Guardrail 9) |
| `@spec-coherence-reviewer` | `docs/conventions.md` | Verify no contradictions introduced between the new policy and existing specs (Guardrail 15) |
| `@cicd` | `.github/workflows/ci.yml`, `.github/workflows/deploy-api-docs.yml`, `.github/workflows/build-images.yml`, `backend/Dockerfile` | Verify CI/CD changes are correct and consistent (Guardrail 5) |

If any reviewer identifies issues rated "Needs revision", address them
before considering the change complete.

---

### Step 13 — Delete this draft

**File**: `docs/drafts/python-version-convergence.md`  
**Action**: Delete.

This draft has served its purpose as a review artifact. Once the plan is
fully applied and reviewers have confirmed correctness, it must be
removed — draft documents are transient planning tools, not permanent
specifications.

---

## Files Modified (summary)

| File | Type of change |
|---|---|
| `docs/conventions.md` | New subsection added |
| `docs/deployment.md` | One table row updated |
| `docs/features/platform/git-fetcher-infrastructure.md` | One sentence made version-agnostic |
| `.gitignore` | One negation pattern added |
| `backend/.python-version` | Content changed + committed (was gitignored) |
| `backend/pyproject.toml` | `requires-python` updated; `target-version` removed |
| `backend/Dockerfile` | `ARG` added; `FROM` lines parametrized |
| `.github/workflows/ci.yml` | 3× `python-version-file`; drift-check step added |
| `.github/workflows/deploy-api-docs.yml` | 1× `python-version-file` |
| `.github/workflows/build-images.yml` | Python version read step + `build-args` added |

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| A dependency fails on Python 3.13 at runtime despite declaring support | Low (3.13 is 9 months old, widely adopted) | Full test suite covers this; revert to 3.12 if critical failure found |
| ruff behavior changes when inferring target from `requires-python` vs explicit `py312` | Very low (documented ruff behavior) | Step 11 verifies lint output is unchanged |
| `python-version-file` not supported by older `actions/setup-python` | None (`python-version-file` available since v4, project uses v6) | N/A |
| Docker `ARG` before `FROM` breaks layer caching | None (Docker BuildKit handles this correctly) | N/A |
| pyenv users cannot use minor-only `.python-version` | Very low (pyenv matches by prefix since v2.0) | Document in policy; users can `pyenv install 3.13` |

## Out of Scope (deferred to future PRs)

- Renovate / Dependabot configuration for automated dependency updates
- Scheduled forward-compatibility CI workflow (next Python + pre-releases)
- Migration to `uv` for unified interpreter and dependency management
- Upgrade to Python 3.14 (blocked by Celery; re-evaluate when Celery
  declares 3.14 support)
