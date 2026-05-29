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
(e.g., HPC ESPOS 15-SP5), and SAP variants. Each variant is a
**separate product** in both SMELT and AIMAAS, with its own CPE
identifier.

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

Products go through different support phases. The applicable phase
depends on the product type:

| Phase | Determined by | Description |
|-------|--------------|-------------|
| **Pre-release** | `today < fcs` | Not yet shipped to customers |
| **General Support** | `fcs <= today < end_of_gs` | Full support, all CVEs eligible |
| **ESPOS** | `end_of_gs <= today < end_of_espos` | Extended Service Pack Overlap Support |
| **LTSS** | `end_of_gs <= today < end_of_ltss` | Long Term Service Pack Support |
| **Reactive LTSS** | `end_of_ltss <= today < end_of_reactive_ltss` | On-demand support only |
| **EOL** | Past all applicable dates | End of life, no updates |

Not all products go through all phases. Some products have ESPOS but no
LTSS (e.g., SAP Application modules), some have both (e.g., HPC), some
have neither. LTSS variants (separate products) may have a Reactive LTSS
phase after their LTSS phase ends.

For automated actions triggered by lifecycle phase transitions (Reactive
LTSS eligibility changes, EOL product removal), see
`docs/features/packages/product-lifecycle-transitions.md`.

---

## Data Model

See `docs/data-model.md` for the full schema. The tables owned by this
feature are:

### Product

Represents a SUSE product (base products, LTSS variants, ESPOS variants,
etc.). Each variant is a separate product with its own CPE. Synced from
SMELT and enriched with lifecycle data from AIMAAS.

See `docs/data-model.md` for the full column listing.

### ProductRepository

Maps SMELT repository project names to products. Used to resolve the
`target` values returned by SMELT's `maintainedpackage` endpoint to
local Product records. A single product typically has multiple repository
entries (one per architecture, plus separate entries for
`SUSE:Products:*` and `SUSE:Updates:*` namespaces).

See `docs/data-model.md` for the full column listing.

---

## SMELT Integration -- Product Sync

Product data is synced from SMELT's product listing endpoint. See
`docs/features/packages/package-model.md` (Domain Concepts: SMELT)
for the general SMELT description.

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

---

## AIMAAS Integration

### Product Lifecycle Sync (periodic)

- **Endpoint**: `GET /api/entity/products/{slug}` (individual product)
  or `GET /api/entity/products?limit=100&page={n}` (paginated list)
- **Base URL**: `https://aimaas.suse.de/api`
- **Matching**: AIMAAS products are matched to local `Product` records
  via `cpe`. Both SMELT and AIMAAS use identical CPE identifiers.
- **Response fields used**: `name` (used as `display_name` in Sentinel),
  `cpe`, `fcs`, `end_of_gs`, `end_of_ltss`, `end_of_espos`,
  `end_of_reactive_ltss`
- **Note**: the list endpoint returns a subset of fields (no `cpe`, no
  lifecycle dates). To get full details, fetch each product individually
  by slug, or use the list endpoint to discover slugs and then fetch
  details.
- **Sync behavior**:
  1. For each local `Product` with a known CPE, find the matching
     AIMAAS product and update `display_name` and lifecycle date fields
  2. Update `aimaas_synced_at` timestamp

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
  3. Update the corresponding local `Product.cvss_threshold`
  4. If a product's threshold changes, re-evaluate eligibility for all
     open tickets referencing that product
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
| `active` | boolean | -- | Filter by active status. If omitted, returns all products |
| `lifecycle_phase` | string | -- | Filter by current lifecycle phase. Valid values: `pre_release`, `general_support`, `espos`, `ltss`, `reactive_ltss`, `eol` |

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

**`Access: Public`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 422 | `VALIDATION_ERROR` | Invalid query parameter value (e.g., non-integer `page`, unknown `sort_by` field, unknown `lifecycle_phase` value) |

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

TBD

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

TBD

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

TBD

#### Error Handling

TBD

#### Metrics

TBD

---

## Security

- Listing products is publicly accessible (no authentication required)
- SMELT and AIMAAS base URLs are configured via environment variables
  (`SMELT_API_URL`, `AIMAAS_API_URL`). See `docs/configuration.md`
- Authentication requirements for SMELT and AIMAAS are TBD (see
  `docs/data-sources.md`). When defined, credentials will be provided
  via environment variables, never in code

---

## Cross-references

- `docs/api-spec.md` -- global API conventions (envelope format, error
  codes, pagination, shared 422 responses)
- `docs/data-model.md` -- full database schema (Product,
  ProductRepository tables)
- `docs/features/packages/package-model.md` -- package affectedness
  model; eligibility rules consume product lifecycle and threshold data
- `docs/features/packages/product-lifecycle-transitions.md` -- EOL and
  Reactive LTSS automated actions
- `docs/features/tickets/cvss-scoring.md` -- CVSS resolution cascade
  used for threshold comparison
- `docs/features/platform/system-settings.md` -- default CVSS version
  configuration
