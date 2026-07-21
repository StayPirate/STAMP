# Architecture

## System Overview

Sentinel is a security update management platform for SUSE and openSUSE-based
Linux distributions. It automates CVE tracking, impact analysis, and update
coordination across multiple maintained distribution versions.

## High-Level Architecture

```
┌──────────────────────────────────┐
│         FastAPI Backend           │
│                                  │
│  ┌─────────┐  ┌──────────────┐  │
│  │ API v1  │  │   Services    │  │
│  └────┬────┘  └──────┬───────┘  │
│       │              │          │
│  ┌────▼──────────────▼───────┐  │
│  │      SQLAlchemy Models     │  │
│  └────────────┬──────────────┘  │
└───────────────┼──────────────────┘
                │
┌───────────────▼──────────────────┐
│          PostgreSQL               │
└──────────────────────────────────┘

┌──────────────────┐     ┌──────────────────────────────────┐
│   CVE Sources    │────▶│        Celery Workers            │
│  NVD, MITRE,     │     │                                  │
│  others          │     │  ┌────────────┐ ┌────────────┐  │
└──────────────────┘     │  │  CVE Sync  │ │  IBS Sync  │  │
                        │  └────────────┘ └────────────┘  │
┌──────────────────┐     │                                  │
│   IBS            │◀──▶│  ┌────────────────────────────┐  │
│  (build.suse.de) │     │  │  Package Resolution  │  │
└──────────────────┘     │  └────────────────────────────┘  │
                        └──────────────────────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │       Redis (Broker/Cache)        │
                        └──────────────────────────────────┘

┌──────────────────┐     ┌──────────────────────────────────┐
│  IBS RabbitMQ    │────▶│      IBSEventConsumer            │
│ (rabbit.suse.de) │     │                                  │
└──────────────────┘     │  Consumes IBS events             │
                         │  (package.commit, request.create,│
                         │  request.state_change) for       │
                         │  release & submission tracking.  │
                         └──────────────────────────────────┘
```

## Components

### Backend (FastAPI)

- **Framework**: FastAPI (async)
- **ORM**: SQLAlchemy 2.0 (async)
- **Migrations**: Alembic
- **Validation**: Pydantic v2
- **Authentication**: JWT tokens in HttpOnly cookies (browser sessions) and API keys (programmatic access)
- **Location**: `backend/app/`

#### Backend Layers

1. **API Layer** (`app/api/v1/`): Thin endpoint handlers. Validate input, call
   services, return responses. No business logic here.
2. **Service Layer** (`app/services/`): All business logic. Services accept
   typed parameters and return typed results. Database operations happen here.
3. **Model Layer** (`app/models/`): SQLAlchemy ORM models. Define tables,
   columns, relationships, and constraints.
4. **Schema Layer** (`app/schemas/`): Pydantic models for request/response
   validation and serialization.
5. **Core Layer** (`app/core/`): Cross-cutting concerns — authentication,
   authorization, configuration, exceptions.
6. **Task Layer** (`app/tasks/`): Celery task definitions for background
   processing.

### Task Queue (Celery)

- **Broker**: Redis
- **Result backend**: disabled (`task_ignore_result = True`) — task
  outcomes are tracked in PostgreSQL (`FetcherRun`), not in Redis. See
  `docs/features/platform/fetcher-infrastructure.md` for rationale.
- **Periodic tasks**: Celery Beat with dynamic scheduling (`celery-redbeat`)
- **Workers**: Separate worker processes for CVE ingestion, OBS interaction,
  and impact analysis
- **Fetcher infrastructure**: all background tasks that fetch data from
  external sources inherit from `BaseFetcher`
  (`app/services/base_fetcher.py`), which provides automatic execution
  tracking, metric collection, and registry. CVE fetchers additionally
  inherit from `BaseCVEFetcher` (`app/services/base_cve_fetcher.py`),
  which provides the `cve_source_type`, optional `fetch_single()`, and default
  `catch_up()` contracts. The fetcher registry feeds
   a dashboard that shows execution history, performance charts, and
   operational controls. See `docs/features/platform/fetcher-infrastructure.md`
   for the generic `BaseFetcher` contract,
   `docs/features/platform/cve-fetcher-infrastructure.md` for the
   `BaseCVEFetcher` contract, and
   `docs/features/platform/git-fetcher-infrastructure.md` for the
   `BaseGitFetcher` contract (git-based CVE fetchers). See
   `docs/features/platform/fetcher-operations.md` for the monitoring dashboard.

### Database (PostgreSQL)

- **ORM**: SQLAlchemy 2.0 with async support
- **Migrations**: Alembic with autogenerate support
- **Schema**: see `docs/data-model.md`

### External Integrations

For a comprehensive catalog of all external data sources — including active
integrations, planned sources, and potential future sources — with access
details, protocols, and documentation links, see `docs/data-sources.md`.

The sections below describe how Sentinel architecturally integrates with each
active source. See the data sources catalog for the full picture.

#### CVE Sources

- **NVD (NIST)**: REST API v2 for CVE data
- **MITRE**: CVE feed for early CVE information
- Additional sources can be added via the pluggable ingestion architecture

#### IBS (Internal Build Service)

- Internal OBS instance at build.suse.de for SUSE commercial products
- Source packages are maintained in codestream projects (e.g.,
  `SUSE:SLE-15-SP6:Update`)
- Sentinel queries IBS to detect when security fixes have been released to
  track and product repositories
- **Real-time event consumer**: Sentinel connects to the IBS RabbitMQ message
  bus (`rabbit.suse.de`) and consumes `suse.obs.package.commit` events for
  near-real-time track-level release detection. The periodic polling
  fetcher (`detect_ibs_track_releases`, every 24 hours at 02:00 UTC)
  serves as a catch-up mechanism for events missed during downtime. See
  `docs/features/integrations/ibs-rabbitmq-integration.md` for the full specification.
- **Submission tracking**: the same RabbitMQ consumer also processes
  `suse.obs.request.create` and `suse.obs.request.state_change` events to
  track IBS submission requests (SRs) and release requests (RRs),
  providing VAs visibility into the MU process progression. A periodic
  fetcher (`SyncIbsRequests`, 02:30 UTC) handles catch-up. See
  `docs/features/packages/ibs-submission-tracking.md`.
- **Package bugowner resolution**: Sentinel queries IBS to resolve the
  bugowner (maintainer) of each source package tracked in tickets. This
  data is cached locally and maintained by a periodic fetcher. See
  `docs/features/packages/package-bugowner.md`.
- See `docs/features/packages/package-model.md` for track/product concepts

#### SMELT

- Internal SUSE aggregator service (REST API at `smelt.suse.de/api`)
- SMELT internally reads from IBS, channel files, and other sources
- Sentinel uses two SMELT endpoints:
  - `GET /api/v1/basic/products/` (paginated): periodic sync of the Product
    table with name, version, CPE, and repository project names
  - `GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1`
    (paginated): on-demand query when adding a package to a ticket. Returns
    tracks (codestreams) and target repositories for the package. The
    `include_reactive=1` parameter MUST always be used to include products
    in Reactive LTSS phase. All pages MUST be fetched.
- Target repository names from `maintainedpackage` are matched to local
  Product records via the ProductRepository table
- See `docs/features/packages/package-model.md` for full integration details

#### AIMAAS

- Internal SUSE service (REST API at `aimaas.suse.de/api`) for product
  lifecycle data and CVSS thresholds
- Sentinel uses two AIMAAS endpoints:
  - `GET /api/entity/products/{slug}`: product lifecycle dates (`fcs`,
    `end_of_gs`, `end_of_ltss`, `end_of_espos`, `end_of_reactive_ltss`).
    Matched to local Product records via CPE (identical between SMELT and
    AIMAAS).
  - `GET /api/entity/cvss-threshold`: CVSS threshold for products in
    LTSS/ESPOS phases (~24 entries). Each entry references an AIMAAS
    product ID; Sentinel resolves this to a CPE to match locally.
- When thresholds or lifecycle dates change, Sentinel re-evaluates eligibility
  for active tickets referencing the affected products

#### Open Build Service (OBS)

- Public instance at build.opensuse.org for openSUSE distributions
- Not currently integrated — there is no plan to integrate openSUSE
  package tracking at this time, but it may be evaluated in the future
- See `docs/data-sources.md` for details on OBS and its RabbitMQ event bus

#### External Identity Provider

- External user provisioning is deferred to a future phase. See
  `docs/features/identity/identity-provisioning.md` for the planned
  approach (SCIM-based push from SUSEID)
- In the current phase, only local user accounts are supported
  (created via CLI)
- See `docs/features/identity/user-management.md` for local user
  management

## Data Flow

### CVE Ingestion Flow

1. Celery Beat triggers periodic CVE sync tasks
2. Workers fetch CVE data from configured sources
3. New/updated CVEs are stored in PostgreSQL
4. A Ticket is created automatically for each new CVE
5. Sentinel resolves package names from CVE data (CPE applicability
   statements, CNA/ADP CPE strings, vendor:product pairs, or
   pre-resolved packages) via the static CPE mapping and vendor:product
   lookup (see `docs/features/packages/cpe-package-mapping.md`)
6. For mapped packages, SMELT is queried to resolve tracks and products
7. TicketPackage, TicketPackageTrack, and TicketPackageProduct records are
   created automatically with status ANALYSIS

See `docs/features/tickets/cve-tracking.md` for the full CVE ingestion
specification (fetcher algorithms, error handling, first-run strategy).

### Manual Ticket Creation

Tickets can also be created manually by Vulnerability Analysts without an
associated CVE — for example, to track security issues reported through
internal bug trackers before a CVE-ID is assigned. A CVE can be
associated with the ticket later. See `docs/features/tickets/tickets.md` for
the full ticket specification.

### Package Affectedness Flow

1. VA analyzes a ticket and sets affectedness status per track
2. Products track only eligibility and delivery confirmation —
   affectedness is determined exclusively at the track level
3. Eligibility is evaluated per product as a boolean flag (`eligible`)
   based on CVSS score vs product threshold from AIMAAS, regardless of
   track affectedness status
4. Products not eligible (CVSS below threshold, Reactive LTSS phase,
   etc.) are marked `eligible=false`. The VA can override eligibility on
   individual products
5. See `docs/features/packages/package-model.md` for the full package model

### Release Tracking Flow

Release detection runs on two **independent** levels — track and
product — through different mechanisms. See
`docs/features/packages/ibs-track-release-detection.md` and
`docs/features/packages/ibs-product-release-detection.md` for the
authoritative details.

1. Track-level detection uses two complementary mechanisms:
   the `IBSEventConsumer` (real-time via IBS RabbitMQ) and the periodic
   `detect_ibs_track_releases` fetcher (catch-up every 24 hours at
   02:00 UTC). Both share the same MD5 cache to avoid duplicate work.
   See `docs/features/integrations/ibs-rabbitmq-integration.md`.
2. **Track level**: the consumer or fetcher queries IBS diff endpoints
   (see `docs/features/integrations/ibs-integration.md` and
   `docs/features/packages/ibs-track-release-detection.md`) to detect whether
   the fix for the ticket's CVE has landed in the codestream IBS project.
   When detected, `TicketPackageTrack.status` is set to `FIXED`.
   Separately, `TicketPackageTrack.delivery_status` is set to `RELEASED`
   when the Release Request (RR) is accepted (via IBS submission tracking).
   The two axes are independent.
3. **Product level**: workers fetch `updateinfo.xml` from each product's
   update repository and look for advisories that reference the ticket's
   CVE. A package match cascade (title → heuristic → `primary.xml`)
   identifies the specific source package fixed by the advisory. When
   matched, `TicketPackageProduct.released_at` is set to the advisory's
   `<issued date>`.
4. Track-level detection only transitions records in `AFFECTED` or
   `ANALYSIS` status; records in a final status are not modified.
   Product-level detection sets `released_at` regardless of affectedness
   status (it is a factual observation, not a status transition).
5. When every active track in a ticket is resolution-complete
   (`NOT_AFFECTED`/`WONT_FIX`, or `FIXED` with all eligible products
   released, or `AFFECTED` with no eligible products remaining), the
   ticket can transition to Resolved.

## Deployment Portability

Sentinel must remain deployable with Docker or Podman for local and simple
environments while preserving a clear path to Kubernetes deployment in the
future. Kubernetes-specific manifests, Helm charts, or Kustomize overlays are
intentionally deferred until the infrastructure target is known. Until then,
runtime and packaging decisions must keep the application stateless,
externally configured, and independently startable.

### Deployment Target

The deployment target is not fixed at this stage. Sentinel must support:

- Docker or Podman for local development and simple self-hosted deployments
- Kubernetes as a future production-capable deployment target

The application code must not depend on one runtime orchestrator. Runtime
differences belong in deployment configuration, not in backend
implementation code.

### Container Images

Backend builds produce standard OCI-compatible images. Docker,
Podman, and Kubernetes must consume the same images without environment-specific
image variants.

The backend image must be able to run distinct process roles by command or
entrypoint configuration. This is the canonical enumeration of all
process roles; see `docs/deployment.md` (Process Architecture) for
operational properties (scalability, volume requirements).

**Runtime processes** (long-running):

- API server (uvicorn)
- Celery worker
- Git worker (Celery worker with dedicated queue and persistent volume —
  see `docs/features/platform/git-fetcher-infrastructure.md`)
- Celery Beat scheduler (singleton)
- IBS RabbitMQ consumer (singleton — see
  `docs/features/integrations/ibs-rabbitmq-integration.md`)

**One-shot jobs**:

- Alembic migration job

This keeps process separation explicit and avoids later refactoring when
moving from Docker/Podman services to Kubernetes Deployments or Jobs.

### Runtime State

Application containers are stateless. They must not rely on local persistent
filesystem state for correctness. Persistent state belongs in PostgreSQL,
Redis, or external services.

Recoverable caches (e.g., git clone volumes used by CVE fetchers) may
use persistent local storage for performance, provided the application
remains correct without them — see
`docs/features/platform/git-fetcher-infrastructure.md` (Recovery).

Local Docker/Podman environments may run PostgreSQL and Redis as containers.
Production environments may instead use managed services or separately managed
workloads. Application configuration must treat PostgreSQL and Redis as
replaceable external dependencies addressed by connection settings.

### Configuration And Secrets

All runtime configuration must be provided through environment variables. This
includes database and Redis connection strings, CORS settings, authentication
settings, and credentials or tokens for
external integrations.

Docker/Podman deployments can provide these values through compose files or
`.env` files. Kubernetes deployments will provide the same values through
ConfigMaps and Secrets. Secrets must not be baked into images or committed to
the repository.

### Database Migrations

Database migrations are a separate operational step. API containers must not
run Alembic migrations automatically during normal startup, because multiple
replicas could start concurrently in Kubernetes.

Migrations should be run through an explicit command or one-shot service in
Docker/Podman environments, and through a dedicated Job or deployment hook in
Kubernetes environments.

### Singleton Processes

Celery Beat and the IBS RabbitMQ consumer are singleton processes.
Running multiple instances causes duplicate task scheduling or duplicate
event processing. The git worker is constrained to a single instance by
volume affinity (ReadWriteOnce), not by a logical singleton requirement.

Local environments run one instance of each. Kubernetes deployments must
enforce singleton constraints unless a future design introduces
distributed locking or leader election.

### API Routing

API endpoints are served under the `/api` path prefix. In production, a
reverse proxy or ingress routes `/api` requests to the backend service.
The API must remain independent of any specific client hosting strategy.

### Repository Scope

This repository contains:
- All product specifications (including future UI specs in `docs/features/`)
- The backend implementation (FastAPI, Celery workers, migrations)
- CI/CD pipelines for the backend

The frontend implementation will be developed in a dedicated repository
against the published OpenAPI contract once backend specifications are
implemented and tested. UI specifications remain here as the single
source of truth for product requirements.

### Clock Synchronization

All application instances in a multi-instance deployment must have their system
clocks synchronized via NTP (or an equivalent time synchronization protocol).
Sentinel relies on timestamps for several security and correctness mechanisms:

- SSO state parameter validation (10-minute TTL window)
- JWT `exp` and `iat` claim verification
- Session expiration enforcement
- Cache TTL calculations (discovery document, JWKS)

Clock skew between instances can shorten or lengthen time-based windows
unpredictably. For example, if the instance generating an SSO state has a clock
2 minutes ahead of the instance processing the callback, the effective validity
window shrinks from 10 minutes to 8 minutes. While modern NTP-synced servers
typically maintain sub-second accuracy (making this negligible in practice),
operators must ensure NTP is configured and running on all hosts.

### Health And Readiness

Runtime services must expose health checks that work for both Docker/Podman
healthchecks and Kubernetes probes. The API should expose lightweight liveness
and readiness checks so orchestrators can distinguish between a running process
and a service that is ready to handle traffic.

`/health` is the liveness endpoint. It should confirm that the API process is
running and able to serve HTTP requests without requiring downstream services.
`/ready` is the readiness endpoint. It should verify required runtime
dependencies, such as PostgreSQL and Redis, before the API receives traffic in
an orchestrated deployment.

For the full endpoint specification (response schemas, checks performed,
failure behavior, orchestrator configuration), see
`docs/features/platform/health-endpoints.md`.

## Environments

- **Development**: `docker-compose.yml` provides PostgreSQL + Redis locally
- **Staging**: auto-deployed from `master` branch (deferred — see
  `docs/deployment.md`)
- **Production**: manually deployed from version tags (`v*`) created by
  the release-please process (see `docs/deployment.md`, Release Process)

## Observability

Sentinel emits structured operational logs (JSON or human-readable
console format, selectable per environment) to stdout/stderr only —
the application never writes, rotates, or backs up log files, in
keeping with the stateless container principle above. Log entries
carry correlation identifiers (`request_id`, `celery_task_id`,
`fetcher_run_id`) so operators can filter the stream by request, task,
or fetcher run rather than grepping free text. Log aggregation,
rotation, and retention are the deployment platform's responsibility,
not the application's.

This is distinct from the audit trail infrastructure
(`docs/features/platform/audit-trail-infrastructure.md`), which
persists business events in PostgreSQL. See
`docs/features/platform/logging.md` for the full operational logging
specification and `docs/deployment.md` (Log Aggregation) for
per-environment operational detail.

## Security Considerations

- Capability-based access control (RBAC with capabilities and scope)
  for all operations
- API authentication required for all non-public endpoints
- Secrets managed via environment variables, never in code
- See `docs/features/identity/rbac.md` for the full authorization model
  (capabilities, scope, predefined roles, endpoint permission map)
