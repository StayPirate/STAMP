# Deployment Guide

Operational guide for deploying and configuring Sentinel across
environments: local development, staging, and production.

For environment variable reference, see `docs/configuration.md`.
For architectural decisions and design constraints, see
`docs/architecture.md`.

## Contents

- [Prerequisites](#prerequisites)
  - [Software Requirements](#software-requirements)
  - [Network Access (Staging/Production)](#network-access-stagingproduction)
- [External Service Registration](#external-service-registration)
  - [IdP Client Registration (id.suse.com)](#idp-client-registration-idsusecom)
- [Environments](#environments)
  - [Local Development](#local-development)
  - [Staging Deployment](#staging-deployment)
  - [Production Deployment](#production-deployment)
- [Release Process](#release-process)
  - [How It Works](#how-it-works)
  - [Creating a Release](#creating-a-release)
  - [Squash Merge](#squash-merge)
  - [Changelog](#changelog)
  - [Pipeline Chain](#pipeline-chain)
  - [Version Locations](#version-locations)
  - [Image Tag Semantics](#image-tag-semantics)
  - [API Documentation Publication](#api-documentation-publication)
  - [Container Image Retention](#container-image-retention)
  - [Configuration Files](#configuration-files)
  - [Repository Secret](#repository-secret)
- [Process Architecture](#process-architecture)
  - [Container Images](#container-images)
  - [Singleton Processes](#singleton-processes)
  - [Startup Ordering](#startup-ordering)
  - [Git Worker Volume](#git-worker-volume)
  - [Timezone and Locale Requirements](#timezone-and-locale-requirements)
  - [Clock Synchronization](#clock-synchronization)
- [Operations](#operations)
  - [Database Migrations](#database-migrations)
  - [CLI Operational Access](#cli-operational-access)
  - [Health Checks](#health-checks)
  - [Redis Durability, Memory, and Persistence](#redis-durability-memory-and-persistence)
  - [Log Aggregation](#log-aggregation)
  - [Image Vulnerability Monitoring](#image-vulnerability-monitoring)
  - [Python Forward-Compatibility Check](#python-forward-compatibility-check)
  - [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Software Requirements

| Component | Minimum Version | Purpose |
|-----------|----------------|---------|
| Docker or Podman | Docker 24+ / Podman 4+ | Container runtime |
| PostgreSQL | 15+ | Primary database |
| Redis | 7+ | Session cache, Celery broker, rate limiting |
| Git | 2.25+ | Git-based CVE fetcher operations (git worker container only) |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 0.11+ | Manages the Python 3.13 interpreter and all backend dependencies for local development (see "Quick Start" below). Development only |
| [shellcheck](https://www.shellcheck.net/) | match `ci.yml` (shell-lint) | Optional, development only — lints shell scripts via the pre-commit hook; CI enforces regardless. See `docs/conventions.md` (Shell Scripting) |
| [shfmt](https://github.com/mvdan/sh) | match `ci.yml` (shell-lint) | Optional, development only — formats shell scripts via the pre-commit hook. Match the CI version to avoid formatting drift. See `docs/conventions.md` (Shell Scripting) |
| [actionlint](https://github.com/rhysd/actionlint) | match `ci.yml` (shell-lint) | Optional, development only — validates GitHub Actions workflows locally before pushing. See `docs/conventions.md` (Shell Scripting) |
| [gitleaks](https://github.com/gitleaks/gitleaks) | any recent release | Optional, development only — scans staged changes for secrets via the pre-commit hook. Local-only; no CI job performs secret scanning. See `docs/features/platform/testing-strategy.md` (Pre-Commit Hooks) |

### Network Access (Staging/Production)

Sentinel requires outbound access to:

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| SUSE IdP | `id.suse.com` | 443 | SSO authentication (OIDC) |
| IBS API | `api.suse.de` | 443 | Build service integration |
| IBS RabbitMQ | `rabbit.suse.de` | 5671 | Real-time event consumption |
| SMELT | `smelt.suse.de` | 443 | Product/package data |
| AIMAAS | `aimaas.suse.de` | 443 | Product lifecycle, CVSS thresholds |
| NVD | `services.nvd.nist.gov` | 443 | CVE data |
| GitHub | `github.com` | 443 | MITRE cvelistV5 repository clone/fetch |
| git.kernel.org | `git.kernel.org` | 443 | Linux kernel vulnerability repo clone/fetch |

---

## External Service Registration

### IdP Client Registration (id.suse.com)

Before SSO authentication works, you must register Sentinel as an OIDC
client on the SUSE identity provider (`id.suse.com`).

#### Steps

1. Request a new OIDC client registration (contact the IdP
   administrators or use the self-service portal if available)
2. Provide the following client configuration:
   - **Client type**: Confidential (server-side application)
   - **Grant type**: Authorization Code
   - **Scopes**: `openid profile email`
   - **Redirect URIs** (register all environments that will use this
     client):
     - `https://sentinel.suse.de/auth/callback` (production)
     - `https://sentinel-staging.suse.de/auth/callback` (staging)
     - `http://localhost:5173/auth/callback` (local development)
3. After registration, you will receive:
   - `client_id` — set as `SSO_CLIENT_ID`
   - `client_secret` — set as `SSO_CLIENT_SECRET`

#### Environment-Specific Configuration

Each Sentinel instance must set `SSO_REDIRECT_URI` to the value matching
its environment:

| Environment | `SSO_REDIRECT_URI` |
|-------------|-------------------|
| Production | `https://sentinel.suse.de/auth/callback` |
| Staging | `https://sentinel-staging.suse.de/auth/callback` |
| Local dev | `http://localhost:5173/auth/callback` |

The IdP validates that the `redirect_uri` in each authorization request
matches one of the registered URIs. A mismatch causes the IdP to reject
the request with a `redirect_uri_mismatch` error.

All environments share the same `SSO_CLIENT_ID` and `SSO_CLIENT_SECRET`
— only `SSO_REDIRECT_URI` differs.

---

## Environments

### Local Development

#### Quick Start

```bash
# Install dependencies (downloads Python 3.13 and creates
# backend/.venv automatically if not already present)
cd backend && uv sync

# (Optional) Enable the repository's local git hooks for fast pre-commit
# feedback. Scoped to this repo via --local; does not affect other repos.
# See docs/features/platform/testing-strategy.md (Pre-Commit Hooks).
git config --local core.hooksPath .githooks

# Start PostgreSQL + Redis containers
./scripts/dev-env.sh up

# Run database migrations
cd backend && uv run alembic upgrade head

# Start the backend API server
cd backend && uv run uvicorn app.main:app --reload --port 8000

# Start Celery worker (separate terminal)
cd backend && uv run celery -A app.celery_app worker

# Start Celery Beat scheduler (separate terminal)
cd backend && uv run celery -A app.celery_app beat
# Note: the redbeat scheduler class is configured in the Celery app
# settings (beat_scheduler). No --scheduler CLI flag is needed.
```

#### Local Environment Variables

Create `backend/.env` for local development:

```bash
# Required (app refuses to start without this)
JWT_SECRET_KEY=local-development-secret-minimum-32-characters

# Connection settings (defaults work with scripts/dev-env.sh)
DATABASE_URL=postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1

# SSO (optional for local — omit to disable SSO)
SSO_ISSUER_URL=https://id.suse.com
SSO_CLIENT_ID=<your-client-id>
SSO_CLIENT_SECRET=<your-client-secret>
SSO_REDIRECT_URI=http://localhost:5173/auth/callback

# CORS
CORS_ORIGINS=http://localhost:5173

# Debug
DEBUG=true
```

#### Creating the First Local User

With SSO disabled (no SSO env vars), create a local admin user via CLI:

```bash
cd backend && uv run python -m sentinel manage-user create \
  --username admin \
  --email admin@localhost \
  --role admin
```

### Staging Deployment

#### Configuration Checklist

| Setting | Value | Notes |
|---------|-------|-------|
| `DATABASE_URL` | Staging PostgreSQL connection string | Dedicated staging DB |
| `JWT_SECRET_KEY` | Unique per environment (>= 32 chars) | Never reuse across environments |
| `REDIS_URL` | Staging Redis instance | |
| `SSO_ISSUER_URL` | `https://id.suse.com` | Same IdP for all environments |
| `SSO_CLIENT_ID` | Same as production | Single client registration |
| `SSO_CLIENT_SECRET` | Same as production | Single client registration |
| `SSO_REDIRECT_URI` | `https://sentinel-staging.suse.de/auth/callback` | Must match IdP registration |
| `CORS_ORIGINS` | `https://sentinel-staging.suse.de` | |
| `DEBUG` | `false` | Never enable debug in staging |
| `IBS_API_URL` | `https://api.suse.de` | |
| `IBS_USERNAME` / `IBS_PASSWORD` | Service account credentials | |

#### Deployment Steps

1. **Database**: ensure PostgreSQL is running and accessible
2. **Redis**: ensure Redis is running and accessible
3. **Run migrations** (one-shot, before starting API):
   ```bash
    docker run --rm --env-file .env sentinel:latest \
     alembic upgrade head
   ```
4. **Start all runtime processes** defined in
   [Container Images](#container-images) — each as a separate
   container/process
5. **Verify health**:
   - `GET /health` — liveness (API process is running)
   - `GET /ready` — readiness (PostgreSQL + Redis reachable)
6. **Verify SSO**: navigate to the login page, confirm "Login with SUSE
   SSO" button appears, complete a test login

#### Staging-Specific Notes

- Staging auto-deployment from the `master` branch is deferred until
  the deployment target (Kubernetes, Docker Compose on VM, or cloud
  service) is decided. The current process is manual. When the target
  is known, a deployment workflow will be created via the `@cicd` agent
- IBS/RabbitMQ integration is active — staging receives real events

### Production Deployment

#### Configuration Checklist

Same as staging, with these differences:

| Setting | Value | Notes |
|---------|-------|-------|
| `SSO_REDIRECT_URI` | `https://sentinel.suse.de/auth/callback` | Production URI |
| `CORS_ORIGINS` | `https://sentinel.suse.de` | |
| `JWT_SECRET_KEY` | Unique production secret | Different from staging |

#### Deployment Steps

1. **Database migrations**: run as a one-shot job BEFORE deploying new
   application containers. Never run migrations automatically on API
   startup (multiple replicas could conflict).
2. **Deploy all runtime processes** defined in
   [Container Images](#container-images)
3. **Health checks**: configure orchestrator to use `/health` (liveness)
   and `/ready` (readiness)
4. **Verify**: confirm all services are healthy, check logs for errors

#### Pre-Production Checklist

Before the first production deployment:

- [ ] IdP client registered with production redirect URI
- [ ] `JWT_SECRET_KEY` generated (cryptographically random, >= 32 chars)
- [ ] PostgreSQL provisioned and accessible
- [ ] Redis provisioned and accessible
- [ ] IBS service account created (`IBS_USERNAME` / `IBS_PASSWORD`)
- [ ] SUSE Trust Root CA installed in container for TLS validation of *.suse.de services
- [ ] DNS configured for `sentinel.suse.de`
- [ ] TLS certificate provisioned for `sentinel.suse.de`
- [ ] Reverse proxy / ingress configured to route `/api` to backend
- [ ] Rate limiting configured on the reverse proxy (see
      `docs/drafts/open-points.md`, OP-2)
- [ ] CORS origins set correctly
- [ ] Log aggregation configured (see Log Aggregation, below)
- [ ] Backup strategy for PostgreSQL defined

#### Production-Specific Notes

- Production is deployed manually from version tags (`v*`). A deployment
  workflow will be added when the infrastructure target is decided
- Celery Beat is a singleton — ensure only one instance runs
- `JWT_SECRET_KEY` rotation invalidates all active sessions (plan for
  off-peak maintenance window). In-flight SSO logins are also affected
  (max 10 minutes of disruption)

---

## Release Process

Sentinel uses [release-please](https://github.com/googleapis/release-please)
to automate versioning, changelog generation, and GitHub Releases. The
process is driven entirely by Conventional Commit messages on the
`master` branch.

### How It Works

1. Developers merge PRs to `master` using conventional commits
   (`feat:`, `fix:`, etc.). Squash merge is required so the PR title
   becomes the commit message (see Squash Merge below)
2. The `release-please` GitHub Action
   (`.github/workflows/release-please.yml`) analyzes new commits and
   creates (or updates) a **Release PR** with:
   - Version bump in `backend/pyproject.toml`
   - Updated `backend/CHANGELOG.md`
   - Summary of all changes since the last release
3. The Release PR stays open and is updated automatically as more
   commits land on `master`
4. When the team decides to release, a maintainer merges the Release PR
5. On merge, release-please:
   - Creates a git tag (`v<major>.<minor>.<patch>`)
   - Creates a GitHub Release with release notes
6. The tag triggers `build-images.yml`, which builds the Docker image
   once, runs a **blocking image smoke-test gate** against that exact
   artifact, and only on success pushes the same image digest to
   `ghcr.io` with semver tags. A failing smoke test prevents publication
   (see `docs/features/platform/testing-strategy.md`, Image / Container
   Smoke Testing)

### Creating a Release

To create a release, merge the open Release PR. No manual version
bumping, tagging, or changelog editing is required.

To force a specific version (e.g., to reach `1.0.0`), use the
`Release-As` footer in a commit message:

```
chore: prepare 1.0.0 release

Release-As: 1.0.0
```

### Squash Merge

All PRs to `master` MUST use squash merge — it is the only allowed
merge method (merge commits and rebase merge are disabled at the
repository level). This keeps the git history linear and gives
release-please a clean, single commit to analyze per PR. With squash
merge, the PR title becomes the commit message — ensure it follows the
Conventional Commits format defined in `docs/conventions.md` (Git
Conventions).

### Changelog

`backend/CHANGELOG.md` is maintained automatically by
release-please. Do not edit it manually. It groups changes by type
(Features, Bug Fixes, etc.) and links to commits and PRs.

### Pipeline Chain

```
master branch commits (via squash-merge PR)
     │
     ▼
CI (ci.yml) — lint, test, security scan, shell lint
     │ (on success)
     ▼
release-please.yml (workflow_run, gated behind CI)
     → creates/updates Release PR
     │ (on merge)
     ▼
creates git tag (v*) + GitHub Release
     │
     ▼
build-images.yml (workflow_run, gated behind CI on master; also push tags)
     → builds image once → image smoke-test gate
     → pushes same digest to ghcr.io (only if gate passes)
     │
     ▼
manual deployment from tag (staging/production)
```

### Version Locations

| Location | Mechanism |
|----------|-----------|
| `backend/pyproject.toml` | Updated by release-please (source of truth) |
| `backend/app/main.py` | Reads dynamically via `importlib.metadata` |
| Git tag | Created by release-please (`v1.2.3`) |
| Docker image tag | Derived from git tag by `build-images.yml` |
| GitHub Release | Created by release-please with changelog |
| `backend/CHANGELOG.md` | Updated by release-please |

### Image Tag Semantics

`build-images.yml` publishes images to `ghcr.io/<repo>` under distinct
tags depending on which trigger produced the build. Each tag has exactly
one meaning — there is no overlap between what the `master` build
produces and what a version-tag build produces:

| Tag | Produced by | Meaning |
|-----|-------------|---------|
| `master` | Every merge to `master` (`workflow_run` trigger, via `type=ref,event=branch`) | Latest CI-green `master` HEAD. Not a release artifact — content changes on every merge |
| `latest` | Highest semver tag pushed so far (`push: tags: v*` trigger, via `docker/metadata-action`'s default `flavor: latest=auto`) | The most recently published release. Only ever produced by a version-tag build |
| `X.Y.Z` | Version tag push (`push: tags: v*`) | The exact release version |
| `X.Y` | Version tag push (`push: tags: v*`) | Floating pointer to the latest patch release within that minor version |

**Why `master` never produces `latest`**: `docker/metadata-action` derives
`latest` exclusively from its semver `flavor: latest=auto` default, which
only activates on the semver-tag trigger path. The `master`-triggered
path only matches the `type=ref,event=branch` rule, producing the
`master` tag alone. No explicit raw `latest` rule is declared in the
`tags:` input — adding one would make both trigger paths produce
`latest` independently, racing each other with no deterministic winner
and leaving `latest` pointing at `master` HEAD after every subsequent
merge instead of the last release. `latest` is therefore reliable for
consumers (e.g., `image-scan.yml`, manual deployments) that expect it to
track "the last release," not "the tip of master."

### API Documentation Publication

On every version release (version tag push), the OpenAPI contract and an
interactive API documentation site are published to GitHub Pages. API
consumers — including the frontend application developed in a separate
repository — depend on this publication as the authoritative,
machine-readable contract reference.

### Container Image Retention

Untagged container image versions in the package registry are subject to
bounded retention: only a fixed number of the most recent untagged
versions are kept; older untagged versions are removed on a weekly
schedule. Tagged images (`master`, `latest`, semver release tags) are
never subject to cleanup — only the pool of untagged versions (produced
as a side effect of re-tagging on every push) is bounded.

### Configuration Files

The release-please configuration lives in two files at the repository
root:

- `release-please-config.json` — release strategy and package
  configuration
- `.release-please-manifest.json` — current version tracking

These files are managed by release-please and should not be edited
manually except during initial setup or to force a version via
`Release-As`.

### Repository Secret

The `release-please.yml` workflow requires a repository secret named
`RELEASE_TOKEN` containing a Fine-Grained Personal Access Token (or
GitHub App token) with `contents: write`, `issues: write`, and
`pull-requests: write` permissions. The default `GITHUB_TOKEN` cannot
be used because tags created by it do not trigger downstream workflows
(a GitHub Actions limitation to prevent recursive runs).

---

## Process Architecture

### Container Images

All runtime processes run from the same OCI image with different
entrypoint commands — see `docs/architecture.md` (Single Docker image,
multiple entrypoints) for the rationale. This is the canonical
enumeration of all process roles.

**Runtime processes** (long-running):

| Process | Role | Scalable |
|---------|------|----------|
| API server (uvicorn) | HTTP request handling | Yes (multiple replicas) |
| Celery worker | Background task execution | Yes (multiple workers) |
| Git worker (Celery) | Git-based fetcher execution | No (single, volume affinity — see [Git Worker Volume](#git-worker-volume)) |
| Celery Beat | Periodic task scheduling | No (singleton) |
| IBS RabbitMQ consumer | Real-time event consumption | No (singleton — see `docs/features/integrations/ibs-rabbitmq-integration.md`) |

**One-shot jobs:**

- Alembic migration job — see [Database Migrations](#database-migrations)

### Singleton Processes

Celery Beat and the IBS RabbitMQ consumer are singleton processes.
Running multiple instances causes duplicate task scheduling or duplicate
event processing. The git worker is constrained to a single instance by
volume affinity (ReadWriteOnce), not by a logical singleton requirement.

Local environments run one instance of each. Kubernetes deployments must
enforce singleton constraints unless a future design introduces
distributed locking or leader election.

### Startup Ordering

After Alembic migrations complete, all runtime processes (API server,
Celery worker, Git worker, Celery Beat, IBS RabbitMQ consumer) MAY start
in any order. No inter-process startup dependency exists.

This property is guaranteed by the following mechanisms:

- **`bootstrap_fetcher_configs()`** runs in every process (worker, Beat,
  API server) and uses `INSERT ... ON CONFLICT DO NOTHING` — Beat does
  not depend on workers or the API having created `FetcherConfig` records
  first. If Beat starts first, it creates them; if a worker starts first,
  Beat's bootstrap is a no-op (records already exist); if all start
  simultaneously, the first `INSERT` wins and concurrent duplicates are
  no-ops.
- **`system_settings` seeding** uses `ON CONFLICT DO NOTHING` (Alembic
  data migration is the primary mechanism; FastAPI lifespan is
  defense-in-depth).
- **The IBS RabbitMQ consumer** connects to RabbitMQ with retry semantics
  — it operates independently of Beat and workers.
- **Each process fails fast** if infrastructure dependencies (PostgreSQL,
  Redis) are unreachable — no process silently waits for another
  application process.

If a future change introduces an inter-process startup dependency, this
section MUST be updated with the new constraint and the deployment
manifests (Docker Compose, Kubernetes) adjusted accordingly.

See `docs/features/platform/fetcher-infrastructure.md` (Startup
Reconciliation, Complete Beat Startup Sequence) for the Beat-specific
startup sequence detail.

### Git Worker Volume

The git worker requires a persistent volume mounted at
`$GIT_CLONE_BASE_DIR` (default: `/var/lib/sentinel/git`). This volume
stores bare clones of external git repositories used by CVE fetchers.

| Property | Value |
|----------|-------|
| Minimum capacity | 8 GB |
| Access mode | ReadWriteOnce (single worker) |
| Backup | Not required — recoverable cache (fetchers re-clone if lost) |

Bare clones have no working tree — accidental checkout expansion
(which could consume ~4 GB for cvelistV5 alone) is structurally
impossible.

See `docs/features/platform/git-fetcher-infrastructure.md` (Volume
Requirements, Recovery, Worker Affinity) for volume layout, recovery
procedures, and worker affinity configuration.

### Timezone and Locale Requirements

All Sentinel runtime processes (see [Container Images](#container-images))
MUST operate with UTC as the system timezone. This is enforced
at two levels:

1. **Celery configuration**: the application sets `timezone = "UTC"` and
   `enable_utc = True` in the Celery config. The Celery app factory
   validates these at module import time and raises a `RuntimeError` if
   overridden — this prevents any Celery-based process from starting
    with incorrect timezone configuration (see
    `docs/features/platform/fetcher-infrastructure.md`, Startup
    Validation)

2. **Container timezone**: set `TZ=UTC` in the container environment (or
   leave unset — most base images default to UTC). This ensures that
   system-level time functions (`datetime.now()`, file timestamps, log
   entries) are consistent with the Celery scheduler

**Why this matters**: all fetcher cron schedules are expressed in UTC.
Some external data sources publish at specific UTC times (e.g., EPSS at
13:31 UTC daily). A timezone misconfiguration causes fetchers to run at
incorrect wall-clock times, potentially before upstream data is
available.

#### Locale for git worker containers

Containers running the git worker (git-based CVE fetchers) SHOULD set
`LC_ALL=C` in their environment as a secondary defense. The primary
guarantee is code-level: `git_operations.py` injects `LC_ALL=C`,
`GIT_TERMINAL_PROMPT=0`, and `TZ=UTC` into every git subprocess call
(see `docs/features/platform/git-fetcher-infrastructure.md`, Module
Invariants — Rule 3). The container-level setting serves as
defense-in-depth in case a future code path invokes git outside the
centralized module.

Recommended container environment for git workers:

```
TZ=UTC
LC_ALL=C
GIT_TERMINAL_PROMPT=0
```

### Clock Synchronization

All application instances in a multi-instance deployment must have their
system clocks synchronized via NTP (or an equivalent time
synchronization protocol). Sentinel relies on timestamps for several
security and correctness mechanisms:

- SSO state parameter validation (10-minute TTL window)
- JWT `exp` and `iat` claim verification
- Session expiration enforcement
- Cache TTL calculations (discovery document, JWKS)

Clock skew between instances can shorten or lengthen time-based windows
unpredictably. For example, if the instance generating an SSO state has
a clock 2 minutes ahead of the instance processing the callback, the
effective validity window shrinks from 10 minutes to 8 minutes. While
modern NTP-synced servers typically maintain sub-second accuracy (making
this negligible in practice), operators must ensure NTP is configured
and running on all hosts.

---

## Operations

### Database Migrations

Migrations are managed by Alembic and must be run explicitly:

```bash
# Apply all pending migrations
alembic upgrade head

# Check current migration state
alembic current

# Create a new migration
alembic revision --autogenerate -m "description"
```

**Rules**:
- Never run migrations automatically on API container startup
- Always run migrations as a separate step before deploying new code
- In Kubernetes: use a Job that runs before the Deployment rollout
- In Docker/Podman: run as a one-shot container before starting services

#### Migration Failure Recovery

**Recovery strategy: fix-forward.** Sentinel does not support `alembic
downgrade` in production. When a migration fails, the operator corrects
the root cause (network issue, disk space, code bug) and re-runs
`alembic upgrade head`. Migrations are not required to implement a
functional `downgrade()` function.

**PostgreSQL transactional DDL.** PostgreSQL executes DDL statements
(`CREATE TABLE`, `ALTER TABLE`, `DROP COLUMN`, etc.) inside
transactions. If a migration fails for any reason, the transaction is
rolled back automatically — the schema returns to its pre-migration
state. This eliminates the most dangerous failure mode (partially
applied schema changes) that affects other databases.

**Diagnostics.** After a migration failure, run `alembic current` to
determine the database state. If it reports the pre-migration revision,
the transactional rollback succeeded and the schema is intact. If it
reports an unexpected state, compare the `alembic_version` table
contents against the actual schema objects to assess the situation
before re-running.

**Deployment model: stop-the-world.** All application processes (API
server, Celery workers, Celery Beat, IBS consumer) must be stopped
before running migrations, then restarted with the new code version.
There is no requirement for backward compatibility between the schema
of version N and the application code of version N-1. This simplifies
migration authoring — schema changes do not need to be split across
multiple releases to maintain compatibility with running code.

#### Post-Deployment Recovery

When a deployment succeeds (migrations applied, new code running) but a
critical application bug is discovered afterward, the recovery strategy
is also fix-forward:

1. **Immediate mitigation**: stop all application processes
   (stop-the-world) to prevent the bug from causing further damage.
   The system is unavailable during this window
2. **Prepare a hotfix**: create a patch release that fixes the bug.
   This is a new forward version, not a rollback to a previous one
3. **Deploy the hotfix**: run any pending migrations (if any) and
   restart services with the hotfix version

**Do not deploy a previous container image.** Because the database
schema is not required to be backward-compatible with older code
versions, deploying an older image against the current schema may cause
query failures, data corruption, or silent data loss. The only safe
recovery direction is forward.

**Pre-deployment database backup.** Before every production deployment,
take a database backup (e.g., `pg_dump`) so that in the extreme case
where a bug has already corrupted data, the database can be restored to
a consistent pre-deployment state. The backup is a safety net, not the
primary recovery mechanism — fix-forward remains the standard path.

### CLI Operational Access

The `sentinel` console script is available on `PATH` inside every
container image, because all process roles share the same Docker image
(see [Container Images](#container-images)). See `docs/cli-reference.md`
for the full command catalog.

**Execution model.** In staging and production, CLI commands are
executed **exclusively via container shell access** — there is no
supported host-level execution path in these environments. (Running the
CLI directly on the host via `uv run python -m sentinel ...` is a
local-development-only pattern — see [Local Development](#local-development)
above. It relies on a local `uv`-managed virtual environment that does
not exist in staging/production containers.)

**Environment dependencies.** Run CLI commands in an environment where
both PostgreSQL and Redis are reachable — the same dependencies already
required by the API and worker processes. Most commands only need
PostgreSQL; the sole exception is `sentinel manage-user unlock`, which
clears the login lockout counter stored in Redis and therefore requires
Redis connectivity to have a practical effect. This is operational
guidance about environment provisioning, not a new runtime hard
dependency — it does not change the fail-open behavior already
specified for login lockout and session liveness (see
`docs/conventions.md`, Redis Error Handling).

**Docker / Podman Compose pattern.** The recommended pattern generalizes
the one-off container approach already used for Alembic migrations
(see [Database Migrations](#database-migrations)):

```bash
# Recommended: one-off container
docker run --rm -it --env-file .env sentinel:latest \
  sentinel <group> <command> ...

# Alternative: exec into an already-running container
docker exec -it <container> sentinel <group> <command> ...
```

Interactive commands (`manage-user create`, `manage-user set-password`)
prompt for a hidden password and require a TTY — the `-it` flags shown
above are mandatory for these commands.

**Kubernetes pattern.** The deployment target (Kubernetes, Docker
Compose, or another orchestrator) remains undecided (see
[Staging-Specific Notes](#staging-specific-notes)). The Kubernetes
pattern below is documented so operators are not blocked regardless of
which target is eventually chosen:

```bash
# Ad hoc: exec into a running pod
kubectl exec -it <pod> -- sentinel <group> <command> ...

# Recommended for one-off operations: a dedicated Job/pod built from
# the same image, analogous to the Alembic migration Job
```

### Health Checks

The API exposes lightweight liveness and readiness checks so
orchestrators can distinguish between a running process and a service
ready to handle traffic. See
`docs/features/platform/health-endpoints.md` for the authoritative
endpoint specification (response schemas, failure semantics, design
decisions).

| Endpoint | Purpose | Checks |
|----------|---------|--------|
| `GET /health` | Liveness | API process running |
| `GET /ready` | Readiness | PostgreSQL + Redis reachable |

Configure your orchestrator to use these endpoints:

- **Docker**: `healthcheck` directive in compose file or Dockerfile
- **Kubernetes**: `livenessProbe` → `/health`, `readinessProbe` → `/ready`

The orchestrator MUST set `timeoutSeconds` (Kubernetes) or `timeout`
(Docker) to at least 5 seconds to accommodate the internal check
timeouts (2s per dependency, checks concurrent; 5s provides margin for network overhead).

### Redis Durability, Memory, and Persistence

Sentinel uses Redis in two roles, addressed by two configuration URLs
(see `docs/configuration.md`):

- **Application cache/coordination** (`REDIS_URL`, db 0): session
  liveness cache, login lockout counters, on-demand fetch deduplication
  locks, CVSS recalculation lock, IBS consumer heartbeat.
- **Celery broker + scheduler** (`CELERY_BROKER_URL`, db 1): task queue
  and `celery-redbeat` schedule entries (including the distributed lock
  used as recovery sentinel).

#### Persistence is Disabled by Design

Redis persistence (RDB and AOF) MUST be disabled in all environments:

```
save ""
appendonly no
```

**Rationale**:

1. **No durable data lives solely in Redis.** PostgreSQL is the source
   of truth for all persistent state (sessions, schedules, task
   outcomes, mutation serialization). Every Redis key is either
   TTL-bounded and self-healing, or fully reconstructible at Beat
   startup — from PostgreSQL (fetcher schedules, via Sentinel's startup
   reconciliation) or from code (non-fetcher static entries, via
   redbeat's native `setup_schedule()`). See
   `docs/features/platform/fetcher-infrastructure.md` ("Non-Fetcher
   Periodic Tasks") for the coexistence mechanism.

2. **The Beat lock sentinel provides automatic recovery.** When Redis
   loses data (restart or flush), Beat detects the missing lock within
   ≤60 seconds, terminates, and the orchestrator restarts it. The
   startup process rebuilds the full schedule — fetcher entries from
   PostgreSQL (reconciliation) and non-fetcher static entries from code
   (`setup_schedule()`). No manual intervention is required. See
   `docs/features/platform/fetcher-infrastructure.md` (Runtime: Redis
   Data Loss) for the mechanism.

3. **Persistence would undermine the lock sentinel.** If RDB restored
   the `redbeat::lock` key after a Redis restart (the snapshot is recent
   enough that the lock has not expired — the lock TTL is 300s, typically
   still valid within a restart window), Beat's `lock.extend()` would
   succeed, the sentinel would NOT fire, and Beat would continue running
   with the schedule from the snapshot — bypassing the clean crash →
   reconciliation recovery path. Expired keys are correctly discarded at
   RDB reload, so this concerns non-expired keys specifically. Volatile
   Redis guarantees the lock is always absent after data loss, ensuring
   the sentinel always fires.

4. **Task queue loss is acceptable.** Queued tasks that are lost during
   a Redis restart are recovered by the next periodic fetcher execution
   (scheduled intervals range from 6 hours to 24 hours). On-demand
   fetches can be re-triggered via the API. The `FetcherRun` table in
   PostgreSQL tracks outcomes — no Celery result backend is used.

#### Memory Configuration

Redis MUST be configured with explicit memory limits and the
`noeviction` policy to prevent silent data loss through eviction:

| Setting | Value | Purpose |
|---------|-------|---------|
| `maxmemory` | `768mb` | Internal memory ceiling (~75% of container limit). When reached, Redis refuses new writes rather than evicting existing keys |
| `maxmemory-policy` | `noeviction` | Write commands return OOM error; read commands continue. Preserves all existing data (queued tasks, schedule entries, locks) |

**Container resource limits** (Kubernetes QoS Guaranteed):

| Resource | Value | Purpose |
|----------|-------|---------|
| `requests.memory` | `1Gi` | Minimum guaranteed memory (scheduler placement) |
| `limits.memory` | `1Gi` | Maximum allowed memory (kernel OOM-kill threshold) |

Setting `requests == limits` achieves QoS class "Guaranteed": the pod
is never evicted under node memory pressure. This is appropriate for
Redis as a broker/coordination service.

**Why `maxmemory` must be lower than `limits.memory`**: the container
memory limit is enforced by the kernel — exceeding it causes immediate
process termination (OOM-kill). The Redis `maxmemory` setting is an
*internal* threshold that triggers the `noeviction` policy *before* the
kernel intervenes. The ~25% gap (768 MB vs 1024 MB) provides headroom
for Redis process overhead: allocator fragmentation, client connection
buffers, internal data structures, and Lua script execution memory.

**Behavior when `noeviction` triggers**: Redis returns
`OOM command not allowed when used memory > 'maxmemory'` on write
commands. Read commands continue normally. Application code handles this
as a `RedisError` with graceful degradation (see `docs/conventions.md`,
Redis Error Handling). For the Celery broker, OOM indicates a capacity
issue — operators should investigate queue backlog growth (e.g., workers
not consuming tasks).

**If the orchestrator imposes a memory limit lower than `maxmemory`**:
the kernel OOM-kills Redis *before* the `noeviction` policy activates.
The `maxmemory` becomes ineffective. Always ensure: `maxmemory` <
container `limits.memory`.

**Memory sizing rationale**: Sentinel's Redis footprint is small.
Application keys (db 0) total < 10 MB even with thousands of active
sessions. Redbeat entries are negligible (~1 KB × ~12 fetchers). The
primary variable is the Celery task queue backlog (db 1): under normal
operation nearly empty (workers consume in real-time); under stress
(first-run with thousands of CVEs, or workers down) may grow to
~100-150 MB. The 768 MB `maxmemory` provides >5× headroom over
realistic peak usage.

#### Monitoring Scheduler Liveness (Recommended)

The lock sentinel mechanism ensures automatic recovery in all standard
failure modes. As defense-in-depth for edge cases (lock accidentally
disabled, Redis manipulated selectively), operators SHOULD configure
external monitoring on scheduler activity.

**Recommended signal** (cause-agnostic — detects any cause of stalled
ingestion):

> Alert when at least one fetcher with `enabled = true` has a
> `last_run.finished_at` older than 2× its configured schedule interval,
> or has never run (`last_run = null`).

This signal is derivable from `GET /api/v1/fetchers` without any code
changes to Sentinel. It detects not only empty schedules but also dead
workers, database unavailability, or any other cause of stalled
processing.

**Why not `/health` or `/ready`**: these endpoints report API server
instance health for the load balancer. Returning non-200 for a Beat
problem would incorrectly remove healthy API instances from rotation.
Beat is a separate process — its liveness is the orchestrator's
responsibility, not the API server's.

**When the schedule is legitimately empty**: if an operator disables all
fetchers, the schedule is empty by design. The monitoring signal above
correctly handles this: with no enabled fetchers, the condition "at
least one enabled fetcher with stale last_run" is false → no alert.

### Log Aggregation

See `docs/features/platform/logging.md` for the application-side
contract (structured format, log levels, correlation IDs, standard
record schema). This section documents how the log stream surfaces
and is retained in each deployment context — the application itself
never writes, rotates, or persists log files; it only writes to
stdout/stderr.

#### Docker / Podman

Logs are captured via the container engine's logging driver. For
local rotation without any external log shipper, configure the
`json-file` (Docker) or `local` (Podman) logging driver with
`max-size`/`max-file` options in `docker-compose.yml` — this is a
platform/engine configuration concern, not something Sentinel
implements. Example:

```yaml
services:
  api:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
```

#### Kubernetes

Use `kubectl logs <pod>` for ad hoc inspection. For persistent
aggregation, a cluster-level log shipper (e.g., Fluent Bit or Vector)
forwarding to an aggregator (e.g., Loki or an ELK stack) is the
operator's responsibility — Sentinel does not bundle or require any
specific shipper.

#### Process-role identification

Per `docs/features/platform/logging.md`, log records do not carry a
process-role field of their own. Which of the 5 runtime roles (`api`,
`celery-worker`, `git-worker`, `beat`, `ibs-consumer`) produced a given
line is identified via platform-provided metadata: Kubernetes pod/
container labels, or the Docker Compose service name
(`com.docker.compose.service`, attached automatically by the Compose
engine). Configuring the log collector to attach and propagate this
metadata when shipping logs to the aggregator is the operator's
responsibility.

#### `LOG_LEVEL=DEBUG` risk in production

Setting `LOG_LEVEL=DEBUG` causes third-party loggers (notably
`sqlalchemy.engine` and `httpx`) to emit sensitive data — SQL
statements with bound parameters, full request URLs that may embed
tokens. See `docs/features/platform/logging.md` (Secrets and PII
Discipline) for the full policy. Operators should use
`LOG_LEVEL=DEBUG` in production only for time-bounded diagnostics and
revert promptly.

### Image Vulnerability Monitoring

A weekly scheduled workflow (`.github/workflows/image-scan.yml`) scans
the published `ghcr.io/<repo>:latest` backend image for OS-level
vulnerabilities using Trivy. It also supports manual dispatch for
on-demand checks between scheduled runs.

**Why the OS layer needs its own visibility.** `pip-audit` (see
Pipeline Chain above) gates Python dependencies on every merge, but the
image is Debian-based (`python:3.13-slim`) and the OS package layer is
not covered by any dependency scanner. Dependabot cannot help here
either: `python:3.13-slim` is a moving tag whose string never changes,
so it never triggers a Dependabot update PR.

**Deliberately non-blocking.** This scan never fails the workflow run
and is not part of the publish path (`build-images.yml` is untouched).
Many Debian OS package CVEs are "will not fix" upstream, and even
fixable ones require a rebuild of the base image — a remediation the
project does not control on demand. Blocking image publication on a
layer with no available immediate remediation would stall the release
pipeline without a corresponding way to resolve it.

**Fixable/unfixable split.** Each run produces two Trivy outputs:

- A **fixable HIGH/CRITICAL** result — vulnerabilities with a known
  upstream fix available. This result drives issue creation/update
  (see below).
- A **complete report** — every finding regardless of fix
  availability, uploaded as a workflow artifact for awareness only.

**Delivery.** A fixable finding opens a new GitHub issue labeled
`security` (the label already exists in the repository), or updates
the existing open one if a prior run already opened it — the workflow
never creates a duplicate issue for the same ongoing condition.

**Remediation path.** Because the scan does not trigger a rebuild,
fixable findings are remediated on the next merge to `master` that
produces a new image (which pulls the then-current `python:3.13-slim`
base layer). This is intended behavior, not an oversight: the platform
has no separate mechanism to force an out-of-band base image rebuild.

### Python Forward-Compatibility Check

A weekly scheduled workflow
(`.github/workflows/python-forward-compat.yml`) runs the backend test
suite against the **next** Python minor version — one above the
current `backend/.python-version` target — using `uv python install`
to fetch the interpreter (including the latest available pre-release
build, if that is the newest published build for that minor) and `uv
sync` / `uv run` with a `--python` override. It also supports manual
dispatch for on-demand checks between scheduled runs.

**Why this exists.** Python runtime bumps follow the Version Bump
Checklist in `docs/conventions.md` (Runtime Version), which is a
manual, reactive procedure: incompatibilities in the interpreter or in
a dependency (particularly the Celery stack and packages with C/Rust
extensions) only surface when someone actually executes the bump. This
workflow turns that into an early-warning signal, surfacing breakage
weeks or months before the bump PR is opened.

**Deliberately non-blocking.** This run never fails the workflow in a
way that blocks other work and is not part of the publish path
(`build-images.yml` is untouched). The next Python minor version is
frequently a pre-release during most of its development cycle, and
failures are expected and uninteresting until close to that version's
own stable release — the workflow is informational, not a merge gate,
and is never a required status check.

**Availability guard.** Right after a Runtime Version bump, the next
minor may have no published build at all yet (not even an alpha). The
workflow checks this first via `uv python list <next> --only-downloads`
before attempting anything else. If no build is available, the run
exits successfully with an informational `::notice::` — no test is
attempted and no tracking issue is opened. This avoids a false-positive
failure signal for a condition that carries no compatibility
information.

**Best-effort resolution.** Unlike the reproducible `backend-test` job
in `ci.yml` (which uses `uv sync --locked` against the committed
lockfile), this workflow runs `uv sync` without `--locked` — it
intentionally allows dependency resolution to float against the new
interpreter, since the goal is to detect whether the current
dependency set *can* resolve and pass on the next version, not to
reproduce a pinned environment.

**Delivery.** Once a build is available, a failure at either remaining
stage (dependency resolution or the test run itself) opens a new
GitHub issue labeled `quality-tooling` (the label already exists in
the repository) with a fixed, version-agnostic title, or updates the
existing open one if a prior run already opened it — the workflow
never creates a duplicate issue for the same ongoing condition, and
never auto-closes it on a subsequent green run. A human triages and
closes the issue once addressed or acknowledged.

See `docs/conventions.md` (Version Bump Checklist) for the manual
upgrade procedure this check complements.

### Troubleshooting

Log messages referenced below appear as structured `event` fields in
the JSON/console log output — see `docs/features/platform/logging.md`
for the record schema.

#### SSO Login Fails

1. Check that `SSO_REDIRECT_URI` matches exactly one of the URIs
   registered in the IdP client configuration
2. Check that `id.suse.com` is reachable from the Sentinel backend
   (network/firewall)
3. Check logs for `"SSO token exchange failed"` warnings — indicates
   the IdP rejected the authorization code
4. Check logs for `"SSO callback: expected claim ... not found"` — the
   configured `SSO_USER_CLAIM` does not exist in the ID token
5. Verify the user exists in the Sentinel database with a matching
   `username` and `external_id IS NOT NULL` (the user must be provisioned via external identity provider first — see `identity-provisioning.md`)

#### Celery Tasks Not Running

1. Verify Redis is reachable at `CELERY_BROKER_URL`
2. Check that Celery Beat is running (scheduler)
3. Check that at least one Celery worker is running
4. Check worker logs for task exceptions
5. Check Beat logs for the reconciliation summary message ("Beat
   schedule reconciliation complete: ..."). If absent, reconciliation
   failed — check for PostgreSQL connectivity errors above it
6. If Beat exits repeatedly with "cannot read FetcherConfig from
   PostgreSQL", ensure the database is reachable before Beat can start
   successfully (Beat fails fast when PostgreSQL is unavailable at
   startup)
