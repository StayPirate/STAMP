# RFC: Adopt `uv` for Python Dependency and Environment Management

## Status

Draft — awaiting review before implementation.

## Context

The local development machine previously relied on `pyenv` to manage
Python interpreter versions. `pyenv` has been removed from the
developer's dotfiles and machine (verified: `~/.config/pyenv/` no
longer exists, `pyenv` binary not found, no references remain in
`.zshenv`/`.zshrc`/`.zprofile`). As a direct consequence, the
`backend/.venv` virtual environment that was built against
`~/.config/pyenv/versions/3.12.13/bin/python3.12` is now broken (its
symlinked interpreter no longer exists on disk).

This is an opportunity to replace `pyenv` + `pip` with a single modern
tool — [`uv`](https://github.com/astral-sh/uv) (Astral) — for both
Python interpreter management and dependency management, and to make
this the standard, reproducible tooling for **every** developer who
clones this repository (not just a personal workstation fix).

## Problem Statement

Today, `backend/pyproject.toml` declares dependencies but there is no
lockfile. Every developer's `pip install -e ".[dev]"` can resolve
slightly different transitive dependency versions over time (pip's
resolver is not required to produce identical results run-to-run as
new package releases appear on PyPI). CI (`pip install -e ".[dev]"`)
has the same non-determinism. There is no single source of truth for
"exactly which dependency versions does this project run against."

Additionally, onboarding a new developer today requires: installing a
Python 3.13 interpreter manually (via a version manager or OS package),
creating a venv, and running `pip install -e ".[dev]"` — several manual
steps, each a potential source of drift or error.

## Decision

Adopt `uv` as the standard tool for:

1. Installing/managing the Python 3.13 interpreter used for local
   development (replacing `pyenv`)
2. Creating and syncing the project virtual environment (replacing
   `python -m venv` + `pip install`)
3. Producing a committed lockfile (`backend/uv.lock`) that pins exact
   dependency versions for reproducible installs across all
   developers, CI, and Docker builds
4. Running project-scoped commands (`uv run pytest`, `uv run alembic
   ...`) and ad-hoc pinned tools in CI (`uvx ruff@<version>`, `uvx
   bandit@<version>`, `uvx pip-audit@<version>`)

`uv` is NOT a hard requirement to contribute to this project — anyone
can still run `pip install -e ".[dev]"` manually against a Python 3.13
interpreter obtained some other way, because `backend/pyproject.toml`
remains a fully standard PEP 621 project file. `uv` is the
**recommended and CI-enforced** path because it is the only path that
guarantees the lockfile-pinned dependency versions.

This RFC does **not** introduce any new runtime dependency for the
deployed application — `uv` is a build-time/dev-time tool only. It is
not installed in the final Docker runtime image (see Step 6).

## Verified Versions and Sources

Verified on 2026-07-20 against the following authoritative sources:

| Item | Verified value | Source |
|------|-----------------|--------|
| `uv` latest release | `0.11.29` (2026-07-15) | https://github.com/astral-sh/uv/releases/latest |
| `astral-sh/setup-uv` latest release | `v8.3.2` (2026-07-08) | https://github.com/astral-sh/setup-uv/releases/latest |
| `uv export` supports `--no-hashes` and `--no-dev` flags | confirmed | https://docs.astral.sh/uv/reference/cli/#uv-export |
| `uv export` default format is `requirements.txt` | confirmed | https://docs.astral.sh/uv/reference/cli/#uv-export |
| `uv sync --no-dev` is an alias of `--no-group dev` (dev group included by default otherwise) | confirmed | https://docs.astral.sh/uv/reference/cli/#uv-sync |
| `uvx <package>@<version>` pins an exact tool version for an isolated ephemeral run | confirmed | https://docs.astral.sh/uv/reference/cli/#uv-tool-run |
| Official GitHub Actions integration pattern (`setup-uv` + `uv sync` + `uv run`) | confirmed | https://docs.astral.sh/uv/guides/integration/github/ |
| Official Docker multi-stage "non-editable install" pattern | confirmed | https://docs.astral.sh/uv/guides/integration/docker/#non-editable-installs |
| Official Docker "intermediate layers" caching pattern (`--no-install-project`) | confirmed | https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers |
| Distroless uv image for `COPY --from=` in Dockerfiles, pinned tag `ghcr.io/astral-sh/uv:0.11.29` | confirmed | https://docs.astral.sh/uv/guides/integration/docker/#installing-uv |

**Pinning strategy adopted in this RFC**: major-tag pinning for the
GitHub Action (`astral-sh/setup-uv@v8`), consistent with this
project's existing convention for other actions (`actions/checkout@v7`,
`actions/setup-python@v6`). The exact `uv` version is additionally
pinned via the action's `version:` input (`"0.11.29"`) and via the
Docker image tag (`ghcr.io/astral-sh/uv:0.11.29`), consistent with this
project's existing convention of exact-pinning ad-hoc CI tools (e.g.
`ruff==0.15.22`, `bandit==1.9.4`, `pip-audit==2.10.1`).

**No local `uv` version requirement**: this pin applies only to CI
(`setup-uv` downloads the exact version from GitHub Releases,
independent of any OS package manager) and to the Docker build (the
distroless `ghcr.io/astral-sh/uv` image is pulled directly from
`ghcr.io`). Developers are NOT required to install this exact `uv`
version locally — any reasonably recent `uv` (0.11.x or newer) that
can read/write the `uv.lock` format works for local development,
regardless of what version their OS distribution packages. `uv sync
--locked` in CI is what enforces lockfile-vs-pyproject.toml
consistency; it does not depend on which `uv` version produced the
lockfile.

## Current State (verified facts, 2026-07-20)

- `backend/pyproject.toml` declares `dependencies` under `[project]`
  and dev tooling under `[project.optional-dependencies] dev = [...]`
- No lockfile exists anywhere in the repository
- `backend/.venv` exists but is broken (interpreter symlink target
  removed along with pyenv)
- `.gitignore` already ignores `.venv/`, `venv/`, `env/` and does not
  contain any `*.lock` pattern (so a future `uv.lock` will not be
  accidentally ignored — no `.gitignore` change is required)
- `backend/.dockerignore` already excludes `tests/`, `.venv/`, caches,
  and `.env` — no change required there
- `.github/workflows/ci.yml` has three jobs (`backend-lint`,
  `backend-test`, `backend-security`), all currently using
  `actions/setup-python@v6` + `pip install`
- `.github/workflows/deploy-api-docs.yml` uses
  `actions/setup-python@v6` + `pip install -e .`. It triggers on
  `push: tags: ["v*"]` and `workflow_dispatch:` — both triggers MUST be
  preserved unchanged by this RFC (Step 5 modifies only the
  Python/dependency setup steps, not the `on:` block)
- `.github/workflows/build-images.yml` does **not** install Python
  dependencies directly — it only reads `backend/.python-version` to
  pass `--build-arg PYTHON_VERSION` to `docker/build-push-action`. It
  requires **no changes** in this RFC (verified by reading the file in
  full)
- `.github/workflows/release-please.yml` does not touch Python
  dependencies (out of scope, not modified)
- `backend/Dockerfile` is a two-stage build (`builder` + `runtime`)
  using bare `pip install --prefix=/install .`
- `.githooks/pre-commit` and `.githooks/pre-push` invoke `ruff` and
  `pytest` directly, assuming an activated venv (or globally available
  binaries) — both need a `uv run` prefix
- `docs/deployment.md` documents a "Quick Start" and a "Creating the
  First Local User" command that both assume a pre-activated venv with
  `alembic`/`uvicorn`/`celery`/`python` directly on `PATH`
- `AGENTS.md` "Commands" section lists bare `pytest`/`ruff`/`alembic`
  invocations assuming an activated venv
- `docs/conventions.md` (Runtime Version section) already generically
  lists `pyenv, uv, mise` as example local-dev tools that read
  `backend/.python-version` — this sentence remains accurate after
  this RFC and requires **no change** (uv is one of the tools listed;
  this RFC does not contradict it, it operationalizes it as the
  project's recommended choice via `docs/deployment.md`, per the
  reasoning in "Placement Decision" below)

## Placement Decision (Guardrail 21 self-check)

The rule "use `uv` for local development setup" is a local development
workflow instruction, not a version-policy fact. `docs/conventions.md`
(Runtime Version section) already owns the version-policy facts
(source of truth file, bump checklist) and correctly remains
tool-agnostic (it lists pyenv/uv/mise as interchangeable ways to honor
`.python-version`). The prescriptive "how to set up your local
environment" instructions belong in `docs/deployment.md`, which is
already the owning document for local development setup (it has a
"Local Development" section with "Quick Start"). This RFC adds the
`uv` recommendation there, not in `conventions.md`. No consolidation
or generalization is proposed; each document keeps its existing scope.

## Scope

### Files created

| File | Purpose |
|------|---------|
| `backend/uv.lock` | Committed lockfile — pins exact dependency versions |

### Files modified

| File | Nature of change |
|------|-------------------|
| `backend/pyproject.toml` | Rename `[project.optional-dependencies]` → `[dependency-groups]` |
| `.github/workflows/ci.yml` | Replace `actions/setup-python` + `pip` with `astral-sh/setup-uv` + `uv` in all three jobs |
| `.github/workflows/deploy-api-docs.yml` | Same replacement in the `build` job |
| `backend/Dockerfile` | Rewrite builder stage to use `uv sync` with non-editable, intermediate-layer caching pattern |
| `.githooks/pre-commit` | Prefix `ruff`/`pytest` invocations with `uv run` |
| `.githooks/pre-push` | Prefix `pytest` invocation with `uv run` |
| `docs/deployment.md` | Add `uv` to Software Requirements; update Quick Start and first-user creation commands |
| `AGENTS.md` | Update Commands section to use `uv run` / `uv sync` |

### Files explicitly NOT modified (verified, with rationale)

| File | Rationale |
|------|-----------|
| `.github/workflows/build-images.yml` | Does not install Python dependencies; only reads `.python-version` for the Docker build-arg. Unaffected by this RFC |
| `.github/workflows/release-please.yml` | Does not touch Python dependencies |
| `.gitignore` | Already correctly ignores `.venv/`; does not ignore lockfiles |
| `backend/.dockerignore` | Already correctly excludes `tests/`, `.venv/`, caches |
| `docs/conventions.md` | Runtime Version section remains accurate and tool-agnostic (see Placement Decision above) |
| `docs/configuration.md` | No environment variable is introduced or changed by this RFC |
| `backend/app/**`, `backend/tests/**` | No application or test code changes — this RFC is tooling-only |

## Out of Scope

- Migrating the frontend (not yet implemented) to any Node/JS package
  manager decision — unrelated to this RFC
- Changing the pinned exact versions of `ruff`, `bandit`, or
  `pip-audit` themselves — only the mechanism used to invoke them
  changes (from `pip install X==Y` to `uvx X@Y`)
- Introducing `uv` workspaces (this is a single-package project, not a
  multi-package workspace)
- Changing `backend/.python-version` (remains `3.13`)
- Adding `mise`/`asdf` support — `uv` alone is sufficient and is the
  tool decided upon in the prior discussion with the user
- Re-installing `pyenv` or providing any pyenv compatibility path

## Risk Assessment

**Low risk.** Justification:

- `uv` reads standard `pyproject.toml` — no proprietary format
  lock-in. If `uv` were ever abandoned, `pip install -e ".[dev]"`
  continues to work unchanged (both `[project.optional-dependencies]`
  and `[dependency-groups]` are read by pip ≥ 24.3; this project's CI
  and Dockerfile pin newer tool versions than that floor)
- No application runtime behavior changes — this is a build/dev
  tooling change only
- CI already exercises every path this RFC touches (lint, test,
  security scan, Docker build, API docs deploy) — any regression is
  caught immediately by the existing CI jobs
- The Docker image's final runtime stage does not contain `uv` at all
  (multi-stage build copies only the synced venv) — no change to the
  deployed artifact's attack surface

## Action Plan

Every step below is self-contained and specifies the **exact** final
file content (or exact diff) to apply. Steps must be applied in order
because later steps assume earlier ones are already in place (e.g. the
lockfile in Step 2 must exist before Step 4 references it in CI).

---

### Step 1: Modify `backend/pyproject.toml`

**Change**: rename the dependency group section header. This is the
only change to this file — dependency contents are unchanged.

Replace:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0.0",
    "httpx>=0.28.0",
    "ruff>=0.8.0",
    "factory-boy>=3.3.0",
    "testcontainers[postgres]>=4.9.0",
]
```

with:

```toml
[dependency-groups]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=6.0.0",
    "httpx>=0.28.0",
    "ruff>=0.8.0",
    "factory-boy>=3.3.0",
    "testcontainers[postgres]>=4.9.0",
]
```

**Rationale**: `[dependency-groups]` (PEP 735) is the modern standard
that `uv sync` treats specially — the `dev` group is included by
default on every `uv sync` invocation (excluded only with the explicit
`--no-dev` flag), whereas `[project.optional-dependencies]` extras
require an explicit `--extra dev` on every invocation. This
simplifies every downstream command in this RFC. No other section of
`pyproject.toml` changes.

---

### Step 2: Generate `backend/uv.lock`

**Command to run** (requires `uv` installed locally — already done by
the user per the conversation preceding this draft):

```bash
cd backend && uv lock
```

This reads the post-Step-1 `pyproject.toml` and produces
`backend/uv.lock`. **This file must be committed to the repository.**
It is a generated, deterministic, cross-platform TOML file (not
platform-specific) — do not hand-edit it; always regenerate via
`uv lock` or `uv sync` (which updates the lock automatically when
`pyproject.toml` changes and `--locked`/`--frozen` is not passed).

**Verification after running**: confirm the file exists at
`backend/uv.lock` and that `git status` shows it as untracked (ready to
be added in the final commit).

---

### Step 3: Verify `.gitignore` and `backend/.dockerignore` (no change)

**Verification only — no file modification.**

Confirm `backend/uv.lock` is NOT matched by any pattern in
`/home/crazybyte/Workspace/Sentinel/.gitignore`:

```bash
git check-ignore -v backend/uv.lock
```

**Expected result**: no output (exit code 1), meaning the file is not
ignored. If any pattern matches, STOP — do not proceed with Step 13
(commit) until the pattern is identified and excluded, since the
lockfile MUST be tracked in git.

Confirm `backend/.dockerignore` still excludes `.venv/` and `tests/`
(both already present per the Current State section above) — no
modification needed to this file.

---

### Step 4: Modify `.github/workflows/ci.yml`

Apply the following targeted changes. Do NOT replace the file
wholesale — every line not mentioned below (triggers, concurrency,
permissions, job names, `services:` blocks, `env:` blocks) remains
byte-for-byte unchanged.

**`backend-lint` job** — replace:

```yaml
      - uses: actions/setup-python@v6
        with:
          python-version-file: backend/.python-version
          cache: pip
          cache-dependency-path: backend/pyproject.toml
      - run: pip install ruff==0.15.22
      - run: ruff check .
      - run: ruff format --check .
```

with:

```yaml
      - uses: astral-sh/setup-uv@v8
        with:
          version: "0.11.29"
          enable-cache: true
      - run: uvx ruff@0.15.22 check .
      - run: uvx ruff@0.15.22 format --check .
```

**`backend-test` job** — replace:

```yaml
      - uses: actions/setup-python@v6
        with:
          python-version-file: backend/.python-version
          cache: pip
          cache-dependency-path: backend/pyproject.toml
      - run: pip install -e ".[dev]"
      - run: pytest -v --cov=app --cov-report=term-missing --cov-fail-under=85
      - name: Check Alembic migration drift
        run: |
          alembic upgrade head
          alembic check
```

with:

```yaml
      - uses: astral-sh/setup-uv@v8
        with:
          version: "0.11.29"
          enable-cache: true
      - run: uv sync --locked
      - run: uv run pytest -v --cov=app --cov-report=term-missing --cov-fail-under=85
      - name: Check Alembic migration drift
        run: |
          uv run alembic upgrade head
          uv run alembic check
```

**`backend-security` job** — replace:

```yaml
      - uses: actions/setup-python@v6
        with:
          python-version-file: backend/.python-version
          cache: pip
          cache-dependency-path: backend/pyproject.toml
      - run: pip install bandit==1.9.4 pip-audit==2.10.1
      - name: Run bandit (static analysis)
        run: bandit -r app/
      - name: Run pip-audit (dependency vulnerabilities)
        run: pip install -e . && pip-audit
```

with:

```yaml
      - uses: astral-sh/setup-uv@v8
        with:
          version: "0.11.29"
          enable-cache: true
      - name: Run bandit (static analysis)
        run: uvx bandit@1.9.4 -r app/
      - name: Run pip-audit (dependency vulnerabilities)
        run: uvx pip-audit@2.10.1 --requirement <(uv export --no-hashes --no-dev)
```

The "Verify Dockerfile Python version" step in `backend-lint` (first
step after checkout) is NOT modified — its logic is orthogonal to the
package manager.

**Changes explained**:

- `backend-lint`: `actions/setup-python` + `pip install ruff==0.15.22`
  replaced with `astral-sh/setup-uv` + `uvx ruff@0.15.22`. `uvx` runs
  the pinned tool in an isolated ephemeral environment without needing
  the project's own dependencies synced (ruff is a static analyzer,
  consistent with prior behavior which also never installed project
  deps in this job)
- `backend-test`: `pip install -e ".[dev]"` replaced with
  `uv sync --locked`. The `--locked` flag makes CI fail if
  `backend/uv.lock` is not in sync with `backend/pyproject.toml` — this
  is a **new** safety net that did not exist before (pip silently
  re-resolved every time). `pytest`/`alembic` invocations gain a
  `uv run` prefix to execute inside the synced project virtual
  environment
- `backend-security`: `pip install bandit==1.9.4 pip-audit==2.10.1`
  replaced with `uvx bandit@1.9.4` / `uvx pip-audit@2.10.1` — both
  pinned to the identical versions as before. Neither `bandit` nor
  `pip-audit` are added to `backend/pyproject.toml` as dependencies
  (consistent with the current state — they are ad-hoc CI tools, not
  project dependencies). `pip-audit` is fed the project's exact locked
  dependency set via process substitution:
  `<(uv export --no-hashes --no-dev)` generates a `requirements.txt`
  equivalent from `uv.lock` on the fly. This step requires no `uv
  sync` at all — `uv export` only reads `uv.lock` +
  `pyproject.toml`, and `bandit` only reads source files — both are
  faster than the previous `pip install -e .` step which needed a full
  dependency install just to give `pip-audit` visibility into resolved
  versions
- `JWT_SECRET_KEY` in the `backend-test` job env block is unchanged
  (still needed by `uv run alembic ...`, same as it was needed by the
  prior `alembic` invocation — see the env var alignment change
  applied earlier in this project's history)
- The "Verify Dockerfile Python version" step is unchanged — its logic
  (parsing `.python-version` and `Dockerfile`) is orthogonal to the
  package manager

---

### Step 5: Modify `.github/workflows/deploy-api-docs.yml`

Apply the following targeted change. Every other line (triggers —
including the existing `push: tags: ["v*"]`, which MUST be preserved
unchanged — permissions, concurrency, the `deploy` job, and the
Swagger UI generation logic) remains byte-for-byte unchanged.

Replace:

```yaml
      - uses: actions/setup-python@v6
        with:
          python-version-file: backend/.python-version
          cache: pip
          cache-dependency-path: backend/pyproject.toml

      - name: Install backend dependencies
        run: pip install -e .

      - name: Generate OpenAPI schema
        run: python scripts/generate_openapi.py > openapi.json
```

with:

```yaml
      - uses: astral-sh/setup-uv@v8
        with:
          version: "0.11.29"
          enable-cache: true

      - name: Install backend dependencies
        run: uv sync --locked --no-dev

      - name: Generate OpenAPI schema
        run: uv run python scripts/generate_openapi.py > openapi.json
```

**Changes explained**:

- `actions/setup-python` + `pip install -e .` replaced with
  `astral-sh/setup-uv` + `uv sync --locked --no-dev` (no dev
  dependencies needed to generate the OpenAPI schema)
- `python scripts/generate_openapi.py` gains a `uv run` prefix to
  execute inside the synced venv
- The `deploy` job, the Swagger UI generation logic, and the workflow
  `on:` triggers (`push: tags: ["v*"]` and `workflow_dispatch:`) are
  byte-for-byte unchanged

---

### Step 6: Modify `backend/Dockerfile`

Replace the **entire file** with:

```dockerfile
# syntax=docker/dockerfile:1

# Python version — must match backend/.python-version (CI drift-check enforces this)
ARG PYTHON_VERSION=3.13

# Stage 1: Install dependencies and build the project (non-editable)
FROM python:${PYTHON_VERSION}-slim AS builder

# Pin the uv version used to build this image (keep in sync with the
# `version:` input of astral-sh/setup-uv in .github/workflows/ci.yml)
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv

# Use the Python interpreter already present in this base image instead
# of letting uv download its own managed interpreter
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies only, in their own layer. This layer is
# invalidated only when pyproject.toml or uv.lock change — not on every
# application code change — which keeps rebuilds fast.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev --no-editable

# Copy application source and install the project itself (non-editable).
COPY app/ app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Stage 2: Runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

WORKDIR /app

# Install SUSE internal CA certificate for TLS connections to internal
# services (IBS, SMELT, AIMAAS, RabbitMQ)
COPY certs/SUSE_Trust_Root.crt /usr/local/share/ca-certificates/SUSE_Trust_Root.crt
RUN update-ca-certificates

# Copy the synced virtual environment (dependencies + project code,
# installed non-editable) from the builder stage. uv itself is not
# copied — it is not needed at runtime.
COPY --from=builder /app/.venv /app/.venv

# Copy application code needed at runtime (Alembic migrations run from
# this same image via a one-shot job — see docs/architecture.md,
# Database Migrations)
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

# Activate the virtual environment for all subsequent commands
ENV PATH="/app/.venv/bin:$PATH"

# Create non-root user
RUN useradd --create-home --no-log-init appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Changes explained**:

- `# syntax=docker/dockerfile:1` directive added — required to
  guarantee BuildKit's `--mount=type=cache` support regardless of the
  Docker daemon's default frontend version
- `COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv` installs the
  `uv` binary from Astral's official distroless image, pinned to the
  exact same version used in CI (`0.11.29`)
- `UV_PYTHON_DOWNLOADS=0` forces `uv` to use the Python interpreter
  already baked into the `python:${PYTHON_VERSION}-slim` base image
  rather than downloading its own managed interpreter — the base image
  tag (via `ARG PYTHON_VERSION`) remains the single source of truth for
  the runtime Python version, consistent with the existing "Dockerfile
  Convention" in `docs/conventions.md`
- `UV_LINK_MODE=copy` avoids noisy warnings/fallback behavior since the
  BuildKit cache mount (`/root/.cache/uv`) is a separate filesystem from
  the sync target
- The builder stage follows Astral's official "Intermediate layers" +
  "Non-editable installs" Docker patterns: dependencies are installed
  in a dedicated layer (`--no-install-project`) before the application
  source is copied, then the project itself is installed non-editable
  in a second `uv sync`. This maximizes Docker layer cache reuse — a
  change to `app/` no longer invalidates the (slow) dependency
  resolution/download layer
- The runtime stage no longer runs `pip install --prefix=/install .`
  (removed entirely) — it copies the already-built `.venv` directory
  verbatim from the builder stage via `COPY --from=builder
  /app/.venv /app/.venv`
- The runtime stage copies only `app/`, `alembic/`, and `alembic.ini`
  explicitly (previously `COPY . .` relied on `.dockerignore` to
  exclude `tests/`). This is a minor, deliberate improvement: the
  runtime image no longer contains `backend/scripts/` (CI/docs-only
  tooling) or any other non-runtime file, reducing image size and
  attack surface. `certs/` is still copied explicitly for the CA
  certificate step above it
- `RUN pip install --no-cache-dir hatchling` (previously needed to make
  the build backend available to `pip`) is removed entirely — `uv
  sync` transparently builds the local project using the
  `build-system.requires` declared in `pyproject.toml` (`hatchling`),
  installing it in an isolated, ephemeral build environment as needed

**Note on version coupling**: this Dockerfile pins `uv` to `0.11.29`
via the `COPY --from=ghcr.io/astral-sh/uv:0.11.29` line, and
`.github/workflows/ci.yml` pins the same version via
`astral-sh/setup-uv`'s `version: "0.11.29"` input. These two pins MUST
be kept in sync whenever `uv` is upgraded. This RFC does not propose
adding an automated drift-check for this (analogous to the existing
Python-version drift-check) — this is flagged as a follow-up
suggestion for the user to decide on separately, not a blocking issue
for this RFC.

---

### Step 7: Modify `.githooks/pre-commit`

Replace the **entire file** with:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Running pre-commit checks..."

echo "  ruff check..."
(cd backend && uv run ruff check .)

echo "  ruff format..."
(cd backend && uv run ruff format --check .)

echo "  unit tests..."
# Exit code 5 = "no tests collected" (no @pytest.mark.unit tests yet).
# Treat it as success so the hook doesn't block commits before the
# first unit test is added.
(cd backend && uv run pytest -m unit --no-header -q) || [ $? -eq 5 ]
```

**Changes explained**: `ruff check .`, `ruff format --check .`, and
`pytest -m unit --no-header -q` each gain a `uv run` prefix. This uses
the `ruff` version resolved from `backend/uv.lock` (currently
`ruff>=0.8.0` in the `dev` dependency group) — intentionally NOT the
CI-pinned `0.15.22` used via `uvx` in `ci.yml`. This distinction is
deliberate: local pre-commit hooks should use whatever the developer's
synced environment has (fast, no extra download), while CI enforces an
exact pinned version for determinism across all contributors' machines
and PRs. `set -euo pipefail` and the exit-code-5 handling are
unchanged.

---

### Step 8: Modify `.githooks/pre-push`

Replace the **entire file** with:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "Running pre-push checks..."

echo "  full test suite..."
# Exit code 5 = "no tests collected". Treat as success for the same
# reason as pre-commit (see pre-commit script comments).
(cd backend && uv run pytest --no-header -q) || [ $? -eq 5 ]
```

**Changes explained**: `pytest --no-header -q` gains a `uv run` prefix.
No other change.

---

### Step 9: Modify `docs/deployment.md`

**Modification A** — Software Requirements table. Replace:

```markdown
| Component | Minimum Version | Purpose |
|-----------|----------------|---------|
| Docker or Podman | Docker 24+ / Podman 4+ | Container runtime |
| PostgreSQL | 15+ | Primary database |
| Redis | 7+ | Session cache, Celery broker, rate limiting |
| Git | 2.25+ | Git-based CVE fetcher operations (git worker container only) |
| Python | 3.13+ | Backend runtime (development only; version policy in `docs/conventions.md`) |
```

with:

```markdown
| Component | Minimum Version | Purpose |
|-----------|----------------|---------|
| Docker or Podman | Docker 24+ / Podman 4+ | Container runtime |
| PostgreSQL | 15+ | Primary database |
| Redis | 7+ | Session cache, Celery broker, rate limiting |
| Git | 2.25+ | Git-based CVE fetcher operations (git worker container only) |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 0.11+ | Manages the Python 3.13 interpreter and all backend dependencies for local development (see "Quick Start" below). Development only |
```

Note: the standalone `Python 3.13+` row is removed — `uv` installs and
manages the pinned Python interpreter automatically (from
`backend/.python-version`), so operators no longer need to install
Python manually as a separate prerequisite. The version policy
reference to `docs/conventions.md` remains valid and is preserved via
the `uv` row's link to `backend/.python-version`'s role, unchanged in
that document.

**Modification B** — Quick Start. Replace:

```markdown
### Quick Start

```bash
# Start PostgreSQL + Redis containers
./dev-env.sh up

# Run database migrations
cd backend && alembic upgrade head

# Start the backend API server
cd backend && uvicorn app.main:app --reload --port 8000

# Start Celery worker (separate terminal)
cd backend && celery -A app.celery_app worker --loglevel=info

# Start Celery Beat scheduler (separate terminal)
cd backend && celery -A app.celery_app beat --loglevel=info
# Note: the redbeat scheduler class is configured in the Celery app
# settings (beat_scheduler). No --scheduler CLI flag is needed.
```
```

with:

```markdown
### Quick Start

```bash
# Install dependencies (downloads Python 3.13 and creates
# backend/.venv automatically if not already present)
cd backend && uv sync

# Start PostgreSQL + Redis containers
./dev-env.sh up

# Run database migrations
cd backend && uv run alembic upgrade head

# Start the backend API server
cd backend && uv run uvicorn app.main:app --reload --port 8000

# Start Celery worker (separate terminal)
cd backend && uv run celery -A app.celery_app worker --loglevel=info

# Start Celery Beat scheduler (separate terminal)
cd backend && uv run celery -A app.celery_app beat --loglevel=info
# Note: the redbeat scheduler class is configured in the Celery app
# settings (beat_scheduler). No --scheduler CLI flag is needed.
```
```

**Modification C** — Creating the First Local User. Replace:

```markdown
With SSO disabled (no SSO env vars), create a local admin user via CLI:

```bash
cd backend && python -m sentinel manage-user create \
  --username admin \
  --email admin@localhost \
  --role admin
```
```

with:

```markdown
With SSO disabled (no SSO env vars), create a local admin user via CLI:

```bash
cd backend && uv run python -m sentinel manage-user create \
  --username admin \
  --email admin@localhost \
  --role admin
```
```

No other section of `docs/deployment.md` is modified (in particular,
"Local Environment Variables", "Staging Deployment", and all later
sections are unaffected by this RFC).

---

### Step 10: Modify `AGENTS.md`

Replace the **entire "Commands" section**:

```markdown
## Commands

- **Backend tests**: `cd backend && pytest`
- **Backend lint**: `cd backend && ruff check . && ruff format --check .`
- **DB migrations**: `cd backend && alembic upgrade head`
- **New migration**: `cd backend && alembic revision --autogenerate -m "description"`
- **Local dev stack**: `./dev-env.sh up` (PostgreSQL + Redis, auto-detects Podman or Docker)
```

with:

```markdown
## Commands

- **Install/sync dependencies**: `cd backend && uv sync` (installs Python 3.13 and creates `.venv` automatically if needed)
- **Backend tests**: `cd backend && uv run pytest`
- **Backend lint**: `cd backend && uv run ruff check . && uv run ruff format --check .`
- **DB migrations**: `cd backend && uv run alembic upgrade head`
- **New migration**: `cd backend && uv run alembic revision --autogenerate -m "description"`
- **Local dev stack**: `./dev-env.sh up` (PostgreSQL + Redis, auto-detects Podman or Docker)
```

No other section of `AGENTS.md` is modified.

---

### Step 11: Verify no stale references remain

**Verification commands** (run after applying Steps 1–10):

```bash
cd /home/crazybyte/Workspace/Sentinel

# No workflow should install Python deps via bare pip anymore
grep -rn "pip install" .github/workflows/ci.yml .github/workflows/deploy-api-docs.yml
# Expected: no matches

# No workflow should use actions/setup-python for the backend jobs
# touched by this RFC
grep -n "actions/setup-python" .github/workflows/ci.yml .github/workflows/deploy-api-docs.yml
# Expected: no matches

# Git hooks must not invoke ruff/pytest without a uv run prefix
grep -nE "^\s*\(cd backend && (ruff|pytest)" .githooks/pre-commit .githooks/pre-push
# Expected: no matches (every invocation must read
# "(cd backend && uv run ruff ...)" or "(cd backend && uv run pytest ...)")

# The Dockerfile must not contain a bare pip install for the project
grep -n "pip install" backend/Dockerfile
# Expected: no matches

# deployment.md and AGENTS.md must not contain bare backend commands
# assuming an activated venv
grep -nE "cd backend && (pytest|ruff|alembic|uvicorn|celery|python -m sentinel)" docs/deployment.md AGENTS.md
# Expected: no matches (every invocation must be prefixed with "uv run")

# deploy-api-docs.yml must still retain its original tag trigger
grep -n 'tags: \["v\*"\]' .github/workflows/deploy-api-docs.yml
# Expected: exactly one match (the trigger must not be dropped)
```

**Expected result for all commands above**: no matches (empty output).
If any match is found, the corresponding file was not fully updated —
fix it before proceeding to Step 12.

---

### Step 12: Local smoke test

Run locally (the user has already installed `uv` on this machine per
the prior conversation):

```bash
cd /home/crazybyte/Workspace/Sentinel/backend
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -v
```

**Expected**: `uv sync` downloads Python 3.13 (if not already present
via `uv python install`) and creates a fresh `.venv`; all `ruff` checks
pass (no code changes were made to application code, so no new
violations are expected); all tests pass, matching the last known-good
state on `master` before `backend/.venv` was broken by the `pyenv`
removal.

If a Docker/Podman engine is available on this machine, additionally
verify the Dockerfile builds:

```bash
cd /home/crazybyte/Workspace/Sentinel/backend
docker build -t sentinel-backend:uv-test .
# or: podman build -t sentinel-backend:uv-test .
```

**Expected**: the build completes successfully through both stages.

---

### Step 13: Commit

Single commit with message:

```
chore: adopt uv for Python dependency and environment management

Replace pip + pyenv with uv (Astral) as the standard tool for Python
interpreter management, virtual environment creation, and dependency
resolution across local development, CI, and Docker builds.

- Add backend/uv.lock (committed lockfile, pins exact dependency
  versions for reproducible installs)
- Rename backend/pyproject.toml [project.optional-dependencies] to
  [dependency-groups] (PEP 735) so `uv sync` includes dev dependencies
  by default
- Replace actions/setup-python + pip with astral-sh/setup-uv + uv in
  all CI jobs (ci.yml, deploy-api-docs.yml). CI now enforces lockfile
  freshness via `uv sync --locked`
- Pin ad-hoc CI tools (ruff, bandit, pip-audit) via `uvx <tool>@<version>`
  instead of `pip install <tool>==<version>` — same exact versions,
  isolated from project dependencies
- Rewrite backend/Dockerfile to use uv's official multi-stage,
  non-editable install pattern with intermediate dependency-only
  layers for better build caching. uv itself is not present in the
  final runtime image
- Update .githooks/pre-commit and .githooks/pre-push to invoke
  ruff/pytest via `uv run`
- Update docs/deployment.md (Software Requirements, Quick Start,
  first local user creation) and AGENTS.md (Commands) to reflect the
  new uv-based workflow

No application runtime behavior changes. No new environment variables.
build-images.yml, release-please.yml, .gitignore, and
backend/.dockerignore require no changes (verified).
```

Files in commit:

- `backend/pyproject.toml`
- `backend/uv.lock` (new)
- `.github/workflows/ci.yml`
- `.github/workflows/deploy-api-docs.yml`
- `backend/Dockerfile`
- `.githooks/pre-commit`
- `.githooks/pre-push`
- `docs/deployment.md`
- `AGENTS.md`
- `docs/drafts/adopt-uv-tooling.md` (deleted — see Step 15)

---

### Step 14: Run reviewers

After the commit is applied, invoke the following reviewers to verify
the change was applied correctly and without issues:

1. **`@cicd`** on `.github/workflows/ci.yml`,
   `.github/workflows/deploy-api-docs.yml`, and `backend/Dockerfile` —
   verify the `uv`-based pipeline is correct, that
   `build-images.yml` truly requires no changes (re-verify the
   `PYTHON_VERSION` build-arg flow still works end-to-end with the new
   Dockerfile), and that no CI job silently lost a check that existed
   before (lint, test, coverage threshold, migration drift check,
   bandit, pip-audit)

2. **`@docs-reviewer`** on `docs/deployment.md` and `AGENTS.md` —
   verify the updated local development instructions are complete,
   internally consistent, and that no stale bare-command example
   remains anywhere in either file

3. **`@docs-placement-reviewer`** on `docs/deployment.md` — verify the
   `uv` recommendation is correctly placed there (per the Placement
   Decision section of this RFC) and does not need to be duplicated or
   moved to `docs/conventions.md`

If any reviewer identifies issues rated as "Needs revision", resolve
them before considering this RFC complete. Minor issues should be
fixed in the same PR/commit.

---

### Step 15: Delete this draft

Once Step 13 (commit) is applied and Step 14 (reviewers) confirms no
outstanding issues:

```bash
rm docs/drafts/adopt-uv-tooling.md
```

Include the deletion in the same commit as Step 13, or as a separate
follow-up `chore:` commit if the reviewers require revisions first
(i.e., do not delete the draft until reviewers have signed off).

---

## Open Questions / Follow-ups (not blocking this RFC)

1. **Version-coupling drift-check**: this RFC introduces two places
   where the exact `uv` version must match (`ci.yml`'s
   `setup-uv` `version:` input and the Dockerfile's
   `ghcr.io/astral-sh/uv:<version>` tag). Unlike the Python-version
   drift-check (which is CI-enforced), no automated check is proposed
   here. If the user wants one, a follow-up RFC could add a
   `backend-lint` step comparing the two values (analogous to the
   existing Python-version check), mirroring the "Dockerfile
   Convention" pattern in `docs/conventions.md`
2. **`docs/conventions.md` "Runtime Version" section**: this RFC
   deliberately leaves it unchanged (see Placement Decision). If, after
   this RFC lands, the team wants `conventions.md` to explicitly name
   `uv` as the project's endorsed tool (rather than one of several
   generic examples), that would be a separate, small follow-up change
   subject to its own Guardrail 21 self-check
