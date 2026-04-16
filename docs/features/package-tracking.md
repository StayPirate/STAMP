# Package Tracking

## Purpose

Track the affectedness of source packages across IBS codestreams and SUSE
commercial products in the context of CVE tickets. This feature replaces the
previous Distribution/Package/AffectedPackage model with a structure that
reflects how SUSE actually organizes packages, codestreams, and products.

## Domain Concepts

### IBS (Internal Build Service)

The internal OBS instance at build.suse.de used for all SUSE commercial
products. Packages are built and maintained here.

### Codestream

An IBS project where source packages live and are built. Each codestream
follows the naming pattern `SUSE:SLE-<version>:GA` (development phase) or
`SUSE:SLE-<version>:Update` (maintenance phase after GA freeze).

- **GA codestream**: receives packages during development of a Service Pack.
  Once the SP is finalized, this codestream is frozen.
- **Update codestream**: receives all maintenance updates after GA freeze.
  This is where security fixes land.

A source package may exist in multiple codestreams. If a newer SP inherits a
package from an older SP without changes, the newer codestream contains an IBS
link to the older codestream's package — updates to the source codestream
automatically propagate to the linked codestreams.

### Product

A commercial SUSE product (e.g., SLES 15 SP6, SLED 15 SP6) with its own
repositories from which end users receive updates via the package manager.
A product receives binary packages from one or more codestreams. The same
codestream can feed multiple products.

### Channel File

An XML file in the IBS project `SUSE:Channels` that defines which packages
from which codestreams are shipped to which products. There is one channel file
per product. STAMP does not parse channel files directly — it relies on SMELT
to resolve these mappings.

### SMELT

An internal SUSE aggregator service (HTTP API) that, given a source package
name, returns the list of codestreams where the package is maintained and the
products that receive it. SMELT reads from IBS, channel files, and other
sources internally.

### AIMAAS

An internal SUSE service (HTTP API) that provides product lifecycle data,
including the CVSS threshold — the minimum CVSS score for which a product is
eligible to receive a security update.

### Package Eligibility

A product is eligible to receive a security update for a given CVE only if the
CVE's CVSS score meets or exceeds the product's CVSS threshold. For example:

- A product in General Support phase has a threshold of 0 — it receives all
  security updates.
- A product in LTSS phase may have a threshold of 7.0 — it only receives
  updates for CVEs with CVSS >= 7.0.
- Some products have an arbitrary threshold set in AIMAAS.

If a codestream is marked as "Affected" for a CVE but a product under that
codestream is not eligible (CVSS below threshold), that product is
automatically set to a resolved "Affected" state (internally
`AFFECTED_RESOLVED`) indicating it is affected but no action is required.

## Data Model

See `docs/data-model.md` for the full schema. The tables defined by this
feature are:

### Codestream

Represents an IBS codestream project. Synced periodically from SMELT.

| Column     | Type      | Constraints        | Description                        |
|------------|-----------|--------------------|------------------------------------|
| id         | UUID      | PK                 | Internal identifier                |
| name       | VARCHAR   | UNIQUE, NOT NULL   | IBS project name (e.g., `SUSE:SLE-15-SP6:Update`) |
| active     | BOOLEAN   | NOT NULL, DEFAULT true | False when SMELT no longer reports this codestream |
| synced_at  | TIMESTAMP |                    | Last sync from SMELT               |
| created_at | TIMESTAMP | NOT NULL, DEFAULT  | Record creation timestamp          |
| updated_at | TIMESTAMP | NOT NULL, DEFAULT  | Record update timestamp            |

### Product

Represents a SUSE commercial product. Synced periodically from SMELT and
AIMAAS.

| Column         | Type         | Constraints        | Description                        |
|----------------|--------------|--------------------|------------------------------------|
| id             | UUID         | PK                 | Internal identifier                |
| name           | VARCHAR      | UNIQUE, NOT NULL   | Product name (e.g., `SLES 15 SP6`) |
| cpe            | VARCHAR      | UNIQUE, nullable   | CPE identifier for this product    |
| cvss_threshold | DECIMAL(3,1) | NOT NULL, DEFAULT 0 | Minimum CVSS score for eligibility (from AIMAAS) |
| active         | BOOLEAN      | NOT NULL, DEFAULT true | False when product is EOL      |
| synced_at      | TIMESTAMP    |                    | Last sync from AIMAAS              |
| created_at     | TIMESTAMP    | NOT NULL, DEFAULT  | Record creation timestamp          |
| updated_at     | TIMESTAMP    | NOT NULL, DEFAULT  | Record update timestamp            |

### CodestreamProduct

Mapping table that records which products receive packages from which
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
within the context of a ticket. The IM sets the status at this level.

| Column        | Type      | Constraints                  | Description                        |
|---------------|-----------|------------------------------|------------------------------------|
| id            | UUID      | PK                           | Internal identifier                |
| ticket_id     | UUID      | FK(ticket.id), NOT NULL      | Related ticket                     |
| package_name  | VARCHAR   | NOT NULL                     | Source package name                |
| codestream_id | UUID      | FK(codestream.id), NOT NULL  | Related codestream                 |
| status        | ENUM      | NOT NULL, DEFAULT ANALYSIS   | Package status in this codestream  |
| created_at    | TIMESTAMP | NOT NULL, DEFAULT            | Record creation timestamp          |
| updated_at    | TIMESTAMP | NOT NULL, DEFAULT            | Record update timestamp            |

**Unique constraint**: (ticket_id, package_name, codestream_id)

### TicketPackageProduct

Records the affectedness status of a source package for a specific product,
within the context of a ticket and codestream. Status is inherited from the
parent TicketPackageCodestream and adjusted for eligibility, but can be
overridden by the IM.

| Column                        | Type      | Constraints                                | Description                        |
|-------------------------------|-----------|--------------------------------------------|------------------------------------|
| id                            | UUID      | PK                                         | Internal identifier                |
| ticket_package_codestream_id  | UUID      | FK(ticket_package_codestream.id), NOT NULL | Parent codestream record           |
| product_id                    | UUID      | FK(product.id), NOT NULL                   | Related product                    |
| status                        | ENUM      | NOT NULL, DEFAULT ANALYSIS                 | Product status (inherited or overridden) |
| is_override                   | BOOLEAN   | NOT NULL, DEFAULT false                    | True if IM manually overrode the inherited status |
| released_at                   | TIMESTAMP | nullable                                  | When STAMP detected the fix in the product's repository |
| created_at                    | TIMESTAMP | NOT NULL, DEFAULT                          | Record creation timestamp          |
| updated_at                    | TIMESTAMP | NOT NULL, DEFAULT                          | Record update timestamp            |

**Unique constraint**: (ticket_package_codestream_id, product_id)

### PackageStatus Enum

A single enum used for status in both TicketPackageCodestream and
TicketPackageProduct.

| Value             | UI Label      | Color      | Type       | Set by             |
|-------------------|---------------|------------|------------|--------------------|
| ANALYSIS          | Analysis      | Neutral    | Non-final  | Automatic (default)|
| AFFECTED          | Affected      | Red        | Non-final  | IM (as "Affected") |
| AFFECTED_RESOLVED | Affected      | Green      | Final      | Automatic / IM override |
| NOT_AFFECTED      | Not Affected  | Green      | Final      | IM                 |
| WONT_FIX          | Won't Fix     | Green      | Final      | IM only            |
| IGNORED           | Ignored       | Greyed-out | Final      | IM only            |
| RELEASED          | Released      | Green      | Final      | Automatic or IM    |

**UI note**: The IM dropdown shows the following options: Analysis, Affected,
Not Affected, Won't Fix, Ignored, Released. The distinction between AFFECTED
and AFFECTED_RESOLVED is never exposed to the IM — when the IM selects
"Affected", STAMP internally decides which variant to use based on
eligibility.

### Status Behavior

#### IM sets "Affected" on a codestream

1. Codestream status is set to `AFFECTED`
2. STAMP propagates to all products under that codestream:
   - Product eligible (CVE CVSS >= product threshold) → `AFFECTED`
   - Product not eligible (CVE CVSS < product threshold) →
     `AFFECTED_RESOLVED`
3. Products with `is_override = true` are not modified

#### IM sets any other status on a codestream

1. Codestream status is set to the chosen value
2. STAMP propagates the same status to all products under that codestream
3. Products with `is_override = true` are not modified

#### IM overrides a product status

1. Product status is set to the chosen value (with eligibility logic applied
   if "Affected" is chosen)
2. `is_override` is set to `true`
3. The codestream status is not affected

#### Automatic transitions

The following transitions can be performed automatically by STAMP (e.g., when
detecting a release in IBS):

| From              | To                | Trigger                                |
|-------------------|-------------------|----------------------------------------|
| AFFECTED          | RELEASED          | STAMP detects fix in repository        |
| NOT_AFFECTED      | RELEASED          | STAMP detects fix in repository        |
| ANALYSIS          | RELEASED          | STAMP detects fix in repository        |
| AFFECTED          | AFFECTED_RESOLVED | Product not eligible (CVSS < threshold)|
| AFFECTED_RESOLVED | AFFECTED          | Product becomes eligible (threshold change) |

**Protected states**: `WONT_FIX` and `IGNORED` are never modified by automatic
transitions.

#### Manual transitions

The IM can manually change any status to any other status without restriction.

## Adding Packages to a Ticket

### Automatic (CPE mapping)

When a CVE is ingested, STAMP attempts to map the CPE data from the CVE record
to source package names. For each mapped package name, STAMP queries SMELT to
get the list of codestreams and products, and automatically creates the
TicketPackageCodestream and TicketPackageProduct records with status `ANALYSIS`.

### Manual

The IM can manually add a package by name to a ticket. STAMP queries SMELT to
resolve the codestreams and products, and creates the records with status
`ANALYSIS`.

## Release Tracking

STAMP monitors two levels of release for each affected package:

1. **Codestream level**: the fix has been added to the codestream's repository
   (e.g., `SUSE:SLE-15-SP6:Update`).
2. **Product level**: the fix has been copied to the product's repository
   (e.g., the SLES 15 SP6 update repository).

Both levels must be confirmed before the package is considered fully released
for a given product. STAMP detects releases by periodically querying IBS.

When a release is detected:
- The corresponding TicketPackageCodestream or TicketPackageProduct status is
  set to `RELEASED` (unless status is `WONT_FIX` or `IGNORED`)
- The `released_at` timestamp is set on TicketPackageProduct

## External Data Sources

### SMELT Integration

- **Endpoint**: HTTP API (URL configured in STAMP settings)
- **Query**: given a source package name, returns list of codestreams and
  products
- **Usage**: called when adding a package to a ticket (automatic or manual)
- **Sync**: periodic sync to update Codestream and Product tables, and
  CodestreamProduct mappings. Codestreams/products no longer reported by SMELT
  are marked `active = false`.

### AIMAAS Integration

- **Endpoint**: HTTP API (URL configured in STAMP settings)
- **Query**: given a product, returns lifecycle data and CVSS threshold
- **Usage**: periodic sync to update Product.cvss_threshold
- **Impact**: when a product's threshold changes, STAMP re-evaluates
  eligibility for all open tickets referencing that product

## UI Requirements

### Ticket Detail — Affectedness Section

The affectedness section on the ticket detail page displays a tree structure:

```
[+ Add Package]

Package: openssl-3                              [Remove]
├── SUSE:SLE-15-SP6:Update        [Affected ▼]
│   ├── SLES 15 SP6               Affected      (eligible)
│   ├── SLED 15 SP6               Affected      (eligible)
│   └── SLES 15 SP4 LTSS          Affected      (not eligible, threshold 7.0)
├── SUSE:SLE-15-SP5:Update        [Not Affected ▼]
│   └── SLES 15 SP5 LTSS          Not Affected
└── SUSE:SLE-15-SP3:Update        [Affected ▼]
    └── SLES 15 SP3 LTSS          Affected      (not eligible, threshold 9.0)

Package: curl                                   [Remove]
└── SUSE:SLE-15-SP4:Update        [Analysis ▼]
    └── ...
```

- **Package level**: shows the package name with an option to remove it
- **Codestream level**: shows the codestream name with a status dropdown.
  The dropdown shows: Analysis, Affected, Not Affected, Won't Fix, Ignored,
  Released.
- **Product level**: shows the product name, inherited status (with color),
  and eligibility indicator. Products have an option to override the status
  (which then shows a dropdown and marks `is_override = true`).
- **Color coding**: Red-Affected = red, all final states = green (except
  Ignored = greyed-out), Analysis = neutral/no color.
- **Add Package**: opens an input where the IM types a package name. STAMP
  queries SMELT and populates the tree. If SMELT returns no results, an error
  is shown.

### Ticket Lifecycle Integration

The ticket transitions that depend on affectedness data are updated as follows:

- **Analysis → Analyzed**: all TicketPackageCodestream records must have a
  final status (not `ANALYSIS`). Note: `AFFECTED` is non-final but is allowed
  for this transition since it indicates the IM has made a decision.
- **Analyzed → Resolved**: all TicketPackageCodestream and
  TicketPackageProduct records must have status `RELEASED`, `NOT_AFFECTED`,
  `WONT_FIX`, `IGNORED`, or `AFFECTED_RESOLVED`.

## Background Tasks

- `sync_smelt_data`: periodic task to sync codestreams, products, and
  mappings from SMELT. Marks entries no longer in SMELT as `active = false`.
- `sync_aimaas_data`: periodic task to sync product lifecycle data (CVSS
  threshold) from AIMAAS. When thresholds change, re-evaluates eligibility
  for open tickets.
- `check_release_status`: periodic task to check IBS for released packages
  and update TicketPackageCodestream / TicketPackageProduct statuses.

## Security

- Adding/removing packages on a ticket requires IM role (Security Team or
  Admin)
- Changing codestream/product status requires IM role
- Viewing affectedness data is available to all authenticated users
- SMELT and AIMAAS credentials are stored as environment variables, never in
  code

## Superseded Specifications

This feature replaces the following concepts from earlier specifications:

- **Distribution** table and **distro-management.md**: replaced by Codestream
  and Product. The `docs/features/distro-management.md` spec is superseded.
- **Package** table (as defined in data-model.md): package names are now
  stored inline in TicketPackageCodestream (`package_name` field) rather than
  as a separate entity with its own table.
- **AffectedPackage** table: replaced by TicketPackageCodestream and
  TicketPackageProduct.

## Future Considerations

- **openSUSE / OBS public**: tracking packages in build.opensuse.org for
  openSUSE Tumbleweed and Leap will be addressed in a separate spec.
- **Channel file parsing**: direct parsing of channel files from
  `SUSE:Channels` may be added if SMELT data is insufficient.
- **IBS release tracking details**: technical implementation of how STAMP
  queries IBS to detect releases will be detailed during implementation.
