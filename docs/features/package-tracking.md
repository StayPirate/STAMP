# Package Tracking

## Purpose

Track the affectedness of source packages across IBS codestreams and SUSE
products in the context of CVE tickets. This feature replaces the previous
Distribution/Package/AffectedPackage model with a structure that reflects how
SUSE actually organizes packages, codestreams, and products.

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

Codestreams are **not** maintained as a separate table in STAMP. SMELT does
not expose an endpoint to list codestreams independently — they are discovered
per-package via the `maintainedpackage` endpoint. The codestream name is stored
as a string directly in the TicketPackageCodestream record.

### Product

A SUSE product with its own repositories from which end users receive updates
via the package manager. Products include base products (e.g., SLES 15 SP6),
LTSS variants (e.g., SLES-LTSS 15-SP4), ESPOS variants (e.g., HPC ESPOS
15-SP5), and SAP variants. Each variant is a **separate product** in both
SMELT and AIMAAS, with its own CPE identifier.

A product receives binary packages from one or more codestreams. The same
codestream can feed multiple products. The mapping between a codestream's
packages and the products that receive them is resolved by SMELT on a
per-package basis.

### Channel File

An XML file in the IBS project `SUSE:Channels` that defines which packages
from which codestreams are shipped to which products. There is one channel file
per product. STAMP does not parse channel files directly — it relies on SMELT
to resolve these mappings.

### SMELT

An internal SUSE aggregator service (REST API at `smelt.suse.de/api`) that
provides:

1. **Product listing** (`GET /api/v1/basic/products/`): paginated list of all
   SUSE products with name, version, CPE, end-of-life date, and repository
   project names.
2. **Per-package maintenance info** (`GET /api/v1/basic/maintainedpackage/`):
   given a source package name, returns the list of codestreams where the
   package is maintained and the target repositories (which map to products).

SMELT reads from IBS, channel files, and other sources internally.

### AIMAAS

An internal SUSE service (REST API at `aimaas.suse.de/api`) that provides:

1. **Product lifecycle data** (`GET /api/entity/products/{slug}`): dates for
   each lifecycle phase — `fcs` (first customer shipment), `end_of_gs` (end
   of General Support), `end_of_ltss`, `end_of_espos`, and
   `end_of_reactive_ltss`.
2. **CVSS thresholds** (`GET /api/entity/cvss-threshold`): the minimum CVSS
   score for which a product is eligible to receive a security update. Only
   products with a non-zero threshold have an entry (currently ~24 products,
   mostly LTSS/ESPOS variants).

### Product Lifecycle Phases

Products go through different support phases. The applicable phase depends on
the product type:

| Phase | Determined by | Description |
|-------|--------------|-------------|
| **Pre-release** | `today < fcs` | Not yet shipped to customers |
| **General Support** | `fcs <= today < end_of_gs` | Full support, all CVEs eligible |
| **ESPOS** | `end_of_gs <= today < end_of_espos` | Extended Service Pack Overlap Support |
| **LTSS** | `end_of_gs <= today < end_of_ltss` | Long Term Service Pack Support |
| **Reactive LTSS** | `end_of_ltss <= today < end_of_reactive_ltss` | On-demand support only |
| **EOL** | Past all applicable dates | End of life, no updates |

Not all products go through all phases. Some products have ESPOS but no LTSS
(e.g., SAP Application modules), some have both (e.g., HPC), some have
neither. LTSS variants (separate products) may have a Reactive LTSS phase
after their LTSS phase ends.

### Package Eligibility

Eligibility determines whether a product will receive a security update for a
given CVE. The rules are:

1. **Check for CVSS threshold**: look up the product in AIMAAS
   `cvss-threshold` endpoint. If an entry exists, use its `threshold` value.
   If no entry exists, the threshold is implicitly 0 (all CVEs eligible).
2. **Apply threshold**: if the CVE's CVSS score is below the product's
   threshold, the product is not eligible — status is set to
   `AFFECTED_RESOLVED` (green "Affected") indicating the product is affected
   but no action is required.
3. **Reactive LTSS override**: if the product is currently in the Reactive
   LTSS phase (`end_of_ltss < today < end_of_reactive_ltss`), status is
   always `AFFECTED_RESOLVED` regardless of the CVSS score. The IM can still
   perform the assessment, but the result is always green.

## Data Model

See `docs/data-model.md` for the full schema. The tables defined by this
feature are:

### Product

Represents a SUSE product (base products, LTSS variants, ESPOS variants,
etc.). Each variant is a separate product with its own CPE. Synced from SMELT
and enriched with lifecycle data from AIMAAS.

See `docs/data-model.md` for the full column listing.

### ProductRepository

Maps SMELT repository project names to products. Used to resolve the `target`
values returned by SMELT's `maintainedpackage` endpoint to local Product
records. A single product typically has multiple repository entries (one per
architecture, plus separate entries for `SUSE:Products:*` and
`SUSE:Updates:*` namespaces).

See `docs/data-model.md` for the full column listing.

### TicketPackageCodestream

Records the affectedness status of a source package in a specific codestream
within the context of a ticket. The IM sets the status at this level. The
codestream is identified by name (a string), not by a foreign key — see the
Codestream section above for rationale.

See `docs/data-model.md` for the full column listing.

### TicketPackageProduct

Records the affectedness status of a source package for a specific product,
within the context of a ticket and codestream. Status is inherited from the
parent TicketPackageCodestream and adjusted for eligibility, but can be
overridden by the IM.

See `docs/data-model.md` for the full column listing.

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
   - Product in Reactive LTSS phase → `AFFECTED_RESOLVED`
   - Product has `cvss_threshold` and CVE CVSS < threshold →
     `AFFECTED_RESOLVED`
   - Otherwise → `AFFECTED`
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
| AFFECTED          | AFFECTED_RESOLVED | Product enters Reactive LTSS phase     |
| AFFECTED_RESOLVED | AFFECTED          | Product becomes eligible (threshold change or lifecycle phase change) |

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

### SMELT Query for Package Resolution

When adding a package to a ticket (automatic or manual), STAMP calls:

```
GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1
```

**Important implementation notes**:

- The parameter `include_reactive=1` MUST always be included to ensure
  products in Reactive LTSS phase are returned.
- Results are **paginated**. STAMP must follow the `next` field and fetch
  **all pages** to get the complete list of codestreams and products.
- Each result contains a `(package, codestream)` pair with a `channel`
  object. The `channel.targets` array lists the repository project names
  where the package is shipped.

**Processing each result**:

1. Create a `TicketPackageCodestream` record with the `codestream` value as
   `codestream_name` (if one does not already exist for this ticket +
   package + codestream combination).
2. For each `target` in `channel.targets`:
   a. Look up the target in the `ProductRepository` table to find the
      corresponding `Product`.
   b. If a matching product is found, create a `TicketPackageProduct` record
      linked to the `TicketPackageCodestream` (if one does not already exist).
   c. Deduplicate by product: multiple targets from the same result may map
      to the same product (one per architecture). Only one
      `TicketPackageProduct` record per product per codestream is needed.
3. If no matching product is found for a target, log a warning but do not
   fail — the product may not yet be synced from SMELT.

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

#### Product Sync (periodic)

- **Endpoint**: `GET /api/v1/basic/products/` (paginated)
- **Base URL**: `https://smelt.suse.de/api`
- **Response fields used**: `id`, `name`, `version`, `cpe`, `repos`
- **Sync behavior**:
  1. Iterate all pages of the products endpoint
  2. For each product, upsert a `Product` record using `smelt_id` as the
     match key, setting `name`, `version`, `cpe`
  3. For each product, replace the `ProductRepository` entries with the
     current `repos` list from SMELT
  4. Products no longer reported by SMELT are marked `active = false`
  5. Update `smelt_synced_at` timestamp on each synced product

#### Package Query (on-demand)

- **Endpoint**: `GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1`
  (paginated)
- **CRITICAL**: The `include_reactive=1` parameter MUST always be included.
  Without it, products currently in the Reactive LTSS phase are excluded from
  results.
- **CRITICAL**: Results are paginated. STAMP MUST iterate all pages by
  following the `next` URL until it is `null`.
- **Response structure** (per result):
  ```json
  {
    "package": "openssl-3",
    "codestream": "SUSE:SLE-15-SP6:Update",
    "channel": {
      "name": "channel-name",
      "status": "enabled",
      "targets": [
        {
          "status": "enabled",
          "target": "SUSE:Updates:SLE-Module-Basesystem:15-SP7:x86_64"
        }
      ]
    }
  }
  ```
- **Target resolution**: the `target` value is a SMELT repository project
  name. It is matched against the `ProductRepository.repo_name` column to
  find the corresponding `Product`. Multiple targets may map to the same
  product (one per architecture) — deduplicate by product.

### AIMAAS Integration

#### Product Lifecycle Sync (periodic)

- **Endpoint**: `GET /api/entity/products/{slug}` (individual product) or
  `GET /api/entity/products?limit=100&page={n}` (paginated list)
- **Base URL**: `https://aimaas.suse.de/api`
- **Matching**: AIMAAS products are matched to local `Product` records via
  `cpe`. Both SMELT and AIMAAS use identical CPE identifiers.
- **Response fields used**: `name` (used as `display_name` in STAMP), `cpe`,
  `fcs`, `end_of_gs`, `end_of_ltss`, `end_of_espos`, `end_of_reactive_ltss`
- **Note**: the list endpoint returns a subset of fields (no `cpe`, no
  lifecycle dates). To get full details, fetch each product individually by
  slug, or use the list endpoint to discover slugs and then fetch details.
- **Sync behavior**:
  1. For each local `Product` with a known CPE, find the matching AIMAAS
     product and update `display_name` and lifecycle date fields
  2. Update `aimaas_synced_at` timestamp

#### CVSS Threshold Sync (periodic)

- **Endpoint**: `GET /api/entity/cvss-threshold` (paginated)
- **Response fields used**: `product` (AIMAAS product ID), `threshold`
- **Matching**: each cvss-threshold entry has a `product` field containing an
  AIMAAS product ID. Fetch that product's details to obtain its CPE, then
  match to the local `Product` record via CPE.
- **Sync behavior**:
  1. Fetch all cvss-threshold entries
  2. For each entry, resolve the `product` ID to a CPE (via AIMAAS products
     endpoint)
  3. Update the corresponding local `Product.cvss_threshold`
  4. If a product's threshold changes, re-evaluate eligibility for all open
     tickets referencing that product
- **Note**: only ~24 products currently have a threshold entry. Products
  without an entry have an implicit threshold of 0 (all CVEs eligible).

## UI Requirements

### Ticket Detail — Affectedness Section

The affectedness section on the ticket detail page displays a tree structure:

```
[+ Add Package]

Package: openssl-3                              [Remove]
├── SUSE:SLE-15-SP6:Update        [Affected ▼]
│   ├── SLES 15 SP6               Affected      (eligible)
│   ├── SLED 15 SP6               Affected      (eligible)
│   └── SLES-LTSS 15-SP4          Affected      (not eligible, threshold 7.0)
├── SUSE:SLE-15-SP5:Update        [Not Affected ▼]
│   └── SLES-LTSS 15-SP5          Not Affected
└── SUSE:SLE-15-SP3:Update        [Affected ▼]
    └── SLES-LTSS 15-SP1          Affected      (reactive LTSS)

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

- **Analysis -> Analyzed**: all TicketPackageCodestream records must have a
  final status (not `ANALYSIS`). Note: `AFFECTED` is non-final but is allowed
  for this transition since it indicates the IM has made a decision.
- **Analyzed -> Resolved**: all TicketPackageCodestream and
  TicketPackageProduct records must have status `RELEASED`, `NOT_AFFECTED`,
  `WONT_FIX`, `IGNORED`, or `AFFECTED_RESOLVED`.

## Background Tasks

- `sync_smelt_products`: periodic task to sync products and their
  repositories from SMELT `GET /api/v1/basic/products/`. Iterates all pages.
  Products no longer reported by SMELT are marked `active = false`.
- `sync_aimaas_lifecycle`: periodic task to sync product lifecycle data
  (`fcs`, `end_of_gs`, `end_of_ltss`, `end_of_espos`,
  `end_of_reactive_ltss`) from AIMAAS. Matches to local products via CPE.
- `sync_aimaas_thresholds`: periodic task to sync CVSS thresholds from
  AIMAAS `GET /api/entity/cvss-threshold`. When thresholds change,
  re-evaluates eligibility for open tickets.
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

- **Distribution** table and **distro-management.md**: replaced by Product
  and ProductRepository. The `docs/features/distro-management.md` spec is
  superseded.
- **Package** table (as defined in data-model.md): package names are now
  stored inline in TicketPackageCodestream (`package_name` field) rather than
  as a separate entity with its own table.
- **AffectedPackage** table: replaced by TicketPackageCodestream and
  TicketPackageProduct.
- **Codestream** table: codestream names are now stored as strings in
  TicketPackageCodestream. SMELT does not provide an independent listing of
  codestreams.
- **CodestreamProduct** table: the codestream-to-product mapping is
  per-package and is already captured by the TicketPackageCodestream to
  TicketPackageProduct hierarchy.

## Future Considerations

- **openSUSE / OBS public**: tracking packages in build.opensuse.org for
  openSUSE Tumbleweed and Leap will be addressed in a separate spec.
- **Channel file parsing**: direct parsing of channel files from
  `SUSE:Channels` may be added if SMELT data is insufficient.
- **IBS release tracking details**: technical implementation of how STAMP
  queries IBS to detect releases will be detailed during implementation.
