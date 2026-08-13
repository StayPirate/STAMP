<p align="center"><img src=".github/assets/logo.png" alt="Sentinel — Linux Vulnerability Management" width="500"></p>

<p align="center">
<a href="https://github.com/StayPirate/sentinel/actions/workflows/ci.yml"><img src="https://github.com/StayPirate/sentinel/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI"></a>
<a href="https://codecov.io/gh/StayPirate/sentinel"><img src="https://codecov.io/gh/StayPirate/sentinel/graph/badge.svg" alt="codecov"></a>
<a href="https://github.com/StayPirate/sentinel/releases"><img src="https://img.shields.io/github/v/release/StayPirate/sentinel" alt="Release"></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.13-blue?logo=python&logoColor=white" alt="Python"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License"></a>
</p>

Security update management platform for SUSE and openSUSE-based Linux
distributions. Sentinel automates CVE tracking, impact analysis, and
update coordination across multiple maintained distribution versions.

## Overview

### What Sentinel does

- Ingests CVE data from multiple public and internal sources (NVD, MITRE,
  Red Hat, GHSA, OSV, CISA KEV, EPSS, Linux Kernel CNA)
- Creates and manages security tickets from CVE detections
- Tracks which packages, codestreams, and products are affected
- Evaluates product eligibility based on CVSS thresholds and lifecycle
- Detects when security fixes are released via IBS
- Provides a REST API for vulnerability analysts, team leads, and automation
- Offers a CLI for administrative operations

### What Sentinel does NOT do

- Build or submit packages (IBS handles builds)
- Manage product lifecycle or release schedules (SMELT/AIMAAS own this)
- Provision user identities (deferred to external identity provider)
- Provide a web UI (frontend will be developed in a separate repository)

## Architecture

```mermaid
graph TB
    subgraph External Sources
        NVD[NVD / NIST]
        MITRE[MITRE cvelistV5]
        RHSA[Red Hat Security]
        GHSA[GitHub Advisories]
        IBS[IBS / OBS]
        SMELT[SMELT / AIMAAS]
    end

    subgraph Sentinel
        API[FastAPI<br/>REST API]
        Worker[Celery Workers<br/>Fetchers & Tasks]
        Beat[Celery Beat<br/>Scheduler]
        CLI[CLI<br/>Admin Commands]
    end

    subgraph Infrastructure
        PG[(PostgreSQL 16)]
        Redis[(Redis 7)]
    end

    NVD & MITRE & RHSA & GHSA --> Worker
    IBS & SMELT --> Worker
    Worker --> PG
    API --> PG
    CLI --> PG
    Beat --> Redis
    Worker --> Redis
    API --> Redis
```

All runtime processes (API server, Celery worker, git worker, Celery Beat)
run from a single Docker image with different entrypoints. See
[docs/architecture.md](docs/architecture.md) for full architectural details.

## Project Status

![Open Issues](https://img.shields.io/github/issues/StayPirate/sentinel?label=issues%20open&color=orange)
![Closed Issues](https://img.shields.io/github/issues-closed/StayPirate/sentinel?label=issues%20closed&color=green)
![Open PRs](https://img.shields.io/github/issues-pr/StayPirate/sentinel?label=PRs%20open&color=orange)
![Closed PRs](https://img.shields.io/github/issues-pr-closed/StayPirate/sentinel?label=PRs%20closed&color=green)

Sentinel is in **active pre-1.0 development** (current version: `0.3.0`).
The API is not yet considered stable — breaking changes may occur in minor
version bumps.

### Feature progress

- [x] **Identity & Access Management** — authentication, RBAC, user
  management, API keys, identity audit trail
- [x] **Platform Infrastructure** *(partial)* — health endpoints, logging,
  HTTP client, system settings, CLI framework, audit trail base
- [ ] **Fetcher Infrastructure** — BaseFetcher, BaseCVEFetcher, BaseGitFetcher,
  scheduling, monitoring
- [ ] **Tickets & CVE Tracking** — ticket lifecycle, CVE ingestion from 8
  sources, CVSS scoring, severity resolution
- [ ] **Package Tracking** — product catalog, package model, three orthogonal
  dimensions, release detection
- [ ] **External Integrations** — IBS REST client, IBS RabbitMQ consumer
- [ ] **SSO Authentication** *(deferred)* — OIDC integration
- [ ] **External Identity Provisioning** *(deferred)* — directory sync

See the [implementation milestones](https://github.com/StayPirate/sentinel/milestones)
for detailed progress tracking.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.13 |
| API Framework | FastAPI |
| ORM | SQLAlchemy 2.0 (async, asyncpg) |
| Database | PostgreSQL 16 |
| Task Queue | Celery with Redis broker |
| Cache / Coordination | Redis 7 |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| CLI | Click |
| HTTP Client | httpx |

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design and architectural decisions |
| [API Specification](docs/api-spec.md) | REST API conventions, envelope format, errors |
| [Data Model](docs/data-model.md) | Database schema and relationships |
| [Configuration](docs/configuration.md) | Environment variables reference |
| [Deployment](docs/deployment.md) | Deployment guide and CI/CD pipeline |
| [Conventions](docs/conventions.md) | Code patterns and style guide |
| [CLI Reference](docs/cli-reference.md) | CLI commands documentation |
| [Data Sources](docs/data-sources.md) | External data sources catalog |
| [Feature Specs](docs/features/) | Detailed feature specifications |

## API Documentation

Interactive API documentation (Swagger UI) is published to GitHub Pages on
every release: **[API Docs](https://staypirate.github.io/sentinel/)**
