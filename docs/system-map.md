# System Map

Visual overview of the Sentinel platform. This document provides navigational
diagrams to understand how components, data, and features relate to each
other. For full details, follow the links to the relevant specification
documents.

## Table of Contents

- [System Components](#system-components)
- [Data Model](#data-model)
- [Data Flows](#data-flows)
  - [CVE Ingestion](#cve-ingestion)
  - [Package and Release Tracking](#package-and-release-tracking)
  - [Ticket Lifecycle](#ticket-lifecycle)
- [Feature Specification Map](#feature-specification-map)

---

## System Components

How the main components of Sentinel connect to each other and to external
services. See [architecture.md](architecture.md) for full details.

```mermaid
flowchart TB
    subgraph frontend["Frontend"]
        SPA["React SPA<br/>(TypeScript, Vite, shadcn/ui)"]
    end

    subgraph backend["Backend"]
        API["FastAPI API<br/>(api/v1/)"]
        SVC["Services<br/>(business logic)"]
        MDL["SQLAlchemy Models"]
        API --> SVC --> MDL
    end

    subgraph taskqueue["Task Queue"]
        BEAT["Celery Beat<br/>(scheduler)"]
        WORKER["Celery Workers<br/>(background tasks)"]
        BEAT -->|triggers| WORKER
    end

    subgraph datastores["Data Stores"]
        PG[("PostgreSQL")]
        RD[("Redis<br/>(broker + cache)")]
    end

    CONSUMER["IBS RabbitMQ<br/>Consumer"]

    subgraph external["External Services"]
        NVD["NVD<br/>(services.nvd.nist.gov)"]
        MITRE["MITRE<br/>(cveawg.mitre.org)"]
        REDHAT["Red Hat<br/>(access.redhat.com)"]
        IBS["IBS<br/>(build.suse.de)"]
        SMELT["SMELT<br/>(smelt.suse.de)"]
        AIMAAS["AIMAAS<br/>(aimaas.suse.de)"]
        RABBIT["IBS RabbitMQ<br/>(rabbit.suse.de)"]
        LDAP["SUSE AD<br/>(pan.suse.de)"]
    end

    SPA -->|"REST API (HTTP)"| API
    MDL --> PG
    WORKER --> PG
    WORKER <--> RD
    BEAT <--> RD
    CONSUMER --> PG

    WORKER -->|"REST API"| NVD
    WORKER -->|"REST API"| MITRE
    WORKER -->|"REST API"| REDHAT
    WORKER -->|"REST API"| IBS
    WORKER -->|"REST API"| SMELT
    WORKER -->|"REST API"| AIMAAS
    WORKER -->|"LDAPS (port 636)"| LDAP

    CONSUMER -->|"AMQP"| RABBIT
    CONSUMER -->|"REST API"| IBS
    CONSUMER <--> RD

    style frontend fill:#e0f2fe,stroke:#0284c7
    style backend fill:#fef3c7,stroke:#d97706
    style taskqueue fill:#fce7f3,stroke:#db2777
    style datastores fill:#d1fae5,stroke:#059669
    style external fill:#f3e8ff,stroke:#7c3aed
```

---

## Data Model

Entity-relationship diagram showing all database tables and their
connections. Only primary keys and foreign keys are shown for readability.
See [data-model.md](data-model.md) for full column details.

```mermaid
erDiagram
    CVE {
        UUID id PK
        VARCHAR cve_id UK
        ENUM severity
    }

    CVESource {
        UUID id PK
        UUID cve_id FK
        ENUM source_type
    }

    CVECVSSAssessment {
        UUID id PK
        UUID cve_id FK
        VARCHAR provider_name
        VARCHAR cvss_version
        DECIMAL score
    }

    Ticket {
        UUID id PK
        INTEGER sequence_id UK
        UUID cve_id FK "nullable, unique"
        ENUM status
        UUID assignee_id FK "nullable"
        UUID duplicate_of_id FK "nullable, self-ref"
    }

    TicketAuditEvent {
        UUID id PK
        UUID ticket_id FK
        UUID user_id FK "nullable"
        ENUM event_type
    }

    TicketReference {
        UUID id PK
        UUID ticket_id FK
        VARCHAR url
        UUID created_by FK "nullable"
    }

    TicketPackage {
        UUID id PK
        UUID ticket_id FK
        VARCHAR package_name
    }

    TicketPackageTrack {
        UUID id PK
        UUID ticket_package_id FK
        VARCHAR reference
        ENUM status
    }

    TicketPackageProduct {
        UUID id PK
        UUID tpc_id FK
        UUID product_id FK
        BOOLEAN eligible
        BOOLEAN is_eligible_override
    }

    Product {
        UUID id PK
        INTEGER smelt_id UK
        VARCHAR cpe UK
        VARCHAR name
        VARCHAR version
        DECIMAL cvss_threshold "nullable"
    }

    ProductRepository {
        UUID id PK
        UUID product_id FK
        VARCHAR repo_name UK
    }

    User {
        UUID id PK
        VARCHAR username UK
        VARCHAR email UK
        UUID ad_object_guid "UNIQUE, nullable"
        UUID manager_id FK "nullable"
    }

    UserRole {
        UUID id PK
        UUID user_id FK
        ENUM role
        VARCHAR ad_group_cn "nullable"
        UUID assigned_by FK "nullable"
    }

    RoleMapping {
        UUID id PK
        VARCHAR ad_group_cn
        ENUM role
        UUID created_by FK
    }

    SystemSetting {
        VARCHAR key PK
        VARCHAR value
    }

    CodestreamPackageChecksum {
        UUID id PK
        VARCHAR codestream_name
        VARCHAR package_name
        VARCHAR srcmd5
    }

    PackageBugowner {
        UUID id PK
        VARCHAR package_name UK
        ENUM bugowner_type "nullable"
    }

    PackageBugownerMember {
        UUID id PK
        UUID package_bugowner_id FK
        VARCHAR userid
    }

    FetcherConfig {
        VARCHAR fetcher_name PK
        BOOLEAN enabled
    }

    FetcherRun {
        UUID id PK
        VARCHAR fetcher_name
        ENUM status
        UUID triggered_by_user_id FK "nullable"
    }

    FetcherAuditEvent {
        UUID id PK
        VARCHAR fetcher_name
        ENUM event_type
        UUID user_id FK
    }

    FetcherRunWeeklyAggregate {
        UUID id PK
        VARCHAR fetcher_name
        DATE week_start
    }

    CVE ||--o{ CVESource : "has sources"
    CVE ||--o{ CVECVSSAssessment : "has assessments"
    CVE |o--o| Ticket : "tracked by"

    Ticket ||--o{ TicketAuditEvent : "has events"
    Ticket ||--o{ TicketReference : "has references"
    Ticket ||--o{ TicketPackage : "has packages"
    Ticket }o--o| User : "assigned to"
    Ticket }o--o| Ticket : "duplicate of"

    TicketPackage ||--o{ TicketPackageTrack : "has tracks"
    TicketPackageTrack ||--o{ TicketPackageProduct : "has products"
    TicketPackageProduct }o--|| Product : "targets"

    Product ||--o{ ProductRepository : "has repositories"

    User ||--o{ UserRole : "has roles"
    User ||--o{ TicketAuditEvent : "performed"
    RoleMapping }o--|| User : "created by"
    TicketReference }o--o| User : "created by"

    PackageBugowner ||--o{ PackageBugownerMember : "has members"

    FetcherRun }o--o| User : "triggered by"
    FetcherAuditEvent }o--|| User : "performed by"
```

### Entity Groups

| Group | Tables | Purpose |
|-------|--------|---------|
| **CVE Domain** | CVE, CVESource, CVECVSSAssessment | Vulnerability data from external sources |
| **Ticket Domain** | Ticket, TicketAuditEvent, TicketReference, TicketPackage, TicketPackageTrack, TicketPackageProduct | Security workflow and audit trail |
| **Product Domain** | Product, ProductRepository | SUSE distribution products and update repositories |
| **Identity Domain** | User, UserRole, RoleMapping | Users, roles, and AD group mappings |
| **Package Domain** | PackageBugowner, PackageBugownerMember | IBS package maintainer cache |
| **Fetcher Domain** | FetcherConfig, FetcherRun, FetcherAuditEvent, FetcherRunWeeklyAggregate | Background task monitoring |
| **Operational** | CodestreamPackageChecksum, SystemSetting | Release detection cache and system config |

---

## Data Flows

### CVE Ingestion

How CVEs flow from external sources into Sentinel and trigger ticket creation.
See [features/tickets/cve-tracking.md](features/tickets/cve-tracking.md) and
[features/tickets/cvss-scoring.md](features/tickets/cvss-scoring.md).

```mermaid
flowchart LR
    subgraph sources["External Sources"]
        NVD["NVD"]
        MITRE["MITRE"]
        RH["Red Hat"]
    end

    subgraph celery["Celery Workers"]
        SYNC_NVD["sync_cves_nvd"]
        SYNC_MITRE["sync_cves_mitre"]
        SYNC_RH["sync_cvss_redhat"]
    end

    subgraph store["Database Operations"]
        CVE_REC["Create/Update CVE<br/>+ CVESource"]
        CVSS_REC["Create/Update<br/>CVECVSSAssessment"]
        REF_REC["Create<br/>TicketReference"]
        SEV["Recalculate<br/>CVE.severity"]
    end

    subgraph ticket_ops["Ticket Operations"]
        NEW_TKT["Auto-create Ticket<br/>(status: New)"]
        CASCADE["Eligibility cascade<br/>(product thresholds)"]
        EVENT["Create<br/>TicketAuditEvent"]
    end

    NVD --> SYNC_NVD
    MITRE --> SYNC_MITRE
    RH --> SYNC_RH

    SYNC_NVD --> CVE_REC
    SYNC_NVD --> CVSS_REC
    SYNC_NVD --> REF_REC
    SYNC_MITRE --> CVE_REC
    SYNC_MITRE --> REF_REC
    SYNC_RH --> CVSS_REC

    CVE_REC -->|new CVE| NEW_TKT
    CVSS_REC --> SEV
    SEV --> CASCADE
    NEW_TKT --> EVENT
    CASCADE --> EVENT

    style sources fill:#f3e8ff,stroke:#7c3aed
    style celery fill:#fce7f3,stroke:#db2777
    style store fill:#d1fae5,stroke:#059669
    style ticket_ops fill:#fef3c7,stroke:#d97706
```

### Package and Release Tracking

How packages are resolved, tracked across codestreams and products, and how
releases are detected. See
[features/packages/package-model.md](features/packages/package-model.md),
[features/integrations/ibs-integration.md](features/integrations/ibs-integration.md), and
[features/integrations/ibs-rabbitmq-integration.md](features/integrations/ibs-rabbitmq-integration.md).

```mermaid
flowchart LR
    subgraph add_pkg["Package Addition"]
        VA_ADD["VA adds package<br/>to ticket"]
        CPE_MATCH["Auto-add via<br/>CPE match"]
    end

    subgraph resolve["Resolution (on-demand)"]
        SMELT_Q["Query SMELT<br/>maintainedpackage"]
        CREATE_CS["Create<br/>TicketPackageTrack<br/>(per codestream)"]
        CREATE_PR["Create<br/>TicketPackageProduct<br/>(per product)"]
    end

    subgraph status["Track Status & Eligibility"]
        VA_SET["VA sets codestream<br/>status"]
        ELIG["Eligibility check<br/>(CVSS vs threshold)"]
        ELIG_OVR["VA overrides<br/>product eligibility"]
    end

    subgraph release["Release Detection"]
        direction TB
        RT["Real-time:<br/>IBS RabbitMQ<br/>Consumer"]
        PERIODIC["Periodic:<br/>check_ibs_track_releases<br/>(daily 02:00 UTC)"]
        MD5["Shared MD5 cache<br/>(CodestreamPackageChecksum)"]
        DIFF["IBS diff analysis<br/>(CVE-ID in changes)"]
        CS_REL["Codestream → FIXED"]

        RT --> MD5
        PERIODIC --> MD5
        MD5 --> DIFF
        DIFF --> CS_REL
    end

    subgraph prod_release["Product Release"]
        UINFO["Fetch updateinfo.xml<br/>from product repos"]
        MATCH["Match advisory<br/>to CVE + package"]
        PR_REL["Product released_at set"]

        UINFO --> MATCH --> PR_REL
    end

    VA_ADD --> SMELT_Q
    CPE_MATCH --> SMELT_Q
    SMELT_Q --> CREATE_CS --> CREATE_PR

    VA_SET --> ELIG
    ELIG_OVR -.->|manual| ELIG

    style add_pkg fill:#e0f2fe,stroke:#0284c7
    style resolve fill:#fef3c7,stroke:#d97706
    style status fill:#d1fae5,stroke:#059669
    style release fill:#fce7f3,stroke:#db2777
    style prod_release fill:#f3e8ff,stroke:#7c3aed
```

### Ticket Lifecycle

State machine for ticket status transitions. Gates control automatic
transitions between Analysis, Analyzed, and Resolved. See
[features/tickets/tickets.md](features/tickets/tickets.md).

```mermaid
flowchart TD
    NEW["🔵 New"]
    ANALYSIS["🟡 Analysis"]
    ANALYZED["🟢 Analyzed"]
    RESOLVED["✅ Resolved"]
    IGNORED["⚪ Ignored"]
    DUPLICATED["🔗 Duplicated"]

    NEW -->|"assignment or<br/>any modifying operation"| ANALYSIS
    NEW -->|"manual or<br/>NVD rejection"| IGNORED

    ANALYSIS -->|"✓ all gates met:<br/>≥1 package,<br/>no ANALYSIS statuses,<br/>severity set,<br/>SUSE CVSS (if CVE)"| ANALYZED
    ANALYSIS -->|"manual"| IGNORED

    ANALYZED -->|"✓ all packages<br/>in final status"| RESOLVED
    ANALYZED -->|"gate conditions<br/>no longer met"| ANALYSIS

    RESOLVED -->|"resolved gates broken,<br/>analyzed gates still met"| ANALYZED
    RESOLVED -->|"both gates broken"| ANALYSIS

    NEW -->|"manual"| DUPLICATED
    ANALYSIS -->|"manual"| DUPLICATED
    ANALYZED -->|"manual"| DUPLICATED
    RESOLVED -->|"manual"| DUPLICATED

    DUPLICATED -->|"revert:<br/>_reenter_gate_zone"| NEW
    IGNORED -->|"reopen:<br/>_reenter_gate_zone"| NEW

    NEW -.->|"evaluate promotes"| ANALYSIS

    style NEW fill:#dbeafe,stroke:#2563eb
    style ANALYSIS fill:#fef9c3,stroke:#ca8a04
    style ANALYZED fill:#dcfce7,stroke:#16a34a
    style RESOLVED fill:#d1fae5,stroke:#059669
    style IGNORED fill:#f3f4f6,stroke:#6b7280
    style DUPLICATED fill:#e0e7ff,stroke:#4f46e5
```

**Analyzed gate** (all must be true):
- At least one package added to the ticket
- No `TicketPackageTrack` in `ANALYSIS` status
- Severity is set (non-None)
- If CVE is associated: SUSE CVSS assessment exists

**Resolved gate**: all active `TicketPackageTrack` records are in a final
status (`FIXED`, `NOT_AFFECTED`, or `WONT_FIX`) and all eligible products
under `FIXED` tracks have `released_at IS NOT NULL`. Tracks/products are
soft-deleted rather than using a separate `IGNORED` status. Delivery status
(`PENDING`/`IN_PROGRESS`/`RELEASED`) is tracked independently for workflow
visibility but is not a gate condition.

---

## Feature Specification Map

How the 30 feature specifications relate to each other. Arrows indicate
dependencies (A → B means A depends on or references B). Specs are
grouped by domain. See individual specs in [features/](features/).

```mermaid
flowchart TD
    subgraph core["Core"]
        TICKETS["tickets"]
        HISTORY["ticket-audit-log"]
        PKG["package-model"]
    end

    subgraph ingestion["Data Ingestion"]
        CVE["cve-tracking"]
        CVSS["cvss-scoring"]
        REFS["references"]
    end

    subgraph integration["External Integration"]
        OBS["ibs-integration"]
        RABBIT["ibs-rabbitmq-integration"]
        BUGOWNER["package-bugowner"]
        MAINT["maintainer"]
    end

    subgraph identity["Identity and Access"]
        ADI["ad-integration"]
        RBAC["rbac"]
    end

    subgraph platform["Platform"]
        SETTINGS["system-settings"]
        FETCHER_INFRA["fetcher-infrastructure"]
        FETCHER["fetcher-operations"]
    end

    %% Core internal links
    TICKETS <--> PKG
    TICKETS --> HISTORY

    %% Ingestion → Core
    CVE --> TICKETS
    CVE --> CVSS
    CVE --> REFS
    CVSS --> TICKETS
    CVSS --> PKG

    %% Integration → Core
    OBS --> PKG
    RABBIT --> PKG
    RABBIT --> OBS
    BUGOWNER --> PKG
    BUGOWNER --> OBS
    MAINT --> BUGOWNER
    MAINT --> PKG

    %% Identity → Core
    ADI --> RBAC
    RBAC --> TICKETS

    %% Platform → everything
    SETTINGS --> CVSS
    SETTINGS --> TICKETS
    FETCHER --> CVE
    FETCHER --> RABBIT

    %% History links
    HISTORY --> PKG

    style core fill:#fef3c7,stroke:#d97706
    style ingestion fill:#d1fae5,stroke:#059669
    style integration fill:#f3e8ff,stroke:#7c3aed
    style identity fill:#e0f2fe,stroke:#0284c7
    style platform fill:#fce7f3,stroke:#db2777
```

### Hub Specifications

The two most interconnected specifications, referenced by almost every
other feature:

- **[tickets](features/tickets/tickets.md)**: the central workflow entity — ticket
  creation, lifecycle, status gates, severity resolution. The service-layer
  module contract is in
  [ticket-mutations](features/tickets/ticket-mutations.md)
- **[package-model](features/packages/package-model.md)**: codestream/product
  resolution, status propagation, eligibility rules, and release detection

### Specification Index

| Spec | Domain | Summary |
|------|--------|---------|
| [tickets](features/tickets/tickets.md) | Core | Ticket entity, lifecycle, gates, severity resolution |
| [ticket-audit-log](features/tickets/ticket-audit-log.md) | Core | Audit trail via TicketAuditEvent records |
| [package-model](features/packages/package-model.md) | Core | Track affectedness, product eligibility, and release detection |
| [cve-tracking](features/tickets/cve-tracking.md) | Ingestion | CVE sync from NVD, MITRE, and other sources |
| [cvss-scoring](features/tickets/cvss-scoring.md) | Ingestion | Multi-provider CVSS assessment and severity derivation |
| [references](features/tickets/ticket-references.md) | Ingestion | External links on tickets (auto and manual) |
| [ibs-integration](features/integrations/ibs-integration.md) | Integration | IBS API client for source info, diffs, bugowners |
| [ibs-rabbitmq-integration](features/integrations/ibs-rabbitmq-integration.md) | Integration | Real-time release detection via IBS RabbitMQ |
| [package-bugowner](features/packages/package-bugowner.md) | Integration | IBS package maintainer cache |
| [ad-integration](features/identity/ad-integration.md) | Identity | SUSE AD sync for user provisioning and roles |
| [rbac](features/identity/rbac.md) | Identity | Role-based access control and permissions |
| [system-settings](features/platform/system-settings.md) | Platform | System settings (default CVSS version) |
| [fetcher-infrastructure](features/platform/fetcher-infrastructure.md) | Platform | BaseFetcher base class, registry, data model |
| [fetcher-operations](features/platform/fetcher-operations.md) | Platform | Background task monitoring, API, and CLI |
| [maintainer](features/packages/maintainer.md) | Integration | Maintainer-oriented package/ticket views |
