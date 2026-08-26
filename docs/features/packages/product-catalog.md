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
platform behavior. If the available AIMAAS fields are insufficient to derive
one of these phases, the lifecycle phase is unavailable and represented as
`NULL`; it is not treated as General Support, Reactive Support, or EOL. The
exact boundary inclusivity, precedence, and treatment of inconsistent
lifecycle dates remain to be defined. Missing or inconsistent lifecycle data
MUST NOT be interpreted as `eol`.

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

Maps SMELT repository project names to products. Used by the Product
catalog synchronization and by release detection to enumerate a Product's
known update repositories. Package resolution (adding packages to tickets)
matches products directly by CPE from the SMELT v2 maintained-package
response and does not use `ProductRepository`.

A single product typically has multiple repository entries (one per
architecture, plus separate entries for `SUSE:Products:*` and
`SUSE:Updates:*` namespaces).

Repository names are not globally unique. An association is unique by
`(product_id, repo_name)`. Associations absent from a later complete catalog
snapshot are retained because existing ticket records may still require them
for Product-level release detection.

An association is **current** when its `catalog_last_seen_at` equals the
timestamp of the latest complete Product catalog snapshot; otherwise it is
historical. Historical associations remain available for historical lookup
and Product-level release detection.

See `docs/data-model.md` for the full column listing.

---

## SMELT Integration

### Origin, Authentication, and Pagination

The configured `SMELT_API_URL` identifies the SMELT API prefix. Its default
value is `https://smelt.suse.de/api`; endpoint paths such as
`v1/basic/products/` are appended to that prefix after removing any trailing
slash (`https://smelt.suse.de/api/v1/basic/products/` for the default). The
configured URL MUST use HTTPS and MUST NOT contain user information, a query,
or a fragment. A non-default port is permitted for an explicitly configured
deployment. At application startup, Sentinel validates these constraints and
refuses to start with an error naming `SMELT_API_URL` when the value is invalid.

SMELT requests use no authentication. Sentinel sends no credentials and does
not follow redirects automatically. A future upstream authentication
requirement is a contract change and requires specification review before the
integration changes.

HTTPS connections to SMELT use the combined trust store (system CAs + SUSE
Trust Root CA). See `networking.md`, TLS Trust Store Configuration.

The Product listing endpoint (`v1/basic/products/`) uses the following
pagination contract:

1. Request pages sequentially, starting at page 1, with an explicit
   `page_size=100`. Construct every request from the configured HTTPS API
   prefix and the known endpoint path; never use `next` as a request
   destination.
2. Require an object containing integer `count` and `total_pages`, nullable
   string `next` and `previous`, and array `results` on every page. `count`
   MUST be non-negative, `total_pages` MUST be positive, and both values MUST
   remain constant across the retrieval.
3. Treat `next` and `previous` only as consistency metadata. When present,
   parse them and require the configured hostname, the expected endpoint path,
   the fixed page size, the expected adjacent page number, and exactly the
   endpoint-specific immutable query parameters. An explicitly present port
   must equal the configured port. When `SMELT_API_URL` includes an explicit
   port, the metadata must contain the same explicit port; when the setting
   omits one, the metadata must also omit it. In the latter case, the accepted
   `http` metadata scheme does not create an implied-port mismatch. An omitted
   `page` value in `previous` means page 1, matching verified SMELT
   serialization. User
   information, fragments, unknown or duplicate query parameters, malformed or
   repeated page values, a different origin or path, and a non-adjacent page
   invalidate the response. SMELT currently serializes these metadata URLs
   with `http`; that known scheme defect is accepted only in the parsed
   metadata. Every Sentinel request still uses the configured HTTPS origin.
4. Fetch each page exactly once in increasing order. Every non-final page MUST
   contain results and a non-null `next`; `previous` MUST be null only on page
   1 and non-null afterward; the final page MUST have `next = NULL`; and the
   number of collected results MUST equal `count`. A pagination inconsistency
   aborts the operation; Sentinel does not restart the sequence within the
   same operation.

Live verification on 2026-08-25 confirmed this envelope on the Product listing
endpoint, one-based page numbering, an effective maximum page size of 500, and
the HTTP scheme defect in continuation metadata. Using the fixed page size and
server-provided `total_pages` avoids depending on either an inferred default or
the requested page size being applied unchanged.

The maintained-package endpoint (`experimental/v2/maintained/`) is not
paginated; it returns all results in a single JSend response. Its envelope and
processing contract are defined in `package-model.md` (SMELT Query for Package
Resolution).

### Product Sync

Product data is synced from SMELT's product listing endpoint. See
`docs/features/packages/package-model.md` (Domain Concepts: SMELT)
for the general SMELT description.

- **Endpoint suffix**: `v1/basic/products/`, resolved relative to
  `SMELT_API_URL`
- **Pagination query parameters**: immutable `page_size=100`; varying `page`
- **Response fields used**: `name`, `version`, `cpe`, `friendly_name`, `repos`
- **Sync behavior**:
  1. Fetch every page under the shared SMELT pagination contract before
     opening a database transaction.
  2. Validate the complete snapshot before publication:
     - `count` MUST be greater than zero.
     - Every Product MUST contain non-empty strings for `name`, `version`,
       `cpe`, and `friendly_name`, within the corresponding persisted column
       lengths.
     - `repos` MUST be a non-empty array of non-empty strings within the
       `ProductRepository.repo_name` column length.
     - Duplicate CPE rows, a repeated repository within one Product, or a
       duplicate `(CPE, repository)` association invalidates the snapshot. A
       repository shared by different Products is valid.
     - Source values are preserved exactly; Sentinel does not trim,
       case-normalize, or canonicalize CPE or repository identities.
     - Unknown fields and the ignored `id`, `end_of_life`, `changed`, and
       `details` fields do not affect validation or persistence. In
       particular, SMELT `end_of_life` never drives Sentinel lifecycle state.
     Any invalid row rejects the complete snapshot; rows are never skipped.
  3. Capture one UTC `snapshot_at` value for the complete snapshot.
  4. In the publication transaction, before modifying any catalog row,
     capture the set of Product CPEs belonging to the previously applied
     snapshot and the set of `ProductRepository` associations belonging to
     it. If no previous complete snapshot exists, use the empty set for
     both.
  5. Upsert Products by exact CPE and
     ProductRepository associations by `(product_id, repo_name)`. Assign the
     same `snapshot_at` to every observed row's `catalog_last_seen_at`.
     The Product upsert modifies only the SMELT-owned descriptive fields
     (`name`, `version`, `display_name`) and `catalog_last_seen_at`; it MUST
     NOT clear or overwrite AIMAAS-owned lifecycle or threshold fields.
  6. Compare the new Product CPE set with the captured previous set and
     retain whether at least one Product is newly current. A newly current
     Product is a CPE that was not present in the previous snapshot
     (including the first snapshot, where the previous set is empty).
  7. Commit once. Any database error rolls back the complete publication.
     Network I/O MUST NOT occur while the transaction is open.
  8. If the committed snapshot contains at least one newly current Product,
     enqueue Product catalog backfill after the commit. A snapshot that
     only re-observes already-current Products does not trigger backfill.

Products and associations not observed in the new snapshot are retained with
their previous `catalog_last_seen_at`. The applied snapshot is identified by
`MAX(Product.catalog_last_seen_at)`; a row belongs to that snapshot when its
`catalog_last_seen_at` equals that value. Snapshot identity does not depend on
`FetcherRun` finalization, which occurs in a separate transaction.

The Product sync persists every valid repository in `repos`; it does not
filter repository categories or infer inclusion from any SMELT lifecycle or
status field. A structurally valid snapshot is not rejected solely because
its Product or association count decreased relative to the prior snapshot.
No arbitrary count-regression threshold is used. Complete-response
validation, atomic publication, retained historical rows, and fetcher-run
observability provide the corruption safeguards.

Any transport, HTTP, pagination, response-schema, row-validation, or database
failure publishes nothing, dispatches no backfill, and leaves the last
committed snapshot unchanged. Recovery is the next scheduled or
operator-triggered complete run. The Product sync adds no in-run restart,
cursor, or durable recovery state beyond the shared HTTP transport retries and
fetcher infrastructure.

Failure to enqueue backfill is best-effort: log a warning and retain the
successfully committed catalog. It does not roll back publication and no
durable retry state is introduced. The omitted Products remain recoverable by
a later `add_package_to_ticket()` invocation or a future backfill trigger.

### Catalog Readiness and Freshness

The Product catalog is ready after the first non-empty complete SMELT snapshot
commits. Readiness is derived from the existence of
`MAX(Product.catalog_last_seen_at)`; it requires no separate persisted state.
AIMAAS lifecycle and threshold enrichment is independent and does not
participate in catalog readiness.

Package resolution requires catalog readiness. It checks readiness after
retrieving and validating the complete SMELT v2 maintained-package response
but before matching Product CPEs or modifying ticket data. This ordering
preserves the I/O-then-transaction boundary: the caller-supplied database
session performs no database operation before the external network I/O. If no
complete snapshot exists, resolution raises `ProductCatalogNotReadyError`; it
MUST NOT report `PackageNotFoundInSmeltError` or
`PackageTargetsUnresolvedError`. Readiness failure takes precedence over both
the zero-track and zero-resolved-Product outcomes because neither can be
interpreted against an initialized local catalog.

Catalog readiness is a domain-operation gate and is not part of the API
process `/ready` probe. A committed snapshot has no hard expiry. Failed later
synchronizations leave the latest complete snapshot usable; its timestamp and
failed fetcher runs provide freshness and operational visibility without a
second stale state or freshness setting. Operations that do not require
current Product CPE resolution are not blocked.

### Product Catalog Backfill

Product catalog backfill is an on-demand Celery sub-operation of
`sync_smelt_products`, not an independently scheduled `BaseFetcher`. It is
unrelated to the reserved `BaseFetcher.catch_up(ticket_id, session)` mechanism
used when an inactive ticket becomes active.

Backfill is triggered when the Product catalog snapshot introduces at least
one newly current Product (a CPE that was not present in the previous
snapshot). A newly current Product may now match CPEs returned by the SMELT
v2 maintained-package endpoint for packages already tracked by active tickets;
backfill re-runs resolution to pick up these previously unresolvable matches.

After dispatch, it:

1. Selects every distinct `(ticket_id, package_name)` whose Ticket is active
   (`New`, `Analysis`, or `Analyzed`) and whose `TicketPackage` has
   `deleted_at IS NULL`.
2. Processes each pair independently by calling `add_package_to_ticket()`
   in active-ticket-only mode, with `acting_user_id = None` and the system
   audit comment `Product catalog backfill`. The mutation boundary re-checks
   the Ticket status while holding its row lock; if the Ticket is no longer
   active, the pair is skipped without mutation or post-commit effects.
   Similarly, if the `TicketPackage` has been soft-deleted between batch
   selection and the lock acquisition, the pair is skipped — new tracks and
   products are not created beneath a soft-deleted parent.
3. Product resolution emits the structured partial-resolution warning defined
   in `package-model.md` when applicable. Because resolution precedes the
   Ticket lock, this warning may also be emitted for a pair subsequently
   skipped after the active-status re-check.
4. Commits each pair separately. A failure rolls back that pair, logs a
   warning, and continues with the next pair; successful earlier pairs are not
   rolled back.

The workflow is idempotent. Existing package-tree records are skipped, and a
completely no-op pair creates no `package_added` event. Existing tracks retain
their affectedness and delivery states. Backfill may add missing Products
beneath existing tracks and may create a previously omitted track; a new track
starts in `ANALYSIS`/`PENDING`, and normal status reconciliation may regress
an `Analyzed` Ticket to `Analysis`. The normal `add_package_to_ticket()`
post-commit effects apply only when at least one package-tree record is
created. A package-tree no-op performs no post-commit effects.

Backfill completes only package trees represented by an existing active
`TicketPackage`. A package addition that previously failed with
`PACKAGE_TARGETS_UNRESOLVED` created no such row and is not discoverable by
this workflow; it is retried only by a later manual or automatic caller. This
is an accepted limitation and no durable failed-addition registry is
introduced.

The system-wide scan is deliberate because Sentinel does not persist which
unmatched CPEs were omitted from earlier package resolutions. Backfill
processes pairs sequentially,
which bounds SMELT request concurrency without adding another configuration
surface, correlation table, or targeting index. The resulting sequential
full-system scan and record-creating post-commit task dispatches are accepted,
including after the first snapshot when every Product is newly current.
Overlapping backfill tasks are safe because package creation is idempotent and
serialized by the Ticket row lock; duplicate external work is accepted. The
task has no task-level retry after process loss. Recovery remains the next
qualifying backfill trigger or a later package-addition invocation.

---

## AIMAAS Integration

HTTPS connections to AIMAAS use the combined trust store (system CAs + SUSE
Trust Root CA). See `networking.md`, TLS Trust Store Configuration.

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

A Product without sufficient AIMAAS lifecycle data remains usable for package
resolution. Its derived `lifecycle_phase` is `NULL`, and no lifecycle
exclusion is applied. When later synchronization supplies sufficient data,
the lifecycle evaluator exposes the derived phase and
`evaluate_lifecycle_transitions` applies Reactive Support or EOL behavior
when applicable.

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
  A later change from `NULL` to an explicit threshold is a threshold change
  and triggers automatic eligibility re-evaluation for non-overridden
  `TicketPackageProduct` records.

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
| `lifecycle_phase` | string (repeatable) | -- | Filter by current lifecycle phase. Valid values: `pre_release`, `general_support`, `extended_support`, `reactive_support`, `eol`. Multiple values use OR semantics. Products whose phase is `NULL` do not match this filter. Invalid values follow `docs/api-spec.md` (Enum Filter Validation). |

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

`lifecycle_phase` is a nullable string. It is `NULL` when AIMAAS data is
absent or insufficient to derive a phase safely.

Whether this endpoint returns all retained Products or only Products in the
current catalog snapshot remains to be completed with the Product read service
contract.

---

## Background Tasks

### Fetcher: `sync_smelt_products`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_smelt_products` |
| Class name | TBD |
| Schedule | TBD |
| Source | SMELT (`smelt.suse.de/api`) |
| Scope | Complete SMELT Product catalog and repository projection on every run; no cursor or incremental mode |
| Auth | None |
| Custom settings | No |

#### Algorithm

The retrieval, validation, publication, readiness, and recovery algorithm is
defined in [SMELT Integration](#smelt-integration).

#### Error Handling

Shared transport retries apply to retryable HTTP failures. After those retries
are exhausted, any retrieval, non-success HTTP response, pagination,
response-schema, or snapshot-validation failure aborts the run without
publication or backfill. A publication failure rolls back the complete
snapshot. The last committed snapshot remains usable, and recovery is a later
scheduled or operator-triggered full run. Error messages and logs identify the
failed page or validation category without retaining full response payloads.

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
- SMELT uses anonymous HTTPS requests. AIMAAS authentication remains to be
  defined. Any future credentials are provided through secret configuration,
  never in code.

---

## Configuration

- `SMELT_API_URL`: SMELT HTTPS API prefix for product catalog sync and
  package resolution (default: `https://smelt.suse.de/api`). It must contain
  no user information, query, or fragment; a non-default port is permitted.
  Invalid values fail application startup with an error naming the setting.
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
- `docs/features/packages/package-service.md` -- package-tree creation used by
  Product catalog backfill
- `docs/features/packages/product-lifecycle-transitions.md` -- EOL and
  Reactive Support automated actions
- `docs/features/tickets/cvss-scoring.md` -- CVSS resolution cascade
  used for threshold comparison
- `docs/features/platform/system-settings.md` -- default CVSS version
  configuration
- `docs/features/platform/networking.md` -- HTTP client and TLS trust store
