# Package Tracking

## Purpose

Track the affectedness of source packages across maintenance tracks and
SUSE products in the context of tickets. See
`docs/features/tickets/tickets.md` for the ticket specification
(identification, creation, lifecycle).

## Design Rationale

The package tracking model separates three orthogonal concepts into
independent dimensions:

1. **Affectedness** — is the source code vulnerable?
2. **Eligibility** — will this product receive the fix?
3. **Delivery** — has the fix been distributed?

Each dimension has its own status values, update rules, and override
mechanisms. This separation keeps business logic simple: status
propagation never needs to consider eligibility, eligibility never
changes the status label, and delivery tracking is fully independent
from both.

The entity hierarchy (Ticket → Package → Track → Product) uses a
workflow-agnostic abstraction ("track") that covers both IBS codestreams
and git branches, allowing the same business logic to operate
transparently across both workflows.

## Design Decisions

### 1. Explicit TicketPackage entity

A `TicketPackage` table anchors a source package within a ticket,
providing:

- A clear anchor for package-level metadata (bugowner join, future notes)
- A single grouping point for tracks of both workflows
- Cleaner API design (`/tickets/{id}/packages/{id}/tracks`)

### 2. Workflow-agnostic "track" abstraction

The intermediate level is called "track" — a neutral term for "a
maintenance track for a package that serves one or more products." A
single `TicketPackageTrack` table with a `workflow_type` enum (`ibs` |
`git`) discriminates between IBS codestreams and git branches. This was
chosen over separate tables because the differences are minimal at the
data level and all business logic (status propagation, eligibility,
gates) is identical.

A single `reference` VARCHAR field identifies the track in its external
system (IBS codestream project name or git branch name). Both are
human-readable. For git, the full repository URL is derivable from
`package_name` (convention: `src.suse.de/pool/{package_name}`).

SMELT will serve both IBS and git tracks but will not provide an explicit
workflow type indicator. Sentinel infers `workflow_type` at ingestion time
(e.g., IBS codestreams match `^(SUSE|openSUSE):.*`). Both workflows can
coexist under the same package.

### 3. Eligibility as a separate dimension

Product eligibility (whether a product will receive the fix) is a
separate persisted boolean (`eligible`) on `TicketPackageProduct`, with
its own override mechanism (`is_eligible_override`). This keeps status
propagation unidirectional (track → product) with no rollup: the track
stays `AFFECTED` regardless of whether any product is eligible. CVSS
score changes only flip the `eligible` flag — no status changes, no
rollup cascades.

### 4. Delivery as a separate dimension

Delivery progress is tracked independently from affectedness:

- **Delivery status** (`delivery_status` on `TicketPackageTrack`):
  tracks the fix through the maintenance pipeline (PENDING →
  IN_PROGRESS → RELEASED), derived from SR/RR tracking data
- **Product release confirmation** (`released_at` on
  `TicketPackageProduct`): confirms the fix appeared in the product's
  update repository via `updateinfo.xml` verification

The `delivery_status` is persisted as a column (not computed from SR/RR
joins) because the ticket resolution gate queries it frequently and
anomaly detection benefits from having both axes on the same record.
Disalignment risk is mitigated by `ticket_mutations` and the
`RequestSyncFetcher` reconciliation phase (see
[Delivery Reconciliation](#delivery-reconciliation)).

### 5. FIXED as a distinct affectedness state

`FIXED` distinguishes "was vulnerable, now remediated" from "was never
vulnerable" (`NOT_AFFECTED`). Both mean the code is not currently
vulnerable, but they carry different history and workload implications.

- The VA can set `FIXED` manually via the dropdown
- The system sets `FIXED` automatically when delivery reaches `RELEASED`
  (one-shot event, not continuous reconciliation)
- The VA can change `FIXED` back to `AFFECTED` if the fix is insufficient
- No `is_status_override` flag is needed on tracks — the VA has direct
  control via the dropdown

### 6. Affectedness and delivery are independent axes

Neither axis resets nor constrains the other. "Anomalous" combinations
(e.g., `AFFECTED` + `RELEASED`) are valid system states that signal
situations requiring VA attention. See
[Anomaly Detection](#anomaly-detection-future-review-queue).

### 7. Soft-deletion instead of IGNORED status

Spurious tracks or products are handled by soft-deletion rather than a
special `IGNORED` status. A record that should not exist is removed, not
marked with a status value. See [Soft-Deletion](#soft-deletion).

### 8. Soft-deleted records continue to receive updates

Soft-deleted records are excluded from normal views, gates, and anomaly
detection, but they **continue to receive updates** from propagation,
delivery tracking, eligibility recalculation, and release detection.
Their state is always current, eliminating the need for reconciliation
logic at restore time. Exclusion is determined hierarchically — see
[Hierarchical Exclusion Model](#hierarchical-exclusion-model).

---

## Domain Concepts

### IBS (Internal Build Service)

The internal OBS instance at build.suse.de used for all SUSE commercial
products. Packages are built and maintained here.

### Codestream

An IBS project where source packages live and are built. Each codestream
follows the naming pattern `SUSE:SLE-<version>:GA` (development phase) or
`SUSE:SLE-<version>:Update` (maintenance phase after GA freeze).

- **GA codestream**: receives packages during development of a Service
  Pack. Once the SP is finalized, this codestream is frozen.
- **Update codestream**: receives all maintenance updates after GA
  freeze. This is where security fixes land.

A source package may exist in multiple codestreams. If a newer SP inherits
a package from an older SP without changes, the newer codestream contains
an IBS link to the older codestream's package — updates to the source
codestream automatically propagate to the linked codestreams.

### Git Track (SLFO)

A branch in a git repository on `src.suse.de` (e.g., `slfo-main`,
`slfo-1.2`) that serves the same role as an IBS codestream: it represents
a maintained version of a package, serving one or more products. The
repository URL follows the convention `src.suse.de/pool/{package_name}`.

### Track (Generic)

A maintenance track for a package — the workflow-agnostic abstraction
that covers both IBS codestreams and git branches. In Sentinel's data
model this is `TicketPackageTrack`. All business logic (status
propagation, eligibility, gates) operates on tracks regardless of
`workflow_type`.

### Product

A SUSE product with its own repositories from which end users receive
updates via the package manager. Each variant (base, LTSS, ESPOS, SAP)
is a separate product with its own CPE identifier. See
`docs/features/packages/product-catalog.md` for the full product
definition, lifecycle phases, and AIMAAS integration.

### Channel File

An XML file in the IBS project `SUSE:Channels` that defines which
packages from which codestreams are shipped to which products. There is
one channel file per product. Sentinel does not parse channel files
directly — it relies on SMELT to resolve these mappings.

### SMELT

An internal SUSE aggregator service (REST API at `smelt.suse.de/api`)
that provides:

1. **Product listing** (`GET /api/v1/basic/products/`): paginated list of
   all SUSE products with name, version, CPE, and repository project
   names. See `docs/features/packages/product-catalog.md` (SMELT
   Integration) for the product sync specification.
2. **Per-package maintenance info**
   (`GET /api/v1/basic/maintainedpackage/`): given a source package name,
   returns the list of tracks where the package is maintained and the
   target repositories (which map to products).

SMELT reads from IBS, channel files, and other sources internally.

### AIMAAS

See `docs/features/packages/product-catalog.md` (Domain Concepts:
AIMAAS) for the full description of the AIMAAS service and its
endpoints. AIMAAS provides product lifecycle data and CVSS thresholds
used by the eligibility rules in this spec.

---

## Data Model

See `docs/data-model.md` for the full schema. The tables defined by this
feature are:

### Product / ProductRepository

See `docs/features/packages/product-catalog.md` (Data Model) for the
Product and ProductRepository tables. These are owned by the product
catalog feature and consumed here for track-to-product mapping and
eligibility evaluation.

### TicketPackage

An explicit entity that anchors a source package within a ticket. Replaces
the implicit grouping by `package_name` across
`TicketPackageTrack` records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Internal identifier |
| `ticket_id` | UUID | FK(ticket.id), NOT NULL | Related ticket |
| `package_name` | VARCHAR | NOT NULL | Source package name |
| `deleted_at` | TIMESTAMP | nullable | Soft-deletion timestamp. NULL = active |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT | Record creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT | Record update timestamp |

**Unique constraint**: `(ticket_id, package_name)`

### TicketPackageTrack

Records the affectedness and delivery status of a source package in a
specific maintenance track within the context of a ticket. The VA sets
the affectedness status at this level. The delivery status is maintained
by the system based on IBS SR/RR tracking data.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Internal identifier |
| `ticket_package_id` | UUID | FK(ticket_package.id), NOT NULL | Parent package record |
| `workflow_type` | ENUM | NOT NULL | `ibs` or `git` |
| `reference` | VARCHAR | NOT NULL | Track identifier: IBS codestream name or git branch name |
| `status` | PackageStatus | NOT NULL, DEFAULT ANALYSIS | Affectedness status |
| `delivery_status` | DeliveryStatus | NOT NULL, DEFAULT PENDING | Delivery pipeline status |
| `deleted_at` | TIMESTAMP | nullable | Soft-deletion timestamp. NULL = active |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT | Record creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT | Record update timestamp |

**Unique constraint**: `(ticket_package_id, reference)`

The track is identified by `reference` (a string), not by a foreign key.
Tracks are not maintained as a separate table — they are discovered
per-package via the SMELT `maintainedpackage` endpoint.

### TicketPackageProduct

Records the affectedness status, eligibility, and release confirmation
of a source package for a specific product, within the context of a
ticket and track. Status is inherited from the parent TicketPackageTrack
and adjusted for eligibility, but both dimensions can be overridden by
the VA.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Internal identifier |
| `ticket_package_track_id` | UUID | FK(ticket_package_track.id), NOT NULL | Parent track record |
| `product_id` | UUID | FK(product.id), NOT NULL | Related product |
| `status` | PackageStatus | NOT NULL, DEFAULT ANALYSIS | Effective affectedness status |
| `is_status_override` | BOOLEAN | NOT NULL, DEFAULT false | True if VA has manually set the status |
| `eligible` | BOOLEAN | NOT NULL | Effective eligibility |
| `is_eligible_override` | BOOLEAN | NOT NULL, DEFAULT false | True if VA has manually set the eligibility |
| `released_at` | TIMESTAMP | nullable | When Sentinel detected the fix in the product's update repository |
| `deleted_at` | TIMESTAMP | nullable | Soft-deletion timestamp. NULL = active |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT | Record creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT | Record update timestamp |

**Unique constraint**: `(ticket_package_track_id, product_id)`

### Enums

See `docs/data-model.md` for the full definitions of `PackageStatus`,
`DeliveryStatus`, and `WorkflowType` enums (values, UI labels, colors).
The semantic meaning of each value in the context of package tracking is
described in [Three Orthogonal Dimensions](#three-orthogonal-dimensions)
below.

---

## Three Orthogonal Dimensions

The package tracking model separates three independent dimensions:

### Axis 1: Affectedness (per track, inherited by products)

Property of the source code. Determined by the VA during analysis.

| State | Meaning |
|-------|---------|
| `ANALYSIS` | Not yet determined |
| `AFFECTED` | Code is vulnerable, fix needed |
| `NOT_AFFECTED` | Code was never vulnerable to this CVE |
| `FIXED` | Code was vulnerable, fix has been applied |
| `WONT_FIX` | Code is vulnerable, decision not to fix |

The VA sets affectedness at the **track level** via a dropdown. Products
inherit the track's status unless the VA overrides a specific product
(`is_status_override = true`).

### Axis 2: Eligibility (per product only)

Whether the product will receive the fix. Calculated automatically by
Sentinel based on CVSS threshold and product lifecycle phase.

| Eligible | Meaning |
|----------|---------|
| `true` | Product will receive the update |
| `false` | Product will not receive the update (CVSS threshold or Reactive LTSS) |

Eligibility is evaluated **only when a product's status is or becomes
`AFFECTED`**. It is never applied when the status is `ANALYSIS` or any
other value — eligibility is meaningful only in the context of an
affected product.

**Eligibility rules** (evaluated in order):

1. **Reactive LTSS override**: if the product is currently in the
   Reactive LTSS phase (`end_of_ltss < today < end_of_reactive_ltss`),
   `eligible = false` regardless of CVSS score.
2. **Check CVSS threshold**: look up the product's `cvss_threshold` from
   AIMAAS. If no entry exists, the threshold is implicitly 0 (all CVEs
   eligible).
3. **Resolve the CVSS score**: via the CVSS resolution cascade (see
   `docs/features/tickets/cvss-scoring.md`):
   - SUSE assessment of the system-wide default CVSS version → if
     present, use this score
   - Highest score among all providers for the default CVSS version → if
     at least one exists, use the highest
   - No score available → treat as **10.0** (worst-case; the product is
     always eligible — a CVE without CVSS data is never excluded)
4. **Apply threshold**: if the resolved CVSS score is below the product's
   threshold, `eligible = false`. Otherwise, `eligible = true`.

**Important**: the CVSS version used for threshold comparison MUST always
be resolved from the system-wide default CVSS version configuration —
never hardcoded. See `docs/features/tickets/cvss-scoring.md` and
`docs/features/platform/admin.md`.

**Override model**: the VA can override eligibility on individual products
by setting `is_eligible_override = true`. When overridden, automatic
eligibility recalculation skips the product.

### Axis 3: Delivery (per track)

Progress of the fix through the SUSE maintenance pipeline. Derived from
SR/incident/RR state in IBS and persisted as a column on
`TicketPackageTrack`.

| State | Meaning | Condition |
|-------|---------|-----------|
| `PENDING` | No delivery action yet | No SR created |
| `IN_PROGRESS` | Fix in the pipeline | SR created, until RR accepted |
| `RELEASED` | Fix delivered to customers | RR accepted |

The delivery status is updated by the system when SR/RR state changes
are detected (via IBS RabbitMQ events or the `RequestSyncFetcher`
catch-up).

The delivery badge is visible in the UI for all tracks regardless of
affectedness status. The two axes are independent — see
[Affectedness-Delivery Independence](#affectedness-delivery-independence).

#### SUSE Maintenance Workflow Mapping

The delivery pipeline maps to the SUSE maintenance process as follows:

```
VA sets track to AFFECTED
        |
        v
Maintainer prepares fix
        |
        v
SR (Submission Request) created          --> delivery: IN_PROGRESS
        |
        v
Incident (SUSE:Maintenance:XXXXX)
    - builds package
    - runs QA tests
    - specifies eligible products
        |
        v
RR (Release Request) created
        |
        v
RR accepted (incident closed)            --> delivery: RELEASED
        |                                     affectedness: FIXED (automatic)
        v
Fix lands in track AND eligible products (nearly simultaneously)
```

When the system detects that delivery has reached `RELEASED`, it
automatically sets the track's affectedness to `FIXED` (one-shot
event). See Design Decision 10.

#### Product Release Confirmation

Product-level release is confirmed independently via `updateinfo.xml`:

- The `ProductReleaseDetector` downloads `updateinfo.xml` from each
  product's update repository
- Looks for advisories referencing the ticket's CVE
- Uses package match cascade (title pattern, heuristic prefix,
  `primary.xml`) to confirm the specific source package
- Sets `released_at` on the `TicketPackageProduct` from the advisory's
  `<issued date>`

Codestream/track delivery tracking and product release confirmation are
independent and complementary mechanisms. Track delivery tracks the
maintenance process. Product confirmation verifies the end result.

#### Delivery Reconciliation

The `RequestSyncFetcher` (daily at 02:30 UTC) includes a reconciliation
phase after its primary catch-up of missed SR/RR events. For every IBS
track (`workflow_type = 'ibs'`) in open tickets with
`delivery_status != RELEASED`, it verifies that the persisted
`delivery_status` is consistent with the current state of the SR/RR data
in IBS. If a disalignment is found, the `delivery_status` is corrected.

This reconciliation applies only to IBS tracks. Git tracks will have
their own delivery detection and reconciliation mechanism (TBD).

---

## Affectedness-Delivery Independence

The affectedness status and the delivery status are tracked as
independent axes. Neither resets nor constrains the other. All
combinations are valid system states:

| Affectedness | Delivery | Anomaly | Meaning |
|-------------|----------|---------|---------|
| `ANALYSIS` | `PENDING` | | Not yet analyzed, no SR |
| `ANALYSIS` | `IN_PROGRESS` | | SR exists before VA analyzed the track |
| `ANALYSIS` | `RELEASED` | | Fix released before VA analyzed the track |
| `AFFECTED` | `PENDING` | | Affected, no SR yet |
| `AFFECTED` | `IN_PROGRESS` | | Fix in the pipeline |
| `AFFECTED` | `RELEASED` | Yes | Fix released but VA considers it insufficient — needs review |
| `NOT_AFFECTED` | `PENDING` | | Not affected, nothing to deliver |
| `NOT_AFFECTED` | `IN_PROGRESS` | Yes | SR in progress for unaffected code — possible confusion |
| `NOT_AFFECTED` | `RELEASED` | Yes | Fix released for unaffected code — possible confusion |
| `FIXED` | `PENDING` | | VA set FIXED manually, no SR detected yet |
| `FIXED` | `IN_PROGRESS` | | Fix confirmed, SR still in pipeline |
| `FIXED` | `RELEASED` | | Fix confirmed and delivered |
| `WONT_FIX` | `PENDING` | | Decided not to fix, nothing in pipeline |
| `WONT_FIX` | `IN_PROGRESS` | Yes | SR in progress despite won't-fix decision — conflicting |
| `WONT_FIX` | `RELEASED` | Yes | Fix released despite won't-fix decision — conflicting |

### Anomaly Detection (future: Review Queue)

Anomalous combinations (marked in the table above) indicate situations
that require VA attention — a possible bug, a maintainer not following
the workflow, or an outdated VA assessment. These combinations are
destined to be integrated into the future **Review Queue** — a mechanism
that will automatically tag tickets presenting anomalies, making them
visible to VAs for review. The specification of the Review Queue and the
tagging mechanism will be defined in a dedicated specification.

---

## Status Behavior

All track and product status changes described in this section MUST go
through the `ticket_mutations` module (see
`docs/features/tickets/tickets.md`, Ticket Mutations Module), which
ensures automatic ticket status re-evaluation after each change.

### VA Sets "Affected" on a Track

1. Track status is set to `AFFECTED` (via `ticket_mutations`)
2. Sentinel propagates to all active (not effectively excluded) products
   under that track:
   - Products with `is_status_override = true` are not modified
   - For remaining products: status is set to `AFFECTED`
3. Eligibility is calculated separately for each product (see
   [Axis 2: Eligibility](#axis-2-eligibility-per-product-only)):
   - Products with `is_eligible_override = true` are not modified
   - For remaining products: `eligible` is recalculated based on CVSS
     threshold and lifecycle phase

There is **no codestream eligibility rollup** — the track stays
`AFFECTED` regardless of whether any product is eligible. The question
"is there work to do on this track?" is answered by checking whether
any active product under it has `eligible = true`.

### VA Sets Any Other Status on a Track

1. Track status is set to the chosen value
2. Sentinel propagates the same status to all active products under
   that track
3. Products with `is_status_override = true` are not modified

### VA Overrides a Product Status

1. Product status is set to the chosen value
2. `is_status_override` is set to `true`
3. If the chosen value is `AFFECTED`, eligibility is recalculated
   (unless `is_eligible_override = true`)
4. The track status is not affected

### VA Overrides Product Eligibility

1. Product `eligible` is set to the chosen value
2. `is_eligible_override` is set to `true`
3. The product status is not affected
4. The track status is not affected

### Automatic Transitions

| From | To | Applies to | Trigger |
|------|----|------------|---------|
| `AFFECTED` or `ANALYSIS` | `FIXED` | TicketPackageTrack | Release detected (delivery reaches `RELEASED`, one-shot) |
| any non-protected | inherited from track | TicketPackageProduct | Track status changed by VA (propagation) |

**Protected state**: `WONT_FIX` is never modified by automatic
transitions. If a track or product has status `WONT_FIX`, no automatic
status change is applied.

**Delivery status transitions** (system-managed):

| From | To | Trigger |
|------|----|---------|
| `PENDING` | `IN_PROGRESS` | SR created for the track |
| `IN_PROGRESS` | `RELEASED` | RR accepted for the track |

These transitions are detected via IBS RabbitMQ events
(`suse.obs.request.create`, `suse.obs.request.state_change`) and the
`RequestSyncFetcher` catch-up mechanism. See
`docs/features/packages/ibs-submission-tracking.md`.

When `delivery_status` transitions to `RELEASED`:
- The system automatically sets the track's `status` to `FIXED`
  (one-shot, see Design Decision 10)
- This triggers normal propagation to products

### Manual Transitions

The VA can manually change the affectedness status of any track to any
value without restriction via the dropdown. The VA cannot manually
change the delivery status — it is system-managed.

---

## Soft-Deletion

### Mechanism

The VA can soft-delete packages, tracks, or products to exclude them
from the ticket. Soft-deletion is indicated by a non-null `deleted_at`
timestamp on the record:

- `deleted_at IS NOT NULL` → record is soft-deleted (excluded)
- `deleted_at IS NULL` → record is active

The identity of the user who performed the deletion is recorded in the
corresponding `TicketAuditEvent` (`user_id` field), not on the record itself.

### Hierarchical Exclusion Model

Soft-deletion follows a **hierarchical** model: `deleted_at` is set
**only** on the record where the VA (or the system) acts. Child records
are NOT modified — they are implicitly excluded through the hierarchy.

- **Package soft-deleted** → only the `TicketPackage` record receives
  `deleted_at`. All tracks and products under it are **effectively
  excluded** via the parent, but their own `deleted_at` remains `NULL`.
- **Track soft-deleted** → only the `TicketPackageTrack` record receives
  `deleted_at`. All products under it are effectively excluded via the
  parent track.
- **Product soft-deleted** → only the `TicketPackageProduct` record
  receives `deleted_at`.

When a soft-deletion leaves a parent record with no remaining children
that have `deleted_at IS NULL`, the orphan cleanup invariants (defined in
`docs/features/tickets/tickets.md`, Ticket Mutations Module, "Orphan
Cleanup Invariants") apply upward: the parent is also soft-deleted. See
also `docs/features/packages/product-lifecycle-transitions.md` for the
EOL-triggered cascade.

#### Effectively Excluded

A record is **effectively excluded** if any of the following is true:

- Its own `deleted_at IS NOT NULL` (directly excluded), OR
- Its parent's `deleted_at IS NOT NULL`, OR
- Its grandparent's `deleted_at IS NOT NULL`

Concretely:

| Record type | Effectively excluded when |
|-------------|--------------------------|
| Package | `package.deleted_at IS NOT NULL` |
| Track | `track.deleted_at IS NOT NULL` OR `package.deleted_at IS NOT NULL` |
| Product | `product.deleted_at IS NOT NULL` OR `track.deleted_at IS NOT NULL` OR `package.deleted_at IS NOT NULL` |

All system operations that need to determine whether a record is excluded
(gates, UI filtering, anomaly detection) MUST use the hierarchical check,
not just the record's own `deleted_at`.

### Continued Updates

Soft-deleted records **continue to receive updates** from all automated
processes:

- Status propagation (track → product)
- Eligibility recalculation (CVSS/threshold/lifecycle changes)
- Delivery status updates (SR/RR state changes)
- Release detection (codestream and product level)

This means the state of a soft-deleted record always reflects the
current reality, not the state at the time of deletion.

### Exclusion from System Operations

Soft-deleted records are excluded from:

- **UI normal view** — not shown in the ticket's package tree
- **Ticket resolution gate** — not considered when evaluating whether
  a ticket can transition to Resolved
- **Anomaly detection** — not flagged in the future Review Queue
- **Analysis gate** — not considered when evaluating Analysis → Analyzed

### UI for Soft-Deleted Records

When a ticket contains soft-deleted records, the UI shows a discrete
indicator (e.g., "3 excluded items"). Clicking the indicator opens a
panel that displays:

- Each excluded item with its **current state** (not the state at
  deletion time)
- When it was excluded (from `deleted_at`)
- A "Restore" button for each item

### Restore

Restore operates **only on the directly excluded record** — there is no
cascade to child records. The VA can only restore a record that has its
own `deleted_at IS NOT NULL`.

When the VA restores a soft-deleted record:

1. **Pre-check (tracks and packages only)**: verify that the record will
   have at least one active child after restoration:
   - Restoring a **track**: at least 1 product under it must have
     `deleted_at IS NULL` (directly). If all products are directly
     excluded, return error `PACKAGE_RESTORE_BLOCKED`.
   - Restoring a **package**: at least 1 track under it must have
     `deleted_at IS NULL` (directly), and that track must have at least
     1 product with `deleted_at IS NULL` (directly). If no such
     track-product chain exists, return error `PACKAGE_RESTORE_BLOCKED`.
   - Restoring a **product**: no pre-check needed (products have no
     children).
2. `deleted_at` is set to `NULL` on the record.
3. The record's state is already current (no recalculation needed —
   soft-deleted records continue to receive updates).
4. A single `TicketAuditEvent` is created for the restored record.

**Restore is permitted even when an ancestor is excluded** (per design
decision D2). Clearing a product's `deleted_at` while its parent track
is excluded means the product is no longer directly excluded, but remains
effectively excluded via the track. When the track is later restored, the
product becomes fully active.

### Interaction with add_package_to_ticket

The `add_package_to_ticket` function proceeds normally regardless of
whether the `TicketPackage` is soft-deleted. It queries SMELT, and
creates any missing `TicketPackageTrack` and `TicketPackageProduct`
records. Existing records (active or soft-deleted) are skipped.

New records are created with `deleted_at = NULL`. If the parent package
or track is soft-deleted, these new records are **effectively excluded**
via the hierarchy — no special logic is needed.

The **API handler** for `POST /api/v1/tickets/{ticket_id}/packages` is
responsible for checking whether the `TicketPackage` is soft-deleted
(`deleted_at IS NOT NULL`) **before** calling the function. If it is,
the handler returns `409 PACKAGE_ALREADY_EXCLUDED` without invoking the
function. Internal callers (CPE mapping, release detection) call the
function directly and benefit from the automatic exclusion via hierarchy.

### Ticket Events for Soft-Deletion

A single `TicketAuditEvent` is created for each soft-deletion or restore
operation — only for the **directly affected record**. Child records
that become effectively excluded via the hierarchy do not generate
separate events.

When a VA soft-deletes a track, 1 event is created (`track_excluded`).
Products under the track are implicitly excluded via the hierarchy but
do not produce individual events.

| Action | `event_type` | `user_id` | Details recorded |
|--------|-------------|-----------|------------------|
| VA soft-deletes a package | `package_excluded` | VA user | `package_name` |
| VA soft-deletes a track | `track_excluded` | VA user | `package_name`, `reference` |
| VA soft-deletes a product | `product_excluded` | VA user | `package_name`, `reference`, `product_id` |
| VA restores a package | `package_restored` | VA user | `package_name` |
| VA restores a track | `track_restored` | VA user | `package_name`, `reference` |
| VA restores a product | `product_restored` | VA user | `package_name`, `reference`, `product_id` |

---

## Package Eligibility

Eligibility determines whether a product will receive a security update
for a given CVE. Eligibility is evaluated **only when a product's status
is or becomes `AFFECTED`** — either through VA-initiated track
propagation, status inheritance during package addition, or
CVSS/threshold/lifecycle recalculation. It is never applied when the
status is `ANALYSIS` or any other value.

The rules are described in
[Axis 2: Eligibility](#axis-2-eligibility-per-product-only).

### Override Model

Product-level overrides follow a symmetric pattern for both dimensions:

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `status` | PackageStatus | inherited | Effective affectedness status |
| `is_status_override` | bool | false | VA has manually set the status |
| `eligible` | bool | calculated | Effective eligibility |
| `is_eligible_override` | bool | false | VA has manually set the eligibility |

When `is_*_override = false`, the system maintains the value
automatically (status via propagation from parent track, eligibility via
CVSS threshold + lifecycle phase calculation). When
`is_*_override = true`, automatic updates skip the field.

Both dimensions go through `ticket_mutations` and trigger
`evaluate_ticket_status`.

---

## Adding Packages to a Ticket

### Centralized Function: `add_package_to_ticket`

All package additions — regardless of the trigger — MUST go through a
single centralized service function. This function is the only place
where SMELT is queried to resolve tracks and products, and where
`TicketPackage`, `TicketPackageTrack`, and `TicketPackageProduct`
records are created.

**Signature** (conceptual):

```python
add_package_to_ticket(ticket_id, package_name) -> AddPackageResult
```

**Behavior**:

1. Create a `TicketPackage` record for the package if one does not
   already exist. If a record already exists (active or soft-deleted),
   skip creation and proceed to step 2.
2. Query SMELT to resolve all currently maintained tracks and products
   for the given package (see
   [SMELT Query](#smelt-query-for-package-resolution) below).
3. Infer `workflow_type` for each resolved track (see Design
   Decision 5).
4. For each resolved track, delegate `TicketPackageTrack` record
   creation to `ticket_mutations` (if a record does not already exist,
   including soft-deleted).
5. For each resolved product under each track, delegate
   `TicketPackageProduct` record creation to `ticket_mutations` (if a
   record does not already exist, including soft-deleted).
6. Resolve and cache the IBS bugowner for the package. If a
   `PackageBugowner` record already exists for this `package_name`,
   update it with fresh data from IBS. If it does not exist, create it.
   See `docs/features/packages/package-bugowner.md` for the resolution
   algorithm.
7. Enqueue
   `discover_submissions_for_ticket_package(ticket_id, package_name)` to
   retroactively discover IBS submission requests (SRs) and release
   requests (RRs) for the ticket's CVE created within the last 14 days.
   See `docs/features/packages/ibs-submission-tracking.md`, Pipeline 3.
8. Return an `AddPackageResult` containing:
   - `tracks_created`, `tracks_skipped`, `products_created`,
     `products_skipped`: counts of records created vs. skipped.

`ticket_mutations` handles idempotency (skipping existing records,
including soft-deleted), initial status determination, and eligibility
logic internally — see `docs/features/tickets/tickets.md`, Ticket
Mutations Module.

New records are created with `deleted_at = NULL`. If the parent package
or track is soft-deleted, these records are automatically **effectively
excluded** via the hierarchy — no special handling is needed. See
[Hierarchical Exclusion Model](#hierarchical-exclusion-model).

**Idempotency**: the function is safe to call multiple times for the
same package. If SMELT adds new tracks or products for a package after
the initial addition, calling the function again will add only the new
records. Existing records (active or soft-deleted) are skipped.

### Triggers

The following scenarios invoke `add_package_to_ticket`:

1. **Automatic (CPE mapping)**: when a CVE is ingested, Sentinel maps
   the CPE data from the CVE record to source package names. For each
   mapped package name, `add_package_to_ticket` is called.
2. **Manual**: the VA manually adds a package by name via the UI.
   `add_package_to_ticket` is called with the entered name.
3. **Track release detection (Case B)**: the release detector finds a
   CVE fix in a package that is not tracked in the ticket. It calls
   `add_package_to_ticket` to add all tracks and products, then sets
   the specific track where the fix was detected to `FIXED` and
   `delivery_status` to `RELEASED`. See
   `docs/features/packages/ibs-track-release-detection.md` (Case B).
4. **Ticket auto-creation (Case C)**: a CVE fix is detected for a CVE
   with no existing ticket. After creating the ticket,
   `add_package_to_ticket` is called, then the originating track is
   set to `FIXED` and `delivery_status` to `RELEASED`. See
   `docs/features/packages/ibs-track-release-detection.md` (Case C).
5. **Restore from soft-deletion**: restoring a package, track, or
   product clears its `deleted_at` only. New tracks/products that
   appeared on SMELT since the deletion are picked up by subsequent
   automatic calls to `add_package_to_ticket` (CPE mapping, release
   detection Case B) — no explicit call is needed at restore time.

### Package Management Constraints

The VA manages packages at the **package level only**:

- The VA can **add** packages to a ticket.
- The VA can **soft-delete** entire packages, individual tracks, or
  individual products from a ticket (see [Soft-Deletion](#soft-deletion)).
- The VA **cannot** add individual tracks or products — these are
  determined exclusively by SMELT when a package is added via
  `add_package_to_ticket`.
- The VA **can** change the affectedness status of individual tracks
  (via the status dropdown) and override the status and eligibility of
  individual products.

### Removing a Package from a Ticket

When a VA removes a package from a ticket, Sentinel performs a
**soft-deletion** (see [Soft-Deletion](#soft-deletion)): `deleted_at`
is set on the `TicketPackage` record only. Child `TicketPackageTrack`
and `TicketPackageProduct` records are not modified — they become
effectively excluded via the hierarchy.

**UI confirmation**: if any of the records being removed are in a final
status (`FIXED`, `NOT_AFFECTED`, or `WONT_FIX`), the UI must display a
confirmation dialog before proceeding (e.g., "This package has N
tracks/products in a final status. Are you sure you want to remove
it?"). The backend API does not enforce this check — it is a UI-only
safeguard.

### SMELT Query for Package Resolution

When `add_package_to_ticket` resolves a package, it calls:

```
GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1
```

**Important implementation notes**:

- The parameter `include_reactive=1` MUST always be included to ensure
  products in Reactive LTSS phase are returned.
- Results are **paginated**. Sentinel must follow the `next` field and
  fetch **all pages** to get the complete list of tracks and products.
- Each result contains a `(package, codestream)` pair with a `channel`
  object. The `channel.targets` array lists the repository project names
  where the package is shipped.

**Processing each result**:

1. Infer `workflow_type` from the track reference (e.g., IBS codestreams
   match `^(SUSE|openSUSE):.*`).
2. Create a `TicketPackageTrack` record with the `codestream` value as
   `reference` and the inferred `workflow_type` (if one does not already
   exist for this package + reference combination, including
   soft-deleted).
3. For each `target` in `channel.targets`:
   a. Look up the target in the `ProductRepository` table to find the
      corresponding `Product`.
   b. If a matching product is found, create a `TicketPackageProduct`
      record linked to the `TicketPackageTrack` (if one does not already
      exist, including soft-deleted).
   c. Deduplicate by product: multiple targets from the same result may
      map to the same product (one per architecture). Only one
      `TicketPackageProduct` record per product per track is needed.
4. If no matching product is found for a target, log a warning but do
   not fail — the product may not yet be synced from SMELT.

---

## Ticket Events for Package Changes

Every modification to a ticket's package data MUST produce a
`TicketAuditEvent` record for audit and traceability. The following event
types are defined:

| Action | `event_type` | `user_id` | Details recorded |
|--------|-------------|-----------|------------------|
| VA adds package | `package_added` | VA user | `package_name` |
| Package auto-added (CPE match or Case B) | `package_added` | `NULL` | `package_name`, contextual `comment` |
| VA soft-deletes package | `package_excluded` | VA user | `package_name` |
| VA soft-deletes track | `track_excluded` | VA user | `package_name`, `reference` |
| VA soft-deletes product | `product_excluded` | VA user | `package_name`, `reference`, `product_id` |
| VA restores package | `package_restored` | VA user | `package_name` |
| VA restores track | `track_restored` | VA user | `package_name`, `reference` |
| VA restores product | `product_restored` | VA user | `package_name`, `reference`, `product_id` |
| VA changes track status | `track_status_changed` | VA user | `package_name`, `reference`, `old_status`, `new_status` |
| VA overrides product status | `product_status_overridden` | VA user | `package_name`, `product_id`, `old_status`, `new_status` |
| VA overrides product eligibility | `product_eligibility_changed` | VA user | `package_name`, `product_id`, `old_eligible`, `new_eligible` |
| Ticket created | `ticket_created` | `NULL` | Creation source description |
| Track release detected | `track_released` | `NULL` | `package_name`, `reference` |
| Product release detected | `product_released` | `NULL` | `package_name`, `product_id`, `advisory_id` |
| Product eligibility recalculated | `product_eligibility_changed` | `NULL` | `package_name`, `product_id`, `old_eligible`, `new_eligible` |

- `user_id = NULL` indicates an automatic system action. For
  `package_added`, this distinguishes manual additions (VA user) from
  automatic ones (CPE match, release detection). The `comment` field
  provides context for automatic additions.
- All events include an implicit `created_at` timestamp.
- The "Details recorded" column lists the values stored in the event's
  `old_value`, `new_value`, and `comment` fields as strings. See
  `docs/features/tickets/ticket-audit-log.md` for the exact field mapping
  and `docs/data-model.md` for the schema.

---

## Release Tracking

Sentinel monitors two **independent** levels of release for each
affected package:

1. **Track level**: the fix has been added to the track's IBS project
   (e.g., `SUSE:SLE-15-SP6:Update`). See
   `docs/features/packages/ibs-track-release-detection.md` for the
   full detection mechanism (MD5 cache, IBS diff analysis, match
   outcomes).
2. **Product level**: the fix has been published to the product's update
   repository (e.g., the SLES 15 SP6 update repository consumed by
   `zypper`). See
   `docs/features/packages/ibs-product-release-detection.md` for the
   full detection mechanism (updateinfo.xml parsing, advisory match
   chain).

The two levels are detected through different mechanisms and update
different data:

- The track level updates `TicketPackageTrack.delivery_status` to
  `RELEASED` and `TicketPackageTrack.status` to `FIXED` (automatic,
  one-shot) when the fix appears in the track's IBS project.
- The product level sets `TicketPackageProduct.released_at` when the
  fix appears in that specific product's update repository.

In both cases, the automatic transition is suppressed when the current
affectedness status is `WONT_FIX` (protected state).

---

## Ticket Lifecycle Integration

All track and product status changes go through the `ticket_mutations`
module, which automatically re-evaluates ticket status after each change.
See `docs/features/tickets/tickets.md` (Ticket Lifecycle, Centralized
Status Evaluation) for the authoritative gate conditions and status
transition rules, including:

- **Analysis → Analyzed**: requires at least one package, all tracks and
  products decided, severity set, SUSE CVSS provided
- **Analyzed → Resolved**: requires all tracks in terminal status, all
  `FIXED` tracks with `delivery_status = RELEASED`, all eligible
  products with `released_at IS NOT NULL`
- Reverse transitions when gate conditions are no longer met

---

## Workflow-Agnostic vs Workflow-Specific

The following concerns are identical regardless of `workflow_type`:

- `PackageStatus` enum and all valid transitions
- `DeliveryStatus` enum (the delivery concept exists for both workflows)
- Status propagation (track → products)
- Protected state (`WONT_FIX` never modified automatically)
- `ticket_mutations` module — operates on `TicketPackageTrack` and
  `TicketPackageProduct`
- Ticket status gates (Analysis → Analyzed → Resolved)
- Product eligibility (CVSS threshold, Reactive LTSS phase)
- Soft-deletion and restore
- UI — VA sees packages → tracks → products with no workflow distinction
- Bugowner — `PackageBugowner` cache keyed by `package_name`; joined via
  `TicketPackage.package_name`

The following concerns are workflow-specific (service layer only):

| Concern | IBS (`ibs`) | Git (`git`) |
|---------|-------------|-------------|
| Track + product resolution | SMELT `maintainedpackage` | SMELT (same API, future) |
| Source retrieval for analysis | IBS API | src.suse.de git API |
| Release detection (track) | IBS MD5 diff | TBD |
| Release detection (product) | updateinfo.xml | TBD |
| Bugowner resolution | IBS bugowner API | TBD (CODEOWNERS?) |
| Real-time events | IBS RabbitMQ | TBD |
| Submission tracking (SR/RR) | IBS submission tracking | TBD |

---

## External Data Sources

- **SMELT product sync** (periodic): see
  `docs/features/packages/product-catalog.md` (SMELT Integration)
- **SMELT package query** (on-demand): see
  [SMELT Query for Package Resolution](#smelt-query-for-package-resolution)
  above
- **AIMAAS lifecycle and threshold sync** (periodic): see
  `docs/features/packages/product-catalog.md` (AIMAAS Integration)

---

## API Endpoints

### Add Package to Ticket

```
POST /api/v1/tickets/{ticket_id}/packages
```

Add a source package to a ticket. Sentinel queries SMELT to resolve all
maintained tracks and products for the package, creates `TicketPackage`,
`TicketPackageTrack`, and `TicketPackageProduct` records via
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
    "tracks_created": 3,
    "tracks_skipped": 0,
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
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Package exists on this ticket but is soft-deleted — use the restore endpoint |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `VALIDATION_ERROR` | Missing or empty `package_name` |
| 422 | `PACKAGE_NOT_FOUND_IN_SMELT` | SMELT returned no results for the given package name |
| 503 | `SMELT_UNAVAILABLE` | SMELT is unreachable or returned a server error |

**Idempotency**: safe to call multiple times for the same **active**
package. If the package is already fully resolved, the response will
report zero created records. If the package is soft-deleted, the endpoint
returns 409 `PACKAGE_ALREADY_EXCLUDED` — the VA must use the restore
endpoint to re-include it.

---

### Soft-Delete Package from Ticket

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/exclude
```

Soft-delete a package from the ticket. Sets `deleted_at` on the package
record only — tracks and products are not modified but become
effectively excluded via the hierarchy. Creates a single `TicketAuditEvent`.
See [Soft-Deletion](#soft-deletion) for the full behavior.

**Response** (200 OK):

```json
{
  "data": {
    "package_name": "openssl-3"
  }
}
```

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `PACKAGE_ALREADY_EXCLUDED` | Package is already soft-deleted |

---

### Restore Package

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/restore
```

Restore a soft-deleted package. Clears `deleted_at` on the package
record only — child records are not modified. Creates a single
`TicketAuditEvent`. See [Soft-Deletion — Restore](#restore).

**Pre-check**: the package must have at least one track with
`deleted_at IS NULL` that itself has at least one product with
`deleted_at IS NULL`. If not, returns `422 PACKAGE_RESTORE_BLOCKED`.

**Response** (200 OK):

```json
{
  "data": {
    "package_name": "openssl-3"
  }
}
```

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `PACKAGE_NOT_EXCLUDED` | Package is not directly soft-deleted |
| 422 | `PACKAGE_RESTORE_BLOCKED` | Package has no active tracks with active products. Restore at least one track (with active products) first. |

---

### Soft-Delete Track

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/exclude
```

Soft-delete a track from the ticket. Sets `deleted_at` on the track
record only — products under it are not modified but become effectively
excluded via the hierarchy. Creates a single `TicketAuditEvent`.

**Response** (200 OK):

```json
{
  "data": {
    "reference": "SUSE:SLE-15-SP6:Update"
  }
}
```

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `PACKAGE_ALREADY_EXCLUDED` | Track is already soft-deleted |

---

### Restore Track

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/restore
```

Restore a soft-deleted track. Clears `deleted_at` on the track record
only — products under it are not modified. Creates a single
`TicketAuditEvent`.

**Pre-check**: the track must have at least one product with
`deleted_at IS NULL`. If not, returns `422 PACKAGE_RESTORE_BLOCKED`.

**Response** (200 OK):

```json
{
  "data": {
    "reference": "SUSE:SLE-15-SP6:Update"
  }
}
```

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `PACKAGE_NOT_EXCLUDED` | Track is not directly soft-deleted |
| 422 | `PACKAGE_RESTORE_BLOCKED` | Track has no active products. Restore at least one product first. |

---

### Soft-Delete Product

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}/exclude
```

Soft-delete a single product from a track.

**Response** (200 OK):

```json
{
  "data": {
    "product_id": "uuid",
    "product_name": "SLES-LTSS 15-SP4"
  }
}
```

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `PACKAGE_ALREADY_EXCLUDED` | Product is already soft-deleted |

---

### Restore Product

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}/restore
```

Restore a soft-deleted product. Clears `deleted_at` on the product
record. No pre-check needed (products have no children). Creates a
single `TicketAuditEvent`.

**Response** (200 OK):

```json
{
  "data": {
    "product_id": "uuid",
    "product_name": "SLES-LTSS 15-SP4"
  }
}
```

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `PACKAGE_NOT_EXCLUDED` | Product is not directly soft-deleted |

---

### Change Track Status

```
PATCH /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}
```

Change the affectedness status of a track. Triggers status propagation
to all active child products (with eligibility evaluation for
"Affected"), TicketAuditEvent creation, and ticket status re-evaluation — all
via `ticket_mutations`.

**Request body**:

```json
{
  "status": "AFFECTED"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status value. Valid values: `ANALYSIS`, `AFFECTED`, `NOT_AFFECTED`, `FIXED`, `WONT_FIX` |

**Note on PATCH with side effects**: this endpoint uses PATCH because
from the client's perspective it is a single-field update on a specific
resource. The side effects (product propagation, eligibility evaluation,
ticket status re-evaluation) are a consequence of the domain model, not
of additional business operations. This is a documented deviation from
the `POST /resource/{id}/verb` convention for operations with side
effects.

**Response** (200 OK):

```json
{
  "data": {
    "ticket_id": "uuid",
    "package_name": "openssl-3",
    "reference": "SUSE:SLE-15-SP6:Update",
    "status": "AFFECTED",
    "delivery_status": "PENDING",
    "products": [
      {
        "product_id": "uuid",
        "product_name": "SLES 15 SP6",
        "status": "AFFECTED",
        "eligible": true,
        "is_status_override": false,
        "is_eligible_override": false
      },
      {
        "product_id": "uuid",
        "product_name": "SLES-LTSS 15-SP4",
        "status": "AFFECTED",
        "eligible": false,
        "is_status_override": false,
        "is_eligible_override": false
      }
    ]
  }
}
```

The response includes the updated track and all its active child
products with their resulting statuses and eligibility (after
propagation), allowing the client to update the UI tree without a
separate fetch.

**Permissions**: Vulnerability Analyst role required.

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 403 | `AUTH_INSUFFICIENT_ROLE` | Caller does not have Vulnerability Analyst role |
| 404 | `TICKET_NOT_FOUND` | Ticket with given ID does not exist |
| 404 | `RESOURCE_NOT_FOUND` | Package or track not found on this ticket |
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `VALIDATION_ERROR` | Invalid status value |

---

### Override Product Status

```
PATCH /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}
```

Override the affectedness status and/or eligibility of a specific
product. Sets the corresponding `is_*_override` flag to `true`.
Triggers TicketAuditEvent creation and ticket status re-evaluation via
`ticket_mutations`.

**Request body**:

```json
{
  "status": "WONT_FIX"
}
```

Or for eligibility override:

```json
{
  "eligible": false
}
```

Or both:

```json
{
  "status": "AFFECTED",
  "eligible": false
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | No | New status value. Valid values: `ANALYSIS`, `AFFECTED`, `NOT_AFFECTED`, `FIXED`, `WONT_FIX` |
| `eligible` | boolean | No | Eligibility override |

At least one of `status` or `eligible` must be provided.

**Note on PATCH with side effects**: same rationale as the track
endpoint above — single-field update from the client's perspective.

**Response** (200 OK):

```json
{
  "data": {
    "ticket_id": "uuid",
    "package_name": "openssl-3",
    "reference": "SUSE:SLE-15-SP6:Update",
    "product_id": "uuid",
    "product_name": "SLES-LTSS 15-SP4",
    "status": "WONT_FIX",
    "eligible": false,
    "is_status_override": true,
    "is_eligible_override": true
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
| 422 | `VALIDATION_ERROR` | Invalid status value, or neither `status` nor `eligible` provided |

---

## UI Requirements

### Ticket Detail — Affectedness Section

The affectedness section on the ticket detail page displays a tree
structure:

```
[+ Add Package]

Package: openssl-3                              [Exclude]
├── SUSE:SLE-15-SP6:Update  [Affected ▼]   In Progress  [Exclude]
│   ├── SLES 15 SP6         Affected  (eligible)        [Exclude]
│   ├── SLED 15 SP6         Affected  (eligible)        [Exclude]
│   └── SLES-LTSS 15-SP4    Affected  (not eligible)    [Exclude]
├── SUSE:SLE-15-SP5:Update  [Not Affected ▼]  Pending   [Exclude]
│   └── SLES-LTSS 15-SP5    Not Affected                [Exclude]
└── SUSE:SLE-15-SP3:Update  [Fixed ▼]         Released  [Exclude]
    └── SLES-LTSS 15-SP1    Fixed  (not eligible)       [Exclude]
```

- **Package level**: shows the package name with an option to exclude it
  (soft-delete)
- **Track level**: shows the track reference with:
  - A **status dropdown** (left): Analysis, Affected, Not Affected,
    Fixed, Won't Fix
  - A **delivery badge** (right): shows delivery progress (Pending /
    In Progress / Released) with color coding (grey / orange / green).
    Clicking the badge opens a popover with SR/incident/RR details.
  - An **Exclude** action
- **Product level**: shows the product name, inherited status (with
  color), and eligibility indicator. Products have an option to override
  the status and eligibility (which sets the corresponding
  `is_*_override = true`). Each product has an Exclude action.
- **Color coding**: Affected = red, all final states = green,
  Analysis = neutral/no color. Not eligible products are greyed out.
- **Add Package**: opens an input where the VA types a package name.
  Sentinel queries SMELT and populates the tree. If SMELT returns no
  results, an error is shown.

### Excluded Items Panel

When the ticket has excluded records (directly or via hierarchy), a
discrete indicator is shown (e.g., "3 excluded items"). Clicking it
opens a panel showing:

- Each excluded item with its **current state** (updated in real-time)
- Whether it was excluded **directly** (`deleted_at` on the record
  itself) or **indirectly** (via a parent's `deleted_at`), and at which
  level (package or track)
- When it was excluded (from the relevant `deleted_at` timestamp —
  the record's own or the ancestor's)
- A "Restore" button for **directly excluded** items only (items
  excluded via hierarchy cannot be restored individually — the parent
  must be restored first)

### Product Release Anomaly Indicator

When a track has `status = FIXED` and `delivery_status = RELEASED` but
a specific eligible product has NOT received the fix (no `released_at`),
that product displays a warning indicator in the UI:

```
kernel-default (SLE-15-SP6)  [FIXED]         Released
   SLES 15 SP6              (normal)
   SLED 15 SP6              ! update not received    <-- blocks ticket
   SLES 15 SP5 LTSS         (greyed out, not eligible)
```

This is an exceptional case (possible causes: product not enabled in
incident by mistake, repository sync delay, operational error). It gives
the VA immediate visibility into what is blocking ticket resolution.

---

## Background Tasks

Product sync tasks (`sync_smelt_products`, `sync_aimaas_lifecycle`,
`sync_aimaas_thresholds`) are specified in
`docs/features/packages/product-catalog.md` (Background Tasks).

- `check_ibs_track_releases`: periodic task (every 24 hours at 02:00
  UTC via Celery Beat) that invokes the `IBSTrackReleaseDetector`
  service. Serves as a catch-up mechanism for events missed by the
  real-time `IBSEventConsumer` (see
  `docs/features/integrations/ibs-rabbitmq-integration.md`). See
  `docs/features/packages/ibs-track-release-detection.md` for the
  full procedure. When a release is detected, sets
  `TicketPackageTrack.delivery_status = RELEASED` and
  `TicketPackageTrack.status = FIXED`.
- `check_product_releases`: periodic task that invokes the
  `ProductReleaseDetector` (`updateinfo.xml`-based) for
  `TicketPackageProduct` records and sets `released_at`. See
  `docs/features/packages/ibs-product-release-detection.md` for the
  full procedure. Frequency and scope are TBD.
- `create_ticket_from_detection`: on-demand task enqueued by the
  `IBSTrackReleaseDetector` or the `IBSEventConsumer` when a CVE fix
  is detected for a CVE that has no ticket in Sentinel. Fetches CVE
  data from NVD, creates the ticket, resolves packages via SMELT, and
  sets the originating track to `FIXED` with
  `delivery_status = RELEASED`. See
  `docs/features/packages/ibs-track-release-detection.md` (Case C)
  for details.
- `check_lifecycle_phase_transitions`: periodic task (daily at 04:00
  UTC) that detects products currently in Reactive LTSS or EOL phase
  with actionable `TicketPackageProduct` records and enqueues
  re-evaluation. With the new model: Reactive LTSS sets
  `eligible = false` (status stays `AFFECTED`); EOL with `AFFECTED`
  status removes the product (soft-delete with system TicketAuditEvent); EOL
  with `ANALYSIS` status removes the product. Idempotent — operates on
  current state with no cache. See
  `docs/features/packages/product-lifecycle-transitions.md` for the
  full specification.

---

## Security

- Adding/removing/excluding/restoring packages on a ticket requires the
  Vulnerability Analyst role
- Changing track/product status or eligibility requires the
  Vulnerability Analyst role
- Viewing affectedness data is publicly accessible (no authentication
  required)

---

## Future Considerations

- **openSUSE / OBS public**: tracking packages in build.opensuse.org for
  openSUSE Tumbleweed and Leap will be addressed in a separate spec.
- **Channel file parsing**: direct parsing of channel files from
  `SUSE:Channels` may be added if SMELT data is insufficient.
- **Git workflow specifics**: release detection (track and product
  level), bugowner resolution, real-time events, and submission tracking
  for the git workflow are TBD and will be specified when the SLFO
  workflow is better defined.
- **Review Queue**: anomalous combinations of affectedness and delivery
  status will be integrated into a ticket review queue. See
  [Anomaly Detection](#anomaly-detection-future-review-queue).

---

## Open Items

- [ ] Release detection mechanism for git workflow (track-level and
      product-level)
- [ ] Bugowner resolution for git workflow (CODEOWNERS? maintainer file?)
- [ ] Real-time event source for git workflow (webhook? polling?)
- [ ] SMELT API evolution — confirm that `maintainedpackage` will return
      git branches alongside codestreams
- [ ] Inference heuristic for workflow_type — define the exact pattern
      matching rules
- [ ] Submission tracking (SR/RR) equivalent for git workflow, if any
- [ ] Override reset mechanism — define how a VA can reset a product
      status or eligibility override back to automatic inheritance from
      the parent track

---

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error
  codes, pagination, shared 422 responses)
- `docs/features/packages/product-catalog.md` — product catalog, SMELT
  product sync, AIMAAS lifecycle/threshold sync, `GET /api/v1/products`
- `docs/features/tickets/tickets.md` — ticket lifecycle, status gates,
  ticket mutations module
- `docs/features/tickets/cvss-scoring.md` — CVSS resolution cascade,
  eligibility threshold comparison
- `docs/features/tickets/ticket-audit-log.md` — TicketAuditEvent field mapping
- `docs/features/packages/ibs-track-release-detection.md` — IBS
  track-level release detection
- `docs/features/packages/ibs-product-release-detection.md` — IBS
  product-level release detection
- `docs/features/packages/ibs-submission-tracking.md` — SR/RR tracking,
  delivery pipeline, RequestSyncFetcher
- `docs/features/integrations/ibs-rabbitmq-integration.md` — real-time
  IBS event consumption
- `docs/features/packages/product-lifecycle-transitions.md` — EOL and
  Reactive LTSS handling
- `docs/features/packages/package-bugowner.md` — bugowner resolution
- `docs/features/platform/admin.md` — default CVSS version configuration
- `docs/data-model.md` — full database schema
