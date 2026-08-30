# IBS Track-Level Release Detection

## Purpose

Detect when CVE fixes land in IBS codestream projects (e.g.,
`SUSE:SLE-15-SP6:Update`) by monitoring source package changes via MD5
checksum comparison and IBS diff analysis. This is the **track-level**
release detection mechanism for the IBS workflow.

In the IBS workflow, the track-level unit is the **codestream** — an IBS
project where source packages are maintained and built. This specification
uses "codestream" throughout to refer to this IBS-specific concept.

For the overall release tracking architecture (two independent levels —
codestream and product), see `docs/features/packages/package-model.md`, section
"Release Tracking". For product-level detection, see
`docs/features/packages/ibs-product-release-detection.md`.

## Context

Sentinel monitors two independent levels of release for each affected
package:

1. **Codestream level** (this spec): the fix has been added to the
   codestream's IBS project.
2. **Product level** (separate spec): the fix has been published to the
   product's update repository.

The codestream level updates `TicketPackageTrack.status` to `FIXED` as
soon as the fix appears in the codestream IBS project, **regardless of the
state of the products under it**. The `delivery_status` is not modified by
track release detection — it is managed independently by the submission
tracking mechanism (see `docs/features/packages/ibs-submission-tracking.md`).

The automatic transition applies only when the current track status is
`AFFECTED` or `ANALYSIS` (see `docs/features/packages/package-model.md`,
Automatic Transitions).

## Detection Mechanism

Sentinel uses an `IBSTrackReleaseDetector` service that monitors IBS codestream
projects for package source changes and detects CVE fixes by analyzing diffs.
The mechanism is based on MD5 checksum comparison (inspired by SMASH's
`TrackedReleaseFetcher`).

### IBS Endpoints

The detector uses two IBS API calls (see `docs/features/integrations/ibs-integration.md`
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

### MD5 Checksum Cache

The detector maintains a `CodestreamPackageChecksum` table in PostgreSQL
(see `docs/data-model.md`) that stores the last known `srcmd5` for each
`(codestream_name, package_name)` pair. This cache enables efficient
change detection: only packages whose MD5 has changed since the last run
need to be diffed.

This cache is shared with the real-time `IBSEventConsumer` (see
`docs/features/integrations/ibs-rabbitmq-integration.md`) — changes processed in
real-time are not re-processed by the periodic fetcher.

### Procedure

The `IBSTrackReleaseDetector` runs on a periodic schedule (every 24
hours at 02:00 UTC via Celery Beat) and executes the following steps.
This periodic fetcher serves as a catch-up mechanism for events missed
by the real-time `IBSEventConsumer` during downtime — see
`docs/features/integrations/ibs-rabbitmq-integration.md`.

1. **Identify active codestreams**: query the distinct `reference`
   values from `TicketPackageTrack` records with `status` in
   (`ANALYSIS`, `AFFECTED`) and `workflow_type = ibs`, belonging to
   **active tickets** (ticket status in `New`, `Analysis`, `Analyzed`).
   VA-excluded and lifecycle-non-actionable tracks under active Tickets are
   included because release detection records factual state regardless of
   operational actionability (see Exclusion and Actionability in
   `docs/features/packages/package-model.md`). Only codestreams with at least
   one such track are scanned.

2. **Fetch current MD5 checksums**: for each active codestream, call
   `GET /source/{codestream}?view=info` via the `IBSClient` service. This
   returns `{package_name: srcmd5}` for all packages in the project.

3. **Compare against cached MD5s**: for each package returned by IBS,
   compare the `srcmd5` with the value stored in
   `CodestreamPackageChecksum`. Packages with unchanged MD5 are skipped.

4. **First-run and long-gap behavior**: absence of a cached MD5, first
   execution after feature enablement, and a gap beyond the normal
   incremental history MUST reconcile current release state for the active IBS
   scope. The current MD5 MUST NOT be saved as an unexamined baseline when that
   would make an already-present relevant fix permanently undiscoverable. The
   concrete bounded current-state procedure remains owned by this
   track-release specification; it reuses this fetcher rather than an
   enablement hook or generic backfill framework.

5. **Diff changed packages**: for each package with a changed MD5, call
   `POST /source/{project}/{package}?cmd=diff&view=xml&onlyissues=1&orev={old_md5}&rev={new_md5}`
   via the `IBSClient`. Extract references with `state="added"` and
   `tracker` equal to `cve` or `bnc`.

6. **Process extracted CVEs**: for each CVE-ID string found in the diff,
   validate format via `is_valid_cve_id(cve_id)` (from
   `core.identifiers`). If the value does not match, log WARNING ("IBS
   diff contains malformed CVE reference: {value} in package
   {package_name}") and skip this reference (do not process it as a
   CVE-ID). Continue with the next reference. For valid CVE-IDs, apply
   the match logic described in
   [Codestream Match Outcomes](#codestream-match-outcomes) below.

7. **Update cache**: write the new MD5 to `CodestreamPackageChecksum` only
   after every relevant CVE outcome from the diff is completely processed or
   remains discoverable through an independent permanent recovery path. A
   successful IBS diff response alone is insufficient. If diff retrieval or
   any required downstream outcome fails, keep the previous checksum so the
   next run repeats the idempotent processing. See `package-model.md`
   (Checkpoint Safety). Enqueuing `create_ticket_from_detection` for Case C is
   not completion and does not advance the checksum. The next run repeats the
   diff and re-evaluates current state: an existing Ticket is processed through
   Case A or B, while an absent Ticket remains Case C. The checksum advances
   only after a later run observes every relevant outcome as complete.

## Codestream Match Outcomes

For each CVE-ID extracted from the diff of a changed package P in
codestream C, the detector evaluates three cases:

### Case A — Ticket exists, package tracked in that codestream

A `TicketPackageTrack` record exists for the ticket's CVE with
`package_name = P` and `reference = C`.

- Set `TicketPackageTrack.status` to `FIXED` through the
  `package_service` module (only when current status is `AFFECTED` or
  `ANALYSIS`).
- Create a `TicketAuditEvent` with `event_type = track_status_changed`,
  `user_id = NULL` (system action), `old_value` = previous status,
  `new_value = FIXED`, `comment` = `"{C} {P}"` (track_name package_name).

### Case B — Ticket exists, package NOT tracked in the ticket

A ticket exists for the CVE, but no `TicketPackageTrack` record exists
for package P (in any codestream).

- Call `add_package_to_ticket(ticket_id, P)` to resolve all codestreams
  and products via SMELT and create the `TicketPackage` +
  `TicketPackageTrack` records with status `ANALYSIS` (record creation
  goes through `package_service`). See
  `docs/features/packages/package-model.md`, "Adding Packages to a Ticket".
- Set the `TicketPackageTrack` for codestream C to `status = FIXED`
  through `package_service` (the specific codestream where the fix was
  detected).
- Create a `TicketAuditEvent` with `event_type = package_added`,
  `user_id = NULL`, comment: "Package `{P}` auto-added: CVE fix
  detected in `{C}`".

### Case C — No ticket exists for the CVE

No ticket exists in Sentinel for the extracted CVE-ID.

- Enqueue a `create_ticket_from_detection` Celery task with parameters:
  `cve_id` (string), `package_name`, `codestream_name`.
- The task performs:
   1. Fetch CVE data from NVD API v2
      (`GET /rest/json/cves/2.0?cveId={cve_id}`). If NVD is unreachable
      or the CVE is not yet published, create a minimal CVE record with
      only the CVE-ID and `severity = NULL`.
   2. Create the CVE record.
   3. Create a Ticket with status `New`, no assignee.
   4. Call `add_package_to_ticket(ticket_id, package_name)` to resolve
      all codestreams and products via SMELT and create the
      `TicketPackage` + `TicketPackageTrack` records with status
      `ANALYSIS` (record creation goes through `package_service`).
   5. Set the `TicketPackageTrack` for the originating codestream to
      `status = FIXED` through `package_service`.
   6. Create a `TicketAuditEvent` with `event_type = ticket_created`,
       `user_id = NULL`, comment: `"CVE fix detected in {package}
       ({codestream})"`.

## Error Handling

- **IBS unreachable / timeout**: skip the codestream with WARNING-level
  log, `record_failed()`, retry on the next scheduled run. The
  `items_failed` counter and `partial` run status surface the condition
  on the fetcher dashboard.
- **IBS returns error for a specific package diff**: log WARNING,
  `record_failed()`, do NOT update the MD5 cache (the next run will
  re-attempt the diff), continue with remaining packages.
- **SMELT unreachable** (during Case B/C package resolution): log WARNING,
  call `record_failed()`, skip the package addition, and retain the previous
  MD5 so the next run re-attempts the diff and idempotent outcome processing.
- **SMELT targets unresolved** (during Case B/C package resolution): handle
  `PackageTargetsUnresolvedError` identically to SMELT unavailability: log
  WARNING, call `record_failed()`, skip package addition, and retain the
  previous MD5. Product catalog backfill cannot discover a package marker that
  was never created, so the unchanged checkpoint is the permanent automatic
  retry path.
- **Product catalog not ready** (during Case B/C package resolution): handle
  `ProductCatalogNotReadyError` identically to SMELT unavailability. No
  package-tree records are written. Retain the previous MD5; the existing
  failed-item metric and `partial` run status surface the skipped addition and
  the next run re-attempts it.
- **Deduplication** (Case C): if multiple packages in the same run yield
  the same CVE-ID without a ticket, only one `create_ticket_from_detection`
  task is enqueued. Every affected package remains an incomplete Case C outcome
  for checkpoint purposes, so none of their checksums advances in that run. A
  later run re-evaluates each package as Case A or B after the Ticket exists, or
  as Case C if creation did not complete.

## Background Task

### Fetcher: `detect_ibs_track_releases`

| Property | Value |
|----------|-------|
| Fetcher name | `detect_ibs_track_releases` |
| Class name | `DetectIbsTrackReleases` |
| Schedule | Daily at 02:00 UTC (`0 2 * * *`) |
| Source | IBS (`build.suse.de`) |
| Scope | All codestreams with at least one `TicketPackageTrack` where `workflow_type = ibs` and status is `ANALYSIS` or `AFFECTED`, belonging to active Tickets (New, Analysis, Analyzed). VA-excluded and lifecycle-non-actionable tracks are included |
| Auth | HTTP Basic / API token (internal) |
| `participates_in_catch_up` | `True` — participates in per-ticket catch-up on ticket reactivation |
| Custom settings | No |

Catch-up mechanism for events missed by the real-time
`IBSEventConsumer` (see
`docs/features/integrations/ibs-rabbitmq-integration.md`).

#### Catch-Up

`DetectIbsTrackReleases` implements `catch_up()` as a custom override
(not the default CVE fetcher implementation). See
[fetcher-infrastructure.md](../platform/fetcher-infrastructure.md)
("Per-Ticket Catch-Up: `catch_up()` Method") for the base class
contract.

**Scope**: after package-tree re-resolution has committed, extracts only the
ticket's `TicketPackageTrack` records with `workflow_type = ibs` and status in
`ANALYSIS` or `AFFECTED`. It performs a targeted current-state release check
for the Ticket's CVE and package on each track. The shared
`CodestreamPackageChecksum` is global to `(codestream, package)` and may already
have advanced because another Ticket was active; therefore checksum equality
MUST NOT by itself suppress this per-ticket check.

The detailed bounded current-state algorithm remains owned by this
track-release specification and must be complete before implementation. Catch-up
recovers whether the fix is currently present, not every intervening source
revision. It MUST NOT write `CodestreamPackageChecksum`: a targeted Ticket/CVE
check has not processed every relevant outcome in the shared package revision,
so only the periodic `execute()` flow or RabbitMQ package-event flow may advance
that checkpoint after complete diff processing.

#### Metrics

- `record_created`: a new ticket was created from a detected release
  (Case C)
- `record_updated`: a `TicketPackageTrack` status was transitioned to
  `FIXED` (Case A), or a package was added to a ticket (Case B)
- `record_failed`: a codestream could not be scanned (IBS API error)

## Remaining Specification Work

Endpoint selection and diff matching are resolved:

- **IBS endpoint** — Resolved: `GET /source/{project}?view=info` for
  change detection, `POST /source/{project}/{package}?cmd=diff` for CVE
  extraction. See [Detection Mechanism](#detection-mechanism) above
  and `docs/features/integrations/ibs-integration.md`.
- **Match strategy** — Resolved: the IBS diff endpoint provides an
  explicit `CVE -> source package` link, so the Advisory ↔ Source Package
  Match chain (used by the product-level detector) is not needed at the
  codestream level.

Before implementation, this specification must define the
bounded first-run, long-gap, and per-ticket current-state procedures referenced
above, including partial success for a diff containing multiple CVEs. These
requirements belong to this detector and do not introduce a generic backfill
framework.
