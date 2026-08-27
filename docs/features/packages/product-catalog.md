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

1. **Product lifecycle data** (`GET /api/entity/products?all_fields=true`):
   dates for each lifecycle phase -- `fcs` (first customer shipment),
   `end_of_gs` (end of General Support), `end_of_ltss`, `end_of_espos`,
   and `end_of_reactive_ltss`. Sentinel uses the paginated list endpoint
   with `all_fields=true`; see [Product Lifecycle Sync](#product-lifecycle-sync-periodic).
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
platform behavior. The lifecycle evaluator below defines the exact phase,
boundary, missing-data, and inconsistent-data behavior. If it returns `NULL`,
the lifecycle phase is unavailable; `NULL` is not treated as General Support,
Reactive Support, or EOL and does not make package-tree Products
non-actionable.

#### Lifecycle Evaluator

`evaluate_product_lifecycle_phase()` is a pure function with the following
inputs:

| Parameter | Type | Description |
|---|---|---|
| `evaluation_date` | `date` | UTC calendar date for which to evaluate the Product |
| `first_customer_ship_date` | `date \| None` | AIMAAS `fcs` projection |
| `general_support_end_date` | `date \| None` | Inclusive end of General Support |
| `extended_support_end_date` | `date \| None` | Inclusive end of the collapsed LTSS/ESPOS phase |
| `reactive_support_end_date` | `date \| None` | Inclusive end of Reactive LTSS |

It returns `LifecyclePhase | None`, where `None` is exposed as `NULL` in API
responses. It performs no I/O, mutation, or logging and raises no domain
exception. Syntactically invalid upstream date values are response-schema
failures handled by `sync_aimaas_lifecycle`; they never reach this function.

All phase-end dates are inclusive, and a following phase begins on the next
calendar day. Equal adjacent boundaries are valid; the later phase then has
an empty interval. Date-time or timezone conversion is not involved because
all inputs are calendar dates and `evaluation_date` is derived from the
current UTC date.

Before selecting a phase, the evaluator validates the available dates:

- `extended_support_end_date` requires `general_support_end_date`;
- `reactive_support_end_date` requires `extended_support_end_date`;
- when both are present, `first_customer_ship_date` MUST be no later than
  `general_support_end_date`;
- when present, `general_support_end_date` MUST be no later than
  `extended_support_end_date`; and
- when present, `extended_support_end_date` MUST be no later than
  `reactive_support_end_date`.

Any violation makes the complete date set inconsistent. The evaluator returns
`None` for every `evaluation_date`; it never derives `eol` from an inconsistent
set.

For a consistent date set, evaluate in this order:

1. If `first_customer_ship_date` exists and `evaluation_date` is earlier,
   return `pre_release`.
2. If `general_support_end_date` exists and `evaluation_date` is no later,
   return `general_support`. A missing `first_customer_ship_date` does not
   prevent this result.
3. If `extended_support_end_date` exists and `evaluation_date` is after
   `general_support_end_date` but no later than
   `extended_support_end_date`, return `extended_support`.
4. If `reactive_support_end_date` exists and `evaluation_date` is after
   `extended_support_end_date` but no later than
   `reactive_support_end_date`, return `reactive_support`.
5. If `evaluation_date` is later than the last available end date in the
   valid continuous chain, return `eol`. Thus a General Support end date is
   sufficient to establish EOL on the following day when no later phase is
   present, even when `first_customer_ship_date` is missing.
6. Otherwise, return `None`. In particular, an FCS date without any support
   end date produces `pre_release` before FCS and `None` from FCS onward; all
   four dates absent also produces `None`.

The evaluator depends only on these inputs. SMELT catalog presence, ticket
state, eligibility, and any previously derived phase do not affect its result.

Every operation that evaluates lifecycle captures one UTC `evaluation_date`
for its full set of rows, filters, counts, and gate predicates. List requests
made on opposite sides of midnight may observe different derived phases; one
request never mixes dates. The Product query service provides a reusable SQL
expression equivalent to this pure evaluator so lifecycle filters and package
actionability remain database-filterable. The Python and SQL forms MUST agree
for every valid, incomplete, inconsistent, and boundary-date combination.
Neither lifecycle phase nor its EOL result is persisted as current state.

For automated reconciliation triggered by lifecycle phase changes (Reactive
Support eligibility and EOL-derived package-tree actionability), see
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

### Origin, Authentication, and Pagination

The configured `AIMAAS_API_URL` identifies the AIMAAS API prefix. Its default
value is `https://aimaas.suse.de/api`; endpoint paths such as
`entity/products` are appended to that prefix after removing any trailing
slash. The configured URL MUST use HTTPS and MUST NOT contain user
information, a query, or a fragment. A non-default port is permitted for an
explicitly configured deployment. At application startup, Sentinel validates
these constraints and refuses to start with an error naming `AIMAAS_API_URL`
when the value is invalid.

AIMAAS requests use no authentication. Sentinel sends no credentials. A
future upstream authentication requirement for GET requests is a contract
change and requires specification review before the integration changes. The
AIMAAS OpenAPI spec at `/api/openapi.json` indicates write operations may
require authentication; Sentinel uses only GET (read) operations.

HTTPS connections to AIMAAS use the combined trust store (system CAs + SUSE
Trust Root CA). See `networking.md`, TLS Trust Store Configuration.

AIMAAS endpoints use a pagination envelope distinct from SMELT:

| Field | AIMAAS | SMELT |
|-------|--------|-------|
| Item array | `items` | `results` |
| Total count | `total` | `count` |
| Total pages | `pages` | `total_pages` |
| Current page | `page` | (in `next`/`previous` metadata) |
| Page size | `size` | (implicit/requested) |
| Continuation URLs | None | `next`, `previous` |

Sentinel requests pages sequentially, starting at page 1, with explicit
`size=100`. AIMAAS enforces a maximum page size of 100; values above this
return a 422 validation error. Page numbering is 1-based; page 0 returns
422. Pages beyond `pages` return `{items: []}` with correct metadata (not
404).

Pagination validation invariants:
- `total` and `pages` must remain constant across all pages of a single
  retrieval.
- Non-final pages must return exactly `size` items.
- The final page must return `total - (pages - 1) * size` items.
- Total collected items must equal `total`.
- An inconsistency aborts the run without publication.

Live verification on 2026-08-26 confirmed this envelope on both the Products
and CVSS threshold endpoints, 1-based page numbering, the 100-item page-size
cap, and empty-array (not 404) behavior for pages beyond `pages`.

### Deleted Flag Semantics

AIMAAS exposes a `deleted` boolean on both Product and threshold entities. By
default, the list endpoints return only `deleted: false` records. The `all`
and `deleted_only` query parameters control inclusion of deleted records.

Sentinel uses the default behavior (no `all=true` or `deleted_only=true`),
which excludes deleted records. No explicit filter is needed.

When a previously synchronized product becomes `deleted: true` in AIMAAS (or
otherwise disappears from the default list): lifecycle dates on the local
`Product` are retained unchanged. Clearing to NULL would regress a product
from `eol` to `lifecycle_phase = NULL` (actionable unless manually excluded),
which is operationally worse than retaining accurate historical dates. AIMAAS
product deletions are
not expected in normal operation; the existing deleted products are from
initial test imports.

When a threshold entry disappears: `Product.cvss_threshold` is cleared per
the threshold-specific rule below.

### Product Lifecycle Sync (periodic)

- **Endpoint suffix**: `entity/products`, resolved relative to
  `AIMAAS_API_URL`
- **Query parameters**: `all_fields=true` (required), `size=100`, `page={n}`
- **Response fields consumed**: `cpe`, `fcs`, `end_of_gs`, `end_of_ltss`,
  `end_of_espos`, `end_of_reactive_ltss`
- **Response fields ignored**: `slug`, `name`, `id`, `deleted`, `version`,
  `tracked_in_bz`, `end_of_lts_core`
- **Matching**: AIMAAS products are matched to local `Product` records by
  exact CPE. The two catalogs have different coverage (414 overlap out of
  475 AIMAAS and 556 SMELT); unmatched Products are expected and MUST NOT
  be matched heuristically by name or version. A product without a local
  CPE match is silently ignored.

The `all_fields=true` parameter is required. Without it, the list endpoint
returns a reduced field set that omits `fcs`, `end_of_reactive_ltss`, and
`version`. With `all_fields=true`, the list response contains all fields
available from the detail endpoint, eliminating the need for per-product
detail requests.

- **Sync behavior**:
  1. Fetch all AIMAAS product pages with `all_fields=true` and `size=100`
     under the shared AIMAAS pagination contract before opening a database
     transaction.
  2. For each AIMAAS product, match by exact `cpe` against local
     `Product.cpe`. Skip products with no local match.
  3. Set `first_customer_ship_date` from `fcs` and
     `general_support_end_date` from `end_of_gs`.
  4. Set `extended_support_end_date` to the latest non-null value of
     `end_of_ltss` and `end_of_espos`, or NULL when both are NULL.
  5. Set `reactive_support_end_date` from `end_of_reactive_ltss`.
  6. When an AIMAAS field changes from a non-null date to null, the local
     field is updated to NULL. The lifecycle evaluator derives the phase
     from the current field values; a field becoming NULL may change the
     computed `lifecycle_phase`.

The fetcher persists every syntactically valid source date faithfully. A
structurally missing but non-contradictory date set is valid input and does not
make the Product row fail synchronization. For each matched local Product with
an inconsistent date set under the Lifecycle Evaluator rules, emit one
`product_lifecycle_dates_inconsistent` WARNING for each violated rule. Every
warning includes `product_cpe` and one of these stable `reason` values:

| `reason` | Violation |
|---|---|
| `missing_general_support_end_date` | Extended Support end exists without General Support end |
| `missing_extended_support_end_date` | Reactive Support end exists without Extended Support end |
| `first_customer_ship_after_general_support_end` | FCS is later than General Support end |
| `general_support_end_after_extended_support_end` | General Support end is later than Extended Support end |
| `extended_support_end_after_reactive_support_end` | Extended Support end is later than Reactive Support end |

The warning does not include the full upstream record. Inconsistency returns a
`NULL` lifecycle phase but does not reject the row, abort the complete run, or
increment `record_failed`; synchronization continues with the remaining
Products. A non-contradictory incomplete date set, or an AIMAAS Product with no
local CPE match, does not emit this warning.

The lifecycle synchronization modifies only the four lifecycle date columns;
it does not modify SMELT-owned descriptive or catalog-observation fields, or
`cvss_threshold`.

A Product whose lifecycle evaluator returns `NULL` remains usable for package
resolution and actionable unless manually excluded. The Reactive Support
eligibility rule also does not apply, while independent CVSS threshold
evaluation still does. When later synchronization changes the evaluator result
to a phase, `evaluate_lifecycle_transitions` reconciles Reactive Support
eligibility and EOL-derived actionability when applicable.

### CVSS Threshold Sync (periodic)

- **Endpoint suffix**: `entity/cvss-threshold`, resolved relative to
  `AIMAAS_API_URL`
- **Query parameters**: `size=100`, `page={n}`
- **Response fields consumed**: `product` (AIMAAS product ID integer),
  `threshold` (numeric)
- **Response fields ignored**: `slug`, `name`, `id`, `deleted`
- **CPE resolution**: each threshold entry has a `product` field containing
  an AIMAAS product ID (integer). CPE resolution uses an in-memory join:
  the fetcher first fetches the complete AIMAAS product list (with
  `all_fields=true` and `size=100`), then matches each threshold's
  `product` value against the product list's `id` field to obtain the
  corresponding `cpe`. This avoids per-threshold detail requests and
  produces an internally consistent snapshot.
- **Sync behavior**:
  1. Fetch all AIMAAS product pages (with `all_fields=true`) and all
     threshold pages before opening a database transaction. Build a
     `product_id → cpe` mapping from the product list.
  2. For each threshold entry, resolve its `product` ID to a CPE via
     the in-memory mapping. A threshold whose `product` ID has no match
     in the product list is skipped with a structured warning log.
  3. Match the resolved CPE against local `Product.cpe`. A threshold
     whose CPE has no local match is expected (different catalog
     coverage) and silently ignored.
  4. Update only the corresponding local `Product.cvss_threshold`; do not
     modify catalog-observation, descriptive, or lifecycle date fields.
  5. Retain the IDs of Products whose persisted threshold changes for
     post-commit eligibility recalculation.
  6. **Threshold clearing**: after processing all AIMAAS thresholds,
     identify local `Product` rows whose `cvss_threshold` is non-null but
     whose CPE is not present in the resolved AIMAAS threshold set. Clear
     those products' `cvss_threshold` to NULL (implicit threshold of 0,
     conservatively permissive). Each clearing is a threshold change and its
     Product ID is retained for post-commit eligibility recalculation.
  7. Commit the complete threshold publication before dispatching any task.
  8. In a new read-only phase after commit, identify Products whose
      system-managed `TicketPackageProduct` eligibility in an operable Ticket
      (`New`, `Analysis`, `Analyzed`, or `Resolved`)
      differs from the result under the committed threshold snapshot. This
     mismatch scan captures one UTC `evaluation_date` and uses it for every
     lifecycle-dependent eligibility comparison. The mismatch set recovers
     prior task-dispatch or per-Ticket failures.
  9. Enqueue the Product-level recalculation defined in
     `product-lifecycle-transitions.md` once per Product in the union of the
     changed and mismatch sets, with reason `threshold`. A dispatch failure
     logs a structured warning containing the Product ID and continues with
     other Products; it does not roll back the committed threshold snapshot.
     The next complete threshold run rediscovers any remaining mismatch and
     can be triggered through the existing fetcher-operations API. No durable
     dispatch state or dedicated recovery endpoint is added.
- **Note**: only ~24 products currently have a threshold entry. Products
  without an entry have an implicit threshold of 0 (all CVEs eligible).
  A later change from `NULL` to an explicit threshold is a threshold change
  and is included in the same post-commit automatic eligibility
  recalculation.

### SMELT Decoupling

Both AIMAAS fetchers match by CPE against `Product.cpe` with no dependency
on SMELT data, `ProductRepository`, or SMELT endpoints. The threshold CPE
resolution uses AIMAAS-internal product IDs resolved via the AIMAAS product
list. If Product discovery were later migrated away from SMELT, the AIMAAS
integration would require no changes.

---

## API Endpoints

### List Products

```
GET /api/v1/products
```

List all products synced from SMELT. Paginated.

The service captures one UTC `evaluation_date` for the request and uses the
canonical SQL lifecycle expression for both the result query and count query.
The endpoint remains database-filterable and does not load the complete
Product table for Python-side filtering.

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

`lifecycle_phase` is a nullable string. It is `NULL` when the Lifecycle
Evaluator returns `None`, including when AIMAAS data is absent or the date set
is inconsistent or does not establish a current phase or EOL boundary.

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
| Schedule | Daily at 01:00 UTC (`0 1 * * *`) |
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
| Class name | `SyncAimaasLifecycle` |
| Schedule | Daily at 02:15 UTC (`15 2 * * *`) |
| Source | AIMAAS (`aimaas.suse.de/api`) |
| Scope | Complete AIMAAS Product list with `all_fields=true` on every run; no cursor or incremental mode |
| Auth | None |
| Custom settings | No |

#### Algorithm

The retrieval, field projection, matching, and clearing behavior is
defined in [AIMAAS Integration](#aimaas-integration): pagination contract
in [Origin, Authentication, and Pagination](#origin-authentication-and-pagination-1),
deleted-flag handling in [Deleted Flag Semantics](#deleted-flag-semantics),
and sync logic in [Product Lifecycle Sync (periodic)](#product-lifecycle-sync-periodic).

#### Error Handling

Shared transport retries apply to retryable HTTP failures. After those
retries are exhausted, any retrieval, non-success HTTP response,
pagination, or response-schema failure aborts the run without modifying
local lifecycle data. Recovery is the next scheduled or
operator-triggered run. Error messages and logs identify the failed page
or validation category without retaining full response payloads.

#### Metrics

Lifecycle-date inconsistency warnings do not increment `record_failed`.
The remaining created, updated, and failed metric semantics are TBD.

### Fetcher: `sync_aimaas_thresholds`

| Property | Value |
|----------|-------|
| Fetcher name | `sync_aimaas_thresholds` |
| Class name | `SyncAimaasThresholds` |
| Schedule | Daily at 02:45 UTC (`45 2 * * *`) |
| Source | AIMAAS (`aimaas.suse.de/api`) |
| Scope | Complete AIMAAS Product list (for CPE resolution) and complete threshold list on every run; in-memory join; no cursor or incremental mode |
| Auth | None |
| Custom settings | No |

#### Algorithm

The retrieval, CPE resolution, matching, clearing, and re-evaluation
behavior is defined in [AIMAAS Integration](#aimaas-integration):
pagination contract in [Origin, Authentication, and Pagination](#origin-authentication-and-pagination-1),
deleted-flag handling in [Deleted Flag Semantics](#deleted-flag-semantics),
and sync logic in [CVSS Threshold Sync (periodic)](#cvss-threshold-sync-periodic).

#### Error Handling

Shared transport retries apply to retryable HTTP failures. After those
retries are exhausted, any retrieval, non-success HTTP response,
pagination, or response-schema failure aborts the run without modifying
local thresholds. This includes failures during the product-list
retrieval phase (required for CPE resolution). Recovery is the next
scheduled or operator-triggered run.

#### Metrics

TBD

---

## Security

- Listing Products is public with optional authentication. Selected invalid
  credentials are rejected according to `docs/api-spec.md`.
- SMELT and AIMAAS base URLs are configured via environment variables
  (`SMELT_API_URL`, `AIMAAS_API_URL`). See [Configuration](#configuration)
- Both SMELT and AIMAAS use anonymous HTTPS requests. Neither service
  currently requires authentication for read operations. A future upstream
  authentication requirement is a contract change requiring specification
  review. Any future credentials are provided through secret configuration,
  never in code.

---

## Configuration

- `SMELT_API_URL`: SMELT HTTPS API prefix for product catalog sync and
  package resolution (default: `https://smelt.suse.de/api`). It must contain
  no user information, query, or fragment; a non-default port is permitted.
  Invalid values fail application startup with an error naming the setting.
- `AIMAAS_API_URL`: AIMAAS HTTPS API prefix for product lifecycle and CVSS
  threshold sync (default: `https://aimaas.suse.de/api`). It must contain
  no user information, query, or fragment; a non-default port is permitted.
  Invalid values fail application startup with an error naming the setting.

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
- `docs/features/packages/product-lifecycle-transitions.md` -- Reactive
  Support eligibility and EOL-derived actionability reconciliation
- `docs/features/tickets/cvss-scoring.md` -- CVSS resolution cascade
  used for threshold comparison
- `docs/features/platform/system-settings.md` -- default CVSS version
  configuration
- `docs/features/platform/networking.md` -- HTTP client and TLS trust store
