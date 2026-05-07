# Package Tracking

## Purpose

Track the affectedness of source packages across IBS codestreams and SUSE
products in the context of tickets. See `docs/features/tickets/tickets.md` for
the ticket specification (identification, creation, lifecycle).

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

Codestreams are **not** maintained as a separate table in Sentinel. SMELT does
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
per product. Sentinel does not parse channel files directly — it relies on SMELT
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
given CVE. Eligibility is evaluated **only when a product's status would
become `AFFECTED`** — either through VA-initiated codestream propagation,
status inheritance during package addition, or CVSS/threshold/lifecycle
recalculation. It is never applied at initial record creation time when the
status is `ANALYSIS`.

The rules are:

1. **Check for CVSS threshold**: look up the product in AIMAAS
   `cvss-threshold` endpoint. If an entry exists, use its `threshold` value.
   If no entry exists, the threshold is implicitly 0 (all CVEs eligible).
2. **Resolve the CVSS score**: the score used for threshold comparison is
   determined by the CVSS resolution cascade (see
   `docs/features/tickets/cvss-scoring.md`):
   - SUSE assessment of the system-wide default CVSS version → if present,
     use this score
   - Highest score among all providers for the default CVSS version → if
     at least one exists, use the highest
   - No score available → treat as **10.0** (worst-case; the product is
     always eligible — a CVE without CVSS data is never excluded)
3. **Apply threshold**: if the resolved CVSS score is below the product's
   threshold, the product is not eligible — status is set to
   `AFFECTED_RESOLVED` (green "Affected") indicating the product is affected
   but no action is required.
4. **Reactive LTSS override**: if the product is currently in the Reactive
   LTSS phase (`end_of_ltss < today < end_of_reactive_ltss`), status is
   always `AFFECTED_RESOLVED` regardless of the CVSS score.

**Important**: the CVSS version used for threshold comparison MUST always
be resolved from the system-wide default CVSS version configuration — never
hardcoded. See `docs/features/tickets/cvss-scoring.md` and
`docs/features/platform/admin.md`.

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
within the context of a ticket. The VA sets the status at this level. The
codestream is identified by name (a string), not by a foreign key — see the
Codestream section above for rationale.

See `docs/data-model.md` for the full column listing.

### TicketPackageProduct

Records the affectedness status of a source package for a specific product,
within the context of a ticket and codestream. Status is inherited from the
parent TicketPackageCodestream and adjusted for eligibility, but can be
overridden by the VA.

See `docs/data-model.md` for the full column listing.

### PackageStatus Enum

A single enum used for status in both TicketPackageCodestream and
TicketPackageProduct.

| Value             | UI Label      | Color      | Type       | Set by             |
|-------------------|---------------|------------|------------|--------------------|
| ANALYSIS          | Analysis      | Neutral    | Non-final  | Automatic (default)|
| AFFECTED          | Affected      | Red        | Non-final  | VA (as "Affected") |
| AFFECTED_RESOLVED | Affected      | Green      | Final      | Automatic / VA override |
| NOT_AFFECTED      | Not Affected  | Green      | Final      | VA                 |
| WONT_FIX          | Won't Fix     | Green      | Final      | VA only            |
| IGNORED           | Ignored       | Greyed-out | Final      | VA only            |
| RELEASED          | Released      | Green      | Final      | Automatic (release detector) or VA |

**UI note**: The VA dropdown shows the following options: Analysis, Affected,
Not Affected, Won't Fix, Ignored, Released. The distinction between AFFECTED
and AFFECTED_RESOLVED is never exposed to the VA — when the VA selects
"Affected", Sentinel internally decides which variant to use based on
eligibility.

### Status Behavior

All codestream and product status changes described in this section MUST
go through the `ticket_mutations` module (see `docs/features/tickets/tickets.md`,
Ticket Mutations Module), which ensures automatic ticket status
re-evaluation after each change.

#### VA sets "Affected" on a codestream

1. Codestream status is set to `AFFECTED` (via `ticket_mutations`)
2. Sentinel propagates to all products under that codestream:
   - Product in Reactive LTSS phase → `AFFECTED_RESOLVED`
   - Product has `cvss_threshold` and resolved CVSS score < threshold →
     `AFFECTED_RESOLVED` (score resolved via the CVSS resolution cascade,
     see `docs/features/tickets/cvss-scoring.md`)
   - Otherwise → `AFFECTED`
3. Products with `is_override = true` are not modified
4. After propagation, if **all** products under the codestream are in
   `AFFECTED_RESOLVED`, the codestream itself is automatically set to
   `AFFECTED_RESOLVED` (no eligible product requires a fix, so no work is
   needed on this codestream)

#### VA sets any other status on a codestream

1. Codestream status is set to the chosen value
2. Sentinel propagates the same status to all products under that codestream
3. Products with `is_override = true` are not modified

#### VA overrides a product status

1. Product status is set to the chosen value (with eligibility logic applied
   if "Affected" is chosen)
2. `is_override` is set to `true`
3. The codestream status is not affected

#### Automatic transitions

The following transitions can be performed automatically by Sentinel (see
`docs/features/packages/ibs-codestream-release-detection.md` and
`docs/features/packages/ibs-product-release-detection.md` for the full detection
mechanisms):

| From              | To                | Applies to             | Trigger                                |
|-------------------|-------------------|------------------------|----------------------------------------|
| AFFECTED          | RELEASED          | TicketPackageCodestream | `CodestreamReleaseDetector` detects fix in codestream IBS project |
| NOT_AFFECTED      | RELEASED          | TicketPackageCodestream | `CodestreamReleaseDetector` detects fix in codestream IBS project |
| ANALYSIS          | RELEASED          | TicketPackageCodestream | `CodestreamReleaseDetector` detects fix in codestream IBS project |
| AFFECTED          | RELEASED          | TicketPackageProduct   | `ProductReleaseDetector` detects fix in product update repository (`updateinfo.xml`) |
| NOT_AFFECTED      | RELEASED          | TicketPackageProduct   | `ProductReleaseDetector` detects fix in product update repository (`updateinfo.xml`) |
| ANALYSIS          | RELEASED          | TicketPackageProduct   | `ProductReleaseDetector` detects fix in product update repository (`updateinfo.xml`) |
| AFFECTED          | AFFECTED_RESOLVED | TicketPackageProduct   | Product not eligible (resolved CVSS score < threshold) |
| AFFECTED          | AFFECTED_RESOLVED | TicketPackageProduct   | Product enters Reactive LTSS phase     |
| AFFECTED_RESOLVED | AFFECTED          | TicketPackageProduct   | Product becomes eligible (CVSS score change, threshold change, or lifecycle phase change) |
| AFFECTED          | AFFECTED_RESOLVED | TicketPackageCodestream | All products under the codestream are `AFFECTED_RESOLVED` (no eligible product) |
| AFFECTED_RESOLVED | AFFECTED          | TicketPackageCodestream | At least one product under the codestream returns to `AFFECTED` (product becomes eligible) |

**Codestream eligibility rollup**: whenever a `TicketPackageProduct` status
changes to or from `AFFECTED_RESOLVED`, the `ticket_mutations` module checks
the aggregate status of all sibling products under the same codestream:

- If the codestream is `AFFECTED` and **all** its products are now
  `AFFECTED_RESOLVED` → the codestream is set to `AFFECTED_RESOLVED`.
- If the codestream is `AFFECTED_RESOLVED` and **at least one** product
  returns to `AFFECTED` (e.g., due to CVSS score change, threshold change,
  or lifecycle phase change) → the codestream is set back to `AFFECTED`.

This check is performed as part of the same `ticket_mutations` operation that
changed the product status, before `evaluate_ticket_status` is called.

**Protected states**: `WONT_FIX` and `IGNORED` are never modified by automatic
transitions.

#### Manual transitions

The VA can manually change any status to any other status without restriction.

## Adding Packages to a Ticket

### Centralized Function: `add_package_to_ticket`

All package additions — regardless of the trigger — MUST go through a single
centralized service function. This function is the only place where SMELT is
queried to resolve codestreams and products, and where
`TicketPackageCodestream` and `TicketPackageProduct` records are created.

**Signature** (conceptual):

```python
add_package_to_ticket(ticket_id, package_name) -> AddPackageResult
```

**Behavior**:

1. Query SMELT to resolve all currently maintained codestreams and products
   for the given package (see [SMELT Query](#smelt-query-for-package-resolution)
   below).
2. For each resolved codestream, delegate `TicketPackageCodestream` record
   creation to `ticket_mutations`.
3. For each resolved product under each codestream, delegate
   `TicketPackageProduct` record creation to `ticket_mutations`.
4. Resolve and cache the IBS bugowner for the package. If a
   `PackageBugowner` record already exists for this `package_name`, update
   it with fresh data from IBS. If it does not exist, create it. See
   `docs/features/packages/package-bugowner.md` for the resolution algorithm.
5. Enqueue `discover_submissions_for_ticket_package(ticket_id, package_name)`
   to retroactively discover IBS submission requests (SRs) and release
   requests (RRs) for the ticket's CVE created within the last 14 days.
   See `docs/features/packages/ibs-submission-tracking.md`, Pipeline 3.
6. Return a result indicating which records were created and which were
   skipped (already existed).

`ticket_mutations` handles idempotency (skipping existing records),
initial status determination, and eligibility logic internally — see
`docs/features/tickets/tickets.md`, Ticket Mutations Module.

**Idempotency**: the function is safe to call multiple times for the same
package. If SMELT adds new codestreams or products for a package after the
initial addition, calling the function again will add only the new
records.

### Triggers

The following scenarios invoke `add_package_to_ticket`:

1. **Automatic (CPE mapping)**: when a CVE is ingested, Sentinel maps the CPE
   data from the CVE record to source package names. For each mapped
   package name, `add_package_to_ticket` is called.
2. **Manual**: the VA manually adds a package by name via the UI.
   `add_package_to_ticket` is called with the entered name.
3. **Codestream release detection (Case B)**: the `CodestreamReleaseDetector`
   finds a CVE fix in a package that is not tracked in the ticket. It calls
   `add_package_to_ticket` to add all codestreams and products, then sets
   the specific codestream where the fix was detected to `RELEASED`. See
   `docs/features/packages/ibs-codestream-release-detection.md` (Case B).
4. **Ticket auto-creation (Case C)**: a CVE fix is detected for a CVE with
   no existing ticket. After creating the ticket,
   `add_package_to_ticket` is called, then the originating codestream is
   set to `RELEASED`. See
   `docs/features/packages/ibs-codestream-release-detection.md` (Case C).

### Package Management Constraints

The VA manages packages at the **package level only**:

- The VA can **add** or **remove** entire packages from a ticket.
- The VA **cannot** add or remove individual codestreams or products —
  these are determined exclusively by SMELT when a package is added via
  `add_package_to_ticket`.
- The VA **can** change the status of individual codestreams (via the
  status dropdown) and override the status of individual products (which
  sets `is_override = true`).

### Removing a Package from a Ticket

When an VA removes a package from a ticket, Sentinel deletes **all**
`TicketPackageCodestream` and `TicketPackageProduct` records associated
with that package in the ticket.

**UI confirmation**: if any of the records being removed are in a final
status (`RELEASED`, `WONT_FIX`, `IGNORED`, `NOT_AFFECTED`, or
`AFFECTED_RESOLVED`), the UI must display a confirmation dialog before
proceeding (e.g., "This package has N codestreams/products in a final
status. Are you sure you want to remove it?"). The backend API does not
enforce this check — it is a UI-only safeguard.

A `TicketEvent` with `event_type = package_removed` is created to record
the removal (see
[Ticket Events for Package Changes](#ticket-events-for-package-changes)).

### Ticket Events for Package Changes

Every modification to a ticket's package data MUST produce a `TicketEvent`
record for audit and traceability. The following event types are defined:

| Action | `event_type` | `user_id` | Details recorded |
|--------|-------------|-----------|------------------|
| VA adds package | `package_added` | VA user | `package_name` |
| Package auto-added (CPE match or Case B) | `package_added` | `NULL` | `package_name`, contextual `comment` |
| VA removes package | `package_removed` | VA user | `package_name` |
| VA changes codestream status | `codestream_status_changed` | VA user | `package_name`, `codestream_name`, `old_status`, `new_status` |
| VA overrides product status | `product_status_overridden` | VA user | `package_name`, `product_id`, `old_status`, `new_status` |
| Ticket created | `ticket_created` | `NULL` | Creation source description |
| Codestream release detected | `codestream_released` | `NULL` | `package_name`, `codestream_name` |
| Product release detected | `product_released` | `NULL` | `package_name`, `product_id`, `advisory_id` |

- `user_id = NULL` indicates an automatic system action. For `package_added`,
  this distinguishes manual additions (VA user) from automatic ones (CPE
  match, codestream detection). The `comment` field provides context for
  automatic additions.
- All events include an implicit `created_at` timestamp.
- The "Details recorded" column lists the values stored in the event's
  `old_value`, `new_value`, and `comment` fields as strings. See
  `docs/features/tickets/ticket-history.md` for the exact field mapping and
  `docs/data-model.md` for the schema.

### SMELT Query for Package Resolution

When `add_package_to_ticket` resolves a package, it calls:

```
GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1
```

**Important implementation notes**:

- The parameter `include_reactive=1` MUST always be included to ensure
  products in Reactive LTSS phase are returned.
- Results are **paginated**. Sentinel must follow the `next` field and fetch
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

Sentinel monitors two **independent** levels of release for each affected
package:

1. **Codestream level**: the fix has been added to the codestream's IBS
   project (e.g., `SUSE:SLE-15-SP6:Update`). See
   `docs/features/packages/ibs-codestream-release-detection.md` for the full
   detection mechanism (MD5 cache, IBS diff analysis, match outcomes).
2. **Product level**: the fix has been published to the product's update
   repository (e.g., the SLES 15 SP6 update repository consumed by
   `zypper`). See `docs/features/packages/ibs-product-release-detection.md` for the
   full detection mechanism (updateinfo.xml parsing, advisory match chain).

The two levels are detected through different mechanisms and update different
records:

- The codestream level updates `TicketPackageCodestream.status` to `RELEASED`
  as soon as the fix appears in the codestream IBS project, **regardless of
  the status of the products under it**.
- The product level updates `TicketPackageProduct.status` to `RELEASED` and
  sets `released_at` as soon as the fix appears in that specific product's
  update repository.

In both cases, the automatic transition to `RELEASED` is suppressed when the
current status is `WONT_FIX` or `IGNORED` (these states are protected, see
"Status Behavior" above).

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
- **CRITICAL**: Results are paginated. Sentinel MUST iterate all pages by
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
- **Response fields used**: `name` (used as `display_name` in Sentinel), `cpe`,
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

## API Endpoints

### Add Package to Ticket

```
POST /api/v1/tickets/{ticket_id}/packages
```

Add a source package to a ticket. Sentinel queries SMELT to resolve all
maintained codestreams and products for the package, creates
`TicketPackageCodestream` and `TicketPackageProduct` records via
`ticket_mutations`, resolves the IBS bugowner, and enqueues submission
discovery. See [Adding Packages to a Ticket](#adding-packages-to-a-ticket)
for the full behavior.

**Request body**:

```json
{
  "package_name": "openssl-3"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `package_name` | string | Yes | Name of the source package to add |

**Response** (201 Created):

```json
{
  "data": {
    "package_name": "openssl-3",
    "codestreams_created": 3,
    "codestreams_skipped": 0,
    "products_created": 7,
    "products_skipped": 0
  }
}
```

The response reports how many records were created vs. skipped (already
existed). This supports idempotent re-calls — if the package was already
added, all counts will be zero in the `created` fields.

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `VALIDATION_ERROR` | Missing or empty `package_name` |
| 422 | `PACKAGE_NOT_FOUND_IN_SMELT` | SMELT returned no results for the given package name |
| 503 | `SMELT_UNAVAILABLE` | SMELT is unreachable or returned a server error |

**Idempotency**: safe to call multiple times for the same package. If the
package is already fully resolved, the response will report zero created
records.

---

### Remove Package from Ticket

```
DELETE /api/v1/tickets/{ticket_id}/packages/{package_name}
```

Remove a package and all its associated `TicketPackageCodestream` and
`TicketPackageProduct` records from the ticket. Creates a `TicketEvent`
with `event_type = package_removed`.

**Response**: 204 No Content (empty body).

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |

---

### Change Codestream Status

```
PATCH /api/v1/tickets/{ticket_id}/packages/{package_name}/codestreams/{codestream_name}
```

Change the affectedness status of a codestream. Triggers status propagation
to all child products (with eligibility evaluation for "Affected"), the
codestream eligibility rollup, TicketEvent creation, and ticket status
re-evaluation — all via `ticket_mutations`.

**Request body**:

```json
{
  "status": "AFFECTED"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status value. Valid values: `ANALYSIS`, `AFFECTED`, `NOT_AFFECTED`, `WONT_FIX`, `IGNORED`, `RELEASED` |

**Note on PATCH with side effects**: this endpoint uses PATCH because from
the client's perspective it is a single-field update on a specific
resource. The side effects (product propagation, eligibility evaluation,
ticket status re-evaluation) are a consequence of the domain model, not
of additional business operations. This is a documented deviation from the
`POST /resource/{id}/verb` convention for operations with side effects.

**Response** (200 OK):

```json
{
  "data": {
    "ticket_id": "uuid",
    "package_name": "openssl-3",
    "codestream_name": "SUSE:SLE-15-SP6:Update",
    "status": "AFFECTED",
    "products": [
      {
        "product_id": "uuid",
        "product_name": "SLES 15 SP6",
        "status": "AFFECTED",
        "is_override": false
      },
      {
        "product_id": "uuid",
        "product_name": "SLES-LTSS 15-SP4",
        "status": "AFFECTED_RESOLVED",
        "is_override": false
      }
    ]
  }
}
```

The response includes the updated codestream and all its child products
with their resulting statuses (after propagation and eligibility
evaluation), allowing the client to update the UI tree without a separate
fetch.

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package or codestream not found on this ticket |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `VALIDATION_ERROR` | Invalid status value |

---

### Override Product Status

```
PATCH /api/v1/tickets/{ticket_id}/packages/{package_name}/products/{product_id}
```

Override the affectedness status of a specific product. Sets
`is_override = true` on the product record. Triggers the codestream
eligibility rollup, TicketEvent creation, and ticket status re-evaluation
via `ticket_mutations`.

**Request body**:

```json
{
  "status": "WONT_FIX"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status value. Valid values: `ANALYSIS`, `AFFECTED`, `NOT_AFFECTED`, `WONT_FIX`, `IGNORED`, `RELEASED`, `AFFECTED_RESOLVED` |

**Note on PATCH with side effects**: same rationale as the codestream
endpoint above — single-field update from the client's perspective.

**Response** (200 OK):

```json
{
  "data": {
    "ticket_id": "uuid",
    "package_name": "openssl-3",
    "codestream_name": "SUSE:SLE-15-SP6:Update",
    "product_id": "uuid",
    "product_name": "SLES-LTSS 15-SP4",
    "status": "WONT_FIX",
    "is_override": true
  }
}
```

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package or product not found on this ticket |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `VALIDATION_ERROR` | Invalid status value |

---

### List Products

```
GET /api/v1/products
```

List all products synced from SMELT. Paginated.

**Query parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | int | 1 | Page number |
| `per_page` | int | 20 | Items per page (max: 100) |
| `sort_by` | string | `name` | Sort field. Valid values: `name`, `version`, `cpe`, `created_at` |
| `sort_order` | string | `asc` | Sort direction: `asc` or `desc` |
| `search` | string | — | Filter by name (case-insensitive substring match) |
| `active` | boolean | — | Filter by active status. If omitted, returns all products |
| `lifecycle_phase` | string | — | Filter by current lifecycle phase. Valid values: `pre_release`, `general_support`, `espos`, `ltss`, `reactive_ltss`, `eol` |

**Response** (200 OK):

```json
{
  "data": [
    {
      "id": "uuid",
      "name": "SUSE Linux Enterprise Server",
      "version": "15 SP6",
      "cpe": "cpe:/o:suse:sles:15:sp6",
      "display_name": "SLES 15 SP6",
      "active": true,
      "lifecycle_phase": "general_support",
      "cvss_threshold": null,
      "smelt_synced_at": "2025-01-15T02:00:00Z",
      "aimaas_synced_at": "2025-01-15T03:00:00Z"
    }
  ],
  "meta": {
    "total": 142,
    "page": 1,
    "per_page": 20
  }
}
```

**Permissions**: public endpoint (no authentication required).

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | Invalid query parameter value (e.g., non-integer `page`, unknown `sort_by` field, unknown `lifecycle_phase` value) |

---

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
- **Add Package**: opens an input where the VA types a package name. Sentinel
  queries SMELT and populates the tree. If SMELT returns no results, an error
  is shown.

### Ticket Lifecycle Integration

See `docs/features/tickets/tickets.md` (Ticket Lifecycle) for the authoritative
gate conditions and status transition rules. All codestream and product
status changes go through the `ticket_mutations` module, which
automatically re-evaluates ticket status after each change (see
`docs/features/tickets/tickets.md`, Centralized Status Evaluation). The
affectedness-related conditions are summarized here for context:

- **Analysis → Analyzed** (automatic): at least one package must be
  added, no TicketPackageCodestream or TicketPackageProduct records may
  be in `ANALYSIS` status. Additional gate conditions (severity, CVSS)
  are defined in `docs/features/tickets/tickets.md`.
- **Analyzed → Resolved** (automatic): all TicketPackageCodestream and
  TicketPackageProduct records must have status `RELEASED`,
  `NOT_AFFECTED`, `WONT_FIX`, `IGNORED`, or `AFFECTED_RESOLVED`.
- **Analyzed → Analysis** (automatic): gate conditions for Analyzed no
  longer met (e.g., package added with codestreams in `ANALYSIS`, or VA
  resets a codestream status to `ANALYSIS`).
- **Resolved → Analyzed** (automatic): resolved gate conditions no
  longer met but analyzed gates still met (e.g., CVSS recalculation
  causes products to transition from `AFFECTED_RESOLVED` to `AFFECTED`).
  See `docs/features/tickets/cvss-scoring.md` (Recalculation Cascade).
- **Resolved → Analysis** (automatic): both resolved and analyzed gate
  conditions no longer met (e.g., package added with codestreams in
  `ANALYSIS`).

## Background Tasks

- `sync_smelt_products`: periodic task to sync products and their
  repositories from SMELT `GET /api/v1/basic/products/`. Iterates all pages.
  Products no longer reported by SMELT are marked `active = false`.
- `sync_aimaas_lifecycle`: periodic task to sync product lifecycle data
  (`fcs`, `end_of_gs`, `end_of_ltss`, `end_of_espos`,
  `end_of_reactive_ltss`) from AIMAAS. Matches to local products via CPE.
- `sync_aimaas_thresholds`: periodic task to sync CVSS thresholds from
  AIMAAS `GET /api/entity/cvss-threshold`. When thresholds change,
  re-evaluates eligibility for active tickets.
- `check_codestream_releases`: periodic task (every 24 hours at 02:00
  UTC via Celery Beat) that invokes the `CodestreamReleaseDetector`
  service. Serves as a catch-up mechanism for events missed by the
  real-time `IBSEventConsumer` (see
  `docs/features/integrations/ibs-rabbitmq-integration.md`). See
  `docs/features/packages/ibs-codestream-release-detection.md` for the full
  procedure.
- `check_product_releases`: periodic task that invokes the
  `ProductReleaseDetector` (`updateinfo.xml`-based) for
  `TicketPackageProduct` records and applies the automatic transitions to
  `RELEASED`. See `docs/features/packages/ibs-product-release-detection.md` for
  the full procedure. Frequency and scope are TBD.
- `create_ticket_from_detection`: on-demand task enqueued by the
  `CodestreamReleaseDetector` or the `IBSEventConsumer` when a CVE fix
  is detected for a CVE that has no ticket in Sentinel. Fetches CVE data
  from NVD, creates the ticket, resolves packages via SMELT, and sets
  the originating codestream to `RELEASED`. See
  `docs/features/packages/ibs-codestream-release-detection.md` (Case C) for
  details.
- `check_lifecycle_phase_transitions`: periodic task (daily at 04:00 UTC)
  that detects products currently in Reactive LTSS or EOL phase with
  actionable `TicketPackageProduct` records and enqueues re-evaluation.
  Idempotent — operates on current state with no cache. See
  `docs/features/packages/product-lifecycle-transitions.md` for the full
  specification.

## Security

- Adding/removing packages on a ticket requires the Vulnerability Analyst role
- Changing codestream/product status requires the Vulnerability Analyst role
- Viewing affectedness data is publicly accessible (no authentication
  required)
- SMELT and AIMAAS credentials are stored as environment variables, never in
  code

## Future Considerations

- **openSUSE / OBS public**: tracking packages in build.opensuse.org for
  openSUSE Tumbleweed and Leap will be addressed in a separate spec.
- **Channel file parsing**: direct parsing of channel files from
  `SUSE:Channels` may be added if SMELT data is insufficient.
- **IBS release tracking details**: both detection levels are now fully
  specified in dedicated specs. See
  `docs/features/packages/ibs-codestream-release-detection.md` (IBS source info
  and diff endpoints) and `docs/features/packages/ibs-product-release-detection.md`
  (`updateinfo.xml` parsing).
