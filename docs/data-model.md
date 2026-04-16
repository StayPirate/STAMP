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
┌────────────────────────────┐
│  TicketPackageCodestream   │     ┌─────────────────────┐
│                            │     │      Product        │
│  ticket_id (FK)            │     │                     │
│  package_name              │     │  name               │
│  codestream_name           │     │  version            │
│  status                    │     │  display_name       │
│                            │     │  cpe                │
└──────────┬─────────────────┘     │  cvss_threshold     │
           │                       │  fcs                │
           ▼ (1:N)                 │  end_of_gs          │
┌────────────────────────────┐     │  end_of_ltss        │
│   TicketPackageProduct     │────▶│  end_of_espos       │
│                            │     │  end_of_reactive_ltss│
│  tkt_pkg_cs_id (FK)        │     └──────┬──────────────┘
│  product_id (FK)           │            │
│  status                    │            ▼ (1:N)
│  is_override               │     ┌─────────────────────┐
│  released_at               │     │ ProductRepository   │
└────────────────────────────┘     │                     │
                                   │  product_id (FK)    │
┌──────────────────┐               │  repo_name          │
│      User        │               └─────────────────────┘
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

### Product

Represents a SUSE product (base products, LTSS variants, ESPOS variants,
etc.). Each variant is a separate product with its own CPE. Synced
periodically from SMELT (product list and repositories) and enriched with
lifecycle data from AIMAAS. See `docs/features/package-tracking.md` for
full details.

| Column               | Type         | Constraints          | Description                        |
|----------------------|--------------|----------------------|------------------------------------|
| id                   | UUID         | PK                   | Internal identifier                |
| smelt_id             | INTEGER      | UNIQUE, NOT NULL     | Product ID in SMELT                |
| name                 | VARCHAR      | NOT NULL             | Short product name from SMELT (e.g., `SLES-LTSS`) |
| version              | VARCHAR      | NOT NULL             | Product version from SMELT (e.g., `15-SP4`) |
| display_name         | VARCHAR      | NOT NULL             | Human-readable full name from AIMAAS, used in the UI (e.g., `SUSE Linux Enterprise Server LTSS 15 SP4`) |
| cpe                  | VARCHAR      | UNIQUE, NOT NULL     | CPE identifier — primary join key between SMELT and AIMAAS |
| cvss_threshold       | DECIMAL(3,1) | nullable             | Minimum CVSS score for eligibility (from AIMAAS `cvss-threshold` endpoint). NULL means threshold is 0 (all CVEs eligible). |
| fcs                  | DATE         | nullable             | First Customer Shipment date (from AIMAAS) |
| end_of_gs            | DATE         | nullable             | End of General Support (from AIMAAS) |
| end_of_ltss          | DATE         | nullable             | End of Long Term Service Pack Support (from AIMAAS) |
| end_of_espos         | DATE         | nullable             | End of Extended Service Pack Overlap Support (from AIMAAS). Serves a similar purpose to `end_of_ltss` for products that have ESPOS instead of or in addition to LTSS. |
| end_of_reactive_ltss | DATE         | nullable             | End of Reactive LTSS (from AIMAAS). During this phase, Affected status is always green (AFFECTED_RESOLVED) regardless of CVSS. |
| active               | BOOLEAN      | NOT NULL, DEFAULT true | False when product is EOL or no longer reported by SMELT |
| smelt_synced_at      | TIMESTAMP    |                      | Last sync from SMELT               |
| aimaas_synced_at     | TIMESTAMP    |                      | Last sync from AIMAAS              |
| created_at           | TIMESTAMP    | NOT NULL, DEFAULT    | Record creation timestamp          |
| updated_at           | TIMESTAMP    | NOT NULL, DEFAULT    | Record update timestamp            |

**Unique constraint**: (name, version)

### ProductRepository

Maps SMELT repository project names to products. Used to resolve the
`target` values returned by SMELT's `maintainedpackage` endpoint to local
Product records. Synced from SMELT alongside products.

| Column     | Type      | Constraints                  | Description                        |
|------------|-----------|------------------------------|------------------------------------|
| id         | UUID      | PK                           | Internal identifier                |
| product_id | UUID      | FK(product.id), NOT NULL     | Related product                    |
| repo_name  | VARCHAR   | UNIQUE, NOT NULL             | SMELT repository project name (e.g., `SUSE:Updates:SLE-Product-SLES:15-SP4-LTSS:x86_64`) |
| created_at | TIMESTAMP | NOT NULL, DEFAULT            | Record creation timestamp          |

### TicketPackageCodestream

Records the affectedness status of a source package in a specific codestream
within the context of a ticket. See `docs/features/package-tracking.md` for
status propagation rules.

| Column          | Type      | Constraints                  | Description                        |
|-----------------|-----------|------------------------------|------------------------------------|
| id              | UUID      | PK                           | Internal identifier                |
| ticket_id       | UUID      | FK(ticket.id), NOT NULL      | Related ticket                     |
| package_name    | VARCHAR   | NOT NULL                     | Source package name                |
| codestream_name | VARCHAR   | NOT NULL                     | IBS codestream project name (e.g., `SUSE:SLE-15-SP6:Update`). Stored as a string — codestreams are not maintained as a separate table because SMELT does not provide an independent codestream listing. |
| status          | ENUM      | NOT NULL, DEFAULT ANALYSIS   | PackageStatus enum                 |
| created_at      | TIMESTAMP | NOT NULL, DEFAULT            | Record creation timestamp          |
| updated_at      | TIMESTAMP | NOT NULL, DEFAULT            | Record update timestamp            |

**Unique constraint**: (ticket_id, package_name, codestream_name)

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
- New -> Analysis (assignment)
- New -> Ignored
- Analysis -> Analyzed (all affectedness data complete)
- Analysis -> Ignored
- Analyzed -> Resolved (all updates released)
- Any -> Duplicated (reversible)
- Duplicated -> previous_status (revert, reassigns to the reverting IM)

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
  replaced by Product, ProductRepository, TicketPackageCodestream, and
  TicketPackageProduct — see `docs/features/package-tracking.md`
- The previous Codestream and CodestreamProduct tables have been removed.
  Codestream names are stored as strings directly in
  TicketPackageCodestream because SMELT does not expose an endpoint to list
  codestreams independently — they are discovered per-package via the
  `maintainedpackage` endpoint. Product-to-codestream mappings are
  per-package and already captured by the TicketPackageCodestream to
  TicketPackageProduct hierarchy.
