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
| Python | 3.11+ | Backend runtime (development only) |

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
cd backend && celery -A app.celery_app worker --loglevel=info

# Start Celery Beat scheduler (separate terminal)
cd backend && celery -A app.celery_app beat --loglevel=info
# Note: the redbeat scheduler class is configured in the Celery app
# settings (beat_scheduler). No --scheduler CLI flag is needed.
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

- Staging auto-deployment from the `master` branch is deferred until
  the deployment target (Kubernetes, Docker Compose on VM, or cloud
  service) is decided. The current process is manual. When the target
  is known, a deployment workflow will be created via the `@cicd` agent
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
- [ ] SUSE Trust Root CA installed in container for TLS validation of *.suse.de services
- [ ] DNS configured for `sentinel.suse.de`
- [ ] TLS certificate provisioned for `sentinel.suse.de`
- [ ] Reverse proxy / ingress configured to route `/api` to backend
- [ ] Rate limiting configured on the reverse proxy (see
      `docs/drafts/open-points.md`, OP-2)
- [ ] CORS origins set correctly
- [ ] Log aggregation configured
- [ ] Backup strategy for PostgreSQL defined

### Production-Specific Notes

- Production is deployed manually from version tags (`v*`). A deployment
  workflow will be added when the infrastructure target is decided
- Celery Beat is a singleton — ensure only one instance runs
- `JWT_SECRET_KEY` rotation invalidates all active sessions (plan for
  off-peak maintenance window). In-flight SSO logins are also affected
  (max 10 minutes of disruption)

### Timezone and Locale Requirements

All Sentinel containers (API server, Celery worker, Celery Beat) MUST
operate with UTC as the system timezone. This is enforced at two levels:

1. **Celery configuration**: the application sets `timezone = "UTC"` and
   `enable_utc = True` in the Celery config. The Celery app factory
   validates these at module import time and raises a `RuntimeError` if
   overridden — this prevents any Celery-based process (worker, Beat,
   consumer) from starting with incorrect timezone configuration (see
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
| Minimum capacity | 8 GB |
| Access mode | ReadWriteOnce (single worker) |
| Backup | Not required — recoverable cache (fetchers re-clone if lost) |

Bare clones have no working tree — accidental checkout expansion
(which could consume ~4 GB for cvelistV5 alone) is structurally
impossible.

See `docs/features/platform/git-fetcher-infrastructure.md` (Volume
Requirements, Recovery, Worker Affinity) for volume layout, recovery
procedures, and worker affinity configuration.

---

## Redis Durability, Memory, and Persistence

Sentinel uses Redis in two roles, addressed by two configuration URLs
(see `docs/configuration.md`):

- **Application cache/coordination** (`REDIS_URL`, db 0): session
  liveness cache, login lockout counters, on-demand fetch deduplication
  locks, CVSS recalculation lock, IBS consumer heartbeat.
- **Celery broker + scheduler** (`CELERY_BROKER_URL`, db 1): task queue
  and `celery-redbeat` schedule entries (including the distributed lock
  used as recovery sentinel).

### Persistence is Disabled by Design

Redis persistence (RDB and AOF) MUST be disabled in all environments:

```
save ""
appendonly no
```

**Rationale**:

1. **No durable data lives solely in Redis.** PostgreSQL is the source
   of truth for all persistent state (sessions, schedules, task
   outcomes, mutation serialization). Every Redis key is either
   TTL-bounded and self-healing, or fully reconstructible from
   PostgreSQL via Beat's startup reconciliation.

2. **The Beat lock sentinel provides automatic recovery.** When Redis
   loses data (restart or flush), Beat detects the missing lock within
   ≤60 seconds, terminates, and the orchestrator restarts it. The
   reconciliation rebuilds the full schedule from PostgreSQL. No manual
   intervention is required. See
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

### Memory Configuration

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

### Monitoring Scheduler Liveness (Recommended)

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

---

## Health Checks

See `docs/features/platform/health-endpoints.md` for the authoritative
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
   `username` and `external_id IS NOT NULL` (the user must be provisioned via external identity provider first — see `identity-provisioning.md`)

### Celery Tasks Not Running

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
