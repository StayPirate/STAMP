# Architecture

## System Overview

Sentinel is a security update management platform for SUSE and openSUSE-based
Linux distributions. It automates CVE tracking, impact analysis, and update
coordination across multiple maintained distribution versions.

## High-Level Architecture

```
┌─────────────────┐     ┌──────────────────────────────────┐
│   React SPA     │────▶│         FastAPI Backend           │
│  (TypeScript)   │     │                                  │
└─────────────────┘     │  ┌─────────┐  ┌──────────────┐  │
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
└──────────────────┘     │  Consumes suse.obs.package.commit│
                        │  events for real-time codestream  │
                        │  release detection. Shares MD5    │
                        │  cache with periodic fetcher.     │
                        └──────────────────────────────────┘
```

## Components

### Frontend (React SPA)

- **Framework**: React with TypeScript
- **Build tool**: Vite
- **Component library**: shadcn/ui
- **Routing**: React Router
- **State management**: TBD (React Query for server state)
- **Location**: `frontend/src/`

### Backend (FastAPI)

- **Framework**: FastAPI (async)
- **ORM**: SQLAlchemy 2.0 (async)
- **Migrations**: Alembic
- **Validation**: Pydantic v2
- **Authentication**: TBD (JWT or session-based)
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
- **Result backend**: Redis
- **Periodic tasks**: Celery Beat with dynamic scheduling (`celery-redbeat`)
- **Workers**: Separate worker processes for CVE ingestion, OBS interaction,
  and impact analysis
- **Fetcher infrastructure**: all background tasks that fetch data from
  external sources inherit from `BaseFetcher`
  (`app/services/base_fetcher.py`), which provides automatic execution
  tracking, metric collection, and registry. The fetcher registry feeds
  a dashboard that shows execution history, performance charts, and
   operational controls. See `docs/features/platform/fetcher-infrastructure.md`
   for the base class contract and `docs/features/platform/fetcher-dashboard.md`
   for the monitoring dashboard.

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
  codestream and product repositories
- **Real-time event consumer**: Sentinel connects to the IBS RabbitMQ message
  bus (`rabbit.suse.de`) and consumes `suse.obs.package.commit` events for
  near-real-time codestream-level release detection. The periodic polling
  fetcher (`check_codestream_releases`, every 24 hours at 02:00 UTC)
  serves as a catch-up mechanism for events missed during downtime. See
  `docs/features/integrations/ibs-rabbitmq-integration.md` for the full specification.
- **Submission tracking**: the same RabbitMQ consumer also processes
  `suse.obs.request.create` and `suse.obs.request.state_change` events to
  track IBS submission requests (SRs) and release requests (RRs),
  providing VAs visibility into the MU process progression. A periodic
  fetcher (`RequestSyncFetcher`, 02:30 UTC) handles catch-up. See
  `docs/features/packages/ibs-submission-tracking.md`.
- **Package bugowner resolution**: Sentinel queries IBS to resolve the
  bugowner (maintainer) of each source package tracked in tickets. This
  data is cached locally and maintained by a periodic fetcher. See
  `docs/features/packages/package-bugowner.md`.
- See `docs/features/packages/package-tracking.md` for codestream/product concepts

#### SMELT

- Internal SUSE aggregator service (REST API at `smelt.suse.de/api`)
- SMELT internally reads from IBS, channel files, and other sources
- Sentinel uses two SMELT endpoints:
  - `GET /api/v1/basic/products/` (paginated): periodic sync of the Product
    table with name, version, CPE, and repository project names
  - `GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1`
    (paginated): on-demand query when adding a package to a ticket. Returns
    codestreams and target repositories for the package. The
    `include_reactive=1` parameter MUST always be used to include products
    in Reactive LTSS phase. All pages MUST be fetched.
- Target repository names from `maintainedpackage` are matched to local
  Product records via the ProductRepository table
- See `docs/features/packages/package-tracking.md` for full integration details

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

#### SUSE Active Directory

- Internal AD at `pan.suse.de` for SUSE employee identity data
- Sentinel syncs all active employees into its User table daily via the
  `sync_ldap_directory` fetcher (BaseFetcher subclass)
- Imported attributes: `sAMAccountName`, `cn`, `mail`, `manager` (DN),
  `EMPLOYEESTATUS`, `MEMBEROF` (transient, not persisted)
- AD group memberships (`MEMBEROF`) are used to derive Sentinel roles via
  admin-configurable RoleMapping rules
- Direct line manager (`manager` DN) is resolved and stored for
  notification escalation and maintainer task management
- Connection: anonymous bind on port 636 (LDAPS — TLS validated against
  SUSE Trust Root CA committed at `certs/SUSE_Trust_Root.crt`). TLS is
  required because `MEMBEROF` data drives role assignment including admin
  privileges — see security rationale in `docs/features/identity/ad-integration.md`
- See `docs/features/identity/ad-integration.md` for the full specification

## Data Flow

### CVE Ingestion Flow

1. Celery Beat triggers periodic CVE sync tasks
2. Workers fetch CVE data from configured sources
3. New/updated CVEs are stored in PostgreSQL
4. A Ticket is created automatically for each new CVE
5. Sentinel attempts to map CPE data to source package names
6. For mapped packages, SMELT is queried to resolve codestreams and products
7. TicketPackageCodestream and TicketPackageProduct records are created
   automatically with status ANALYSIS

### Manual Ticket Creation

Tickets can also be created manually by Vulnerability Analysts without an
associated CVE — for example, to track security issues reported through
internal bug trackers before a CVE-ID is assigned. A CVE can be
associated with the ticket later. See `docs/features/tickets/tickets.md` for
the full ticket specification.

### Package Affectedness Flow

1. VA analyzes a ticket and sets affectedness status per codestream
2. Sentinel propagates codestream status to products, adjusting for eligibility
   only when the propagated status is AFFECTED (CVSS score vs product
   threshold from AIMAAS)
3. Products not eligible that inherit AFFECTED status receive
   AFFECTED_RESOLVED (green) automatically — other inherited statuses are
   not modified by eligibility
4. Products in Reactive LTSS phase that inherit AFFECTED status receive
   AFFECTED_RESOLVED (green) automatically — regardless of CVSS score
5. If all products under a codestream are AFFECTED_RESOLVED (no eligible
   product), the codestream itself is set to AFFECTED_RESOLVED automatically;
   if a product later becomes eligible again, the codestream reverts to
   AFFECTED
6. VA can override individual product statuses when needed
7. See `docs/features/packages/package-tracking.md` for full status propagation rules

### Release Tracking Flow

Release detection runs on two **independent** levels — codestream and
product — through different mechanisms. See
`docs/features/packages/ibs-codestream-release-detection.md` and
`docs/features/packages/ibs-product-release-detection.md` for the
authoritative details.

1. Codestream-level detection uses two complementary mechanisms:
   the `IBSEventConsumer` (real-time via IBS RabbitMQ) and the periodic
   `check_codestream_releases` fetcher (catch-up every 24 hours at
   02:00 UTC). Both share the same MD5 cache to avoid duplicate work.
   See `docs/features/integrations/ibs-rabbitmq-integration.md`.
2. **Codestream level**: the consumer or fetcher queries IBS diff endpoints
   (see `docs/features/integrations/ibs-integration.md` and
   `docs/features/packages/ibs-codestream-release-detection.md`) to detect whether
   the fix for the ticket's CVE has landed in the codestream IBS project.
   When detected, `TicketPackageCodestream.status` is set to `RELEASED`.
3. **Product level**: workers fetch `updateinfo.xml` from each product's
   update repository and look for advisories that reference the ticket's
   CVE. A package match cascade (title → heuristic → `primary.xml`)
   identifies the specific source package fixed by the advisory. When
   matched, `TicketPackageProduct.status` is set to `RELEASED` and
   `released_at` is set to the advisory's `<issued date>`.
4. Both levels honor the protected states `WONT_FIX` and `IGNORED`, which
   are never modified automatically.
5. When all packages in a ticket reach a final status, the ticket can
   transition to Resolved.

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
differences belong in deployment configuration, not in backend or frontend
implementation code.

### Container Images

Backend and frontend builds produce standard OCI-compatible images. Docker,
Podman, and Kubernetes must consume the same images without environment-specific
image variants.

The backend image must be able to run distinct process roles by command or
entrypoint configuration:

- FastAPI API server
- Celery worker
- Celery Beat scheduler
- Alembic migration job
- Long-running integration consumers, such as the IBS RabbitMQ consumer

This keeps process separation explicit and avoids later refactoring when moving
from Docker/Podman services to Kubernetes Deployments or Jobs.

### Runtime State

Application containers are stateless. They must not rely on local persistent
filesystem state for correctness. Persistent state belongs in PostgreSQL,
Redis, or external services.

Local Docker/Podman environments may run PostgreSQL and Redis as containers.
Production environments may instead use managed services or separately managed
workloads. Application configuration must treat PostgreSQL and Redis as
replaceable external dependencies addressed by connection settings.

### Configuration And Secrets

All runtime configuration must be provided through environment variables. This
includes database and Redis connection strings, CORS settings, frontend API
base configuration, authentication settings, and credentials or tokens for
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

Celery Beat is a singleton process. Local environments run a single scheduler
service. Kubernetes deployments must also ensure only one active scheduler is
running unless a future design introduces an explicit distributed locking or
leader election mechanism.

Other long-running integration consumers must document whether they are safe to
scale horizontally before more than one replica is deployed.

### Frontend And API Routing

The frontend should use a stable API path, preferably `/api`, with routing to
the backend handled by nginx, a reverse proxy, or a Kubernetes ingress. This
keeps browser-facing configuration consistent across Docker/Podman and
Kubernetes and avoids coupling the frontend bundle to an orchestrator-specific
backend hostname.

The current frontend container may provide a default nginx upstream suitable for
local Docker/Podman deployments, such as a backend service named `backend` on
port `8000`. This is a deployment default, not an application contract.
Kubernetes deployments should normally route `/api` at the ingress or reverse
proxy layer before requests reach the static frontend container. If the
frontend container is responsible for proxying API requests in a given
environment, its nginx upstream configuration must be provided by deployment
configuration, such as a mounted config file, without rebuilding the image.

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

## Environments

- **Development**: `docker-compose.yml` provides PostgreSQL + Redis locally
- **Staging**: auto-deployed from `master` branch
- **Production**: manually deployed from version tags (`v*`)

## Security Considerations

- Role-based access control (RBAC) for all operations
- API authentication required for all non-public endpoints
- Secrets managed via environment variables, never in code
- See `docs/features/identity/rbac.md` for detailed permission model
