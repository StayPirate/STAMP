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
   (`ANALYSIS`, `AFFECTED`), belonging to **active tickets** (ticket
    status in `New`, `Analysis`, `Analyzed`).
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

4. **First-run behavior**: when no cached MD5 exists for a codestream
   (first time the detector processes it), the current MD5 values are
   saved to `CodestreamPackageChecksum` without performing any diffs. CVE
   detection begins from the second run onward. This avoids spurious
   matches from the entire package history.

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

7. **Update cache**: write the new MD5 to `CodestreamPackageChecksum` for
   each successfully processed package. The cache is updated **only if the
   IBS diff request succeeded** (HTTP 200). If IBS returned an error for
   a specific package diff, the MD5 is NOT updated so the next run will
   re-attempt the diff for that package.

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
  `record_failed()`, the package addition is skipped. The next run will
  not re-trigger it (MD5 already cached). The `items_failed` counter and
  `partial` run status surface the condition on the fetcher dashboard for
  operator attention.
- **SMELT targets unresolved** (during Case B/C package resolution): handle
  `PackageTargetsUnresolvedError` identically to SMELT unavailability: log
  WARNING, call `record_failed()`, and skip package addition. No
  `TicketPackage` exists for Product catalog backfill to discover, and the
  MD5 is already cached, so recovery requires a later CVE-ingestion package
  resolution, manual VA addition, or operator-triggered rerun. This accepted
  limitation is surfaced by the fetcher's failed-item metrics and `partial`
  status.
- **Product catalog not ready** (during Case B/C package resolution): handle
  `ProductCatalogNotReadyError` identically to SMELT unavailability. No
  package-tree records are written. The existing failed-item metric and
  `partial` run status surface the skipped addition; recovery follows the same
  later-ingestion, manual-addition, or operator-rerun paths as an unresolved
  target.
- **Deduplication** (Case C): if multiple packages in the same run yield
  the same CVE-ID without a ticket, only one `create_ticket_from_detection`
  task is enqueued. Subsequent packages with the same CVE-ID in the same
  run are handled as Case B once the ticket is created.

## Background Task

### Fetcher: `detect_ibs_track_releases`

| Property | Value |
|----------|-------|
| Fetcher name | `detect_ibs_track_releases` |
| Class name | `DetectIbsTrackReleases` |
| Schedule | Daily at 02:00 UTC (`0 2 * * *`) |
| Source | IBS (`build.suse.de`) |
| Scope | All codestreams with at least one `TicketPackageTrack` in `ANALYSIS` or `AFFECTED` status, belonging to active Tickets (New, Analysis, Analyzed). VA-excluded and lifecycle-non-actionable tracks are included |
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

**Scope**: extracts the ticket's `TicketPackageTrack` records and
checks IBS for source changes on each codestream, using the same
diff-based detection logic as `execute()` but scoped to a single
ticket.

**Detailed specification**: to be defined during implementation.

#### Metrics

- `record_created`: a new ticket was created from a detected release
  (Case C)
- `record_updated`: a `TicketPackageTrack` status was transitioned to
  `FIXED` (Case A), or a package was added to a ticket (Case B)
- `record_failed`: a codestream could not be scanned (IBS API error)

## Open Items

All codestream-level open items have been resolved:

- **IBS endpoint** — Resolved: `GET /source/{project}?view=info` for
  change detection, `POST /source/{project}/{package}?cmd=diff` for CVE
  extraction. See [Detection Mechanism](#detection-mechanism) above
  and `docs/features/integrations/ibs-integration.md`.
- **Match strategy** — Resolved: the IBS diff endpoint provides an
  explicit `CVE -> source package` link, so the Advisory ↔ Source Package
  Match chain (used by the product-level detector) is not needed at the
  codestream level.
