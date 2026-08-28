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

The SMELT v2 maintained-package endpoint provides the codestream-level
`maintenance_process_type`. Sentinel maps supported values directly to
`workflow_type` at ingestion time: `SLFO` maps to `git` and `SLE_15` maps to
`ibs`. The target-level `product_definition.type` (`channel` or `compose`)
describes Product-definition provenance and is not workflow authority. Both
supported workflows can coexist under the same package. See
[SMELT Query for Package Resolution](#smelt-query-for-package-resolution)
for the full resolution contract.

`workflow_type` is captured when a `TicketPackageTrack` is first created.
Subsequent resolution of the same track reference does not reconcile a later
SMELT maintenance-process reclassification; the existing track retains its
persisted `workflow_type`.

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

### 7. Manual exclusion and derived actionability

Spurious packages, tracks, or Products are excluded by a VA through
hierarchical soft-deletion rather than an `IGNORED` status. Each `deleted_at`
marker records an explicit VA decision at that exact scope; automated
workflows never set or clear these markers.

Whether a record currently participates in operational work is represented by
the derived `actionable` property. Actionability combines the hierarchical VA
exclusion markers with the authoritative Product lifecycle phase. In
particular, EOL is derived from AIMAAS lifecycle dates and never copied into a
package-tree `deleted_at` field. See [Exclusion and Actionability](#exclusion-and-actionability).

### 8. Non-actionable records continue to receive factual updates

VA-excluded and lifecycle-non-actionable records are omitted from operational
views and gates, but they **continue to receive factual and independently
derived updates** from delivery tracking, eligibility recalculation, and
release detection while their Ticket is operable. Their state remains current,
eliminating restore-time reconciliation and allowing lifecycle actionability
to change without replaying missed facts.

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

1. **Product listing** (`v1/basic/products/` relative to the configured SMELT
   API prefix): paginated list of all SUSE products with name, version,
   CPE, and repository project names. See
   `docs/features/packages/product-catalog.md` (SMELT Integration) for the
   product sync specification.
2. **Per-package maintenance info**
   (`experimental/v2/maintained/` relative to the configured SMELT API
   prefix): given a source package name, returns the list of codestreams
   where the package is maintained and the Products it is shipped to,
   with direct CPE identification, authoritative codestream maintenance
   process, and target-level Product-definition provenance.

SMELT reads from IBS, Git Product catalogs, Product build SBOM snapshots,
and other sources internally.

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
catalog feature. `Product` is consumed here for CPE-based package
resolution (see [SMELT Query for Package Resolution](#smelt-query-for-package-resolution))
and eligibility evaluation. `ProductRepository` is not used by package
resolution — it remains scoped to catalog sync and release detection.

### TicketPackage

An explicit entity that anchors a source package within a ticket. Replaces
the implicit grouping by `package_name` across
`TicketPackageTrack` records.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | UUID | PK | Internal identifier |
| `ticket_id` | UUID | FK(ticket.id), NOT NULL | Related ticket |
| `package_name` | VARCHAR(255) | NOT NULL | Source package name |
| `deleted_at` | TIMESTAMPTZ | nullable | Direct VA-exclusion timestamp. NULL = not directly VA-excluded |
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
| `deleted_at` | TIMESTAMPTZ | nullable | Direct VA-exclusion timestamp. NULL = not directly VA-excluded |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Record creation timestamp |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT | Record update timestamp |

**Unique constraint**: `(ticket_package_id, reference)`

The track is identified by `reference` (a string), not by a foreign key.
Tracks are not maintained as a separate table — they are discovered
per-package via the SMELT v2 maintained-package endpoint.

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
| `deleted_at` | TIMESTAMPTZ | nullable | Direct VA-exclusion timestamp. NULL = not directly VA-excluded |
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
| `false` | Product does not meet the criteria (CVSS below threshold or Reactive Support phase) |

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

1. **Reactive Support override**: if the Product is currently in the
   `reactive_support` lifecycle phase,
   `eligible = false` regardless of CVSS score.
   A `NULL` lifecycle phase means that lifecycle is unavailable; it does not
   activate this override and does not otherwise force either eligibility
   value. The remaining CVSS threshold rules still apply.
2. **Check CVSS threshold**: read `Product.cvss_threshold`, which is
   synchronized from AIMAAS. NULL means an implicit threshold of 0 (all CVEs
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
whether any actionable Product under it has `eligible = true`.

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

## Exclusion and Actionability

### Manual Exclusion Markers

The VA can soft-delete packages, tracks, or Products to exclude them from the
Ticket. A non-null `deleted_at` is a durable record of an explicit VA decision
at that exact scope:

- `deleted_at IS NOT NULL` means directly VA-excluded;
- `deleted_at IS NULL` means not directly VA-excluded.

The acting VA is recorded in the corresponding `TicketAuditEvent`, not on the
package-tree record. Automated workflows MUST NOT set or clear any package,
track, or Product `deleted_at` field.

Manual exclusion is hierarchical. Only the record targeted by the VA is
modified:

- excluding a package sets only `TicketPackage.deleted_at`;
- excluding a track sets only `TicketPackageTrack.deleted_at`;
- excluding a Product sets only `TicketPackageProduct.deleted_at`.

A descendant is **effectively VA-excluded** when its own marker or any ancestor
marker is non-null:

| Record type | Effectively VA-excluded when |
|-------------|------------------------------|
| Package | `package.deleted_at IS NOT NULL` |
| Track | `package.deleted_at IS NOT NULL` or `track.deleted_at IS NOT NULL` |
| Product | `package.deleted_at IS NOT NULL`, `track.deleted_at IS NOT NULL`, or `product.deleted_at IS NOT NULL` |

There is no automatic orphan soft-deletion. A parent with no participating
descendants retains `deleted_at = NULL` unless a VA explicitly excludes that
parent.

### Derived Actionability

`actionable` is a derived property, not a database column. It is evaluated
from current package-tree markers, Product lifecycle data, and one UTC
`evaluation_date` captured for the complete request or transaction:

```text
product.actionable =
    package.deleted_at IS NULL
    AND track.deleted_at IS NULL
    AND ticket_product.deleted_at IS NULL
    AND (
        lifecycle_phase(catalog_product, evaluation_date) IS NULL
        OR lifecycle_phase(catalog_product, evaluation_date) != eol
    )

track.actionable =
    package.deleted_at IS NULL
    AND track.deleted_at IS NULL
    AND EXISTS(product where product.actionable)

package.actionable =
    package.deleted_at IS NULL
    AND EXISTS(track where track.actionable)
```

A `NULL` lifecycle phase means lifecycle is unavailable and does not make a
Product non-actionable. Product lifecycle evaluation is defined in
`product-catalog.md` (Lifecycle Evaluator).

The service layer MUST expose reusable SQL/SQLAlchemy expressions implementing
these predicates for filters, aggregate counts, and Ticket gates. The pure
lifecycle evaluator and SQL lifecycle expression MUST produce identical
results for the same dates and `evaluation_date`. Implementations MUST NOT
persist `lifecycle_phase` or `actionable` as current-state columns.

Each package-tree response includes `actionable` and a nullable
`non_actionable_reason`. The reason uses the first applicable value in this
ordered list, which makes the result deterministic when multiple conditions
apply:

| Level | Ordered `non_actionable_reason` values |
|-------|----------------------------------------|
| Package | `package_excluded`, `no_actionable_tracks` |
| Track | `package_excluded`, `track_excluded`, `no_actionable_products` |
| Product | `package_excluded`, `track_excluded`, `product_excluded`, `eol` |

An actionable record has `non_actionable_reason = NULL`. Parent reasons
describe the absence of actionable descendants without changing any parent
row. Consumers MUST use these fields rather than infer current participation
from `deleted_at` alone.

### Gate Participation

Actionability is an observation-point combination of manual exclusion and
lifecycle; it does not modify affectedness, eligibility, or delivery.

- The Analyzed gate's minimum-presence condition requires at least one track
  that is not effectively VA-excluded. This proves that package analysis data
  exists even when every Product is currently EOL.
- Only actionable tracks participate in the undecided-affectedness check and
  the Resolved gate.
- Only actionable Products participate in Product-level resolution
  conditions.
- Therefore a Ticket with at least one VA-included track but no actionable
  tracks can be Resolved once the non-lifecycle Analyzed requirements are met.
  If a Product later leaves EOL, its track becomes actionable and normal gate
  reconciliation may regress the Ticket.

### Continued Updates

Directly or effectively VA-excluded records and EOL Products continue to
receive eligibility recalculation, delivery updates, and release observations
while their Ticket is operable (`New`, `Analysis`, `Analyzed`, or `Resolved`).
Records under manual-zone Tickets are recovered through the applicable catch-up
behavior when the Ticket re-enters an operable status. This keeps factual state
current without making exclusion or lifecycle depend on another dimension.

### Restore

Restore clears `deleted_at` only on the directly VA-excluded record selected by
the VA. It never modifies descendants and is permitted while an ancestor is
excluded or while the restored record remains non-actionable for another
reason. No child-existence or actionability precondition applies.

For example, restoring a Product while its track remains excluded clears the
Product's direct marker but leaves it effectively VA-excluded through the
track. Restoring a track while all its Products are EOL clears the manual
marker but leaves the track non-actionable until at least one Product becomes
actionable. Each effective restore creates one VA-attributed audit event and
reconciles the Ticket once.

### Interaction with add_package_to_ticket

The `add_package_to_ticket` function proceeds normally regardless of
whether the `TicketPackage` is soft-deleted. It queries SMELT, and
creates any missing `TicketPackageTrack` and `TicketPackageProduct`
records. Existing records (active or soft-deleted) are skipped.

New records are created with `deleted_at = NULL`. If the parent package or
track is VA-excluded, these records are effectively VA-excluded through the
hierarchy. If their Product is EOL, they are independently non-actionable.

The **API handler** for `POST /api/v1/tickets/{ticket_id}/packages` is
responsible for checking whether the `TicketPackage` is soft-deleted
(`deleted_at IS NOT NULL`) **before** calling the function. If it is,
the handler returns `409 PACKAGE_ALREADY_EXCLUDED` without invoking the
function. Internal callers (CVE ingestion, release detection) call the
function directly and benefit from the automatic exclusion via hierarchy.

### Ticket Events for Exclusion

A single VA-attributed `TicketAuditEvent` is created for each effective
exclusion or restore operation, only for the directly affected record. Child
records that become effectively VA-excluded through the hierarchy do not
generate events. Derived EOL/actionability changes do not create exclusion or
restore events because they do not mutate package-tree records.

| Action | `event_type` | `user_id` | Details recorded |
|--------|-------------|-----------|------------------|
| VA soft-deletes a package | `package_excluded` | VA user | `package_name` |
| VA soft-deletes a track | `track_excluded` | VA user | `track_name`, `package_name` |
| VA soft-deletes a product | `product_excluded` | VA user | `track_name`, `package_name`, event-time Product name and CPE |
| VA restores a package | `package_restored` | VA user | `package_name` |
| VA restores a track | `track_restored` | VA user | `track_name`, `package_name` |
| VA restores a product | `product_restored` | VA user | `track_name`, `package_name`, event-time Product name and CPE |

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

Standalone eligibility changes go through `package_service`. Product-originated
automatic recalculation groups all matching records by Ticket, locks and
processes one Ticket transaction at a time, and calls
`reconcile_ticket_status()` once only when at least one value changed. The CVSS
recalculation chain remains the documented architectural exception owned by
`ticket_mutations`. Both paths skip manual overrides and continue updating
soft-deleted records under operable Tickets.

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

1. Query SMELT and resolve products as specified in
   [SMELT Query for Package Resolution](#smelt-query-for-package-resolution):
   external I/O, envelope validation, catalog readiness check, CPE matching,
   synthetic channel/compose deduplication, unsupported-process filtering, and
   `workflow_type` determination from `codestream.maintenance_process_type`.
   If no Product is resolved across the complete response, reject the
   operation without database writes.
2. Create a `TicketPackage` record for the package if one does not already
   exist. If a record already exists (active or soft-deleted), skip creation.
3. For each resolved track, delegate `TicketPackageTrack` record
   creation to `package_service` (if a record does not already exist,
   including soft-deleted).
4. For each resolved Product under each track, delegate
   `TicketPackageProduct` record creation to `package_service` (if a
   record does not already exist, including soft-deleted).
5. If at least one package, track, or Product record was created, register
   these best-effort post-commit effects:
   - Resolve and cache the IBS bugowner. If a `PackageBugowner` record already
     exists for this `package_name`, update it with fresh data from IBS; if it
     does not exist, create it. See
     `docs/features/packages/package-bugowner.md`.
   - Enqueue `discover_submissions_for_ticket_package(ticket_id,
     package_name)` to discover IBS submission requests (SRs) and release
     requests (RRs) for the ticket's CVE created within the last 14 days. See
     `docs/features/packages/ibs-submission-tracking.md`, Pipeline 3.
   A fully no-op invocation registers neither effect.
6. Return an `AddPackageResult` containing:
   - `tracks_created`, `tracks_skipped`, `products_created`,
     `products_skipped`: counts of records created vs. skipped.

`package_service` handles idempotency (skipping existing records,
including soft-deleted), initial status determination, and eligibility
logic internally — see `docs/features/packages/package-service.md`.

New records are created with `deleted_at = NULL`. A new descendant under a
VA-excluded parent is effectively VA-excluded through the hierarchy, and a new
EOL Product is non-actionable without any mutation. See
[Exclusion and Actionability](#exclusion-and-actionability).

When a Product is added beneath an existing track, the track retains its
current affectedness and delivery statuses. The Product therefore inherits
the track's affectedness through the hierarchy. Its eligibility is calculated
independently at creation time, and its Product-level `released_at` starts as
`NULL`.

**Idempotency**: the function is safe to call multiple times for the
same package. If SMELT adds new tracks or products for a package after
the initial addition, calling the function again will add only the new
records. Existing records (active or soft-deleted) are skipped. The SMELT and
current-catalog validation gates run before this no-op determination, so a
repeat call can still fail with `PACKAGE_NOT_FOUND_IN_SMELT`,
`PACKAGE_TARGETS_UNRESOLVED`, `PRODUCT_CATALOG_NOT_READY`, or
`SMELT_UNAVAILABLE`.

### Triggers

The following scenarios invoke `add_package_to_ticket`:

1. **Automatic (CVE ingestion)**: when a CVE is ingested, Sentinel
   resolves package names from the CVE data (NVD CPE package candidates
   selected by the NVD ingestion contract, CNA/ADP CPE strings, CNA/ADP
   vendor:product pairs, or pre-resolved packages). For each resolved
   package name,
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
6. **Product catalog backfill**: after a successful SMELT Product catalog
   sync makes at least one Product newly current, a system workflow calls
   `add_package_to_ticket` for each active-Ticket package whose package marker
   is not soft-deleted. Lifecycle actionability does not filter this recovery
   scan because a currently EOL Product can later become actionable without a
   new catalog association. See `product-catalog.md` (Product Catalog
   Backfill).

### Package Management Constraints

The VA manages packages at the **package level only**:

- The VA can **add** packages to a ticket.
- The VA can **soft-delete** entire packages, individual tracks, or
  individual Products from a ticket (see
  [Exclusion and Actionability](#exclusion-and-actionability)).
- The VA **cannot** add individual tracks or products — these are
  determined exclusively by SMELT when a package is added via
  `add_package_to_ticket`.
- The VA **can** change the affectedness status of individual tracks
  (via the status dropdown) and override the eligibility of individual
  products.

### Removing a Package from a Ticket

When a VA removes a package from a ticket, Sentinel performs a
**soft-deletion** (see
[Exclusion and Actionability](#exclusion-and-actionability)): `deleted_at`
is set on the `TicketPackage` record only. Child `TicketPackageTrack`
and `TicketPackageProduct` records are not modified — they become
effectively excluded via the hierarchy.

### SMELT Query for Package Resolution

When `add_package_to_ticket` resolves a package, it calls the SMELT v2
maintained-package endpoint:

```
experimental/v2/maintained/?package={url_encode(name)}&include_reactive_ltss=true
```

The `package` value is URL-encoded before interpolation. The response is a
single non-paginated JSend envelope.

**Envelope and error handling**:

- A successful response has HTTP status 200 and body
  `{"status": "success", "data": [...]}` where `data` is a non-empty array
  of codestream entries.
- A successful response with an empty `data` array (`{"status": "success",
  "data": []}`) is treated as package-not-found. This covers packages known
  to SMELT but currently maintained in zero codestreams.
- A package-not-found response arrives with HTTP status 404 and body
  `{"status": "error", "data": "Package X not found"}`.
- Both package-not-found cases map to `PackageNotFoundInSmeltError`.
- A connection failure, timeout, proxy error, or remote-protocol error after
  the shared transport retries are exhausted maps to `SmeltUnavailableError`.
  No HTTP response exists to inspect in these cases.
- Sentinel MUST parse the JSON body regardless of HTTP status and check the
  JSend `status` field before applying the catch-all rule below. A 404 with
  a valid `status: "error"` body is a package-not-found, not an availability
  failure.
- Sentinel recognizes only the JSend `status` values `success` and `error` in
  this endpoint's responses. Any other value — including JSend `fail`, which
  this experimental endpoint does not document a use for — is unrecognized.
- Any non-200 response other than the valid 404 package-not-found response,
  any body that cannot be parsed as JSON, any unrecognized `status` field, or
  any entry-validation failure maps to `SmeltUnavailableError`.
- Live verification confirmed that the `package` filter is case-sensitive:
  canonical `kernel-default` returned results, while case variants returned
  the error envelope. Sentinel does not normalize package-name case before
  the query.

**Entry validation**:

- The `include_reactive_ltss=true` parameter MUST always be included to
  ensure Products in Reactive LTSS are returned.
- Each entry in `data` must have a `codestream` object with a non-empty
  string `name` that fits the persisted track-reference column length and a
  non-null string `maintenance_process_type`. Codestream names must be unique
  across the grouped response.
- `maintenance_process_type` must be one of the declared SMELT values `SLFO`,
  `SLFO_IBS`, or `SLE_15`. A missing, null, non-string, or unknown value, or a
  repeated codestream name, rejects the complete response and raises
  `SmeltUnavailableError`.
- A supported `SLFO` or `SLE_15` entry must have a non-empty `targets` array.
  Each target must have `product.cpe` as a non-empty string and
  `product_definition.type` with a value of `"channel"` or `"compose"`.
  An invalid supported entry or target rejects the complete response and
  raises `SmeltUnavailableError`; targets are never individually skipped for
  structural validation failures.
- `SLFO_IBS` is a known but unsupported maintenance process. Sentinel skips
  the complete codestream without validating or consuming its targets and
  emits one WARNING-level
  `package_codestream_maintenance_process_unsupported` event containing
  `package_name`, `codestream`, and `maintenance_process_type`. Processing
  continues with supported codestreams.
- `product.friendly_name` is used only for logging and warning messages.
  If absent or empty, the `product.cpe` value is used as a fallback in
  log messages. A missing `friendly_name` does not reject the response.

**Consumed fields** (minimal integration):

| Field | Purpose |
|-------|---------|
| `data[].codestream.name` | Track reference (`TicketPackageTrack.reference`) |
| `data[].codestream.maintenance_process_type` | Authoritative track workflow: `SLFO` → `git`, `SLE_15` → `ibs`; known `SLFO_IBS` entries are unsupported and skipped |
| `data[].targets[].product.cpe` | Product match key against local `Product.cpe` |
| `data[].targets[].product_definition.type` | Validated as `channel` or `compose`; Product-definition provenance used for synthetic same-CPE channel/compose deduplication |
| `data[].targets[].product.friendly_name` | Logging and warning messages |

All other response fields (`codestream.url`, `product.id`,
`product.support_status`, `product_definition.name`, `product_definition.url`,
`binary_packages`, `repository`) are not consumed by Sentinel.

**Deduplication**:

The v2 endpoint aggregates results from both IBS channel records and
Git/SLFO compose records. During a transitional period, some Products may
appear under two different codestreams: once via a synthetic channel file
and once via the real compose resolution. When the same Product CPE appears
in targets under both a `channel` and `compose` entry, the `channel` entry
for that Product is discarded and only the `compose` entry is retained. This
deduplication is a no-op once the transitional synthetic channel files are
removed by SMELT.

**Processing**:

1. Retrieve the response. If transport fails after shared retries, or the
   response does not have a valid JSend envelope and an expected HTTP status,
   raise `SmeltUnavailableError`.
2. Require a ready Product catalog as defined in `product-catalog.md`. If no
   complete Product snapshot has committed, raise
   `ProductCatalogNotReadyError` before interpreting the response content.
   Readiness failure takes precedence over both package-not-found and
   targets-unresolved outcomes.
3. If HTTP status is 404 with a valid `status = "error"` envelope, or status
   is 200 with `status = "success"` and an empty `data` array, raise
   `PackageNotFoundInSmeltError`. Any other combination of HTTP status and
   JSend `status` — including HTTP 200 with `status = "error"` — raises
   `SmeltUnavailableError`.
4. Validate every codestream name and maintenance-process value. Skip each
   `SLFO_IBS` codestream with the warning defined above. Validate all targets
   belonging to the remaining supported codestreams.
5. Map each supported codestream to one `workflow_type`: `SLFO` maps to `git`
   and `SLE_15` maps to `ibs`.
6. Collect all `(codestream.name, workflow_type, product.cpe,
   product_definition.type)` records from supported entries. Apply the
   deduplication rule above.
7. For each remaining record:
   a. Look up the Product by exact `Product.cpe` match in the local catalog.
      If no local Product matches, ignore this triple and continue.
   b. Create or find a `TicketPackageTrack` with `reference =
      codestream.name` and the determined `workflow_type` (if one does not
      already exist for this package + reference combination, including
      soft-deleted).
   c. Create a `TicketPackageProduct` linking the track to the matched
      Product (if one does not already exist).
8. If no Product was resolved across the entire response, including when all
   returned codestreams were skipped as unsupported, fail with
   `PackageTargetsUnresolvedError`; no package-tree record is created.

When at least one Product CPE has no local match in an otherwise successful
resolution, log a WARNING-level `package_target_resolution_partial` event with
the package name and unmatched CPEs. This accepted partial result does not
change the API response shape.

For a package tree that is created partially, a newly introduced Product may
be omitted until the Product catalog sync adds the corresponding `Product` row
and invokes Product catalog backfill. A zero-resolution failure creates no
`TicketPackage` and therefore cannot be discovered by backfill; recovery
requires a later manual or automatic invocation. A CPE that remains absent
from the local Product catalog is intentionally ignored on every invocation.
Sentinel never creates a Product from a CPE string or falls back to
name/version matching.

---

## Ticket Events for Package Changes

Every modification to a ticket's package data MUST produce a
`TicketAuditEvent` record for audit and traceability. The following event
types are defined:

| Action | `event_type` | `user_id` | Details recorded |
|--------|-------------|-----------|------------------|
| VA adds or completes package tree | `package_added` | VA user | `package_name` |
| Package auto-added or completed (CVE ingestion, release detection, or Product catalog backfill) | `package_added` | `NULL` | `package_name`, contextual `comment` |
| VA soft-deletes package | `package_excluded` | VA user | `package_name` |
| VA soft-deletes track | `track_excluded` | VA user | `track_name`, `package_name` |
| VA soft-deletes product | `product_excluded` | VA user | `track_name`, `package_name`, event-time Product name and CPE |
| VA restores package | `package_restored` | VA user | `package_name` |
| VA restores track | `track_restored` | VA user | `track_name`, `package_name` |
| VA restores product | `product_restored` | VA user | `track_name`, `package_name`, event-time Product name and CPE |
| VA changes track status | `track_status_changed` | VA user | `track_name`, `package_name`, `old_status`, `new_status` |
| VA overrides or resets Product eligibility | `product_eligibility_changed` | VA user | `track_name`, `package_name`, event-time Product name and CPE, `old_eligible`, `new_eligible`, `reason = va_override`, and `override_action` |
| Ticket created | `ticket_created` | `NULL` | Creation source description |
| Product release detected | `product_released` | `NULL` | `track_name`, `package_name`, event-time Product name and CPE, `released_at`, `advisory_id` |
| Product eligibility recalculated | `product_eligibility_changed` | `NULL` | `track_name`, `package_name`, event-time Product name and CPE, `old_eligible`, `new_eligible`, `reason` |

- `user_id = NULL` indicates an automatic system action. For
  `package_added`, this distinguishes manual additions (VA user) from
  automatic ones (CVE ingestion, release detection). The `comment` field
  provides context for automatic additions.
- Exactly one `package_added` event is created when an invocation creates at
  least one package, track, or Product record. A completely no-op invocation
  creates no `package_added` event. Product catalog backfill uses the fixed
  comment `Product catalog backfill`.
- All events include an implicit `created_at` timestamp.
- The "Details recorded" column lists the values stored in the event's
  `old_value`, `new_value`, `comment`, and structured `detail` fields. See
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
- **Analyzed → Resolved**: requires every actionable track to
  be resolution-complete — either (a) `NOT_AFFECTED`/`WONT_FIX`, or
  (b) `FIXED` with all actionable eligible Products released, or
  (c) `AFFECTED` with no actionable eligible Products remaining
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
- Product eligibility (CVSS threshold, Reactive Support phase)
- Soft-deletion and restore
- UI — VA sees packages → tracks → products with no workflow distinction
- Bugowner — `PackageBugowner` cache keyed by `package_name`; joined via
  `TicketPackage.package_name`

The following concerns are workflow-specific (service layer only):

| Concern | IBS (`ibs`) | Git (`git`) |
|---------|-------------|-------------|
| Track + product resolution | SMELT v2 `maintained` (same endpoint) | SMELT v2 `maintained` (same endpoint) |
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
`package_service`, and, when at least one record is created, performs the
documented post-commit bugowner resolution and submission-discovery dispatch.
See [Adding Packages to a Ticket](#adding-packages-to-a-ticket) for the full
behavior.

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
API query (`experimental/v2/maintained/?package={url_encode(name)}`).
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
| 422 | `PACKAGE_TARGETS_UNRESOLVED` | SMELT returned tracks, but none of their targets resolved to a Product in Sentinel's current catalog snapshot |
| 503 | `PRODUCT_CATALOG_NOT_READY` | No complete SMELT Product catalog snapshot has committed yet |
| 503 | `SMELT_UNAVAILABLE` | SMELT did not produce a valid successful response |

**Idempotency**: safe to call multiple times for the same **active**
package. If the package is already fully resolved, the response will
report zero created records. If the package is soft-deleted, the endpoint
returns 409 `PACKAGE_ALREADY_EXCLUDED` — the VA must use the restore
endpoint to re-include it. The request still performs SMELT and current-catalog
validation before determining that the package tree is complete, so the
documented SMELT/catalog errors may be returned on a repeat call.

---

### Soft-Delete Package from Ticket

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/exclude
```

Soft-delete a package from the Ticket. Sets `deleted_at` on the package record
only; tracks and Products are not modified but become effectively VA-excluded
through the hierarchy. Creates a single `TicketAuditEvent`.
See [Exclusion and Actionability](#exclusion-and-actionability) for the full
behavior.

After the soft-delete, the system reconciles ticket status via
`package_service`. This is necessary because excluding the package changes the
set of participating records considered by Ticket gates (Resolved gate and
Analyzed gate).

**Response** (200 OK):

```json
{
  "data": {
    "package_name": "openssl-3",
    "actionable": false,
    "non_actionable_reason": "package_excluded"
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

Restore a directly VA-excluded package. Clears `deleted_at` on the package
record only; child records are not modified. The package may remain
non-actionable because every track is excluded or has no actionable Product.
Creates a single `TicketAuditEvent`. See
[Exclusion and Actionability — Restore](#restore).

**Response** (200 OK):

```json
{
  "data": {
    "package_name": "openssl-3",
    "actionable": true,
    "non_actionable_reason": null
  }
}
```

If every track remains non-actionable after the restore, the same successful
response instead returns `actionable = false` and
`non_actionable_reason = "no_actionable_tracks"`.

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Package not found on this ticket |
| 422 | `PACKAGE_NOT_EXCLUDED` | Package is not directly soft-deleted |

---

### Soft-Delete Track

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/exclude
```

Soft-delete a track from the ticket. Sets `deleted_at` on the track record
only; Products under it are not modified but become effectively VA-excluded
through the hierarchy. Creates one VA-attributed `TicketAuditEvent`.

After the soft-delete, the system
reconciles ticket status via `package_service`. This is necessary
because excluding a track changes the set of participating records considered
by ticket gates (Resolved gate and Analyzed gate).

**Response** (200 OK):

```json
{
  "data": {
    "reference": "SUSE:SLE-15-SP6:Update",
    "actionable": false,
    "non_actionable_reason": "track_excluded"
  }
}
```

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

Restore a directly VA-excluded track. Clears `deleted_at` on the track record
only; Product markers are not modified. The track may remain non-actionable
because every Product is individually excluded or EOL. Creates a single
`TicketAuditEvent`.

**Response** (200 OK):

```json
{
  "data": {
    "reference": "SUSE:SLE-15-SP6:Update",
    "actionable": true,
    "non_actionable_reason": null
  }
}
```

If every Product remains non-actionable after the restore, the same successful
response instead returns `actionable = false` and
`non_actionable_reason = "no_actionable_products"`.

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Track not found on this ticket |
| 422 | `PACKAGE_NOT_EXCLUDED` | Track is not directly soft-deleted |

---

### Soft-Delete Product

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{ticket_package_product_id}/exclude
```

Soft-delete a single Product from a track. Creates one VA-attributed
`TicketAuditEvent` for the excluded record. Parent markers are never changed
automatically.

After the soft-delete, the system
reconciles ticket status via `package_service`. This is necessary
because excluding a Product changes the set of actionable records considered
by ticket gates (Resolved gate and Analyzed gate).

**Response** (200 OK):

```json
{
  "data": {
    "id": "uuid",
    "product_cpe": "cpe:/o:suse:sles:15:sp6",
    "product_name": "SLES 15-SP6",
    "actionable": false,
    "non_actionable_reason": "product_excluded"
  }
}
```

**`Capability: manage_packages`**

**Error responses**:

| Status | Code | Condition |
|--------|------|-----------|
| 404 | `RESOURCE_NOT_FOUND` | Product not found on this track |
| 409 | `PACKAGE_ALREADY_EXCLUDED` | Product is already soft-deleted |

---

### Restore Product

```
POST /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{ticket_package_product_id}/restore
```

Restore a directly VA-excluded Product. Clears `deleted_at` on the Product
record. No child, ancestor, or lifecycle pre-check applies. Creates a single
`TicketAuditEvent`.

**Response** (200 OK):

```json
{
  "data": {
    "id": "uuid",
    "product_cpe": "cpe:/o:suse:sles_ltss:15:sp4",
    "product_name": "SLES-LTSS 15-SP4",
    "actionable": false,
    "non_actionable_reason": "eol"
  }
}
```

The example remains non-actionable because the restored Product is EOL. A
non-EOL Product under manually included ancestors returns `actionable = true`
and `non_actionable_reason = null`.

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
  "status": "affected"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status value. Valid values: `analysis`, `affected`, `not_affected`, `fixed`†, `wont_fix` |

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
    "status": "affected",
    "delivery_status": "pending",
    "delivery_relevant": true,
    "actionable": true,
    "non_actionable_reason": null,
    "products": [
      {
        "id": "uuid",
        "product_cpe": "cpe:/o:suse:sles:15:sp6",
        "product_name": "SLES 15 SP6",
        "eligible": true,
        "is_eligible_override": false,
        "lifecycle_phase": "general_support",
        "actionable": true,
        "non_actionable_reason": null
      },
      {
        "id": "uuid",
        "product_cpe": "cpe:/o:suse:sles_ltss:15:sp4",
        "product_name": "SLES-LTSS 15-SP4",
        "eligible": false,
        "is_eligible_override": false,
        "lifecycle_phase": "eol",
        "actionable": false,
        "non_actionable_reason": "eol"
      }
    ]
  }
}
```

The response includes the updated track and all its child Products with their
current eligibility and actionability, allowing the client to update
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
PATCH /api/v1/tickets/{ticket_id}/packages/{package_id}/tracks/{track_id}/products/{ticket_package_product_id}
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
   making the Product eligible unless the Reactive Support override applies.

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
    "id": "uuid",
    "product_cpe": "cpe:/o:suse:sles_ltss:15:sp4",
    "product_name": "SLES-LTSS 15-SP4",
    "eligible": false,
    "is_eligible_override": true,
    "lifecycle_phase": "reactive_support",
    "actionable": true,
    "non_actionable_reason": null
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

Returns the complete package tree for a specific Ticket — all packages,
tracks, and Products including non-actionable records. Direct VA-exclusion
timestamps and current actionability are visible on each level. Identical data
to the `packages` field in
`TicketDetail` from `GET /api/v1/tickets/{ticket_id}`, but available as
a standalone endpoint for clients that only need package data.

| Aspect | Design |
|--------|--------|
| **`Access: Public`** | Consistent with `GET /api/v1/tickets/{ticket_id}` |
| **`Authentication: Optional`** | Resolves caller identity for ticket accessibility |
| **Guard** | `require_accessible_ticket` (404 for missing/confidential tickets) |
| **Pagination** | No — package count per ticket is bounded (typically 1-5, rarely >20) |
| **Envelope** | `{"data": [...]}` (unpaginated list) |
| **Excluded records** | All package/track/Product records are returned, including directly or effectively VA-excluded and lifecycle-non-actionable records |
| **Actionability** | Every level includes derived `actionable` and `non_actionable_reason` values evaluated with one UTC date shared by the response |
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
| **Non-actionable packages** | Always excluded. This includes directly VA-excluded packages and packages with no actionable tracks |
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

**`track_summary`** (`TrackSummary`) — aggregated track status counts for the
package within this Ticket. Counts only actionable tracks, using the same UTC
`evaluation_date` as package filtering and pagination:

| Field | Type | Description |
|-------|------|-------------|
| `total` | integer | Total actionable tracks |
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
- `evaluate_lifecycle_transitions`: periodic task (daily at 04:15 UTC) that
  reconciles lifecycle-derived eligibility and Ticket gate state from current
  Product dates. EOL changes derived actionability without mutating
  package-tree exclusion markers. Idempotent — operates on current state with
  no lifecycle cache. See
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
- [x] SMELT API evolution — the v2 `maintained` endpoint exposes
      `codestream.maintenance_process_type` as the authoritative workflow
      discriminator
- [x] Workflow type mapping — `SLFO` maps to `git`, `SLE_15` maps to `ibs`,
      and known unsupported `SLFO_IBS` codestreams are skipped
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
  Reactive Support handling
- `docs/features/packages/package-bugowner.md` — bugowner resolution
- `docs/features/platform/system-settings.md` — default CVSS version configuration
- `docs/data-model.md` — full database schema
