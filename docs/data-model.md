# Data Model

This document describes the database schema for STAMP. All models are
implemented as SQLAlchemy ORM classes in `backend/app/models/`.

## Entity Relationship Overview

```
┌──────────────┐     ┌──────────────────┐
│     CVE      │────▶│   CVESource      │
│              │     │                  │
│  cve_id      │     │  source_type     │
│  description │     │  source_url      │
│  severity    │     │  raw_data        │
│  cvss_score  │     └──────────────────┘
│  published   │
└──────┬───────┘
       │
       ▼ (1:1)
┌──────────────────┐     ┌──────────────────┐
│     Ticket       │────▶│   TicketEvent    │
│                  │     │                  │
│  cve_id (FK,UQ)  │     │  ticket_id (FK)  │
│  status          │     │  user_id (FK)    │
│  assignee_id(FK) │     │  event_type      │
│  duplicate_of_id │     │  old_value       │
│  (FK, self-ref)  │     │  new_value       │
└──────┬───────────┘     │  comment         │
       │                 └──────────────────┘
       │
       ▼ (1:N)
┌────────────────────────────┐     ┌──────────────┐
│  TicketPackageCodestream   │────▶│  Codestream  │
│                            │     │              │
│  ticket_id (FK)            │     │  name        │
│  package_name              │     │  active      │
│  codestream_id (FK)        │     └──────┬───────┘
│  status                    │            │
└──────────┬─────────────────┘            │
           │                              │ (N:M via CodestreamProduct)
           ▼ (1:N)                        │
┌────────────────────────────┐     ┌──────▼───────┐
│   TicketPackageProduct     │────▶│   Product    │
│                            │     │              │
│  tkt_pkg_cs_id (FK)        │     │  name        │
│  product_id (FK)           │     │  cpe         │
│  status                    │     │  cvss_threshold │
│  is_override               │     │  active      │
│  released_at               │     └──────────────┘
└────────────────────────────┘

┌──────────────────┐
│      User        │
│                  │
│  username        │
│  email           │
│  role            │
│  active          │
└──────────────────┘
```

## Tables

### CVE

Represents a Common Vulnerability and Exposure entry.

| Column         | Type         | Constraints          | Description                     |
|----------------|--------------|----------------------|---------------------------------|
| id             | UUID         | PK                   | Internal identifier             |
| cve_id         | VARCHAR(20)  | UNIQUE, NOT NULL     | CVE identifier (e.g., CVE-2024-1234) |
| description    | TEXT         |                      | Vulnerability description       |
| severity       | ENUM         | NOT NULL             | Critical, High, Medium, Low, None |
| cvss_score     | DECIMAL(3,1) |                      | CVSS v3 score (0.0-10.0)       |
| cvss_vector    | VARCHAR(100) |                      | CVSS v3 vector string          |
| published_date | TIMESTAMP    |                      | Date CVE was published         |
| modified_date  | TIMESTAMP    |                      | Date CVE was last modified     |
| status         | ENUM         | NOT NULL, DEFAULT    | Workflow status — tracked on the associated Ticket (see Ticket table). This field is kept for quick queries but is always derived from the Ticket status. |
| created_at     | TIMESTAMP    | NOT NULL, DEFAULT    | Record creation timestamp      |
| updated_at     | TIMESTAMP    | NOT NULL, DEFAULT    | Record update timestamp        |

### CVESource

Tracks the origin of CVE data from different sources.

| Column      | Type        | Constraints      | Description                        |
|-------------|-------------|------------------|------------------------------------|
| id          | UUID        | PK               | Internal identifier                |
| cve_id      | UUID        | FK(cve.id)       | Related CVE                        |
| source_type | ENUM        | NOT NULL         | NVD, SUSE_OVAL, MITRE, etc.       |
| source_url  | VARCHAR     |                  | URL to the source entry            |
| raw_data    | JSONB       |                  | Original data from the source      |
| fetched_at  | TIMESTAMP   | NOT NULL         | When the data was fetched          |

### Codestream

Represents an IBS codestream project where source packages are maintained
and built. See `docs/features/package-tracking.md` for full details.

| Column     | Type      | Constraints          | Description                        |
|------------|-----------|----------------------|------------------------------------|
| id         | UUID      | PK                   | Internal identifier                |
| name       | VARCHAR   | UNIQUE, NOT NULL     | IBS project name (e.g., `SUSE:SLE-15-SP6:Update`) |
| active     | BOOLEAN   | NOT NULL, DEFAULT true | False when SMELT no longer reports this codestream |
| synced_at  | TIMESTAMP |                      | Last sync from SMELT               |
| created_at | TIMESTAMP | NOT NULL, DEFAULT    | Record creation timestamp          |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT    | Record update timestamp            |

### Product

Represents a SUSE commercial product. See
`docs/features/package-tracking.md` for full details.

| Column         | Type         | Constraints          | Description                        |
|----------------|--------------|----------------------|------------------------------------|
| id             | UUID         | PK                   | Internal identifier                |
| name           | VARCHAR      | UNIQUE, NOT NULL     | Product name (e.g., `SLES 15 SP6`) |
| cpe            | VARCHAR      | UNIQUE, nullable     | CPE identifier for this product    |
| cvss_threshold | DECIMAL(3,1) | NOT NULL, DEFAULT 0  | Minimum CVSS score for eligibility (from AIMAAS) |
| active         | BOOLEAN      | NOT NULL, DEFAULT true | False when product is EOL        |
| synced_at      | TIMESTAMP    |                      | Last sync from AIMAAS              |
| created_at     | TIMESTAMP    | NOT NULL, DEFAULT    | Record creation timestamp          |
| updated_at     | TIMESTAMP    | NOT NULL, DEFAULT    | Record update timestamp            |

### CodestreamProduct

Mapping table recording which products receive packages from which
codestreams. Synced from SMELT.

| Column        | Type      | Constraints                  | Description             |
|---------------|-----------|------------------------------|-------------------------|
| id            | UUID      | PK                           | Internal identifier     |
| codestream_id | UUID      | FK(codestream.id), NOT NULL  | Related codestream      |
| product_id    | UUID      | FK(product.id), NOT NULL     | Related product         |
| created_at    | TIMESTAMP | NOT NULL, DEFAULT            | Record creation timestamp |

**Unique constraint**: (codestream_id, product_id)

### TicketPackageCodestream

Records the affectedness status of a source package in a specific codestream
within the context of a ticket. See `docs/features/package-tracking.md` for
status propagation rules.

| Column        | Type      | Constraints                  | Description                        |
|---------------|-----------|------------------------------|------------------------------------|
| id            | UUID      | PK                           | Internal identifier                |
| ticket_id     | UUID      | FK(ticket.id), NOT NULL      | Related ticket                     |
| package_name  | VARCHAR   | NOT NULL                     | Source package name                |
| codestream_id | UUID      | FK(codestream.id), NOT NULL  | Related codestream                 |
| status        | ENUM      | NOT NULL, DEFAULT ANALYSIS   | PackageStatus enum                 |
| created_at    | TIMESTAMP | NOT NULL, DEFAULT            | Record creation timestamp          |
| updated_at    | TIMESTAMP | NOT NULL, DEFAULT            | Record update timestamp            |

**Unique constraint**: (ticket_id, package_name, codestream_id)

### TicketPackageProduct

Records the affectedness status of a source package for a specific product
within the context of a ticket and codestream. See
`docs/features/package-tracking.md` for status inheritance and override rules.

| Column                        | Type      | Constraints                                | Description                        |
|-------------------------------|-----------|--------------------------------------------|------------------------------------|
| id                            | UUID      | PK                                         | Internal identifier                |
| ticket_package_codestream_id  | UUID      | FK(ticket_package_codestream.id), NOT NULL | Parent codestream record           |
| product_id                    | UUID      | FK(product.id), NOT NULL                   | Related product                    |
| status                        | ENUM      | NOT NULL, DEFAULT ANALYSIS                 | PackageStatus enum                 |
| is_override                   | BOOLEAN   | NOT NULL, DEFAULT false                    | True if IM manually overrode the inherited status |
| released_at                   | TIMESTAMP | nullable                                  | When STAMP detected the fix in the product's repository |
| created_at                    | TIMESTAMP | NOT NULL, DEFAULT                          | Record creation timestamp          |
| updated_at                    | TIMESTAMP | NOT NULL, DEFAULT                          | Record update timestamp            |

**Unique constraint**: (ticket_package_codestream_id, product_id)

### PackageStatus Enum

Used by both TicketPackageCodestream and TicketPackageProduct.

| Value             | UI Label      | Color      | Type       |
|-------------------|---------------|------------|------------|
| ANALYSIS          | Analysis      | Neutral    | Non-final  |
| AFFECTED          | Affected      | Red        | Non-final  |
| AFFECTED_RESOLVED | Affected      | Green      | Final      |
| NOT_AFFECTED      | Not Affected  | Green      | Final      |
| WONT_FIX          | Won't Fix     | Green      | Final      |
| IGNORED           | Ignored       | Greyed-out | Final      |
| RELEASED          | Released      | Green      | Final      |

### User

Platform users with role-based access.

| Column     | Type        | Constraints        | Description                      |
|------------|-------------|--------------------|----------------------------------|
| id         | UUID        | PK                 | Internal identifier              |
| username   | VARCHAR     | UNIQUE, NOT NULL   | Login username                   |
| email      | VARCHAR     | UNIQUE, NOT NULL   | Email address                    |
| full_name  | VARCHAR     |                    | Display name                     |
| role       | ENUM        | NOT NULL, DEFAULT  | Admin, Security Team, Packager, Viewer |
| active     | BOOLEAN     | NOT NULL, DEFAULT  | Whether the account is active    |
| created_at | TIMESTAMP   | NOT NULL, DEFAULT  | Record creation timestamp        |
| updated_at | TIMESTAMP   | NOT NULL, DEFAULT  | Record update timestamp          |

### Ticket

Represents the internal workflow unit for a CVE. Each CVE has exactly one
ticket. Tickets track the triage and resolution lifecycle managed by incident
managers (IMs).

| Column          | Type        | Constraints                  | Description                          |
|-----------------|-------------|------------------------------|--------------------------------------|
| id              | UUID        | PK                           | Internal identifier                  |
| cve_id          | UUID        | FK(cve.id), UNIQUE, NOT NULL | Associated CVE (1:1 relationship)    |
| status          | ENUM        | NOT NULL, DEFAULT New        | New, Analysis, Analyzed, Resolved, Ignored, Duplicated |
| assignee_id     | UUID        | FK(user.id), nullable        | IM currently assigned to this ticket |
| duplicate_of_id | UUID        | FK(ticket.id), nullable      | Self-referencing FK to the original ticket when status is Duplicated |
| previous_status | ENUM        | nullable                     | Status before being marked as Duplicated, used to restore on revert |
| created_at      | TIMESTAMP   | NOT NULL, DEFAULT            | Record creation timestamp            |
| updated_at      | TIMESTAMP   | NOT NULL, DEFAULT            | Record update timestamp              |

**Status transitions**:
- New → Analysis (assignment)
- New → Ignored
- Analysis → Analyzed (all affectedness data complete)
- Analysis → Ignored
- Analyzed → Resolved (all updates released)
- Any → Duplicated (reversible)
- Duplicated → previous_status (revert, reassigns to the reverting IM)

### TicketEvent

Audit log of all changes to a ticket. Each event represents a discrete
action (status change, assignment, duplicate operation).

| Column      | Type        | Constraints            | Description                                |
|-------------|-------------|------------------------|--------------------------------------------|
| id          | UUID        | PK                     | Internal identifier                        |
| ticket_id   | UUID        | FK(ticket.id), NOT NULL| Related ticket                             |
| user_id     | UUID        | FK(user.id), NOT NULL  | User who performed the action              |
| event_type  | ENUM        | NOT NULL               | status_change, assignment, duplicate_set, duplicate_removed |
| old_value   | VARCHAR     | nullable               | Previous value (e.g., old status, old assignee username) |
| new_value   | VARCHAR     | nullable               | New value (e.g., new status, new assignee username) |
| comment     | TEXT        | nullable               | Optional note from the IM                  |
| created_at  | TIMESTAMP   | NOT NULL, DEFAULT      | Event timestamp                            |

## Indexes

TBD — will be defined based on query patterns during implementation.

## Notes

- All tables use UUID primary keys
- All tables include `created_at` and `updated_at` timestamps
- ENUM types are defined as PostgreSQL enums
- JSONB is used for flexible storage of source-specific data
- The schema will evolve as features are implemented; this document must be
  updated before any schema changes
- The previous Distribution, Package, and AffectedPackage tables have been
  replaced by Codestream, Product, CodestreamProduct, TicketPackageCodestream,
  and TicketPackageProduct — see `docs/features/package-tracking.md`
