# Enum Storage Strategy — Decision and Action Plan

Resolves: **OP-1** (Enum Storage Strategy: PostgreSQL ENUM vs VARCHAR +
Python Enum)

## Decision

Sentinel does not use PostgreSQL ENUM types (`CREATE TYPE ... AS ENUM`).
All enumerated columns use `VARCHAR(N)` with validation at one of two
levels:

| Category | Storage | Validation | Adding a value |
|----------|---------|------------|----------------|
| **A — State-machine** | `VARCHAR(N)` + `CHECK` constraint | Database rejects invalid values | Alembic migration (DROP + ADD constraint — reversible) |
| **B — Classification** | `VARCHAR(N)` | Python `StrEnum` in `app/core/enums.py` only | Code change only |

### Classification criterion

A column belongs to **Category A** if and only if:

1. The value is part of a **state machine with transitions managed by
   application code** — an invalid value would break the state machine
   (subsequent transitions fail or produce undefined behavior), OR
2. The value has **direct security implications** (an invalid value could
   affect authorization decisions).

All other enumerated columns belong to **Category B**: classifications,
labels, audit event types, source identifiers, and informational tags. An
invalid value in a Category B column produces a misclassified record but
does not break application logic or cause cascading damage.

### Rationale

- **Why not PostgreSQL ENUM**: `ALTER TYPE ... ADD VALUE` is
  non-transactional and irreversible in PostgreSQL. Removing a value
  requires recreating the entire type. This creates deployment friction
  and rollback risk for no meaningful benefit over CHECK constraints.
- **Why CHECK for Category A**: state-machine columns (ticket status,
  package status, role) are where an invalid value causes the most
  damage — silent logic failures, broken transitions, potential security
  issues. The CHECK constraint is the safety net that catches bugs in the
  application layer.
- **Why bare VARCHAR for Category B**: audit event types and source
  identifiers change frequently during development (every new feature
  adds new event types). A migration for each new event type is
  unnecessary friction. Audit records are immutable — a wrong value in a
  historical record is a cosmetic issue, not a logic failure.

---

## Classification of all enums

### Category A — State-machine (VARCHAR + CHECK)

| Enum | Column(s) | Table(s) | Values | VARCHAR |
|------|-----------|----------|--------|---------|
| TicketStatus | `status` | Ticket | `New`, `Analysis`, `Analyzed`, `Resolved`, `Ignored`, `Duplicated` | VARCHAR(20) |
| PackageStatus | `status` | TicketPackageTrack | `ANALYSIS`, `AFFECTED`, `NOT_AFFECTED`, `FIXED`, `WONT_FIX` | VARCHAR(20) |
| DeliveryStatus | `delivery_status` | TicketPackageTrack | `PENDING`, `IN_PROGRESS`, `RELEASED` | VARCHAR(20) |
| Role | `role` | UserRole, RoleMapping | `Admin`, `Vulnerability Analyst`, `Restricted Analyst` | VARCHAR(30) |
| FetcherRunStatus | `status` | FetcherRun | `running`, `success`, `failure`, `partial` | VARCHAR(20) |
| SubmissionRequestState | `state` | SubmissionRequest | `open`, `accepted`, `declined`, `revoked`, `superseded` | VARCHAR(20) |
| ReleaseRequestState | `state` | ReleaseRequest | `open`, `accepted`, `declined`, `revoked` | VARCHAR(20) |

**CHECK constraint naming**: `chk_{table}_{column}_valid` (e.g.,
`chk_ticket_status_valid`, `chk_user_role_role_valid`).

**Role VARCHAR(30)**: sized larger than other Category A columns because
the longest value (`Vulnerability Analyst`) is 21 characters.

### Category B — Classification (VARCHAR + Python Enum)

| Enum | Column(s) | Table(s) | Values | VARCHAR |
|------|-----------|----------|--------|---------|
| Severity | `severity`, `severity_override` | CVE, Ticket | `Critical`, `High`, `Medium`, `Low`, `None` | VARCHAR(20) |
| cve_state | `cve_state` | CVE | `PUBLISHED`, `REJECTED` | VARCHAR(20) |
| CVESourceFetchStatus | `status` | CVESource | `success`, `failure`, `missing` | VARCHAR(20) |
| CVESourceType | `source` | CVESource | `nvd`, `mitre`, `kernel`, `redhat`, `ghsa`, `osv`, `kev`, `epss` | VARCHAR(100) |
| CVEExternalIdentifierSource | `source` | CVEExternalIdentifier | `GHSA`, `PYSEC`, `RUSTSEC` | VARCHAR(20) |
| TicketAuditEventType | `event_type` | TicketAuditEvent | 28 values (see `data-model.md`) | VARCHAR(50) |
| IdentityAuditEventType | `event_type` | IdentityAuditEvent | 14 values (see `data-model.md`) | VARCHAR(50) |
| FetcherAuditEventType | `event_type` | FetcherAuditEvent | `disabled`, `enabled`, `triggered`, `config_changed` | VARCHAR(50) |
| SettingAuditEventType | `event_type` | SettingAuditEvent | `setting_changed` | VARCHAR(50) |
| WorkflowType | `workflow_type` | TicketPackageTrack | `ibs`, `git` | VARCHAR(20) |
| ReferenceType | `type` | TicketReference | `advisory`, `patch`, `issue`, `article` | VARCHAR(20) |
| BugownerType | `bugowner_type` | PackageBugowner | `person`, `group` | VARCHAR(20) |
| FetcherRunTriggeredBy | `triggered_by` | FetcherRun | `schedule`, `manual` | VARCHAR(20) |

**Audit event types use VARCHAR(50)**: sized for headroom because these
enums grow with every new feature (longest current value:
`reference_description_changed` at 29 characters).

**CVESourceType keeps VARCHAR(100)** and **CVEExternalIdentifierSource
keeps VARCHAR(20)**: these columns are already VARCHAR in the current
spec. No change needed to their sizing.

---

## Convention text

The following section will be added to `docs/conventions.md` under
"### SQLAlchemy Conventions" as a new subsection
"### Enum Storage Strategy":

```markdown
### Enum Storage Strategy

Sentinel does not use PostgreSQL ENUM types (`CREATE TYPE ... AS ENUM`).
All enumerated columns use `VARCHAR(N)` with one of two validation
strategies:

| Category | Validation | Adding a value |
|----------|------------|----------------|
| **State-machine** | `VARCHAR(N)` + `CHECK` constraint | Alembic migration (reversible) |
| **Classification** | `VARCHAR(N)` + Python `StrEnum` in `app/core/enums.py` | Code change only |

**Classification criterion**: a column uses a CHECK constraint if and
only if (a) the value is part of a state machine whose transitions are
managed by application code, or (b) the value has direct security
implications. All other enumerated columns (classifications, labels,
audit event types, source identifiers) use Python Enum validation only.

All Python Enums for enumerated columns — both categories — are defined
in `app/core/enums.py` as `StrEnum` subclasses. Category A enums are
additionally protected by a CHECK constraint at the database level.

All schema tables — in `docs/data-model.md` and in feature
specifications — use `VARCHAR(N)` as the column type for enumerated
columns. The enum name and valid values are documented in the column
description or in a dedicated enum section.

**CHECK constraint naming**: `chk_{table}_{column}_valid`.

**Implementation patterns**:

```python
# Category A — State-machine (VARCHAR + CHECK)
class TicketStatus(StrEnum):
    NEW = "New"
    ANALYSIS = "Analysis"
    ...

status: Mapped[str] = mapped_column(String(20), nullable=False, default=TicketStatus.NEW)

__table_args__ = (
    CheckConstraint(
        status.in_([e.value for e in TicketStatus]),
        name="chk_ticket_status_valid",
    ),
)


# Category B — Classification (VARCHAR + Python Enum only)
class CVESourceType(StrEnum):
    NVD = "nvd"
    MITRE = "mitre"
    ...

source: Mapped[str] = mapped_column(String(100), nullable=False)
# Validation in service layer: CVESourceType(value) raises ValueError if invalid
```

See `docs/data-model.md` (Notes) for the classification of every enum
in the schema.
```

---

## Action plan

### Phase 1 — Convention (source of truth)

#### Step 1.1: Add convention to `docs/conventions.md`

**File**: `docs/conventions.md`  
**Location**: new subsection `### Enum Storage Strategy` after the
existing `### SQLAlchemy Conventions` section (after line ~300, before
`### Pydantic Conventions`).  
**Content**: the convention text above.

---

### Phase 2 — Data model (physical schema)

#### Step 2.1: Rewrite Notes section in `docs/data-model.md`

**File**: `docs/data-model.md`  
**Lines**: 1538–1543  

**Current text**:

```
- ENUM types follow a hybrid approach: stable, closed value sets (e.g.,
  `TicketStatus`, `CVESourceFetchStatus`) use PostgreSQL ENUM types
  (adding a value requires a migration). Evolving value sets (e.g.,
  `CVESourceType`, `CVEExternalIdentifierSource`) use VARCHAR columns
  validated by Python Enums in `app/core/enums.py` (adding a value
  requires only a code change)
```

**New text**:

```
- Sentinel does not use PostgreSQL ENUM types. All enumerated columns
  use VARCHAR. State-machine enums (TicketStatus, PackageStatus,
  DeliveryStatus, Role, FetcherRunStatus, SubmissionRequestState,
  ReleaseRequestState) are protected by CHECK constraints — see
  `docs/conventions.md` (Enum Storage Strategy) for the classification
  criterion, naming convention, and implementation patterns.
  Classification enums (audit event types, source types, informational
  labels) are validated exclusively by Python StrEnum in
  `app/core/enums.py`
```

#### Step 2.2: Update `cve_state` column in CVE table

**File**: `docs/data-model.md`  
**Line**: 436  

**Current column type**: `ENUM`  
**New column type**: `VARCHAR(20)`

**Current description** (relevant fragment):

```
Uses PostgreSQL ENUM (stable value set defined by the CVE Program).
```

**New description** (replace the fragment above with):

```
Validated by Python Enum in `app/core/enums.py` (Category B —
classification). Stable value set defined by the CVE Program.
```

#### Step 2.3: Update `severity` column in CVE table

**File**: `docs/data-model.md`  
**Line**: 433  

**Current column type**: `ENUM`  
**New column type**: `VARCHAR(20)`

No description change needed — the current description does not mention
PostgreSQL ENUM.

#### Step 2.4: Update `status` column in CVESource table

**File**: `docs/data-model.md`  
**Line**: 456  

**Current column type**: `ENUM`  
**New column type**: `VARCHAR(20)`

**Current description** (relevant fragment):

```
Uses PostgreSQL ENUM type `CVESourceFetchStatus`. No default — always
written explicitly by the caller
```

**New description** (replace the fragment above with):

```
CVESourceFetchStatus — validated by Python Enum in `app/core/enums.py`
(Category B — classification). No default — always written explicitly by
the caller
```

#### Step 2.5: Rewrite `CVESourceFetchStatus` enum section

**File**: `docs/data-model.md`  
**Lines**: 472–476  

**Current text**:

```
### CVESourceFetchStatus Enum

Outcome of a CVE data fetch attempt from an external source. Uses
PostgreSQL ENUM type (stable, closed value set — adding a new status
requires a migration).
```

**New text**:

```
### CVESourceFetchStatus Enum

Outcome of a CVE data fetch attempt from an external source. Category B
— classification enum (Python Enum in `app/core/enums.py`, no CHECK
constraint). Adding a new status requires only a code change.
```

#### Step 2.6: Update `CVESourceType` enum section

**File**: `docs/data-model.md`  
**Lines**: 484–489  

**Current text** (relevant fragment):

```
This is a **Python Enum** in
`app/core/enums.py` — NOT a PostgreSQL ENUM. The database column
remains `VARCHAR(100)` for migration flexibility (adding a new source
requires only a code change, not an Alembic migration).
```

**New text**:

```
Category B — classification enum (Python Enum in `app/core/enums.py`,
no CHECK constraint). The database column is `VARCHAR(100)`. Adding a
new source requires only a code change.
```

The "NOT a PostgreSQL ENUM" clarification is no longer needed because
the project-wide convention eliminates PG ENUM entirely.

#### Step 2.7: Update `CVEExternalIdentifierSource` section

**File**: `docs/data-model.md`  
**Lines**: 560–564 (approximate — the section defining this enum)

Same pattern as Step 2.6: remove "NOT a PostgreSQL ENUM" language,
replace with Category B classification reference.

#### Step 2.8: Update `CVESource.source` column description

**File**: `docs/data-model.md`  
**Line**: 455  

**Current description** (relevant fragment):

```
Column is VARCHAR (not PG ENUM) for migration flexibility.
```

**New description** (replace the fragment above with):

```
Column is VARCHAR(100) — Category B classification enum.
```

#### Step 2.9: Update `CVEExternalIdentifier.source` column description

**File**: `docs/data-model.md`  
**Line**: 593  

**Current description** (relevant fragment):

```
Column is VARCHAR (not PG ENUM) for migration flexibility
```

**New description** (replace the fragment above with):

```
Column is VARCHAR(20) — Category B classification enum.
```

#### Step 2.10: Update remaining ENUM columns in `data-model.md`

For each of the following columns, change the column type from `ENUM` (or
`ENUM(EnumName)`) to the specified `VARCHAR(N)`. No description changes
are needed unless noted — the current descriptions do not mention
PostgreSQL ENUM.

| Line | Table | Column | Current type | New type | Notes |
|------|-------|--------|-------------|----------|-------|
| 856 | TicketPackageTrack | `workflow_type` | `ENUM` | `VARCHAR(20)` | |
| 858 | TicketPackageTrack | `status` | `ENUM` | `VARCHAR(20)` | |
| 859 | TicketPackageTrack | `delivery_status` | `ENUM` | `VARCHAR(20)` | |
| 976 | UserRole | `role` | `ENUM` | `VARCHAR(30)` | |
| 1015 | RoleMapping | `role` | `ENUM` | `VARCHAR(30)` | |
| 1086 | Ticket | `status` | `ENUM` | `VARCHAR(20)` | |
| 1087 | Ticket | `severity_override` | `ENUM` | `VARCHAR(20)` | |
| 1150 | TicketReference | `type` | `ENUM(ReferenceType)` | `VARCHAR(20)` | |
| 1203 | TicketAuditEvent | `event_type` | `ENUM` | `VARCHAR(50)` | |
| 1271 | IdentityAuditEvent | `event_type` | `ENUM` | `VARCHAR(50)` | |
| 1309 | SettingAuditEvent | `event_type` | `ENUM` | `VARCHAR(50)` | |
| 1361 | PackageBugowner | `bugowner_type` | `ENUM` | `VARCHAR(20)` | |
| 1405 | FetcherRun | `status` | `ENUM` | `VARCHAR(20)` | |
| 1412 | FetcherRun | `triggered_by` | `ENUM` | `VARCHAR(20)` | |
| 1445 | FetcherAuditEvent | `event_type` | `ENUM` | `VARCHAR(50)` | |
| 1466 | SubmissionRequest | `state` | `ENUM` | `VARCHAR(20)` | |
| 1488 | ReleaseRequest | `state` | `ENUM` | `VARCHAR(20)` | |

#### Step 2.11: Update Mermaid ER diagrams

**File**: `docs/data-model.md`  
**Lines**: 86–419 (all 5 domain-specific ER diagrams)

All Mermaid diagrams use `ENUM` as a type annotation for enumerated
fields. Change each occurrence to `VARCHAR`:

**CVE & Ticket Core** (lines 86–197):

| Line | Entity | Field | Current | New |
|------|--------|-------|---------|-----|
| 91 | CVE | `severity` | `ENUM severity "nullable"` | `VARCHAR severity "nullable"` |
| 92 | CVE | `cve_state` | `ENUM cve_state "NOT NULL, DEFAULT PUBLISHED"` | `VARCHAR cve_state "NOT NULL, DEFAULT PUBLISHED"` |
| 99 | CVESource | `status` | `ENUM status "NOT NULL"` | `VARCHAR status "NOT NULL"` |
| 147 | Ticket | `status` | `ENUM status "NOT NULL"` | `VARCHAR status "NOT NULL"` |
| 148 | Ticket | `severity_override` | `ENUM severity_override "nullable"` | `VARCHAR severity_override "nullable"` |
| 163 | TicketAuditEvent | `event_type` | `ENUM event_type "NOT NULL"` | `VARCHAR event_type "NOT NULL"` |
| 173 | TicketReference | `type` | `ENUM type "nullable"` | `VARCHAR type "nullable"` |

**Package Hierarchy** (lines 201–250):

| Line | Entity | Field | Current | New |
|------|--------|-------|---------|-----|
| 215 | TicketPackageTrack | `workflow_type` | `ENUM workflow_type "NOT NULL (ibs, git)"` | `VARCHAR workflow_type "NOT NULL (ibs, git)"` |
| 217 | TicketPackageTrack | `status` | `ENUM status "NOT NULL, DEFAULT ANALYSIS"` | `VARCHAR status "NOT NULL, DEFAULT ANALYSIS"` |
| 218 | TicketPackageTrack | `delivery_status` | `ENUM delivery_status "NOT NULL, DEFAULT PENDING"` | `VARCHAR delivery_status "NOT NULL, DEFAULT PENDING"` |

**Identity** (lines 254–312):

| Line | Entity | Field | Current | New |
|------|--------|-------|---------|-----|
| 268 | UserRole | `role` | `ENUM role "NOT NULL"` | `VARCHAR role "NOT NULL"` |
| 275 | RoleMapping | `role` | `ENUM role "NOT NULL"` | `VARCHAR role "NOT NULL"` |
| 295 | IdentityAuditEvent | `event_type` | `ENUM event_type "NOT NULL"` | `VARCHAR event_type "NOT NULL"` |

**Platform Infrastructure** (lines 316–365):

| Line | Entity | Field | Current | New |
|------|--------|-------|---------|-----|
| 329 | FetcherRun | `status` | `ENUM status "NOT NULL"` | `VARCHAR status "NOT NULL"` |
| 330 | FetcherRun | `triggered_by` | `ENUM triggered_by "NOT NULL"` | `VARCHAR triggered_by "NOT NULL"` |
| 337 | FetcherAuditEvent | `event_type` | `ENUM event_type "NOT NULL"` | `VARCHAR event_type "NOT NULL"` |
| 349 | SettingAuditEvent | `event_type` | `ENUM event_type "NOT NULL"` | `VARCHAR event_type "NOT NULL"` |

**IBS Integration** (lines 369–419):

| Line | Entity | Field | Current | New |
|------|--------|-------|---------|-----|
| 376 | SubmissionRequest | `state` | `ENUM state "DEFAULT open"` | `VARCHAR state "DEFAULT open"` |
| 390 | ReleaseRequest | `state` | `ENUM state "DEFAULT open"` | `VARCHAR state "DEFAULT open"` |
| 402 | PackageBugowner | `bugowner_type` | `ENUM bugowner_type "nullable"` | `VARCHAR bugowner_type "nullable"` |

#### Step 2.12: Update `docs/system-map.md` Mermaid ER diagram

**File**: `docs/system-map.md`  
**Lines**: 89–340 (single ER diagram covering all tables)

The system-map diagram uses `ENUM` as a type annotation for the same
fields. Change each occurrence to `VARCHAR`:

| Line | Entity | Field | Current | New |
|------|--------|-------|---------|-----|
| 94 | CVE | `severity` | `ENUM severity` | `VARCHAR severity` |
| 95 | CVE | `cve_state` | `ENUM cve_state` | `VARCHAR cve_state` |
| 102 | CVESource | `status` | `ENUM status` | `VARCHAR status` |
| 116 | CVEExternalIdentifier | `source` | `ENUM source` | `VARCHAR source` |
| 158 | Ticket | `status` | `ENUM status` | `VARCHAR status` |
| 168 | TicketAuditEvent | `event_type` | `ENUM event_type` | `VARCHAR event_type` |
| 192 | TicketPackageTrack | `workflow_type` | `ENUM workflow_type` | `VARCHAR workflow_type` |
| 194 | TicketPackageTrack | `status` | `ENUM status` | `VARCHAR status` |
| 195 | TicketPackageTrack | `delivery_status` | `ENUM delivery_status` | `VARCHAR delivery_status` |
| 232 | UserRole | `role` | `ENUM role` | `VARCHAR role` |
| 240 | RoleMapping | `role` | `ENUM role` | `VARCHAR role` |
| 263 | IdentityAuditEvent | `event_type` | `ENUM event_type` | `VARCHAR event_type` |
| 275 | SettingAuditEvent | `event_type` | `ENUM event_type` | `VARCHAR event_type` |
| 290 | PackageBugowner | `bugowner_type` | `ENUM bugowner_type "nullable"` | `VARCHAR bugowner_type "nullable"` |
| 304 | SubmissionRequest | `state` | `ENUM state "DEFAULT open"` | `VARCHAR state "DEFAULT open"` |
| 319 | ReleaseRequest | `state` | `ENUM state "DEFAULT open"` | `VARCHAR state "DEFAULT open"` |
| 331 | FetcherRun | `status` | `ENUM status` | `VARCHAR status` |
| 338 | FetcherAuditEvent | `event_type` | `ENUM event_type` | `VARCHAR event_type` |

---

### Phase 3 — Feature specifications

#### Step 3.1: Update `docs/features/identity/rbac.md`

**File**: `docs/features/identity/rbac.md`  
**Lines**: 667–669  

**Current text**:

```
required. The Role enum in the database is **append-only** — values are
never removed from the PostgreSQL enum type. Deprecated roles (if ever
needed) would be handled via a migration that reassigns affected users.
```

**New text**:

```
required. The Role enum uses VARCHAR(30) columns protected by CHECK
constraints (`chk_user_role_role_valid`, `chk_role_mapping_role_valid`)
— Category A (state-machine, security-critical). Adding a new role
requires an Alembic migration (DROP + ADD constraints — reversible).
Values are never removed from the CHECK if existing records reference
them. Deprecated roles (if ever needed) would be handled via a
migration that reassigns affected users and then removes the value
from the constraints.
```

#### Step 3.2: Update `docs/features/tickets/tickets.md`

**File**: `docs/features/tickets/tickets.md`  
**Lines**: 202, 1706, 1708  

Change column types in the "Key fields" table:

| Line | Column | Current type | New type |
|------|--------|-------------|----------|
| 1706 | `status` | `ENUM` | `VARCHAR(20)` |
| 1708 | `severity_override` | `ENUM` | `VARCHAR(20)` |

Change prose reference on line 202:

**Current text**: `- \`Ticket.severity_override\`: ENUM (Critical, High, Medium, Low, None),`

**New text**: `- \`Ticket.severity_override\`: VARCHAR(20) (Critical, High, Medium, Low, None),`

#### Step 3.3: Update `docs/features/tickets/ticket-references.md`

**File**: `docs/features/tickets/ticket-references.md`  
**Line**: 60  

Change column type:

| Line | Column | Current type | New type |
|------|--------|-------------|----------|
| 60 | `type` | `ENUM(ReferenceType)` | `VARCHAR(20)` |

#### Step 3.4: Update `docs/features/tickets/cve-service.md`

**File**: `docs/features/tickets/cve-service.md`  
**Line**: 1334  

**Current text**:

```
the `CVESourceFetchStatus` PostgreSQL ENUM. The derived statuses
```

**New text**:

```
the persisted `CVESourceFetchStatus` values (`success`, `failure`,
`missing`). The derived statuses
```

#### Step 3.5: Update `docs/features/identity/identity-audit-log.md`

**File**: `docs/features/identity/identity-audit-log.md`  
**Line**: 23  

Change column type:

| Line | Column | Current type | New type |
|------|--------|-------------|----------|
| 23 | `event_type` | `ENUM` | `VARCHAR(50)` |

#### Step 3.6: Update `docs/features/platform/system-settings.md`

**File**: `docs/features/platform/system-settings.md`  
**Line**: 266  

Change column type:

| Line | Column | Current type | New type |
|------|--------|-------------|----------|
| 266 | `event_type` | `ENUM` | `VARCHAR(50)` |

#### Step 3.7: Update `docs/features/platform/fetcher-infrastructure.md`

**File**: `docs/features/platform/fetcher-infrastructure.md`  
**Lines**: 2549, 2556, 2706  

Change column types:

| Line | Column | Current type | New type |
|------|--------|-------------|----------|
| 2549 | `status` | `ENUM` | `VARCHAR(20)` |
| 2556 | `triggered_by` | `ENUM` | `VARCHAR(20)` |
| 2706 | `event_type` | `ENUM` | `VARCHAR(50)` |

#### Step 3.8: Update `docs/features/packages/ibs-submission-tracking.md`

**File**: `docs/features/packages/ibs-submission-tracking.md`  
**Lines**: 145, 196  

Change column types:

| Line | Column | Current type | New type |
|------|--------|-------------|----------|
| 145 | `state` | `ENUM` | `VARCHAR(20)` |
| 196 | `state` | `ENUM` | `VARCHAR(20)` |

#### Step 3.9: Update `docs/features/packages/package-model.md`

**File**: `docs/features/packages/package-model.md`  
**Lines**: 245, 247, 248  

Change column types:

| Line | Column | Current type | New type |
|------|--------|-------------|----------|
| 245 | `workflow_type` | `ENUM` | `VARCHAR(20)` |
| 247 | `status` | `PackageStatus` | `VARCHAR(20)` |
| 248 | `delivery_status` | `DeliveryStatus` | `VARCHAR(20)` |

Note: lines 247–248 use enum class names as column types rather than
the generic `ENUM` keyword. They must be changed to `VARCHAR(20)` for
consistency.

#### Step 3.10: Update `docs/features/tickets/cve-tracking.md`

**File**: `docs/features/tickets/cve-tracking.md`  
**Line**: 240  

Change prose type descriptor:

**Current text**: `The CVE table stores a \`cve_state\` field (ENUM: \`PUBLISHED\`,`

**New text**: `The CVE table stores a \`cve_state\` field (\`PUBLISHED\`,`

---

### Phase 4 — Resolve OP-1

#### Step 4.1: Update `docs/drafts/open-points.md`

**Summary table** (line 9): change OP-1 status from `Open` to
`Resolved`.

**Move OP-1 section**: move the entire "### OP-1. Enum Storage Strategy"
section from "## Open — Data Model" to "## Archive — Resolved". Remove
the "## Open — Data Model" header if OP-1 was the only entry.

**Add resolution text** at the end of the moved section:

```
**Resolution** (2026-07-24): decided on a zero-PG-ENUM strategy. All
enumerated columns use VARCHAR. State-machine enums (TicketStatus,
PackageStatus, DeliveryStatus, Role, FetcherRunStatus,
SubmissionRequestState, ReleaseRequestState) are protected by CHECK
constraints. Classification enums (audit event types, source types,
severity, cve_state, informational labels) are validated exclusively
by Python StrEnum in `app/core/enums.py`. The classification criterion
is: CHECK if the value is part of a state machine with code-managed
transitions or has direct security implications; Python Enum only for
everything else. See `docs/conventions.md` (Enum Storage Strategy) for
the full convention.
```

---

### Phase 5 — Verification

#### Step 5.1: Grep verification

Run a search across `docs/` for any remaining references to PostgreSQL
ENUM that were missed:

- Pattern: `PostgreSQL ENUM`
- Pattern: `PG ENUM`
- Pattern: `ALTER TYPE`
- Pattern: `AS ENUM`
- Pattern: `\bENUM\b` (broad word-boundary match — triage manually:
  references to "Python Enum" as a concept are acceptable; references
  using "ENUM" as a column type descriptor in prose or tables are not)

All matches in approved specifications (`docs/features/`, `docs/*.md`)
must be zero (after triaging acceptable "Python Enum" concept
references). Matches in `docs/drafts/open-points.md` (the archived
OP-1 text) are acceptable — they are historical context.

#### Step 5.2: Column type verification

Run a search across all specification files for column type `ENUM`
appearing in schema tables:

- Pattern: `\| ENUM` (pipe followed by ENUM in a table row)
- Pattern: `ENUM(` (parameterized enum type)

All matches must be zero. Every enum column in every spec must now show
`VARCHAR(N)`.

#### Step 5.3: Execute reviewers

After all changes are applied, run the following reviewers on the
affected specifications to verify correctness:

| Reviewer | Target | Why |
|----------|--------|-----|
| `@data-model-reviewer` | `docs/data-model.md` | Verify schema changes are consistent, no orphan references |
| `@spec-coherence-reviewer` | `docs/data-model.md` | Verify cross-spec consistency (data-model vs feature specs) |
| `@spec-coherence-reviewer` | `docs/conventions.md` | Verify the new convention does not contradict existing conventions |
| `@spec-coherence-reviewer` | `docs/features/identity/rbac.md` | Verify Role storage description is consistent with data-model and convention |
| `@docs-placement-reviewer` | `docs/conventions.md` | Verify the convention is placed in the correct document |

#### Step 5.4: Delete this draft

Once all changes have been applied, verified by grep, and validated by
reviewers, delete this file:

```
docs/drafts/enum-storage-strategy.md
```

The decision is fully captured in:

- `docs/conventions.md` (Enum Storage Strategy) — the convention
- `docs/data-model.md` (Notes) — the schema-level summary
- `docs/drafts/open-points.md` (OP-1 Archive) — the historical record
