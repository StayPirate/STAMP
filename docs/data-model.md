# Data Model

This document describes the database schema for STAMP. All models are
implemented as SQLAlchemy ORM classes in `backend/app/models/`.

## Entity Relationship Overview

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│     CVE      │────▶│   CVESource      │     │   Distribution   │
│              │     │                  │     │                  │
│  cve_id      │     │  source_type     │     │  name            │
│  description │     │  source_url      │     │  version         │
│  severity    │     │  raw_data        │     │  codename        │
│  cvss_score  │     └──────────────────┘     │  active          │
│  published   │                              └────────┬─────────┘
└──────┬───────┘                                       │
       │              ┌──────────────────┐             │
       │              │     Package      │             │
       │              │                  │◀────────────┘
       │              │  name            │  (DistributionPackage)
       │              │  obs_project     │
       │              │  obs_package     │
       │              └────────┬─────────┘
       │                       │
       ▼                       ▼
┌──────────────────────────────────────┐
│          AffectedPackage             │
│                                      │
│  cve_id (FK)                         │
│  package_id (FK)                     │
│  distribution_id (FK)                │
│  status (affected/not_affected/      │
│          investigating/fixed)        │
│  fixed_version                       │
└──────────────────────────────────────┘

┌──────────────────┐     ┌──────────────────┐
│  SecurityUpdate  │────▶│  UpdatePackage   │
│                  │     │                  │
│  title           │     │  update_id (FK)  │
│  status          │     │  package_id (FK) │
│  severity        │     │  dist_id (FK)    │
│  release_date    │     │  version         │
│  cves (M2M)      │     └──────────────────┘
└──────────────────┘

┌──────────────────┐
│      User        │
│                  │
│  username        │
│  email           │
│  role            │
│  active          │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐     ┌──────────────────┐
│     Ticket       │────▶│   TicketEvent    │
│                  │     │                  │
│  cve_id (FK,UQ)  │     │  ticket_id (FK)  │
│  status          │     │  user_id (FK)    │
│  assignee_id(FK) │     │  event_type      │
│  duplicate_of_id │     │  old_value       │
│  (FK, self-ref)  │     │  new_value       │
└──────────────────┘     │  comment         │
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

### Distribution

Represents a maintained Linux distribution version.

| Column      | Type        | Constraints      | Description                        |
|-------------|-------------|------------------|------------------------------------|
| id          | UUID        | PK               | Internal identifier                |
| name        | VARCHAR     | NOT NULL         | Distribution name (e.g., SLES, openSUSE Leap) |
| version     | VARCHAR     | NOT NULL         | Version string (e.g., 15.5)       |
| codename    | VARCHAR     |                  | Optional codename                  |
| active      | BOOLEAN     | NOT NULL, DEFAULT| Whether this distro is maintained  |
| obs_project | VARCHAR     |                  | OBS project name                   |
| created_at  | TIMESTAMP   | NOT NULL, DEFAULT| Record creation timestamp          |
| updated_at  | TIMESTAMP   | NOT NULL, DEFAULT| Record update timestamp            |

**Unique constraint**: (name, version)

### Package

Represents a software package tracked across distributions.

| Column      | Type        | Constraints      | Description                        |
|-------------|-------------|------------------|------------------------------------|
| id          | UUID        | PK               | Internal identifier                |
| name        | VARCHAR     | NOT NULL, UNIQUE | Package name                       |
| obs_project | VARCHAR     |                  | OBS project containing this package|
| obs_package | VARCHAR     |                  | OBS package name (if different)    |
| git_url     | VARCHAR     |                  | Git repository URL (if applicable) |
| created_at  | TIMESTAMP   | NOT NULL, DEFAULT| Record creation timestamp          |
| updated_at  | TIMESTAMP   | NOT NULL, DEFAULT| Record update timestamp            |

### AffectedPackage

Junction table tracking which packages in which distributions are affected
by which CVEs.

| Column          | Type        | Constraints                | Description               |
|-----------------|-------------|----------------------------|---------------------------|
| id              | UUID        | PK                         | Internal identifier       |
| cve_id          | UUID        | FK(cve.id), NOT NULL       | Related CVE               |
| package_id      | UUID        | FK(package.id), NOT NULL   | Related package           |
| distribution_id | UUID        | FK(distribution.id), NOT NULL | Related distribution   |
| status          | ENUM        | NOT NULL, DEFAULT          | Affected, Not Affected, Investigating, Fixed |
| fixed_version   | VARCHAR     |                            | Version that fixes the CVE|
| notes           | TEXT        |                            | Additional notes          |
| created_at      | TIMESTAMP   | NOT NULL, DEFAULT          | Record creation timestamp |
| updated_at      | TIMESTAMP   | NOT NULL, DEFAULT          | Record update timestamp   |

**Unique constraint**: (cve_id, package_id, distribution_id)

### SecurityUpdate

Represents a security update that addresses one or more CVEs.

| Column       | Type        | Constraints      | Description                       |
|--------------|-------------|------------------|-----------------------------------|
| id           | UUID        | PK               | Internal identifier               |
| title        | VARCHAR     | NOT NULL         | Update title                      |
| description  | TEXT        |                  | Update description                |
| severity     | ENUM        | NOT NULL         | Critical, Important, Moderate, Low|
| status       | ENUM        | NOT NULL, DEFAULT| Draft, In Progress, Testing, Released |
| release_date | TIMESTAMP   |                  | When the update was released      |
| created_by   | UUID        | FK(user.id)      | User who created the update       |
| created_at   | TIMESTAMP   | NOT NULL, DEFAULT| Record creation timestamp         |
| updated_at   | TIMESTAMP   | NOT NULL, DEFAULT| Record update timestamp           |

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
