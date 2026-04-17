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
| RELEASED          | Released      | Green      | Final      | Automatic (release detector) or IM |

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

The following transitions can be performed automatically by STAMP (see the
[Release Tracking](#release-tracking) section for the full detection
mechanism):

| From              | To                | Applies to             | Trigger                                |
|-------------------|-------------------|------------------------|----------------------------------------|
| AFFECTED          | RELEASED          | TicketPackageCodestream | `CodestreamReleaseDetector` detects fix in codestream IBS project |
| NOT_AFFECTED      | RELEASED          | TicketPackageCodestream | `CodestreamReleaseDetector` detects fix in codestream IBS project |
| ANALYSIS          | RELEASED          | TicketPackageCodestream | `CodestreamReleaseDetector` detects fix in codestream IBS project |
| AFFECTED          | RELEASED          | TicketPackageProduct   | `ProductReleaseDetector` detects fix in product update repository (`updateinfo.xml`) |
| NOT_AFFECTED      | RELEASED          | TicketPackageProduct   | `ProductReleaseDetector` detects fix in product update repository (`updateinfo.xml`) |
| ANALYSIS          | RELEASED          | TicketPackageProduct   | `ProductReleaseDetector` detects fix in product update repository (`updateinfo.xml`) |
| AFFECTED          | AFFECTED_RESOLVED | TicketPackageProduct   | Product not eligible (CVSS < threshold)|
| AFFECTED          | AFFECTED_RESOLVED | TicketPackageProduct   | Product enters Reactive LTSS phase     |
| AFFECTED_RESOLVED | AFFECTED          | TicketPackageProduct   | Product becomes eligible (threshold change or lifecycle phase change) |

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

STAMP monitors two **independent** levels of release for each affected
package:

1. **Codestream level**: the fix has been added to the codestream's IBS
   project (e.g., `SUSE:SLE-15-SP6:Update`).
2. **Product level**: the fix has been published to the product's update
   repository (e.g., the SLES 15 SP6 update repository consumed by `zypper`).

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

### Codestream-level Detection

STAMP uses an internal abstraction `CodestreamReleaseDetector` that, given a
`TicketPackageCodestream` record (ticket CVE, package name, codestream name),
determines whether the fix for the CVE has been released into the codestream
IBS project.

When a release is detected:

- `TicketPackageCodestream.status` is set to `RELEASED` (unless current
  status is `WONT_FIX` or `IGNORED`).

**TBD**: the concrete IBS endpoint and query procedure used by
`CodestreamReleaseDetector` are not yet specified. They will be detailed in
`docs/features/obs-integration.md` before implementation. See
[Open Items](#open-items) below.

### Product-level Detection

STAMP uses an internal abstraction `ProductReleaseDetector` based on the
standard `updateinfo.xml` metadata file published in every product update
repository (the same metadata file consumed by `zypper`). This is the
ground-truth source: an advisory present in `updateinfo.xml` is, by
definition, available to end users of that product.

#### Procedure

For each product P with an associated update repository URL `<repo_url>`
(see [Update Repository URL Resolution](#update-repository-url-resolution)
below for how `<repo_url>` is constructed):

1. Download `<repo_url>/repodata/repomd.xml`.
2. Locate the `<data type="updateinfo">` element and extract the location
   of the `updateinfo.xml.gz` file (path relative to `<repo_url>`).
3. Download and parse `updateinfo.xml`.
4. Iterate the `<update>` elements. For each `<update>` U, check whether its
   `<references>` block contains a `<reference type="cve" id="CVE-XXXX-YYYY">`
   matching the CVE-ID of any open ticket whose `TicketPackageProduct`
   records reference P and are in a non-final, non-protected status.
5. For each such advisory, apply the
   [Advisory ↔ Source Package Match](#advisory--source-package-match) chain
   below to identify which specific source package of the ticket received
   the fix.

#### Outcome per matched (ticket, product, package)

- `TicketPackageProduct.status` is set to `RELEASED` (unless current status
  is `WONT_FIX` or `IGNORED`).
- `TicketPackageProduct.released_at` is set to the `<issued date>` attribute
  of the advisory.

#### Update Repository URL Resolution

STAMP does not store a separate URL field for update repositories. The HTTP
URL is constructed at runtime from each `ProductRepository.repo_name` using
the pattern:

```
{IBS_DOWNLOAD_BASE_URL}/{repo_name.replace(':', '/')}/update/
```

where `IBS_DOWNLOAD_BASE_URL` is an environment variable (default:
`https://download.suse.de/ibs`). For example, repo name
`SUSE:Updates:SLE-Module-Basesystem:15-SP7:x86_64` produces the URL
`https://download.suse.de/ibs/SUSE/Updates/SLE-Module-Basesystem/15-SP7/x86_64/update/`.

Only `ProductRepository` entries with prefix `SUSE:Updates:` are relevant
for release tracking. Other repository types are excluded:

- `SUSE:Products:*` — base product/pool repos, never contain
  `updateinfo.xml`.
- Repos whose last segment is `debug` or `src` — companion repos for
  debuginfo and source packages, never contain advisory metadata.
- Repos targeting non-RPM distributions (Debian, Ubuntu, or
  `MultiLinuxManagerTools` targeting Debian/Ubuntu) — these use apt
  format, not RPM repodata.

If a product has no eligible `SUSE:Updates:*` entries in
`ProductRepository`, it is skipped during release tracking with a
WARNING-level log. This is expected for products that are not yet released
(e.g., SLE 16.x) or deprecated.

#### Multi-architecture Handling

SMELT repository names fall into two categories:

- **Single-arch repos**: name ends with a known architecture segment.
  Known architectures: `x86_64`, `aarch64`, `s390x`, `ppc64le`, `i586`,
  `i686`, `ia64`, `ppc64`.
  Example: `SUSE:Updates:SLE-Module-Basesystem:15-SP7:x86_64`.
- **Multi-arch repos**: name does NOT end with an architecture segment.
  These repos contain packages for all architectures in a single
  repository.
  Example: `SUSE:Updates:openSUSE-SLE:15.6`.

STAMP does NOT track release status per architecture — a match on any
architecture is sufficient to set the status to `RELEASED`.

**Scanning strategy per product**:

1. From the product's `ProductRepository` entries, select those eligible
   for release tracking (prefix `SUSE:Updates:`, excluding `debug`,
   `src`, and non-RPM repos as described above).
2. If a multi-arch repo exists, scan it first (it covers all
   architectures in a single repository).
3. If no match was found (or no multi-arch repo exists), scan single-arch
   repos: `x86_64` first (primary architecture), then remaining
   architectures in alphabetical order.
4. As soon as a match is found on any repo, set status to `RELEASED` and
   stop — do not scan remaining repos.

This approach handles the common case efficiently (most advisories land on
x86_64) while also covering arch-specific packages like `s390-tools` that
are only released for `s390x`.

#### Error Handling

The `ProductReleaseDetector` handles the following error conditions
gracefully:

- **HTTP 404** (repository does not exist on `download.suse.de`): skip
  with WARNING-level log. This is expected for brand-new products whose
  repos have not yet been created (e.g., SLE 16.x, SL-Micro 6.x).
- **HTTP 403** (access restricted): skip with WARNING-level log. Some
  partner repos may have access restrictions.
- **`repomd.xml` exists but has no `<data type="updateinfo">`**: skip
  silently. This means the repository exists but has had zero security
  updates published to it. This is normal for newly launched or niche
  products.
- **Network errors / timeouts**: skip with ERROR-level log, retry on the
  next scheduled run of `check_release_status`.

**TBD** (see [Open Items](#open-items)):

- Caching of repodata metadata (ETag/Last-Modified, incremental parsing).
- Backfill semantics for advisories that pre-date the ticket.

### Advisory ↔ Source Package Match

This match procedure is defined once and applies to both detection levels.
At the product level it operates on `<update>` entries from `updateinfo.xml`;
at the codestream level it may be reused by `CodestreamReleaseDetector` or
bypassed if the chosen IBS endpoint already exposes the explicit link
`CVE → source package` (TBD, see [Open Items](#open-items)).

**Why this matters**: a single CVE can affect multiple distinct source
packages, typically when a vulnerable library is statically linked into
binding packages (e.g., a CVE in a Go or Rust library that impacts
`containerd`, `podman`, `golang-1.21`, and others — each requiring its own
independent fix). STAMP must identify **which specific source package** of
the ticket has been fixed by a given advisory, so that only the
corresponding `TicketPackageProduct` record is transitioned to `RELEASED`,
leaving the others untouched until their own fixes land.

The match is a cascade — the first step that produces a positive match
wins; on failure, processing falls through to the next step.

#### Step 1 — Title pattern match

- Apply the regular expression
  `^(Security|Recommended|Optional|Feature) update for (\S+)$` to the
  advisory's `<title>`.
- **Pattern not recognized** (no match for the regex above): emit a
  WARNING-level application log including `advisory_id`, `repo`, and the
  raw `title`, then fall through to Step 2. These warnings will feed the
  future admin "Sync diagnostics" page (separate spec).
- **Pattern recognized and the captured group `<X>` exactly equals one of
  the ticket's `package_name` values**: MATCH on that package.
- **Pattern recognized but `<X>` does not equal any ticket package**: fall
  through silently to Step 2 (this is the normal case for advisories that
  legitimately use a title package name distinct from the source name).

#### Step 2 — Heuristic prefix match

For each `package_name` PT of the ticket, PT is a candidate match if it
appears either:

- in the package name `<X>` extracted from the title (rule:
  `X == PT` OR `X.startswith(PT + "-")`), **or**
- in at least one `<package name="B">` of the `<pkglist>` (rule:
  `B == PT` OR `B.startswith(PT + "-")`).

Then:

- **No candidate** → fall through to Step 3.
- **Exactly one candidate** → MATCH on that package.
- **Multiple candidates**: the longest PT wins (most specific match).
  Example: a ticket containing both `openssl` and `openssl-3` against an
  advisory whose pkglist includes `libopenssl-3-devel` resolves to
  `openssl-3`.
- **Ambiguity not resolved by length** (two or more PT of the same length
  matching) → fall through to Step 3.

#### Step 3 — `primary.xml` exact source match

- Download `primary.xml` of the repository (also referenced from
  `repomd.xml`).
- For each binary RPM listed in the advisory's `<pkglist>`, read its
  `<rpm:sourcerpm>` element (e.g.,
  `openssl-3-3.1.4-150600.5.9.1.src.rpm`) and derive the source package
  name by stripping the trailing `-version-release.arch.src.rpm`
  components (yielding e.g. `openssl-3`).
- Compare the resulting source names against the ticket's `package_name`
  values (exact equality).
- **No match** → proceed with the no-match flow below.
- **Exactly one ticket package matches** → MATCH on that package.
- **Multiple ticket packages match** (e.g., the advisory ships SRPMs for
  several source packages that are all in the ticket): apply the same
  tie-breaker as Step 2 — the longest `package_name` wins. If two or more
  matching packages have the same length, fall through to the no-match
  flow (this is conservative: better to surface the case for IM review
  than to risk flipping the wrong record).

### Match Outcomes

#### Positive match (source package S of the ticket on product/codestream X)

- Product level: `TicketPackageProduct(S, X).status` → `RELEASED`,
  `released_at` = advisory's `<issued date>`.
- Codestream level: `TicketPackageCodestream(S, X).status` → `RELEASED`
  (codestream-specific transition details depend on the
  `CodestreamReleaseDetector` implementation, TBD).
- The transition is suppressed when the current status is `WONT_FIX` or
  `IGNORED` (protected states, see "Status Behavior").

#### No-match (advisory cites the ticket's CVE but no ticket package matches, even via `primary.xml`)

- Create a `TicketEvent` of informational type recording: `advisory_id`,
  the source name derived from `primary.xml` if available, and a note that
  no ticket package matched.
- Notify the ticket's assignee (notification mechanism is TBD at the system
  level, see [Open Items](#open-items)).
- Add the ticket to the **"Revisit" list** (separate feature spec, TBD).
- **No automatic modification** is made to the ticket's package records.

### Open Items

The following aspects of release tracking are intentionally left open in
this revision of the spec. They will be closed in subsequent sessions
before implementation begins. They are listed here so any reader (human or
agent) can see at a glance what is missing.

#### Product-level detection

- **Repodata caching** — Strategy for caching `repomd.xml` /
  `updateinfo.xml` / `primary.xml` (ETag, Last-Modified, incremental
  parsing) to avoid redundant downloads.
- **Backfill of pre-existing advisories** — Behavior when a new ticket is
  opened for a CVE for which an advisory already exists in the product
  repository (mark `RELEASED` retroactively with a historical
  `released_at`, or ignore advisories older than the ticket).
- **Formal definition of "relevant advisory"** — Edge cases (e.g.,
  `<update status>` values other than `stable`, advisories with empty
  `<pkglist>`, retracted advisories) need formalization.

#### Codestream-level detection

- **IBS endpoint** — The specific IBS endpoint and query procedure used by
  `CodestreamReleaseDetector`. To be documented in
  `docs/features/obs-integration.md`.
- **Match strategy** — Whether the codestream detector reuses the
  Advisory ↔ Source Package Match chain defined above, or whether the
  chosen IBS endpoint exposes an explicit `CVE → source package` link
  that makes the chain unnecessary.

#### Cross-cutting

- **Task scheduling** — Frequency and scope of the `check_release_status`
  background task (which tickets/products to scan, how often).
- **Code structure** — Whether to introduce a dedicated `IBSClient`
  service or extend the existing `OBSClient` described in
  `docs/features/obs-integration.md`.
- **Released advisory persistence** — Whether to store a reference to the
  advisory that caused the automatic `RELEASED` transition (e.g., a
  `released_advisory_id` field on `TicketPackageProduct` holding the
  `SUSE-SU-YYYY:NNNN` identifier) for traceability and UI display, or to
  rely solely on `released_at` plus the audit log.
- **Audit events on automatic transitions** — Whether successful automatic
  `RELEASED` transitions emit a `TicketEvent` (for timeline
  traceability), in addition to the `TicketEvent` already specified for
  the no-match flow.
- **New `TicketEvent.event_type` value** — The no-match flow specifies
  the creation of an informational `TicketEvent`, but `event_type` in
  `docs/data-model.md` is an ENUM whose current values
  (`status_change`, `assignment`, `duplicate_set`, `duplicate_removed`)
  do not cover this case. A new enum value (name TBD, e.g.
  `release_no_match`) will need to be added to the data model when this
  spec is implemented.

#### Dependencies on separate features

- **"Revisit" list** — Destination for tickets in the no-match flow.
  Separate feature spec.
- **Notifications** — Mechanism (in-app, email) for notifying the
  assignee in the no-match flow. Separate feature spec.
- **Admin "Sync diagnostics" page** — Destination for unrecognized title
  warnings (and, potentially, products without a configured update
  repository URL). Separate feature spec.

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
- `check_release_status`: periodic task that drives release detection at
  both levels. Internally it invokes the `CodestreamReleaseDetector`
  (IBS-based, TBD endpoint) for `TicketPackageCodestream` records and the
  `ProductReleaseDetector` (`updateinfo.xml`-based) for
  `TicketPackageProduct` records, and applies the automatic transitions to
  `RELEASED` described in the [Release Tracking](#release-tracking) section.
  Frequency and scope (which tickets/products to scan, how often) are TBD,
  see [Open Items](#open-items).

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
- **IBS release tracking details**: the product-level detection mechanism
  is now described in the [Release Tracking](#release-tracking) section
  (based on `updateinfo.xml`). The codestream-level detection mechanism
  still needs to be detailed in `docs/features/obs-integration.md` once
  the specific IBS endpoint is chosen — see [Open Items](#open-items).
