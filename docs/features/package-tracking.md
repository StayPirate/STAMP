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
2. **Resolve the CVSS score**: the score used for threshold comparison is
   determined by the CVSS resolution cascade (see
   `docs/features/cvss-scoring.md`):
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
   always `AFFECTED_RESOLVED` regardless of the CVSS score. The IM can still
   perform the assessment, but the result is always green.

**Important**: the CVSS version used for threshold comparison MUST always
be resolved from the system-wide default CVSS version configuration — never
hardcoded. See `docs/features/cvss-scoring.md` and
`docs/features/admin.md`.

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
   - Product has `cvss_threshold` and resolved CVSS score < threshold →
     `AFFECTED_RESOLVED` (score resolved via the CVSS resolution cascade,
     see `docs/features/cvss-scoring.md`)
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
| AFFECTED          | AFFECTED_RESOLVED | TicketPackageProduct   | Product not eligible (resolved CVSS score < threshold) |
| AFFECTED          | AFFECTED_RESOLVED | TicketPackageProduct   | Product enters Reactive LTSS phase     |
| AFFECTED_RESOLVED | AFFECTED          | TicketPackageProduct   | Product becomes eligible (CVSS score change, threshold change, or lifecycle phase change) |

**Protected states**: `WONT_FIX` and `IGNORED` are never modified by automatic
transitions.

#### Manual transitions

The IM can manually change any status to any other status without restriction.

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
2. For each resolved codestream, create a `TicketPackageCodestream` record
   with status `ANALYSIS` (skip if one already exists for this ticket +
   package + codestream combination).
3. For each resolved product under each codestream, create a
   `TicketPackageProduct` record with status `ANALYSIS` (skip if one
   already exists). Apply eligibility rules (CVSS threshold, Reactive LTSS
   override) to adjust the initial status where applicable.
4. Return a result indicating which records were created and which were
   skipped (already existed).

**Idempotency**: the function is safe to call multiple times for the same
package. Existing records are never modified — only missing records are
created. This means that if SMELT adds new codestreams or products for a
package after the initial addition, calling the function again will add only
the new records.

### Triggers

The following scenarios invoke `add_package_to_ticket`:

1. **Automatic (CPE mapping)**: when a CVE is ingested, STAMP maps the CPE
   data from the CVE record to source package names. For each mapped
   package name, `add_package_to_ticket` is called.
2. **Manual**: the IM manually adds a package by name via the UI.
   `add_package_to_ticket` is called with the entered name.
3. **Codestream release detection (Case B)**: the `CodestreamReleaseDetector`
   finds a CVE fix in a package that is not tracked in the ticket. It calls
   `add_package_to_ticket` to add all codestreams and products, then sets
   the specific codestream where the fix was detected to `RELEASED`. See
   [Case B](#case-b--ticket-exists-package-not-tracked-in-the-ticket).
4. **Ticket auto-creation (Case C)**: a CVE fix is detected for a CVE with
   no existing ticket. After creating the ticket,
   `add_package_to_ticket` is called, then the originating codestream is
   set to `RELEASED`. See
   [Case C](#case-c--no-ticket-exists-for-the-cve).

### Package Management Constraints

The IM manages packages at the **package level only**:

- The IM can **add** or **remove** entire packages from a ticket.
- The IM **cannot** add or remove individual codestreams or products —
  these are determined exclusively by SMELT when a package is added via
  `add_package_to_ticket`.
- The IM **can** change the status of individual codestreams (via the
  status dropdown) and override the status of individual products (which
  sets `is_override = true`).

### Removing a Package from a Ticket

When an IM removes a package from a ticket, STAMP deletes **all**
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
| IM adds package | `package_added` | IM user | `package_name` |
| IM removes package | `package_removed` | IM user | `package_name` |
| IM changes codestream status | `codestream_status_changed` | IM user | `package_name`, `codestream_name`, `old_status`, `new_status` |
| IM overrides product status | `product_status_overridden` | IM user | `package_name`, `product_id`, `old_status`, `new_status` |
| Auto-added (Case B) | `package_auto_added` | `NULL` | `package_name`, `codestream_name` |
| Auto-created ticket (Case C) | `ticket_auto_created` | `NULL` | `package_name`, `codestream_name` |
| Codestream release detected | `codestream_released` | `NULL` | `package_name`, `codestream_name` |
| Product release detected | `product_released` | `NULL` | `package_name`, `product_id`, `advisory_id` |

- `user_id = NULL` indicates an automatic system action.
- All events include an implicit `created_at` timestamp.
- The "Details recorded" column lists the fields stored in the event's
  structured data payload (the exact storage format — JSON column, separate
  fields, etc. — is defined in `docs/data-model.md`).

### SMELT Query for Package Resolution

When `add_package_to_ticket` resolves a package, it calls:

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

STAMP uses a `CodestreamReleaseDetector` service that monitors IBS codestream
projects for package source changes and detects CVE fixes by analyzing diffs.
The mechanism is based on MD5 checksum comparison (inspired by SMASH's
`TrackedReleaseFetcher`).

#### IBS Endpoints

The detector uses two IBS API calls (see `docs/features/obs-integration.md`
for full endpoint documentation):

1. **Source info** — `GET /source/{project}?view=info` — returns a
   `<sourceinfo>` element per package containing the `srcmd5` checksum of
   the current source revision. One call per codestream project retrieves
   all packages at once.

2. **Diff with issues** —
   `POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1&orev={old_md5}&rev={new_md5}`
   — returns `<issues><issue>` elements listing CVE and BNC references
   added between the two revisions. IBS extracts these from the changelog
   and spec changes internally.

#### MD5 Checksum Cache

The detector maintains a `CodestreamPackageChecksum` table in PostgreSQL
(see `docs/data-model.md`) that stores the last known `srcmd5` for each
`(codestream_name, package_name)` pair. This cache enables efficient
change detection: only packages whose MD5 has changed since the last run
need to be diffed.

#### Procedure

The `CodestreamReleaseDetector` runs on a periodic schedule (every 8 hours
via Celery Beat) and executes the following steps:

1. **Identify active codestreams**: query the distinct `codestream_name`
   values from `TicketPackageCodestream` records with a non-final,
   non-protected status (`ANALYSIS` or `AFFECTED`). Only codestreams with
   at least one active ticket package are scanned.

2. **Fetch current MD5 checksums**: for each active codestream, call
   `GET /source/{codestream}?view=info` via the `IBSClient` service. This
   returns `{package_name: srcmd5}` for all packages in the project.

3. **Compare against cached MD5s**: for each package returned by IBS,
   compare the `srcmd5` with the value stored in
   `CodestreamPackageChecksum`. Packages with unchanged MD5 are skipped.

4. **First-run behavior**: when no cached MD5 exists for a codestream
   (first time the detector processes it), the current MD5 values are
   saved to `CodestreamPackageChecksum` without performing any diffs. CVE
   detection begins from the second run onward. This avoids spurious
   matches from the entire package history.

5. **Diff changed packages**: for each package with a changed MD5, call
   `POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1&orev={old_md5}&rev={new_md5}`
   via the `IBSClient`. Extract references with `state="added"` and
   `tracker` equal to `cve` or `bnc`.

6. **Process extracted CVEs**: for each CVE-ID found in the diff, apply
   the match logic described in
   [Codestream Match Outcomes](#codestream-match-outcomes) below.

7. **Update cache**: write the new MD5 to `CodestreamPackageChecksum` for
   each processed package. The cache is updated even if an error occurs
   during CVE processing, so already-processed packages are not
   reprocessed on the next run.

#### Codestream Match Outcomes

For each CVE-ID extracted from the diff of a changed package P in
codestream C, the detector evaluates three cases:

##### Case A — Ticket exists, package tracked in that codestream

A `TicketPackageCodestream` record exists for the ticket's CVE with
`package_name = P` and `codestream_name = C`.

- Set `TicketPackageCodestream.status` to `RELEASED` (unless current
  status is `WONT_FIX` or `IGNORED`).
- Create a `TicketEvent` with `event_type = codestream_released`,
  `user_id = NULL` (system action).

##### Case B — Ticket exists, package NOT tracked in the ticket

A ticket exists for the CVE, but no `TicketPackageCodestream` record
exists for package P (in any codestream).

- Call `add_package_to_ticket(ticket_id, P)` to resolve all codestreams
  and products via SMELT and create the records with status `ANALYSIS`.
- Set the `TicketPackageCodestream` for codestream C to `RELEASED`
  (the specific codestream where the fix was detected).
- Create a `TicketEvent` with `event_type = package_auto_added`,
  `user_id = NULL`, comment: "Package `{P}` auto-added: CVE fix
  detected in `{C}`".
- Notify the ticket's assignee.
- Add the ticket to the "Revisit" list.

##### Case C — No ticket exists for the CVE

No ticket exists in STAMP for the extracted CVE-ID.

- Enqueue a `create_ticket_from_detection` Celery task with parameters:
  `cve_id` (string), `package_name`, `codestream_name`.
- The task performs:
   1. Fetch CVE data from NVD API v2
      (`GET /rest/json/cves/2.0?cveId={cve_id}`). If NVD is unreachable
      or the CVE is not yet published, create a minimal CVE record with
      only the CVE-ID and `severity = None`.
   2. Create the CVE record.
   3. Create a Ticket with status `New`, no assignee.
   4. Call `add_package_to_ticket(ticket_id, package_name)` to resolve
      all codestreams and products via SMELT and create the records with
      status `ANALYSIS`.
   5. Set the `TicketPackageCodestream` for the originating codestream to
      `RELEASED`.
   6. Create a `TicketEvent` with `event_type = ticket_auto_created`,
      `user_id = NULL`, comment: "Ticket auto-created: CVE fix detected
      in `{package}` (`{codestream}`)".

#### Error Handling

- **IBS unreachable / timeout**: skip the codestream with ERROR-level log,
  retry on the next scheduled run.
- **IBS returns error for a specific package diff**: log ERROR, update the
  MD5 cache to avoid re-processing, continue with remaining packages.
- **SMELT unreachable** (during Case B/C package resolution): log ERROR,
  the package addition is skipped. The next run will not re-trigger it
  (MD5 already cached), so the condition should be surfaced to operators
  via monitoring.
- **Deduplication** (Case C): if multiple packages in the same run yield
  the same CVE-ID without a ticket, only one `create_ticket_from_detection`
  task is enqueued. Subsequent packages with the same CVE-ID in the same
  run are handled as Case B once the ticket is created.

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
   matching the CVE-ID of any active ticket whose `TicketPackageProduct`
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
  next scheduled run of `check_product_releases`.

**TBD** (see [Open Items](#open-items)):

- Caching of repodata metadata (ETag/Last-Modified, incremental parsing).
- Backfill semantics for advisories that pre-date the ticket.

### Advisory ↔ Source Package Match

This match procedure is defined once and applies to the **product-level**
detection only. It operates on `<update>` entries from `updateinfo.xml`.

The codestream-level detector does not use this match chain — the IBS diff
endpoint (`POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1`)
already provides an explicit `CVE → source package` link via the `<issues>`
response, so the package that received the fix is known directly.

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

#### Positive match (source package S of the ticket on product P)

- `TicketPackageProduct(S, P).status` → `RELEASED`.
- `TicketPackageProduct(S, P).released_at` = advisory's `<issued date>`.
- The transition is suppressed when the current status is `WONT_FIX` or
  `IGNORED` (protected states, see "Status Behavior").

Note: codestream-level match outcomes are described separately in the
[Codestream Match Outcomes](#codestream-match-outcomes) section above.

#### No-match (product level: advisory cites the ticket's CVE but no ticket package matches, even via `primary.xml`)

- Create a `TicketEvent` of informational type recording: `advisory_id`,
  the source name derived from `primary.xml` if available, and a note that
  no ticket package matched.
- Notify the ticket's assignee (notification mechanism is TBD at the system
  level, see [Open Items](#open-items)).
- Add the ticket to the **"Revisit" list** (separate feature spec, TBD).
- **No automatic modification** is made to the ticket's package records.

Note: codestream-level no-match behavior (CVE found in diff but package
not tracked in ticket, or no ticket exists at all) is described in
[Codestream Match Outcomes](#codestream-match-outcomes) above — Cases B
and C.

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

All codestream-level open items have been resolved:

- **IBS endpoint** — Resolved: `GET /source/{project}?view=info` for
  change detection, `POST /source/{project}/{package}?cmd=diff` for CVE
  extraction. See [Codestream-level Detection](#codestream-level-detection)
  and `docs/features/obs-integration.md`.
- **Match strategy** — Resolved: the IBS diff endpoint provides an
  explicit `CVE → source package` link, so the Advisory ↔ Source Package
  Match chain is not needed at the codestream level.

#### Cross-cutting

- **Released advisory persistence** — Whether to store a reference to the
  advisory that caused the automatic `RELEASED` transition (e.g., a
  `released_advisory_id` field on `TicketPackageProduct` holding the
  `SUSE-SU-YYYY:NNNN` identifier) for traceability and UI display, or to
  rely solely on `released_at` plus the audit log.

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
- **Resolved -> Analysis**: if a package is added to a Resolved ticket
  (new codestreams in `ANALYSIS` status), or if an Incident Manager resets
  a codestream status to `ANALYSIS`, the ticket is moved back to `Analysis`.
- **Analyzed -> Analysis**: if a package is added to an Analyzed ticket
  (new codestreams in `ANALYSIS` status), or if an Incident Manager resets
  a codestream status to `ANALYSIS`, the ticket is moved back to `Analysis`.
- **Resolved -> Analyzed**: if a CVSS recalculation causes products to
  transition from `AFFECTED_RESOLVED` to `AFFECTED`, the ticket is moved
  back to `Analyzed`. See `docs/features/cvss-scoring.md` (Recalculation
  Cascade).

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
- `check_codestream_releases`: periodic task (every 8 hours via Celery
  Beat) that invokes the `CodestreamReleaseDetector` service. Scans all
  codestreams that have at least one `TicketPackageCodestream` record in
  a non-final, non-protected status. See
  [Codestream-level Detection](#codestream-level-detection) for the full
  procedure.
- `check_product_releases`: periodic task that invokes the
  `ProductReleaseDetector` (`updateinfo.xml`-based) for
  `TicketPackageProduct` records and applies the automatic transitions to
  `RELEASED` described in the [Release Tracking](#release-tracking) section.
  Frequency and scope are TBD, see [Open Items](#open-items).
- `create_ticket_from_detection`: on-demand task enqueued by the
  `CodestreamReleaseDetector` when a CVE fix is detected for a CVE that
  has no ticket in STAMP. Fetches CVE data from NVD, creates the ticket,
  resolves packages via SMELT, and sets the originating codestream to
  `RELEASED`. See [Case C](#case-c--no-ticket-exists-for-the-cve) for
  details.

## Security

- Adding/removing packages on a ticket requires the Incident Manager role
- Changing codestream/product status requires the Incident Manager role
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
  specified. The product-level mechanism uses `updateinfo.xml` (see
  [Product-level Detection](#product-level-detection)). The codestream-level
  mechanism uses IBS source info and diff endpoints (see
  [Codestream-level Detection](#codestream-level-detection) and
  `docs/features/obs-integration.md`).
