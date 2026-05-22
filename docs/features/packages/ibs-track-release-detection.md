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
status of the products under it**. The `delivery_status` is not modified by
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
   status in `New`, `Analysis`, `Analyzed` and `deleted_at IS NULL`).
   Soft-deleted tracks under active tickets are included — release
   detection applies regardless of exclusion status (see hierarchical
   exclusion model in `docs/features/packages/package-model.md`). Only
   codestreams with at least one such track are scanned.

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
- Notify the ticket's assignee.
- Add the ticket to the "Revisit" list.

### Case C — No ticket exists for the CVE

No ticket exists in Sentinel for the extracted CVE-ID.

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
      all codestreams and products via SMELT and create the
      `TicketPackage` + `TicketPackageTrack` records with status
      `ANALYSIS` (record creation goes through `package_service`).
   5. Set the `TicketPackageTrack` for the originating codestream to
      `status = FIXED` through `package_service`.
   6. Create a `TicketAuditEvent` with `event_type = ticket_created`,
       `user_id = NULL`, comment: `"CVE fix detected in {package}
       ({codestream})"`.

## Error Handling

- **IBS unreachable / timeout**: skip the codestream with ERROR-level log,
  retry on the next scheduled run.
- **IBS returns error for a specific package diff**: log ERROR, do NOT
  update the MD5 cache (the next run will re-attempt the diff), continue
  with remaining packages.
- **SMELT unreachable** (during Case B/C package resolution): log ERROR,
  the package addition is skipped. The next run will not re-trigger it
  (MD5 already cached), so the condition should be surfaced to operators
  via monitoring.
- **Deduplication** (Case C): if multiple packages in the same run yield
  the same CVE-ID without a ticket, only one `create_ticket_from_detection`
  task is enqueued. Subsequent packages with the same CVE-ID in the same
  run are handled as Case B once the ticket is created.

## Background Task

- **Task name**: `check_ibs_track_releases`
- **Type**: `BaseFetcher` subclass
- **Schedule**: every 24 hours at 02:00 UTC (`0 2 * * *`)
- **Role**: catch-up mechanism for events missed by the real-time
  `IBSEventConsumer` (see `docs/features/integrations/ibs-rabbitmq-integration.md`)
- **Scope**: scans all codestreams that have at least one
  `TicketPackageTrack` record with `status` in
  (`ANALYSIS`, `AFFECTED`), belonging to active tickets (status in
  `New`, `Analysis`, `Analyzed` and `deleted_at IS NULL`). Soft-deleted
  tracks under active tickets are included (see hierarchical exclusion
  model)

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
