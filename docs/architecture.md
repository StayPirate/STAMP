# Architecture

## System Overview

STAMP is a security update management platform for SUSE and openSUSE-based
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
└──────────────────┘     │  │  CVE Sync  │ │  OBS Sync  │  │
                        │  └────────────┘ └────────────┘  │
┌──────────────────┐     │                                  │
│   Open Build     │◀──▶│  ┌────────────────────────────┐  │
│   Service (OBS)  │     │  │     Impact Analysis        │  │
└──────────────────┘     │  └────────────────────────────┘  │
                        └──────────────────────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │       Redis (Broker/Cache)        │
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
- **Periodic tasks**: Celery Beat for scheduled CVE synchronization
- **Workers**: Separate worker processes for CVE ingestion, OBS interaction,
  and impact analysis

### Database (PostgreSQL)

- **ORM**: SQLAlchemy 2.0 with async support
- **Migrations**: Alembic with autogenerate support
- **Schema**: see `docs/data-model.md`

### External Integrations

#### CVE Sources

- **NVD (NIST)**: REST API v2 for CVE data
- **MITRE**: CVE feed for early CVE information
- Additional sources can be added via the pluggable ingestion architecture

#### IBS (Internal Build Service)

- Internal OBS instance at build.suse.de for SUSE commercial products
- Source packages are maintained in codestream projects (e.g.,
  `SUSE:SLE-15-SP6:Update`)
- STAMP queries IBS to detect when security fixes have been released to
  codestream and product repositories
- See `docs/features/package-tracking.md` for codestream/product concepts

#### SMELT

- Internal SUSE aggregator service (REST API at `smelt.suse.de/api`)
- SMELT internally reads from IBS, channel files, and other sources
- STAMP uses two SMELT endpoints:
  - `GET /api/v1/basic/products/` (paginated): periodic sync of the Product
    table with name, version, CPE, and repository project names
  - `GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1`
    (paginated): on-demand query when adding a package to a ticket. Returns
    codestreams and target repositories for the package. The
    `include_reactive=1` parameter MUST always be used to include products
    in Reactive LTSS phase. All pages MUST be fetched.
- Target repository names from `maintainedpackage` are matched to local
  Product records via the ProductRepository table
- See `docs/features/package-tracking.md` for full integration details

#### AIMAAS

- Internal SUSE service (REST API at `aimaas.suse.de/api`) for product
  lifecycle data and CVSS thresholds
- STAMP uses two AIMAAS endpoints:
  - `GET /api/entity/products/{slug}`: product lifecycle dates (`fcs`,
    `end_of_gs`, `end_of_ltss`, `end_of_espos`, `end_of_reactive_ltss`).
    Matched to local Product records via CPE (identical between SMELT and
    AIMAAS).
  - `GET /api/entity/cvss-threshold`: CVSS threshold for products in
    LTSS/ESPOS phases (~24 entries). Each entry references an AIMAAS
    product ID; STAMP resolves this to a CPE to match locally.
- When thresholds or lifecycle dates change, STAMP re-evaluates eligibility
  for open tickets referencing the affected products

#### Open Build Service (OBS)

- Public instance at build.opensuse.org for openSUSE distributions
- Future: will be used for tracking openSUSE Tumbleweed and Leap packages
- Not currently integrated — see `docs/features/package-tracking.md`

## Data Flow

### CVE Ingestion Flow

1. Celery Beat triggers periodic CVE sync tasks
2. Workers fetch CVE data from configured sources
3. New/updated CVEs are stored in PostgreSQL
4. A Ticket is created automatically for each new CVE
5. STAMP attempts to map CPE data to source package names
6. For mapped packages, SMELT is queried to resolve codestreams and products
7. TicketPackageCodestream and TicketPackageProduct records are created
   automatically with status ANALYSIS

### Package Affectedness Flow

1. IM analyzes a ticket and sets affectedness status per codestream
2. STAMP propagates codestream status to products, adjusting for eligibility
   (CVSS score vs product threshold from AIMAAS)
3. Products not eligible that inherit AFFECTED status receive
   AFFECTED_RESOLVED (green) automatically — other inherited statuses are
   not modified by eligibility
4. Products in Reactive LTSS phase that inherit AFFECTED status receive
   AFFECTED_RESOLVED (green) automatically — regardless of CVSS score
5. IM can override individual product statuses when needed
6. See `docs/features/package-tracking.md` for full status propagation rules

### Release Tracking Flow

1. Celery Beat triggers periodic release status checks
2. Workers query IBS to detect if fixes have landed in codestream repositories
3. Workers check if fixes have been copied to product repositories
4. TicketPackageCodestream and TicketPackageProduct statuses are updated to
   RELEASED when fixes are detected (unless status is WONT_FIX or IGNORED)
5. When all packages in a ticket reach a final status, the ticket can
   transition to Resolved

## Environments

- **Development**: `docker-compose.yml` provides PostgreSQL + Redis locally
- **Staging**: auto-deployed from `master` branch
- **Production**: manually deployed from version tags (`v*`)

## Security Considerations

- Role-based access control (RBAC) for all operations
- API authentication required for all non-public endpoints
- Secrets managed via environment variables, never in code
- See `docs/features/rbac.md` for detailed permission model
