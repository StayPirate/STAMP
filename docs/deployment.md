# Deployment Guide

Operational guide for deploying and configuring Sentinel across
environments: local development, staging, and production.

For environment variable reference, see `docs/configuration.md`.
For architectural decisions and portability constraints, see
`docs/architecture.md`.

---

## Prerequisites

### Software Requirements

| Component | Minimum Version | Purpose |
|-----------|----------------|---------|
| Docker or Podman | Docker 24+ / Podman 4+ | Container runtime |
| PostgreSQL | 15+ | Primary database |
| Redis | 7+ | Session cache, Celery broker, rate limiting |
| Git | 2.25+ | Git-based CVE fetcher operations (git worker container only) |
| Node.js | 20+ | Frontend build (development only) |
| Python | 3.11+ | Backend runtime (development only) |

### Network Access (Staging/Production)

Sentinel requires outbound access to:

| Service | Host | Port | Purpose |
|---------|------|------|---------|
| SUSE IdP | `id.suse.com` | 443 | SSO authentication (OIDC) |
| SUSE AD | `pan.suse.de` | 636 | LDAP directory sync |
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

## Local Development

### Quick Start

```bash
# Start PostgreSQL + Redis containers
./dev-env.sh up

# Run database migrations
cd backend && alembic upgrade head

# Start the backend API server
cd backend && uvicorn app.main:app --reload --port 8000

# Start Celery worker (separate terminal)
cd backend && celery -A app.tasks.celery_app worker --loglevel=info

# Start Celery Beat scheduler (separate terminal)
cd backend && celery -A app.tasks.celery_app beat --loglevel=info

# Start the frontend dev server (separate terminal)
cd frontend && npm install && npm run dev
```

### Local Environment Variables

Create `backend/.env` for local development:

```bash
# Required
DATABASE_URL=postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel
JWT_SECRET_KEY=local-development-secret-minimum-32-characters

# Redis (defaults work with dev-env.sh)
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

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

### Creating the First Local User

With SSO disabled (no SSO env vars), create a local admin user via CLI:

```bash
cd backend && python -m sentinel manage-user create \
  --username admin \
  --email admin@localhost \
  --role admin
```

---

## Staging Deployment

### Configuration Checklist

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
| `LDAP_URI` | `ldaps://pan.suse.de:636` | Same AD for all environments |
| `IBS_API_URL` | `https://api.suse.de` | |
| `IBS_USERNAME` / `IBS_PASSWORD` | Service account credentials | |

### Deployment Steps

1. **Database**: ensure PostgreSQL is running and accessible
2. **Redis**: ensure Redis is running and accessible
3. **Run migrations** (one-shot, before starting API):
   ```bash
   docker run --rm --env-file .env sentinel-backend:latest \
     alembic upgrade head
   ```
4. **Start services** (API server, Celery worker, Celery Beat, RabbitMQ
   consumer) — each as a separate container/process
5. **Verify health**:
   - `GET /health` — liveness (API process is running)
   - `GET /ready` — readiness (PostgreSQL + Redis reachable)
6. **Verify SSO**: navigate to the login page, confirm "Login with SUSE
   SSO" button appears, complete a test login

### Staging-Specific Notes

- Staging is auto-deployed from the `master` branch
- LDAP sync runs on the same schedule as production (daily) — staging
  has real user data from AD
- IBS/RabbitMQ integration is active — staging receives real events

---

## Production Deployment

### Configuration Checklist

Same as staging, with these differences:

| Setting | Value | Notes |
|---------|-------|-------|
| `SSO_REDIRECT_URI` | `https://sentinel.suse.de/auth/callback` | Production URI |
| `CORS_ORIGINS` | `https://sentinel.suse.de` | |
| `JWT_SECRET_KEY` | Unique production secret | Different from staging |

### Deployment Steps

1. **Database migrations**: run as a one-shot job BEFORE deploying new
   application containers. Never run migrations automatically on API
   startup (multiple replicas could conflict).
2. **Deploy containers**: API server, Celery worker(s), Celery Beat,
   IBS RabbitMQ consumer
3. **Health checks**: configure orchestrator to use `/health` (liveness)
   and `/ready` (readiness)
4. **Verify**: confirm all services are healthy, check logs for errors

### Pre-Production Checklist

Before the first production deployment:

- [ ] IdP client registered with production redirect URI
- [ ] `JWT_SECRET_KEY` generated (cryptographically random, >= 32 chars)
- [ ] PostgreSQL provisioned and accessible
- [ ] Redis provisioned and accessible
- [ ] IBS service account created (`IBS_USERNAME` / `IBS_PASSWORD`)
- [ ] SUSE Trust Root CA installed in container for LDAP TLS validation
- [ ] DNS configured for `sentinel.suse.de`
- [ ] TLS certificate provisioned for `sentinel.suse.de`
- [ ] Reverse proxy / ingress configured to route `/api` to backend
- [ ] Rate limiting configured on the reverse proxy (see
      `docs/drafts/open-points.md`, OP-2)
- [ ] CORS origins set correctly
- [ ] Log aggregation configured
- [ ] Backup strategy for PostgreSQL defined

### Production-Specific Notes

- Production is deployed manually from version tags (`v*`)
- Celery Beat is a singleton — ensure only one instance runs
- `JWT_SECRET_KEY` rotation invalidates all active sessions (plan for
  off-peak maintenance window). In-flight SSO logins are also affected
  (max 10 minutes of disruption)

### Timezone and Locale Requirements

All Sentinel containers (API server, Celery worker, Celery Beat) MUST
operate with UTC as the system timezone. This is enforced at two levels:

1. **Celery configuration**: the application sets `timezone = "UTC"` and
   `enable_utc = True` in the Celery config. The worker validates these
   at startup and refuses to start if overridden (see
   `docs/configuration.md`, Celery Worker Configuration)

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

---

## Database Migrations

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

---

## Process Architecture

Sentinel requires multiple processes running concurrently:

| Process | Role | Scalable |
|---------|------|----------|
| API server (uvicorn) | HTTP request handling | Yes (multiple replicas) |
| Celery worker | Background task execution | Yes (multiple workers) |
| Git worker (Celery) | Background git-based fetcher execution | No (single volume affinity) |
| Celery Beat | Periodic task scheduling | No (singleton) |
| IBS RabbitMQ consumer | Real-time event consumption | No (singleton — see spec) |

### Singleton Processes

Celery Beat and the IBS RabbitMQ consumer must run as single instances.
Running multiple replicas causes duplicate task scheduling or duplicate
event processing.

### Git Worker Volume

The git worker requires a persistent volume mounted at
`$GIT_CLONE_BASE_DIR` (default: `/var/lib/sentinel/git`). This volume
stores bare clones of external git repositories used by CVE fetchers.

| Property | Value |
|----------|-------|
| Minimum capacity | 1 GB |
| Access mode | ReadWriteOnce (single worker) |
| Backup | Not required — recoverable cache (fetchers re-clone if lost) |

Bare clones have no working tree — accidental checkout expansion
(which could consume ~4 GB for cvelistV5 alone) is structurally
impossible.

See `docs/features/platform/git-fetcher-infrastructure.md` (Volume
Requirements, Recovery, Worker Affinity) for volume layout, recovery
procedures, and worker affinity configuration.

---

## Health Checks

| Endpoint | Purpose | Checks |
|----------|---------|--------|
| `GET /health` | Liveness | API process is running, can serve HTTP |
| `GET /ready` | Readiness | PostgreSQL reachable, Redis reachable |

Configure your orchestrator (Docker healthcheck, Kubernetes probes) to
use these endpoints for automated health monitoring.

---

## Troubleshooting

### SSO Login Fails

1. Check that `SSO_REDIRECT_URI` matches exactly one of the URIs
   registered in the IdP client configuration
2. Check that `id.suse.com` is reachable from the Sentinel backend
   (network/firewall)
3. Check logs for `"SSO token exchange failed"` warnings — indicates
   the IdP rejected the authorization code
4. Check logs for `"SSO callback: expected claim ... not found"` — the
   configured `SSO_USER_CLAIM` does not exist in the ID token
5. Verify the user exists in the Sentinel database with a matching
   `username` and `ad_object_guid IS NOT NULL` (run LDAP sync first)

### LDAP Sync Not Working

1. Verify `LDAP_URI` is correct and port 636 is reachable
2. Verify the SUSE Trust Root CA is installed at the path specified by
   `SUSE_CA_CERT_PATH`
3. Check logs for TLS handshake errors

### Celery Tasks Not Running

1. Verify Redis is reachable at `CELERY_BROKER_URL`
2. Check that Celery Beat is running (scheduler)
3. Check that at least one Celery worker is running
4. Check worker logs for task exceptions
