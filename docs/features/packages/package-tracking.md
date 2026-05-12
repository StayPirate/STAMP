# Package Tracking

## Purpose

Track the affectedness of source packages across maintenance tracks and
SUSE products in the context of tickets. See
`docs/features/tickets/tickets.md` for the ticket specification
(identification, creation, lifecycle).

## Motivation

The current model conflates three orthogonal concepts in a single
`PackageStatus` enum:

1. **Affectedness** — is the source code vulnerable? (`AFFECTED` vs
   `NOT_AFFECTED`)
2. **Eligibility** — will this product receive the fix? (`AFFECTED` vs
   `AFFECTED_RESOLVED`)
3. **Delivery** — has the fix been distributed? (`RELEASED`)

Additionally, the model is tightly coupled to the IBS codestream workflow.
A new workflow is emerging for future SUSE products (SLFO) where packages
live on git (`src.suse.de`) instead of IBS. The model must support both
workflows transparently.

This redesign separates the three concepts into independent dimensions,
introduces a workflow-agnostic entity hierarchy, and simplifies
propagation logic.

## Design Decisions

### 1. New explicit entity: TicketPackage

A `TicketPackage` table is introduced as the first level under Ticket.
Today the "package" is an implicit grouping by `package_name` across
`TicketPackageCodestream` records. With two workflows potentially
coexisting under the same package, an explicit entity provides:

- A clear anchor for package-level metadata (bugowner join, future notes)
- A single grouping point for tracks of both workflows
- Cleaner API design (`/tickets/{id}/packages/{id}/tracks`)

### 2. TicketPackageTrack replaces TicketPackageCodestream

The intermediate level is renamed from "codestream" to "track" — a
neutral term that describes "a maintenance track for a package that serves
one or more products." This abstraction covers:

- IBS codestreams (e.g., `SUSE:SLE-15-SP6:Update`)
- Git branches (e.g., `slfo-main`, `slfo-1.2` on `src.suse.de/pool/{package}`)

### 3. Single table with workflow_type discriminator

A single `TicketPackageTrack` table with a `workflow_type` enum (`ibs` |
`git`) discriminates between workflows. This was chosen over separate
tables or base+satellite patterns because:

- The differences between workflows are minimal at the data level
- Business logic (status propagation, eligibility, rollup) is identical
- Separate tables would duplicate logic everywhere (queries, propagation, UI)

### 4. Generic reference field

A single `reference` VARCHAR field identifies the track in its external
system:

- IBS: the codestream project name (e.g., `SUSE:SLE-15-SP6:Update`)
- Git: the branch name (e.g., `slfo-main`)

No separate `name` field — the reference is already human-readable in
both cases. For git, the full repository URL is derivable from
`package_name` (convention: `src.suse.de/pool/{package_name}`).

### 5. workflow_type inferred by Sentinel

SMELT will serve both IBS and git tracks but will not provide an explicit
workflow type indicator. Sentinel infers `workflow_type` at ingestion time
(e.g., IBS codestreams match `^(SUSE|openSUSE):.*`) and persists it. The
heuristic is centralized in the SMELT ingestion service.

### 6. Both workflows can coexist under the same package

A single package in a ticket can have both IBS tracks (for legacy
products) and git tracks (for SLFO products). This is expected during the
transition period and possibly long-term.

### 7. SMELT handles both workflows

The resolution flow (package → tracks → products) remains unified through
SMELT's `maintainedpackage` endpoint (or its future evolution). No
separate API is needed for git track discovery.

### 8. Separate eligibility from affectedness (remove AFFECTED_RESOLVED)

The `AFFECTED_RESOLVED` value is removed from the `PackageStatus` enum.
Product eligibility (whether a product will receive the fix) becomes a
separate persisted boolean (`eligible`) on `TicketPackageProduct`, with
its own override mechanism (`is_eligible_override`).

#### Historical Context

The original model used `AFFECTED_RESOLVED` to represent "the product is
affected, but no fix will be delivered because the product is not
eligible" — encoding two orthogonal concepts in a single status value.

#### Problems Solved

1. **Codestream eligibility rollup eliminated** — the most complex piece
   of status propagation. When all products under a codestream became
   `AFFECTED_RESOLVED`, the codestream itself was set to
   `AFFECTED_RESOLVED`. This bidirectional rollup between track and
   product levels is eliminated entirely. The track stays `AFFECTED` and
   the question "is there work to do on this track?" is answered by
   checking whether any product under it is eligible.

2. **Status ping-pong eliminated** — CVSS score changes caused products
   to flip between `AFFECTED` and `AFFECTED_RESOLVED`, which triggered
   the codestream rollup, which triggered ticket gate re-evaluation.
   With the new model, only the `eligible` flag changes. No status
   change, no rollup, direct gate re-evaluation.

3. **Semantic confusion resolved** — a VA setting "Affected" on a track
   would see some products turn green. With the new model, `AFFECTED`
   means `AFFECTED` everywhere — ineligible products are visually
   distinguished (greyed out) without changing the status label.

4. **Gate simplified** — the Resolved gate no longer treats
   `AFFECTED_RESOLVED` as a final state. The new gate is explicit: "all
   records in a final status, OR AFFECTED and not eligible."

### 9. Separate delivery from affectedness (remove RELEASED from PackageStatus)

The `RELEASED` status is removed from the `PackageStatus` enum and
replaced by two independent mechanisms:

- **Delivery status** (`delivery_status` column on `TicketPackageTrack`):
  tracks the progress of the fix through the maintenance pipeline
  (PENDING → IN_PROGRESS → RELEASED), derived from SR/RR tracking data
- **Product release confirmation** (`released_at` on
  `TicketPackageProduct`): confirms that the fix has appeared in the
  product's update repository via `updateinfo.xml` verification

Source code is either affected or not affected. Once a fix is applied, the
code is no longer vulnerable — calling it "Released" describes where the
fix went, not what the code is.

### 10. FIXED as a new affectedness state

A new `FIXED` value is added to `PackageStatus` to distinguish "was
vulnerable, now remediated" from "was never vulnerable"
(`NOT_AFFECTED`):

- `NOT_AFFECTED` — no action was ever necessary (analysis only)
- `FIXED` — action was taken, vulnerability has been remediated

Both mean "the code today is not vulnerable," but they carry different
history and workload implications.

**Transition behavior**:

- The VA can set `FIXED` manually at any time via the dropdown (e.g.,
  when they know the fix exists but has not been released yet)
- The system sets `FIXED` automatically when it detects a release (RR
  accepted) — this is a one-shot event, not a continuous reconciliation
- The VA can change `FIXED` back to `AFFECTED` if they determine the
  fix is insufficient
- No `is_status_override` flag is needed on the track — the VA has
  direct control via the dropdown and the automatic transition only
  fires on discrete release events

### 11. Delivery status persisted as a column

The `delivery_status` is persisted as a column on `TicketPackageTrack`
rather than computed at query time. Reasons:

- With the decision to keep affectedness and delivery as independent axes
  (Decision 12), the delivery status is first-class data
- The ticket resolution gate is a frequent and critical operation — it
  should not require complex joins with SR/RR tables
- Anomaly detection (future Review Queue) is simpler with both values
  as columns on the same record
- Disalignment risk is mitigated: updates go through `ticket_mutations`,
  and the `RequestSyncFetcher` includes a reconciliation phase (see
  [Delivery Reconciliation](#delivery-reconciliation))

### 12. Affectedness and delivery are independent axes

The affectedness status (set by the VA) and the delivery status (derived
from IBS SR/RR tracking) are tracked independently. Neither axis resets
or constrains the other.

This means "anomalous" combinations are possible (e.g., `AFFECTED` +
`RELEASED`). These combinations are valid system states that carry
diagnostic meaning — they signal situations requiring VA attention.
See [Anomaly Detection](#anomaly-detection-future-review-queue).

### 13. Remove IGNORED, replace with soft-deletion

The `IGNORED` status is removed from `PackageStatus`. Its use case
(marking spurious tracks or products that should not be in the ticket)
is handled by a soft-deletion mechanism that is more semantically
correct: a track that should not exist is removed, not marked with a
special status.

See [Soft-Deletion](#soft-deletion) for the full mechanism.

### 14. Soft-deleted records continue to receive updates

Records that have been soft-deleted by a VA are excluded from normal
views, gates, and anomaly detection, but they **continue to receive
updates** from propagation, delivery tracking, eligibility recalculation,
and release detection. Their state is always current.

This eliminates the need for special reconciliation logic at restore
time — restoring a record simply sets `deleted_by = NULL`, and the
record's state already reflects the current reality.

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
updates via the package manager. Products include base products (e.g.,
SLES 15 SP6), LTSS variants (e.g., SLES-LTSS 15-SP4), ESPOS variants
(e.g., HPC ESPOS 15-SP5), and SAP variants. Each variant is a
**separate product** in both SMELT and AIMAAS, with its own CPE
identifier.

A product receives binary packages from one or more tracks. The same
track can feed multiple products. The mapping between a track's packages
and the products that receive them is resolved by SMELT on a per-package
basis.

### Channel File

An XML file in the IBS project `SUSE:Channels` that defines which
packages from which codestreams are shipped to which products. There is
one channel file per product. Sentinel does not parse channel files
directly — it relies on SMELT to resolve these mappings.

### SMELT

An internal SUSE aggregator service (REST API at `smelt.suse.de/api`)
that provides:

1. **Product listing** (`GET /api/v1/basic/products/`): paginated list of
   all SUSE products with name, version, CPE, end-of-life date, and
   repository project names.
2. **Per-package maintenance info**
   (`GET /api/v1/basic/maintainedpackage/`): given a source package name,
   returns the list of tracks where the package is maintained and the
   target repositories (which map to products).

SMELT reads from IBS, channel files, and other sources internally.

### AIMAAS

An internal SUSE service (REST API at `aimaas.suse.de/api`) that
provides:

1. **Product lifecycle data** (`GET /api/entity/products/{slug}`): dates
   for each lifecycle phase — `fcs` (first customer shipment),
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

---

## Data Model

See `docs/data-model.md` for the full schema. The tables defined by this
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

### TicketPackage

An explicit entity that anchors a source package within a ticket. Replaces
the implicit grouping by `package_name` across
`TicketPackageCodestream` records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Internal identifier |
| `ticket_id` | UUID | FK(ticket.id), NOT NULL | Related ticket |
| `package_name` | VARCHAR | NOT NULL | Source package name |
| `deleted_by` | UUID | FK(user.id), nullable | Soft-deletion: user who excluded this package. NULL = active |
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
| `deleted_by` | UUID | FK(user.id), nullable | Soft-deletion: user who excluded this track. NULL = active |
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
| `deleted_by` | UUID | FK(user.id), nullable | Soft-deletion: user who excluded this product. NULL = active |
| `created_at` | TIMESTAMP | NOT NULL, DEFAULT | Record creation timestamp |
| `updated_at` | TIMESTAMP | NOT NULL, DEFAULT | Record update timestamp |

**Unique constraint**: `(ticket_package_track_id, product_id)`

### PackageStatus Enum

The affectedness status enum, used by both `TicketPackageTrack` and
`TicketPackageProduct`.

| Value | UI Label | Color | Type | Set by |
|-------|----------|-------|------|--------|
| `ANALYSIS` | Analysis | Neutral | Non-final | Automatic (default) |
| `AFFECTED` | Affected | Red | Non-final | VA (as "Affected") |
| `NOT_AFFECTED` | Not Affected | Green | Final | VA |
| `FIXED` | Fixed | Green | Final | Automatic (release detected) or VA |
| `WONT_FIX` | Won't Fix | Green | Final | VA only |

**UI note**: the VA dropdown shows the following options: Analysis,
Affected, Not Affected, Fixed, Won't Fix.

### DeliveryStatus Enum

The delivery pipeline status, used by `TicketPackageTrack`.

| Value | UI Label | Color | Condition |
|-------|----------|-------|-----------|
| `PENDING` | Pending | Grey | No SR created |
| `IN_PROGRESS` | In Progress | Orange | SR created, until RR accepted |
| `RELEASED` | Released | Green | RR accepted |

### WorkflowType Enum

| Value | Meaning | Example reference |
|-------|---------|-------------------|
| `ibs` | IBS project (traditional) | `SUSE:SLE-15-SP6:Update` |
| `git` | Git branch on src.suse.de | `slfo-main`, `slfo-1.2` |

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

| Affectedness | Delivery | Typical meaning |
|-------------|----------|-----------------|
| `ANALYSIS` | `PENDING` | Normal: not yet analyzed, no SR |
| `ANALYSIS` | `IN_PROGRESS` | SR exists before VA analyzed the track |
| `ANALYSIS` | `RELEASED` | Fix released before VA analyzed the track |
| `AFFECTED` | `PENDING` | Normal: affected, no SR yet |
| `AFFECTED` | `IN_PROGRESS` | Normal: fix in the pipeline |
| `AFFECTED` | `RELEASED` | Anomalous: fix released but VA considers it insufficient |
| `NOT_AFFECTED` | `PENDING` | Normal: not affected, nothing to deliver |
| `NOT_AFFECTED` | `IN_PROGRESS` | Anomalous: SR in progress for unaffected code |
| `NOT_AFFECTED` | `RELEASED` | Anomalous: fix released for unaffected code |
| `FIXED` | `PENDING` | VA set FIXED manually, no SR detected yet |
| `FIXED` | `IN_PROGRESS` | Fix confirmed, SR still in pipeline |
| `FIXED` | `RELEASED` | Normal: fix confirmed and delivered |
| `WONT_FIX` | `PENDING` | Normal: decided not to fix, nothing in pipeline |
| `WONT_FIX` | `IN_PROGRESS` | Anomalous: SR in progress for a won't-fix decision |
| `WONT_FIX` | `RELEASED` | Anomalous: fix released despite won't-fix decision |

### Anomaly Detection (future: Review Queue)

Combinations where affectedness and delivery are incongruent indicate
situations that require VA attention — a possible bug, a maintainer not
following the workflow, or an outdated VA assessment. These combinations
are:

| Affectedness | Delivery | Signal |
|-------------|----------|--------|
| `AFFECTED` | `RELEASED` | Fix released but VA considers it insufficient — needs review |
| `NOT_AFFECTED` | `IN_PROGRESS` | SR in progress for code not affected — possible confusion |
| `NOT_AFFECTED` | `RELEASED` | Fix released for code not affected — possible confusion |
| `WONT_FIX` | `IN_PROGRESS` | SR in progress despite won't-fix decision — conflicting |
| `WONT_FIX` | `RELEASED` | Fix released despite won't-fix decision — conflicting |

These combinations are destined to be integrated into the future
**Review Queue** — a mechanism that will automatically tag tickets
presenting anomalies, making them visible to VAs for review. The
specification of the Review Queue and the tagging mechanism will be
defined in a dedicated specification.

---

## Status Behavior

All track and product status changes described in this section MUST go
through the `ticket_mutations` module (see
`docs/features/tickets/tickets.md`, Ticket Mutations Module), which
ensures automatic ticket status re-evaluation after each change.

### VA Sets "Affected" on a Track

1. Track status is set to `AFFECTED` (via `ticket_mutations`)
2. Sentinel propagates to all active (non-soft-deleted) products under
   that track:
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
from the ticket. Soft-deletion is indicated by a non-null `deleted_by`
column (FK to User) on the record:

- `deleted_by IS NOT NULL` → record is soft-deleted (excluded)
- `deleted_by IS NULL` → record is active

The timestamp of the deletion is recorded in the corresponding
`TicketEvent`, not on the record itself (avoids drift between
`deleted_at` and `deleted_by`).

### Cascade

Soft-deletion cascades downward:

- **Package soft-deleted** → all tracks under it are soft-deleted → all
  products under those tracks are soft-deleted
- **Track soft-deleted** → all products under it are soft-deleted
- **Product soft-deleted** → only the product itself

All cascaded records are marked with the same `deleted_by` user.

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
- Who excluded it (from `deleted_by`)
- When it was excluded (derived from the `TicketEvent` timestamp)
- A "Restore" button for each item

### Restore

When the VA restores a soft-deleted record:

1. `deleted_by` is set to `NULL` on the record
2. If restoring a track: all products under it are also restored
   (`deleted_by = NULL`)
3. If restoring a package: all tracks and products under it are also
   restored
4. The record's state is already current (no recalculation needed)
5. `add_package_to_ticket` is called for the package — this adds any
   new tracks/products that appeared on SMELT since the deletion
   (idempotent: existing records are skipped)
6. `TicketEvent` records are created for each restored element

### Interaction with add_package_to_ticket

The `add_package_to_ticket` function checks for record existence
including soft-deleted records. If a `TicketPackageTrack` or
`TicketPackageProduct` record already exists (whether active or
soft-deleted), it is skipped — a soft-deleted record is NOT recreated.
This prevents automatic processes (SMELT sync, release detection
Case B/C) from overriding a VA's explicit exclusion decision.

### Ticket Events for Soft-Deletion

A separate `TicketEvent` is created for **every** element affected by a
soft-deletion or restore operation. When a VA soft-deletes a track with
5 products, 6 events are created (1 for the track + 5 for the products).
This supports filtering by individual products in audit queries.

| Action | `event_type` | `user_id` | Details recorded |
|--------|-------------|-----------|------------------|
| VA soft-deletes a track | `track_excluded` | VA user | `package_name`, `reference` |
| VA soft-deletes a product | `product_excluded` | VA user | `package_name`, `reference`, `product_id` |
| VA soft-deletes a package | `package_excluded` | VA user | `package_name` |
| VA restores a track | `track_restored` | VA user | `package_name`, `reference` |
| VA restores a product | `product_restored` | VA user | `package_name`, `reference`, `product_id` |
| VA restores a package | `package_restored` | VA user | `package_name` |

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
   already exist (including soft-deleted — a soft-deleted
   `TicketPackage` is considered "existing" and is skipped).
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
8. Return a result indicating which records were created and which were
   skipped (already existed or soft-deleted).

`ticket_mutations` handles idempotency (skipping existing records,
including soft-deleted), initial status determination, and eligibility
logic internally — see `docs/features/tickets/tickets.md`, Ticket
Mutations Module.

**Idempotency**: the function is safe to call multiple times for the
same package. If SMELT adds new tracks or products for a package after
the initial addition, calling the function again will add only the new
records. Existing records (including soft-deleted) are skipped.

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
   `docs/features/packages/ibs-codestream-release-detection.md` (Case B).
4. **Ticket auto-creation (Case C)**: a CVE fix is detected for a CVE
   with no existing ticket. After creating the ticket,
   `add_package_to_ticket` is called, then the originating track is
   set to `FIXED` and `delivery_status` to `RELEASED`. See
   `docs/features/packages/ibs-codestream-release-detection.md` (Case C).
5. **Restore from soft-deletion**: when a VA restores a soft-deleted
   package, track, or product, `add_package_to_ticket` is called to
   add any new tracks/products that appeared on SMELT since the
   deletion.

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
**soft-deletion** (see [Soft-Deletion](#soft-deletion)): the
`TicketPackage` record and all its child `TicketPackageTrack` and
`TicketPackageProduct` records are marked with `deleted_by`.

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
`TicketEvent` record for audit and traceability. The following event
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
  `docs/features/tickets/ticket-history.md` for the exact field mapping
  and `docs/data-model.md` for the schema.

---

## Release Tracking

Sentinel monitors two **independent** levels of release for each
affected package:

1. **Track level**: the fix has been added to the track's IBS project
   (e.g., `SUSE:SLE-15-SP6:Update`). See
   `docs/features/packages/ibs-codestream-release-detection.md` for the
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

## Ticket Resolution Gate

A ticket transitions to Resolved automatically when ALL of the following
conditions are met (only active, non-soft-deleted records are
considered):

1. **Every track** has a terminal affectedness status:
   - `FIXED`, `NOT_AFFECTED`, or `WONT_FIX`

2. **Every track with status `FIXED`** has `delivery_status = RELEASED`

3. **Every eligible product** (`eligible = true`) under a `FIXED` track
   has confirmed receipt of the update (`released_at IS NOT NULL`,
   verified via `updateinfo.xml` in the product's update repository)

If any of these conditions is not met, the ticket remains open. The VA
can inspect which component is blocking resolution.

### Anomaly Indicator

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

### Ticket Lifecycle Integration

See `docs/features/tickets/tickets.md` (Ticket Lifecycle) for the
authoritative gate conditions and status transition rules. All track and
product status changes go through the `ticket_mutations` module, which
automatically re-evaluates ticket status after each change (see
`docs/features/tickets/tickets.md`, Centralized Status Evaluation). The
affectedness-related conditions are summarized here for context:

- **Analysis → Analyzed** (automatic): at least one package must be
  added, no active `TicketPackageTrack` or `TicketPackageProduct`
  records may be in `ANALYSIS` status. Additional gate conditions
  (severity, CVSS) are defined in `docs/features/tickets/tickets.md`.
- **Analyzed → Resolved** (automatic): all active `TicketPackageTrack`
  and `TicketPackageProduct` records must satisfy the resolution gate
  (see above).
- **Analyzed → Analysis** (automatic): gate conditions for Analyzed no
  longer met (e.g., package added with tracks in `ANALYSIS`, or VA
  resets a track status to `ANALYSIS`).
- **Resolved → Analyzed** (automatic): resolved gate conditions no
  longer met but analyzed gates still met (e.g., CVSS recalculation
  changes eligibility, causing a previously satisfied gate to fail).
- **Resolved → Analysis** (automatic): both resolved and analyzed gate
  conditions no longer met.

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
- Bugowner — `PackageBugowner` cache keyed by `package_name`; join moves
  from `TicketPackageCodestream.package_name` to
  `TicketPackage.package_name` (cleaner)

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

- **Endpoint**:
  `GET /api/v1/basic/maintainedpackage/?package={name}&include_reactive=1`
  (paginated)
- **CRITICAL**: The `include_reactive=1` parameter MUST always be
  included. Without it, products currently in the Reactive LTSS phase
  are excluded from results.
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
- **Target resolution**: the `target` value is a SMELT repository
  project name. It is matched against the `ProductRepository.repo_name`
  column to find the corresponding `Product`. Multiple targets may map
  to the same product (one per architecture) — deduplicate by product.

### AIMAAS Integration

#### Product Lifecycle Sync (periodic)

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

#### CVSS Threshold Sync (periodic)

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
| 410 | `TICKET_DELETED` | Ticket exists but has been soft-deleted |
| 422 | `VALIDATION_ERROR` | Missing or empty `package_name` |
| 422 | `PACKAGE_NOT_FOUND_IN_SMELT` | SMELT returned no results for the given package name |
| 503 | `SMELT_UNAVAILABLE` | SMELT is unreachable or returned a server error |

**Idempotency**: safe to call multiple times for the same package. If the
package is already fully resolved, the response will report zero created
records.

---

### Soft-Delete Package from Ticket

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/exclude
```

Soft-delete a package and all its tracks and products from the ticket.
Creates `TicketEvent` records for each affected element. See
[Soft-Deletion](#soft-deletion) for the full behavior.

**Response** (200 OK):

```json
{
  "data": {
    "package_name": "openssl-3",
    "tracks_excluded": 3,
    "products_excluded": 7
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
| 422 | `ALREADY_EXCLUDED` | Package is already soft-deleted |

---

### Restore Package

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/restore
```

Restore a soft-deleted package and all its tracks and products. Calls
`add_package_to_ticket` to add any new tracks/products from SMELT.
Creates `TicketEvent` records for each restored element. See
[Soft-Deletion — Restore](#restore).

**Response** (200 OK):

```json
{
  "data": {
    "package_name": "openssl-3",
    "tracks_restored": 3,
    "products_restored": 7,
    "new_tracks_added": 1,
    "new_products_added": 2
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
| 422 | `NOT_EXCLUDED` | Package is not soft-deleted |

---

### Soft-Delete Track

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/exclude
```

Soft-delete a track and all its products. Creates `TicketEvent` records
for the track and each product.

**Response** (200 OK):

```json
{
  "data": {
    "reference": "SUSE:SLE-15-SP6:Update",
    "products_excluded": 3
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
| 422 | `ALREADY_EXCLUDED` | Track is already soft-deleted |

---

### Restore Track

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/restore
```

Restore a soft-deleted track and all its products. Calls
`add_package_to_ticket` to add any new tracks/products from SMELT.

**Response** (200 OK):

```json
{
  "data": {
    "reference": "SUSE:SLE-15-SP6:Update",
    "products_restored": 3,
    "new_products_added": 1
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
| 422 | `NOT_EXCLUDED` | Track is not soft-deleted |

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
| 422 | `ALREADY_EXCLUDED` | Product is already soft-deleted |

---

### Restore Product

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}/restore
```

Restore a soft-deleted product. Calls `add_package_to_ticket` to add any
new tracks/products from SMELT.

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
| 422 | `NOT_EXCLUDED` | Product is not soft-deleted |

---

### Change Track Status

```
PATCH /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}
```

Change the affectedness status of a track. Triggers status propagation
to all active child products (with eligibility evaluation for
"Affected"), TicketEvent creation, and ticket status re-evaluation — all
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
Triggers TicketEvent creation and ticket status re-evaluation via
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

When the ticket has soft-deleted records, a discrete indicator is shown
(e.g., "3 excluded items"). Clicking it opens a panel showing:

- Each excluded item with its **current state** (updated in real-time)
- Who excluded it (username from `deleted_by`)
- When it was excluded (from `TicketEvent` timestamp)
- A "Restore" button for each item

---

## Background Tasks

- `sync_smelt_products`: periodic task to sync products and their
  repositories from SMELT `GET /api/v1/basic/products/`. Iterates all
  pages. Products no longer reported by SMELT are marked
  `active = false`.
- `sync_aimaas_lifecycle`: periodic task to sync product lifecycle data
  (`fcs`, `end_of_gs`, `end_of_ltss`, `end_of_espos`,
  `end_of_reactive_ltss`) from AIMAAS. Matches to local products via
  CPE.
- `sync_aimaas_thresholds`: periodic task to sync CVSS thresholds from
  AIMAAS `GET /api/entity/cvss-threshold`. When thresholds change,
  re-evaluates eligibility for active tickets.
- `check_codestream_releases`: periodic task (every 24 hours at 02:00
  UTC via Celery Beat) that invokes the `CodestreamReleaseDetector`
  service. Serves as a catch-up mechanism for events missed by the
  real-time `IBSEventConsumer` (see
  `docs/features/integrations/ibs-rabbitmq-integration.md`). See
  `docs/features/packages/ibs-codestream-release-detection.md` for the
  full procedure. When a release is detected, sets
  `TicketPackageTrack.delivery_status = RELEASED` and
  `TicketPackageTrack.status = FIXED`.
- `check_product_releases`: periodic task that invokes the
  `ProductReleaseDetector` (`updateinfo.xml`-based) for
  `TicketPackageProduct` records and sets `released_at`. See
  `docs/features/packages/ibs-product-release-detection.md` for the
  full procedure. Frequency and scope are TBD.
- `create_ticket_from_detection`: on-demand task enqueued by the
  `CodestreamReleaseDetector` or the `IBSEventConsumer` when a CVE fix
  is detected for a CVE that has no ticket in Sentinel. Fetches CVE
  data from NVD, creates the ticket, resolves packages via SMELT, and
  sets the originating track to `FIXED` with
  `delivery_status = RELEASED`. See
  `docs/features/packages/ibs-codestream-release-detection.md` (Case C)
  for details.
- `check_lifecycle_phase_transitions`: periodic task (daily at 04:00
  UTC) that detects products currently in Reactive LTSS or EOL phase
  with actionable `TicketPackageProduct` records and enqueues
  re-evaluation. With the new model: Reactive LTSS sets
  `eligible = false` (status stays `AFFECTED`); EOL with `AFFECTED`
  status removes the product (soft-delete with system TicketEvent); EOL
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
- SMELT and AIMAAS credentials are stored as environment variables,
  never in code

---

## Migration Path

When this redesign is implemented, the following data migration is
required:

| Current state | New state |
|---------------|-----------|
| `TicketPackageCodestream` records | Create `TicketPackage` per unique `(ticket_id, package_name)`, then create `TicketPackageTrack` with `workflow_type = 'ibs'`, `reference = codestream_name` |
| `TicketPackageCodestream.status = RELEASED` | `TicketPackageTrack.status = FIXED`, `delivery_status = RELEASED` |
| `TicketPackageCodestream.status = AFFECTED_RESOLVED` | `TicketPackageTrack.status = AFFECTED` (rollup is eliminated) |
| `TicketPackageCodestream.status = IGNORED` | Soft-delete the `TicketPackageTrack` (set `deleted_by` to a system migration user) |
| `TicketPackageProduct.status = RELEASED` | `TicketPackageProduct.status = FIXED`, `released_at` preserved |
| `TicketPackageProduct.status = AFFECTED_RESOLVED` | `TicketPackageProduct.status = AFFECTED`, `eligible = false` |
| `TicketPackageProduct.status = IGNORED` | Soft-delete the `TicketPackageProduct` |
| `TicketPackageProduct.is_override = true` | `is_status_override = true`, `is_eligible_override = false` (override was always on status) |
| `TicketPackageProduct.is_override = false` | `is_status_override = false`, `is_eligible_override = false` |
| `CodestreamPackageChecksum.codestream_name` | No change (this table is keyed by the codestream project name, which is now stored as `TicketPackageTrack.reference`) |
| `SubmissionRequest.codestream_name` | No change (SR/RR tables reference the track by codestream name string) |
| `SubmissionRequestCodestream.ticket_package_codestream_id` | Rename FK to `ticket_package_track_id` |

---

## Specs Impacted

When this design is finalized, the following documents need updating:

| Document | Change |
|----------|--------|
| `docs/data-model.md` | Remove TicketPackageCodestream, add TicketPackage + TicketPackageTrack. Update PackageStatus enum (remove AFFECTED_RESOLVED, IGNORED, RELEASED; add FIXED). Add DeliveryStatus enum. Add WorkflowType enum. Update TicketPackageProduct (add eligible, is_status_override, is_eligible_override, deleted_by; rename is_override). Add deleted_by to all three entities. Update TicketEventType (rename codestream_* to track_*, add exclude/restore events). |
| `docs/features/packages/package-tracking.md` | Replaced by this document |
| `docs/features/packages/ibs-codestream-release-detection.md` | Update entity references (TicketPackageCodestream → TicketPackageTrack), update status changes (RELEASED → FIXED + delivery_status = RELEASED), update protected state (WONT_FIX only, not IGNORED) |
| `docs/features/packages/ibs-product-release-detection.md` | Update entity references, update status changes (RELEASED → set released_at), update protected state |
| `docs/features/integrations/ibs-rabbitmq-integration.md` | Update entity references, update status changes |
| `docs/features/packages/ibs-submission-tracking.md` | Reference TicketPackageTrack, update SR/RR → delivery_status mapping, document reconciliation phase |
| `docs/features/tickets/tickets.md` | Update gate definitions (new resolution gate), update ticket_mutations module (new record creation logic, no codestream rollup, eligibility as separate dimension), update orphan cleanup (TicketPackage level). Rename codestream references to track |
| `docs/features/tickets/ticket-history.md` | Update event types (rename codestream_* to track_*, add exclude/restore events, add product_eligibility_changed) |
| `docs/features/tickets/cvss-scoring.md` | Update recalculation cascade — flips `eligible` flag instead of status AFFECTED ↔ AFFECTED_RESOLVED |
| `docs/features/packages/product-lifecycle-transitions.md` | EOL: soft-delete AFFECTED products instead of transitioning to AFFECTED_RESOLVED. Reactive LTSS: set eligible=false instead of status change |
| `docs/architecture.md` | Update data flow sections |
| `docs/features/ui/pages/ticket-detail.md` | Update package section (track terminology, delivery badge, eligibility indicator, excluded items panel) |
| `docs/features/packages/package-bugowner.md` | Join moves from TicketPackageCodestream.package_name to TicketPackage.package_name |

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

---

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error
  codes, pagination, shared 422 responses)
- `docs/features/tickets/tickets.md` — ticket lifecycle, status gates,
  ticket mutations module
- `docs/features/tickets/cvss-scoring.md` — CVSS resolution cascade,
  eligibility threshold comparison
- `docs/features/tickets/ticket-history.md` — TicketEvent field mapping
- `docs/features/packages/ibs-codestream-release-detection.md` — IBS
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
