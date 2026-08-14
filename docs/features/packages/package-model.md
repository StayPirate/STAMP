# Package Model

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

Each dimension is independently computable — its value depends only on
its own inputs, never on the current state of another dimension.
Status propagation does not affect eligibility computation, eligibility
never changes the status label, and delivery tracking is fully
independent from both. The three dimensions are combined only at
observation points (the Resolved gate, the anomaly matrix, and
presentation views) — never during computation or mutation.

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
its own override mechanism (`is_eligible_override`). The track retains
its affectedness status regardless of whether any product is eligible.
CVSS score changes only flip the `eligible` flag — no status changes,
no rollup chains.

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
Disalignment risk is mitigated by `package_service` and the
`SyncIbsRequests` reconciliation phase (see
[Delivery Reconciliation](#delivery-reconciliation)).

### 5. FIXED as a distinct affectedness state

`FIXED` distinguishes "was vulnerable, now remediated" from "was never
vulnerable" (`NOT_AFFECTED`). Both mean the code is not currently
vulnerable, but they carry different history and workload implications.

- `FIXED` is system-managed — set only by track release detection (MD5
  match — see `docs/features/packages/ibs-track-release-detection.md`)
  or via the admin escape hatch (`admin_ticket_ops` capability)
- The VA can change `FIXED` back to `AFFECTED` if the fix is insufficient
- No `is_status_override` flag is needed on tracks — the VA has direct
  control over non-FIXED target statuses

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
| `package_name` | VARCHAR(255) | NOT NULL | Source package name |
| `deleted_at` | TIMESTAMPTZ | nullable | Soft-deletion timestamp. NULL = active |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Record creation timestamp |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Record update timestamp |

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
| `workflow_type` | VARCHAR(20) | NOT NULL | `ibs` or `git` |
| `reference` | VARCHAR(255) | NOT NULL | Track identifier: IBS codestream name or git branch name |
| `status` | VARCHAR(20) | NOT NULL, DEFAULT ANALYSIS | Affectedness status |
| `delivery_status` | VARCHAR(20) | NOT NULL, DEFAULT PENDING | Delivery pipeline status |
| `deleted_at` | TIMESTAMPTZ | nullable | Soft-deletion timestamp. NULL = active |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Record creation timestamp |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Record update timestamp |

**Unique constraint**: `(ticket_package_id, reference)`

The track is identified by `reference` (a string), not by a foreign key.
Tracks are not maintained as a separate table — they are discovered
per-package via the SMELT `maintainedpackage` endpoint.

### TicketPackageProduct

Records the eligibility and release confirmation of a source package for
a specific product, within the context of a ticket and track.
Affectedness is determined exclusively at the track level (see
[Axis 1](#axis-1-affectedness-per-track)). Products track only
eligibility and delivery confirmation.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Internal identifier |
| `ticket_package_track_id` | UUID | FK(ticket_package_track.id), NOT NULL | Parent track record |
| `product_id` | UUID | FK(product.id), NOT NULL | Related product |
| `eligible` | BOOLEAN | NOT NULL, DEFAULT true | Effective eligibility |
| `is_eligible_override` | BOOLEAN | NOT NULL, DEFAULT false | True if VA has manually set the eligibility |
| `released_at` | TIMESTAMPTZ | nullable | When Sentinel detected the fix in the product's update repository |
| `deleted_at` | TIMESTAMPTZ | nullable | Soft-deletion timestamp. NULL = active |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Record creation timestamp |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Record update timestamp |

**Unique constraint**: `(ticket_package_track_id, product_id)`

### Enums

See `docs/data-model.md` for the full definitions of `PackageStatus`,
`DeliveryStatus`, and `WorkflowType` enums (values and descriptions).
The semantic meaning of each value in the context of package tracking is
described in [Three Orthogonal Dimensions](#three-orthogonal-dimensions)
below.

---

## Three Orthogonal Dimensions

The package tracking model separates three independent dimensions:

### Axis 1: Affectedness (per track)

Property of the source code relative to the CVE. Determined by the VA
during analysis, or automatically by track release detection.
Affectedness depends only on whether the source code contains the
vulnerability — it is independent of CVSS thresholds, product
lifecycle phase, and delivery pipeline state.

| State | Meaning |
|-------|---------|
| `ANALYSIS` | Not yet determined |
| `AFFECTED` | Code is vulnerable, fix needed |
| `NOT_AFFECTED` | Code was never vulnerable to this CVE |
| `FIXED` | Code was vulnerable, fix has been applied |
| `WONT_FIX` | Code is vulnerable, decision not to fix |

**Status classification**: statuses are classified as either *final* or
*non-final*. A final status indicates that no further work is expected on
the track for this ticket. A non-final status indicates that the track
still requires attention (analysis pending or fix in progress).

- **Final statuses**: `NOT_AFFECTED`, `FIXED`, `WONT_FIX`
- **Non-final statuses**: `ANALYSIS`, `AFFECTED`

Other specifications that reference "final status" or "non-final status"
use this classification as defined here.

The VA sets affectedness at the **track level**. Products do not have
their own affectedness status — they inherit the track's affectedness
implicitly through the hierarchy.

### Axis 2: Eligibility (per product only)

Property of the product relative to the CVE. Determined purely by CVSS
score vs. product threshold and product lifecycle phase. Eligibility
does not logically depend on whether the code is vulnerable — it
answers "does this product meet the criteria for receiving a fix?"
regardless of the current affectedness status.

| Eligible | Meaning |
|----------|---------|
| `true` | Product meets the CVSS threshold and lifecycle criteria for receiving the update |
| `false` | Product does not meet the criteria (CVSS below threshold or Reactive LTSS phase) |

Eligibility is evaluated for all products regardless of affectedness
status. It represents whether the product meets the CVSS threshold and
lifecycle criteria for receiving a fix.

The database default is `true` (conservative toward fix delivery — a
missing calculation results in a visible product rather than a silently
hidden one, consistent with the CVSS 10.0 fallback principle). If
eligibility calculation is skipped due to a bug, falsely-eligible
products block ticket resolution (the Resolved gate requires
`released_at IS NOT NULL` for all `eligible = true` products under
FIXED tracks). This is the intended safety net — blocked resolution is
visible and correctable; silent omission of eligible products is not.

**Eligibility rules** (evaluated in order):

1. **Reactive LTSS override**: if the product is currently in the
   Reactive LTSS phase (`end_of_ltss < today < end_of_reactive_ltss`),
   `eligible = false` regardless of CVSS score.
2. **Check CVSS threshold**: look up the product's `cvss_threshold` from
   AIMAAS. If no entry exists, the threshold is implicitly 0 (all CVEs
   eligible).
3. **Resolve the CVSS score**: via the Eligibility Score Resolution (see
   `docs/features/tickets/cvss-scoring.md`). Only the SUSE assessment of
   the system-wide default CVSS version is used — no fallback to other
   providers or other versions:
   - SUSE assessment of the default version present → use this score
   - Not resolvable (ticket has no CVE, CVE has no SUSE assessment for
     the default version, or SUSE has not scored the default version) →
     treat as **10.0** (worst-case; the product is always eligible)
4. **Apply threshold**: if the resolved CVSS score is below the product's
   threshold, `eligible = false`. Otherwise, `eligible = true`.

**Important**: the CVSS version used for threshold comparison MUST always
be resolved from the system-wide default CVSS version configuration —
never hardcoded. See `docs/features/tickets/cvss-scoring.md` and
`docs/features/platform/system-settings.md`.

**Override model**: the VA can override eligibility on individual products
by setting `is_eligible_override = true`. When overridden, automatic
eligibility recalculation skips the product.

### Axis 3: Delivery (per track)

Factual observation of the fix's progress through the SUSE maintenance
pipeline (SR/incident/RR lifecycle at the track level, `updateinfo.xml`
advisory detection at the product level). Delivery tracking records
what happened in IBS — it is independent of whether the code is
vulnerable (affectedness) or whether the product meets threshold
criteria (eligibility).

| State | Meaning | Condition |
|-------|---------|-----------|
| `PENDING` | No delivery action yet | No SR created |
| `IN_PROGRESS` | Fix in the pipeline | SR created, until RR accepted |
| `RELEASED` | Fix delivered to customers | RR accepted |

The delivery status is updated by the system when SR/RR state changes
are detected (via IBS RabbitMQ events or the `SyncIbsRequests`
catch-up).

The two axes are independent — see
[Affectedness-Delivery Independence](#affectedness-delivery-independence).

#### Delivery Relevance Indicator

The `delivery_status` column on `TicketPackageTrack` always contains the
real system value (`PENDING`, `IN_PROGRESS`, or `RELEASED`). However,
`PENDING` is the system default and carries no operational meaning when
the track's affectedness status does not imply that a fix is expected.
For example, a track with `NOT_AFFECTED + PENDING` simply means no SR
was ever created — the `PENDING` value is noise, not a signal.

To help API consumers distinguish meaningful delivery states from default
noise, API responses for tracks include a computed boolean field
`delivery_relevant`:

```python
delivery_relevant = (
    track.status in ("ANALYSIS", "AFFECTED")
    or track.delivery_status != "PENDING"
)
```

Rules:

- `delivery_relevant = true`: the delivery status has operational
  meaning. Either the track is still being analyzed / is affected (so
  delivery progress matters), or the delivery pipeline has moved beyond
  the default (an SR exists or the fix was released).
- `delivery_relevant = false`: the delivery status is the system default
  (`PENDING`) on a track with a final affectedness status
  (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`) where no delivery activity was
  ever detected. Consumers SHOULD treat the delivery status as noise —
  do not display it or act on it.

When `delivery_relevant = true` **and** the affectedness is
`NOT_AFFECTED` or `WONT_FIX`, the combination is anomalous: it signals
that delivery activity exists for a track that was assessed as not
requiring a fix. `FIXED` with delivery activity (`IN_PROGRESS` or
`RELEASED`) is not anomalous — it is the expected progression of a fix.
See [Anomaly Detection](#anomaly-detection-future-review-queue).

**Important**: `delivery_relevant` is a **computed API field only** — it
is not a database column. It is derived from `status` and
`delivery_status` at serialization time in the Pydantic response schema.
The database schema is not affected.

**OpenAPI documentation**: since external consumers will discover
`delivery_relevant` and `delivery_status` through the generated OpenAPI
documentation (not through internal specs), the Pydantic response schema
MUST include `Field(description=...)` on both fields that conveys the
consumer-facing guidance:

- `delivery_status`: explain that `PENDING` is the system default and
  does not imply a fix is expected; reference `delivery_relevant` for
  operational significance
- `delivery_relevant`: explain that when `false`, consumers should not
  display `delivery_status` or make decisions based on it

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
        |
        v
Fix lands in track (package.commit)      --> affectedness: FIXED (automatic, via MD5 detection)
        |
        v
Fix lands in eligible products (updateinfo.xml) --> released_at set
```

The two axes are managed independently:
- `delivery_status` transitions to `RELEASED` when the RR is accepted
  (via IBS submission tracking)
- `status` transitions to `FIXED` when track release detection confirms
  the fix in the codestream (MD5 match)

In practice, the RR acceptance and the package appearing in the
codestream happen nearly simultaneously, but Sentinel detects them
through independent mechanisms.

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

The `SyncIbsRequests` (daily at 02:30 UTC) includes a reconciliation
phase after its primary catch-up of missed SR/RR events. For every IBS
track (`workflow_type = 'ibs'`) in active tickets with
`delivery_status != RELEASED`, it verifies that the persisted
`delivery_status` is consistent with the current state of the SR/RR data
in IBS. If a disalignment is found, the `delivery_status` is corrected.

This reconciliation applies only to IBS tracks. Git tracks will have
their own delivery detection and reconciliation mechanism (TBD).

---

## Affectedness-Delivery Independence

The affectedness status and the delivery status are tracked as
independent axes. Neither resets nor constrains the other. All
combinations are valid system states. The `delivery_relevant` field
indicates whether the delivery status carries operational meaning in the
context of the current affectedness — see
[Delivery Relevance Indicator](#delivery-relevance-indicator).

| Affectedness | Delivery | Relevant | Anomaly | Meaning |
|-------------|----------|----------|---------|---------|
| `ANALYSIS` | `PENDING` | Yes | | Not yet analyzed, no SR |
| `ANALYSIS` | `IN_PROGRESS` | Yes | | SR exists before VA analyzed the track |
| `ANALYSIS` | `RELEASED` | Yes | | Fix released before VA analyzed the track |
| `AFFECTED` | `PENDING` | Yes | | Affected, fix expected, no SR yet |
| `AFFECTED` | `IN_PROGRESS` | Yes | | Fix in the pipeline |
| `AFFECTED` | `RELEASED` | Yes | Yes | Fix released but VA considers it insufficient — needs review |
| `NOT_AFFECTED` | `PENDING` | **No** | | Not affected; delivery is the system default — not meaningful |
| `NOT_AFFECTED` | `IN_PROGRESS` | Yes | Yes | SR in progress for unaffected code — possible confusion |
| `NOT_AFFECTED` | `RELEASED` | Yes | Yes | Fix released for unaffected code — possible confusion |
| `FIXED` | `PENDING` | **No** | | Fix confirmed via track release detection; no SR submitted yet — delivery not meaningful |
| `FIXED` | `IN_PROGRESS` | Yes | | Fix confirmed, SR still in pipeline |
| `FIXED` | `RELEASED` | Yes | | Fix confirmed and delivered |
| `WONT_FIX` | `PENDING` | **No** | | Decided not to fix; delivery is the system default — not meaningful |
| `WONT_FIX` | `IN_PROGRESS` | Yes | Yes | SR in progress despite won't-fix decision — conflicting |
| `WONT_FIX` | `RELEASED` | Yes | Yes | Fix released despite won't-fix decision — conflicting |

### Anomaly Detection (future: Review Queue)

Anomalous combinations (marked in the table above) indicate situations
that require VA attention — a possible bug, a maintainer not following
the workflow, or an outdated VA assessment. Note that all anomalous
combinations have `delivery_relevant = true` by definition: if delivery
has moved beyond the default, it is always relevant regardless of
affectedness. Conversely, the three `delivery_relevant = false`
combinations (`NOT_AFFECTED + PENDING`, `FIXED + PENDING`,
`WONT_FIX + PENDING`) are never anomalous — they are simply the system
default with no delivery activity.

These anomalous combinations are destined to be integrated into the
future **Review Queue** — a mechanism that will automatically tag
tickets presenting anomalies, making them visible to VAs for review.
The specification of the Review Queue and the tagging mechanism will be
defined in a dedicated specification.

---

## Status Behavior

All track status changes and product eligibility overrides described in
this section MUST go through the `package_service` module (see
`docs/features/packages/package-service.md`), which ensures automatic
ticket status re-evaluation after each change.

### VA Sets a Status on a Track

1. Track status is set to the chosen value (via `package_service`)
2. A `TicketAuditEvent` (`track_status_changed`) is created
3. Ticket status is re-evaluated via `reconcile_ticket_status()`

There is **no codestream eligibility rollup** — the track retains its
affectedness status regardless of whether any product is eligible. The
question "is there work to do on this track?" is answered by checking
whether any active product under it has `eligible = true`.

### VA Overrides Product Eligibility

1. Product `eligible` is set to the chosen value
2. `is_eligible_override` is set to `true`
3. The track status is not affected

### Automatic Transitions

| From | To | Applies to | Trigger |
|------|----|------------|---------|
| `AFFECTED` or `ANALYSIS` | `FIXED` | TicketPackageTrack | Track release detection (MD5 match confirms fix in codestream) |

Records in a final status (`NOT_AFFECTED`, `FIXED`, `WONT_FIX`) are not
eligible as source states for automatic transitions.

**Delivery status transitions** (system-managed):

| From | To | Trigger |
|------|----|---------|
| `PENDING` | `IN_PROGRESS` | SR created (state `open` or `accepted`) for the track |
| `IN_PROGRESS` | `RELEASED` | RR accepted for the track |
| `IN_PROGRESS` | `PENDING` | No SR linked to this track remains in `open` or `accepted` state |

#### Delivery status regression

`delivery_status` can regress from `IN_PROGRESS` back to `PENDING` when
no submission request linked to the track (via `SubmissionRequestTrack`)
remains in `open` or `accepted` state. In practice this means all SRs
are in `revoked` (final), `declined` (non-final — can be reopened), or
`superseded` (final — but a superseding SR inherits the delivery role;
see below). This signals to the VA that the maintainer's submission
attempt has failed and a new submission is needed.

Specific SR state change effects on `delivery_status`:

- **SR `revoked` or `declined`**: evaluate whether any other SR linked to
  the track is still in `open` or `accepted` state. If none remains,
  regress `delivery_status` to `PENDING`
- **SR `superseded`**: no regression — a superseding SR already exists and
  inherits the delivery role
- **RR `revoked` or `declined`**: no regression — `delivery_status` remains
  `IN_PROGRESS`. The RR failure does not invalidate the accepted SR /
  incident; a new RR can be created from the same incident

#### RELEASED is irreversible

Once `delivery_status` reaches `RELEASED`, it cannot regress. This is
guaranteed by the IBS model: `accepted` is a final state for release
requests, so a released RR cannot be revoked or declined.

These transitions are detected via IBS RabbitMQ events
(`suse.obs.request.create`, `suse.obs.request.state_change`) and the
`SyncIbsRequests` catch-up mechanism. See
`docs/features/packages/ibs-submission-tracking.md`.

### Manual Transitions

The VA can manually change the affectedness status of any track to
`ANALYSIS`, `AFFECTED`, `NOT_AFFECTED`, or `WONT_FIX`. The VA cannot
set a track to `FIXED` — this status is system-managed (set only by
track release detection or via the admin escape hatch with
`admin_ticket_ops` capability). The VA cannot manually change the
delivery status — it is system-managed.

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
`docs/features/packages/package-service.md`, Orphan Cleanup Invariants)
apply upward: the parent is also soft-deleted. See
also `docs/features/packages/product-lifecycle-transitions.md` for the
EOL-triggered chain.

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

- Eligibility recalculation (CVSS/threshold/lifecycle changes)
- Delivery status updates (SR/RR state changes)
- Release detection (codestream and product level)

These continued updates apply only to records under **active tickets**
(status `New`, `Analysis`, or `Analyzed` — see
`docs/features/tickets/tickets.md`). Records under inactive tickets
(`Resolved`, `Ignored`, `Duplicated`) are not subject to automated
processing regardless of their soft-deletion status.

This means the state of a soft-deleted record always reflects the
current reality, not the state at the time of deletion.

### Exclusion from System Operations

Soft-deleted records are excluded from:

- **Default views** — excluded from default ticket responses (requires `include_deleted` parameter)
- **Ticket resolution gate** — not considered when evaluating the
  per-track resolution-complete predicate (see
  `docs/features/tickets/tickets.md`, "Gate: Analyzed → Resolved")
- **Anomaly detection** — not flagged in the future Review Queue
- **Analyzed gate** — not considered when evaluating Analysis → Analyzed

### Restore

Restore operates **only on the directly excluded record** — there is no
propagation to child records. The VA can only restore a record that has its
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
function. Internal callers (CVE ingestion, release detection) call the
function directly and benefit from the automatic exclusion via hierarchy.

### Ticket Events for Soft-Deletion

A single `TicketAuditEvent` is created for each soft-deletion or restore
operation — only for the **directly affected record**. Child records
that become effectively excluded via the hierarchy do not generate
separate events.

When a VA soft-deletes a track, 1 event is created (`track_excluded`).
Products under the track are implicitly excluded via the hierarchy but
do not produce individual events.

**Upward chain (orphan cleanup)**: when orphan cleanup chains upward
(e.g., deleting the last product triggers track deletion, which may
trigger package deletion), each record soft-deleted by orphan cleanup
generates its **own** `TicketAuditEvent` with the appropriate event type
(`track_excluded`, `package_excluded`). These orphan cleanup audit events use
`user_id = NULL` to indicate they are system-triggered (not directly
requested by the VA). The total number of `TicketAuditEvent` records
created by a soft-delete operation is `1 + len(orphan_cleanup)`: one for the
directly excluded record (with the VA's `user_id`) plus one for each
ancestor chained by orphan cleanup (with `user_id = NULL`). Ticket
status re-evaluation occurs after each chain step (up to 3 times
for a full product → track → package chain — see
`docs/features/packages/package-service.md`, Chain Composition).

| Action | `event_type` | `user_id` | Details recorded |
|--------|-------------|-----------|------------------|
| VA soft-deletes a package | `package_excluded` | VA user | `package_name` |
| VA soft-deletes a track | `track_excluded` | VA user | `track_name`, `package_name` |
| VA soft-deletes a product | `product_excluded` | VA user | `track_name`, `package_name`, `product_id` |
| VA restores a package | `package_restored` | VA user | `package_name` |
| VA restores a track | `track_restored` | VA user | `track_name`, `package_name` |
| VA restores a product | `product_restored` | VA user | `track_name`, `package_name`, `product_id` |

---

## Package Eligibility

Eligibility determines whether a product will receive a security update
for a given CVE. The eligibility computation rules, default value
rationale, and override model are defined in
[Axis 2: Eligibility](#axis-2-eligibility-per-product-only).

### Override Model

Product-level eligibility has an override mechanism:

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `eligible` | bool | calculated | Effective eligibility |
| `is_eligible_override` | bool | false | VA has manually set the eligibility |

When `is_eligible_override = false`, the system maintains `eligible`
automatically via CVSS threshold + lifecycle phase calculation. When
`is_eligible_override = true`, automatic recalculation skips the
product.

Eligibility changes go through `package_service` and trigger
`reconcile_ticket_status`.

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
   creation to `package_service` (if a record does not already exist,
   including soft-deleted).
5. For each resolved product under each track, delegate
   `TicketPackageProduct` record creation to `package_service` (if a
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

`package_service` handles idempotency (skipping existing records,
including soft-deleted), initial status determination, and eligibility
logic internally — see `docs/features/packages/package-service.md`.

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

1. **Automatic (CVE ingestion)**: when a CVE is ingested, Sentinel
   resolves package names from the CVE data (NVD CPE applicability
   statements, CNA/ADP CPE strings, CNA/ADP vendor:product pairs, or
   pre-resolved packages). For each resolved package name,
   `add_package_to_ticket` is called. See
   `docs/features/tickets/cve-service.md` (Phase 2).
2. **Manual**: the VA manually adds a package by name via the UI.
   `add_package_to_ticket` is called with the entered name.
3. **Track release detection (Case B)**: the release detector finds a
   CVE fix in a package that is not tracked in the ticket. It calls
   `add_package_to_ticket` to add all tracks and products, then sets
   the specific track where the fix was detected to `FIXED`. See
   `docs/features/packages/ibs-track-release-detection.md` (Case B).
4. **Ticket auto-creation (Case C)**: a CVE fix is detected for a CVE
   with no existing ticket. After creating the ticket,
   `add_package_to_ticket` is called, then the originating track is
   set to `FIXED`. See
   `docs/features/packages/ibs-track-release-detection.md` (Case C).
5. **Restore from soft-deletion**: restoring a package, track, or
   product clears its `deleted_at` only. New tracks/products that
   appeared on SMELT since the deletion are picked up by subsequent
   automatic calls to `add_package_to_ticket` (CVE ingestion, release
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
  (via the status dropdown) and override the eligibility of individual
  products.

### Removing a Package from a Ticket

When a VA removes a package from a ticket, Sentinel performs a
**soft-deletion** (see [Soft-Deletion](#soft-deletion)): `deleted_at`
is set on the `TicketPackage` record only. Child `TicketPackageTrack`
and `TicketPackageProduct` records are not modified — they become
effectively excluded via the hierarchy.

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
| Package auto-added (CVE ingestion or Case B) | `package_added` | `NULL` | `package_name`, contextual `comment` |
| VA soft-deletes package | `package_excluded` | VA user | `package_name` |
| VA soft-deletes track | `track_excluded` | VA user | `track_name`, `package_name` |
| VA soft-deletes product | `product_excluded` | VA user | `track_name`, `package_name`, `product_id` |
| VA restores package | `package_restored` | VA user | `package_name` |
| VA restores track | `track_restored` | VA user | `track_name`, `package_name` |
| VA restores product | `product_restored` | VA user | `track_name`, `package_name`, `product_id` |
| VA changes track status | `track_status_changed` | VA user | `track_name`, `package_name`, `old_status`, `new_status` |
| VA overrides product eligibility | `product_eligibility_changed` | VA user | `track_name`, `package_name`, `product_id`, `old_eligible`, `new_eligible` |
| Ticket created | `ticket_created` | `NULL` | Creation source description |
| Product release detected | `product_released` | `NULL` | `track_name`, `package_name`, `product_id`, `advisory_id` |
| Product eligibility recalculated | `product_eligibility_changed` | `NULL` | `track_name`, `package_name`, `product_id`, `old_eligible`, `new_eligible` |

- `user_id = NULL` indicates an automatic system action. For
  `package_added`, this distinguishes manual additions (VA user) from
  automatic ones (CVE ingestion, release detection). The `comment` field
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

- The track level updates `TicketPackageTrack.status` to `FIXED`
  (automatic) when track release detection confirms the fix in the
  codestream (MD5 match).
- The track level updates `TicketPackageTrack.delivery_status` to
  `RELEASED` when the Release Request (RR) for the track is accepted
  (via IBS submission tracking).
- The product level sets `TicketPackageProduct.released_at` when the
  fix appears in that specific product's update repository.

The track-level automatic transition applies only when the current
status is `AFFECTED` or `ANALYSIS` (see Automatic Transitions above).
The product-level `released_at` timestamp is set regardless of
affectedness status — it records a factual observation about the
product's update repository.

---

## Ticket Lifecycle Integration

All track status and product eligibility changes go through the
`package_service` module, which automatically reconciles ticket status
after each change. See `docs/features/tickets/tickets.md` (Ticket
Lifecycle) for the authoritative gate conditions and status transition
rules, and `docs/features/packages/package-service.md` for the module
contract, including:

- **Analysis → Analyzed**: requires at least one package, all tracks
  decided, severity set, SUSE CVSS provided
- **Analyzed → Resolved**: requires every non-excluded active track to
  be resolution-complete — either (a) `NOT_AFFECTED`/`WONT_FIX`, or
  (b) `FIXED` with all non-excluded eligible products released, or
  (c) `AFFECTED` with no non-excluded eligible products remaining
- Reverse transitions when gate conditions are no longer met

---

## Workflow-Agnostic vs Workflow-Specific

The following concerns are identical regardless of `workflow_type`:

- `PackageStatus` enum and all valid transitions
- `DeliveryStatus` enum (the delivery concept exists for both workflows)
- Final-status immunity (records in `NOT_AFFECTED`, `FIXED`, or `WONT_FIX`
  are never modified by automatic transitions)
- `package_service` module — operates on `TicketPackageTrack` and
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
`package_service`, resolves the IBS bugowner, and enqueues submission
discovery. See [Adding Packages to a Ticket](#adding-packages-to-a-ticket)
for the full behavior.

**Request body**:

```json
{
  "package_name": "openssl-3"
}
```

| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `package_name` | string | Yes | Max 255 chars. Pattern: `^[a-zA-Z0-9][a-zA-Z0-9._+\-]{0,253}[a-zA-Z0-9]$` (min 2 chars). Only alphanumeric, dots, underscores, hyphens, and plus signs allowed. | Source package name |

The `package_name` value is URL-encoded before interpolation in the SMELT
API query (`GET /api/v1/basic/maintainedpackage/?package={url_encode(name)}`).
This prevents injection of URL control characters regardless of validation.

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

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Package exists on this ticket but is soft-deleted — use the restore endpoint |
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

After the soft-delete, the system reconciles ticket status via
`package_service`. This is necessary because excluding the package
changes the set of active records considered by ticket gates (Resolved
gate and Analyzed gate).

**Response** (200 OK):

```json
{
  "data": {
    "package_name": "openssl-3"
  }
}
```

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Package is already soft-deleted |

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

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 422 | `PACKAGE_NOT_EXCLUDED` | Package is not directly soft-deleted |
| 422 | `PACKAGE_RESTORE_BLOCKED` | Package has no active tracks with active products. Restore at least one track (with active products) first. |

---

### Soft-Delete Track

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/exclude
```

Soft-delete a track from the ticket. Sets `deleted_at` on the track
record only — products under it are not modified but become effectively
excluded via the hierarchy. Creates a `TicketAuditEvent` for the excluded
record. If orphan cleanup chains to ancestors, additional
system-triggered audit events are created (see
[Ticket Events for Soft-Deletion](#ticket-events-for-soft-deletion)).

After the soft-delete (and any orphan cleanup chain), the system
reconciles ticket status via `package_service`. This is necessary
because excluding a track changes the set of active records considered
by ticket gates (Resolved gate and Analyzed gate).

**Response** (200 OK):

```json
{
  "data": {
    "reference": "SUSE:SLE-15-SP6:Update",
    "orphan_cleanup": []
  }
}
```

When this is the last active track under the parent package, orphan cleanup
removes the package as well. In that case, `orphan_cleanup` contains the affected
ancestor:

```json
{
  "data": {
    "reference": "SUSE:SLE-15-SP6:Update",
    "orphan_cleanup": [
      {"level": "package", "package_name": "openssl-3"}
    ]
  }
}
```

`orphan_cleanup` is an array of ancestors that were automatically soft-deleted by
orphan cleanup. Empty array if no orphan cleanup occurred. Maximum 1 element for
track exclusion (the parent package). Each element identifies the level
(`"package"`) and the identifying field (`package_name`).

**Orphan cleanup behavior**: when exclusion leaves a parent record with no
remaining active children (`deleted_at IS NULL`), the parent is also
soft-deleted automatically. The `orphan_cleanup` array allows clients to update
their local tree state without a full refetch.

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Track is already soft-deleted |

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

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 422 | `PACKAGE_NOT_EXCLUDED` | Track is not directly soft-deleted |
| 422 | `PACKAGE_RESTORE_BLOCKED` | Track has no active products. Restore at least one product first. |

---

### Soft-Delete Product

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}/exclude
```

Soft-delete a single product from a track. Creates a `TicketAuditEvent`
for the excluded record. If orphan cleanup chains to ancestors,
additional system-triggered audit events are created (see
[Ticket Events for Soft-Deletion](#ticket-events-for-soft-deletion)).

After the soft-delete (and any orphan cleanup chain), the system
reconciles ticket status via `package_service`. This is necessary
because excluding a product changes the set of active records considered
by ticket gates (Resolved gate and Analyzed gate).

**Response** (200 OK):

```json
{
  "data": {
    "product_id": "uuid",
    "product_name": "SLES 15-SP6",
    "orphan_cleanup": []
  }
}
```

When orphan cleanup triggers (this was the last active product under the
parent track, and/or the last active track under the grandparent package):

```json
{
  "data": {
    "product_id": "uuid",
    "product_name": "SLES 15-SP6",
    "orphan_cleanup": [
      {"level": "track", "reference": "SUSE:SLE-15-SP6:Update"},
      {"level": "package", "package_name": "openssl-3"}
    ]
  }
}
```

`orphan_cleanup` is an array of ancestors automatically soft-deleted by orphan
cleanup, ordered from immediate parent upward. Empty array if no orphan cleanup
occurred. Maximum 2 elements for product exclusion (parent track, then
grandparent package). Each element identifies the level (`"track"` or
`"package"`) and the identifying field (`reference` for tracks,
`package_name` for packages).

**Orphan cleanup behavior**: when exclusion leaves a parent record with no
remaining active children (`deleted_at IS NULL`), the parent is also
soft-deleted automatically. The `orphan_cleanup` array allows clients to update
their local tree state without a full refetch.

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Product is already soft-deleted |

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

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 422 | `PACKAGE_NOT_EXCLUDED` | Product is not directly soft-deleted |

---

### Change Track Status

```
PATCH /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}
```

Change the affectedness status of a track. Triggers TicketAuditEvent
creation and ticket status re-evaluation via `package_service`.

**Request body**:

```json
{
  "status": "AFFECTED"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status value. Valid values: `ANALYSIS`, `AFFECTED`, `NOT_AFFECTED`, `FIXED`†, `WONT_FIX` |

† Setting `status` to `FIXED` requires the `admin_ticket_ops` capability
(Hard Conditional Check). Users with only `manage_packages` can set any
other status but not `FIXED`.

**Response** (200 OK):

```json
{
  "data": {
    "ticket_id": "uuid",
    "package_name": "openssl-3",
    "reference": "SUSE:SLE-15-SP6:Update",
    "status": "AFFECTED",
    "delivery_status": "PENDING",
    "delivery_relevant": true,
    "products": [
      {
        "product_id": "uuid",
        "product_name": "SLES 15 SP6",
        "eligible": true,
        "is_eligible_override": false
      },
      {
        "product_id": "uuid",
        "product_name": "SLES-LTSS 15-SP4",
        "eligible": false,
        "is_eligible_override": false
      }
    ]
  }
}
```

The response includes the updated track and all its active child
products with their current eligibility, allowing the client to update
the UI tree without a separate fetch.

**`Capability: manage_packages`** | **`†admin_ticket_ops`** (Hard
Conditional Check: required only when `status = FIXED`)

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package or track not found on this ticket |

---

### Override Product Eligibility

```
PATCH /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{product_id}
```

Override the eligibility of a specific product. Sets
`is_eligible_override = true`. Triggers TicketAuditEvent creation and
ticket status re-evaluation via `package_service`.

**Request body**:

```json
{
  "eligible": false
}
```

Reset eligibility override:

```json
{"eligible": null}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `eligible` | boolean \| null | Yes | Eligibility override value, or `null` to reset to automatic calculation |

#### Reset behavior

When `eligible` is sent as `null`, the override is removed and the value
reverts to automatic:

- **`eligible: null`** — sets `is_eligible_override = false`. Eligibility is
  immediately recalculated using the standard rules (CVSS threshold + lifecycle
  phase). The recalculation uses the Eligibility Score Resolution: only the
  SUSE assessment of the default CVSS version is considered. If not resolvable
  (including tickets without an associated CVE), the 10.0 fallback applies —
  making the product eligible unless the Reactive LTSS override applies.

Both override and reset operations follow the same post-modification flow:

1. A `TicketAuditEvent` (`product_eligibility_changed`) is created
2. A single ticket status re-evaluation is performed at the end of the
   transaction (via `package_service`)

This applies to all operations through this endpoint: setting an override,
changing an override value, and resetting an override.

**Response** (200 OK):

```json
{
  "data": {
    "ticket_id": "uuid",
    "package_name": "openssl-3",
    "reference": "SUSE:SLE-15-SP6:Update",
    "product_id": "uuid",
    "product_name": "SLES-LTSS 15-SP4",
    "eligible": false,
    "is_eligible_override": true
  }
}
```

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package or product not found on this ticket |

---

### List Ticket Packages

```
GET /api/v1/tickets/{ticket_id}/packages
```

Returns the complete package tree for a specific ticket — all packages,
tracks, and products including soft-deleted records (with `deleted_at`
visible on each level). Identical data to the `packages` field in
`TicketDetail` from `GET /api/v1/tickets/{ticket_id}`, but available as
a standalone endpoint for clients that only need package data.

| Aspect | Design |
|--------|--------|
| **`Access: Public`** | Consistent with `GET /api/v1/tickets/{ticket_id}` |
| **`Authentication: Optional`** | Resolves caller identity for ticket accessibility |
| **Guard** | `require_accessible_ticket` (404 for missing/confidential tickets) |
| **Pagination** | No — package count per ticket is bounded (typically 1-5, rarely >20) |
| **Envelope** | `{"data": [...]}` (unpaginated list) |
| **Soft-deleted records** | All package/track/product records are returned (including soft-deleted), with `deleted_at` visible on each — identical to `TicketDetail.packages` behavior |
| **Response schema** | `PackageDetail[]` — reuses the existing schema (full tree: package -> tracks -> products) |
| **Sorting** | Fixed alphabetical order by `package_name`. Client-controlled sorting (`sort_by`/`sort_order`) is not supported — the dataset has bounded cardinality and fixed ordering provides consistent display without configuration overhead. |
| **Delegation** | Delegates to `package_service.get_ticket_packages()` |

**Response** (200 OK):

```json
{
  "data": [...]
}
```

The response body is a `PackageDetail[]` array — the same schema used in
`TicketDetail.packages`.

---

### Search Packages Across Tickets

```
GET /api/v1/packages
```

Search and list packages across all tickets. Each result represents a
single `TicketPackage` record — i.e., one `(package_name, ticket)` pair.
If the same source package is tracked in multiple tickets, it appears
once per ticket in the results.

| Aspect | Design |
|--------|--------|
| **`Access: Public`** | Consistent with `GET /api/v1/tickets` |
| **`Authentication: Optional`** | Resolves caller identity for confidentiality filtering |
| **Confidentiality** | Packages belonging to confidential tickets are excluded for unauthorized callers (same filter as `GET /api/v1/tickets`). The endpoint handler constructs `confidential_ticket_filter()` and passes it to `search_packages(confidentiality_filter=...)` |
| **Soft-deleted packages** | Always excluded — soft-deleted `TicketPackage` records (`deleted_at IS NOT NULL`) are never returned |
| **Pagination** | Yes — `page` (default 1), `per_page` (default 20, max 100) |
| **Envelope** | `{"data": [...], "meta": {"total": N, "page": P, "per_page": PP}}` |
| **Delegation** | Delegates to `package_service.search_packages()` |

#### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `search` | string | Substring match on `package_name` (case-insensitive, equivalent to SQL ILIKE `%term%`). Max 500 chars |
| `name` | string | Exact match on `package_name`. Max 500 chars |
| `ticket_status` | string (repeatable) | Ticket statuses to include: `new`, `analysis`, `analyzed`, `resolved`, `ignored`, `duplicated`. Repeatable — multiple values are specified as separate query parameters (e.g., `?ticket_status=new&ticket_status=analysis`). Invalid values are silently ignored per `api-spec.md` (Enum Filter Validation). If all values are invalid, an empty result set is returned. Default: no filter (all statuses) |
| `sort_by` | string | `package_name` or `created_at` (default: `created_at`). Refers to `TicketPackage.created_at` (the date the package was added to the ticket), not `Ticket.created_at`. Deterministic tiebreaker per `docs/api-spec.md` (Deterministic Pagination Ordering) |
| `sort_order` | string | `asc` or `desc` (default: `desc`) |
| `page` | integer | Page number (default: 1, min: 1) |
| `per_page` | integer | Items per page (default: 20, min: 1, max: 100) |

`search` and `name` are mutually exclusive. If both are provided,
return 422 `VALIDATION_ERROR`.

Pagination constraints follow the standard rule in `docs/api-spec.md`
(Pagination).

**Naming note**: the parameter is named `ticket_status` (not `status`)
to disambiguate from package-level statuses visible in `track_summary`.
On `GET /api/v1/tickets`, `status` is unambiguous because the resource
itself is a ticket.

#### Response Schema: `PackageListItem`

```json
{
  "id": "uuid",
  "package_name": "openssl-3",
  "ticket": {
    "id": "uuid",
    "identifier": "SNTL-123",
    "status": "analysis",
    "severity": "high"
  },
  "track_summary": {
    "total": 5,
    "affected": 2,
    "fixed": 1,
    "not_affected": 1,
    "wont_fix": 0,
    "analysis": 1
  },
  "created_at": "2026-05-15T10:30:00Z",
  "updated_at": "2026-05-16T08:00:00Z"
}
```

**`ticket`** (`TicketPackageRef`) — lightweight ticket reference:

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Ticket ID |
| `identifier` | string | Human-readable identifier (e.g., `SNTL-123`) |
| `status` | string | Current ticket status |
| `severity` | string \| null | Ticket severity |

**`track_summary`** (`TrackSummary`) — aggregated track status counts
for the package within this ticket. Counts only active tracks
(`deleted_at IS NULL`):

| Field | Type | Description |
|-------|------|-------------|
| `total` | integer | Total active tracks |
| `affected` | integer | Tracks with status `AFFECTED` |
| `fixed` | integer | Tracks with status `FIXED` |
| `not_affected` | integer | Tracks with status `NOT_AFFECTED` |
| `wont_fix` | integer | Tracks with status `WONT_FIX` |
| `analysis` | integer | Tracks with status `ANALYSIS` |

---

---

## Background Tasks

Product sync tasks (`sync_smelt_products`, `sync_aimaas_lifecycle`,
`sync_aimaas_thresholds`) are specified in
`docs/features/packages/product-catalog.md` (Background Tasks).

- `detect_ibs_track_releases`: periodic task (every 24 hours at 02:00
  UTC via Celery Beat) that invokes the `IBSTrackReleaseDetector`
  service. Serves as a catch-up mechanism for events missed by the
  real-time `IBSEventConsumer` (see
  `docs/features/integrations/ibs-rabbitmq-integration.md`). See
  `docs/features/packages/ibs-track-release-detection.md` for the
  full procedure. When a release is detected, sets
  `TicketPackageTrack.status = FIXED`.
- `detect_ibs_product_releases`: periodic task that invokes the
  `ProductReleaseDetector` (`updateinfo.xml`-based) for
  `TicketPackageProduct` records and sets `released_at`. See
  `docs/features/packages/ibs-product-release-detection.md` for the
  full procedure. Frequency and scope are TBD.
- `create_ticket_from_detection`: on-demand task enqueued by the
  `IBSTrackReleaseDetector` or the `IBSEventConsumer` when a CVE fix
  is detected for a CVE that has no ticket in Sentinel. Fetches CVE
  data from NVD, creates the ticket, resolves packages via SMELT, and
  sets the originating track to `FIXED`. See
  `docs/features/packages/ibs-track-release-detection.md` (Case C)
  for details.
- `evaluate_lifecycle_transitions`: periodic task (daily at 04:00
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
  `manage_packages` capability
- Changing track status or product eligibility requires the
  `manage_packages` capability
- Viewing affectedness data is publicly accessible (no authentication
  required):
  - `GET /api/v1/tickets/{ticket_id}/packages` — subject to
    `require_accessible_ticket` (confidentiality check)
  - `GET /api/v1/packages` — packages belonging to confidential tickets
    are excluded for unauthorized callers via
    `confidential_ticket_filter()`

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
- `docs/features/packages/package-service.md` — package-centric
  mutations, orchestration, and query operations
- `docs/features/packages/product-catalog.md` — product catalog, SMELT
  product sync, AIMAAS lifecycle/threshold sync, `GET /api/v1/products`
- `docs/features/tickets/tickets.md` — ticket lifecycle, status gates,
  confidentiality filtering (`confidential_ticket_filter()`)
- `docs/features/tickets/ticket-mutations.md` — ticket-centric mutations,
  `reconcile_ticket_status()`, `auto_assign_actor()`
- `docs/features/tickets/cvss-scoring.md` — CVSS resolution cascade,
  eligibility threshold comparison
- `docs/features/tickets/ticket-audit-log.md` — TicketAuditEvent field mapping
- `docs/features/packages/ibs-track-release-detection.md` — IBS
  track-level release detection
- `docs/features/packages/ibs-product-release-detection.md` — IBS
  product-level release detection
- `docs/features/packages/ibs-submission-tracking.md` — SR/RR tracking,
  delivery pipeline, SyncIbsRequests
- `docs/features/integrations/ibs-rabbitmq-integration.md` — real-time
  IBS event consumption
- `docs/features/packages/product-lifecycle-transitions.md` — EOL and
  Reactive LTSS handling
- `docs/features/packages/package-bugowner.md` — bugowner resolution
- `docs/features/platform/system-settings.md` — default CVSS version configuration
- `docs/data-model.md` — full database schema
