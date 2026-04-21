# Data Model

This document describes the database schema for STAMP. All models are
implemented as SQLAlchemy ORM classes in `backend/app/models/`.

## Entity Relationship Overview

```
┌──────────────────┐ (1:N) ┌──────────────────┐
│       CVE        │──────▶│    CVESource     │
│                  │       │                  │
│  cve_id          │       │  cve_id (FK)     │
│  description     │       │  source_type     │
│  severity        │       │  source_url      │
│  published_date  │       │  raw_data        │
│  nvd_status      │       └──────────────────┘
└──────┬───┬───────┘
       │   │
       │   ▼ (1:N)
       │  ┌──────────────────────┐
       │  │  CVECVSSAssessment   │
       │  │                      │
       │  │  cve_id (FK)         │
       │  │  provider_name       │
       │  │  cvss_version        │
       │  │  score               │
       │  │  vector              │
       │  └──────────────────────┘
       │
       ▼ (1:1)
┌──────────────────┐ (1:N) ┌──────────────────┐
│     Ticket       │──────▶│   TicketEvent    │
│                  │       │                  │
│  cve_id (FK,UQ)  │       │  ticket_id (FK)  │
│  status          │       │  user_id (FK) ──────┐
│  assignee_id (FK)──┐     │  event_type      │  │
│  duplicate_of_id │  │    │  old_value       │  │
│  (FK, self-ref)  │  │    │  new_value       │  │
│  previous_status │  │    │  comment         │  │
└──────┬───────────┘  │    └──────────────────┘  │
       │              │                          │
       │              │    ┌──────────────────┐   │
       │              └───▶│      User        │◀──┘
       │                   │                  │
       │                   │  username        │
       │                   │  email           │
       │                   │  full_name       │
       │                   │  active          │
       │                   └────────┬─────────┘
       │                            │
       │                            ▼ (1:N)
       │                   ┌──────────────────┐
       │                   │    UserRole      │
       │                   │                  │
       │                   │  user_id (FK)    │
       │                   │  role            │
       │                   └──────────────────┘
       │
       │
       ▼ (1:N)
┌──────────────────┐
│ TicketReference  │
│                  │
│  ticket_id (FK)  │
│  url             │
│  title           │
│  source          │
│  tags            │
│  created_by (FK)─────────────────────────┐
└──────────────────┘                       │
       │                                   │
       ▼ (1:N)                             │
┌────────────────────────────┐             │
│  TicketPackageCodestream   │     ┌───────┴─────────────────┐
│                            │     │        Product          │
│  ticket_id (FK)            │     │                         │
│  package_name              │     │  smelt_id               │
│  codestream_name           │     │  name                   │
│  status                    │     │  version                │
│                            │     │  display_name           │
└──────────┬─────────────────┘     │  cpe                    │
           │                       │  cvss_threshold         │
           ▼ (1:N)                 │  active                 │
┌────────────────────────────┐     │  fcs                    │
│   TicketPackageProduct     │────▶│  end_of_gs              │
│                            │     │  end_of_ltss            │
│  tpc_id (FK) *             │     │  end_of_espos           │
│  product_id (FK)           │     │  end_of_reactive_ltss   │
│  status                    │     └──────┬──────────────────┘
│  is_override               │            │
│  released_at               │            ▼ (1:N)
└────────────────────────────┘     ┌─────────────────────────┐
                                   │   ProductRepository     │
┌──────────────────┐               │                         │
│  SystemSetting   │               │  product_id (FK)        │
│                  │               │  repo_name              │
│  key (PK)        │               └─────────────────────────┘
│  value           │
└──────────────────┘       ┌─────────────────────────────────┐
                           │ CodestreamPackageChecksum        │
                           │ (operational cache)              │
                           │                                 │
                           │  codestream_name                │
                           │  package_name                   │
                           │  srcmd5                         │
                           │  last_seen_at                   │
                           └─────────────────────────────────┘

┌──────────────────────┐   ┌─────────────────────────────────┐
│  FetcherConfig       │   │ FetcherRun                       │
│                      │   │                                 │
│  fetcher_name (PK)   │   │  fetcher_name                   │
│  enabled             │   │  started_at / finished_at       │
│  schedule_override   │   │  duration_seconds               │
│  timeout_seconds     │   │  status                         │
│  rate_limit          │   │  items_created/updated/failed   │
└──────────────────────┘   │  error_message                  │
                           │  error_traceback                │
┌──────────────────────┐   │  triggered_by                   │
│  FetcherAuditLog     │   │  triggered_by_user_id (FK)      │
│                      │   └─────────────────────────────────┘
│  fetcher_name        │
│  action              │   ┌─────────────────────────────────┐
│  performed_by_user_id│   │ FetcherRunWeeklyAggregate        │
│  (FK)                │   │                                 │
│  details             │   │  fetcher_name                   │
└──────────────────────┘   │  week_start                     │
                           │  run_count                      │
                           │  success/failure/partial_count  │
                           │  avg/min/max_duration_seconds   │
                           │  total_items_created/updated    │
                           │  total_items_failed             │
                           └─────────────────────────────────┘

* tpc_id = ticket_package_codestream_id (abbreviated for diagram readability)
```

## Tables

### CVE

Represents a Common Vulnerability and Exposure entry.

| Column         | Type         | Constraints          | Description                     |
|----------------|--------------|----------------------|---------------------------------|
| id             | UUID         | PK                   | Internal identifier             |
| cve_id         | VARCHAR(20)  | UNIQUE, NOT NULL     | CVE identifier (e.g., CVE-2024-1234) |
| description    | TEXT         |                      | Vulnerability description       |
| severity       | ENUM         | NOT NULL, DEFAULT None | Critical, High, Medium, Low, None — denormalized field, always derived from CVSS assessments via the resolution cascade (see `docs/features/cvss-scoring.md`). Recalculated whenever CVSS assessments change or the default CVSS version is modified. |
| published_date | TIMESTAMP    |                      | Date CVE was published         |
| modified_date  | TIMESTAMP    |                      | Date CVE was last modified     |
| nvd_status     | VARCHAR      |                      | NVD vulnerability status (e.g., `Analyzed`, `Rejected`, `Modified`). Updated during NVD sync. See `docs/features/cve-tracking.md` for handling rules. |
| created_at     | TIMESTAMP    | NOT NULL, DEFAULT    | Record creation timestamp      |
| updated_at     | TIMESTAMP    | NOT NULL, DEFAULT    | Record update timestamp        |

### CVESource

Tracks the origin of CVE data from different sources.

| Column      | Type        | Constraints      | Description                        |
|-------------|-------------|------------------|------------------------------------|
| id          | UUID        | PK               | Internal identifier                |
| cve_id      | UUID        | FK(cve.id)       | Related CVE                        |
| source_type | ENUM        | NOT NULL         | NVD, MITRE, etc.       |
| source_url  | VARCHAR     |                  | URL to the source entry            |
| raw_data    | JSONB       |                  | Original data from the source      |
| fetched_at  | TIMESTAMP   | NOT NULL         | When the data was fetched          |
| created_at  | TIMESTAMP   | NOT NULL, DEFAULT| Record creation timestamp          |
| updated_at  | TIMESTAMP   | NOT NULL, DEFAULT| Record update timestamp            |

### CVECVSSAssessment

Stores individual CVSS assessments from multiple providers for each CVE.
A CVE can have assessments from NVD, CNA vendors, Red Hat, and SUSE (IM
input). Each provider may supply assessments for multiple CVSS versions.
See `docs/features/cvss-scoring.md` for the full specification.

| Column        | Type          | Constraints                            | Description                        |
|---------------|---------------|----------------------------------------|------------------------------------|
| id            | UUID          | PK                                     | Internal identifier                |
| cve_id        | UUID          | FK(cve.id), NOT NULL                   | Related CVE                        |
| provider_name | VARCHAR       | NOT NULL                               | Human-readable provider name (e.g., `"NVD"`, `"Intel Corporation"`, `"Red Hat"`, `"SUSE"`) |
| cvss_version  | VARCHAR(10)   | NOT NULL                               | CVSS version (e.g., `"3.1"`, `"4.0"`, `"2.0"`) |
| score         | DECIMAL(3,1)  | NOT NULL                               | Calculated base score (0.0-10.0)   |
| vector        | VARCHAR(200)  | NOT NULL                               | Full CVSS vector string            |
| created_at    | TIMESTAMP     | NOT NULL, DEFAULT                      | Record creation timestamp          |
| updated_at    | TIMESTAMP     | NOT NULL, DEFAULT                      | Record update timestamp            |

**Unique constraint**: (cve_id, provider_name, cvss_version)

**Notes**:
- `provider_name` for NVD Primary assessments is always `"NVD"`
- `provider_name` for NVD Secondary (CNA) assessments is resolved from the
  NVD Source API to a human-readable name (e.g., `"Intel Corporation"`)
- `provider_name` for the SUSE internal assessment is always `"SUSE"`
- When a direct source (e.g., Red Hat API) provides data that also exists
  as an NVD Secondary, the direct source takes priority and overwrites the
  NVD Secondary record for the same `provider_name` and `cvss_version`

### SystemSetting

Key-value store for system-wide configuration. See
`docs/features/admin.md` for details.

| Column     | Type        | Constraints        | Description                      |
|------------|-------------|--------------------|----------------------------------|
| key        | VARCHAR     | PK                 | Setting identifier (e.g., `default_cvss_version`) |
| value      | VARCHAR     | NOT NULL           | Setting value                    |
| updated_at | TIMESTAMP   | NOT NULL, DEFAULT  | Last modification timestamp      |

**Initial data**:

| Key                    | Initial Value |
|------------------------|---------------|
| `default_cvss_version` | `3.1`         |

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

Platform users with role-based access. Users can hold zero, one, or
multiple roles via the UserRole junction table. A user with no roles has
the same access as an unauthenticated user (read-only on public data).

| Column     | Type        | Constraints        | Description                      |
|------------|-------------|--------------------|----------------------------------|
| id         | UUID        | PK                 | Internal identifier              |
| username   | VARCHAR     | UNIQUE, NOT NULL   | Login username                   |
| email      | VARCHAR     | UNIQUE, NOT NULL   | Email address                    |
| full_name  | VARCHAR     |                    | Display name                     |
| active     | BOOLEAN     | NOT NULL, DEFAULT  | Whether the account is active    |
| created_at | TIMESTAMP   | NOT NULL, DEFAULT  | Record creation timestamp        |
| updated_at | TIMESTAMP   | NOT NULL, DEFAULT  | Record update timestamp          |

### UserRole

Junction table linking users to roles. A user may have zero, one, or
multiple roles assigned.

| Column     | Type        | Constraints                  | Description                      |
|------------|-------------|------------------------------|----------------------------------|
| id         | UUID        | PK                           | Internal identifier              |
| user_id    | UUID        | FK(user.id), NOT NULL        | Associated user                  |
| role       | ENUM        | NOT NULL                     | Role: Admin, Incident Manager    |
| created_at | TIMESTAMP   | NOT NULL, DEFAULT            | When the role was assigned       |

**Unique constraint**: (user_id, role)

**Role enum values**:

| Value             | Description                                      |
|-------------------|--------------------------------------------------|
| Admin             | Platform administration (users, settings, fetchers) |
| Incident Manager  | CVE triage and assessment (tickets, packages, CVSS) |

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

**Status categories**:
- **Active tickets**: tickets in status `New`, `Analysis`, or `Analyzed`.
  These are actively monitored: CVSS sync, release detection, and
  recalculation cascades apply to active tickets.
- **Inactive tickets**: tickets in status `Resolved`, `Ignored`, or
  `Duplicated`. These are no longer monitored: CVSS sync and
  recalculation cascades skip inactive tickets.

### TicketReference

Stores external links associated with a ticket. References are created
automatically by CVE fetchers during ingestion and can also be added
manually by Incident Managers. See `docs/features/references.md` for
the full specification.

| Column     | Type           | Constraints                  | Description                        |
|------------|----------------|------------------------------|------------------------------------|
| id         | UUID           | PK                           | Internal identifier                |
| ticket_id  | UUID           | FK(ticket.id), NOT NULL      | Related ticket                     |
| url        | VARCHAR        | NOT NULL                     | URL of the external resource       |
| title      | VARCHAR        | nullable                     | Optional human-readable label      |
| source     | VARCHAR        | NOT NULL                     | Origin: fetcher name (e.g., `"sync_cves_nvd"`, `"sync_cves_mitre"`) or `"manual"` for user-added references |
| tags       | ARRAY(VARCHAR) | nullable                     | Descriptive tags from CVE data (e.g., `"Patch"`, `"Vendor Advisory"`) |
| created_by | UUID           | FK(user.id), nullable        | User who added the reference. NULL for automatic references created by fetchers |
| created_at | TIMESTAMP      | NOT NULL, DEFAULT            | Record creation timestamp          |
| updated_at | TIMESTAMP      | NOT NULL, DEFAULT            | Record update timestamp            |

**Unique constraint**: (ticket_id, url)

**Cascade delete**: when a ticket is deleted, all its references are
deleted.

### TicketEvent

Audit log of all changes to a ticket. Each event represents a discrete
action (status change, assignment, duplicate operation, or automated
system action).

| Column      | Type        | Constraints            | Description                                |
|-------------|-------------|------------------------|--------------------------------------------|
| id          | UUID        | PK                     | Internal identifier                        |
| ticket_id   | UUID        | FK(ticket.id), NOT NULL| Related ticket                             |
| user_id     | UUID        | FK(user.id), nullable  | User who performed the action. NULL for automated system actions (e.g., release detection, auto-created tickets). |
| event_type  | ENUM        | NOT NULL               | See TicketEventType enum below             |
| old_value   | VARCHAR     | nullable               | Previous value (e.g., old status, old assignee username) |
| new_value   | VARCHAR     | nullable               | New value (e.g., new status, new assignee username) |
| comment     | TEXT        | nullable               | Optional note from the IM, or system-generated description for automated events |
| created_at  | TIMESTAMP   | NOT NULL, DEFAULT      | Event timestamp                            |

### TicketEventType Enum

| Value                      | Description                                        |
|----------------------------|----------------------------------------------------|
| status_change              | Ticket status was changed                          |
| assignment                 | Ticket was assigned or reassigned                  |
| duplicate_set              | Ticket was marked as duplicate of another          |
| duplicate_removed          | Duplicate mark was reverted                        |
| package_added              | Package added to the ticket (manual by IM or automatic via CPE match / codestream detection). `user_id` is set for IM actions, NULL for automatic. `comment` provides context for automatic additions. |
| package_removed            | IM removed a package from the ticket               |
| codestream_status_changed  | IM changed codestream affectedness status           |
| product_status_overridden  | IM overrode product affectedness status             |
| codestream_released        | Codestream release detected by CodestreamReleaseDetector (Case A) |
| product_released           | Product release detected via updateinfo.xml advisory |
| ticket_auto_created        | Ticket auto-created after CVE fix detected with no existing ticket (Case C) |
| severity_changed           | CVE severity was recalculated due to a CVSS assessment change or default CVSS version change. `old_value` and `new_value` contain severity labels. `user_id` is always NULL (system event). |
| cvss_assessment_changed    | A CVSS assessment was added, modified, or removed. `old_value` contains previous `"provider_name vX.Y score"` (or NULL if new). `new_value` contains current value (or NULL if removed). `comment` is NULL. `user_id` set for SUSE changes, NULL for external sync. |
| product_eligibility_changed | Product eligibility changed due to CVSS score recalculation. `old_value` and `new_value` contain the product status. `user_id` is NULL (always system-triggered). |

### CodestreamPackageChecksum

Operational cache table used by the `CodestreamReleaseDetector` to track
source MD5 checksums of packages in IBS codestream projects. By comparing
the current `srcmd5` from IBS with the cached value, the detector
identifies which packages have changed since the last run.

This table contains no domain data — it is purely an operational artifact
of the release detection mechanism.

| Column          | Type        | Constraints          | Description                        |
|-----------------|-------------|----------------------|------------------------------------|
| id              | UUID        | PK                   | Internal identifier                |
| codestream_name | VARCHAR     | NOT NULL             | IBS codestream project name (e.g., `SUSE:SLE-15-SP6:Update`) |
| package_name    | VARCHAR     | NOT NULL             | Source package name                |
| srcmd5          | VARCHAR     | NOT NULL             | MD5 checksum of the package source revision from IBS |
| last_seen_at    | TIMESTAMP   | NOT NULL, DEFAULT    | When this checksum was last observed |

**Unique constraint**: (codestream_name, package_name)

### FetcherRun

Records every execution of a fetcher. Primary data source for the fetcher
dashboard charts. See `docs/features/fetcher-dashboard.md` for full
specification.

| Column               | Type        | Constraints              | Description                        |
|----------------------|-------------|--------------------------|-------------------------------------|
| id                   | UUID        | PK                       | Internal identifier                |
| fetcher_name         | VARCHAR     | NOT NULL, indexed        | Fetcher identifier (matches `BaseFetcher.name`) |
| started_at           | TIMESTAMP   | NOT NULL                 | When the run started               |
| finished_at          | TIMESTAMP   | nullable                 | When the run ended (NULL while running) |
| duration_seconds     | FLOAT       | nullable                 | `finished_at - started_at` in seconds |
| status               | ENUM        | NOT NULL                 | FetcherRunStatus: `running`, `success`, `failure`, `partial` |
| items_created        | INTEGER     | NOT NULL, DEFAULT 0      | New records created                |
| items_updated        | INTEGER     | NOT NULL, DEFAULT 0      | Existing records updated           |
| items_failed         | INTEGER     | NOT NULL, DEFAULT 0      | Items that failed processing       |
| error_message        | TEXT        | nullable                 | Short error description            |
| error_traceback      | TEXT        | nullable                 | Full Python traceback (admin-only visibility in API) |
| triggered_by         | ENUM        | NOT NULL                 | FetcherRunTriggeredBy: `schedule`, `manual` |
| triggered_by_user_id | UUID        | FK(user.id), nullable    | Admin who triggered the run (only for `manual`) |
| created_at           | TIMESTAMP   | NOT NULL, DEFAULT        | Record creation timestamp          |

### FetcherConfig

Per-fetcher configuration managed by admins. Auto-created on worker
startup if not present.

| Column            | Type        | Constraints        | Description                        |
|-------------------|-------------|--------------------|------------------------------------|
| fetcher_name      | VARCHAR     | PK                 | Fetcher identifier (matches `BaseFetcher.name`) |
| enabled           | BOOLEAN     | NOT NULL, DEFAULT true | Whether the fetcher is active   |
| schedule_override | VARCHAR     | nullable           | Cron expression to override the default schedule |
| timeout_seconds   | INTEGER     | nullable           | Max execution time in seconds      |
| rate_limit        | VARCHAR     | nullable           | Rate limit (e.g., `"2/s"`, `"100/m"`) |
| updated_at        | TIMESTAMP   | NOT NULL, DEFAULT  | Last modification timestamp        |

### FetcherAuditLog

Audit trail for administrative actions on fetchers.

| Column               | Type        | Constraints              | Description                        |
|----------------------|-------------|--------------------------|-------------------------------------|
| id                   | UUID        | PK                       | Internal identifier                |
| fetcher_name         | VARCHAR     | NOT NULL, indexed        | Fetcher identifier                 |
| action               | ENUM        | NOT NULL                 | FetcherAuditAction: `disabled`, `enabled`, `triggered`, `config_changed` |
| performed_by_user_id | UUID        | FK(user.id), NOT NULL    | Admin who performed the action     |
| details              | JSONB       | nullable                 | Additional context (e.g., old/new config values) |
| created_at           | TIMESTAMP   | NOT NULL, DEFAULT        | When the action occurred           |

### FetcherRunWeeklyAggregate

Weekly summaries of fetcher runs, created by the `aggregate_fetcher_runs`
retention task after the 90-day individual retention window.

| Column               | Type        | Constraints              | Description                        |
|----------------------|-------------|--------------------------|-------------------------------------|
| id                   | UUID        | PK                       | Internal identifier                |
| fetcher_name         | VARCHAR     | NOT NULL, indexed        | Fetcher identifier                 |
| week_start           | DATE        | NOT NULL                 | Monday of the aggregation week     |
| run_count            | INTEGER     | NOT NULL                 | Total runs in the week             |
| success_count        | INTEGER     | NOT NULL                 | Runs with status `success`         |
| failure_count        | INTEGER     | NOT NULL                 | Runs with status `failure`         |
| partial_count        | INTEGER     | NOT NULL                 | Runs with status `partial`         |
| avg_duration_seconds | FLOAT       | NOT NULL                 | Average duration across all runs   |
| min_duration_seconds | FLOAT       | NOT NULL                 | Minimum duration                   |
| max_duration_seconds | FLOAT       | NOT NULL                 | Maximum duration                   |
| total_items_created  | INTEGER     | NOT NULL                 | Sum of `items_created`             |
| total_items_updated  | INTEGER     | NOT NULL                 | Sum of `items_updated`             |
| total_items_failed   | INTEGER     | NOT NULL                 | Sum of `items_failed`              |
| created_at           | TIMESTAMP   | NOT NULL, DEFAULT        | When this aggregate was created    |

**Unique constraint**: (fetcher_name, week_start)

## Indexes

TBD — will be defined based on query patterns during implementation.

## Notes

- All tables use UUID primary keys (exceptions: `SystemSetting` uses a
  VARCHAR `key` as PK; `FetcherConfig` uses `fetcher_name` VARCHAR as PK)
- All tables include `created_at` and `updated_at` timestamps (exceptions:
  `TicketEvent`, `CodestreamPackageChecksum`, `UserRole`, `ProductRepository`,
  `FetcherRun`, `FetcherAuditLog`, and `FetcherRunWeeklyAggregate` only have
  `created_at` because they are immutable write-once records —
  `ProductRepository` records are replaced during SMELT sync, never updated
  in place)
- ENUM types are defined as PostgreSQL enums
- JSONB is used for flexible storage of source-specific data
- The schema will evolve as features are implemented; this document must be
  updated before any schema changes
- The `CVECVSSAssessment` table supports multiple providers and CVSS
  versions — see `docs/features/cvss-scoring.md`
