# Product Catalog

## Purpose

Maintain a local catalog of SUSE products, synchronized from SMELT
(product listing and repository mappings) and enriched with lifecycle
data and CVSS thresholds from AIMAAS. The product catalog is the
foundation for package tracking eligibility decisions and release
detection across all tickets.

## Domain Concepts

### Product

A SUSE product with its own repositories from which end users receive
updates via the package manager. Products include base products (e.g.,
SLES 15 SP6), LTSS variants (e.g., SLES-LTSS 15-SP4), ESPOS variants
(e.g., HPC ESPOS 15-SP5), and SAP variants. Each variant exposed by
SMELT is a separate Sentinel Product identified by its CPE. AIMAAS
coverage is independent: absence from AIMAAS does not remove, deactivate,
or otherwise change the SMELT-backed Product identity.

A product receives binary packages from one or more tracks. The same
track can feed multiple products. The mapping between a track's packages
and the products that receive them is resolved by SMELT on a per-package
basis (see `docs/features/packages/package-model.md`, SMELT Query for
Package Resolution).

### AIMAAS

An internal SUSE service (REST API at `aimaas.suse.de/api`) that
provides:

1. **Product lifecycle data** (`GET /api/entity/products/{slug}`): dates
   for each lifecycle phase -- `fcs` (first customer shipment),
   `end_of_gs` (end of General Support), `end_of_ltss`, `end_of_espos`,
   and `end_of_reactive_ltss`.
2. **CVSS thresholds** (`GET /api/entity/cvss-threshold`): the minimum
   CVSS score for which a product is eligible to receive a security
   update. Only products with a non-zero threshold have an entry
   (currently ~24 products, mostly LTSS/ESPOS variants).

### Product Lifecycle Phases

Sentinel derives one of five lifecycle phases from the AIMAAS projection:

| Value | Description |
|-------|-------------|
| `pre_release` | The Product has not reached first customer shipment |
| `general_support` | The Product is in General Support |
| `extended_support` | The Product is in LTSS, ESPOS, or their overlapping interval |
| `reactive_support` | The Product is in Reactive LTSS |
| `eol` | The Product has reached end of life |

Sentinel does not distinguish LTSS from ESPOS because they have equivalent
platform behavior. The exact boundary inclusivity, precedence, and outcome
for missing or inconsistent lifecycle dates are not yet defined. Until that
contract is completed, missing lifecycle data MUST NOT be interpreted as
`eol`.

For automated actions triggered by lifecycle phase transitions (Reactive
Support eligibility changes, EOL Product removal), see
`docs/features/packages/product-lifecycle-transitions.md`.

---

## Data Model

See `docs/data-model.md` for the full schema. The tables owned by this
feature are:

### Product

Represents a SUSE product (base products, LTSS variants, ESPOS variants,
etc.). Each variant is a separate product with its own CPE. Synced from
SMELT and enriched with lifecycle data from AIMAAS.

The internal UUIDv7 `id` is the database primary key. CPE is the canonical
Product identity and is unique and non-null. SMELT IDs are not persisted;
`name`, `version`, and `display_name` are descriptive attributes and are not
identity constraints. A Product absent from a later complete SMELT snapshot
is retained. Catalog presence and lifecycle are independent.

See `docs/data-model.md` for the full column listing.

### ProductRepository

Maps SMELT repository project names to products. Used to resolve the
`target` values returned by SMELT's `maintainedpackage` endpoint to
local Product records. A single product typically has multiple repository
entries (one per architecture, plus separate entries for
`SUSE:Products:*` and `SUSE:Updates:*` namespaces).

Repository names are not globally unique. An association is unique by
`(product_id, repo_name)`. Associations absent from a later complete catalog
snapshot are retained because existing ticket records may still require them
for Product-level release detection.

See `docs/data-model.md` for the full column listing.

---

## SMELT Integration -- Product Sync

Product data is synced from SMELT's product listing endpoint. See
`docs/features/packages/package-model.md` (Domain Concepts: SMELT)
for the general SMELT description.

- **Endpoint**: `GET /api/v1/basic/products/` (paginated)
- **Base URL**: `https://smelt.suse.de/api`
- **Response fields used**: `name`, `version`, `cpe`, `friendly_name`, `repos`
- **Sync behavior**:
  1. Fetch every page before opening a database transaction. Pagination MUST
     NOT downgrade HTTPS or send requests or credentials to an untrusted
     origin. The exact continuation-URL validation and normalization rules
     remain to be completed.
  2. Normalize and validate the complete snapshot before publication. Empty,
     partial, truncated, or inconsistent responses MUST NOT be published.
     The exact validation criteria remain to be completed.
  3. Capture one UTC `snapshot_at` value for the complete snapshot.
  4. In one database transaction, upsert Products by exact CPE and
     ProductRepository associations by `(product_id, repo_name)`. Assign the
     same `snapshot_at` to every observed row's `catalog_last_seen_at`.
     The Product upsert modifies only the SMELT-owned descriptive fields
     (`name`, `version`, `display_name`) and `catalog_last_seen_at`; it MUST
     NOT clear or overwrite AIMAAS-owned lifecycle or threshold fields.
  5. Commit once. Any database error rolls back the complete publication.
     Network I/O MUST NOT occur while the transaction is open.

Products and associations not observed in the new snapshot are retained with
their previous `catalog_last_seen_at`. The applied snapshot is identified by
`MAX(Product.catalog_last_seen_at)`; a row belongs to that snapshot when its
`catalog_last_seen_at` equals that value. Snapshot identity does not depend on
`FetcherRun` finalization, which occurs in a separate transaction.

---

## AIMAAS Integration

HTTPS connections to SMELT (`smelt.suse.de`) and AIMAAS
(`aimaas.suse.de`) are validated via the combined trust store (system
CAs + SUSE Trust Root CA). See `networking.md`, TLS Trust
Store Configuration.

### Product Lifecycle Sync (periodic)

- **Endpoint**: `GET /api/entity/products/{slug}` (individual Product)
  or `GET /api/entity/products?size=100&page={n}` (paginated list)
- **Base URL**: `https://aimaas.suse.de/api`
- **Matching**: AIMAAS products are matched to local `Product` records by
  exact CPE. The two catalogs have different coverage; unmatched Products are
  expected and MUST NOT be matched heuristically by name or version.
- **Response fields used**: `cpe`, `fcs`, `end_of_gs`, `end_of_ltss`,
  `end_of_espos`, `end_of_reactive_ltss`
- **Sync behavior**:
  1. Resolve AIMAAS Product details to an exact CPE match.
  2. Set `first_customer_ship_date` from `fcs` and
     `general_support_end_date` from `end_of_gs`.
  3. Set `extended_support_end_date` to the latest non-null value of
     `end_of_ltss` and `end_of_espos`, or NULL when both are NULL.
  4. Set `reactive_support_end_date` from `end_of_reactive_ltss`.

The lifecycle synchronization modifies only the four lifecycle date columns;
it does not modify SMELT-owned descriptive or catalog-observation fields, or
`cvss_threshold`.

The Product discovery/detail strategy, complete-snapshot behavior, clearing
rules, and freshness semantics remain to be completed.

### CVSS Threshold Sync (periodic)

- **Endpoint**: `GET /api/entity/cvss-threshold` (paginated)
- **Response fields used**: `product` (AIMAAS product ID), `threshold`
- **Matching**: each cvss-threshold entry has a `product` field
  containing an AIMAAS product ID. Fetch that product's details to
  obtain its CPE, then match to the local `Product` record via CPE.
- **Sync behavior**:
  1. Fetch all cvss-threshold entries
  2. For each entry, resolve the `product` ID to a CPE (via AIMAAS
     products endpoint)
  3. Update only the corresponding local `Product.cvss_threshold`; do not
     modify catalog-observation, descriptive, or lifecycle date fields.
  4. If a Product's threshold changes, trigger the eligibility re-evaluation
     defined in `product-lifecycle-transitions.md`.
- **Note**: only ~24 products currently have a threshold entry. Products
  without an entry have an implicit threshold of 0 (all CVEs eligible).

---

## API Endpoints

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
| `search` | string | -- | Filter by name (case-insensitive substring match) |
| `lifecycle_phase` | string (repeatable) | -- | Filter by current lifecycle phase. Valid values: `pre_release`, `general_support`, `extended_support`, `reactive_support`, `eol`. Multiple values use OR semantics |

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
      "lifecycle_phase": "general_support",
      "cvss_threshold": null,
      "catalog_last_seen_at": "2025-01-15T02:00:00Z"
    }
  ],
  "meta": {
    "total": 142,
    "page": 1,
    "per_page": 20
  }
}
```

**`Access: Public`**
**`Authentication: Optional`**

---

## Background Tasks

### Fetcher: `sync_smelt_products`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_smelt_products` |
| Class name | TBD |
| Schedule | TBD |
| Source | SMELT (`smelt.suse.de/api`) |
| Scope | TBD |
| Auth | TBD (internal) |
| Custom settings | TBD |

#### Algorithm

The approved publication algorithm is defined in [SMELT Integration --
Product Sync](#smelt-integration----product-sync). Complete response,
pagination, validation, readiness, and recovery behavior remains to be
defined.

#### Error Handling

TBD

#### Metrics

TBD

### Fetcher: `sync_aimaas_lifecycle`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_aimaas_lifecycle` |
| Class name | TBD |
| Schedule | TBD |
| Source | AIMAAS (`aimaas.suse.de/api`) |
| Scope | TBD |
| Auth | TBD (internal) |
| Custom settings | TBD |

#### Algorithm

The approved field projection is defined in [Product Lifecycle Sync
(periodic)](#product-lifecycle-sync-periodic). Discovery, clearing,
freshness, publication, and recovery behavior remains to be defined.

#### Error Handling

TBD

#### Metrics

TBD

### Fetcher: `sync_aimaas_thresholds`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_aimaas_thresholds` |
| Class name | TBD |
| Schedule | TBD |
| Source | AIMAAS (`aimaas.suse.de/api`) |
| Scope | TBD |
| Auth | TBD (internal) |
| Custom settings | TBD |

#### Algorithm

The approved matching and column-ownership behavior is defined in [CVSS
Threshold Sync (periodic)](#cvss-threshold-sync-periodic). Complete snapshot,
clearing, recalculation, and recovery behavior remains to be defined.

#### Error Handling

TBD

#### Metrics

TBD

---

## Security

- Listing Products is public with optional authentication. Selected invalid
  credentials are rejected according to `docs/api-spec.md`.
- SMELT and AIMAAS base URLs are configured via environment variables
  (`SMELT_API_URL`, `AIMAAS_API_URL`). See [Configuration](#configuration)
- Authentication requirements for SMELT and AIMAAS are TBD (see
  `docs/data-sources.md`). When defined, credentials will be provided
  via environment variables, never in code

---

## Configuration

- `SMELT_API_URL`: SMELT API base URL for product catalog sync and
  package resolution (default: `https://smelt.suse.de/api`)
- `AIMAAS_API_URL`: AIMAAS API base URL for product lifecycle and CVSS
  threshold sync (default: `https://aimaas.suse.de/api`)

## Cross-references

- `docs/api-spec.md` -- global API conventions (envelope format, error
  codes, pagination, shared 422 responses)
- `docs/data-sources.md` -- Product source authority and external-service
  access details
- `docs/data-model.md` -- full database schema (Product,
  ProductRepository tables)
- `docs/features/packages/package-model.md` -- package affectedness
  model; eligibility rules consume product lifecycle and threshold data
- `docs/features/packages/product-lifecycle-transitions.md` -- EOL and
  Reactive Support automated actions
- `docs/features/tickets/cvss-scoring.md` -- CVSS resolution cascade
  used for threshold comparison
- `docs/features/platform/system-settings.md` -- default CVSS version
  configuration
- `docs/features/platform/networking.md` -- HTTP client and TLS trust store
