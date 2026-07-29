# Image Testing Setup

**Status**: Living document — prep effort, executed once, before
[`implementation-plan.md`](implementation-plan.md) Phase 0 begins.
**Purpose**: Establish the mechanism for testing the built Docker image
as a black-box artifact — distinct from the unit/integration/e2e tests
that already exercise the application code in-process. This document
defines the harness and gate; it does NOT enumerate every assertion the
suite will eventually contain — those grow phase by phase alongside
`implementation-plan.md` (see [Growth Model](#growth-model)).

## Contents

- [Why This Is a Separate Effort](#why-this-is-a-separate-effort)
- [Scope](#scope)
- [Current State](#current-state)
- [Deliverables](#deliverables)
  - [1. Full-Stack Compose File](#1-full-stack-compose-file)
  - [2. Image Smoke Test Suite](#2-image-smoke-test-suite)
  - [3. Runner Script](#3-runner-script)
  - [4. CI Gate](#4-ci-gate)
  - [5. Testing Strategy Documentation Update](#5-testing-strategy-documentation-update)
- [Growth Model](#growth-model)
- [Prerequisites](#prerequisites)
- [Out of Scope](#out-of-scope)
- [Definition of Done for This Prep Effort](#definition-of-done-for-this-prep-effort)
- [Cross-References](#cross-references)

---

## Why This Is a Separate Effort

The built container image can fail in ways that in-process tests
(`backend/tests/`, run via `uv run pytest` against the local venv) never
exercise: files not copied into the image (`alembic/`, `app/data/`,
`certs/`), missing OS-level binaries (`git` for the git worker), broken
entrypoint/`CMD`, non-root user permission issues, or process-role
startup failures (Celery UTC validation, redbeat lock sentinel,
`bootstrap_fetcher_configs()`) that only manifest when the actual image
runs as a container.

This is cross-cutting infrastructure used by every phase of
`implementation-plan.md`, and it gates the CI pipeline (no image should
be tagged `latest` or a semver tag if it fails its smoke test). Building
it once, upfront, as a bounded prep effort avoids mixing infrastructure
work into feature-phase work, and ensures the very first `latest` image
produced during Phase 0 is already protected by the gate.

## Scope

This effort delivers the **mechanism** and a **minimal assertion**
(the image builds, the container starts, and does not crash). It
explicitly does NOT deliver the full set of per-feature assertions
(health checks, worker startup, migrations, CLI, git worker) — those
are added incrementally as each corresponding phase in
`implementation-plan.md` introduces the underlying functionality. See
[Growth Model](#growth-model).

## Current State

- `docker-compose.yml` — PostgreSQL 16 + Redis 7 only. No application
  service is defined; nothing currently runs the built image locally.
- `backend/Dockerfile` — multi-stage, non-root, builds successfully;
  never executed as part of any automated test today.
- `.github/workflows/build-images.yml` — builds and **pushes
  immediately** on CI success (`latest`/`master` tags) or on `v*` tags
  (semver tags). No test step exists between build and push.
- `backend/tests/` — unit/integration/e2e tests only, all running
  in-process against the local venv (see `conftest.py`). No black-box
  container tests exist.
- `dev-env.sh` — already implements Docker/Podman runtime
  auto-detection (`detect_runtime()` → `COMPOSE_CMD`) for
  `docker-compose.yml`. `scripts/image-smoke.sh` (see
  [3. Runner Script](#3-runner-script)) reuses this same pattern rather
  than introducing a second, inconsistent detection mechanism.
- `RELEASE_TOKEN` repository secret — confirmed present (verified
  manually).

## Deliverables

### 1. Full-Stack Compose File

A new compose file — `docker-compose.app.yml` — that extends the
existing `docker-compose.yml` (postgres + redis) with application
services built from the local `backend/Dockerfile`, using the
process-role entrypoints defined in `docs/deployment.md` (Container
Images):

| Service | Entrypoint | Introduced when tested (phase) |
|---|---|---|
| `api` | `uvicorn app.main:app` | Phase 1 (health endpoints) |
| `worker` | `celery -A app.celery_app worker` | Phase 3 (fetcher framework) |
| `beat` | `celery -A app.celery_app beat` | Phase 3 (fetcher framework) |
| `git-worker` | `celery -A app.celery_app worker -Q git` | Phase 5 (git-based CVE fetchers) |
| `migrate` | `alembic upgrade head` (one-shot) | Phase 4 (real schema) |

All services use the same image (per `docs/architecture.md`, Single
Docker image, multiple entrypoints), built once per run via
`compose build`. The file is usable both locally (via
`scripts/image-smoke.sh`, or directly with `docker compose`/
`podman compose -f docker-compose.yml -f docker-compose.app.yml up`)
and in CI. See [3. Runner Script](#3-runner-script) for the
runtime-agnostic invocation used by automation.

**The file is created complete (all 5 services) at prep time.** This is
a deliberate choice: it is acceptable, and expected, that services
whose underlying code does not exist yet fail to start when exercised
at prep time — `worker`/`beat`/`git-worker` depend on `app.celery_app`,
which is not introduced until Phase 3; `migrate` has no real schema to
apply until Phase 4. Only `api` (and `migrate` as a no-op against an
empty schema) are expected to start successfully at prep time. This
is consistent with the [Growth Model](#growth-model): the compose
topology is complete from day one, but the corresponding smoke
assertions — and therefore the gate's ability to certify each service —
are added incrementally as each phase lands the underlying code. A
service failing to start before its phase does not block the prep
effort or the image's `latest` publication, since no smoke assertion
yet exercises that service.

**Environment variables**: every application service MUST receive, via
the compose file's `environment` block, the minimum configuration
`app/config.py` requires to start without crashing:

- `JWT_SECRET_KEY` — a fictional value of at least 32 characters
  (required; `Settings()` raises `ValueError` at import time otherwise
  — see `app/config.py`). Not a real secret; safe to commit as a fixed
  test value.
- `DATABASE_URL` — `postgresql+asyncpg://sentinel:sentinel@postgres:5432/sentinel`
  (hostname resolves via the compose network to the `postgres` service).
- `REDIS_URL` — `redis://redis:6379/0`.
- `CELERY_BROKER_URL` — `redis://redis:6379/1`.

Without these, the `api` container crashes immediately on startup
(config validation failure), which would make even the prep effort's
minimal assertion ("the container starts and stays up") fail for the
wrong reason.

**Readiness**: each application service defines a `healthcheck` (same
pattern already used by `postgres`/`redis` in `docker-compose.yml`).
The exact healthcheck command evolves with the Growth Model — initially
a process-liveness check (e.g., `CMD-SHELL` process probe), from Phase
1 onward `curl`/equivalent against `/health`.

### 2. Image Smoke Test Suite

A dedicated, black-box pytest suite under `backend/tests/image/`,
distinct from the in-process suite:

```
backend/tests/image/
  conftest.py            # HTTP client pointed at the running container
                          # (base URL from IMAGE_SMOKE_BASE_URL env var,
                          # default http://localhost:8000); helper for
                          # `docker compose exec` calls. Does NOT reuse
                          # the in-process db_session/client fixtures
                          # from backend/tests/conftest.py.
  test_image_build.py    # Minimal assertion (delivered by this prep
                          # effort): the image builds, the `api`
                          # container starts and stays up (no crash)
                          # within a bounded wait window.
```

**Marker**: all tests in this suite carry `@pytest.mark.image`. The
marker is registered in `backend/pyproject.toml` and **excluded from
the default test run**:

```toml
[tool.pytest.ini_options]
addopts = "-m 'not image'"
markers = [
    ...
    "image: Black-box container smoke tests (require Docker; excluded from default run)",
]
```

This ensures `cd backend && uv run pytest` (the command used throughout
`implementation-plan.md`'s Definition of Done) never attempts to start
containers. Image tests run exclusively via the runner script below,
which passes `-m image` explicitly.

**Coverage exclusion**: the `image` suite is a black-box suite running
against a separately-built artifact, not against the instrumented
local venv. It MUST NOT contribute to, nor be counted toward, the ≥85%
coverage threshold defined in `implementation-plan.md` (Definition of
Done). Since `uv run pytest` already excludes it by default (`-m 'not
image'`), and coverage is measured on that same default invocation,
this exclusion is automatic — stated here explicitly to prevent a
future change to the coverage command from accidentally pulling it in.

### 3. Runner Script

A single script — `scripts/image-smoke.sh` — used identically in local
development and in CI (no logic duplicated between the two).

**Runtime-agnostic**: the script MUST work with either Docker or
Podman, reusing the same detection pattern already implemented in
`dev-env.sh` (`detect_runtime()` → `COMPOSE_CMD`, preferring the native
`podman compose`/`docker compose` plugin, falling back to
`podman-compose`/`docker-compose` standalone). This keeps the script
consistent with the project's existing Podman-first local tooling — a
contributor is not required to install Docker locally. **CI uses
Docker** as its runtime (the GitHub Actions runner already has the
Docker daemon and `docker/build-push-action` available); the CI gate
in [4. CI Gate](#4-ci-gate) is Docker-specific by necessity, but it
invokes this same runtime-agnostic script for the smoke test step.

Steps:

1. `${COMPOSE_CMD} -f docker-compose.yml -f docker-compose.app.yml build`
2. `${COMPOSE_CMD} -f docker-compose.yml -f docker-compose.app.yml up -d --wait`
   — blocks until all started services report healthy per their
   `healthcheck` (see [1. Full-Stack Compose File](#1-full-stack-compose-file)),
   or fails after the compose-defined timeout. `--wait` is supported by
   both the Docker Compose plugin and the local Podman Compose plugin
   used in this project — no custom polling loop is implemented.
3. `cd backend && IMAGE_SMOKE_BASE_URL=http://localhost:8000 uv run pytest -m image tests/image/`
4. Capture the pytest exit code
5. `${COMPOSE_CMD} -f docker-compose.yml -f docker-compose.app.yml down -v` (always, even on failure)
6. Exit with the captured pytest exit code

This script is the single source of truth for "how to smoke-test the
image" — both a developer running it manually and the CI gate invoke
it identically.

### 4. CI Gate

Modify `.github/workflows/build-images.yml` (delegated to `@cicd` at
implementation time) so the image is **built locally first, tested,
and only pushed on success** — a blocking gate:

1. `docker/build-push-action` with `push: false, load: true` (builds
   the image **once**, into the local Docker daemon, does not push).
2. Run `scripts/image-smoke.sh` against the freshly built image (the
   script's `compose build` step should be skippable when the image is
   already loaded — the exact mechanism, e.g. an `--image-tag` argument
   or a pre-set `IMAGE` env var consumed by `docker-compose.app.yml`,
   is decided during implementation).
3. Only if step 2 exits 0: `docker push` the **exact same image
   loaded in step 1** (same digest) with the tags computed by
   `docker/metadata-action` — never a second `docker/build-push-action`
   build invocation.

**Build-once requirement**: the image tested in step 2 and the image
published in step 3 MUST be the same artifact (identical digest).
Re-running `docker/build-push-action` a second time to publish is
explicitly forbidden, even with layer caching enabled, because a
second build is not guaranteed to be bit-identical to the first
(cache misses, non-deterministic layers). A gate that certifies an
artifact it did not actually publish provides a false guarantee — this
would defeat the entire purpose of this effort. The implementation
must tag the image once, test that tag, then push that same tag/digest
directly (e.g., `docker push <image>:<tag>` against the already-built
local image, or an equivalent single-build-multiple-tag mechanism).

**This gate is blocking**: a failing smoke test prevents `latest` and
semver tags from ever being published. This decision was made
explicitly (confirmed with the user) over a non-blocking/informational
alternative, because an untested `latest` image is the exact failure
mode this effort exists to prevent.

**Trigger scope unchanged**: `build-images.yml`'s existing triggers
(`workflow_run` after CI succeeds on `master`, `push` on `v*` tags,
`workflow_dispatch`) are not modified by this effort. The gate wraps
the existing build job uniformly across all three trigger paths — no
per-trigger subset or reduced-scope variant is introduced. Note this
already means the full pipeline (and therefore the full smoke gate)
runs once per `master` merge (not per commit on a feature branch) plus
once per release tag — an acceptable frequency for a full-stack smoke
test.

### 5. Testing Strategy Documentation Update

`docs/features/platform/testing-strategy.md` is the authoritative home
for testing conventions (per `docs/conventions.md`, Testing
Conventions, and Guardrail 21 — this is cross-cutting information, not
specific to any single feature). This prep effort adds a new section
documenting the durable convention:

- Location: `backend/tests/image/`, one file per concern/role.
- Marker: `image`, excluded from the default `pytest` invocation.
- Execution: exclusively via `scripts/image-smoke.sh` (local and CI —
  same command).
- Growth rule: each phase in `implementation-plan.md` that introduces
  new container-observable behavior (a new endpoint, a new process
  role, a new startup validation) extends this suite with a
  corresponding assertion, as part of that phase's Definition of Done.

This section is added when this prep effort is implemented — it is the
authoritative record; this draft only plans it.

## Growth Model

The suite starts with a single minimal assertion and grows alongside
`implementation-plan.md`. This mapping is indicative — the owning phase
decides the exact assertions when it is implemented:

| Phase (in `implementation-plan.md`) | New assertion(s) added to `backend/tests/image/` |
|---|---|
| Prep (this effort) | `test_image_build.py`: image builds, `api` container starts, no crash |
| Phase 1 | `test_api_image.py`: `GET /health` and `GET /ready` return 200 |
| Phase 2 | `test_cli_image.py`: a `sentinel manage-user ...` command runs inside the container and exits 0 |
| Phase 3 | `test_worker_image.py`: `worker` and `beat` containers start and stay up; log lines confirm UTC/redbeat validation passed |
| Phase 4 | `test_migrations_image.py`: `migrate` one-shot service runs `alembic upgrade head` against the real schema and exits 0 |
| Phase 5 | `test_git_worker_image.py`: `git-worker` container has the `git` binary available and can clone a throwaway repository |

Each phase's Definition of Done (see `implementation-plan.md`) includes
a reference back to this table.

## Prerequisites

- `RELEASE_TOKEN` repository secret — **confirmed present** (verified
  manually before this document was written). Required for `v*` tags
  created by `release-please` to trigger `build-images.yml` at all
  (the default `GITHUB_TOKEN` does not trigger downstream workflows on
  tags it creates — see `docs/deployment.md`, Repository Secret).
- A container runtime with a Compose implementation supporting
  `up --wait` available in the local development environment — Docker
  or Podman, auto-detected by `scripts/image-smoke.sh` following the
  same pattern as `dev-env.sh`. No specific engine needs to be
  installed beyond what the developer already uses for
  `./dev-env.sh up`.
- Docker available in the GitHub Actions runner (already the case —
  `docker/build-push-action` is already in use). The CI gate is
  Docker-specific by design (see [4. CI Gate](#4-ci-gate)).

## Out of Scope

- Per-feature assertions beyond the minimal one — these belong to each
  phase's Definition of Done in `implementation-plan.md`, not to this
  prep effort.
- Kubernetes-specific testing (manifests do not exist yet — see
  `docs/architecture.md`, Design Constraints).
- Load/performance testing of the built image.
- Testing the `deploy-api-docs.yml` and `release-please.yml` workflows
  — out of scope for image testing specifically.

## Definition of Done for This Prep Effort

1. `docker-compose.app.yml` created complete (all 5 services) with
   `environment` wiring per [1. Full-Stack Compose File](#1-full-stack-compose-file)
   and per-service `healthcheck` definitions. Validated locally: `api`
   (and `migrate` as a no-op) start and pass their healthcheck against
   the current — near-empty — application. `worker`/`beat`/`git-worker`
   are expected to fail to start at this point (no `app.celery_app`
   until Phase 3) — this is documented as expected, not a defect.
2. `backend/tests/image/` created with `conftest.py` and
   `test_image_build.py`; `image` marker registered and excluded from
   the default `pytest` run; confirmed not to affect the coverage
   measurement.
3. `scripts/image-smoke.sh` created, executable, runtime-agnostic
   (Docker or Podman via `dev-env.sh`-style detection), and produces
   identical results when run locally and when invoked from a CI job.
4. `build-images.yml` modified (via `@cicd`) to build-once → load →
   test → push (same digest), with the gate confirmed blocking (a
   deliberately broken image fails the workflow before any push
   occurs — verified once during implementation, then reverted) and
   confirmed that the pushed image's digest matches the tested image's
   digest.
5. `docs/features/platform/testing-strategy.md` updated with the
   "Image / Container Smoke Testing" section.
6. `uv run pytest` (default invocation, no `-m image`) confirmed to
   skip the new suite entirely and not affect coverage.
7. Reviewed via `@cicd` (workflow changes) and `@docs-reviewer`
   (testing-strategy.md update), per the standard guardrails.

Only after this is done does `implementation-plan.md` Phase 0 begin.

## Cross-References

- `docs/drafts/implementation-plan.md` — the implementation plan this
  effort precedes and supports.
- `docs/features/platform/testing-strategy.md` — authoritative testing
  conventions (destination for the durable convention established
  here).
- `docs/deployment.md` — Container Images (process roles), Release
  Process (tagging, `RELEASE_TOKEN`).
- `docs/architecture.md` — Single Docker image, multiple entrypoints.
- `docs/conventions.md` — Testing Conventions (style rules).
