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
│  NVD, SUSE OVAL, │     │                                  │
│  MITRE, others   │     │  ┌────────────┐ ┌────────────┐  │
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
- **SUSE Security OVAL**: XML feeds for SUSE-specific vulnerability data
- **MITRE**: CVE feed for early CVE information
- Additional sources can be added via the pluggable ingestion architecture

#### Open Build Service (OBS)

- REST API for package metadata and build status
- Source management for some distributions
- Build triggering and monitoring

## Data Flow

### CVE Ingestion Flow

1. Celery Beat triggers periodic CVE sync tasks
2. Workers fetch CVE data from configured sources
3. New/updated CVEs are stored in PostgreSQL
4. Impact analysis runs to determine affected distributions/packages
5. Notifications are generated for high-severity CVEs

### Update Coordination Flow

1. Security team reviews CVE impact assessment
2. Patches are prepared and linked to CVEs
3. Updates are submitted to OBS for building
4. Build status is monitored via OBS API
5. Completed updates are released to repositories

## Environments

- **Development**: `docker-compose.yml` provides PostgreSQL + Redis locally
- **Staging**: auto-deployed from `main` branch
- **Production**: manually deployed from version tags (`v*`)

## Security Considerations

- Role-based access control (RBAC) for all operations
- API authentication required for all non-public endpoints
- Secrets managed via environment variables, never in code
- See `docs/features/rbac.md` for detailed permission model
