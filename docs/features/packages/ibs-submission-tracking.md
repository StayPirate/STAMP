# Submission Tracking

## Purpose

Track IBS submission requests (SR) and release requests (RR) as entities
parallel to the package affectedness status, giving Vulnerability Analysts
visibility into the progression of fixes without altering the existing
`PackageStatus` model.

Today the track status stays `AFFECTED` with no visibility into what
happens between fix submission and delivery. A maintainer may have
already submitted a fix days ago, but the VA has no way to know until
the system sets `delivery_status` to `RELEASED` (when the RR is
accepted). This feature fills that gap by tracking both SRs and RRs
and showing them alongside the track affectedness status.

## Domain Concepts

SUSE has two processes for producing security updates:

1. **Maintenance Update (MU)** — the traditional process where source code
   resides on IBS (build.suse.de) and the entire update lifecycle happens
   within IBS. Used for most products (SLE 15 SPx, SLES-LTSS, etc.).

2. **Git-based workflow** — a newer process for recent products (SLE 16+)
   where source code resides on gitea.suse.de. IBS is used only for
   building packages and releasing updates to product repositories.

**This specification covers only the MU process.** The git-based workflow
will require a separate tracking mechanism (Gitea API/webhooks) in the
future.

### MU Lifecycle

```
1. Maintainer creates a submission request (SR)
   └── Contains fix for a single package on a single codestream
       (SUSE convention for security updates, enforced by bot)
       │
       ▼
2. Update Manager (UM) reviews and accepts the SR
   └── IBS creates a maintenance incident project: SUSE:Maintenance:XXXXX
   └── The SR content populates the incident
   └── Sources are rebuilt within the incident
       │
       ▼
3. UM creates a release request (RR) from the incident
       │
       ▼
4. QA team tests the RR
       │
       ▼
5. UM accepts the RR (mostly a formality after QA approval)
   └── Sources land in the target codestream project
   └── Binaries are copied to eligible product repositories
```

### Key Constraints

- **One SR = one package + one codestream**: for security updates, SUSE
  convention requires that a submission request contains exactly one
  package targeting one codestream. SRs violating this are declined by
  a bot before the UM sees them. Multi-codestream SRs are technically
  possible in IBS but not used for security updates.

- **One incident = one package + one codestream**: a maintenance incident
  project (SUSE:Maintenance:XXXXX) contains exactly one package for one
  codestream.

- **Multiple SRs per incident**: more than one SR can be accepted into the
  same incident. Each accepted SR overwrites the package sources in the
  incident (e.g., a maintainer submits a fix, then submits an improved
  version). The last accepted SR determines the incident content.

- **One active RR per incident**: IBS enforces that only one RR can be
  active (in `new` or `review` state) for a given incident at any time.
  To create a new RR, all previous RRs must be revoked first.

- **The incident is an implicit concept**: in Sentinel's data model, the
  incident is not a separate entity. It is the linking key
  (`incident_number`) shared between accepted SRs and RRs.

### IBS Request States

| State       | Final? | Description                                    |
|-------------|--------|------------------------------------------------|
| `new`       | No     | Just created                                   |
| `review`    | No     | Under review                                   |
| `accepted`  | Yes    | Accepted and executed                          |
| `declined`  | **No** | Rejected, but can be reopened                  |
| `revoked`   | Yes    | Withdrawn, irreversible                        |
| `superseded`| Yes    | Replaced by another request                    |

**Important**: `declined` is NOT a final state in IBS. A declined SR or RR
can always be reopened (state transitions back to `new` or `review`).
Sentinel must handle this by transitioning the record back to `open` state.

## Design Principle: Parallel Tracking

SRs and RRs are tracked as **separate entities** with their own lifecycle,
not as modifications to `PackageStatus`. The track remains `AFFECTED`
until the system sets it to `FIXED` when `delivery_status` reaches
`RELEASED` — SR/RR status is purely informational for tracking the
MU progression.

**Why not a new PackageStatus value?** Adding an intermediate status (e.g.,
`FIX_IN_PROGRESS`) between `AFFECTED` and `FIXED` was considered and
rejected because:

- It would impact the ticket gate logic (is the new status final?
  non-final? does it block progression?)
- It would complicate the track-to-product propagation rules
- It would require handling regression (submission declined -> revert to
  `AFFECTED`)
- The PackageStatus enum is shared between track and product levels,
  but submissions only exist at the track level

**Why SR + RR instead of SR + MaintenanceIncident?** The VA's mental model
is centered on requests: "is there an SR? what state is it in? is there a
RR? what state is it in?". The incident is important as a linking concept
but not as the primary entity the VA interacts with. Modeling SR and RR as
first-class entities directly reflects the VA's perspective and avoids
introducing a derived "phase" enum that would need to be kept in sync with
the underlying request states.

## Data Model

Three new tables. No modifications to existing tables.

**Retention**: records are kept indefinitely. The data volume is small
(a few records per ticket) and the full history provides value for
auditing and analysis.

### SubmissionRequest

Tracks an IBS submission request (type `maintenance_incident`) relevant
to Sentinel.

| Column             | Type         | Constraints          | Description                              |
|--------------------|--------------|----------------------|------------------------------------------|
| id                 | UUID         | PK                   | Internal identifier                      |
| request_number     | INTEGER      | UNIQUE, NOT NULL     | IBS request number (from payload `number`) |
| package_name       | VARCHAR(255) | NOT NULL             | Target package (from payload `actions[0].targetpackage`) |
| codestream_name    | VARCHAR(255) | NOT NULL             | Target codestream (from payload `actions[0].target_releaseproject`) |
| state              | ENUM         | NOT NULL, DEFAULT open | See SubmissionRequestState below        |
| author             | VARCHAR(64)  |                      | IBS username who created the request (from payload `author`) |
| incident_number    | INTEGER      | nullable             | Populated when state becomes `accepted` (extracted from `actions[0].targetproject` which becomes `SUSE:Maintenance:XXXXX` after acceptance) |
| superseded_by      | INTEGER      | nullable             | Request number of the superseding request (from payload `superseded_by`) |
| created_at         | TIMESTAMPTZ    | NOT NULL, DEFAULT    | Record creation timestamp                |
| updated_at         | TIMESTAMPTZ    | NOT NULL, DEFAULT    | Record update timestamp                  |

**Notes**:

- `codestream_name` comes from `target_releaseproject` in the action
  payload (verified on wire), NOT from `targetproject`. At SR creation
  time, `targetproject` is the generic maintenance project (e.g.,
  `SUSE:Maintenance`), while `target_releaseproject` contains the actual
  codestream (e.g., `SUSE:SLE-15-SP5:Update`).
- `package_name` is extracted from `sourcepackage` by stripping the
  codestream suffix (verified on wire). Example:
  `sourcepackage = "PackageKit.SUSE_SLE-15-SP5_Update"` ->
  `package_name = "PackageKit"`. See Package Name Extraction in Data
  Sources.
- `incident_number` is extracted from `targetproject` in the
  `request.state_change` event when the SR is accepted. At that point,
  IBS updates the action's `targetproject` to the incident project (e.g.,
  `SUSE:Maintenance:12345` -> `incident_number = 12345`). This is
  confirmed by OBS source code analysis; wire verification pending (see
  Open Questions).
- SUSE convention for security updates ensures one `maintenance_incident`
  action per SR. The payload may contain additional spurious actions
  (e.g., `delete` for cleanup of temporary projects) — these must be
  filtered out.

### SubmissionRequestState Enum

| Value       | Final? | Description                                    |
|-------------|--------|------------------------------------------------|
| open        | No     | Request created, pending review/acceptance. Maps to IBS states `new` and `review`. |
| accepted    | Yes    | Request accepted, incident created/updated     |
| declined    | No     | Request rejected, but can be reopened. On reopen, transitions back to `open`. |
| revoked     | Yes    | Request withdrawn, irreversible                |
| superseded  | Yes    | Request replaced by a newer request            |

### ReleaseRequest

Tracks an IBS release request (type `maintenance_release`) relevant to
Sentinel.

| Column             | Type         | Constraints          | Description                              |
|--------------------|--------------|----------------------|------------------------------------------|
| id                 | UUID         | PK                   | Internal identifier                      |
| request_number     | INTEGER      | UNIQUE, NOT NULL     | IBS request number (from payload `number`) |
| package_name       | VARCHAR(255) | NOT NULL             | Target package (from payload `actions[0].targetpackage`) |
| codestream_name    | VARCHAR(255) | NOT NULL             | Target codestream (from payload `actions[0].targetproject`) |
| state              | ENUM         | NOT NULL, DEFAULT open | See ReleaseRequestState below           |
| incident_number    | INTEGER      | NOT NULL             | Extracted from `actions[0].sourceproject` (e.g., `SUSE:Maintenance:12345` -> `12345`) |
| created_at         | TIMESTAMPTZ    | NOT NULL, DEFAULT    | Record creation timestamp                |
| updated_at         | TIMESTAMPTZ    | NOT NULL, DEFAULT    | Record update timestamp                  |

**Notes**:

- Unlike SRs, the RR `codestream_name` comes directly from
  `targetproject` (the codestream where the fix will land).
- `incident_number` is always known at RR creation time — it comes from
  `sourceproject` (the incident project from which the release is made).
- The `incident_number` links the RR to the SR(s) that populated the
  incident (`SR.incident_number = RR.incident_number`).

### ReleaseRequestState Enum

| Value       | Final? | Description                                    |
|-------------|--------|------------------------------------------------|
| open        | No     | RR created, in QA / pending acceptance. Maps to IBS states `new` and `review`. |
| accepted    | Yes    | RR accepted, fix released to codestream and product repositories |
| declined    | No     | RR rejected, but can be reopened. On reopen, transitions back to `open`. |
| revoked     | Yes    | RR revoked (must be revoked before a new RR can be created from the same incident) |

### SubmissionRequestTrack (Join Table)

Links a `SubmissionRequest` to the specific `TicketPackageTrack`
records whose CVEs are mentioned in the request's diff.

| Column                        | Type      | Constraints                                | Description                        |
|-------------------------------|-----------|--------------------------------------------|------------------------------------|
| id                            | UUID      | PK                                         | Internal identifier                |
| submission_request_id         | UUID      | FK(submission_request.id), NOT NULL        | Related submission request         |
| ticket_package_track_id       | UUID      | FK(ticket_package_track.id), NOT NULL      | Related track record               |
| created_at                    | TIMESTAMPTZ | NOT NULL, DEFAULT                          | Record creation timestamp          |

**Unique constraint**: (submission_request_id, ticket_package_track_id)

### Why Explicit Correlation (Join Table)

Implicit matching (querying `TicketPackageTrack` by codestream_name +
package_name at display time) was considered and rejected because it would
create **false positives**: if package `curl` on `SLE-15-SP6:Update` is
tracked by 3 tickets (CVE-A, CVE-B, CVE-C) but a submission only fixes
CVE-A, implicit matching would show the submission on all 3 tickets.

The join table ensures that a submission is only shown on tickets whose
CVEs are actually mentioned in the request's diff (changelog).

### Why No Join Table for ReleaseRequest

The RR does not need its own join table. Its correlation to tickets is
derived from the SR: given a `TicketPackageTrack`, find the correlated
SRs via the join table, collect their `incident_number` values, then find
the RRs with matching `incident_number`.

### Linking SR and RR: The Incident as Implicit Concept

The maintenance incident (`SUSE:Maintenance:XXXXX`) is not modeled as a
separate entity. Instead, the `incident_number` field on both
`SubmissionRequest` (populated at acceptance) and `ReleaseRequest`
(always populated) serves as the implicit link:

```
SubmissionRequest.incident_number = ReleaseRequest.incident_number
```

This allows queries like:
- "Given this ticket's SR, is there a RR in progress for the same
  incident?" (join SR -> RR on incident_number)
- "Which SRs contributed to this incident?" (filter SRs by
  incident_number)

## Data Sources

### IBS RabbitMQ Events (Real-Time)

The `IBSEventConsumer` is extended to consume two additional routing keys
alongside the existing `suse.obs.package.commit`:

#### `suse.obs.request.create`

Emitted when a new request is created in IBS. Used for both SRs
(type `maintenance_incident`) and RRs (type `maintenance_release`).

**Payload** (verified empirically on IBS RabbitMQ wire 2026-04-24):

```
Top-level keys:
  author, comment, description, id, number, actions,
  state, when, who, namespace

Each action in actions[] contains:
  action_id, type, sourceproject, sourcepackage, sourcerevision,
  targetproject, targetpackage, target_releaseproject,
  makeoriginolder
```

Not all keys are present in every action — presence depends on the
action type. Fields observed as absent in captured payloads:
`targetrepository`, `sourceupdate` (present in OBS source but not
seen on the wire).

**Key fields for Sentinel**:

For SRs (`type = "maintenance_incident"`):
- `number` — the SR number
- `author` — who created it
- `actions[0].type` — `"maintenance_incident"`
- `actions[0].target_releaseproject` — the codestream (e.g.,
  `"SUSE:SLE-15-SP5:Update"`) — **VERIFIED on wire**
- `actions[0].targetproject` — at creation time, the generic maintenance
  project (e.g., `"SUSE:Maintenance"`)
- `actions[0].sourcepackage` — package name with codestream suffix (e.g.,
  `"PackageKit.SUSE_SLE-15-SP5_Update"`)
- `actions[0].targetpackage` — **NOT PRESENT** in SR payloads. The
  package name must be extracted from `sourcepackage` by stripping the
  codestream suffix (see Package Name Extraction below).

For RRs (`type = "maintenance_release"`):
- `number` — the RR number
- `actions[0].type` — `"maintenance_release"`
- `actions[0].sourceproject` — the incident (e.g.,
  `"SUSE:Maintenance:43905"`)
- `actions[0].targetproject` — the codestream (e.g.,
  `"SUSE:SLE-15-SP5:Update"`)
- `actions[0].targetpackage` — package name with incident number suffix
  (e.g., `"PackageKit.43905"`)

**Spurious actions**: SR payloads may contain additional actions of type
`delete` (cleanup of temporary project `SUSE:Maintenance:REQUEST:XXXXX`).
RR payloads may contain `patchinfo` actions. Sentinel must filter these and
process only actions of type `maintenance_incident` or
`maintenance_release`.

#### Package Name Extraction

The package name is not directly available as a clean field in SR or RR
payloads. Extraction rules:

For SRs: extract from `sourcepackage` by stripping the codestream suffix.
The suffix is `.` followed by `target_releaseproject` with `:` replaced
by `_`:

```
sourcepackage:          "PackageKit.SUSE_SLE-15-SP5_Update"
target_releaseproject:  "SUSE:SLE-15-SP5:Update"
suffix to strip:        ".SUSE_SLE-15-SP5_Update"
extracted package_name: "PackageKit"
```

For RRs: extract from `targetpackage` by stripping the incident number
suffix. The suffix is `.` followed by the incident number (which is the
last component of `sourceproject`):

```
targetpackage:   "PackageKit.43905"
sourceproject:   "SUSE:Maintenance:43905"
suffix to strip: ".43905"
extracted package_name: "PackageKit"
```

#### `suse.obs.request.state_change`

Emitted when a request changes state. Used for both SRs and RRs.

**Routing key**: `suse.obs.request.state_change` — **verified on wire**.
The OBS source defines it as `request.state_change` (with underscore in
the class, but dots on the wire as part of the full routing key).

**Additional payload keys** (beyond the base `Request` keys):
- `state` — new state
- `oldstate` — previous state
- `duration` — time spent in the previous state

**Key detail for SRs**: when a `maintenance_incident` SR is accepted,
`actions[0].targetproject` in the event payload is updated to the incident
project name (e.g., `SUSE:Maintenance:12345`). This happens because OBS
modifies the action's `target_project` to the incident project before
saving and emitting the event (see `bs_request.rb#changestate_accepted`
and `bs_request_action_maintenance_incident.rb#execute_accept`). Sentinel
extracts the `incident_number` from this field.

**State change events are only emitted for conclusive (final) state
transitions** (`bs_request.rb#send_state_change`): accepted, declined,
revoked, superseded. Transitions like `new -> review` or `declined -> new`
(reopen) do NOT emit `state_change` events. This means Sentinel cannot detect
reopens via RabbitMQ — the catch-up fetcher is the only mechanism for
detecting them.

### IBS REST API (Catch-Up Fetcher)

Two IBS API endpoints are used by the periodic catch-up fetcher:

#### Request Search

```
GET /request?view=collection&project={codestream}&states=new,review
```

Returns all requests targeting the given project in `new` or `review`
state. Response is XML (`<collection>` of `<request>` elements).

Supports `limit` and `offset` parameters for pagination. Requires at
least one filter parameter (project, package, user, states, types, or
ids).

Additional filter parameters (used by Step 1b for temporal lookback):

- `types` — comma-separated action types (e.g., `maintenance_incident`,
  `maintenance_release`)
- `created_at_from` — ISO 8601 datetime; only return requests created at
  or after this timestamp. Verified in OBS source code
  (`BsRequest::FindFor::Query`); not yet tested on-the-wire against IBS.
- `created_at_to` — ISO 8601 datetime; upper bound (inclusive, with
  1-minute tolerance in OBS implementation)

These temporal parameters allow the catch-up fetcher to query accepted
requests within a bounded time window (e.g., last 25 hours) instead of
scanning the entire history.

#### Request Details

```
GET /request/{number}
```

Returns full details of a single request, including current state and
action list. Used by the fetcher to check the current state of requests
that are `open` in Sentinel but no longer appear in the search results
(because they transitioned to accepted/declined/revoked/superseded).

### IBS Diff API (CVE Correlation)

To correlate a submission request with specific CVEs, Sentinel extracts
CVE-IDs from the request's diff using:

```
POST /request/{id}?cmd=diff&withissues=1&view=xml
```

Operates directly on the request — no need to know source/target MD5s.
Returns `<issues>` block with structured entries:

```xml
<issue state="added|changed|deleted" tracker="cve" name="2026-XXXXX"
       label="CVE-2026-XXXXX" url="..."/>
```

Only issues with `state="added"` and `tracker="cve"` are processed.
Issues with `state="changed"` (pre-existing CVE references in diff
context) and `state="deleted"` (removed references) are skipped. This
filtering is consistent with `IBSTrackReleaseDetector` on the source
diff endpoint (see `docs/features/integrations/ibs-integration.md`).

Verified empirically on IBS (2026-04-29) with SR#407603: a changelog
containing six pre-existing CVE references correctly reports them as
`state="changed"`, while two newly added CVEs appear as
`state="added"` — no false positives from context lines.

## Processing Pipelines

Submission tracking is **independent** from release detection.
`IBSTrackReleaseDetector` and the existing `IBSEventConsumer`
`package.commit` handler do NOT update `ReleaseRequest` records when
they detect a codestream release. The RR state is updated exclusively
by the submission tracking pipelines below (real-time consumer for
conclusive state changes, catch-up fetcher for missed events and
reopens).

SR and RR state changes also drive `TicketPackageTrack.delivery_status`
updates:
- When an SR is created for a track, `delivery_status` transitions from
  `PENDING` to `IN_PROGRESS`
- When the RR is accepted, `delivery_status` transitions from
  `IN_PROGRESS` to `RELEASED`

### Pipeline 1: Real-Time (IBSEventConsumer)

#### On `request.create` (type = maintenance_incident) — New SR

```
1. Parse event payload
2. Filter actions: skip any action where type != "maintenance_incident"
3. Extract from the maintenance_incident action:
   - codestream_name = target_releaseproject
   - package_name = extract from sourcepackage by stripping the
     codestream suffix (see Package Name Extraction in Data Sources)
4. Is codestream_name an active codestream with at least one
   TicketPackageTrack in ANALYSIS or AFFECTED?
   If no -> skip
5. Is package_name tracked in at least one ticket for that
   codestream? If no -> skip
6. Create SubmissionRequest record (state = open)
7. Enqueue Celery task: correlate_submission_request(submission_id)
```

#### Celery Task: `correlate_submission_request`

```
1. Call IBS diff API for the request (see Data Sources above)
2. Extract CVE-IDs from the diff response (filter for `state="added"` and
      `tracker="cve"` only — see IBS Diff API section in Data Sources)
3. If no CVE-IDs found -> delete the SubmissionRequest (silent discard)
4. For each CVE-ID:
   a. Find the ticket with that CVE
    b. Find the TicketPackageTrack for (ticket, codestream, package)
   c. Create SubmissionRequestTrack join record (idempotent:
      skip if unique constraint already satisfied)
5. If no correlations EXIST for this SR (total count of join records
   in the database = 0, not just those created in this run) -> delete
   the SubmissionRequest (silent discard).
   Note: unknown CVE-IDs are intentionally skipped — no ticket/CVE
   creation. If a ticket for that CVE is created later,
   `discover_submissions_for_ticket_package` (Pipeline 3) will
   retroactively discover this SR via IBS query.
```

#### On `request.create` (type = maintenance_release) — New RR

```
1. Parse event payload
2. Filter actions: skip any action where type != "maintenance_release"
   (e.g., skip patchinfo actions)
3. Extract from the maintenance_release action:
   - codestream_name = targetproject
   - incident_number = extract from sourceproject
     (e.g., "SUSE:Maintenance:12345" -> 12345)
   - package_name = extract from targetpackage by stripping the
     incident number suffix (see Package Name Extraction in Data Sources)
4. Does a SubmissionRequest with this incident_number exist in Sentinel?
   If no -> skip (the incident is not tracked)
5. Create ReleaseRequest record (state = open)
```

#### On `request.state_change` — SR or RR State Changed

```
1. Parse event payload
2. Determine request type from actions[0].type:
   - "maintenance_incident" -> SR
   - "maintenance_release" -> RR

For SRs:
3. Find SubmissionRequest by request_number
4. If not found -> ignore (not relevant to Sentinel)
5. Map IBS state to Sentinel state:
   - "accepted" -> accepted
     - Extract incident_number from actions[0].targetproject
       (now "SUSE:Maintenance:XXXXX")
     - Call set_sr_incident_number(SR, extracted number)
   - "declined" -> declined
   - "revoked" -> revoked
   - "superseded" -> superseded
     - Set SR.superseded_by from payload if available
6. Update SR.state

For RRs:
3. Find ReleaseRequest by request_number
4. If not found -> ignore (not relevant to Sentinel)
5. Map IBS state to Sentinel state:
   - "accepted" -> accepted
   - "declined" -> declined
   - "revoked" -> revoked
6. Update RR.state
```

**Note on reopens**: IBS does not emit `state_change` events for
non-conclusive transitions (e.g., `declined -> new`). If a declined SR
or RR is reopened, Sentinel will not detect this via RabbitMQ. The catch-up
fetcher handles this case (see Pipeline 2).

### Pipeline 2: Periodic Catch-Up (RequestSyncFetcher)

A `BaseFetcher` subclass that runs every **24 hours** (02:30 UTC) to
recover events missed during consumer downtime, reconcile state
drift, and verify delivery status consistency. This is the only
mechanism for detecting request reopens (declined -> new/review).

The lookback window (controlled by the `lookback_hours` custom setting,
default: **25 hours**) ensures overlap across consecutive fetcher runs,
covering total platform outages of up to 25 hours (consumer + fetcher
both down).

#### Procedure

```
Step 1 — Discover missed open SRs and reconcile known ones:

  1. Identify active codestreams (distinct codestream_name values from
     TicketPackageTrack records with status ANALYSIS or AFFECTED).
     Soft-deleted tracks are included — submission tracking applies
     regardless of exclusion status.

  2. For each active codestream:
     GET /request?view=collection&project={codestream}&states=new,review

     For each request in the response:
       a. Determine type from action: maintenance_incident (SR) or
          maintenance_release (RR)

       For SRs:
        b. Filter: is the targetpackage tracked in at least one ticket
           for this codestream?
           If no -> skip
       c. If NOT present in SubmissionRequest table:
          -> Create SubmissionRequest (state = open)
          -> Enqueue correlate_submission_request task
       d. If ALREADY present in SubmissionRequest but state is
          'declined':
          -> Update state to 'open' (the SR was reopened and Sentinel
             missed the event)

       For RRs:
       e. If NOT present in ReleaseRequest table:
          -> Does a SubmissionRequest with this incident_number exist?
             If no -> skip
          -> Create ReleaseRequest (state = open)
       f. If ALREADY present in ReleaseRequest but state is 'declined':
          -> Update state to 'open' (the RR was reopened)

Step 1b — Discover missed accepted SRs (temporal lookback):

  3. For each active codestream:
     GET /request?view=collection&project={codestream}
         &states=accepted&types=maintenance_incident
         &created_at_from={now - 25h}

     For each SR in the response:
       a. Already in SubmissionRequest table? -> skip
        b. targetpackage tracked in at least one ticket for this
           codestream? If no -> skip
        c. Create SubmissionRequest (state=accepted)
        d. Call set_sr_incident_number(SR, extracted incident_number)
        e. Enqueue correlate_submission_request

Step 2 — Reconcile requests no longer in new/review:

  4. Query all SubmissionRequest records with state = 'open' that were
     NOT seen in Step 1 (no longer in new/review state in IBS)

  5. For each such record:
     GET /request/{number}
     -> Update state to the current IBS state (accepted, declined,
       revoked, superseded)
     -> If accepted: call set_sr_incident_number(SR, extracted
        incident_number)

  6. Query all ReleaseRequest records with state = 'open' that were
     NOT seen in Step 1

  7. For each such record:
     GET /request/{number}
     -> Update state to the current IBS state (accepted, declined,
       revoked)

Step 3 — Delivery status reconciliation:

  8. Query all TicketPackageTrack records where:
      - track type is IBS (codestream-based)
      - delivery_status != RELEASED
      - the parent ticket is in an open state
  9. For each such track, verify that delivery_status is consistent
     with the current SR/RR state:
     - If an SR is correlated and in open or accepted state but
       delivery_status is PENDING -> set to IN_PROGRESS
     - If an accepted RR exists for the correlated incident but
       delivery_status is not RELEASED -> set to RELEASED
```

#### Why This Approach

The catch-up problem for request tracking differs from the
`package.commit` catch-up (handled by `IBSTrackReleaseDetector`):

- For `package.commit`, the MD5 checksum cache
  (`CodestreamPackageChecksum`) provides a "known good state" to diff
  against — any MD5 change since the last check is detectable regardless
  of missed events.
- For requests, there is no equivalent "state to compare" — a request
  created and accepted during downtime leaves no trace in Sentinel's local
  state.
- Additionally, IBS does not emit `state_change` events for reopens
  (`declined -> new/review`), making the catch-up fetcher the only
  mechanism for detecting them.

The IBS Request Search API (`GET /request?view=collection`) fills this
gap by allowing Sentinel to query for currently open requests. Combined with
point-lookup for known requests (`GET /request/{number}`) and temporal
lookback queries (`created_at_from`), this covers missed creations,
missed state changes, reopens, and requests that were created and
accepted during consumer downtime.

**Volume estimate**: with ~20-30 active codestreams and ~30 open requests
per codestream, Step 1 processes ~600-900 requests per run. Most will
already be known to Sentinel (local DB lookup, fast). Only genuinely new
requests trigger a diff API call. Step 1b adds one query per codestream
for accepted SRs in the last 25 hours — typically 1-5 results per
codestream, most already known (skipped immediately). Step 2 processes
only requests in `open` state in Sentinel that were not seen in Step 1 —
typically a small number.

### Pipeline 3: Retroactive Discovery (`discover_submissions_for_ticket_package`)

A Celery sub-operation task (not a `BaseFetcher`) enqueued by
`add_package_to_ticket` whenever a package is added to a ticket. Discovers
SRs and RRs that were created before Sentinel started tracking the package.

This is the same architectural pattern as `create_ticket_from_detection`:
an on-demand task triggered by a parent operation, with no independent
schedule or dashboard presence.

**Trigger**: `add_package_to_ticket` enqueues this task as its final step,
after all `TicketPackageTrack` and `TicketPackageProduct` records have
been created and the bugowner has been resolved. The task runs regardless of
what triggered the package addition (VA manual action, CPE mapping,
release detection Case B/C).

#### Procedure

```
1. Retrieve the ticket's CVE-ID
2. Retrieve ALL TicketPackageTrack records for (ticket, package)
   — no status filter (includes ANALYSIS, AFFECTED, FIXED, etc.)
   and no soft-deletion filter (includes soft-deleted tracks)
3. For each track:
   a. Query IBS:
      GET /request?view=collection&project={codestream}
          &package={package}&states=new,review,accepted
          &types=maintenance_incident
          &created_at_from={now - 14d}
   b. For each SR in the response:
      - Already in SubmissionRequest AND a join record exists for
        (this SR, this TicketPackageTrack)? → skip
      - Already in SubmissionRequest but no join record for
        (this SR, this TicketPackageTrack)?
        → Re-enqueue correlate_submission_request(submission_id)
      - Not in SubmissionRequest?
        → Create SubmissionRequest record (state mapped from IBS)
        → Enqueue correlate_submission_request(submission_id)
   c. For each SR (newly created or re-enqueued) with incident_number:
      - Call set_sr_incident_number(SR, incident_number)
        (idempotent: if incident_number is already set to the same
        value, discover_release_requests_for_incident is still called
        but will skip existing RR records)
```

#### Design Decisions

- **No status filter on tracks**: all tracks are checked regardless of
  their `PackageStatus` or soft-deletion status. Soft-deleted tracks are
  included because submission tracking applies regardless of exclusion
  status (see hierarchical exclusion model in
  `docs/features/packages/package-model.md`). This ensures SR/RR data
  is captured even for tracks already in `FIXED` state (e.g., Case C
  tickets created by `create_ticket_from_detection`) or tracks excluded
  by the VA. The data is not displayed in the UI for final-status or
  excluded tracks but is retained for audit and future use.
- **14-day lookback window**: limits the volume of accepted SRs returned
  by IBS for long-lived packages. An SR older than 14 days whose ticket is
  only being created now is an extreme edge case with low informational
  value.
- **Sub-operation task**: not a `BaseFetcher` — runs on-demand as a
  side-effect of `add_package_to_ticket`, same category as
  `create_ticket_from_detection`.
- **Location of trigger**: the enqueue lives in `add_package_to_ticket`
  (service layer), not in `ticket_mutations`. The discovery task performs
  external I/O (IBS queries) which is outside the responsibility of
  `ticket_mutations` (record mutations only).
- **Reuse of `correlate_submission_request`**: Pipeline 3 delegates
  diff API calls and CVE correlation to `correlate_submission_request`
  (Pipeline 1) instead of reimplementing the logic. This ensures that
  multi-CVE SRs are correlated with all matching tickets, not just the
  one that triggered the discovery. `correlate_submission_request` is
  idempotent: join records that already exist are skipped, and the SR
  is only deleted if it has zero correlations in total.

### Centralized Functions

#### `set_sr_incident_number(submission_request, incident_number)`

All code that sets `incident_number` on a `SubmissionRequest` MUST use
this function instead of modifying the attribute directly.

**Procedure**:

```
1. Set submission_request.incident_number = incident_number
2. Call discover_release_requests_for_incident(incident_number)
```

Note: currently all callers are within the submission tracking feature
(IBSEventConsumer, RequestSyncFetcher,
`discover_submissions_for_ticket_package`). No external module has a
reason to set `incident_number` directly. The centralized function
exists to ensure `discover_release_requests_for_incident` is always
called when an incident is discovered, not to enforce a cross-module
boundary.

#### `discover_release_requests_for_incident(incident_number)`

A synchronous function that searches IBS for release requests associated
with a given maintenance incident. Called automatically by
`set_sr_incident_number` — not invoked directly by pipeline code.

**Procedure**:

```
1. Query IBS:
   GET /request?view=collection
       &project=SUSE:Maintenance:{incident_number}
       &types=maintenance_release
       &states=new,review,accepted
2. For each RR in the response:
   - Already in ReleaseRequest table? → skip
   - Create ReleaseRequest record (state mapped from IBS state,
     codestream_name and package_name extracted from action fields)
```

### Chain Selection Rules

The chain returned for a given ticket/track is determined by:

1. **If an incident exists** (at least one accepted SR with an
   `incident_number`): return the chain based on the **most recent
   incident**:
   - SR = the most recent SR accepted into that incident
   - SM = the incident
   - RR = the most recent RR for that incident (if any)

2. **If no incident exists**: return the **most recent SR** (pending or
   declined, with no incident or RR in the chain).

New SRs that are pending but not yet associated with an active incident
are **not included** while an incident chain is active. They appear in the
chain only after the UM accepts them into the incident.

The chain is only relevant for non-final track statuses (`ANALYSIS`,
`AFFECTED`). Tracks with `FIXED` or `WONT_FIX` status do not have an
active chain (though the data remains in the database).

## API Endpoints

Two read-only endpoints nested under the ticket resource. Both follow the
same access rules as `GET /api/v1/tickets/{ticket_id}` — if the caller
can access the ticket detail, they can access its submission and release
requests.

Both endpoints return unpaginated lists (expected volume is small — fewer
than 20 records per ticket, similar to ticket references).

### `GET /api/v1/tickets/{ticket_id}/submission-requests`

List all submission requests correlated to the ticket via the
`SubmissionRequestTrack` join table.

**Query parameters** (all optional):

| Parameter        | Type   | Description                                      |
|------------------|--------|--------------------------------------------------|
| `package_name`   | string | Filter by package name (exact match)             |
| `codestream_name`| string | Filter by codestream name (exact match)          |
| `state`          | string | Filter by state: `open`, `accepted`, `declined`, `revoked`, `superseded` |

**Response** (200):

```json
{
  "data": [
    {
      "id": "uuid",
      "request_number": 407175,
      "package_name": "curl",
      "codestream_name": "SUSE:SLE-15-SP6:Update",
      "state": "accepted",
      "author": "jdoe",
      "incident_number": 43894,
      "superseded_by": null,
      "ibs_url": "https://build.suse.de/request/show/407175",
      "incident_url": "https://build.suse.de/project/show/SUSE:Maintenance:43894",
      "created_at": "2026-04-20T10:00:00Z",
      "updated_at": "2026-04-20T12:00:00Z"
    }
  ]
}
```

**Response fields**:

| Field              | Type              | Description                                      |
|--------------------|-------------------|--------------------------------------------------|
| `id`               | UUID              | Internal identifier                              |
| `request_number`   | integer           | IBS request number                               |
| `package_name`     | string            | Target package name                              |
| `codestream_name`  | string            | Target codestream                                |
| `state`            | string            | Current state (see `SubmissionRequestState`)      |
| `author`           | string \| null    | IBS username who created the request             |
| `incident_number`  | integer \| null   | Maintenance incident number (set on acceptance)  |
| `superseded_by`    | integer \| null   | Request number of the superseding request        |
| `ibs_url`          | string            | Computed: `https://build.suse.de/request/show/{request_number}` |
| `incident_url`     | string \| null    | Computed: `https://build.suse.de/project/show/SUSE:Maintenance:{incident_number}`. Null when `incident_number` is null. |
| `created_at`       | datetime (UTC)    | Record creation timestamp                        |
| `updated_at`       | datetime (UTC)    | Record update timestamp                          |

**Error responses**:

| Status | Code | Condition                                              |
|--------|------|--------------------------------------------------------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found                                       |
| 422    | `VALIDATION_ERROR` | Invalid `state` value                                  |

### `GET /api/v1/tickets/{ticket_id}/release-requests`

List all release requests associated with the ticket. Derived via the
SR correlation: find SRs correlated to the ticket, collect their
`incident_number` values, then return RRs with matching
`incident_number`.

**Query parameters** (all optional):

| Parameter         | Type    | Description                                      |
|-------------------|---------|--------------------------------------------------|
| `package_name`    | string  | Filter by package name (exact match)             |
| `codestream_name` | string  | Filter by codestream name (exact match)          |
| `state`           | string  | Filter by state: `open`, `accepted`, `declined`, `revoked` |
| `incident_number` | integer | Filter by maintenance incident number            |

**Response** (200):

```json
{
  "data": [
    {
      "id": "uuid",
      "request_number": 407225,
      "package_name": "curl",
      "codestream_name": "SUSE:SLE-15-SP6:Update",
      "state": "open",
      "incident_number": 43894,
      "ibs_url": "https://build.suse.de/request/show/407225",
      "incident_url": "https://build.suse.de/project/show/SUSE:Maintenance:43894",
      "created_at": "2026-04-21T08:00:00Z",
      "updated_at": "2026-04-21T08:00:00Z"
    }
  ]
}
```

**Response fields**:

| Field              | Type           | Description                                      |
|--------------------|----------------|--------------------------------------------------|
| `id`               | UUID           | Internal identifier                              |
| `request_number`   | integer        | IBS request number                               |
| `package_name`     | string         | Target package name                              |
| `codestream_name`  | string         | Target codestream                                |
| `state`            | string         | Current state (see `ReleaseRequestState`)         |
| `incident_number`  | integer        | Maintenance incident number                      |
| `ibs_url`          | string         | Computed: `https://build.suse.de/request/show/{request_number}` |
| `incident_url`     | string         | Computed: `https://build.suse.de/project/show/SUSE:Maintenance:{incident_number}` |
| `created_at`       | datetime (UTC) | Record creation timestamp                        |
| `updated_at`       | datetime (UTC) | Record update timestamp                          |

**Error responses**:

| Status | Code | Condition                                              |
|--------|------|--------------------------------------------------------|
| 404    | `TICKET_NOT_FOUND` | Ticket not found                                       |
| 422    | `VALIDATION_ERROR` | Invalid `state` or `incident_number` value             |

## Background Tasks

| Task                                         | Type                       | Schedule           | Purpose                                                              |
|----------------------------------------------|----------------------------|--------------------|----------------------------------------------------------------------|
| `RequestSyncFetcher`                         | BaseFetcher (periodic)     | Every 24h (02:30 UTC) | Catch-up: discover missed SRs/RRs, reconcile states, detect reopens, verify delivery status |
| `correlate_submission_request`               | Celery task (on-demand)    | —                  | Call IBS diff API, extract CVE-IDs, create join records              |
| `discover_submissions_for_ticket_package`    | Celery task (sub-operation)| —                  | Retroactive SR/RR discovery when a package is added to a ticket      |

`RequestSyncFetcher` follows the `BaseFetcher` contract: automatic
execution tracking, metric collection, and dashboard visibility.

`correlate_submission_request` and `discover_submissions_for_ticket_package`
are sub-operation tasks (same category as `create_ticket_from_detection`):
on-demand, no independent schedule, no dashboard presence.

## Error Handling

| Scenario                                                     | Behavior                                                                                     |
|--------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| IBS diff API unreachable / timeout (`correlate_submission_request`) | Celery retry with standard backoff. After max retries, SR remains without correlations — catch-up fetcher will retry on next run. |
| IBS diff API returns 4xx/5xx                                 | Same as above.                                                                               |
| IBS REST API unreachable (`RequestSyncFetcher`)              | Fetcher run fails, reported via `BaseFetcher` metrics. Next scheduled run covers the gap.    |
| RabbitMQ event with malformed/incomplete payload             | Log warning, skip event. Catch-up fetcher recovers.                                          |
| SR/RR references a codestream not tracked in any active ticket  | Silent skip (not relevant to Sentinel).                                                         |
| IBS diff API returns no CVE-IDs for an SR                    | SR is deleted (silent discard — see Pipeline 1, `correlate_submission_request` step 3).      |

## Configuration

| Setting                         | Type                    | Default    | Description                                                  |
|---------------------------------|-------------------------|------------|--------------------------------------------------------------|
| `RequestSyncFetcher` schedule   | Cron (BaseFetcher)      | Every 24h  | Overridable via fetcher config API                           |
| Consumer routing keys           | Static (code)           | `suse.obs.request.create`, `suse.obs.request.state_change` | Added to existing `IBSEventConsumer` bindings |
| Catch-up lookback window        | Custom setting (`sync_requests`) | 25h | `lookback_hours` — configurable via admin dashboard |
| Retroactive discovery window    | Custom setting (`sync_requests`) | 14d | `retroactive_discovery_days` — configurable via admin dashboard |

### sync_requests — Custom Settings

This fetcher declares the following custom settings (see
`docs/features/platform/fetcher-infrastructure.md`, "Custom Settings
Schema" for the schema structure and validation rules):

| Setting | Type | Default | Range | Description |
|---------|------|---------|-------|-------------|
| `lookback_hours` | int | 25 | 1–168 | Hours to look back for missed events during catch-up |
| `retroactive_discovery_days` | int | 14 | 1–90 | Days to look back for retroactive SR/RR discovery |

## Security

The submission tracking feature introduces no new authentication
mechanisms or credentials:

- **API endpoints**: same access rules as
  `GET /api/v1/tickets/{ticket_id}` — no additional role required.
- **IBS API calls**: use the same IBS credentials already configured
  for `IBSTrackReleaseDetector` and the existing `IBSEventConsumer`
  (see `ibs-rabbitmq-integration.md` and `ibs-integration.md`).
- **No sensitive data exposed**: endpoints return only IBS request
  numbers, package names, codestream names, and states.

## Dependencies

- `ibs-rabbitmq-integration.md` — consumer architecture, connection
  management, routing key bindings
- `ibs-integration.md` — IBS REST API and diff API
- `package-model.md` — `TicketPackageTrack` model,
  `add_package_to_ticket` trigger
- `tickets.md` — ticket model and access rules
- `fetcher-infrastructure.md` — `BaseFetcher` base class contract

## Scope Exclusions

The following are explicitly out of scope for this feature:

- **Git-based workflow**: this spec covers only the MU process. Tracking
  submissions for git-based products (SLE 16+) will require a separate
  specification using Gitea API/webhooks. The design of this feature is
  intentionally specific to the MU/IBS workflow — no premature abstraction
  is introduced to accommodate Git. The eventual unification at the
  API/UI level will be evaluated when both workflows are implemented.
- **Manual correlation**: the VA cannot manually link/unlink SRs to
  tickets. Correlation is fully automatic via the diff API.
- **Orphan submissions**: SRs for which the diff API returns no CVE-IDs
  are silently discarded (not saved).
- **PackTrack integration**: PackTrack tracks a subset of submissions
  (coodpool team packages only). Sentinel consumes IBS events directly for
  universal coverage.
- **Product-level tracking**: SRs and RRs are tracked at the codestream
  level only. Products inherit the codestream fix when released.
- **Multi-codestream SRs**: while technically possible in IBS, SUSE
  convention for security updates requires one package + one codestream
  per SR. Sentinel assumes this convention and processes only `actions[0]`.

## Open Questions

The following should be verified before implementation, but are not
blocking for the specification:

### 1. SR Accepted Payload — `targetproject` Update

When a `maintenance_incident` SR is accepted, the OBS source code shows
that `action.target_project` is updated to the incident project name
(e.g., `SUSE:Maintenance:12345`) before the `state_change` event is
emitted. This has been confirmed by reading the OBS source
(`bs_request.rb#changestate_accepted`,
`bs_request_action_maintenance_incident.rb#execute_accept`) but has NOT
been observed on the wire — no SR acceptance events were captured during
the test window.

**Risk**: low. The code path is clear and the logic is straightforward.
If for some reason `targetproject` is not updated in the event payload,
Sentinel can fall back to querying `GET /request/{number}` to obtain the
incident number after detecting an accepted state.

**Action**: verify opportunistically during implementation by logging
the first SR accepted event received by the consumer.

### 2. `request.create` for `maintenance_incident` — Payload Structure

No `request.create` events of type `maintenance_incident` were captured
during the test window (~2.5 hours). The `state_change` payload for a
declined SR confirms the field structure (`target_releaseproject` present,
`targetpackage` absent), and it is reasonable to assume `request.create`
uses the same structure (both use `event_parameters` ->
`notify_params`).

**Risk**: very low. The same `notify_params` method generates both
payloads.

**Action**: verify opportunistically during implementation.

### 3. IBS Request Search by Incident Project

`discover_release_requests_for_incident` queries IBS using
`project=SUSE:Maintenance:{incident_number}` to find release requests
for a specific incident. OBS source code confirms that the `project`
parameter matches both `source_project` and `target_project` (via OR
in `BsRequest::FindFor::Query`), and for `maintenance_release` requests
the source project is always the incident. However, this specific usage
(querying with an incident project name rather than a codestream) has
NOT been tested on-the-wire against IBS.

**Risk**: low. The `project` parameter is standard and the query logic
is straightforward.

**Fallback**: if the query does not return expected results, revert to
querying by codestream with a `created_at_from` temporal filter. The
exact signature and parameter sourcing for the fallback will be decided
at implementation time if needed. One option is to derive
`created_at_from` from the parent `SubmissionRequest.created_at` (the
RR is necessarily created after the SR), which the function can resolve
from the database using the `incident_number`.

**Action**: verify empirically during implementation by running a
manual test query against IBS for a known incident with an active RR.

## Cross-references

- `docs/api-spec.md` — global API conventions (envelope format, error codes,
  pagination, shared 422 responses)
- `docs/data-model.md` — full database schema
- `docs/features/packages/package-model.md` — `TicketPackageTrack` model,
  `add_package_to_ticket` trigger, delivery status dimension
- `docs/features/tickets/tickets.md` — ticket model and access rules
- `docs/features/integrations/ibs-integration.md` — IBS REST API endpoints
  and diff API
- `docs/features/integrations/ibs-rabbitmq-integration.md` — consumer
  architecture, connection management, routing key bindings
- `docs/features/platform/fetcher-infrastructure.md` — `BaseFetcher` base
  class contract, custom settings

